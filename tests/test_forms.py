import pytest

from sp500_sim.config import default_config, parse_config
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
    assert parse_form(values, **DIRS)[1] == {
        "form": "Добавьте хотя бы одну стратегию или позицию портфеля"
    }

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


def test_color_from_form_reaches_config():
    values = _values()
    values.strategies = [
        StrategyRow("buy_and_hold", "a", "", "#ff0000"),
        StrategyRow("sma_trend", "b", "n=50", ""),  # без цвета: берётся палитра
    ]
    config, errors = parse_form(values, **DIRS)
    assert errors == {}
    assert [s.color for s in config.strategies] == ["#ff0000", None]


def test_bad_color_is_reported_in_its_row():
    values = _values()
    values.strategies = [StrategyRow("buy_and_hold", "a", "", "красный")]
    config, errors = parse_form(values, **DIRS)
    assert config is None
    assert "#rrggbb" in errors["strategy.0"]


def test_form_shows_palette_color_when_strategy_has_none():
    rows = FormValues.from_config(default_config()).strategies
    assert [r.color for r in rows] == ["#7d6fe0", "#4a9fe0"]


def test_custom_color_survives_store_and_read_back():
    raw = {"strategies": [{"name": "a", "type": "buy_and_hold", "color": "#ff0000"}]}
    config = parse_config(raw)
    assert config_from_stored(config.to_dict()).strategies[0].color == "#ff0000"
    assert FormValues.from_config(config).strategies[0].color == "#ff0000"


def test_old_stored_runs_without_color_still_load():
    stored = default_config().to_dict()
    for strategy in stored["strategies"]:
        del strategy["color"]  # так хранились прогоны до появления цветов
    assert config_from_stored(stored) == default_config()


# ------------------------------------------------------------------ портфель


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("AAPL 40, MSFT 60", {"AAPL": 40.0, "MSFT": 60.0}),
        ("aapl:40; msft:60", {"AAPL": 40.0, "MSFT": 60.0}),
        ("AAPL 40%, MSFT 60%", {"AAPL": 40.0, "MSFT": 60.0}),
        ("AAPL 33.5, MSFT 66.5", {"AAPL": 33.5, "MSFT": 66.5}),
        ("AAPL 40\nMSFT 60", {"AAPL": 40.0, "MSFT": 60.0}),
        ("^GSPC 50, BRK-B 50", {"^GSPC": 50.0, "BRK-B": 50.0}),
        ("EURUSD=X 100", {"EURUSD=X": 100.0}),
    ],
)
def test_parse_portfolio(text, expected):
    from sp500_sim.web.forms import parse_portfolio

    assert parse_portfolio(text) == expected


def test_portfolio_without_weights_is_split_equally():
    from sp500_sim.web.forms import parse_portfolio

    weights = parse_portfolio("AAPL, MSFT, GOOG")
    assert sum(weights.values()) == pytest.approx(100)
    assert weights["AAPL"] == pytest.approx(100 / 3)


@pytest.mark.parametrize(
    "text",
    [
        "AAPL 40, MSFT",
        "AAPL 40, AAPL 60",
        "AAPL!, MSFT",
        "40 AAPL",
        "A, B, C, D, E, F, G, H, I, J, K",
    ],
)
def test_parse_portfolio_rejects(text):
    from sp500_sim.web.forms import parse_portfolio

    with pytest.raises(ValueError):
        parse_portfolio(text)


def test_portfolio_from_form_reaches_config():
    values = _values()
    values.strategies = [
        StrategyRow("buy_and_hold", "мой", "", "", "AAPL 70, MSFT 30", "monthly"),
        StrategyRow("buy_and_hold", "обычный"),
    ]
    config, errors = parse_form(values, **DIRS)
    assert errors == {}
    assert config.strategies[0].portfolio == {"AAPL": 70.0, "MSFT": 30.0}
    assert config.strategies[0].rebalance == "monthly"
    assert config.strategies[1].portfolio is None


def test_portfolio_errors_are_shown_in_its_row():
    values = _values()
    values.strategies = [StrategyRow("buy_and_hold", "a", "", "", "AAPL 40, MSFT 40")]
    config, errors = parse_form(values, **DIRS)
    assert config is None and "100%" in errors["strategy.0"]

    values.strategies = [StrategyRow("buy_and_hold", "a", "", "", "AAPL 40, MSFT")]
    _, errors = parse_form(values, **DIRS)
    assert "портфель" in errors["strategy.0"]


