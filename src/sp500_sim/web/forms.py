"""Разбор форм и JSON с сайта: введённые строки -> проверенные настройки.

Сайт не даёт пользователю выбирать пути (папку кэша и папку результатов):
их задаёт сервер. Стратегии можно выбирать только из списка REGISTRY, свой код
сайт не принимает."""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any

from ..config import Config, collect_config, parse_config
from ..strategies import REGISTRY

MAX_STRATEGIES = 6
MAX_PARAMS_LENGTH = 200

# Комиссию и проскальзывание в форме вводят в процентах, в настройках они долями
PERCENT_LIMITS = {"commission": 10, "slippage": 10, "min_change": 100}


@dataclass
class StrategyRow:
    type: str = ""
    name: str = ""
    params: str = ""


@dataclass
class FormValues:
    ticker: str = ""
    start: str = ""
    end: str = ""
    cash: str = ""
    commission: str = ""
    slippage: str = ""
    min_change: str = ""
    refresh: bool = False
    strategies: list[StrategyRow] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: Config) -> FormValues:
        return cls(
            ticker=config.data.ticker,
            start=config.data.start,
            end=config.data.end or "",
            cash=f"{config.run.cash:g}",
            commission=_percent(config.broker.commission),
            slippage=_percent(config.broker.slippage),
            min_change=_percent(config.broker.min_change),
            strategies=[
                StrategyRow(type=s.type, name=s.name, params=format_params(s.params))
                for s in config.strategies
            ],
        )

    @classmethod
    def from_form(cls, form: Any) -> FormValues:
        def text(key: str) -> str:
            return str(form.get(key, "")).strip()

        rows = zip_longest(
            form.getlist("strategy_type"),
            form.getlist("strategy_name"),
            form.getlist("strategy_params"),
            fillvalue="",
        )
        return cls(
            ticker=text("ticker"),
            start=text("start"),
            end=text("end"),
            cash=text("cash"),
            commission=text("commission"),
            slippage=text("slippage"),
            min_change=text("min_change"),
            refresh=form.get("refresh") is not None,
            strategies=[
                StrategyRow(str(t).strip(), str(n).strip(), str(p).strip()) for t, n, p in rows
            ],
        )


def _percent(fraction: float) -> str:
    return f"{fraction * 100:.6g}"


def format_params(params: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in params.items())


def parse_params(text: str) -> dict[str, Any]:
    """'n=200, k=0.5' -> {'n': 200, 'k': 0.5}. Значения читаются как JSON, иначе строкой."""
    text = text.strip()
    if len(text) > MAX_PARAMS_LENGTH:
        raise ValueError("слишком длинная строка")
    params: dict[str, Any] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = (x.strip() for x in part.partition("="))
        if not sep or not key.isidentifier() or not value:
            raise ValueError("параметры пишутся так: n=200, k=0.5")
        if key in ("type", "name"):
            raise ValueError(f"{key} задаётся в отдельном поле")
        if key in params:
            raise ValueError(f"параметр {key} указан дважды")
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def _number(text: str, name: str, errors: dict[str, str]) -> float | None:
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        errors[name] = "нужно число"
        return None
    return value


def parse_form(
    values: FormValues, *, cache_dir: str, output_dir: str
) -> tuple[Config | None, dict[str, str]]:
    """Возвращает (настройки, ошибки). Ошибки - словарь «имя поля -> текст», показывается под полем.
    Ключ strategy.N - строка стратегии с номером N, ключ form - общая ошибка."""
    errors: dict[str, str] = {}
    data: dict[str, Any] = {"ticker": values.ticker, "start": values.start, "cache_dir": cache_dir}
    if values.end:
        data["end"] = values.end
    raw: dict[str, Any] = {
        "data": data,
        "broker": {},
        "run": {},
        "output": {"dir": output_dir},
        "strategies": [],
    }

    cash = _number(values.cash, "cash", errors)
    if cash is not None:
        raw["run"]["cash"] = cash
    for name in PERCENT_LIMITS:
        percent = _number(getattr(values, name), name, errors)
        if percent is not None:
            raw["broker"][name] = percent / 100

    if not values.strategies:
        errors["form"] = "Добавьте хотя бы одну стратегию"
    elif len(values.strategies) > MAX_STRATEGIES:
        errors["form"] = f"Стратегий не больше {MAX_STRATEGIES}"
    for i, row in enumerate(values.strategies[:MAX_STRATEGIES]):
        item: dict[str, Any] = {"type": row.type}
        if row.name:
            item["name"] = row.name
        try:
            item.update(parse_params(row.params))
        except ValueError as exc:
            errors[f"strategy.{i}"] = str(exc)
        raw["strategies"].append(item)

    config, config_errors = collect_config(raw)
    for exc in config_errors:
        where = exc.field or ""
        if where.startswith("strategies."):
            key = "strategy." + where.split(".")[1]
        elif where.startswith(("data.", "broker.", "run.")):
            key = where.split(".", 1)[1]
        else:
            key = "form"
        detail = exc.detail
        if key in PERCENT_LIMITS:
            detail = f"нужно число не меньше 0 и меньше {PERCENT_LIMITS[key]} %"
        errors.setdefault(key, detail)

    if errors or config is None:
        return None, errors
    return config, {}


def config_from_api(
    raw: Any, *, cache_dir: str, output_dir: str
) -> tuple[Config | None, list[str]]:
    """Настройки из JSON. Те же разделы, что в config.toml, но без output и data.cache_dir."""
    if not isinstance(raw, dict):
        return None, ["Ожидается JSON-объект с настройками"]
    errors = []
    data = raw.get("data", {})
    if not isinstance(data, dict):
        return None, ["[data] должна быть объектом"]
    if "output" in raw:
        errors.append("раздел output задаётся на сервере")
    if "cache_dir" in data:
        errors.append("data.cache_dir задаётся на сервере")
    if errors:
        return None, errors

    full = {**raw, "data": {**data, "cache_dir": cache_dir}, "output": {"dir": output_dir}}
    config, config_errors = collect_config(full)
    if config is None:
        return None, [str(e) for e in config_errors]
    if len(config.strategies) > MAX_STRATEGIES:
        return None, [f"Стратегий не больше {MAX_STRATEGIES}"]
    return config, []


def config_from_stored(stored: dict[str, Any]) -> Config:
    """Настройки из сохранённого прогона (для кнопки «повторить с этими настройками»).
    В базе параметры стратегии лежат во вложенном поле params, а при чтении они плоские."""
    strategies = [
        {"name": s["name"], "type": s["type"], **s["params"]} for s in stored["strategies"]
    ]
    return parse_config({**stored, "strategies": strategies})


def strategy_catalog() -> list[dict[str, str]]:
    """Список доступных стратегий для формы: имя, описание, параметры с значениями по умолчанию."""
    catalog = []
    for type_, cls in sorted(REGISTRY.items()):
        doc = (inspect.getdoc(cls) or "").splitlines()
        params = [
            f"{p.name}={json.dumps(p.default)}"
            for p in inspect.signature(cls.__init__).parameters.values()
            if p.name != "self" and p.default is not inspect.Parameter.empty
        ]
        catalog.append(
            {
                "type": type_,
                "doc": doc[0] if doc else "",
                "params": ", ".join(params) or "без параметров",
            }
        )
    return catalog
