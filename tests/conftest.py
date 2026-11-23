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


def _stock(prices, factor, shift=0, drop_first=0):
    """Другая «бумага»: те же даты и похожая динамика, но своя цена."""
    frame = prices.copy()
    frame[["Open", "High", "Low", "Close"]] *= factor
    frame[["Open", "High", "Low", "Close"]] *= 1 + shift * np.linspace(0, 1, len(frame))[:, None]
    return frame.iloc[drop_first:]


@pytest.fixture
def stocks(prices):
    """Три бумаги: AAA и BBB торгуются весь период, CCC появилась на 200 дней позже."""
    return {
        "AAA": _stock(prices, 1.5, shift=0.4),
        "BBB": _stock(prices, 0.7, shift=-0.2),
        "CCC": _stock(prices, 2.0, shift=0.1, drop_first=200),
    }


PORTFOLIO_CONFIG = """
[data]
ticker = "TEST"
start = "2020-01-01"
end = "2022-01-01"
cache_dir = "data"

[[strategies]]
name = "Мой портфель"
type = "buy_and_hold"
portfolio = {{ AAA = 60, BBB = 40 }}
rebalance = "yearly"

[[strategies]]
name = "Только TEST"
type = "buy_and_hold"
"""


@pytest.fixture
def portfolio_project(tmp_path, monkeypatch, prices, stocks):
    """Рабочая папка с кэшем цен для TEST и трёх бумаг портфеля, интернет не нужен."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(PORTFOLIO_CONFIG.format(), encoding="utf-8")
    for ticker, frame in {"TEST": prices, **stocks}.items():
        cache = cache_file(
            DataConfig(ticker=ticker, start="2020-01-01", end="2022-01-01"),
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache, index_label="Date")
    return tmp_path


@pytest.fixture
def web_portfolio(portfolio_project):
    """Сайт с готовым кэшем цен для портфеля из нескольких бумаг."""
    with _make_client(InlineExecutor()) as client:
        yield client
