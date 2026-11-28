import time

import pytest

from sp500_sim import data, storage
from sp500_sim.data import DataError

FORM = {
    "ticker": "TEST",
    "start": "2020-01-01",
    "end": "2022-01-01",
    "cash": "10000",
    "commission": "0.05",
    "slippage": "0.05",
    "min_change": "1",
    "strategy_type": ["buy_and_hold", "sma_trend"],
    "strategy_name": ["Купить и держать", "Тренд SMA-50"],
    "strategy_params": ["", "n=50"],
}

API_CONFIG = {
    "data": {"ticker": "TEST", "start": "2020-01-01", "end": "2022-01-01"},
    "broker": {"commission": 0.0005},
    "strategies": [
        {"name": "bh", "type": "buy_and_hold"},
        {"name": "sma", "type": "sma_trend", "n": 50},
    ],
}


def api_run(client, **broker):
    config = {**API_CONFIG, "broker": {**API_CONFIG["broker"], **broker}}
    response = client.post("/api/runs", json=config)
    assert response.status_code == 202, response.text
    return response.json()["id"]


# ---------------------------------------------------------------- страницы


def test_index_is_prefilled_from_config(web):
    page = web.get("/")
    assert page.status_code == 200
    assert 'value="TEST"' in page.text
    assert 'value="0.05"' in page.text  # комиссия 0.0005 показана в процентах
    assert "Тренд SMA-50" in page.text


def test_valid_form_creates_run_and_report(web):
    response = web.post("/runs", data=FORM)
    assert response.status_code == 303
    assert response.headers["location"] == "/runs/1"

    page = web.get("/runs/1")
    assert page.status_code == 200
    assert "Доходность по годам" in page.text  # отчёт встроен в страницу прогона
    assert "<iframe" not in page.text

    report = web.get("/runs/1/report")
    assert report.status_code == 200
    assert report.text.count("<svg") == 4
    assert "Тренд SMA-50" in report.text


def test_invalid_form_shows_errors_and_creates_nothing(web):
    bad = {**FORM, "commission": "abc", "start": "01.01.2020", "strategy_params": ["", "n=1"]}
    response = web.post("/runs", data=bad)
    assert response.status_code == 422
    assert "нужно число" in response.text
    assert "n должно быть целым числом не меньше 2" in response.text
    assert web.get("/api/runs").json() == []


def test_form_keeps_what_was_typed_after_error(web):
    bad = {**FORM, "ticker": "MYTICKER", "commission": "abc", "strategy_params": ["", "n=1"]}
    page = web.post("/runs", data=bad).text
    assert 'value="MYTICKER"' in page
    assert 'value="n=1"' in page


def test_strategy_names_are_escaped(web):
    name = "<b>x</b>"
    web.post("/runs", data={**FORM, "strategy_name": [name, "Тренд SMA-50"]})
    runs = web.get("/runs").text
    assert "&lt;b&gt;x&lt;/b&gt;" in runs
    assert name not in runs


def test_form_cannot_choose_server_paths(web):
    web.post("/runs", data={**FORM, "cache_dir": "/etc", "dir": "/tmp/evil"})
    meta, config = storage.load_meta(storage.connect("results/results.db"), 1)
    assert config["data"]["cache_dir"] == "data"
    assert config["output"]["dir"] == "results"


def test_from_run_prefills_form(web):
    web.post("/runs", data={**FORM, "commission": "0.2"})
    page = web.get("/", params={"from_run": 1})
    assert 'value="0.2"' in page.text
    assert web.get("/", params={"from_run": 99}).status_code == 404


def test_runs_page_and_compare_picker(web):
    assert "Прогонов пока нет" in web.get("/runs").text
    web.post("/runs", data=FORM)
    page = web.get("/runs").text
    assert "№1" in page and "готов" in page
    assert 'name="ids"' in web.get("/compare").text


def test_compare_page_shows_differences(web):
    api_run(web)
    api_run(web, commission=0.002)
    page = web.get("/compare", params={"ids": "1,2"})
    assert page.status_code == 200
    assert "broker.commission" in page.text
    assert "0.002" in page.text


