import pandas as pd
import pytest

from sp500_sim.metrics import compute_metrics, yearly_returns


def _series(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


def test_max_drawdown():
    m = compute_metrics(_series([100, 120, 60, 90]), 0, 100)
    assert m["max_drawdown"] == pytest.approx(-0.5)
    assert m["total_return"] == pytest.approx(-0.1)


def test_cagr_of_doubling_in_one_year():
    equity = pd.Series([100.0, 200.0], index=pd.to_datetime(["2020-01-01", "2021-01-01"]))
    assert compute_metrics(equity, 0, 100)["cagr"] == pytest.approx(1.0, abs=0.01)


def test_flat_equity_has_zero_sharpe():
    assert compute_metrics(_series([100] * 10), 0, 100)["sharpe"] == 0.0


def test_yearly_returns():
    equity = pd.Series(
        [100.0, 110.0, 99.0],
        index=pd.to_datetime(["2020-06-01", "2020-12-31", "2021-12-31"]),
    )
    yearly = yearly_returns(equity, 100)
    assert yearly[2020] == pytest.approx(0.10)
    assert yearly[2021] == pytest.approx(-0.10)
