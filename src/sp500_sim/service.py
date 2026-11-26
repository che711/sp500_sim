"""Общая логика «выполнить прогон и сохранить». Её вызывают и командная строка, и сайт."""

from __future__ import annotations

import logging
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from . import storage
from .config import Config, needed_tickers
from .engine import RunResult
from .runner import execute
from .storage import RunMeta

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    run_id: int | None  # None, если прогон не сохранялся
    meta: RunMeta
    results: list[RunResult]


def run_and_save(
    config: Config,
    db_path: str | Path,
    *,
    refresh: bool = False,
    save: bool = True,
    existing_run_id: int | None = None,
) -> Outcome:
    """Выполняет прогон и сохраняет его в базу.

    existing_run_id - номер прогона, заведённого заранее через storage.create_run
    (так делает сайт, чтобы сразу показать статус «идёт»). Без него прогон
    создаётся сразу готовым."""
    meta, results = execute(config, refresh=refresh)
    run_id = existing_run_id
    if save:
        with closing(storage.connect(db_path)) as conn:
            if existing_run_id is None:
                run_id = storage.save_run(conn, meta, config.to_dict(), results)
            else:
                storage.finish_run(conn, existing_run_id, meta, results)
        log.info("Прогон №%s сохранён", run_id)
    return Outcome(run_id=run_id, meta=meta, results=results)


def start_run(config: Config, db_path: str | Path) -> int:
    """Заводит прогон в статусе «идёт» и возвращает его номер."""
    with closing(storage.connect(db_path)) as conn:
        mine = config.my_portfolio
        tickers = needed_tickers(config.data, config.strategies, mine.positions if mine else [])
        return storage.create_run(conn, config.to_dict(), ", ".join(tickers))


def run_in_background(
    config: Config, db_path: str | Path, run_id: int, *, refresh: bool = False
) -> None:
    """Тело фоновой задачи: выполняет прогон, а любую ошибку записывает в статус прогона."""
    try:
        run_and_save(config, db_path, refresh=refresh, existing_run_id=run_id)
    except Exception as exc:
        log.exception("Прогон №%s завершился ошибкой", run_id)
        message = str(exc) if isinstance(exc, storage.SimError) else f"{type(exc).__name__}: {exc}"
        with closing(storage.connect(db_path)) as conn:
            storage.fail_run(conn, run_id, message)