def test_compare_with_bad_ids(web):
    assert web.get("/compare", params={"ids": "abc"}).status_code == 400


def test_unknown_run_is_404(web):
    for url in ("/runs/99", "/runs/99/report", "/compare?ids=99"):
        assert web.get(url).status_code == 404, url
    response = web.get("/api/runs/99")
    assert response.status_code == 404
    assert "error" in response.json()


# ---------------------------------------------------------------------- API


def test_api_create_and_read(web):
    run_id = api_run(web)
    info = web.get(f"/api/runs/{run_id}").json()
    assert info["status"] == "done"
    assert info["ticker"] == "TEST"
    assert [r["strategy"] for r in info["results"]] == ["bh", "sma"]
    assert info["results"][0]["n_trades"] == 1
    # Пути сервера наружу не отдаются
    assert "output" not in info["config"]
    assert "cache_dir" not in info["config"]["data"]

    equity = web.get(f"/api/runs/{run_id}/equity").json()
    assert set(equity) == {"bh", "sma"}
    assert len(equity["bh"]["dates"]) == len(equity["bh"]["values"]) == 500


def test_api_lists_runs(web):
    api_run(web)
    runs = web.get("/api/runs").json()
    assert [r["id"] for r in runs] == [1]
    assert runs[0]["status"] == "done"
    assert runs[0]["strategies"] == "bh, sma"


def test_api_rejects_bad_requests(web):
    assert web.post("/api/runs", content=b"{not json").status_code == 400

    for raw, fragment in [
        ({**API_CONFIG, "output": {"dir": "/tmp"}}, "output"),
        ({**API_CONFIG, "data": {**API_CONFIG["data"], "cache_dir": "/etc"}}, "cache_dir"),
        ({**API_CONFIG, "broker": {"commission": 5}}, "commission"),
        ({"strategies": []}, "стратегия"),
    ]:
        response = web.post("/api/runs", json=raw)
        assert response.status_code == 422, raw
        assert any(fragment in e for e in response.json()["errors"])
    assert web.get("/api/runs").json() == []


def test_api_compare(web):
    api_run(web)
    api_run(web, commission=0.002)
    result = web.get("/api/compare", params={"ids": "1,2"}).json()
    assert {row["run_id"] for row in result["rows"]} == {1, 2}
    assert result["diff"]["broker.commission"] == {"1": 0.0005, "2": 0.002}
    assert web.get("/api/compare").status_code == 400


def test_docs_are_available(web):
    assert web.get("/api/docs").status_code == 200


# --------------------------------------------------------- фоновые прогоны


def test_running_job_has_status_and_no_results_yet(web_manual):
    client, executor = web_manual
    assert client.post("/runs", data=FORM).status_code == 303

    assert "Идёт прогон" in client.get("/runs/1").text
    assert client.get("/api/runs/1").json()["status"] == "running"
    assert client.get("/runs/1/report").status_code == 409
    assert client.get("/api/runs/1/equity").status_code == 409
    assert client.get("/compare", params={"ids": "1"}).status_code == 409

    executor.run_all()
    assert client.get("/api/runs/1").json()["status"] == "done"
    assert "Доходность по годам" in client.get("/runs/1").text
    assert client.get("/runs/1/report").status_code == 200


def test_failed_job_shows_error(web, monkeypatch):
    def no_data(_cfg):
        raise DataError("Для NOPE нет данных за этот период")

    monkeypatch.setattr(data, "_download", no_data)
    web.post("/runs", data={**FORM, "ticker": "NOPE"})

    info = web.get("/api/runs/1").json()
    assert info["status"] == "error"
    assert "нет данных" in info["error"]
    assert "нет данных" in web.get("/runs/1").text
    assert web.get("/runs/1/report").status_code == 409


def test_unexpected_error_is_recorded_with_its_type(web, monkeypatch):
    def broken(_cfg):
        raise RuntimeError("boom")

    monkeypatch.setattr(data, "_download", broken)
    web.post("/runs", data={**FORM, "ticker": "NOPE"})
    assert web.get("/api/runs/1").json()["error"] == "RuntimeError: boom"


