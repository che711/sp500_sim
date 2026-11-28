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
    {"ticker": "AAA", "qty": 10, "avg_price": 100},
    {"ticker": "BBB", "qty": 20, "avg_price": 50},
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
    assert portfolio.positions == [Position("AAA", 10.0, 100.0), Position("BBB", 20.0, 50.0)]
    lower = parse_config(mine(positions=[{"ticker": "aapl", "qty": 1, "avg_price": 190}]))
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
        ([{"ticker": "AAA", "qty": 0, "avg_price": 100}], "количество"),
        ([{"ticker": "AAA", "qty": -1, "avg_price": 100}], "количество"),
        ([{"ticker": "AAA", "qty": "много", "avg_price": 100}], "количество"),
        ([{"ticker": "AAA", "qty": 1, "avg_price": 0}], "средняя цена"),
        ([{"ticker": "AAA", "qty": 1, "avg_price": -3}], "средняя цена"),
        ([{"ticker": "AAA", "qty": 1}], "средняя цена"),
        ([{"ticker": "A A", "qty": 1, "avg_price": 1}], "тикер"),
        ([{"qty": 1, "avg_price": 1}], "тикер"),
        ([{"ticker": "AAA", "qty": 1, "avg_price": 1, "value": 5}], "ticker, qty, avg_price"),
        (
            [
                {"ticker": "AAA", "qty": 1, "avg_price": 1},
                {"ticker": "aaa", "qty": 2, "avg_price": 2},
            ],
            "дважды",
        ),
        ([], "хотя бы одну позицию"),
        ("AAA", "хотя бы одну позицию"),
        ([{"ticker": f"T{i}", "qty": 1, "avg_price": 1} for i in range(31)], "не больше 30"),
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
        '[my_portfolio]\npositions = [\n  { ticker = "AAPL", qty = 10, avg_price = 190.5 },\n]\n'
    )
    assert load_config(path).my_portfolio.positions == [Position("AAPL", 10.0, 190.5)]


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


def test_value_curve_is_quantity_times_price(stocks):
    positions = [Position("AAA", 10, 100), Position("BBB", 20, 50)]
    frames = {t: stocks[t] for t in ("AAA", "BBB")}
    result = run_holdings(frames, positions, "Мой портфель")
    expected = 10 * stocks["AAA"]["Close"] + 20 * stocks["BBB"]["Close"]
    pd.testing.assert_series_equal(result.equity, expected.rename("equity"))
    assert result.start_cash == pytest.approx(expected.iloc[0])
    assert result.trades.empty and result.metrics["n_trades"] == 0
    assert result.metrics["total_return"] == pytest.approx(expected.iloc[-1] / expected.iloc[0] - 1)


def test_average_price_does_not_change_the_curve(stocks):
    frames = {t: stocks[t] for t in ("AAA", "BBB")}
    cheap = run_holdings(frames, [Position("AAA", 1, 1), Position("BBB", 1, 1)], "p")
    dear = run_holdings(frames, [Position("AAA", 1, 900), Position("BBB", 1, 900)], "p")
    pd.testing.assert_series_equal(cheap.equity, dear.equity)


def test_doubling_quantities_doubles_the_curve(stocks):
    frames = {t: stocks[t] for t in ("AAA", "BBB")}
    one = run_holdings(frames, [Position("AAA", 1, 5), Position("BBB", 2, 5)], "p")
    two = run_holdings(frames, [Position("AAA", 2, 5), Position("BBB", 4, 5)], "p")
    pd.testing.assert_series_equal(two.equity, one.equity * 2)
    assert two.metrics["total_return"] == pytest.approx(one.metrics["total_return"])


# ----------------------------------------------- прогон, хранение и отчёт


def run():
    return service.run_and_save(load_config("config.toml"), DB)


def last_close(stocks, ticker):
    return float(stocks[ticker]["Close"].iloc[-1])


def test_portfolio_comes_first_and_uses_common_period(holdings_project, stocks):
    outcome = run()
    assert [r.name for r in outcome.results] == ["Мой портфель", "Просто TEST"]
    assert outcome.meta.ticker == "AAA, BBB, TEST"
    expected = 10 * last_close(stocks, "AAA") + 20 * last_close(stocks, "BBB")
    assert outcome.results[0].equity.iloc[-1] == pytest.approx(expected)
    assert outcome.results[0].equity.index.equals(outcome.results[1].equity.index)


def test_last_prices_are_saved_with_the_run(holdings_project, stocks):
    outcome = run()
    with closing(storage.connect(DB)) as conn:
        meta, _ = storage.load_meta(conn, outcome.run_id)
    assert meta["last_prices"]["AAA"] == pytest.approx(last_close(stocks, "AAA"))
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


