"""Сохранение прогонов в SQLite, чтобы их можно было сравнивать между собой."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import pandas as pd

from .engine import TRADE_COLUMNS, RunResult
from .errors import SimError


class StorageError(SimError):
    """Нет базы с прогонами или нет нужного прогона."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    git_commit       TEXT,
    ticker           TEXT NOT NULL,
    data_start       TEXT NOT NULL,
    data_end         TEXT NOT NULL,
    data_rows        INTEGER NOT NULL,
    data_hash        TEXT NOT NULL,
    data_fetched_at  TEXT NOT NULL,
    config_json      TEXT NOT NULL
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


def connect(path: str | Path, must_exist: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if must_exist and not path.exists():
        raise StorageError(f"База прогонов не найдена: {path}. Сначала выполните sp500-sim run")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def save_run(
    conn: sqlite3.Connection, meta: RunMeta, config: dict[str, Any], results: list[RunResult]
) -> int:
    """Сохраняет прогон целиком одной транзакцией. Возвращает его номер."""
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (created_at, git_commit, ticker, data_start, data_end, data_rows,"
            " data_hash, data_fetched_at, config_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meta.created_at,
                meta.git_commit,
                meta.ticker,
                meta.data_start,
                meta.data_end,
                meta.data_rows,
                meta.data_hash,
                meta.data_fetched_at,
                json.dumps(config, ensure_ascii=False, sort_keys=True),
            ),
        )
        run_id = int(cur.lastrowid or 0)
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
                (
                    (run_id, r.name, day.strftime("%Y-%m-%d"), float(v))
                    for day, v in r.equity.items()
                ),
            )
            conn.executemany(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (run_id, r.name, t.date.strftime("%Y-%m-%d"), t.side, t.qty, t.price, t.fee)
                    for t in r.trades.itertuples(index=False)
                ),
            )
    return run_id


def list_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT r.id, r.created_at, r.git_commit, r.ticker, r.data_start, r.data_end,"
        " group_concat(s.strategy, ', ') AS strategies"
        " FROM runs r LEFT JOIN results s ON s.run_id = r.id"
        " GROUP BY r.id ORDER BY r.id",
        conn,
    )


def load_meta(conn: sqlite3.Connection, run_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Возвращает (описание прогона, настройки прогона)."""
    row = conn.execute(
        "SELECT created_at, git_commit, ticker, data_start, data_end, data_rows,"
        " data_hash, data_fetched_at, config_json FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise StorageError(f"Прогон №{run_id} не найден")
    keys = [f.name for f in fields(RunMeta)]
    meta = dict(zip(keys, row[:-1], strict=True))
    return meta, json.loads(row[-1])


def load_run(conn: sqlite3.Connection, run_id: int) -> StoredRun:
    meta, config = load_meta(conn, run_id)
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
        load_meta(conn, run_id)  # выбросит StorageError, если прогона нет
    marks = ",".join("?" * len(run_ids))
    return pd.read_sql_query(
        "SELECT r.id AS run_id, r.ticker, s.strategy, s.final_value, s.total_return, s.cagr,"
        " s.max_drawdown, s.sharpe, s.n_trades"
        " FROM results s JOIN runs r ON r.id = s.run_id"
        f" WHERE s.run_id IN ({marks}) ORDER BY s.run_id, s.rowid",
        conn,
        params=run_ids,
    )
