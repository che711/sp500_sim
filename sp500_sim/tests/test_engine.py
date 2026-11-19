import pytest

from sp500_sim.broker import Broker
from sp500_sim.engine import EngineError, run_strategy
from sp500_sim.strategies import BuyAndHold, SmaTrend

FREE = Broker(commission=0, slippage=0, min_change=0.01)


class Idle:
    def target(self, hist):
        return 0.0


def test_buy_and_hold_without_costs_equals_price_growth(prices):
    # Покупка по открытию 2-го дня, оценка по последнему закрытию
    result = run_strategy(prices, BuyAndHold(), "bh", 10_000, FREE)
    expected = 10_000 * prices["Close"].iloc[-1] / prices["Open"].iloc[1]
    assert result.equity.iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert len(result.trades) == 1


def test_idle_strategy_keeps_cash(prices):
    result = run_strategy(prices, Idle(), "idle", 10_000, FREE)
    assert (result.equity == 10_000).all()
    assert result.trades.empty


def test_costs_reduce_result(prices):
    costly = Broker(commission=0.001, slippage=0.001, min_change=0.01)
    free = run_strategy(prices, SmaTrend(50), "s", 10_000, FREE).equity.iloc[-1]
    paid = run_strategy(prices, SmaTrend(50), "s", 10_000, costly).equity.iloc[-1]
    assert paid < free


def test_order_executes_next_day_at_open(prices):
    result = run_strategy(prices, BuyAndHold(), "bh", 10_000, FREE)
    first = result.trades.iloc[0]
    assert first["date"] == prices.index[1]
    assert first["price"] == pytest.approx(prices["Open"].iloc[1])


def test_strategy_sees_only_past_and_present(prices):
    seen = []

    class Spy:
        def target(self, hist):
            seen.append(hist.index[-1])
            return 0.0

    run_strategy(prices, Spy(), "spy", 10_000, FREE)
    # На шаге i стратегия видит данные ровно до дня i, ни днём позже
    assert seen == list(prices.index)


def test_run_is_reproducible(prices):
    a = run_strategy(prices, SmaTrend(50), "s", 10_000, FREE)
    b = run_strategy(prices, SmaTrend(50), "s", 10_000, FREE)
    assert a.equity.equals(b.equity)
    assert a.trades.equals(b.trades)


@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), "много"])
def test_invalid_target_is_rejected(prices, bad):
    class Bad:
        def target(self, hist):
            return bad

    with pytest.raises(EngineError):
        run_strategy(prices, Bad(), "bad", 10_000, FREE)
