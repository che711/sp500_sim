"""Портфель: деньги, позиции по бумагам и история сделок."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Trade:
    date: pd.Timestamp
    ticker: str
    side: str  # "BUY" или "SELL"
    qty: float
    price: float  # цена исполнения, уже с проскальзыванием
    fee: float  # комиссия в деньгах


class Portfolio:
    def __init__(self, cash: float):
        self.cash = float(cash)
        self.positions: dict[str, float] = {}  # тикер -> число акций
        self.trades: list[Trade] = []

    def value(self, prices: Mapping[str, float]) -> float:
        """Стоимость всего портфеля: деньги плюс позиции по заданным ценам."""
        return self.cash + sum(qty * prices[t] for t, qty in self.positions.items())

    def weight(self, ticker: str, prices: Mapping[str, float]) -> float:
        return self.positions.get(ticker, 0.0) * prices[ticker] / self.value(prices)

    def buy(self, date: pd.Timestamp, ticker: str, qty: float, price: float, fee: float) -> None:
        self.cash -= qty * price + fee
        self.positions[ticker] = self.positions.get(ticker, 0.0) + qty
        self.trades.append(Trade(date, ticker, "BUY", qty, price, fee))

    def sell(self, date: pd.Timestamp, ticker: str, qty: float, price: float, fee: float) -> None:
        self.cash += qty * price - fee
        self.positions[ticker] = self.positions.get(ticker, 0.0) - qty
        self.trades.append(Trade(date, ticker, "SELL", qty, price, fee))