def test_refresh_downloads_prices_again(web, prices, monkeypatch):
    calls = []

    def fake_download(_cfg):
        calls.append(1)
        return prices

    monkeypatch.setattr(data, "_download", fake_download)
    web.post("/runs", data=FORM)
    assert calls == []  # цены взяты из кэша
    web.post("/runs", data={**FORM, "refresh": "on"})
    assert calls == [1]
    assert web.get("/api/runs/2").json()["status"] == "done"


def test_interrupted_runs_are_marked_on_startup(project):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sp500_sim.config import load_config, parse_config
    from sp500_sim.web.app import create_app

    conn = storage.connect("results/results.db")
    run_id = storage.create_run(conn, parse_config(API_CONFIG).to_dict(), "TEST")
    conn.close()

    app = create_app(defaults=load_config("config.toml"))
    with TestClient(app) as client:
        info = client.get(f"/api/runs/{run_id}").json()
    assert info["status"] == "error"
    assert "прерван" in info["error"]


def test_real_background_thread(project):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sp500_sim.config import load_config
    from sp500_sim.web.app import create_app

    app = create_app(defaults=load_config("config.toml"))
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json=API_CONFIG).json()["id"]
        deadline = time.time() + 15
        status = "running"
        while status == "running" and time.time() < deadline:
            time.sleep(0.05)
            status = client.get(f"/api/runs/{run_id}").json()["status"]
    assert status == "done"


# ------------------------------------------------------------------ оформление


def test_pages_share_layout_with_theme_toggle_and_active_menu(web):
    pages = {"/": "Новый прогон", "/runs": "Прогоны", "/compare": "Сравнение"}
    for url, label in pages.items():
        page = web.get(url).text
        assert 'id="theme-toggle"' in page
        assert f'class="active">{label}</a>' in page, url
        assert page.count('class="active"') == 1, url


def test_compare_marks_the_better_result(web):
    api_run(web)
    api_run(web, commission=0.02)  # дорогая комиссия: результат хуже
    page = web.get("/compare", params={"ids": "1,2"}).text
    assert 'class="num best"' in page
    assert page.count('class="num best"') == 5  # итог, доходность, в год, просадка, Sharpe


def test_run_page_embeds_report_in_the_site_theme(web):
    web.post("/runs", data=FORM)
    page = web.get("/runs/1").text
    assert page.count('id="theme-toggle"') == 1  # переключатель один, не два
    assert page.count('<div class="kpi tile-') == 2
    assert "Повторить с этими настройками" in page


# ----------------------------------------------------------------------- цвета


def test_form_color_is_saved_and_used_in_report(web):
    web.post("/runs", data={**FORM, "strategy_color": ["#ff0000", "#00aa55"]})
    report = web.get("/runs/1/report").text
    assert "linear-gradient(135deg,#ff0000" in report
    assert "linear-gradient(135deg,#00aa55" in report


def test_form_without_colors_uses_palette(web):
    web.post("/runs", data=FORM)
    assert 'class="kpi tile-0"' in web.get("/runs/1/report").text


def test_form_offers_color_pickers_and_keeps_chosen_color(web):
    page = web.get("/").text
    # цвет портфеля, две строки стратегий и шаблон новой строки
    assert page.count('type="color"') == 4
    assert 'value="#7d6fe0"' in page  # цвет первой стратегии из палитры
    web.post("/runs", data={**FORM, "strategy_color": ["#123456", "#abcdef"]})
    page = web.get("/", params={"from_run": 1}).text
    assert 'value="#123456"' in page and 'value="#abcdef"' in page


def test_bad_color_in_form_shows_error(web):
    response = web.post("/runs", data={**FORM, "strategy_color": ["красный", "#abcdef"]})
    assert response.status_code == 422
    assert "#rrggbb" in response.text


def test_api_accepts_color(web):
    config = {
        **API_CONFIG,
        "strategies": [
            {**API_CONFIG["strategies"][0], "color": "#ff0000"},
            API_CONFIG["strategies"][1],
        ],
    }
    assert web.post("/api/runs", json=config).status_code == 202
    assert "linear-gradient(135deg,#ff0000" in web.get("/runs/1/report").text
    assert web.get("/api/runs/1").json()["config"]["strategies"][0]["color"] == "#ff0000"

    bad = {**API_CONFIG, "strategies": [{"type": "buy_and_hold", "color": "red"}]}
    response = web.post("/api/runs", json=bad)
    assert response.status_code == 422 and "#rrggbb" in response.json()["errors"][0]


