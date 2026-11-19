import pytest

from sp500_sim import storage
from sp500_sim.broker import Broker
from sp500_sim.compare import config_diff
from sp500_sim.config import parse_config
from sp500_sim.engine import run_strategy
from sp500_sim.storage import RunMeta
from sp500_sim.strategies import BuyAndHold, SmaTrend

META = RunMeta(
    created_at="2026-01-01T00:00:00+00:00",
    git_commit="abc1234",
    ticker="TEST",
    data_start="2020-01-01",
    data_end="2021-11-12",
    data_rows=500,
    data_hash="deadbeef",
    data_fetched_at="2026-01-01T00:00:00+00:00",
)
RAW = {
    "strategies": [
        {"name": "bh", "type": "buy_and_hold"},
        {"name": "sma", "type": "sma_trend", "n": 50},
    ]
}


@pytest.fixture
def results(prices):
    broker = Broker(0.0005, 0.0005, 0.01)
    return [
        run_strategy(prices, BuyAndHold(), "bh", 10_000, broker),
        run_strategy(prices, SmaTrend(50), "sma", 10_000, broker),
    ]


@pytest.fixture
def conn(tmp_path):
    connection = storage.connect(tmp_path / "db" / "results.db")
    yield connection
    connection.close()


def test_roundtrip(conn, results):
    config = parse_config(RAW).to_dict()
    run_id = storage.save_run(conn, META, config, results)
    loaded = storage.load_run(conn, run_id)

    assert loaded.meta["git_commit"] == "abc1234"
    assert loaded.meta["data_hash"] == "deadbeef"
    assert [r.name for r in loaded.results] == ["bh", "sma"]
    for original, restored in zip(results, loaded.results, strict=True):
        assert restored.equity.to_numpy() == pytest.approx(original.equity.to_numpy())
        assert restored.equity.index.equals(original.equity.index)
        assert len(restored.trades) == len(original.trades)
        assert restored.metrics["final_value"] == pytest.approx(original.metrics["final_value"])
        assert restored.metrics["n_trades"] == original.metrics["n_trades"]


def test_run_ids_increase_and_are_listed(conn, results):
    config = parse_config(RAW).to_dict()
    first = storage.save_run(conn, META, config, results)
    second = storage.save_run(conn, META, config, results)
    assert (first, second) == (1, 2)
    listing = storage.list_runs(conn)
    assert list(listing["id"]) == [1, 2]
    assert listing["strategies"].iloc[0] == "bh, sma"


def test_missing_run(conn):
    with pytest.raises(storage.StorageError, match="№5"):
        storage.load_run(conn, 5)


def test_missing_database(tmp_path):
    with pytest.raises(storage.StorageError, match="не найдена"):
        storage.connect(tmp_path / "нет.db", must_exist=True)


def test_metrics_table(conn, results):
    config = parse_config(RAW).to_dict()
    storage.save_run(conn, META, config, results)
    table = storage.metrics_table(conn, [1])
    assert list(table["strategy"]) == ["bh", "sma"]
    assert table["final_value"].iloc[0] == pytest.approx(results[0].metrics["final_value"])


def test_config_diff_shows_only_differences(conn, results):
    a = parse_config(RAW).to_dict()
    raw_b = {
        "broker": {"commission": 0.002},
        "strategies": [
            {"name": "bh", "type": "buy_and_hold"},
            {"name": "sma", "type": "sma_trend", "n": 100},
        ],
    }
    b = parse_config(raw_b).to_dict()
    id_a = storage.save_run(conn, META, a, results)
    id_b = storage.save_run(conn, META, b, results)
    diff = config_diff({i: storage.load_meta(conn, i) for i in (id_a, id_b)})
    assert set(diff) == {"broker.commission", "strategy[sma]"}
    assert diff["broker.commission"] == {id_a: 0.0005, id_b: 0.002}


def test_config_diff_of_identical_runs_is_empty(conn, results):
    config = parse_config(RAW).to_dict()
    ids = [storage.save_run(conn, META, config, results) for _ in range(2)]
    assert config_diff({i: storage.load_meta(conn, i) for i in ids}) == {}


# ------------------------------------------------- статусы, миграция, WAL


def test_run_lifecycle(conn, results):
    config = parse_config(RAW).to_dict()
    run_id = storage.create_run(conn, config, "TEST")
    meta, _ = storage.load_meta(conn, run_id)
    assert meta["status"] == "running"
    assert meta["data_hash"] is None
    with pytest.raises(storage.RunNotFinishedError, match="ещё идёт"):
        storage.load_run(conn, run_id)

    storage.finish_run(conn, run_id, META, results)
    meta, _ = storage.load_meta(conn, run_id)
    assert meta["status"] == "done" and meta["data_hash"] == "deadbeef"
    assert [r.name for r in storage.load_run(conn, run_id).results] == ["bh", "sma"]
    assert [r["strategy"] for r in storage.load_results(conn, run_id)] == ["bh", "sma"]


