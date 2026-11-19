"""Главный цикл: идёт по дням, спрашивает стратегию, отдаёт приказы брокеру."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import cached_property

import pandas as pd

from .broker import Broker
from .errors import SimError
from .metrics import compute_metrics
from .portfolio import Portfolio
from .strategies import Strategy

log = logging.getLogger(__name__)

TRADE_COLUMNS = ["date", "side", "qty", "price", "fee"]


class EngineError(SimError):
    """Стратегия вернула недопустимое значение."""


@dataclass
class RunResult:
    name: str
    equity: pd.Series  # стоимость портфеля по закрытиям
    trades: pd.DataFrame  # колонки TRADE_COLUMNS
    start_cash: float

    @cached_property
    def metrics(self) -> dict[str, float]:
        return compute_metrics(self.equity, len(self.trades), self.start_cash)


def _check_target(value: object, name: str, day: pd.Timestamp) -> float:
    try:
        weight = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise EngineError(f"{name}, {day.date()}: стратегия вернула не число: {value!r}") from None
    if math.isnan(weight) or not 0.0 <= weight <= 1.0:
        raise EngineError(f"{name}, {day.date()}: доля акций {weight} вне диапазона 0..1")
    return weight


def run_strategy(
    df: pd.DataFrame,
    strategy: Strategy,
    name: str,
    start_cash: float,
    broker: Broker,
) -> RunResult:
    """Решение принимается по закрытию дня i, исполняется по открытию дня i+1.
    Стратегия получает только df[: i + 1], будущее ей недоступно."""
    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    pf = Portfolio(start_cash)
    equity: list[float] = []
    wanted: float | None = None

    for i, day in enumerate(df.index):
        if wanted is not None:
            broker.execute(pf, day, wanted, opens[i])
        equity.append(pf.value(closes[i]))
        wanted = _check_target(strategy.target(df.iloc[: i + 1]), name, day)

    trades = pd.DataFrame(
        [(t.date, t.side, t.qty, t.price, t.fee) for t in pf.trades], columns=TRADE_COLUMNS
    )
    log.info("%s: сделок %d, итог %.2f", name, len(trades), equity[-1])
    return RunResult(
        name=name,
        equity=pd.Series(equity, index=df.index, name="equity"),
        trades=trades,
        start_cash=start_cash,
    )