def body_of(run_id):
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, run_id)
    return build_body(stored.results, stored.meta, stored.config)


def test_tile_shows_profit_against_what_you_paid(holdings_project, stocks):
    value = 10 * last_close(stocks, "AAA") + 20 * last_close(stocks, "BBB")
    invested = 10 * 100 + 20 * 50
    body = body_of(run().run_id)
    assert f"{(value / invested - 1) * 100:+.1f}" in body  # главная цифра: к вложенному
    assert "к вложенному" in body
    assert f"$ {invested:,.2f}".replace(",", " ") in body  # «Вложено»


def test_positions_table_shows_cost_price_now_and_profit(holdings_project, stocks):
    body = body_of(run().run_id)
    assert "<h2>Мои позиции</h2>" in body
    assert ">Средняя цена</th>" in body
    assert "100.00" in body and "50.00" in body  # то, что вы ввели
    assert "1 000.00" in body  # вложено в AAA: 10 × 100
    assert f"{last_close(stocks, 'AAA'):,.2f}".replace(",", " ") in body  # цена сейчас
    assert "не ваша фактическая доходность" in body


def test_composition_is_by_current_value(holdings_project, stocks):
    now = {t: n * last_close(stocks, t) for t, n in (("AAA", 10), ("BBB", 20))}
    share = now["AAA"] / sum(now.values()) * 100
    body = body_of(run().run_id)
    big, small = ("AAA", "BBB") if now["AAA"] > now["BBB"] else ("BBB", "AAA")
    big_share = max(share, 100 - share)
    assert f"{big} {big_share:.0f}% · {small} {100 - big_share:.0f}%" in body


def test_chart_has_a_dashed_cost_line_only_with_my_portfolio(holdings_project):
    assert "stroke-dasharray" in body_of(run().run_id)


def test_plain_report_has_no_cost_line(project):
    outcome = service.run_and_save(load_config("config.toml"), DB)
    body = body_of(outcome.run_id)
    assert "stroke-dasharray" not in body and "Мои позиции" not in body


def set_prices(aaa, bbb):
    edit_config(
        '{ ticker = "AAA", qty = 10, avg_price = 100 }',
        f'{{ ticker = "AAA", qty = 10, avg_price = {aaa:.2f} }}',
    )
    edit_config(
        '{ ticker = "BBB", qty = 20, avg_price = 50 }',
        f'{{ ticker = "BBB", qty = 20, avg_price = {bbb:.2f} }}',
    )


def test_report_warns_when_total_was_entered_instead_of_average(holdings_project, stocks):
    # У AAA вместо средней цены ввели стоимость всех 10 акций: в 10 раз больше цены акции
    set_prices(aaa=last_close(stocks, "AAA") * 10, bbb=last_close(stocks, "BBB") * 1.1)
    body = body_of(run().run_id)
    assert "заметно отличается" in body and "(AAA)" in body


def test_report_does_not_warn_when_numbers_agree(holdings_project, stocks):
    set_prices(aaa=last_close(stocks, "AAA") * 0.8, bbb=last_close(stocks, "BBB") * 1.3)
    assert "заметно отличается" not in body_of(run().run_id)


def test_loss_is_shown_as_loss(holdings_project, stocks):
    # Купили гораздо дороже, чем сейчас: убыток к вложенному
    set_prices(aaa=last_close(stocks, "AAA") * 2, bbb=last_close(stocks, "BBB") * 2)
    body = body_of(run().run_id)
    assert "Убыток к вложенному" in body
    assert "-50.0" in body


def test_runs_saved_by_the_previous_version_do_not_break_the_report(holdings_project):
    outcome = run()
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome.run_id)
    for position in stored.config["my_portfolio"]["positions"]:
        position["value"] = position.pop("avg_price")  # так хранились позиции раньше
    body = build_body(stored.results, stored.meta, stored.config)
    assert "Мои позиции" not in body and "kpi tile-0" in body


def test_cli_prints_and_writes_the_portfolio(holdings_project, capsys):
    assert main(["run", "config.toml"]) == 0
    assert "Мой портфель" in capsys.readouterr().out
    html = (holdings_project / "results" / "run_1.html").read_text(encoding="utf-8")
    assert "Мои позиции" in html


def test_compare_shows_changed_positions(holdings_project, capsys):
    main(["run", "config.toml", "--no-report"])
    edit_config("qty = 10, avg_price = 100", "qty = 12, avg_price = 120")
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    assert main(["compare", "1", "2"]) == 0
    out = capsys.readouterr().out
    assert "my_portfolio" in out and "AAA 10×100" in out and "AAA 12×120" in out


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
