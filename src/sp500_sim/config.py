"""Чтение и проверка файла настроек (TOML)."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any

from .errors import SimError
from .strategies import REGISTRY


class ConfigError(SimError):
    """Ошибка в файле настроек."""


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _check_date(section: str, key: str, value: Any) -> None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"[{section}] {key}: дата должна быть в формате ГГГГ-ММ-ДД, получено {value!r}"
        ) from None


def _check_range(section: str, key: str, value: Any, low: float, high: float) -> None:
    if not _is_number(value) or not low <= value < high:
        raise ConfigError(f"[{section}] {key}: нужно число от {low} до {high}, получено {value!r}")


@dataclass(frozen=True)
class DataConfig:
    ticker: str = "SPY"
    start: str = "2000-01-01"
    end: str | None = None
    cache_dir: str = "data"

    def __post_init__(self) -> None:
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise ConfigError("[data] ticker: нужна непустая строка")
        _check_date("data", "start", self.start)
        if self.end is not None:
            _check_date("data", "end", self.end)
            if self.end <= self.start:
                raise ConfigError("[data] end должна быть позже start")
        if not isinstance(self.cache_dir, str) or not self.cache_dir.strip():
            raise ConfigError("[data] cache_dir: нужна непустая строка")


@dataclass(frozen=True)
class BrokerConfig:
    commission: float = 0.0005
    slippage: float = 0.0005
    min_change: float = 0.01

    def __post_init__(self) -> None:
        _check_range("broker", "commission", self.commission, 0, 0.1)
        _check_range("broker", "slippage", self.slippage, 0, 0.1)
        _check_range("broker", "min_change", self.min_change, 0, 1)


@dataclass(frozen=True)
class RunConfig:
    cash: float = 10_000.0

    def __post_init__(self) -> None:
        if not _is_number(self.cash) or self.cash <= 0:
            raise ConfigError(f"[run] cash: нужно положительное число, получено {self.cash!r}")


@dataclass(frozen=True)
class OutputConfig:
    dir: str = "results"

    def __post_init__(self) -> None:
        if not isinstance(self.dir, str) or not self.dir.strip():
            raise ConfigError("[output] dir: нужна непустая строка")


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    data: DataConfig
    broker: BrokerConfig
    run: RunConfig
    output: OutputConfig
    strategies: list[StrategyConfig]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _section(raw: dict[str, Any], name: str, cls: type) -> Any:
    values = raw.get(name, {})
    if not isinstance(values, dict):
        raise ConfigError(f"[{name}] должна быть таблицей")
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ConfigError(f"[{name}]: неизвестные параметры {unknown}. Допустимые: {sorted(known)}")
    # Дата без кавычек (2000-01-01) читается как объект date - приводим к строке
    values = {k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()}
    return cls(**values)


def _strategies(raw: dict[str, Any]) -> list[StrategyConfig]:
    items = raw.get("strategies")
    if not isinstance(items, list) or not items:
        raise ConfigError("Нужна хотя бы одна стратегия: добавьте блок [[strategies]]")

    result: list[StrategyConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ConfigError(f"[[strategies]] №{i}: должна быть таблицей")
        params = dict(item)
        type_ = params.pop("type", None)
        if not isinstance(type_, str) or type_ not in REGISTRY:
            raise ConfigError(
                f"[[strategies]] №{i}: неизвестный type {type_!r}. "
                f"Доступны: {', '.join(sorted(REGISTRY))}"
            )
        name = params.pop("name", type_)
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"[[strategies]] №{i}: name должно быть непустой строкой")
        if name in seen:
            raise ConfigError(f"Имя стратегии {name!r} повторяется")
        try:
            REGISTRY[type_](**params)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"[[strategies]] {name!r}: {exc}") from None
        seen.add(name)
        result.append(StrategyConfig(name=name, type=type_, params=params))
    return result


def parse_config(raw: dict[str, Any]) -> Config:
    known = {"data", "broker", "run", "output", "strategies"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"Неизвестные разделы {unknown}. Допустимые: {sorted(known)}")
    return Config(
        data=_section(raw, "data", DataConfig),
        broker=_section(raw, "broker", BrokerConfig),
        run=_section(raw, "run", RunConfig),
        output=_section(raw, "output", OutputConfig),
        strategies=_strategies(raw),
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Файл настроек не найден: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: ошибка в TOML: {exc}") from None
    return parse_config(raw)
