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
