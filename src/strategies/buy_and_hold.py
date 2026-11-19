import pandas as pd


class BuyAndHold:
    """Купить в первый же день и держать."""

    def target(self, hist: pd.DataFrame) -> float:
        return 1.0
