import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

from sp500_sim import service, storage
from sp500_sim.cli import main
from sp500_sim.config import (
    Position,
    collect_config,
    load_config,
    needed_tickers,
    parse_config,
    series_names,
)
from sp500_sim.engine import run_holdings
from sp500_sim.report import build_body

DB = "results/results.db"
POSITIONS = [
    {"ticker": "AAA", "qty": 10, "value": 6000},
    {"ticker": "BBB", "qty": 20, "value": 4000},
]


def mine(**extra):
    return {"my_portfolio": {"positions": POSITIONS, **extra}}


def edit_config(old, new):
    path = Path("config.toml")
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


# ------------------------------------------------------------------ настройки


def test_positions_are_read_and_normalized():
    config = parse_config(mine())
    portfolio = config.my_portfolio
    assert portfolio.name == "Мой портфель" and portfolio.color is None
    assert portfolio.positions == [Position("AAA", 10.0, 6000.0), Position("BBB", 20.0, 4000.0)]
    lower = parse_config(mine(positions=[{"ticker": "aapl", "qty": 1, "value": 100}]))
    assert lower.my_portfolio.positions[0].ticker == "AAPL"


def test_name_and_color_are_optional():
    config = parse_config(mine(name="Брокерский счёт", color="#E0709F"))
    assert (config.my_portfolio.name, config.my_portfolio.color) == ("Брокерский счёт", "#e0709f")


def test_strategies_are_not_required_when_positions_are_given():
    assert parse_config(mine()).strategies == []


def test_neither_strategies_nor_positions_is_an_error():
    config, errors = collect_config({})
    assert config is None and "или свои позиции" in str(errors[0])


@pytest.mark.parametrize(
    ("positions", "fragment"),
    [
        ([{"ticker": "AAA", "qty": 0, "value": 100}], "количество"),
        ([{"ticker": "AAA", "qty": -1, "value": 100}], "количество"),
        ([{"ticker": "AAA", "qty": "много", "value": 100}], "количество"),
        ([{"ticker": "AAA", "qty": 1, "value": 0}], "стоимость"),
        ([{"ticker": "AAA", "qty": 1}], "стоимость"),
        ([{"ticker": "A A", "qty": 1, "value": 1}], "тикер"),
        ([{"qty": 1, "value": 1}], "тикер"),
        ([{"ticker": "AAA", "qty": 1, "value": 1, "price": 5}], "ticker, qty, value"),
        (
            [{"ticker": "AAA", "qty": 1, "value": 1}, {"ticker": "aaa", "qty": 2, "value": 2}],
            "дважды",
        ),
        ([], "хотя бы одну позицию"),
        ("AAA", "хотя бы одну позицию"),
        ([{"ticker": f"T{i}", "qty": 1, "value": 1} for i in range(31)], "не больше 30"),
    ],
)
def test_bad_positions(positions, fragment):
    config, errors = collect_config({"my_portfolio": {"positions": positions}})
    assert config is None
    assert fragment in errors[0].detail
    assert errors[0].field.startswith("my_portfolio")


def test_bad_color_and_unknown_key():
    _, errors = collect_config(mine(color="красный"))
    assert errors[0].field == "my_portfolio.color"
    _, errors = collect_config(mine(extra=1))
    assert "неизвестные параметры" in errors[0].detail


def test_portfolio_name_must_differ_from_strategy_names():
    config, errors = collect_config(
        {**mine(name="Одно"), "strategies": [{"name": "Одно", "type": "buy_and_hold"}]}
    )
    assert config is None and "разные" in str(errors[0])


def test_positions_from_toml(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[my_portfolio]\npositions = [\n  { ticker = "AAPL", qty = 10, value = 1900.5 },\n]\n'
    )
    assert load_config(path).my_portfolio.positions == [Position("AAPL", 10.0, 1900.5)]


def test_my_tickers_come_first_and_data_ticker_is_not_needed_without_strategies():
    config = parse_config({"data": {"ticker": "SPY"}, **mine()})
    positions = config.my_portfolio.positions
    assert needed_tickers(config.data, config.strategies, positions) == ["AAA", "BBB"]
    with_spy = parse_config(
        {"data": {"ticker": "SPY"}, **mine(), "strategies": [{"name": "s", "type": "buy_and_hold"}]}
    )
    assert needed_tickers(with_spy.data, with_spy.strategies, positions) == ["AAA", "BBB", "SPY"]


def test_series_names_put_the_portfolio_first():
    config = parse_config(
        {**mine(), "strategies": [{"name": "s", "type": "buy_and_hold"}]}
    ).to_dict()
    assert series_names(config) == ["Мой портфель", "s"]
    assert series_names({"strategies": [{"name": "s"}]}) == ["s"]


# --------------------------------------------------------------------- расчёт


def test_value_curve_ends_at_the_current_value_and_follows_prices(stocks):
    positions = [Position("AAA", 10, 6000), Position("BBB", 20, 4000)]
    result = run_holdings({t: stocks[t] for t in ("AAA", "BBB")}, positions, "Мой портфель")
    assert result.equity.iloc[-1] == pytest.approx(10_000)  # ровно то, что вы ввели
    first = sum(
        p.value * stocks[p.ticker]["Close"].iloc[0] / stocks[p.ticker]["Close"].iloc[-1]
        for p in positions
    )
    assert result.equity.iloc[0] == pytest.approx(first)
    assert result.start_cash == pytest.approx(first)
    assert result.trades.empty
    assert result.metrics["n_trades"] == 0
    assert result.metrics["total_return"] == pytest.approx(10_000 / first - 1)


