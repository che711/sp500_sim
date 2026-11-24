"""Один прогон целиком: данные -> стратегии -> результаты."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime

from .broker import Broker
from .config import Config, StrategyConfig, needed_tickers
from .data import align, fingerprint_many, load_many
from .engine import RunResult, run_portfolio
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


def strategy_weights(sc: StrategyConfig, config: Config) -> dict[str, float]:
    """Доли бумаг стратегии (в сумме 1). Без своего портфеля - бумага из раздела [data]."""
    if not sc.portfolio:
        return {config.data.ticker: 1.0}
    total = sum(sc.portfolio.values())
    return {ticker: weight / total for ticker, weight in sc.portfolio.items()}


def execute(config: Config, refresh: bool = False) -> tuple[RunMeta, list[RunResult]]:
    tickers = needed_tickers(config.data, config.strategies)
    loaded = load_many(config.data, tickers, refresh=refresh)
    # Все стратегии считаются на одном и том же периоде, иначе их нельзя сравнивать
    frames = align({t: p.df for t, p in loaded.items()})
    fp = fingerprint_many(frames)
    broker = Broker(
        commission=config.broker.commission,
        slippage=config.broker.slippage,
        min_change=config.broker.min_change,
    )

    results = []
    for sc in config.strategies:
        weights = strategy_weights(sc, config)
        strategy = REGISTRY[sc.type](**sc.params)
        results.append(
            run_portfolio(
                {t: frames[t] for t in weights},
                weights,
                strategy,
                sc.name,
                config.run.cash,
                broker,
                sc.rebalance,
            )
        )

    meta = RunMeta(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=git_commit(),
        ticker=", ".join(tickers),
        data_start=str(fp["start"]),
        data_end=str(fp["end"]),
        data_rows=int(fp["rows"]),
        data_hash=str(fp["hash"]),
        data_fetched_at=min(p.fetched_at for p in loaded.values()),
    )
    return meta, results
