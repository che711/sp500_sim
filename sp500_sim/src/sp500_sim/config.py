"""Чтение и проверка настроек: из файла TOML, из формы на сайте, из JSON."""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any

from .errors import SimError
from .strategies import REGISTRY

TICKER_RE = re.compile(r"[A-Za-z0-9.^=_-]{1,20}")


class ConfigError(SimError):
    """Ошибка в настройках.

    field - к какому полю относится ошибка (например "broker.commission" или
    "strategies.1"), detail - короткий текст без названия раздела. Сайт показывает
    их под нужным полем формы. errors - все ошибки раздела, а не только первая."""

    def __init__(self, message: str, field: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.field = field
        self.detail = detail or message
        self.errors: list[ConfigError] = [self]


def _err(section: str, key: str, detail: str) -> ConfigError:
    return ConfigError(f"[{section}] {key}: {detail}", field=f"{section}.{key}", detail=detail)


def _raise_all(errors: list[ConfigError]) -> None:
    if errors:
        first = errors[0]
        first.errors = errors
        raise first


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _date_error(section: str, key: str, value: Any) -> ConfigError | None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return _err(section, key, f"дата должна быть в формате ГГГГ-ММ-ДД, получено {value!r}")
    return None


def _range_error(section: str, key: str, value: Any, low: float, high: float) -> ConfigError | None:
    if not _is_number(value) or not low <= value < high:
        return _err(section, key, f"нужно число от {low} до {high}, получено {value!r}")
    return None


@dataclass(frozen=True)
class DataConfig:
    ticker: str = "SPY"
    start: str = "2000-01-01"
    end: str | None = None
    cache_dir: str = "data"

    def __post_init__(self) -> None:
        errors: list[ConfigError | None] = []
        if not isinstance(self.ticker, str) or not TICKER_RE.fullmatch(self.ticker):
            errors.append(
                _err("data", "ticker", "допустимы латинские буквы, цифры и знаки . ^ = _ - (до 20)")
            )
        start_error = _date_error("data", "start", self.start)
        errors.append(start_error)
        if self.end is not None:
            end_error = _date_error("data", "end", self.end)
            errors.append(end_error)
            if start_error is None and end_error is None and self.end <= self.start:
                errors.append(_err("data", "end", "дата конца должна быть позже начала"))
        if not isinstance(self.cache_dir, str) or not self.cache_dir.strip():
            errors.append(_err("data", "cache_dir", "нужна непустая строка"))
        _raise_all([e for e in errors if e])


@dataclass(frozen=True)
class BrokerConfig:
    commission: float = 0.0005
    slippage: float = 0.0005
    min_change: float = 0.01

    def __post_init__(self) -> None:
        errors = [
            _range_error("broker", "commission", self.commission, 0, 0.1),
            _range_error("broker", "slippage", self.slippage, 0, 0.1),
            _range_error("broker", "min_change", self.min_change, 0, 1),
        ]
        _raise_all([e for e in errors if e])


@dataclass(frozen=True)
class RunConfig:
    cash: float = 10_000.0

    def __post_init__(self) -> None:
        if not _is_number(self.cash) or self.cash <= 0:
            _raise_all([_err("run", "cash", f"нужно положительное число, получено {self.cash!r}")])


@dataclass(frozen=True)
class OutputConfig:
    dir: str = "results"

    def __post_init__(self) -> None:
        if not isinstance(self.dir, str) or not self.dir.strip():
            _raise_all([_err("output", "dir", "нужна непустая строка")])


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


SECTIONS: dict[str, type] = {
    "data": DataConfig,
    "broker": BrokerConfig,
    "run": RunConfig,
    "output": OutputConfig,
}


def _section(raw: dict[str, Any], name: str, cls: type) -> Any:
    values = raw.get(name, {})
    if not isinstance(values, dict):
        raise ConfigError(f"[{name}] должна быть таблицей", field=name)
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ConfigError(
            f"[{name}]: неизвестные параметры {unknown}. Допустимые: {sorted(known)}", field=name
        )
    # Дата без кавычек (2000-01-01) читается как объект date - приводим к строке
    values = {k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()}
    return cls(**values)


def _strategies(raw: dict[str, Any], errors: list[ConfigError]) -> list[StrategyConfig]:
    items = raw.get("strategies")
    if not isinstance(items, list) or not items:
        errors.append(
            ConfigError("Нужна хотя бы одна стратегия: добавьте блок [[strategies]]", "strategies")
        )
        return []

    result: list[StrategyConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"strategies.{i}"

        def fail(detail: str, where: str = where, i: int = i) -> None:
            errors.append(ConfigError(f"[[strategies]] №{i + 1}: {detail}", where, detail))

        if not isinstance(item, dict):
            fail("должна быть таблицей")
            continue
        params = dict(item)
        type_ = params.pop("type", None)
        if not isinstance(type_, str) or type_ not in REGISTRY:
            fail(f"неизвестный type {type_!r}. Доступны: {', '.join(sorted(REGISTRY))}")
            continue
        name = params.pop("name", type_)
        if not isinstance(name, str) or not name.strip():
            fail("name должно быть непустой строкой")
            continue
        if name in seen:
            fail(f"имя стратегии {name!r} повторяется, задайте разные имена")
            continue
        try:
            REGISTRY[type_](**params)
        except (TypeError, ValueError) as exc:
            errors.append(ConfigError(f"[[strategies]] {name!r}: {exc}", where, str(exc)))
            continue
        seen.add(name)
        result.append(StrategyConfig(name=name, type=type_, params=params))
    return result


def collect_config(raw: dict[str, Any]) -> tuple[Config | None, list[ConfigError]]:
    """Проверяет настройки целиком и возвращает все найденные ошибки, а не первую."""
    errors: list[ConfigError] = []
    unknown = sorted(set(raw) - {*SECTIONS, "strategies"})
    if unknown:
        errors.append(
            ConfigError(
                f"Неизвестные разделы {unknown}. Допустимые: {sorted([*SECTIONS, 'strategies'])}"
            )
        )
    parts: dict[str, Any] = {}
    for name, cls in SECTIONS.items():
        try:
            parts[name] = _section(raw, name, cls)
        except ConfigError as exc:
            errors.extend(exc.errors)
    strategies = _strategies(raw, errors)
    if errors:
        return None, errors
    return Config(strategies=strategies, **parts), []


def parse_config(raw: dict[str, Any]) -> Config:
    config, errors = collect_config(raw)
    if config is None:
        raise errors[0]
    return config


def default_config() -> Config:
    """Настройки по умолчанию, если файла config.toml нет."""
    return parse_config(
        {
            "strategies": [
                {"name": "Купить и держать", "type": "buy_and_hold"},
                {"name": "Тренд SMA-200", "type": "sma_trend", "n": 200},
            ]
        }
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
