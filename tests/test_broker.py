import pandas as pd
import pytest

from sp500_sim.broker import Broker
from sp500_sim.portfolio import Portfolio

DAY = pd.Timestamp("2024-01-02")


def test_buy_applies_slippage_and_commission():
    pf = Portfolio(1000)
    Broker(commission=0.001, slippage=0.01, min_change=0.01).execute(pf, DAY, 1.0, 100.0)
    price = 101.0  # открытие 100 плюс проскальзывание 1%
    qty = 1000 / (price * 1.001)
    assert pf.shares == pytest.approx(qty)
    assert pf.cash == pytest.approx(0, abs=1e-9)
    assert pf.trades[0].fee == pytest.approx(qty * price * 0.001)


def test_sell_applies_slippage_and_commission():
    pf = Portfolio(0)
    pf.shares = 10
    Broker(commission=0.001, slippage=0.01, min_change=0.01).execute(pf, DAY, 0.0, 100.0)
    price = 99.0
    assert pf.shares == pytest.approx(0)
    assert pf.cash == pytest.approx(10 * price * (1 - 0.001))


def test_small_change_is_ignored():
    pf = Portfolio(5)
    pf.shares = 9.95  # в акциях 99.5% портфеля
    Broker(commission=0, slippage=0, min_change=0.01).execute(pf, DAY, 1.0, 100.0)
    assert pf.trades == []


def test_cannot_buy_more_than_cash_allows():
    pf = Portfolio(100)
    Broker(commission=0.01, slippage=0, min_change=0).execute(pf, DAY, 1.0, 50.0)
    assert pf.cash >= -1e-9
