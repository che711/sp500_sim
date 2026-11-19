"""Загрузка, кэш и проверка исторических цен."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .config import DataConfig
from .errors import SimError

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class DataError(SimError):
    """Проблема с данными: нет цен, битые значения, неверный порядок дат."""


@dataclass(frozen=True)
class PriceData:
    df: pd.DataFrame
    fetched_at: str  # когда цены скачаны (UTC, ISO)
    source: str  # путь к файлу кэша


def cache_file(cfg: DataConfig) -> Path:
    """Файл кэша зависит от тикера и периода: другой период - другой файл."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cfg.ticker)
    return Path(cfg.cache_dir) / f"{safe}_{cfg.start}_{cfg.end or 'latest'}.csv"


def validate_prices(df: pd.DataFrame, source: str = "данные") -> None:
    missing = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"{source}: нет колонок {missing}")
    if len(df) < 2:
        raise DataError(f"{source}: нужно хотя бы 2 дня данных, есть {len(df)}")
    if not df.index.is_monotonic_increasing or not df.index.is_unique:
        raise DataError(f"{source}: даты должны идти по возрастанию без повторов")
    prices = df[["Open", "High", "Low", "Close"]]
    if prices.isna().any().any():
        raise DataError(f"{source}: в ценах есть пропуски")
    if (prices <= 0).any().any():
        raise DataError(f"{source}: в ценах есть нули или отрицательные значения")


def fingerprint(df: pd.DataFrame) -> dict[str, str | int]:
    """Короткий отпечаток данных: по нему видно, что два прогона шли на одних и тех же ценах."""
    digest = hashlib.sha256(df.to_csv(index_label="Date").encode()).hexdigest()[:16]
    return {
        "rows": len(df),
        "start": df.index[0].date().isoformat(),
        "end": df.index[-1].date().isoformat(),
        "hash": digest,
    }


def _download(cfg: DataConfig) -> pd.DataFrame:
    import yfinance as yf

    log.info("Скачиваю %s с %s", cfg.ticker, cfg.start)
    df = yf.download(cfg.ticker, start=cfg.start, end=cfg.end, auto_adjust=True, progress=False)
    if df is None or df.empty:
        raise DataError(f"Для {cfg.ticker} нет данных за этот период")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    missing = [c for c in PRICE_COLUMNS if c not in df.columns]
    if missing:
        raise DataError(f"Yahoo не вернул колонки {missing}")
    return df[PRICE_COLUMNS].dropna()


def load_prices(cfg: DataConfig, refresh: bool = False) -> PriceData:
    """Берёт цены из кэша, а если кэша нет (или refresh=True) - скачивает."""
    path = cache_file(cfg)
    if path.exists() and not refresh:
        log.info("Беру цены из кэша %s", path)
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        fetched = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        validate_prices(df, str(path))
    else:
        df = _download(cfg)
        validate_prices(df, f"{cfg.ticker} (Yahoo)")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index_label="Date")
        fetched = datetime.now(UTC)
        log.info("Сохранил цены в %s", path)
    return PriceData(df=df, fetched_at=fetched.isoformat(timespec="seconds"), source=str(path))
