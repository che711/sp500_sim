import pandas as pd
import pytest

from sp500_sim.broker import Broker
from sp500_sim.engine import _rebalance_due, basket_frame, run_portfolio
from sp500_sim.strategies import BuyAndHold, SmaTrend

FREE = Broker(commission=0, slippage=0, min_change=0.001)
WEIGHTS = {"AAA": 0.6, "BBB": 0.4}


def run(stocks, strategy=None, rebalance="never", broker=FREE, weights=WEIGHTS):
    assets = {t: stocks[t] for t in weights}
    return run_portfolio(assets, weights, strategy or BuyAndHold(), "p", 10_000, broker, rebalance)


def test_buy_and_hold_without_rebalancing_follows_each_asset(stocks):
    result = run(stocks)
    # Деньги делятся по весам при первой покупке (открытие 2-го дня), потом бумаги живут сами
    expected = sum(
        10_000 * w * stocks[t]["Close"].iloc[-1] / stocks[t]["Open"].iloc[1]
        for t, w in WEIGHTS.items()
    )
    assert result.equity.iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert len(result.trades) == 2  # по одной покупке на бумагу
    assert set(result.trades["ticker"]) == {"AAA", "BBB"}
    assert (result.trades["date"] == stocks["AAA"].index[1]).all()


def test_first_purchase_matches_weights(stocks):
    result = run(stocks)
    spent = result.trades.assign(cost=result.trades["qty"] * result.trades["price"])
    shares = spent.set_index("ticker")["cost"] / spent["cost"].sum()
    assert shares["AAA"] == pytest.approx(0.6)
    assert shares["BBB"] == pytest.approx(0.4)


def test_yearly_rebalance_trades_only_on_new_year(stocks):
    result = run(stocks, rebalance="yearly")
    index = stocks["AAA"].index
    new_year_days = {
        d for prev, d in zip(index[:-1], index[1:], strict=True) if prev.year != d.year
    }
    trade_days = set(result.trades["date"])
    assert trade_days <= new_year_days | {index[1]}
    assert trade_days & new_year_days  # ребалансировка действительно была
    assert len(result.trades) > 2


def test_more_frequent_rebalancing_means_more_trades(stocks):
    counts = [len(run(stocks, rebalance=mode).trades) for mode in ("never", "yearly", "monthly")]
    assert counts[0] < counts[1] < counts[2]


def test_rebalancing_costs_money(stocks):
    costly = Broker(commission=0.002, slippage=0.001, min_change=0.001)
    free = run(stocks, rebalance="monthly").equity.iloc[-1]
    paid = run(stocks, rebalance="monthly", broker=costly).equity.iloc[-1]
    assert paid < free


def test_exposure_change_moves_every_asset_at_once(stocks):
    result = run(stocks, strategy=SmaTrend(50))
    by_day = result.trades.groupby("date")["ticker"].apply(set)
    assert (by_day == {"AAA", "BBB"}).all()  # в каждый торговый день сделки по обеим бумагам


def test_starts_with_start_cash(stocks):
    assert run(stocks).equity.iloc[0] == 10_000


def test_one_asset_portfolio_behaves_like_single_asset(stocks):
    single = run(stocks, weights={"AAA": 1.0})
    assert len(single.trades) == 1
    assert single.equity.iloc[-1] == pytest.approx(
        10_000 * stocks["AAA"]["Close"].iloc[-1] / stocks["AAA"]["Open"].iloc[1]
    )


# ------------------------------------------------------------- индекс портфеля


def _two_days(a, b):
    index = pd.bdate_range("2024-01-01", periods=len(a))
    return {
        t: pd.DataFrame(
            {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": 1.0},
            index=index,
        )
        for t, prices in (("A", a), ("B", b))
    }


def test_basket_index_starts_at_100_and_follows_weighted_returns():
    frames = _two_days([100, 110, 121], [100, 90, 90])
    view = basket_frame(frames, {"A": 0.5, "B": 0.5})
    assert view["Close"].iloc[0] == 100
    assert view["Close"].iloc[1] == pytest.approx(100 * (1 + 0.5 * 0.10 + 0.5 * -0.10))
    assert view["Close"].iloc[2] == pytest.approx(100 * 1.0 * (1 + 0.5 * 0.10 + 0.5 * 0.0))


def test_one_asset_basket_is_the_asset_itself(stocks):
    assert basket_frame({"AAA": stocks["AAA"]}, {"AAA": 1.0}) is stocks["AAA"]


def test_basket_does_not_look_ahead(stocks):
    changed = {t: df.copy() for t, df in stocks.items()}
    for df in changed.values():
        df.iloc[-10:, df.columns.get_indexer(["Open", "High", "Low", "Close"])] *= 5
    before = basket_frame({t: stocks[t] for t in WEIGHTS}, WEIGHTS)
    after = basket_frame({t: changed[t] for t in WEIGHTS}, WEIGHTS)
    pd.testing.assert_frame_equal(before.iloc[:-10], after.iloc[:-10])


def test_strategy_sees_the_basket_index(stocks):
    seen = []

    class Spy:
        def target(self, hist):
            seen.append(hist["Close"].iloc[-1])
            return 0.0

    run(stocks, strategy=Spy())
    expected = basket_frame({t: stocks[t] for t in WEIGHTS}, WEIGHTS)["Close"]
    assert seen == pytest.approx(list(expected))


# ----------------------------------------------------------------- расписание


@pytest.mark.parametrize(
    ("mode", "previous", "day", "due"),
    [
        ("never", "2024-01-31", "2024-02-01", False),
        ("monthly", "2024-01-30", "2024-01-31", False),
        ("monthly", "2024-01-31", "2024-02-01", True),
        ("quarterly", "2024-02-29", "2024-03-01", False),
        ("quarterly", "2024-03-29", "2024-04-01", True),
        ("yearly", "2024-06-28", "2024-07-01", False),
        ("yearly", "2024-12-31", "2025-01-02", True),
    ],
)
def test_rebalance_due(mode, previous, day, due):
    assert _rebalance_due(mode, pd.Timestamp(previous), pd.Timestamp(day)) is due
