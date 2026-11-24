"""Сайт: страницы в браузере и JSON API поверх тех же прогонов, что и командная строка.

Прогон выполняется в фоне: сайт сразу создаёт запись со статусом «идёт», а страница
прогона сама обновляется, когда он закончится."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import service, storage
from ..compare import config_diff
from ..config import REBALANCE_MODES, Config, default_config
from ..errors import SimError
from ..metrics import fmt_money, fmt_pct
from ..report import HIGHER_IS_BETTER, PALETTES, build_body, build_html
from ..storage import RunNotFinishedError, StorageError
from ..theme import CSS, FAVICON, HEAD_JS, LOGO_SVG, TOGGLE_JS
from .forms import (
    MAX_STRATEGIES,
    FormValues,
    config_from_api,
    config_from_stored,
    parse_form,
    strategy_catalog,
)

log = logging.getLogger(__name__)

DB_NAME = "results.db"
MAX_COMPARE = 10
STATUS_WORDS = {"running": "идёт", "done": "готов", "error": "ошибка"}
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _parse_ids(values: list[str]) -> list[int]:
    """Номера прогонов из ?ids=1,2 или ?ids=1&ids=2."""
    ids: list[int] = []
    for chunk in values:
        for part in chunk.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                raise SimError(f"Номер прогона должен быть числом, получено {part!r}") from None
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_COMPARE:
        raise SimError(f"Сравнить можно не больше {MAX_COMPARE} прогонов")
    return ids


def _records(df: Any) -> list[dict[str, Any]]:
    """Таблица pandas -> список словарей; пустые значения становятся None."""
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _best_values(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Лучшее значение каждой метрики, если лидер один: такие ячейки подсвечиваются."""
    best: dict[str, float] = {}
    if len(rows) < 2:
        return best
    for key in HIGHER_IS_BETTER:
        values = [r[key] for r in rows]
        if values.count(max(values)) == 1:
            best[key] = max(values)
    return best


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    """Настройки без путей сервера."""
    public = {k: v for k, v in config.items() if k != "output"}
    public["data"] = {k: v for k, v in config["data"].items() if k != "cache_dir"}
    return public


