"""Работа с цветами стратегий: проверка #rrggbb, смешивание, яркость."""

from __future__ import annotations

import re
from typing import Any

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def is_hex(value: Any) -> bool:
    return isinstance(value, str) and HEX_RE.fullmatch(value) is not None


def _to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (round(max(0, min(255, v))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def mix(first: str, second: str, ratio: float) -> str:
    """Смесь двух цветов: ratio 0 даёт первый, 1 - второй."""
    a, b = _to_rgb(first), _to_rgb(second)
    return _to_hex(tuple(x + (y - x) * ratio for x, y in zip(a, b, strict=True)))  # type: ignore[arg-type]


def lighten(color: str, ratio: float) -> str:
    return mix(color, "#ffffff", ratio)


def luminance(color: str) -> float:
    """Относительная яркость от 0 (чёрный) до 1 (белый); по ней выбирают цвет текста."""

    def channel(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _to_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