# ------------------------------------------------------------------ портфели


PORTFOLIO_FORM = {
    "ticker": "TEST",
    "start": "2020-01-01",
    "end": "2022-01-01",
    "cash": "10000",
    "commission": "0.05",
    "slippage": "0.05",
    "min_change": "1",
    "strategy_type": ["buy_and_hold", "buy_and_hold"],
    "strategy_name": ["Мой портфель", "Только TEST"],
    "strategy_params": ["", ""],
    "strategy_portfolio": ["AAA 60, BBB 40", ""],
    "strategy_rebalance": ["yearly", "never"],
}


def test_form_builds_portfolio_run(web_portfolio):
    assert web_portfolio.post("/runs", data=PORTFOLIO_FORM).status_code == 303
    info = web_portfolio.get("/api/runs/1").json()
    assert info["status"] == "done"
    assert info["ticker"] == "AAA, BBB, TEST"
    assert info["config"]["strategies"][0]["portfolio"] == {"AAA": 60.0, "BBB": 40.0}
    page = web_portfolio.get("/runs/1").text
    assert "AAA 60% · BBB 40%" in page
    assert "AAA, BBB, TEST" in page


def test_form_shows_portfolio_error_in_row(web_portfolio):
    bad = {**PORTFOLIO_FORM, "strategy_portfolio": ["AAA 60, BBB 30", ""]}
    response = web_portfolio.post("/runs", data=bad)
    assert response.status_code == 422
    assert "100%, сейчас 90%" in response.text
    assert 'value="AAA 60, BBB 30"' in response.text  # введённое не пропало
    assert web_portfolio.get("/api/runs").json() == []


def test_equal_weights_when_none_given(web_portfolio):
    data = {**PORTFOLIO_FORM, "strategy_portfolio": ["AAA, BBB", ""]}
    web_portfolio.post("/runs", data=data)
    weights = web_portfolio.get("/api/runs/1").json()["config"]["strategies"][0]["portfolio"]
    assert weights == pytest.approx({"AAA": 50.0, "BBB": 50.0})


def test_form_prefills_portfolio_from_a_previous_run(web_portfolio):
    web_portfolio.post("/runs", data=PORTFOLIO_FORM)
    page = web_portfolio.get("/", params={"from_run": 1}).text
    assert 'value="AAA 60, BBB 40"' in page
    assert 'value="yearly" selected' in page


def test_form_has_portfolio_and_rebalance_fields(web_portfolio):
    page = web_portfolio.get("/").text
    assert 'name="strategy_portfolio"' in page
    assert "Ребаланс: раз в год" in page


def test_api_accepts_portfolio(web_portfolio):
    config = {
        "data": {"ticker": "TEST", "start": "2020-01-01", "end": "2022-01-01"},
        "strategies": [
            {
                "name": "p",
                "type": "buy_and_hold",
                "portfolio": {"AAA": 50, "BBB": 50},
                "rebalance": "monthly",
            }
        ],
    }
    assert web_portfolio.post("/api/runs", json=config).status_code == 202
    assert web_portfolio.get("/api/runs/1").json()["status"] == "done"

    bad = {**config, "strategies": [{**config["strategies"][0], "portfolio": {"AAA": 10}}]}
    response = web_portfolio.post("/api/runs", json=bad)
    assert response.status_code == 422 and "100%" in response.json()["errors"][0]


def test_api_limits_total_tickers(web_portfolio):
    strategies = [
        {"name": f"s{i}", "type": "buy_and_hold", "portfolio": {f"T{i}{j}": 12.5 for j in range(8)}}
        for i in range(6)
    ]
    response = web_portfolio.post("/api/runs", json={"strategies": strategies})
    assert response.status_code == 422


# --------------------------------------------------------------- мой портфель

HOLDINGS_FORM = {
    **FORM,
    "holding_ticker": ["AAA", "BBB", ""],
    "holding_qty": ["10", "20", ""],
    "holding_price": ["100", "50", ""],
    "portfolio_color": "#ff8800",
}


