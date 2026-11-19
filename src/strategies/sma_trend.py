import pandas as pd


class SmaTrend:
    """Держим акции, пока цена выше средней за n дней, иначе сидим в деньгах."""

    def __init__(self, n: int = 200):
        if not isinstance(n, int) or isinstance(n, bool) or n < 2:
            raise ValueError("n должно быть целым числом не меньше 2")
        self.n = n

    def target(self, hist: pd.DataFrame) -> float:
        if len(hist) < self.n:
            return 0.0
        close = hist["Close"]
        return 1.0 if close.iloc[-1] > close.iloc[-self.n :].mean() else 0.0