def test_finish_requires_a_running_run(conn, results):
    config = parse_config(RAW).to_dict()
    run_id = storage.save_run(conn, META, config, results)  # уже готов
    with pytest.raises(storage.StorageError):
        storage.finish_run(conn, run_id, META, results)


def test_failed_run(conn):
    run_id = storage.create_run(conn, parse_config(RAW).to_dict(), "TEST")
    storage.fail_run(conn, run_id, "нет данных")
    meta, _ = storage.load_meta(conn, run_id)
    assert (meta["status"], meta["error"]) == ("error", "нет данных")
    with pytest.raises(storage.RunNotFinishedError, match="нет данных"):
        storage.metrics_table(conn, [run_id])


def test_mark_interrupted_touches_only_running_runs(conn, results):
    config = parse_config(RAW).to_dict()
    done = storage.save_run(conn, META, config, results)
    running = storage.create_run(conn, config, "TEST")
    assert storage.mark_interrupted(conn) == 1
    assert storage.load_meta(conn, done)[0]["status"] == "done"
    assert storage.load_meta(conn, running)[0]["status"] == "error"


def test_list_runs_has_status_and_error(conn):
    run_id = storage.create_run(conn, parse_config(RAW).to_dict(), "TEST")
    storage.fail_run(conn, run_id, "сломалось")
    listing = storage.list_runs(conn)
    assert listing["status"].iloc[0] == "error"
    assert listing["error"].iloc[0] == "сломалось"


def test_database_uses_wal(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


OLD_SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, git_commit TEXT,
    ticker TEXT NOT NULL, data_start TEXT NOT NULL, data_end TEXT NOT NULL,
    data_rows INTEGER NOT NULL, data_hash TEXT NOT NULL, data_fetched_at TEXT NOT NULL,
    config_json TEXT NOT NULL
);
CREATE TABLE results (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, strategy TEXT NOT NULL,
    final_value REAL NOT NULL, total_return REAL NOT NULL, cagr REAL NOT NULL,
    max_drawdown REAL NOT NULL, sharpe REAL NOT NULL, n_trades INTEGER NOT NULL,
    PRIMARY KEY (run_id, strategy)
);
CREATE TABLE equity (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, strategy TEXT NOT NULL,
    date TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (run_id, strategy, date)
) WITHOUT ROWID;
CREATE TABLE trades (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, strategy TEXT NOT NULL,
    date TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL,
    fee REAL NOT NULL
);
"""


def test_old_database_is_migrated(tmp_path):
    import json
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    config = parse_config(RAW).to_dict()
    old.execute(
        "INSERT INTO runs VALUES (7, '2026-01-01T00:00:00+00:00', 'abc1234', 'SPY', '2020-01-01',"
        " '2020-01-03', 3, 'cafe', '2026-01-01T00:00:00+00:00', ?)",
        (json.dumps(config),),
    )
    old.execute("INSERT INTO results VALUES (7, 'bh', 11000, 0.1, 0.1, -0.05, 1.2, 1)")
    old.execute("INSERT INTO results VALUES (7, 'sma', 10500, 0.05, 0.05, -0.02, 0.9, 2)")
    for day, value in [("2020-01-01", 10000), ("2020-01-02", 10500), ("2020-01-03", 11000)]:
        old.execute("INSERT INTO equity VALUES (7, 'bh', ?, ?)", (day, value))
        old.execute("INSERT INTO equity VALUES (7, 'sma', ?, ?)", (day, value))
    old.execute("INSERT INTO trades VALUES (7, 'bh', '2020-01-02', 'BUY', 10, 100, 0.5)")
    old.commit()
    old.close()

    conn = storage.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        meta, _ = storage.load_meta(conn, 7)
        assert (meta["status"], meta["git_commit"], meta["data_hash"]) == (
            "done",
            "abc1234",
            "cafe",
        )

        run = storage.load_run(conn, 7)  # кривая капитала и сделки на месте
        assert [r.name for r in run.results] == ["bh", "sma"]
        assert len(run.results[0].equity) == 3 and len(run.results[0].trades) == 1

        # Новая схема работает: прогон без данных о ценах и продолжение нумерации
        new_id = storage.create_run(conn, config, "SPY")
        assert new_id == 8
        assert storage.list_runs(conn)["status"].tolist() == ["done", "running"]

        # Внешние ключи не сломаны: удаление прогона убирает его строки
        conn.execute("DELETE FROM runs WHERE id = 7")
        assert conn.execute("SELECT COUNT(*) FROM equity").fetchone()[0] == 0
    finally:
        conn.close()
