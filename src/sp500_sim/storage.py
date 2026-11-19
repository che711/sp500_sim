"""Сохранение прогонов в SQLite, чтобы их можно было сравнивать между собой."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .engine import TRADE_COLUMNS, RunResult
from .errors import SimError

SCHEMA_VERSION = 2

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


class StorageError(SimError):
    """Нет базы с прогонами или нет нужного прогона."""


class RunNotFinishedError(StorageError):
    """Прогон ещё идёт или закончился ошибкой, итогов у него нет."""


# Прогон создаётся в статусе running ещё до загрузки цен, поэтому данные о ценах
# (период, отпечаток) заполняются позже и до этого пусты.
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    git_commit       TEXT,
    ticker           TEXT NOT NULL,
    data_start       TEXT,
    data_end         TEXT,
    data_rows        INTEGER,
    data_hash        TEXT,
    data_fetched_at  TEXT,
    config_json      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'done',
    error            TEXT
);
CREATE TABLE IF NOT EXISTS results (
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    strategy      TEXT NOT NULL,
    final_value   REAL NOT NULL,
    total_return  REAL NOT NULL,
    cagr          REAL NOT NULL,
    max_drawdown  REAL NOT NULL,
    sharpe        REAL NOT NULL,
    n_trades      INTEGER NOT NULL,
    PRIMARY KEY (run_id, strategy)
);
CREATE TABLE IF NOT EXISTS equity (
    run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    strategy  TEXT NOT NULL,
    date      TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (run_id, strategy, date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS trades (
    run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    strategy  TEXT NOT NULL,
    date      TEXT NOT NULL,
    side      TEXT NOT NULL,
    qty       REAL NOT NULL,
    price     REAL NOT NULL,
    fee       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS trades_by_run ON trades (run_id, strategy);
"""

# Версия 1 -> 2: в runs появились status и error, а данные о ценах стали необязательными.
# SQLite не умеет менять NOT NULL у колонки, поэтому таблица пересоздаётся.
MIGRATION_1_TO_2 = """
CREATE TABLE runs_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    git_commit       TEXT,
    ticker           TEXT NOT NULL,
    data_start       TEXT,
    data_end         TEXT,
    data_rows        INTEGER,
    data_hash        TEXT,
    data_fetched_at  TEXT,
    config_json      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'done',
    error            TEXT
);
INSERT INTO runs_new (id, created_at, git_commit, ticker, data_start, data_end, data_rows,
                      data_hash, data_fetched_at, config_json, status, error)
    SELECT id, created_at, git_commit, ticker, data_start, data_end, data_rows,
           data_hash, data_fetched_at, config_json, 'done', NULL FROM runs;
DROP TABLE runs;
ALTER TABLE runs_new RENAME TO runs;
"""


@dataclass(frozen=True)
class RunMeta:
    """Что нужно знать, чтобы воспроизвести прогон: когда, на каком коде и на каких данных."""

    created_at: str
    git_commit: str | None
    ticker: str
    data_start: str
    data_end: str
    data_rows: int
    data_hash: str
    data_fetched_at: str


@dataclass(frozen=True)
class StoredRun:
    run_id: int
    meta: dict[str, Any]
    config: dict[str, Any]
    results: list[RunResult]


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_runs = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
    ).fetchone()
    if version >= SCHEMA_VERSION:
        return
    if has_runs:  # база создана старой версией программы
        # Во время пересоздания таблицы внешние ключи должны быть выключены
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript("BEGIN;" + MIGRATION_1_TO_2 + "COMMIT;")
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def connect(path: str | Path, must_exist: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if must_exist and not path.exists():
        raise StorageError(f"База прогонов не найдена: {path}. Сначала выполните sp500-sim run")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    # WAL: запись фонового прогона не блокирует чтение страниц сайта
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_results(conn: sqlite3.Connection, run_id: int, results: list[RunResult]) -> None:
    for r in results:
        m = r.metrics
        conn.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                r.name,
                m["final_value"],
                m["total_return"],
                m["cagr"],
                m["max_drawdown"],
                m["sharpe"],
                int(m["n_trades"]),
            ),
        )
        conn.executemany(
            "INSERT INTO equity VALUES (?, ?, ?, ?)",
            ((run_id, r.name, day.strftime("%Y-%m-%d"), float(v)) for day, v in r.equity.items()),
        )
        conn.executemany(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (run_id, r.name, t.date.strftime("%Y-%m-%d"), t.side, t.qty, t.price, t.fee)
                for t in r.trades.itertuples(index=False)
            ),
        )


def _dumps(config: dict[str, Any]) -> str:
    return json.dumps(config, ensure_ascii=False, sort_keys=True)


def save_run(
    conn: sqlite3.Connection, meta: RunMeta, config: dict[str, Any], results: list[RunResult]
) -> int:
    """Сохраняет готовый прогон целиком одной транзакцией. Возвращает его номер."""
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, git_commit, ticker, data_start, data_end, data_rows,"
            " data_hash, data_fetched_at, config_json, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meta.created_at,
                meta.git_commit,
                meta.ticker,
                meta.data_start,
                meta.data_end,
                meta.data_rows,
                meta.data_hash,
                meta.data_fetched_at,
                _dumps(config),
                STATUS_DONE,
            ),
        )
        run_id = int(cur.lastrowid or 0)
        _write_results(conn, run_id, results)
    return run_id


