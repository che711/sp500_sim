"""Чтение и проверка настроек: из файла TOML, из формы на сайте, из JSON."""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any

from .colors import is_hex
from .errors import SimError
from .strategies import REGISTRY

TICKER_RE = re.compile(r"[A-Za-z0-9.^=_-]{1,20}")

REBALANCE_MODES = {
    "never": "никогда",
    "monthly": "раз в месяц",
    "quarterly": "раз в квартал",
    "yearly": "раз в год",
}
MAX_PORTFOLIO_ASSETS = 10  # бумаг в одном портфеле
MAX_TICKERS = 40  # разных бумаг во всём прогоне
MAX_HOLDINGS = 30  # позиций в «Моём портфеле»
DEFAULT_PORTFOLIO_NAME = "Мой портфель"
WEIGHT_TOLERANCE = 0.05  # на сколько процентов сумма весов может отличаться от 100


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
    color: str | None = None  # цвет линии и плитки (#rrggbb); без него берётся цвет из палитры
    # Портфель: тикер -> вес в процентах (в сумме 100). Без него стратегия торгует
    # бумагой из раздела [data].
    portfolio: dict[str, float] | None = None
    rebalance: str = "never"  # как часто возвращать веса портфеля к целевым


@dataclass(frozen=True)
class Position:
    """Позиция из вашего приложения: сколько акций и сколько стоит вся позиция сейчас."""

    ticker: str
    qty: float
    value: float


@dataclass(frozen=True)
class MyPortfolioConfig:
    positions: list[Position]
    name: str = DEFAULT_PORTFOLIO_NAME
    color: str | None = None


@dataclass(frozen=True)
class Config:
    data: DataConfig
    broker: BrokerConfig
    run: RunConfig
    output: OutputConfig
    strategies: list[StrategyConfig]
    my_portfolio: MyPortfolioConfig | None = None

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


def _check_portfolio(value: Any) -> tuple[dict[str, float] | None, str | None]:
    """Возвращает (портфель, текст ошибки). Тикеры приводятся к верхнему регистру."""
    if not isinstance(value, dict) or not value:
        return None, "portfolio: нужен набор «тикер = вес», например { AAPL = 60, MSFT = 40 }"
    if len(value) > MAX_PORTFOLIO_ASSETS:
        return None, f"в портфеле не больше {MAX_PORTFOLIO_ASSETS} бумаг"
    weights: dict[str, float] = {}
    for ticker, weight in value.items():
        name = str(ticker).upper()
        if not TICKER_RE.fullmatch(name):
            return None, f"тикер {ticker!r}: допустимы латинские буквы, цифры и знаки . ^ = _ -"
        if name in weights:
            return None, f"тикер {name} указан дважды"
        if not _is_number(weight) or weight <= 0:
            return None, f"вес {name} должен быть положительным числом, получено {weight!r}"
        weights[name] = float(weight)
    total = sum(weights.values())
    if abs(total - 100) > WEIGHT_TOLERANCE:
        return None, f"веса должны давать 100%, сейчас {total:.4g}%"
    return weights, None


