from contextlib import closing

import pytest

from sp500_sim import data, service, storage
from sp500_sim.config import load_config
from sp500_sim.data import DataError


def test_run_and_save(project):
    outcome = service.run_and_save(load_config("config.toml"), "results/results.db")
    assert outcome.run_id == 1
    assert [r.name for r in outcome.results] == ["Купить и держать", "Тренд SMA-50"]
    with closing(storage.connect("results/results.db")) as conn:
        assert storage.load_meta(conn, 1)[0]["status"] == "done"


def test_run_without_saving_creates_no_database(project):
    outcome = service.run_and_save(load_config("config.toml"), "results/results.db", save=False)
    assert outcome.run_id is None and outcome.results
    assert not (project / "results").exists()


def test_run_with_prepared_record(project):
    config = load_config("config.toml")
    run_id = service.start_run(config, "results/results.db")
    with closing(storage.connect("results/results.db")) as conn:
        assert storage.load_meta(conn, run_id)[0]["status"] == "running"

    service.run_in_background(config, "results/results.db", run_id)
    with closing(storage.connect("results/results.db")) as conn:
        assert storage.load_meta(conn, run_id)[0]["status"] == "done"
        assert len(storage.load_results(conn, run_id)) == 2


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (DataError("Для TEST нет данных"), "Для TEST нет данных"),
        (RuntimeError("boom"), "RuntimeError: boom"),
    ],
)
def test_background_error_goes_to_status(project, monkeypatch, error, expected):
    def fail(_cfg):
        raise error

    monkeypatch.setattr(data, "load_prices", lambda *_a, **_k: fail(None))
    # runner импортировал load_prices по имени, поэтому подменяем и там
    from sp500_sim import runner

    monkeypatch.setattr(runner, "load_prices", lambda *_a, **_k: fail(None))

    config = load_config("config.toml")
    run_id = service.start_run(config, "results/results.db")
    service.run_in_background(config, "results/results.db", run_id)
    with closing(storage.connect("results/results.db")) as conn:
        meta, _ = storage.load_meta(conn, run_id)
    assert (meta["status"], meta["error"]) == ("error", expected)