def create_run(conn: sqlite3.Connection, config: dict[str, Any], ticker: str) -> int:
    """Заводит прогон в статусе running. Результаты добавит finish_run."""
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, ticker, config_json, status) VALUES (?, ?, ?, ?)",
            (_now(), ticker, _dumps(config), STATUS_RUNNING),
        )
    return int(cur.lastrowid or 0)


def finish_run(
    conn: sqlite3.Connection, run_id: int, meta: RunMeta, results: list[RunResult]
) -> None:
    with conn:
        cur = conn.execute(
            "UPDATE runs SET git_commit = ?, data_start = ?, data_end = ?, data_rows = ?,"
            " data_hash = ?, data_fetched_at = ?, status = ?, error = NULL"
            " WHERE id = ? AND status = ?",
            (
                meta.git_commit,
                meta.data_start,
                meta.data_end,
                meta.data_rows,
                meta.data_hash,
                meta.data_fetched_at,
                STATUS_DONE,
                run_id,
                STATUS_RUNNING,
            ),
        )
        if cur.rowcount != 1:
            raise StorageError(f"Прогон №{run_id} не ждёт результатов")
        _write_results(conn, run_id, results)


def fail_run(conn: sqlite3.Connection, run_id: int, message: str) -> None:
    with conn:
        conn.execute(
            "UPDATE runs SET status = ?, error = ? WHERE id = ? AND status = ?",
            (STATUS_ERROR, message, run_id, STATUS_RUNNING),
        )


def mark_interrupted(conn: sqlite3.Connection) -> int:
    """Прогоны, которые шли в момент остановки сервера, больше не идут. Возвращает их число."""
    with conn:
        cur = conn.execute(
            "UPDATE runs SET status = ?, error = ? WHERE status = ?",
            (STATUS_ERROR, "Прогон прерван: сервер был остановлен", STATUS_RUNNING),
        )
    return cur.rowcount


def list_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT r.id, r.created_at, r.status, r.git_commit, r.ticker, r.data_start, r.data_end,"
        " group_concat(s.strategy, ', ') AS strategies, r.error"
        " FROM runs r LEFT JOIN results s ON s.run_id = r.id"
        " GROUP BY r.id ORDER BY r.id",
        conn,
    )


def load_meta(conn: sqlite3.Connection, run_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Возвращает (описание прогона, настройки прогона).
    В описании есть status и error; у идущего прогона данные о ценах пока пусты."""
    row = conn.execute(
        "SELECT created_at, git_commit, ticker, data_start, data_end, data_rows,"
        " data_hash, data_fetched_at, status, error, config_json FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise StorageError(f"Прогон №{run_id} не найден")
    keys = [f.name for f in fields(RunMeta)] + ["status", "error"]
    return dict(zip(keys, row[:-1], strict=True)), json.loads(row[-1])


def _require_done(run_id: int, meta: dict[str, Any]) -> None:
    if meta["status"] == STATUS_RUNNING:
        raise RunNotFinishedError(f"Прогон №{run_id} ещё идёт")
    if meta["status"] == STATUS_ERROR:
        raise RunNotFinishedError(f"Прогон №{run_id} завершился ошибкой: {meta['error']}")


def load_results(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    """Метрики стратегий прогона (без кривой капитала и сделок)."""
    meta, _ = load_meta(conn, run_id)
    _require_done(run_id, meta)
    cur = conn.execute(
        "SELECT strategy, final_value, total_return, cagr, max_drawdown, sharpe, n_trades"
        " FROM results WHERE run_id = ? ORDER BY rowid",
        (run_id,),
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def load_run(conn: sqlite3.Connection, run_id: int) -> StoredRun:
    meta, config = load_meta(conn, run_id)
    _require_done(run_id, meta)
    start_cash = float(config["run"]["cash"])
    results = []
    for strat in config["strategies"]:
        name = strat["name"]
        eq = pd.read_sql_query(
            "SELECT date, value FROM equity WHERE run_id = ? AND strategy = ? ORDER BY date",
            conn,
            params=(run_id, name),
            parse_dates=["date"],
        )
        trades = pd.read_sql_query(
            "SELECT date, side, qty, price, fee FROM trades"
            " WHERE run_id = ? AND strategy = ? ORDER BY rowid",
            conn,
            params=(run_id, name),
            parse_dates=["date"],
        )
        results.append(
            RunResult(
                name=name,
                equity=pd.Series(eq["value"].to_numpy(), index=pd.DatetimeIndex(eq["date"])),
                trades=trades[TRADE_COLUMNS],
                start_cash=start_cash,
            )
        )
    return StoredRun(run_id=run_id, meta=meta, config=config, results=results)


def metrics_table(conn: sqlite3.Connection, run_ids: list[int]) -> pd.DataFrame:
    """Метрики стратегий из нескольких прогонов в одной таблице."""
    for run_id in run_ids:
        meta, _ = load_meta(conn, run_id)  # выбросит StorageError, если прогона нет
        _require_done(run_id, meta)
    marks = ",".join("?" * len(run_ids))
    return pd.read_sql_query(
        "SELECT r.id AS run_id, r.ticker, s.strategy, s.final_value, s.total_return, s.cagr,"
        " s.max_drawdown, s.sharpe, s.n_trades"
        " FROM results s JOIN runs r ON r.id = s.run_id"
        f" WHERE s.run_id IN ({marks}) ORDER BY s.run_id, s.rowid",
        conn,
        params=run_ids,
    )
