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
