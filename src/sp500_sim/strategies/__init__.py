"""Стратегии. Чтобы добавить свою: напишите класс с методом target(hist)
и внесите его в REGISTRY, тогда её можно указывать в config.toml по имени type."""

from .base import Strategy
from .buy_and_hold import BuyAndHold
from .sma_trend import SmaTrend

REGISTRY: dict[str, type] = {
    "buy_and_hold": BuyAndHold,
    "sma_trend": SmaTrend,
}

__all__ = ["REGISTRY", "BuyAndHold", "SmaTrend", "Strategy"]
