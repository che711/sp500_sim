"""Отчёт о прогоне: таблицы и графики. Одни и те же блоки показываются и на сайте, и в
отдельном HTML-файле (его пишет командная строка). Графики рисуются дважды, для светлой
и тёмной темы, и переключаются стилями, поэтому файл не зависит от интернета."""

from __future__ import annotations

import html
import io
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator, NullFormatter, NullLocator

from .engine import RunResult
from .metrics import LABELS, fmt_money, fmt_pct, format_metrics, yearly_returns
from .theme import CSS, FAVICON, HEAD_JS, TOGGLE_JS

PALETTES = {
    "light": ["#2563eb", "#ea580c", "#16a34a", "#9333ea", "#dc2626", "#0891b2"],
    "dark": ["#60a5fa", "#fb923c", "#4ade80", "#c084fc", "#f87171", "#22d3ee"],
}
AXIS = {
    "light": {"text": "#64748b", "grid": "#e2e8f0"},
    "dark": {"text": "#94a3b8", "grid": "#243049"},
}
MAX_TRADE_ROWS = 1000
HIGHER_IS_BETTER = ("final_value", "total_return", "cagr", "max_drawdown", "sharpe")

Cell = str | tuple[str, str]  # текст или (текст, css-класс)


def _esc(value: object) -> str:
    return html.escape(str(value))


def _signed_pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _sign_class(value: float) -> str:
    return "pos" if value >= 0 else "neg"


# ---------------------------------------------------------------- графики


def _svg(fig: Figure) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", transparent=True)
    svg = buf.getvalue()
    return svg[svg.index("<svg") :]


def _chart(results: list[RunResult], kind: str, theme: str) -> Figure:
    palette, axis = PALETTES[theme], AXIS[theme]
    fig = Figure(figsize=(10, 3.9), layout="constrained")
    fig.patch.set_alpha(0)
    ax = fig.subplots()
    ax.set_facecolor("none")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(axis["grid"])
    ax.tick_params(colors=axis["text"], labelsize=9, length=3, color=axis["grid"])
    ax.grid(axis="y", color=axis["grid"], linewidth=0.8)
    ax.set_axisbelow(True)

    for i, r in enumerate(results):
        color = palette[i % len(palette)]
        x = r.equity.index.to_numpy()
        if kind == "equity":
            ax.plot(x, (r.equity / r.start_cash).to_numpy(), label=r.name, color=color, lw=1.8)
        else:
            drawdown = ((r.equity / r.equity.cummax() - 1) * 100).to_numpy()
            ax.plot(x, drawdown, label=r.name, color=color, lw=1.4)
            ax.fill_between(x, drawdown, 0, color=color, alpha=0.1, linewidth=0)

    if kind == "equity":
        ax.set_yscale("log")
        values = np.concatenate([(r.equity / r.start_cash).to_numpy() for r in results])
        if values.max() / values.min() >= 4:
            ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 1.5, 2, 3, 4, 5, 7)))
        else:  # узкий диапазон: обычных делений мало, берём больше
            ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.yaxis.set_minor_locator(NullLocator())
        ax.yaxis.set_minor_formatter(NullFormatter())
    else:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    fig.legend(
        loc="outside upper left",  # над графиком, чтобы не закрывать линии
        ncol=len(results),
        frameon=False,
        fontsize=10,
        labelcolor=axis["text"],
    )
    return fig


def _chart_html(results: list[RunResult], kind: str) -> str:
    light = _svg(_chart(results, kind, "light"))
    dark = _svg(_chart(results, kind, "dark"))
    return (
        f'<div class="chart"><div class="chart-light">{light}</div>'
        f'<div class="chart-dark">{dark}</div></div>'
    )


# ---------------------------------------------------------------- таблицы


