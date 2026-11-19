"""Один прогон целиком: данные -> стратегии -> результаты."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime

from .broker import Broker
from .config import Config
from .data import fingerprint, load_prices
from .engine import RunResult, run_strategy
from .storage import RunMeta
from .strategies import REGISTRY

log = logging.getLogger(__name__)


def git_commit() -> str | None:
    """Короткий хеш текущего коммита; с '+dirty', если есть незакоммиченные изменения."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return sha + ("+dirty" if dirty else "")


def execute(config: Config, refresh: bool = False) -> tuple[RunMeta, list[RunResult]]:
    prices = load_prices(config.data, refresh=refresh)
    fp = fingerprint(prices.df)
    broker = Broker(
        commission=config.broker.commission,
        slippage=config.broker.slippage,
        min_change=config.broker.min_change,
    )

    results = []
    for sc in config.strategies:
        strategy = REGISTRY[sc.type](**sc.params)
        results.append(run_strategy(prices.df, strategy, sc.name, config.run.cash, broker))

    meta = RunMeta(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=git_commit(),
        ticker=config.data.ticker,
        data_start=str(fp["start"]),
        data_end=str(fp["end"]),
        data_rows=int(fp["rows"]),
        data_hash=str(fp["hash"]),
        data_fetched_at=prices.fetched_at,
    )
    return meta, results
