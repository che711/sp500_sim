"""Командная строка: run, runs, compare, report."""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd

from . import storage
from .compare import config_diff
from .config import default_config, load_config
from .engine import RunResult
from .errors import SimError
from .metrics import fmt_money, fmt_pct, format_metrics
from .report import build_html
from .service import run_and_save

log = logging.getLogger("sp500_sim")

DEFAULT_DIR = "results"
DB_NAME = "results.db"


def _results_table(results: list[RunResult]) -> str:
    return pd.DataFrame({r.name: format_metrics(r.metrics) for r in results}).to_string()


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    out_dir = Path(config.output.dir)
    outcome = run_and_save(config, out_dir / DB_NAME, refresh=args.refresh, save=not args.no_save)
    print(_results_table(outcome.results))
    if outcome.run_id is not None:
        print(f"\nПрогон сохранён: №{outcome.run_id}")

    if not args.no_report:
        name = f"run_{outcome.run_id}.html" if outcome.run_id else "run_unsaved.html"
        path = out_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        page = build_html(outcome.results, outcome.meta.__dict__, config.to_dict(), outcome.run_id)
        path.write_text(page, "utf-8")
        print(f"Отчёт: {path}")
    return 0


STATUS_WORDS = {"running": "идёт", "done": "готов", "error": "ошибка"}


def cmd_runs(args: argparse.Namespace) -> int:
    with closing(storage.connect(Path(args.dir) / DB_NAME, must_exist=True)) as conn:
        runs = storage.list_runs(conn)
    if runs.empty:
        print("Сохранённых прогонов пока нет.")
        return 0
    runs["status"] = runs["status"].map(STATUS_WORDS).fillna(runs["status"])
    runs.columns = [
        "№",
        "Время (UTC)",
        "Статус",
        "Коммит",
        "Тикер",
        "Данные с",
        "по",
        "Стратегии",
        "Ошибка",
    ]
    if runs["Ошибка"].isna().all():
        runs = runs.drop(columns="Ошибка")
    print(runs.fillna("—").to_string(index=False))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    ids = list(dict.fromkeys(args.ids))
    with closing(storage.connect(Path(args.dir) / DB_NAME, must_exist=True)) as conn:
        table = storage.metrics_table(conn, ids)
        described = {run_id: storage.load_meta(conn, run_id) for run_id in ids}

    shown = pd.DataFrame(
        {
            "№": table["run_id"],
            "Тикер": table["ticker"],
            "Стратегия": table["strategy"],
            "Итог, $": table["final_value"].map(fmt_money),
            "Доходность": table["total_return"].map(fmt_pct),
            "В год": table["cagr"].map(fmt_pct),
            "Просадка": table["max_drawdown"].map(fmt_pct),
            "Sharpe": table["sharpe"].map(lambda v: f"{v:.2f}"),
            "Сделок": table["n_trades"],
        }
    )
    print(shown.to_string(index=False))

    diff = config_diff(described)
    print()
    if not diff:
        print("Настройки, код и данные у этих прогонов совпадают.")
    else:
        print("Чем отличаются прогоны:")
        for key, values in diff.items():
            parts = ", ".join(f"№{run_id}={value}" for run_id, value in values.items())
            print(f"  {key}: {parts}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    with closing(storage.connect(Path(args.dir) / DB_NAME, must_exist=True)) as conn:
        run = storage.load_run(conn, args.id)
    path = Path(args.output) if args.output else Path(args.dir) / f"run_{args.id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(run.results, run.meta, run.config, run.run_id), "utf-8")
    print(f"Отчёт: {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError as exc:
        raise SimError(
            "Для сайта нужны дополнительные библиотеки: pip install -e '.[web]'"
        ) from exc

    if args.config is not None:
        defaults = load_config(args.config)
    elif Path("config.toml").exists():
        defaults = load_config("config.toml")
    else:
        defaults = default_config()
    app = create_app(
        results_dir=args.dir or defaults.output.dir,
        cache_dir=defaults.data.cache_dir,
        defaults=defaults,
    )
    print(f"Сайт: http://{args.host}:{args.port}  (остановить: Ctrl+C)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sp500-sim", description="Симулятор торговли на истории S&P 500"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="выполнить прогон по файлу настроек")
    p.add_argument("config", nargs="?", default="config.toml", help="файл настроек")
    p.add_argument("--refresh", action="store_true", help="скачать цены заново")
    p.add_argument("--no-save", action="store_true", help="не сохранять прогон в базу")
    p.add_argument("--no-report", action="store_true", help="не создавать HTML-отчёт")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("runs", help="список сохранённых прогонов")
    p.add_argument("--dir", default=DEFAULT_DIR, help="папка с результатами")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("compare", help="сравнить сохранённые прогоны")
    p.add_argument("ids", nargs="+", type=int, help="номера прогонов")
    p.add_argument("--dir", default=DEFAULT_DIR, help="папка с результатами")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("report", help="пересоздать HTML-отчёт сохранённого прогона")
    p.add_argument("id", type=int, help="номер прогона")
    p.add_argument("--dir", default=DEFAULT_DIR, help="папка с результатами")
    p.add_argument("-o", "--output", help="куда записать отчёт")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("serve", help="запустить сайт")
    p.add_argument("--config", help="файл настроек для значений по умолчанию (config.toml)")
    p.add_argument("--dir", help="папка с результатами (по умолчанию из настроек)")
    p.add_argument("--host", default="127.0.0.1", help="адрес (по умолчанию только этот компьютер)")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    try:
        return args.func(args)
    except SimError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
