"""
Симулятор торговли на историческом S&P 500 (дневные свечи).

Как устроен:
  данные -> цикл по дням -> стратегия -> брокер -> портфель -> метрики

Главное правило честности: решение принимается по цене закрытия дня i,
а исполняется по цене открытия дня i+1. Стратегия получает только
историю до дня i включительно, будущего у неё просто нет.

Установка:  pip install pandas numpy yfinance matplotlib
Запуск:     python sp500_sim.py            (SPY, с 2000 года)
            python sp500_sim.py --test     (проверка самого симулятора)
"""
import argparse
import os

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- данные

def load_prices(ticker="SPY", start="2000-01-01", end=None, cache=None):
    """Дневные цены. SPY - фонд на S&P 500, его можно купить.
    '^GSPC' - сам индекс (без дивидендов, купить нельзя, но для теста сойдёт).
    Цены с поправкой на сплиты и дивиденды. Если задан cache, скачанное
    сохраняется в CSV, и в следующий раз интернет не нужен."""
    if cache and os.path.exists(cache):
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    import yfinance as yf
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    if cache:
        df.to_csv(cache)
    return df


# ------------------------------------------------------------- стратегии
# Стратегия получает историю до сегодняшнего закрытия и возвращает
# желаемую долю портфеля в акциях: 0.0 (всё в деньгах) ... 1.0 (всё в акциях).

class BuyAndHold:
    def target(self, hist):
        return 1.0


class SmaTrend:
    """Держим акции, пока цена выше средней за n дней, иначе сидим в деньгах."""

    def __init__(self, n=200):
        self.n = n

    def target(self, hist):
        if len(hist) < self.n:
            return 0.0
        close = hist["Close"]
        return 1.0 if close.iloc[-1] > close.iloc[-self.n:].mean() else 0.0


# ------------------------------------------------------- сам симулятор

def run(df, strategy, cash=10_000.0, commission=0.0005, slippage=0.0005,
        min_change=0.01):
    """
    commission  - комиссия с каждой сделки (0.0005 = 0.05%)
    slippage    - цена хуже на эту долю: покупаем дороже, продаём дешевле
    min_change  - не торгуем, если доля в акциях меняется меньше чем на 1%
                  (иначе каждый день будут мелкие докупки)
    """
    start_cash = cash
    shares = 0.0
    wanted = None            # решение, принятое вчера на закрытии
    equity, trades = [], []

    for i in range(len(df)):
        day = df.index[i]
        row = df.iloc[i]

        # 1. Исполняем вчерашнее решение по цене открытия сегодня
        if wanted is not None:
            px = row["Open"]
            total = cash + shares * px
            current_w = shares * px / total
            if abs(wanted - current_w) > min_change:
                delta = wanted * total / px - shares          # сколько акций докупить (+) или продать (-)
                if delta > 0:
                    buy_px = px * (1 + slippage)
                    qty = min(delta, cash / (buy_px * (1 + commission)))
                    cost = qty * buy_px * (1 + commission)
                    cash -= cost
                    shares += qty
                    trades.append((day, "BUY", qty, buy_px))
                else:
                    sell_px = px * (1 - slippage)
                    qty = min(-delta, shares)
                    cash += qty * sell_px * (1 - commission)
                    shares -= qty
                    trades.append((day, "SELL", qty, sell_px))

        # 2. Оцениваем портфель по закрытию
        equity.append(cash + shares * row["Close"])

        # 3. Стратегия видит историю до сегодня включительно
        wanted = strategy.target(df.iloc[: i + 1])

    return pd.Series(equity, index=df.index, name="equity"), trades, start_cash


# ---------------------------------------------------------------- отчёт

def metrics(equity, n_trades, start_cash):
    daily = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    final = equity.iloc[-1]
    return {
        "Итог, $": round(final, 2),
        "Доходность, %": round((final / start_cash - 1) * 100, 1),
        "В год (CAGR), %": round(((final / start_cash) ** (1 / years) - 1) * 100, 1),
        "Макс. просадка, %": round((equity / equity.cummax() - 1).min() * 100, 1),
        "Sharpe": round(daily.mean() / daily.std() * np.sqrt(252), 2),
        "Сделок": n_trades,
    }


def report(results, plot=True):
    table = pd.DataFrame({name: metrics(*vals) for name, vals in results.items()})
    print(table.to_string())

    if plot:
        import matplotlib.pyplot as plt
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1]})
        for name, (eq, _, cash0) in results.items():
            a1.plot(eq / cash0, label=name)
            a2.plot(eq / eq.cummax() - 1, label=name)
        a1.set_yscale("log")
        a1.set_title("Капитал (старт = 1, лог. шкала)")
        a2.set_title("Просадка")
        a1.legend()
        plt.tight_layout()
        plt.show()


# ---------------------------------------------------------------- тесты

def self_test():
    """Проверка симулятора на заранее известных ответах."""
    rng = np.random.default_rng(1)
    n = 500
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    df = pd.DataFrame({"Open": open_, "High": close, "Low": close,
                       "Close": close, "Volume": 1e6},
                      index=pd.bdate_range("2020-01-01", periods=n))

    # Купить и держать без комиссий: ровно рост цены от открытия
    # второго дня (первое исполнение) до последнего закрытия
    eq, trades, c0 = run(df, BuyAndHold(), commission=0, slippage=0)
    expected = c0 * df["Close"].iloc[-1] / df["Open"].iloc[1]
    assert abs(eq.iloc[-1] - expected) < 1e-6, (eq.iloc[-1], expected)
    assert len(trades) == 1

    # Стратегия, которая ничего не делает, не трогает деньги
    class Idle:
        def target(self, hist):
            return 0.0
    eq, trades, c0 = run(df, Idle())
    assert (eq == c0).all() and not trades

    # Комиссия всегда уменьшает результат
    free = run(df, SmaTrend(50), commission=0, slippage=0)[0].iloc[-1]
    paid = run(df, SmaTrend(50), commission=0.001, slippage=0.001)[0].iloc[-1]
    assert paid < free

    print("Все тесты симулятора прошли")


# ----------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--cache", default="prices.csv")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if args.test:
        self_test()
    else:
        prices = load_prices(args.ticker, args.start, cache=args.cache)
        results = {}
        for name, strat in [("Купить и держать", BuyAndHold()),
                            ("Тренд SMA-200", SmaTrend(200))]:
            eq, trades, cash0 = run(prices, strat)
            results[name] = (eq, len(trades), cash0)
        report(results, plot=not args.no_plot)