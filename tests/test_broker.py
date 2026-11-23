import pandas as pd
import pytest

from sp500_sim.broker import Broker
from sp500_sim.portfolio import Portfolio

DAY = pd.Timestamp("2024-01-02")


def rebalance(pf, targets, opens, **costs):
    settings = {"commission": 0, "slippage": 0, "min_change": 0.01, **costs}
    Broker(**settings).rebalance(pf, DAY, targets, opens)


def test_buy_applies_slippage_and_commission():
    pf = Portfolio(1000)
    rebalance(pf, {"A": 1.0}, {"A": 100.0}, commission=0.001, slippage=0.01)
    price = 101.0  # открытие 100 плюс проскальзывание 1%
    qty = 1000 / (price * 1.001)
    assert pf.positions["A"] == pytest.approx(qty)
    assert pf.cash == pytest.approx(0, abs=1e-9)
    assert pf.trades[0].fee == pytest.approx(qty * price * 0.001)
    assert pf.trades[0].ticker == "A"


def test_sell_applies_slippage_and_commission():
    pf = Portfolio(0)
    pf.positions["A"] = 10
    rebalance(pf, {"A": 0.0}, {"A": 100.0}, commission=0.001, slippage=0.01)
    price = 99.0
    assert pf.positions["A"] == pytest.approx(0)
    assert pf.cash == pytest.approx(10 * price * (1 - 0.001))


def test_small_change_is_ignored():
    pf = Portfolio(5)
    pf.positions["A"] = 9.95  # в акциях 99.5% портфеля
    rebalance(pf, {"A": 1.0}, {"A": 100.0})
    assert pf.trades == []


def test_cannot_buy_more_than_cash_allows():
    pf = Portfolio(100)
    rebalance(pf, {"A": 1.0}, {"A": 50.0}, commission=0.01, min_change=0)
    assert pf.cash >= -1e-9


def test_buys_several_assets_by_weight():
    pf = Portfolio(1000)
    rebalance(pf, {"A": 0.6, "B": 0.4}, {"A": 10.0, "B": 20.0})
    assert pf.positions == pytest.approx({"A": 60.0, "B": 20.0})
    assert pf.cash == pytest.approx(0, abs=1e-9)


def test_keeps_part_in_cash_when_weights_sum_below_one():
    pf = Portfolio(1000)
    rebalance(pf, {"A": 0.3, "B": 0.3}, {"A": 10.0, "B": 10.0})
    assert pf.cash == pytest.approx(400)


def test_sells_one_asset_to_fund_another():
    pf = Portfolio(0)
    pf.positions = {"A": 100.0, "B": 0.0}  # всё в A, стоит 1000
    rebalance(pf, {"A": 0.5, "B": 0.5}, {"A": 10.0, "B": 25.0})
    assert pf.positions == pytest.approx({"A": 50.0, "B": 20.0})
    assert [t.side for t in pf.trades] == ["SELL", "BUY"]  # сначала продажа


def test_purchases_shrink_proportionally_when_cash_is_short():
    pf = Portfolio(1000)
    rebalance(pf, {"A": 0.5, "B": 0.5}, {"A": 10.0, "B": 10.0}, commission=0.01, min_change=0)
    assert pf.cash >= -1e-9
    assert pf.positions["A"] == pytest.approx(pf.positions["B"])  # пропорции сохранились
