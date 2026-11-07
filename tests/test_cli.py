from sp500_sim.cli import main


def test_run_saves_run_and_writes_report(project, capsys):
    assert main(["run", "config.toml"]) == 0
    out = capsys.readouterr().out
    assert "Купить и держать" in out
    assert "Прогон сохранён: №1" in out
    assert (project / "results" / "results.db").exists()
    assert "<svg" in (project / "results" / "run_1.html").read_text(encoding="utf-8")


def test_run_without_saving(project):
    assert main(["run", "config.toml", "--no-save"]) == 0
    assert not (project / "results" / "results.db").exists()
    assert (project / "results" / "run_unsaved.html").exists()


def test_run_without_report(project):
    assert main(["run", "config.toml", "--no-report"]) == 0
    assert not list((project / "results").glob("*.html"))


def test_runs_lists_saved_runs(project, capsys):
    main(["run", "config.toml", "--no-report"])
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    assert main(["runs"]) == 0
    out = capsys.readouterr().out
    assert "Купить и держать, Тренд SMA-50" in out
    assert out.count("TEST") == 2


def test_compare_identical_runs(project, capsys):
    main(["run", "config.toml", "--no-report"])
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    assert main(["compare", "1", "2"]) == 0
    assert "совпадают" in capsys.readouterr().out


def test_compare_shows_changed_setting(project, write_config, capsys):
    main(["run", "config.toml", "--no-report"])
    write_config(commission=0.002)
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    assert main(["compare", "1", "2"]) == 0
    out = capsys.readouterr().out
    assert "broker.commission" in out
    assert "№1=0.0005" in out and "№2=0.002" in out


def test_report_command_recreates_report(project, capsys):
    main(["run", "config.toml", "--no-report"])
    capsys.readouterr()
    target = project / "again.html"
    assert main(["report", "1", "-o", str(target)]) == 0
    assert "Тренд SMA-50" in target.read_text(encoding="utf-8")


def test_stored_report_matches_fresh_report(project):
    main(["run", "config.toml"])
    fresh = (project / "results" / "run_1.html").read_text(encoding="utf-8")
    main(["report", "1", "-o", str(project / "again.html")])
    again = (project / "again.html").read_text(encoding="utf-8")
    # Таблицы с итогами и сделками одинаковы, как и при первом показе
    for marker in ("<h2>Итоги</h2>", "<h2>Сделки</h2>"):
        assert fresh.split(marker)[1].split("<h2>")[0] == again.split(marker)[1].split("<h2>")[0]


def test_errors_return_exit_code_1(project, capsys):
    assert main(["run", "нет.toml"]) == 1
    assert "не найден" in capsys.readouterr().err
    assert main(["compare", "99"]) == 1


def test_runs_without_database(project, capsys):
    assert main(["runs"]) == 1
    assert "не найдена" in capsys.readouterr().err
