import pytest

from sp500_sim.colors import is_hex, lighten, luminance, mix


@pytest.mark.parametrize("value", ["#7d6fe0", "#FFFFFF", "#000000"])
def test_valid_hex(value):
    assert is_hex(value)


@pytest.mark.parametrize("value", ["7d6fe0", "#fff", "#12345g", "красный", "", None, 5])
def test_invalid_hex(value):
    assert not is_hex(value)


def test_mix_ends_and_middle():
    assert mix("#000000", "#ffffff", 0) == "#000000"
    assert mix("#000000", "#ffffff", 1) == "#ffffff"
    assert mix("#000000", "#ffffff", 0.5) == "#808080"


def test_lighten():
    assert lighten("#000000", 0.5) == "#808080"
    assert lighten("#ffffff", 0.5) == "#ffffff"


def test_luminance_orders_colors_by_brightness():
    assert luminance("#000000") == 0
    assert luminance("#ffffff") == pytest.approx(1)
    assert luminance("#ffff00") > 0.55 > luminance("#7d6fe0")
