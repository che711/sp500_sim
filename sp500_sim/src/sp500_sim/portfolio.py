"""Портфель: деньги, акции и история сделок."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Trade:
    date: pd.Timestamp
    side: str  # "BUY" или "SELL"
    qty: float
    price: float  # цена исполнения, уже с проскальзыванием
    fee: float  # комиссия в деньгах


class Portfolio:
    def __init__(self, cash: float):
        self.cash = float(cash)
        self.shares = 0.0
        self.trades: list[Trade] = []

    def value(self, price: float) -> float:
        return self.cash + self.shares * price

    def weight(self, price: float) -> float:
        return self.shares * price / self.value(price)

    def buy(self, date: pd.Timestamp, qty: float, price: float, fee: float) -> None:
        self.cash -= qty * price + fee
        self.shares += qty
        self.trades.append(Trade(date, "BUY", qty, price, fee))

    def sell(self, date: pd.Timestamp, qty: float, price: float, fee: float) -> None:
        self.cash += qty * price - fee
        self.shares -= qty
        self.trades.append(Trade(date, "SELL", qty, price, fee))
