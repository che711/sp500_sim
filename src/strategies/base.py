"""Общий интерфейс стратегии."""

from typing import Protocol

import pandas as pd


class Strategy(Protocol):
    def target(self, hist: pd.DataFrame) -> float:
        """Желаемая доля портфеля в акциях: от 0.0 (всё в деньгах) до 1.0 (всё в акциях).

        `hist` - цены (Open, High, Low, Close, Volume) до сегодняшнего закрытия
        включительно. Будущих данных здесь нет.
        """
        ...
