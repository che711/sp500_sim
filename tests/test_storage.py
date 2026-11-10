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
