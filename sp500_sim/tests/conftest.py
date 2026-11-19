from concurrent.futures import Executor, Future

import numpy as np
import pandas as pd
import pytest

from sp500_sim.config import DataConfig, load_config
from sp500_sim.data import cache_file

CONFIG_TEXT = """
[data]
ticker = "TEST"
start = "2020-01-01"
end = "2022-01-01"
cache_dir = "data"

[broker]
commission = {commission}
slippage = 0.0005
min_change = 0.01

[run]
cash = 10000

[output]
dir = "results"

[[strategies]]
name = "Купить и держать"
type = "buy_and_hold"

[[strategies]]
name = "Тренд SMA-50"
type = "sma_trend"
n = 50
"""


@pytest.fixture
def prices() -> pd.DataFrame:
    """Искусственные цены на 500 рабочих дней. Результат одинаков при каждом запуске."""
    rng = np.random.default_rng(1)
    n = 500
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close),
            "Low": np.minimum(open_, close),
            "Close": close,
            "Volume": 1e6,
        },
        index=pd.bdate_range("2020-01-01", periods=n),
    )


@pytest.fixture
def write_config(tmp_path):
    def _write(commission: float = 0.0005):
        path = tmp_path / "config.toml"
        path.write_text(CONFIG_TEXT.format(commission=commission), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def project(tmp_path, monkeypatch, prices, write_config):
    """Рабочая папка с config.toml и готовым кэшем цен: интернет не нужен."""
    monkeypatch.chdir(tmp_path)
    write_config()
    cache = cache_file(DataConfig(ticker="TEST", start="2020-01-01", end="2022-01-01"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(cache, index_label="Date")
    return tmp_path


class InlineExecutor(Executor):
    """Выполняет фоновую задачу сразу, в том же потоке: в тестах не нужно ничего ждать."""

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class ManualExecutor(Executor):
    """Копит задачи и выполняет их по команде run_all: так можно увидеть статус «идёт»."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        self.jobs.append((future, fn, args, kwargs))
        return future

    def run_all(self):
        for future, fn, args, kwargs in self.jobs:
            future.set_result(fn(*args, **kwargs))
        self.jobs.clear()


def _make_client(executor):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sp500_sim.web.app import create_app

    app = create_app(
        results_dir="results",
        cache_dir="data",
        defaults=load_config("config.toml"),
        executor=executor,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def web(project):
    """Сайт с фоновыми задачами, которые выполняются сразу."""
    with _make_client(InlineExecutor()) as client:
        yield client


@pytest.fixture
def web_manual(project):
    """Сайт, где фоновая задача ждёт команды: (клиент, исполнитель)."""
    executor = ManualExecutor()
    with _make_client(executor) as client:
        yield client, executor
