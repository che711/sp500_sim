"""Отчёт в HTML: таблицы и графики в одном файле, без внешних зависимостей."""

from __future__ import annotations

import html
import io
from typing import Any

import pandas as pd
from matplotlib.figure import Figure

from .engine import RunResult
from .metrics import fmt_money, fmt_pct, format_metrics, yearly_returns

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2"]
MAX_TRADE_ROWS = 1000

CSS = """
body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem;
       color: #111827; line-height: 1.45; }
h1 { margin-bottom: .2rem; }
h2 { margin-top: 2.2rem; border-bottom: 1px solid #e5e7eb; padding-bottom: .3rem; }
.sub { color: #6b7280; margin-top: 0; }
table { border-collapse: collapse; margin: .8rem 0; }
th, td { border: 1px solid #e5e7eb; padding: .35rem .7rem; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { background: #f3f4f6; }
.chart { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; padding: .5rem; }
.chart svg { width: 100%; height: auto; }
.params td:last-child { text-align: left; }
.scroll { max-height: 420px; overflow: auto; }
.buy { color: #15803d; }
.sell { color: #b91c1c; }
details { margin: .6rem 0; }
summary { cursor: pointer; font-weight: 600; }
.note { color: #6b7280; font-size: .9rem; }
.neg { color: #b91c1c; }
"""


def _esc(value: object) -> str:
    return html.escape(str(value))


def _svg(fig: Figure) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg")
    svg = buf.getvalue()
    return svg[svg.index("<svg") :]


def _chart(results: list[RunResult], kind: str) -> str:
    fig = Figure(figsize=(10, 4), layout="constrained")
    ax = fig.subplots()
    for i, r in enumerate(results):
        color = PALETTE[i % len(PALETTE)]
        if kind == "equity":
            ax.plot(
                r.equity.index.to_numpy(),
                (r.equity / r.start_cash).to_numpy(),
                label=r.name,
                color=color,
                linewidth=1.4,
            )
        else:
            dd = (r.equity / r.equity.cummax() - 1) * 100
            ax.plot(dd.index.to_numpy(), dd.to_numpy(), label=r.name, color=color, linewidth=1.2)
    if kind == "equity":
        ax.set_yscale("log")
        ax.set_ylabel("Капитал (старт = 1, лог. шкала)")
    else:
        ax.set_ylabel("Просадка, %")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    return _svg(fig)


def _table(header: list[str], rows: list[list[str]], css_class: str = "") -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    cls = f' class="{css_class}"' if css_class else ""
    return f"<table{cls}><tr>{head}</tr>{body}</table>"


def _metrics_table(results: list[RunResult]) -> str:
    formatted = [format_metrics(r.metrics) for r in results]
    labels = list(formatted[0])
    rows = [[_esc(label)] + [_esc(f[label]) for f in formatted] for label in labels]
    return _table(["Метрика"] + [r.name for r in results], rows)


def _yearly_table(results: list[RunResult]) -> str:
    yearly = pd.DataFrame({r.name: yearly_returns(r.equity, r.start_cash) for r in results})
    rows = []
    for year, row in yearly.iterrows():
        cells = [_esc(year)]
        for value in row:
            css = ' class="neg"' if value < 0 else ""
            cells.append(f"<span{css}>{fmt_pct(value)}</span>")
        rows.append(cells)
    return _table(["Год"] + [r.name for r in results], rows)


def _trades_table(r: RunResult) -> str:
    if r.trades.empty:
        return '<p class="note">Сделок не было.</p>'
    rows = []
    for t in r.trades.head(MAX_TRADE_ROWS).itertuples(index=False):
        buy = t.side == "BUY"
        side = f'<span class="{"buy" if buy else "sell"}">{"Покупка" if buy else "Продажа"}</span>'
        rows.append(
            [
                f"{t.date:%Y-%m-%d}",
                side,
                f"{t.qty:.4f}",
                fmt_money(t.price),
                fmt_money(t.fee),
                fmt_money(t.qty * t.price),
            ]
        )
    table = _table(["Дата", "Операция", "Штук", "Цена", "Комиссия", "Сумма"], rows)
    note = ""
    if len(r.trades) > MAX_TRADE_ROWS:
        note = f'<p class="note">Показаны первые {MAX_TRADE_ROWS} из {len(r.trades)}.</p>'
    return f'<div class="scroll">{table}</div>{note}'


def _params_table(meta: dict[str, Any], config: dict[str, Any]) -> str:
    broker = config["broker"]
    rows = [
        ["Тикер", _esc(meta["ticker"])],
        ["Данные", _esc(f"{meta['data_start']} — {meta['data_end']}, {meta['data_rows']} дней")],
        ["Цены скачаны", _esc(meta["data_fetched_at"])],
        ["Отпечаток данных", _esc(meta["data_hash"])],
        ["Стартовый капитал", _esc(fmt_money(config["run"]["cash"]))],
        ["Комиссия", _esc(f"{broker['commission'] * 100:.4g}%")],
        ["Проскальзывание", _esc(f"{broker['slippage'] * 100:.4g}%")],
        ["Порог ребалансировки", _esc(f"{broker['min_change'] * 100:.4g}%")],
        ["Коммит кода", _esc(meta["git_commit"] or "—")],
        ["Время прогона (UTC)", _esc(meta["created_at"])],
    ]
    for s in config["strategies"]:
        params = ", ".join(f"{k}={v}" for k, v in s["params"].items()) or "без параметров"
        rows.append([f"Стратегия «{_esc(s['name'])}»", _esc(f"{s['type']} ({params})")])
    return _table(["Параметр", "Значение"], rows, "params")


def build_html(
    results: list[RunResult],
    meta: dict[str, Any],
    config: dict[str, Any],
    run_id: int | None = None,
) -> str:
    title = f"Прогон №{run_id}" if run_id is not None else "Прогон (не сохранён)"
    trades = "".join(
        f"<details><summary>{_esc(r.name)} — сделок: {len(r.trades)}</summary>"
        f"{_trades_table(r)}</details>"
        for r in results
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {_esc(meta["ticker"])}</title>
<style>{CSS}</style>
</head>
<body>
<h1>{_esc(title)}</h1>
<p class="sub">{_esc(meta["ticker"])}, {_esc(meta["data_start"])} — {_esc(meta["data_end"])}</p>

<h2>Итоги</h2>
{_metrics_table(results)}

<h2>Капитал</h2>
<div class="chart">{_chart(results, "equity")}</div>

<h2>Просадка</h2>
<div class="chart">{_chart(results, "drawdown")}</div>

<h2>Доходность по годам</h2>
{_yearly_table(results)}

<h2>Сделки</h2>
{trades}

<h2>Параметры прогона</h2>
{_params_table(meta, config)}
</body>
</html>
"""
