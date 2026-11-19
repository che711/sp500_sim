import pytest

from sp500_sim.config import default_config
from sp500_sim.web.forms import (
    MAX_STRATEGIES,
    FormValues,
    StrategyRow,
    config_from_api,
    config_from_stored,
    parse_form,
    parse_params,
    strategy_catalog,
)

DIRS = {"cache_dir": "data", "output_dir": "results"}


def _values(**changes) -> FormValues:
    values = FormValues.from_config(default_config())
    for key, value in changes.items():
        setattr(values, key, value)
    return values


def test_defaults_roundtrip_through_form():
    config = default_config()
    parsed, errors = parse_form(FormValues.from_config(config), **DIRS)
    assert errors == {}
    assert parsed.data == config.data
    assert parsed.run == config.run
    assert parsed.broker.commission == pytest.approx(config.broker.commission)
    assert parsed.broker.min_change == pytest.approx(config.broker.min_change)
    assert [(s.type, s.name, s.params) for s in parsed.strategies] == [
        (s.type, s.name, s.params) for s in config.strategies
    ]


def test_percent_fields_become_fractions_and_comma_is_accepted():
    parsed, errors = parse_form(_values(commission="0,25", slippage="1", min_change="5"), **DIRS)
    assert errors == {}
    assert parsed.broker.commission == pytest.approx(0.0025)
    assert parsed.broker.slippage == pytest.approx(0.01)
    assert parsed.broker.min_change == pytest.approx(0.05)


def test_paths_come_from_the_server():
    parsed, _ = parse_form(_values(), cache_dir="/srv/cache", output_dir="/srv/out")
    assert parsed.data.cache_dir == "/srv/cache"
    assert parsed.output.dir == "/srv/out"


def test_all_errors_are_reported_at_once():
    values = _values(
        ticker="плохой тикер!",
        start="01.01.2020",
        cash="-5",
        commission="abc",
        slippage="50",
    )
    values.strategies = [StrategyRow("sma_trend", "", "n=1"), StrategyRow("buy_and_hold")]
    config, errors = parse_form(values, **DIRS)
    assert config is None
    assert set(errors) == {"ticker", "start", "cash", "commission", "slippage", "strategy.0"}
    assert errors["commission"] == "нужно число"
    assert "%" in errors["slippage"]


def test_empty_end_means_until_latest():
    parsed, errors = parse_form(_values(end=""), **DIRS)
    assert errors == {} and parsed.data.end is None


def test_end_before_start():
    config, errors = parse_form(_values(start="2020-01-01", end="2019-01-01"), **DIRS)
    assert config is None and "end" in errors


def test_strategy_rows():
    values = _values()
    values.strategies = []
    assert parse_form(values, **DIRS)[1] == {"form": "Добавьте хотя бы одну стратегию"}

    values.strategies = [StrategyRow("buy_and_hold", f"s{i}") for i in range(MAX_STRATEGIES + 1)]
    assert "form" in parse_form(values, **DIRS)[1]

    values.strategies = [StrategyRow("buy_and_hold"), StrategyRow("buy_and_hold")]
    config, errors = parse_form(values, **DIRS)
    assert config is None and set(errors) == {"strategy.1"}  # одинаковые названия

    values.strategies = [StrategyRow("momentum")]
    assert "strategy.0" in parse_form(values, **DIRS)[1]


def test_params_cannot_override_type_or_name():
    values = _values()
    values.strategies = [StrategyRow("buy_and_hold", "", "type=sma_trend")]
    _, errors = parse_form(values, **DIRS)
    assert "strategy.0" in errors


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("n=200", {"n": 200}),
        ("n=200, k=0.5", {"n": 200, "k": 0.5}),
        ("mode=fast", {"mode": "fast"}),
        ("flag=true", {"flag": True}),
    ],
)
def test_parse_params(text, expected):
    assert parse_params(text) == expected


@pytest.mark.parametrize("text", ["200", "n=", "=5", "n=1, n=2", "type=x", "name=x", "x" * 300])
def test_parse_params_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_params(text)


def test_api_config_accepts_valid_json():
    raw = {"data": {"ticker": "SPY"}, "strategies": [{"type": "buy_and_hold"}]}
    config, errors = config_from_api(raw, **DIRS)
    assert errors == [] and config.data.cache_dir == "data"


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"strategies": [{"type": "buy_and_hold"}], "output": {"dir": "/tmp"}}, "output"),
        (
            {"data": {"cache_dir": "/etc"}, "strategies": [{"type": "buy_and_hold"}]},
            "cache_dir",
        ),
        ({"strategies": [{"type": "momentum"}]}, "momentum"),
        ({"broker": {"commission": 5}, "strategies": [{"type": "buy_and_hold"}]}, "commission"),
        (["не", "объект"], "JSON-объект"),
        ({"data": "SPY", "strategies": [{"type": "buy_and_hold"}]}, "[data]"),
    ],
)
def test_api_config_rejects(raw, fragment):
    config, errors = config_from_api(raw, **DIRS)
    assert config is None
    assert any(fragment in e for e in errors)


def test_api_config_limits_strategy_count():
    raw = {
        "strategies": [{"type": "buy_and_hold", "name": f"s{i}"} for i in range(MAX_STRATEGIES + 1)]
    }
    config, errors = config_from_api(raw, **DIRS)
    assert config is None and errors


def test_catalog_lists_strategies_with_default_params():
    catalog = {c["type"]: c for c in strategy_catalog()}
    assert set(catalog) == {"buy_and_hold", "sma_trend"}
    assert catalog["sma_trend"]["params"] == "n=200"
    assert catalog["buy_and_hold"]["params"] == "без параметров"
    assert catalog["buy_and_hold"]["doc"]


def test_stored_config_can_be_read_back():
    config = default_config()
    assert config_from_stored(config.to_dict()) == config
