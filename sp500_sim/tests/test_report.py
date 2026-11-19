from sp500_sim.broker import Broker
from sp500_sim.config import parse_config
from sp500_sim.engine import run_strategy
from sp500_sim.report import build_html
from sp500_sim.strategies import BuyAndHold, SmaTrend

META = {
    "created_at": "2026-01-01T00:00:00+00:00",
    "git_commit": None,
    "ticker": "TEST",
    "data_start": "2020-01-01",
    "data_end": "2021-11-12",
    "data_rows": 500,
    "data_hash": "deadbeef",
    "data_fetched_at": "2026-01-01T00:00:00+00:00",
}


def _build(prices, name="Купить и держать"):
    broker = Broker(0.0005, 0.0005, 0.01)
    results = [
        run_strategy(prices, BuyAndHold(), name, 10_000, broker),
        run_strategy(prices, SmaTrend(50), "SMA", 10_000, broker),
    ]
    config = parse_config(
        {
            "strategies": [
                {"name": name, "type": "buy_and_hold"},
                {"name": "SMA", "type": "sma_trend", "n": 50},
            ]
        }
    ).to_dict()
    return build_html(results, META, config, run_id=7), results


def test_report_has_all_sections(prices):
    page, _ = _build(prices)
    assert "Прогон №7" in page
    assert page.count("<svg") == 2  # график капитала и график просадки
    for heading in ("Итоги", "Капитал", "Просадка", "Доходность по годам", "Сделки"):
        assert heading in page


def test_report_lists_trades(prices):
    page, results = _build(prices)
    assert "Покупка" in page
    assert f"сделок: {len(results[1].trades)}" in page


def test_report_escapes_html_in_names(prices):
    page, _ = _build(prices, name="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_report_without_run_id(prices):
    broker = Broker(0, 0, 0.01)
    results = [run_strategy(prices, BuyAndHold(), "bh", 10_000, broker)]
    config = parse_config({"strategies": [{"name": "bh", "type": "buy_and_hold"}]}).to_dict()
    assert "не сохранён" in build_html(results, META, config)
