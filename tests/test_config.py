import pytest

from sp500_sim.config import ConfigError, load_config, parse_config


def test_loads_valid_file(write_config):
    cfg = load_config(write_config())
    assert cfg.data.ticker == "TEST"
    assert [s.type for s in cfg.strategies] == ["buy_and_hold", "sma_trend"]
    assert cfg.strategies[1].params == {"n": 50}


def test_missing_file():
    with pytest.raises(ConfigError, match="не найден"):
        load_config("нет_такого.toml")


def test_unknown_parameter_is_rejected():
    raw = {"broker": {"comission": 0.1}, "strategies": [{"type": "buy_and_hold"}]}
    with pytest.raises(ConfigError, match="comission"):
        parse_config(raw)


def test_unknown_section_is_rejected():
    with pytest.raises(ConfigError, match="разделы"):
        parse_config({"broker2": {}, "strategies": [{"type": "buy_and_hold"}]})


@pytest.mark.parametrize("commission", [-0.1, 0.5, "много"])
def test_bad_commission(commission):
    raw = {"broker": {"commission": commission}, "strategies": [{"type": "buy_and_hold"}]}
    with pytest.raises(ConfigError, match="commission"):
        parse_config(raw)


def test_bad_date():
    raw = {"data": {"start": "01.01.2020"}, "strategies": [{"type": "buy_and_hold"}]}
    with pytest.raises(ConfigError, match="start"):
        parse_config(raw)


def test_end_before_start():
    raw = {
        "data": {"start": "2020-01-01", "end": "2019-01-01"},
        "strategies": [{"type": "buy_and_hold"}],
    }
    with pytest.raises(ConfigError, match="end"):
        parse_config(raw)


def test_unquoted_toml_date_is_accepted(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[data]\nstart = 2010-01-01\n[[strategies]]\ntype = "buy_and_hold"\n')
    assert load_config(path).data.start == "2010-01-01"


def test_no_strategies():
    with pytest.raises(ConfigError, match="хотя бы одна стратегия"):
        parse_config({})


def test_unknown_strategy_type():
    with pytest.raises(ConfigError, match="momentum"):
        parse_config({"strategies": [{"type": "momentum"}]})


def test_bad_strategy_parameter():
    with pytest.raises(ConfigError, match="n"):
        parse_config({"strategies": [{"type": "sma_trend", "n": 1}]})


def test_strategy_with_extra_parameter():
    with pytest.raises(ConfigError):
        parse_config({"strategies": [{"type": "buy_and_hold", "n": 5}]})


def test_duplicate_strategy_names():
    raw = {"strategies": [{"type": "buy_and_hold"}, {"type": "buy_and_hold"}]}
    with pytest.raises(ConfigError, match="повторяется"):
        parse_config(raw)
