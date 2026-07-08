from datetime import UTC, datetime, timedelta
from email.message import Message
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from planquest import fetch as fetch_module
from planquest.fetch import CacheMiss, PoliteFetcher, parse_utc_datetime, retry_after_seconds


def http_error_with_retry_after(value: str) -> HTTPError:
    headers = Message()
    headers["Retry-After"] = value
    return HTTPError("https://example.invalid", 429, "Too Many Requests", headers, None)


def test_retry_after_seconds_accepts_delta_seconds() -> None:
    assert retry_after_seconds(http_error_with_retry_after("12")) == 12.0


def test_retry_after_seconds_rejects_invalid_value() -> None:
    assert retry_after_seconds(http_error_with_retry_after("not a date")) is None


def test_parse_utc_datetime_accepts_z_suffix() -> None:
    parsed = parse_utc_datetime("2026-07-08T03:46:15Z")

    assert parsed == datetime(2026, 7, 8, 3, 46, 15, tzinfo=UTC)


def test_offline_fetch_reads_cached_page(tmp_path: Path) -> None:
    fetcher = PoliteFetcher(tmp_path, "test-agent", offline=True)
    fetcher.can_fetch = lambda _url: True  # type: ignore[method-assign]
    url = "https://example.invalid/thread/reader/"
    path = fetcher._cache_path(url)
    path.parent.mkdir(parents=True)
    path.write_text("cached body", encoding="utf-8")

    fetched = fetcher.fetch_text(url)

    assert fetched.from_cache is True
    assert fetched.text == "cached body"


def test_offline_fetch_rejects_uncached_page(tmp_path: Path) -> None:
    fetcher = PoliteFetcher(tmp_path, "test-agent", offline=True)
    fetcher.can_fetch = lambda _url: True  # type: ignore[method-assign]

    with pytest.raises(CacheMiss):
        fetcher.fetch_text("https://example.invalid/thread/reader/")


def test_can_fetch_accepts_user_agent_override(tmp_path: Path) -> None:
    robots = tmp_path / "robots" / "example.invalid.txt"
    robots.parent.mkdir(parents=True)
    robots.write_text(
        "\n".join(
            [
                "User-agent: GPTBot",
                "Disallow: /",
                "",
                "User-agent: *",
                "Disallow: /login/",
            ]
        ),
        encoding="utf-8",
    )
    fetcher = PoliteFetcher(tmp_path, "ordinary-agent", offline=True)
    url = "https://example.invalid/thread/reader/"

    assert fetcher.can_fetch(url) is True
    assert fetcher.can_fetch(url, user_agent="GPTBot") is False


def test_network_fetch_writes_receipt_log(tmp_path: Path) -> None:
    fetcher = PoliteFetcher(tmp_path, "test-agent", delay_seconds=12.0, retries=3, retry_delay_seconds=45.0)
    fetcher.can_fetch = lambda _url: True  # type: ignore[method-assign]
    fetcher._http_get_with_retries = lambda _url: "network body"  # type: ignore[method-assign]

    fetched = fetcher.fetch_text("https://example.invalid/thread/reader/")

    receipts = [json.loads(line) for line in fetcher.receipt_log_path().read_text(encoding="utf-8").splitlines()]
    assert fetched.from_cache is False
    assert len(receipts) == 1
    assert receipts[0]["kind"] == "page"
    assert receipts[0]["url"] == "https://example.invalid/thread/reader/"
    assert receipts[0]["user_agent"] == "test-agent"
    assert receipts[0]["delay_seconds"] == 12.0
    assert receipts[0]["retries"] == 3


def test_cached_fetch_does_not_write_receipt_log(tmp_path: Path) -> None:
    fetcher = PoliteFetcher(tmp_path, "test-agent")
    fetcher.can_fetch = lambda _url: True  # type: ignore[method-assign]
    url = "https://example.invalid/thread/reader/"
    path = fetcher._cache_path(url)
    path.parent.mkdir(parents=True)
    path.write_text("cached body", encoding="utf-8")

    fetcher.fetch_text(url)

    assert not fetcher.receipt_log_path().exists()


def test_network_fetch_waits_for_previous_receipt_across_processes(tmp_path: Path, monkeypatch) -> None:
    url = "https://example.invalid/thread/reader/"
    previous_url = "https://example.invalid/thread/reader/page-1"
    receipt = {
        "fetched_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "kind": "page",
        "url": previous_url,
        "cache_path": "cache.html",
        "bytes": 123,
    }
    receipt_log = tmp_path / "fetch-log.jsonl"
    receipt_log.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    slept: list[float] = []
    monkeypatch.setattr(fetch_module.time, "sleep", slept.append)

    fetcher = PoliteFetcher(tmp_path, "test-agent", delay_seconds=60.0)
    fetcher.can_fetch = lambda _url: True  # type: ignore[method-assign]
    fetcher._http_get_with_retries = lambda _url: "network body"  # type: ignore[method-assign]

    fetcher.fetch_text(url)

    assert slept
    assert 0 < slept[0] <= 60.0


def test_network_fetch_ignores_old_receipts_for_delay(tmp_path: Path, monkeypatch) -> None:
    receipt = {
        "fetched_at_utc": (datetime.now(UTC) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
        "kind": "page",
        "url": "https://example.invalid/thread/reader/page-1",
        "cache_path": "cache.html",
        "bytes": 123,
    }
    receipt_log = tmp_path / "fetch-log.jsonl"
    receipt_log.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    slept: list[float] = []
    monkeypatch.setattr(fetch_module.time, "sleep", slept.append)

    fetcher = PoliteFetcher(tmp_path, "test-agent", delay_seconds=60.0)
    fetcher.can_fetch = lambda _url: True  # type: ignore[method-assign]
    fetcher._http_get_with_retries = lambda _url: "network body"  # type: ignore[method-assign]

    fetcher.fetch_text("https://example.invalid/thread/reader/")

    assert slept == []