def test_form_has_holding_fields(web):
    page = web.get("/").text
    assert 'name="holding_ticker"' in page and 'name="holding_qty"' in page
    assert 'name="holding_price"' in page and "Мой портфель" in page
    assert "Стоимость позиции" in page and "средняя цена" in page.lower()


def test_form_builds_my_portfolio_run(web_holdings):
    assert web_holdings.post("/runs", data=HOLDINGS_FORM).status_code == 303
    info = web_holdings.get("/api/runs/1").json()
    assert info["status"] == "done"
    assert [r["strategy"] for r in info["results"]][0] == "Мой портфель"
    assert info["ticker"] == "AAA, BBB, TEST"
    assert info["config"]["my_portfolio"]["color"] == "#ff8800"
    page = web_holdings.get("/runs/1").text
    assert "Мои позиции" in page and "к вложенному" in page
    assert "linear-gradient(135deg,#ff8800" in page  # свой цвет портфеля


def test_empty_holding_rows_do_not_create_a_portfolio(web_holdings):
    data = {**HOLDINGS_FORM, "holding_ticker": [""], "holding_qty": [""], "holding_price": [""]}
    web_holdings.post("/runs", data=data)
    assert web_holdings.get("/api/runs/1").json()["config"]["my_portfolio"] is None
    assert "Мои позиции" not in web_holdings.get("/runs/1").text


def test_holding_errors_are_shown_and_input_is_kept(web_holdings):
    data = {**HOLDINGS_FORM, "holding_qty": ["abc", "20", ""]}
    response = web_holdings.post("/runs", data=data)
    assert response.status_code == 422
    assert "количество: нужно число" in response.text
    assert 'value="abc"' in response.text and 'value="100"' in response.text
    assert web_holdings.get("/api/runs").json() == []


def test_only_my_portfolio_without_strategies(web_holdings):
    data = {k: v for k, v in HOLDINGS_FORM.items() if not k.startswith("strategy_")}
    assert web_holdings.post("/runs", data=data).status_code == 303
    info = web_holdings.get("/api/runs/1").json()
    assert [r["strategy"] for r in info["results"]] == ["Мой портфель"]
    assert info["ticker"] == "AAA, BBB"


def test_form_with_nothing_is_an_error(web_holdings):
    data = {k: v for k, v in FORM.items() if not k.startswith("strategy_")}
    response = web_holdings.post("/runs", data=data)
    assert response.status_code == 422
    assert "хотя бы одну стратегию или позицию портфеля" in response.text


def test_form_is_prefilled_with_positions_from_a_run(web_holdings):
    web_holdings.post("/runs", data=HOLDINGS_FORM)
    page = web_holdings.get("/", params={"from_run": 1}).text
    assert 'value="AAA"' in page and 'value="100"' in page and 'value="20"' in page
    assert 'value="#ff8800"' in page


def test_api_accepts_my_portfolio(web_holdings, stocks):
    config = {
        "data": {"ticker": "TEST", "start": "2020-01-01", "end": "2022-01-01"},
        "my_portfolio": {
            "name": "Брокерский счёт",
            "positions": [
                {"ticker": "AAA", "qty": 10, "avg_price": 100},
                {"ticker": "BBB", "qty": 20, "avg_price": 50},
            ],
        },
    }
    assert web_holdings.post("/api/runs", json=config).status_code == 202
    info = web_holdings.get("/api/runs/1").json()
    assert [r["strategy"] for r in info["results"]] == ["Брокерский счёт"]
    equity = web_holdings.get("/api/runs/1/equity").json()["Брокерский счёт"]
    expected = 10 * stocks["AAA"]["Close"].iloc[-1] + 20 * stocks["BBB"]["Close"].iloc[-1]
    assert equity["values"][-1] == pytest.approx(expected)  # количество на цену

    bad = {**config, "my_portfolio": {"positions": [{"ticker": "AAA", "qty": 0, "avg_price": 5}]}}
    response = web_holdings.post("/api/runs", json=bad)
    assert response.status_code == 422 and "количество" in response.json()["errors"][0]
