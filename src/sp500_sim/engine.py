"""Главный цикл: идёт по дням, спрашивает стратегию, отдаёт приказы брокеру."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from .broker import Broker
from .config import Position
from .errors import SimError
from .metrics import compute_metrics
from .portfolio import Portfolio
from .strategies import Strategy

log = logging.getLogger(__name__)

TRADE_COLUMNS = ["date", "ticker", "side", "qty", "price", "fee"]


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


def basket_frame(frames: Mapping[str, pd.DataFrame], weights: Mapping[str, float]) -> pd.DataFrame:
    """Цены портфеля как одной «бумаги»: так стратегия видит набор тикеров.

    Это индекс набора с постоянными весами (старт = 100). Каждая строка считается только по
    данным этого дня и предыдущего, будущего в ней нет. У портфеля из одной бумаги
    возвращаются её собственные цены."""
    tickers = list(weights)
    if len(tickers) == 1:
        return frames[tickers[0]]
    w = pd.Series(weights)[tickers]
    close = pd.DataFrame({t: frames[t]["Close"] for t in tickers})
    open_ = pd.DataFrame({t: frames[t]["Open"] for t in tickers})
    prev = close.shift(1)
    index_close = 100 * (1 + ((close / prev - 1) * w).sum(axis=1)).cumprod()
    index_open = index_close.shift(1) * (1 + ((open_ / prev - 1) * w).sum(axis=1))
    index_open.iloc[0] = 100.0
    return pd.DataFrame(
        {
            "Open": index_open,
            "High": np.maximum(index_open, index_close),
            "Low": np.minimum(index_open, index_close),
            "Close": index_close,
            "Volume": sum(frames[t]["Volume"] for t in tickers),
        }
    )


def _rebalance_due(mode: str, previous: pd.Timestamp, day: pd.Timestamp) -> bool:
    """Наступил ли новый месяц, квартал или год по сравнению с прошлым торговым днём."""
    if mode == "monthly":
        return (previous.year, previous.month) != (day.year, day.month)
    if mode == "quarterly":
        return (previous.year, previous.quarter) != (day.year, day.quarter)
    if mode == "yearly":
        return previous.year != day.year
    return False


def run_portfolio(
    assets: Mapping[str, pd.DataFrame],
    weights: Mapping[str, float],
    strategy: Strategy,
    name: str,
    start_cash: float,
    broker: Broker,
    rebalance: str = "never",
) -> RunResult:
    """Решение принимается по закрытию дня i, исполняется по открытию дня i+1.
    Стратегия получает только историю до дня i включительно, будущее ей недоступно.

    weights - доли бумаг в портфеле (в сумме 1). Стратегия отдаёт одно число от 0 до 1:
    какая часть денег сейчас в портфеле. Когда оно меняется, все бумаги приводятся к
    своим долям. Иначе веса остаются как есть и подтягиваются к целевым только по
    расписанию rebalance (never, monthly, quarterly, yearly)."""
    tickers = list(weights)
    frames = {t: assets[t] for t in tickers}
    index = frames[tickers[0]].index
    opens = {t: frames[t]["Open"].to_numpy(dtype=float) for t in tickers}
    closes = {t: frames[t]["Close"].to_numpy(dtype=float) for t in tickers}
    view = basket_frame(frames, weights)

    pf = Portfolio(start_cash)
    equity: list[float] = []
    wanted: float | None = None
    applied: float | None = None

    for i, day in enumerate(index):
        if wanted is not None:
            due = (
                applied is None or wanted != applied or _rebalance_due(rebalance, index[i - 1], day)
            )
            if due:
                targets = {t: wanted * weights[t] for t in tickers}
                broker.rebalance(pf, day, targets, {t: opens[t][i] for t in tickers})
                applied = wanted
        equity.append(pf.value({t: closes[t][i] for t in tickers}))
        wanted = _check_target(strategy.target(view.iloc[: i + 1]), name, day)

    trades = pd.DataFrame(
        [(t.date, t.ticker, t.side, t.qty, t.price, t.fee) for t in pf.trades],
        columns=TRADE_COLUMNS,
    )
    log.info("%s: сделок %d, итог %.2f", name, len(trades), equity[-1])
    return RunResult(
        name=name,
        equity=pd.Series(equity, index=index, name="equity"),
        trades=trades,
        start_cash=start_cash,
    )


def run_strategy(
    df: pd.DataFrame, strategy: Strategy, name: str, start_cash: float, broker: Broker
) -> RunResult:
    """Одна бумага целиком (ticker в сделках пустой)."""
    return run_portfolio({"": df}, {"": 1.0}, strategy, name, start_cash, broker)


def run_holdings(
    frames: Mapping[str, pd.DataFrame], positions: list[Position], name: str
) -> RunResult:
    """Как менялась бы стоимость ваших нынешних акций в прошлом: количество на цену.

    Сделок нет: количество акций считается неизменным. Цены берутся из данных, курсы валют не
    учитываются. Результат не ваша фактическая доходность: раньше эти акции могли быть не у вас.
    Средняя цена покупки на кривую не влияет, она нужна для вложенной суммы и прибыли."""
    index = next(iter(frames.values())).index
    total = pd.Series(0.0, index=index)
    for position in positions:
        total = total + position.qty * frames[position.ticker]["Close"]
    log.info("%s: стоимость сейчас %.2f", name, total.iloc[-1])
    return RunResult(
        name=name,
        equity=total.rename("equity"),
        trades=pd.DataFrame(columns=TRADE_COLUMNS),
        start_cash=float(total.iloc[0]),
    )
