"""Сравнение настроек нескольких прогонов: что в них отличалось."""

from __future__ import annotations

from typing import Any


def _flatten(meta: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for section in ("data", "broker", "run"):
        for key, value in config[section].items():
            if key != "cache_dir":
                flat[f"{section}.{key}"] = value
    mine = config.get("my_portfolio")
    if mine:
        positions = ", ".join(
            f"{p['ticker']} {p['qty']:g}×{p['value']:g}" for p in mine["positions"]
        )
        flat["my_portfolio"] = positions
    for s in config["strategies"]:
        params = ", ".join(f"{k}={v}" for k, v in s["params"].items())
        text = f"{s['type']}({params})"
        if s.get("portfolio"):  # в прогонах, сохранённых раньше, портфеля нет
            assets = ", ".join(f"{t} {w:g}%" for t, w in s["portfolio"].items())
            text += f" | {assets} | ребаланс: {s.get('rebalance', 'never')}"
        flat[f"strategy[{s['name']}]"] = text
    flat["код (коммит)"] = meta["git_commit"]
    flat["данные (хеш)"] = meta["data_hash"]
    return flat


def config_diff(
    runs: dict[int, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, dict[int, Any]]:
    """runs: {номер прогона: (описание, настройки)}.
    Возвращает только те параметры, которые различаются между прогонами."""
    flat = {run_id: _flatten(meta, cfg) for run_id, (meta, cfg) in runs.items()}
    keys = sorted({k for f in flat.values() for k in f})
    diff: dict[str, dict[int, Any]] = {}
    for key in keys:
        values = {run_id: f.get(key) for run_id, f in flat.items()}
        if len({repr(v) for v in values.values()}) > 1:
            diff[key] = values
    return diff
