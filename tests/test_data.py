import pandas as pd
import pytest

from sp500_sim import data
from sp500_sim.config import DataConfig
from sp500_sim.data import DataError, cache_file, fingerprint, load_prices, validate_prices


def test_valid_prices_pass(prices):
    validate_prices(prices)


def test_missing_column(prices):
    with pytest.raises(DataError, match="колонок"):
        validate_prices(prices.drop(columns=["Open"]))


def test_nan_prices(prices):
    prices.iloc[3, prices.columns.get_loc("Close")] = float("nan")
    with pytest.raises(DataError, match="пропуски"):
        validate_prices(prices)


def test_non_positive_prices(prices):
    prices.iloc[3, prices.columns.get_loc("Open")] = 0
    with pytest.raises(DataError, match="нули"):
        validate_prices(prices)


def test_unsorted_dates(prices):
    with pytest.raises(DataError, match="по возрастанию"):
        validate_prices(prices.iloc[::-1])


def test_duplicate_dates(prices):
    with pytest.raises(DataError, match="повторов"):
        validate_prices(pd.concat([prices.iloc[:5], prices.iloc[:5]]))


def test_too_short(prices):
    with pytest.raises(DataError, match="хотя бы 2"):
        validate_prices(prices.iloc[:1])


def test_cache_name_depends_on_ticker_and_period():
    a = cache_file(DataConfig(ticker="^GSPC", start="2000-01-01"))
    b = cache_file(DataConfig(ticker="^GSPC", start="2010-01-01"))
    assert a != b
    assert "^" not in a.name


def test_cache_is_used_without_download(tmp_path, prices, monkeypatch):
    cfg = DataConfig(ticker="TEST", start="2020-01-01", cache_dir=str(tmp_path))
    prices.to_csv(cache_file(cfg), index_label="Date")

    def no_download(_cfg):
        raise AssertionError("не должно скачивать, когда есть кэш")

    monkeypatch.setattr(data, "_download", no_download)
    loaded = load_prices(cfg)
    assert loaded.df.shape == prices.shape
    assert fingerprint(loaded.df) == fingerprint(prices)


def test_download_is_saved_to_cache(tmp_path, prices, monkeypatch):
    cfg = DataConfig(ticker="TEST", start="2020-01-01", cache_dir=str(tmp_path / "data"))
    monkeypatch.setattr(data, "_download", lambda _cfg: prices)
    load_prices(cfg)
    assert cache_file(cfg).exists()


def test_refresh_downloads_again(tmp_path, prices, monkeypatch):
    cfg = DataConfig(ticker="TEST", start="2020-01-01", cache_dir=str(tmp_path))
    prices.to_csv(cache_file(cfg), index_label="Date")
    calls = []

    def fake_download(_cfg):
        calls.append(1)
        return prices

    monkeypatch.setattr(data, "_download", fake_download)
    load_prices(cfg, refresh=True)
    assert calls == [1]


def test_fingerprint_changes_with_data(prices):
    changed = prices.copy()
    changed.iloc[0, 0] += 0.01
    assert fingerprint(prices)["hash"] != fingerprint(changed)["hash"]


# ------------------------------------------------------- несколько бумаг


def test_align_keeps_only_common_days(stocks):
    from sp500_sim.data import align

    aligned = align(stocks)
    assert len({len(df) for df in aligned.values()}) == 1
    assert aligned["AAA"].index[0] == stocks["CCC"].index[0]  # начало по самой молодой бумаге
    assert aligned["AAA"].index.equals(aligned["CCC"].index)


def test_align_single_asset_is_untouched(stocks):
    from sp500_sim.data import align

    frames = {"AAA": stocks["AAA"]}
    assert align(frames) is frames


def test_align_without_common_days_is_an_error(stocks):
    from sp500_sim.data import align

    early = stocks["AAA"].iloc[:100]
    late = stocks["BBB"].iloc[200:]
    with pytest.raises(DataError, match="общих торговых дней"):
        align({"AAA": early, "BBB": late})


def test_fingerprint_many_depends_on_every_asset(stocks):
    from sp500_sim.data import fingerprint_many

    base = fingerprint_many({"AAA": stocks["AAA"], "BBB": stocks["BBB"]})
    changed = stocks["BBB"].copy()
    changed.iloc[0, 0] += 0.01
    assert fingerprint_many({"AAA": stocks["AAA"], "BBB": changed})["hash"] != base["hash"]
    assert base["rows"] == len(stocks["AAA"])


def test_load_many_reads_each_ticker_from_its_own_cache(tmp_path, stocks):
    from sp500_sim.data import load_many

    cfg = DataConfig(ticker="IGNORED", start="2020-01-01", cache_dir=str(tmp_path))
    for ticker in ("AAA", "BBB"):
        stocks[ticker].to_csv(cache_file(cfg, ticker), index_label="Date")
    loaded = load_many(cfg, ["AAA", "BBB"])
    assert set(loaded) == {"AAA", "BBB"}
    assert loaded["AAA"].df.shape == stocks["AAA"].shape
