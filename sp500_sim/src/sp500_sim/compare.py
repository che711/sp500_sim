"""Сравнение настроек нескольких прогонов: что в них отличалось."""

from __future__ import annotations

from typing import Any


def _flatten(meta: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for section in ("data", "broker", "run"):
        for key, value in config[section].items():
            if key != "cache_dir":
                flat[f"{section}.{key}"] = value
    for s in config["strategies"]:
        params = ", ".join(f"{k}={v}" for k, v in s["params"].items())
        flat[f"strategy[{s['name']}]"] = f"{s['type']}({params})"
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
