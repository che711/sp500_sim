"""Метрики результата."""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

LABELS = {
    "final_value": "Итог, $",
    "total_return": "Доходность",
    "cagr": "В год (CAGR)",
    "max_drawdown": "Макс. просадка",
    "sharpe": "Sharpe",
    "n_trades": "Сделок",
}


def compute_metrics(equity: pd.Series, n_trades: int, start_cash: float) -> dict[str, float]:
    final = float(equity.iloc[-1])
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        cagr = 0.0
    elif final <= 0:
        cagr = -1.0
    else:
        cagr = (final / start_cash) ** (1 / years) - 1

    daily = equity.pct_change().dropna()
    std = daily.std()
    sharpe = (
        float(daily.mean() / std * np.sqrt(TRADING_DAYS)) if len(daily) > 1 and std > 0 else 0.0
    )

    return {
        "final_value": final,
        "total_return": final / start_cash - 1,
        "cagr": float(cagr),
        "max_drawdown": float((equity / equity.cummax() - 1).min()),
        "sharpe": sharpe,
        "n_trades": int(n_trades),
    }


def yearly_returns(equity: pd.Series, start_cash: float) -> pd.Series:
    """Доходность по календарным годам. Первый год считается от стартовых денег."""
    last = equity.groupby(equity.index.year).last()
    prev = last.shift(1)
    prev.iloc[0] = start_cash
    return last / prev - 1


def fmt_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_metrics(m: dict[str, float]) -> dict[str, str]:
    """Метрики в виде готовых для показа строк, в порядке LABELS."""
    return {
        LABELS["final_value"]: fmt_money(m["final_value"]),
        LABELS["total_return"]: fmt_pct(m["total_return"]),
        LABELS["cagr"]: fmt_pct(m["cagr"]),
        LABELS["max_drawdown"]: fmt_pct(m["max_drawdown"]),
        LABELS["sharpe"]: f"{m['sharpe']:.2f}",
        LABELS["n_trades"]: str(int(m["n_trades"])),
    }