def create_app(
    results_dir: str | Path = "results",
    cache_dir: str = "data",
    defaults: Config | None = None,
    executor: Executor | None = None,
) -> FastAPI:
    """results_dir - где лежит база прогонов; cache_dir - кэш цен; defaults - значения формы.
    executor - чем выполнять фоновые прогоны (по умолчанию один поток: прогоны идут по очереди)."""
    defaults = defaults or default_config()
    results_dir = Path(results_dir)
    db_path = results_dir / DB_NAME
    pool = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="sim")

    def db() -> closing[Any]:
        return closing(storage.connect(db_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Iterator[None]:
        with db() as conn:
            interrupted = storage.mark_interrupted(conn)
        if interrupted:
            log.warning("Прерванных прогонов: %d", interrupted)
        yield
        if executor is None:
            pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="sp500-sim",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["pct"] = fmt_pct
    templates.env.filters["money"] = fmt_money
    templates.env.globals.update(
        STATUS_WORDS=STATUS_WORDS,
        theme_css=CSS,
        head_js=HEAD_JS,
        toggle_js=TOGGLE_JS,
        favicon=FAVICON,
        logo_svg=LOGO_SVG,
    )

    def submit(config: Config, refresh: bool) -> int:
        run_id = service.start_run(config, db_path)
        pool.submit(service.run_in_background, config, db_path, run_id, refresh=refresh)
        return run_id

    def form_page(
        request: Request, values: FormValues, errors: dict[str, str], status_code: int = 200
    ) -> Response:
        context = {
            "v": values,
            "errors": errors,
            "catalog": strategy_catalog(),
            "max_strategies": MAX_STRATEGIES,
            "palette": PALETTES["light"],
            "rebalance_modes": REBALANCE_MODES,
            "active": "new",
        }
        return templates.TemplateResponse(request, "form.html", context, status_code=status_code)

    def compare_data(ids: list[int]) -> tuple[Any, dict[str, dict[int, Any]]]:
        with db() as conn:
            table = storage.metrics_table(conn, ids)
            described = {i: storage.load_meta(conn, i) for i in ids}
        return table, config_diff(described)

    # ------------------------------------------------------------ ошибки

    @app.exception_handler(SimError)
    async def sim_error(request: Request, exc: SimError) -> Response:
        if isinstance(exc, RunNotFinishedError):
            code = 409
        elif isinstance(exc, StorageError):
            code = 404
        else:
            code = 400
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": str(exc)}, status_code=code)
        return templates.TemplateResponse(
            request, "error.html", {"message": str(exc), "active": ""}, status_code=code
        )

    # ----------------------------------------------------------- страницы

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, from_run: int | None = None) -> Response:
        if from_run is None:
            config = defaults
        else:
            with db() as conn:
                _, stored = storage.load_meta(conn, from_run)
            config = config_from_stored(stored)
        return form_page(request, FormValues.from_config(config), {})

    @app.post("/runs")
    async def create_run(request: Request) -> Response:
        values = FormValues.from_form(await request.form())
        config, errors = parse_form(values, cache_dir=cache_dir, output_dir=str(results_dir))
        if config is None:
            return form_page(request, values, errors, status_code=422)
        run_id = submit(config, refresh=values.refresh)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request) -> Response:
        with db() as conn:
            runs = _records(storage.list_runs(conn))
        runs.reverse()  # новые сверху
        return templates.TemplateResponse(request, "runs.html", {"runs": runs, "active": "runs"})

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: int) -> Response:
        body = None
        with db() as conn:
            meta, config = storage.load_meta(conn, run_id)
            if meta["status"] == "done":
                run = storage.load_run(conn, run_id)
                body = build_body(run.results, run.meta, run.config)
        context = {"run_id": run_id, "meta": meta, "config": config, "body": body, "active": "runs"}
        return templates.TemplateResponse(request, "run.html", context)

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report_page(run_id: int) -> Response:
        with db() as conn:
            run = storage.load_run(conn, run_id)
        return HTMLResponse(build_html(run.results, run.meta, run.config, run.run_id))

    @app.get("/compare", response_class=HTMLResponse)
    def compare_page(
        request: Request, ids: Annotated[list[str] | None, Query()] = None
    ) -> Response:
        run_ids = _parse_ids(ids or [])
        if not run_ids:
            with db() as conn:
                runs = [r for r in _records(storage.list_runs(conn)) if r["status"] == "done"]
            runs.reverse()
            context = {"runs": runs, "rows": None, "active": "compare"}
            return templates.TemplateResponse(request, "compare.html", context)
        table, diff = compare_data(run_ids)
        rows = table.to_dict("records")
        context = {
            "runs": [],
            "rows": rows,
            "best": _best_values(rows),
            "diff": diff,
            "ids": run_ids,
            "active": "compare",
        }
        return templates.TemplateResponse(request, "compare.html", context)

    # ---------------------------------------------------------------- API

    @app.post("/api/runs", status_code=202)
    async def api_create_run(request: Request, refresh: bool = False) -> Response:
        try:
            raw = await request.json()
        except ValueError:
            return JSONResponse({"errors": ["Тело запроса должно быть JSON"]}, status_code=400)
        config, errors = config_from_api(raw, cache_dir=cache_dir, output_dir=str(results_dir))
        if config is None:
            return JSONResponse({"errors": errors}, status_code=422)
        run_id = submit(config, refresh=refresh)
        return JSONResponse(
            {"id": run_id, "status": "running", "url": f"/runs/{run_id}"}, status_code=202
        )

    @app.get("/api/runs")
    def api_runs() -> list[dict[str, Any]]:
        with db() as conn:
            return _records(storage.list_runs(conn))

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: int) -> dict[str, Any]:
        with db() as conn:
            meta, config = storage.load_meta(conn, run_id)
            results = storage.load_results(conn, run_id) if meta["status"] == "done" else []
        return {"id": run_id, **meta, "config": _public_config(config), "results": results}

    @app.get("/api/runs/{run_id}/equity")
    def api_equity(run_id: int) -> dict[str, Any]:
        with db() as conn:
            run = storage.load_run(conn, run_id)
        return {
            r.name: {
                "dates": [d.strftime("%Y-%m-%d") for d in r.equity.index],
                "values": r.equity.tolist(),
            }
            for r in run.results
        }

    @app.get("/api/compare")
    def api_compare(
        ids: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        run_ids = _parse_ids(ids or [])
        if not run_ids:
            raise SimError("Укажите номера прогонов: /api/compare?ids=1,2")
        table, diff = compare_data(run_ids)
        return {
            "rows": _records(table),
            "diff": {key: {str(i): v for i, v in values.items()} for key, values in diff.items()},
        }

    return app