def _strategies(
    raw: dict[str, Any], errors: list[ConfigError], allow_empty: bool = False
) -> list[StrategyConfig]:
    items = raw.get("strategies")
    if allow_empty and items in (None, []):
        return []
    if not isinstance(items, list) or not items:
        errors.append(
            ConfigError(
                "Нужна хотя бы одна стратегия (блок [[strategies]]) "
                "или свои позиции (раздел [my_portfolio])",
                "strategies",
            )
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
        color = params.pop("color", None)
        if color is not None and not is_hex(color):
            fail(f"цвет должен быть в формате #rrggbb, например #e0709f, получено {color!r}")
            continue
        portfolio = None
        if "portfolio" in params:
            portfolio, problem = _check_portfolio(params.pop("portfolio"))
            if problem:
                fail(problem)
                continue
        rebalance = params.pop("rebalance", "never")
        if rebalance not in REBALANCE_MODES:
            fail(f"rebalance: допустимо {', '.join(REBALANCE_MODES)}, получено {rebalance!r}")
            continue
        try:
            REGISTRY[type_](**params)
        except (TypeError, ValueError) as exc:
            errors.append(ConfigError(f"[[strategies]] {name!r}: {exc}", where, str(exc)))
            continue
        seen.add(name)
        result.append(
            StrategyConfig(
                name=name,
                type=type_,
                params=params,
                color=color.lower() if color else None,
                portfolio=portfolio,
                rebalance=rebalance,
            )
        )
    return result


def _my_portfolio(raw: dict[str, Any], errors: list[ConfigError]) -> MyPortfolioConfig | None:
    section = raw.get("my_portfolio")
    if section is None:
        return None

    def fail(detail: str, where: str = "my_portfolio") -> None:
        errors.append(ConfigError(f"[my_portfolio] {detail}", where, detail))

    if not isinstance(section, dict):
        fail("должна быть таблицей")
        return None
    unknown = sorted(set(section) - {"name", "color", "positions"})
    if unknown:
        fail(f"неизвестные параметры {unknown}. Допустимые: ['color', 'name', 'positions']")
        return None

    name = section.get("name", DEFAULT_PORTFOLIO_NAME)
    color = section.get("color")
    problems = len(errors)
    if not isinstance(name, str) or not name.strip():
        fail("name должно быть непустой строкой")
    if color is not None and not is_hex(color):
        fail(
            f"цвет должен быть в формате #rrggbb, например #7d6fe0, получено {color!r}",
            "my_portfolio.color",
        )

    items = section.get("positions")
    if not isinstance(items, list) or not items:
        fail('добавьте хотя бы одну позицию: { ticker = "AAPL", qty = 10, value = 1900 }')
        return None
    if len(items) > MAX_HOLDINGS:
        fail(f"позиций не больше {MAX_HOLDINGS}")
        return None

    positions: list[Position] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"my_portfolio.{i}"
        if not isinstance(item, dict) or set(item) - {"ticker", "qty", "value"}:
            fail("позиция: нужны только поля ticker, qty, value", where)
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        qty, value = item.get("qty"), item.get("value")
        if not TICKER_RE.fullmatch(ticker):
            fail("тикер: допустимы латинские буквы, цифры и знаки . ^ = _ -", where)
        elif ticker in seen:
            fail(f"тикер {ticker} указан дважды", where)
        elif not _is_number(qty) or qty <= 0:
            fail(f"{ticker}: количество должно быть положительным числом", where)
        elif not _is_number(value) or value <= 0:
            fail(f"{ticker}: стоимость должна быть положительным числом", where)
        else:
            seen.add(ticker)
            positions.append(Position(ticker=ticker, qty=float(qty), value=float(value)))
    if len(errors) > problems:
        return None
    return MyPortfolioConfig(
        positions=positions, name=name.strip(), color=color.lower() if color else None
    )


def series_names(config: dict[str, Any]) -> list[str]:
    """Названия линий результата в их порядке: сначала «Мой портфель», потом стратегии.
    config - сохранённые настройки прогона (словарь)."""
    names = []
    portfolio = config.get("my_portfolio")
    if portfolio:
        names.append(portfolio.get("name") or DEFAULT_PORTFOLIO_NAME)
    return names + [s["name"] for s in config.get("strategies", [])]


def needed_tickers(
    data: DataConfig,
    strategies: list[StrategyConfig],
    positions: list[Position] | tuple[()] = (),
) -> list[str]:
    """Все бумаги прогона без повторов: сначала ваши позиции, потом бумаги стратегий.
    Бумага из [data] нужна только тем стратегиям, у которых нет своего портфеля."""
    tickers: list[str] = [p.ticker for p in positions]
    for strategy in strategies:
        for ticker in strategy.portfolio or {data.ticker: 100.0}:
            if ticker not in tickers:
                tickers.append(ticker)
    return tickers


def collect_config(raw: dict[str, Any]) -> tuple[Config | None, list[ConfigError]]:
    """Проверяет настройки целиком и возвращает все найденные ошибки, а не первую."""
    errors: list[ConfigError] = []
    known = sorted([*SECTIONS, "strategies", "my_portfolio"])
    unknown = sorted(set(raw) - set(known))
    if unknown:
        errors.append(ConfigError(f"Неизвестные разделы {unknown}. Допустимые: {known}"))
    parts: dict[str, Any] = {}
    for name, cls in SECTIONS.items():
        try:
            parts[name] = _section(raw, name, cls)
        except ConfigError as exc:
            errors.extend(exc.errors)
    my_portfolio = _my_portfolio(raw, errors)
    strategies = _strategies(raw, errors, allow_empty=my_portfolio is not None)
    if my_portfolio and my_portfolio.name in {s.name for s in strategies}:
        errors.append(
            ConfigError(
                f"Название «{my_portfolio.name}» есть и у портфеля, и у стратегии: задайте разные",
                "my_portfolio",
            )
        )
    if not errors:
        used = needed_tickers(
            parts["data"], strategies, my_portfolio.positions if my_portfolio else ()
        )
        if len(used) > MAX_TICKERS:
            errors.append(
                ConfigError(
                    f"Всего в прогоне не больше {MAX_TICKERS} разных бумаг, сейчас {len(used)}",
                    "strategies",
                )
            )
    if errors:
        return None, errors
    return Config(strategies=strategies, my_portfolio=my_portfolio, **parts), []


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
