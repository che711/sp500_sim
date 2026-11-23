import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from sp500_sim import service, storage
from sp500_sim.cli import main
from sp500_sim.config import load_config
from sp500_sim.report import build_body

DB = "results/results.db"


def edit_config(old, new):
    path = Path("config.toml")
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def run(portfolio_project):
    return service.run_and_save(load_config("config.toml"), DB)


def test_portfolio_run_uses_every_ticker_and_one_common_period(portfolio_project, stocks):
    outcome = run(portfolio_project)
    mine, only_test = outcome.results
    assert outcome.meta.ticker == "AAA, BBB, TEST"
    assert set(mine.trades["ticker"]) == {"AAA", "BBB"}
    assert set(only_test.trades["ticker"]) == {"TEST"}
    # Обе стратегии идут на одних датах, то есть с общих дней всех бумаг
    assert mine.equity.index.equals(only_test.equity.index)
    assert mine.equity.iloc[0] == only_test.equity.iloc[0] == 10_000


def test_period_is_shortened_by_the_youngest_ticker(portfolio_project, stocks, monkeypatch):
    config = load_config("config.toml")
    edit_config("AAA = 60, BBB = 40", "AAA = 60, CCC = 40")
    outcome = service.run_and_save(load_config("config.toml"), DB)
    assert outcome.meta.data_start == str(stocks["CCC"].index[0].date())
    assert config.data.start < outcome.meta.data_start


def test_portfolio_is_saved_and_read_back_with_tickers(portfolio_project):
    outcome = run(portfolio_project)
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome.run_id)
    original = outcome.results[0].trades
    restored = stored.results[0].trades
    assert list(restored["ticker"]) == list(original["ticker"])
    assert stored.config["strategies"][0]["portfolio"] == {"AAA": 60.0, "BBB": 40.0}
    assert stored.config["strategies"][0]["rebalance"] == "yearly"


def test_report_shows_portfolio_and_ticker_column(portfolio_project):
    outcome = run(portfolio_project)
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome.run_id)
    body = build_body(stored.results, stored.meta, stored.config)
    assert "AAA 60% · BBB 40%" in body  # состав в плитке
    assert "<th>Тикер</th>" in body  # колонка тикера в сделках
    assert "ребалансировка: раз в год" in body
    assert "Бумаги" in body  # в параметрах: несколько бумаг


def test_report_notes_when_the_start_was_moved(portfolio_project):
    edit_config("AAA = 60, BBB = 40", "AAA = 60, CCC = 40")
    outcome = run(portfolio_project)
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome.run_id)
    assert "запрошено с 2020-01-01" in build_body(stored.results, stored.meta, stored.config)


def test_single_ticker_report_has_no_ticker_column_noise(project):
    outcome = service.run_and_save(load_config("config.toml"), DB)
    with closing(storage.connect(DB)) as conn:
        stored = storage.load_run(conn, outcome.run_id)
    body = build_body(stored.results, stored.meta, stored.config)
    assert "запрошено с" not in body


def test_cli_runs_a_portfolio_config(portfolio_project, capsys):
    assert main(["run", "config.toml"]) == 0
    out = capsys.readouterr().out
    assert "Мой портфель" in out and "Только TEST" in out
    html = (portfolio_project / "results" / "run_1.html").read_text(encoding="utf-8")
    assert "AAA 60% · BBB 40%" in html


def test_compare_shows_portfolio_change(portfolio_project, capsys):
    main(["run", "config.toml", "--no-report"])
    edit_config("AAA = 60, BBB = 40", "AAA = 50, BBB = 50")
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    assert main(["compare", "1", "2"]) == 0
    out = capsys.readouterr().out
    assert "strategy[Мой портфель]" in out
    assert "AAA 60%" in out and "AAA 50%" in out


# --------------------------------------------------------- миграция базы


V2_TRADES = """
CREATE TABLE trades (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, strategy TEXT NOT NULL,
    date TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL,
    fee REAL NOT NULL
);
"""


def test_database_from_version_2_gets_tickers_in_old_trades(project):
    """База из прошлой версии (без тикера в сделках): тикер берётся из самого прогона."""
    outcome = service.run_and_save(load_config("config.toml"), DB)
    conn = sqlite3.connect(DB)
    conn.execute("DROP TABLE trades")
    conn.executescript(V2_TRADES)
    conn.execute(
        "INSERT INTO trades VALUES (1, 'Купить и держать', '2020-01-02', 'BUY', 1, 100, 0.1)"
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    with closing(storage.connect(DB)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        assert conn.execute("SELECT ticker FROM trades").fetchone()[0] == "TEST"
        trades = storage.load_run(conn, outcome.run_id).results[0].trades
        assert list(trades["ticker"]) == ["TEST"]


def test_migration_is_idempotent(project):
    service.run_and_save(load_config("config.toml"), DB)
    for _ in range(2):
        with closing(storage.connect(DB)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION


@pytest.mark.parametrize("name", ["Мой портфель", "Только TEST"])
def test_strategies_keep_their_order(portfolio_project, name):
    outcome = run(portfolio_project)
    assert name in [r.name for r in outcome.results]