def test_portfolio_survives_store_and_read_back_and_form_roundtrip():
    raw = {
        "strategies": [
            {"name": "a", "type": "buy_and_hold", "portfolio": {"AAPL": 25, "MSFT": 75}},
        ]
    }
    config = parse_config({**raw, "strategies": [{**raw["strategies"][0], "rebalance": "yearly"}]})
    assert config_from_stored(config.to_dict()) == config

    row = FormValues.from_config(config).strategies[0]
    assert row.portfolio == "AAPL 25, MSFT 75" and row.rebalance == "yearly"
    parsed, errors = parse_form(FormValues.from_config(config), **DIRS)
    assert errors == {} and parsed.strategies[0].portfolio == {"AAPL": 25.0, "MSFT": 75.0}


# ------------------------------------------------------------ мой портфель


def _holdings_values(*rows, color="", with_strategies=True):
    from sp500_sim.web.forms import HoldingRow

    values = _values()
    values.holdings = [HoldingRow(*row) for row in rows]
    values.portfolio_color = color
    if not with_strategies:
        values.strategies = []
    return values


def test_holdings_from_form_reach_config():
    values = _holdings_values(("aapl", "10", "1900"), ("MSFT", "5,5", "210,50"))
    config, errors = parse_form(values, **DIRS)
    assert errors == {}
    positions = config.my_portfolio.positions
    assert [(p.ticker, p.qty, p.avg_price) for p in positions] == [
        ("AAPL", 10.0, 1900.0),
        ("MSFT", 5.5, 210.5),  # запятая как десятичный знак принимается
    ]


def test_empty_holding_rows_are_ignored():
    config, errors = parse_form(_holdings_values(("", "", ""), ("AAPL", "1", "100")), **DIRS)
    assert errors == {} and len(config.my_portfolio.positions) == 1
    config, errors = parse_form(_holdings_values(("", "", "")), **DIRS)
    assert errors == {} and config.my_portfolio is None


def test_portfolio_color_from_form():
    config, errors = parse_form(_holdings_values(("AAPL", "1", "100"), color="#ff8800"), **DIRS)
    assert errors == {} and config.my_portfolio.color == "#ff8800"
    _, errors = parse_form(_holdings_values(("AAPL", "1", "100"), color="оранжевый"), **DIRS)
    assert "#rrggbb" in errors["portfolio_color"]


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        (("AAPL", "abc", "100"), "количество: нужно число"),
        (("AAPL", "1", ""), "стоимость позиции: нужно число"),
        (("AAPL", "0", "100"), "количество"),
        (("AAPL", "1", "-5"), "средняя цена"),
        (("A A", "1", "100"), "тикер"),
        (("", "1", "100"), "тикер"),
        (("AAPL", "", ""), "нужно число"),
    ],
)
def test_holding_errors_are_shown_in_the_row(row, fragment):
    config, errors = parse_form(_holdings_values(row), **DIRS)
    assert config is None
    assert fragment in errors["holding.0"]


def test_duplicate_tickers_are_reported_in_the_second_row():
    values = _holdings_values(("AAPL", "1", "100"), ("aapl", "2", "200"))
    _, errors = parse_form(values, **DIRS)
    assert set(errors) == {"holding.1"} and "дважды" in errors["holding.1"]


def test_only_my_portfolio_is_enough_without_strategies():
    config, errors = parse_form(
        _holdings_values(("AAPL", "1", "100"), with_strategies=False), **DIRS
    )
    assert errors == {} and config.strategies == [] and config.my_portfolio


def test_form_values_roundtrip_with_holdings_and_plain_numbers():
    from sp500_sim.web.forms import HoldingRow

    raw = {
        "my_portfolio": {
            "color": "#123456",
            "positions": [{"ticker": "AAPL", "qty": 10.5, "avg_price": 1234567.0}],
        },
        "strategies": [{"name": "s", "type": "buy_and_hold"}],
    }
    config = parse_config(raw)
    values = FormValues.from_config(config)
    assert values.holdings == [HoldingRow("AAPL", "10.5", "1234567")]  # без «1.23457e+06»
    assert values.portfolio_color == "#123456"
    parsed, errors = parse_form(values, **DIRS)
    assert errors == {} and parsed.my_portfolio == config.my_portfolio
    assert config_from_stored(config.to_dict()) == config


def test_cash_is_shown_without_exponent():
    config = parse_config(
        {"run": {"cash": 1_000_000}, "strategies": [{"name": "s", "type": "buy_and_hold"}]}
    )
    assert FormValues.from_config(config).cash == "1000000"