def test_scaling_all_values_scales_the_curve(stocks):
    frames = {t: stocks[t] for t in ("AAA", "BBB")}
    one = run_holdings(frames, [Position("AAA", 1, 100), Position("BBB", 1, 50)], "p")
    two = run_holdings(frames, [Position("AAA", 1, 200), Position("BBB", 1, 100)], "p")
    pd.testing.assert_series_equal(two.equity, one.equity * 2)
    assert two.metrics["total_return"] == pytest.approx(one.metrics["total_return"])


def test_single_position_follows_its_own_price(stocks):
    result = run_holdings({"AAA": stocks["AAA"]}, [Position("AAA", 3, 900)], "p")
    expected = 900 * stocks["AAA"]["Close"] / stocks["AAA"]["Close"].iloc[-1]
    pd.testing.assert_series_equal(result.equity, expected.rename("equity"))


# ----------------------------------------------- прогон, хранение и отчёт


def run():
    return service.run_and_save(load_config("config.toml"), DB)


def test_portfolio_comes_first_and_uses_common_period(holdings_project, stocks):
    outcome = run()
    names = [r.name for r in outcome.results]
    assert names == ["Мой портфель", "Просто TEST"]
    assert outcome.meta.ticker == "AAA, BBB, TEST"
    assert outcome.results[0].equity.iloc[-1] == pytest.approx(10_000)
    assert outcome.results[0].equity.index.equals(outcome.results[1].equity.index)


def test_last_prices_are_saved_with_the_run(holdings_project, stocks):
    outcome = run()
    with closing(storage.connect(DB)) as conn:
        meta, _ = storage.load_meta(conn, outcome.run_id)
    assert meta["last_prices"]["AAA"] == pytest.approx(stocks["AAA"]["Close"].iloc[-1])
    assert set(meta["last_prices"]) == {"AAA", "BBB", "TEST"}


def test_stored_run_keeps_the_order_and_curves(holdings_project):
    outcome = run()
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome.run_id)
    assert [r.name for r in stored.results] == ["Мой портфель", "Просто TEST"]
    assert stored.results[0].equity.to_numpy() == pytest.approx(
        outcome.results[0].equity.to_numpy()
    )
    assert stored.results[0].trades.empty


def body_of(outcome_id):
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome_id)
    return build_body(stored.results, stored.meta, stored.config)


def test_report_shows_positions_and_composition(holdings_project):
    body = body_of(run().run_id)
    assert "<h2>Мои позиции</h2>" in body
    assert "AAA 60% · BBB 40%" in body  # доли считаются по стоимости 6000 и 4000
    assert "600.00" in body  # цена за акцию из ваших данных: 6000 / 10
    assert "200.00" in body  # 4000 / 20
    assert "не ваша фактическая доходность" in body
    assert 'class="kpi tile-0"' in body  # портфель идёт первой плиткой


def set_values(stocks, aaa, bbb):
    """Записывает в config.toml стоимости позиций (AAA: 10 штук, BBB: 20 штук)."""
    edit_config(
        '{ ticker = "AAA", qty = 10, value = 6000 }',
        f'{{ ticker = "AAA", qty = 10, value = {aaa:.2f} }}',
    )
    edit_config(
        '{ ticker = "BBB", qty = 20, value = 4000 }',
        f'{{ ticker = "BBB", qty = 20, value = {bbb:.2f} }}',
    )


def test_report_warns_when_value_looks_like_a_price_per_share(holdings_project, stocks):
    last = {t: stocks[t]["Close"].iloc[-1] for t in ("AAA", "BBB")}
    # BBB введён верно (20 штук по цене из данных), а у AAA вместо стоимости позиции цена акции
    set_values(stocks, aaa=last["AAA"], bbb=last["BBB"] * 20)
    body = body_of(run().run_id)
    assert "заметно отличается" in body and "(AAA)" in body


def test_report_does_not_warn_when_numbers_agree(holdings_project, stocks):
    last = {t: stocks[t]["Close"].iloc[-1] for t in ("AAA", "BBB")}
    set_values(stocks, aaa=last["AAA"] * 10, bbb=last["BBB"] * 20)
    assert "заметно отличается" not in body_of(run().run_id)


def test_report_without_portfolio_has_no_positions_section(project):
    outcome = service.run_and_save(load_config("config.toml"), DB)
    assert "Мои позиции" not in body_of(outcome.run_id)


def test_cli_prints_and_writes_the_portfolio(holdings_project, capsys):
    assert main(["run", "config.toml"]) == 0
    assert "Мой портфель" in capsys.readouterr().out
    html = (holdings_project / "results" / "run_1.html").read_text(encoding="utf-8")
    assert "Мои позиции" in html


def test_compare_shows_changed_positions(holdings_project, capsys):
    main(["run", "config.toml", "--no-report"])
    edit_config("qty = 10, value = 6000", "qty = 12, value = 7200")
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    assert main(["compare", "1", "2"]) == 0
    out = capsys.readouterr().out
    assert "my_portfolio" in out and "AAA 10×6000" in out and "AAA 12×7200" in out


def test_database_from_version_3_still_works(holdings_project):
    """База без колонки last_prices (прошлая версия) обновляется, старый отчёт строится."""
    outcome = run()
    conn = sqlite3.connect(DB)
    conn.execute("ALTER TABLE runs DROP COLUMN last_prices")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    with closing(storage.connect(DB)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        meta, _ = storage.load_meta(conn, outcome.run_id)
    assert meta["last_prices"] is None
    body = body_of(outcome.run_id)
    assert "Мои позиции" in body  # без последних цен таблица всё равно строится
