"""Брокер: решает, по каким ценам и сколько реально купить или продать."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import pandas as pd

from .portfolio import Portfolio

log = logging.getLogger(__name__)


class Broker:
    def __init__(self, commission: float, slippage: float, min_change: float):
        self.commission = commission
        self.slippage = slippage
        self.min_change = min_change

    def rebalance(
        self,
        pf: Portfolio,
        date: pd.Timestamp,
        targets: Mapping[str, float],
        opens: Mapping[str, float],
    ) -> None:
        """Приводит доли бумаг в портфеле к целевым (доля от всего портфеля, остальное деньги).
        Исполнение по ценам открытия. Сначала продажи, потом покупки на вырученные деньги."""
        total = pf.value(opens)
        sells: dict[str, float] = {}
        buys: dict[str, float] = {}
        for ticker, target in targets.items():
            if abs(target - pf.weight(ticker, opens)) <= self.min_change:
                continue  # слишком мелкое изменение, не торгуем
            delta = target * total / opens[ticker] - pf.positions.get(ticker, 0.0)
            if delta > 0:
                buys[ticker] = delta
            else:
                sells[ticker] = -delta

        for ticker, qty in sells.items():
            qty = min(qty, pf.positions.get(ticker, 0.0))
            if qty <= 0:
                continue
            price = opens[ticker] * (1 - self.slippage)
            pf.sell(date, ticker, qty, price, qty * price * self.commission)
            log.debug("%s SELL %s %.4f по %.4f", date.date(), ticker, qty, price)

        # Если денег не хватает (комиссии и проскальзывание), все покупки уменьшаются поровну
        cost = {t: opens[t] * (1 + self.slippage) * (1 + self.commission) for t in buys}
        need = sum(qty * cost[t] for t, qty in buys.items())
        scale = min(1.0, pf.cash / need) if need > 0 else 1.0
        for ticker, qty in buys.items():
            qty *= scale
            if qty <= 0:
                continue
            price = opens[ticker] * (1 + self.slippage)
            pf.buy(date, ticker, qty, price, qty * price * self.commission)
            log.debug("%s BUY %s %.4f по %.4f", date.date(), ticker, qty, price)
