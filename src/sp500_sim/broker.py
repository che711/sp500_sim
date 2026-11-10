"""Брокер: решает, по какой цене и сколько реально купить или продать."""

from __future__ import annotations

import logging

import pandas as pd

from .portfolio import Portfolio

log = logging.getLogger(__name__)


class Broker:
    def __init__(self, commission: float, slippage: float, min_change: float):
        self.commission = commission
        self.slippage = slippage
        self.min_change = min_change

    def execute(
        self, pf: Portfolio, date: pd.Timestamp, target_weight: float, open_price: float
    ) -> None:
        """Приводит долю акций в портфеле к желаемой. Исполнение по цене открытия."""
        total = pf.value(open_price)
        if abs(target_weight - pf.weight(open_price)) <= self.min_change:
            return

        delta = target_weight * total / open_price - pf.shares  # >0 купить, <0 продать
        if delta > 0:
            price = open_price * (1 + self.slippage)
            qty = min(delta, pf.cash / (price * (1 + self.commission)))
            if qty <= 0:
                return
            pf.buy(date, qty, price, qty * price * self.commission)
            log.debug("%s BUY %.4f по %.4f", date.date(), qty, price)
        else:
            price = open_price * (1 - self.slippage)
            qty = min(-delta, pf.shares)
            if qty <= 0:
                return
            pf.sell(date, qty, price, qty * price * self.commission)
            log.debug("%s SELL %.4f по %.4f", date.date(), qty, price)
