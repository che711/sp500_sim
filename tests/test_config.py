import pytest

from sp500_sim.config import (
    BrokerConfig,
    ConfigError,
    DataConfig,
    collect_config,
    load_config,
    parse_config,
)


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


def test_collect_config_reports_every_error_with_its_field():
    raw = {
        "data": {"ticker": "плохо!", "start": "вчера"},
        "broker": {"commission": 5, "slippage": -1},
        "run": {"cash": 0},
        "strategies": [{"type": "sma_trend", "n": 1}, {"type": "momentum"}],
    }
    config, errors = collect_config(raw)
    assert config is None
    assert {e.field for e in errors} == {
        "data.ticker",
        "data.start",
        "broker.commission",
        "broker.slippage",
        "run.cash",
        "strategies.0",
        "strategies.1",
    }


def test_section_error_lists_all_its_problems():
    with pytest.raises(ConfigError) as info:
        BrokerConfig(commission=-1, slippage=-1)
    assert [e.field for e in info.value.errors] == ["broker.commission", "broker.slippage"]


@pytest.mark.parametrize("ticker", ["SPY", "^GSPC", "BRK-B", "EURUSD=X", "BTC-USD"])
def test_good_tickers(ticker):
    assert DataConfig(ticker=ticker).ticker == ticker


@pytest.mark.parametrize("ticker", ["", " ", "SP Y", "../etc", "A" * 21, "а"])
def test_bad_tickers(ticker):
    with pytest.raises(ConfigError, match="ticker"):
        DataConfig(ticker=ticker)


def test_strategy_color_is_optional_and_normalized():
    cfg = parse_config(
        {
            "strategies": [
                {"name": "a", "type": "buy_and_hold", "color": "#E0709F"},
                {"name": "b", "type": "buy_and_hold"},
            ]
        }
    )
    assert [s.color for s in cfg.strategies] == ["#e0709f", None]
    assert cfg.strategies[0].params == {}  # цвет не попадает в параметры стратегии


@pytest.mark.parametrize("color", ["красный", "e0709f", "#fff", 5])
def test_bad_strategy_color(color):
    raw = {"strategies": [{"type": "buy_and_hold", "color": color}]}
    config, errors = collect_config(raw)
    assert config is None
    assert errors[0].field == "strategies.0"
    assert "#rrggbb" in errors[0].detail


# ------------------------------------------------------------------ портфели


def _portfolio(**extra):
    return parse_config({"strategies": [{"name": "p", "type": "buy_and_hold", **extra}]})


def test_portfolio_and_rebalance_are_read():
    cfg = _portfolio(portfolio={"aapl": 60, "msft": 40}, rebalance="quarterly")
    strategy = cfg.strategies[0]
    assert strategy.portfolio == {"AAPL": 60.0, "MSFT": 40.0}  # тикеры в верхнем регистре
    assert strategy.rebalance == "quarterly"
    assert strategy.params == {}  # в параметры стратегии они не попадают


def test_portfolio_is_optional():
    strategy = _portfolio().strategies[0]
    assert strategy.portfolio is None and strategy.rebalance == "never"


def test_portfolio_from_toml_inline_table(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[[strategies]]\ntype = "buy_and_hold"\nportfolio = { AAPL = 50, "^GSPC" = 50 }\n'
    )
    assert load_config(path).strategies[0].portfolio == {"AAPL": 50.0, "^GSPC": 50.0}


@pytest.mark.parametrize(
    ("portfolio", "fragment"),
    [
        ({"AAPL": 60, "MSFT": 30}, "100%, сейчас 90%"),
        ({"AAPL": 60, "MSFT": 60}, "100%, сейчас 120%"),
        ({"AAPL": 100, "MSFT": 0}, "положительным"),
        ({"AAPL": -10, "MSFT": 110}, "положительным"),
        ({"AAPL": "много"}, "положительным"),
        ({"AA PL": 100}, "тикер"),
        ({}, "portfolio"),
        ({"aapl": 50, "AAPL": 50}, "дважды"),
        ("AAPL", "portfolio"),
        ({f"T{i}": 10 for i in range(11)}, "не больше 10"),
    ],
)
def test_bad_portfolios(portfolio, fragment):
    config, errors = collect_config(
        {"strategies": [{"type": "buy_and_hold", "portfolio": portfolio}]}
    )
    assert config is None
    assert errors[0].field == "strategies.0"
    assert fragment in errors[0].detail


def test_bad_rebalance():
    config, errors = collect_config(
        {"strategies": [{"type": "buy_and_hold", "rebalance": "weekly"}]}
    )
    assert config is None and "rebalance" in errors[0].detail


def test_weights_may_be_a_little_off_because_of_rounding():
    assert _portfolio(portfolio={"A": 33.33, "B": 33.33, "C": 33.33}).strategies[0].portfolio


def test_total_ticker_limit():
    strategies = [
        {"name": f"s{i}", "type": "buy_and_hold", "portfolio": {f"T{i}{j}": 12.5 for j in range(8)}}
        for i in range(6)
    ]
    config, errors = collect_config({"strategies": strategies})
    assert config is None and "не больше 40" in str(errors[0])


def test_needed_tickers_uses_data_ticker_only_for_strategies_without_portfolio():
    from sp500_sim.config import needed_tickers

    with_default = parse_config(
        {
            "data": {"ticker": "SPY"},
            "strategies": [
                {"name": "a", "type": "buy_and_hold", "portfolio": {"AAPL": 100}},
                {"name": "b", "type": "buy_and_hold"},
            ],
        }
    )
    assert needed_tickers(with_default.data, with_default.strategies) == ["AAPL", "SPY"]

    portfolios_only = parse_config(
        {
            "data": {"ticker": "SPY"},
            "strategies": [{"name": "a", "type": "buy_and_hold", "portfolio": {"AAPL": 100}}],
        }
    )
    assert needed_tickers(portfolios_only.data, portfolios_only.strategies) == ["AAPL"]