def _table(
    header: list[str],
    rows: list[list[Cell]],
    css: str = "data",
    num_from: int | None = 1,
    wrapper: str = "card table-card",
) -> str:
    """Колонки с номера num_from и дальше выровнены по правому краю (None - все слева)."""

    def is_num(i: int) -> bool:
        return num_from is not None and i >= num_from

    head = "".join(
        f'<th class="num">{_esc(h)}</th>' if is_num(i) else f"<th>{_esc(h)}</th>"
        for i, h in enumerate(header)
    )
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            text, extra = cell if isinstance(cell, tuple) else (cell, "")
            classes = " ".join(c for c in ("num" if is_num(i) else "", extra) if c)
            cells.append(f'<td class="{classes}">{text}</td>' if classes else f"<td>{text}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="{wrapper}"><table class="{css}">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _kpis(results: list[RunResult]) -> str:
    cards = []
    for i, r in enumerate(results):
        m = r.metrics
        light = PALETTES["light"][i % len(PALETTES["light"])]
        dark = PALETTES["dark"][i % len(PALETTES["dark"])]
        cards.append(
            f'<div class="kpi"><div class="kpi-name">'
            f'<span class="kpi-dot" style="--c-light:{light};--c-dark:{dark}"></span>'
            f"{_esc(r.name)}</div>"
            f'<div class="kpi-value {_sign_class(m["total_return"])}">'
            f"{_signed_pct(m['total_return'])}</div>"
            f'<div class="kpi-rows">'
            f"<span>Итог</span><span>$ {fmt_money(m['final_value'])}</span>"
            f"<span>В год</span><span>{fmt_pct(m['cagr'])}</span>"
            f"<span>Макс. просадка</span><span>{fmt_pct(m['max_drawdown'])}</span>"
            f"<span>Sharpe</span><span>{m['sharpe']:.2f}</span>"
            f"</div></div>"
        )
    return f'<div class="kpis">{"".join(cards)}</div>'


def _metrics_table(results: list[RunResult]) -> str:
    formatted = [format_metrics(r.metrics) for r in results]
    rows: list[list[Cell]] = []
    for key, label in LABELS.items():
        best = None
        if key in HIGHER_IS_BETTER and len(results) > 1:
            values = [r.metrics[key] for r in results]
            if values.count(max(values)) == 1:  # подсвечиваем, только если лидер один
                best = values.index(max(values))
        cells: list[Cell] = [_esc(label)]
        for i, f in enumerate(formatted):
            cells.append((_esc(f[label]), "best" if i == best else ""))
        rows.append(cells)
    return _table(["Метрика"] + [r.name for r in results], rows)


def _yearly_table(results: list[RunResult]) -> str:
    yearly = pd.DataFrame({r.name: yearly_returns(r.equity, r.start_cash) for r in results})
    rows: list[list[Cell]] = []
    for year, row in yearly.iterrows():
        cells: list[Cell] = [_esc(year)]
        for value in row:
            cells.append((fmt_pct(value), _sign_class(value)))
        rows.append(cells)
    return _table(["Год"] + [r.name for r in results], rows)


def _trades_table(r: RunResult) -> str:
    if r.trades.empty:
        return '<p class="note">Сделок не было.</p>'
    rows: list[list[Cell]] = []
    for t in r.trades.head(MAX_TRADE_ROWS).itertuples(index=False):
        buy = t.side == "BUY"
        rows.append(
            [
                f"{t.date:%Y-%m-%d}",
                ("Покупка" if buy else "Продажа", "pos" if buy else "neg"),
                f"{t.qty:.4f}",
                fmt_money(t.price),
                fmt_money(t.fee),
                fmt_money(t.qty * t.price),
            ]
        )
    table = _table(
        ["Дата", "Операция", "Штук", "Цена", "Комиссия", "Сумма"],
        rows,
        num_from=2,
        wrapper="scroll",
    )
    note = ""
    if len(r.trades) > MAX_TRADE_ROWS:
        note = f'<p class="note">Показаны первые {MAX_TRADE_ROWS} из {len(r.trades)}.</p>'
    return table + note


def _params_table(meta: dict[str, Any], config: dict[str, Any]) -> str:
    broker = config["broker"]
    rows: list[list[Cell]] = [
        ["Тикер", _esc(meta["ticker"])],
        ["Данные", _esc(f"{meta['data_start']} — {meta['data_end']}, {meta['data_rows']} дней")],
        ["Цены скачаны", _esc(meta["data_fetched_at"])],
        ["Отпечаток данных", f"<code>{_esc(meta['data_hash'])}</code>"],
        ["Стартовый капитал", _esc(fmt_money(config["run"]["cash"]))],
        ["Комиссия", _esc(f"{broker['commission'] * 100:.4g}%")],
        ["Проскальзывание", _esc(f"{broker['slippage'] * 100:.4g}%")],
        ["Порог ребалансировки", _esc(f"{broker['min_change'] * 100:.4g}%")],
        ["Коммит кода", f"<code>{_esc(meta['git_commit'])}</code>" if meta["git_commit"] else "—"],
        ["Время прогона (UTC)", _esc(meta["created_at"])],
    ]
    for s in config["strategies"]:
        params = ", ".join(f"{k}={v}" for k, v in s["params"].items()) or "без параметров"
        rows.append([f"Стратегия «{_esc(s['name'])}»", _esc(f"{s['type']} ({params})")])
    return _table(["Параметр", "Значение"], rows, css="data params", num_from=None)


# ---------------------------------------------------------------- страница


def build_body(results: list[RunResult], meta: dict[str, Any], config: dict[str, Any]) -> str:
    """Содержимое отчёта без оформления страницы: его вставляет и сайт, и отдельный файл."""
    trades = "".join(
        f"<details><summary>{_esc(r.name)} — сделок: {len(r.trades)}</summary>"
        f"{_trades_table(r)}</details>"
        for r in results
    )
    return f"""{_kpis(results)}

<h2>Итоги</h2>
{_metrics_table(results)}

<h2>Капитал</h2>
{_chart_html(results, "equity")}

<h2>Просадка</h2>
{_chart_html(results, "drawdown")}

<h2>Доходность по годам</h2>
{_yearly_table(results)}

<h2>Сделки</h2>
{trades}

<h2>Параметры прогона</h2>
{_params_table(meta, config)}"""


def build_html(
    results: list[RunResult],
    meta: dict[str, Any],
    config: dict[str, Any],
    run_id: int | None = None,
) -> str:
    """Отдельная страница отчёта: один файл, который открывается без интернета."""
    title = f"Прогон №{run_id}" if run_id is not None else "Прогон (не сохранён)"
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} — {_esc(meta["ticker"])}</title>
<link rel="icon" href="{FAVICON}">
<script>{HEAD_JS}</script>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<div class="report-head">
  <div>
    <h1>{_esc(title)}</h1>
    <p class="sub">{_esc(meta["ticker"])}, {_esc(meta["data_start"])} — {_esc(meta["data_end"])}</p>
  </div>
  <button class="theme-toggle" id="theme-toggle" type="button" aria-label="Сменить тему"></button>
</div>
{build_body(results, meta, config)}
</div>
<script>{TOGGLE_JS}</script>
</body>
</html>
"""
