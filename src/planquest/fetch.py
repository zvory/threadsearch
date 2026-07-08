from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import time
from pathlib import Path
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


class RobotsDenied(RuntimeError):
    """Raised when robots.txt disallows a URL for the configured user agent."""


class FetchError(RuntimeError):
    """Raised when a network request fails."""


class CacheMiss(RuntimeError):
    """Raised when offline mode needs a page that is not cached."""


@dataclass
class CachedFetch:
    url: str
    text: str
    from_cache: bool
    path: Path | None


class PoliteFetcher:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        delay_seconds: float = 8.0,
        timeout_seconds: float = 60.0,
        refresh: bool = False,
        retries: int = 2,
        retry_delay_seconds: float = 30.0,
        offline: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.refresh = refresh
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.offline = offline
        self._robots: Dict[str, RobotFileParser] = {}
        self._last_request_at: Dict[str, float] = {}

    def fetch_text(self, url: str) -> CachedFetch:
        self._assert_allowed(url)
        path = self._cache_path(url)
        if path.exists() and not self.refresh:
            return CachedFetch(url=url, text=path.read_text(encoding="utf-8"), from_cache=True, path=path)
        if self.offline:
            raise CacheMiss(f"cached page not found: {url}")

        self._wait(url)
        text = self._http_get_with_retries(url)
        self._write_atomic(path, text)
        self._write_atomic(path.with_suffix(".url"), url)
        self._last_request_at[self._origin(url)] = time.monotonic()
        self._append_fetch_receipt(url, path, kind="page", bytes_written=len(text.encode("utf-8")))
        return CachedFetch(url=url, text=text, from_cache=False, path=path)

    def is_cached(self, url: str) -> bool:
        return self._cache_path(url).exists()

    def can_fetch(self, url: str, user_agent: str | None = None) -> bool:
        parser = self._robots_for(url)
        return parser.can_fetch(user_agent or self.user_agent, url)

    def robots_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))

    def _assert_allowed(self, url: str) -> None:
        if not self.can_fetch(url):
            raise RobotsDenied(f"robots.txt disallows {url!r} for user agent {self.user_agent!r}")

    def _robots_for(self, url: str) -> RobotFileParser:
        origin = self._origin(url)
        if origin in self._robots:
            return self._robots[origin]

        robots_url = self.robots_url(url)
        robots_cache = self.cache_dir / "robots" / f"{urlparse(url).netloc}.txt"
        if robots_cache.exists() and not self.refresh:
            lines = robots_cache.read_text(encoding="utf-8").splitlines()
        else:
            if self.offline:
                raise CacheMiss(f"cached robots.txt not found for {urlparse(url).netloc}")
            self._wait(robots_url)
            robots_text = self._http_get_with_retries(robots_url)
            self._write_atomic(robots_cache, robots_text)
            self._last_request_at[origin] = time.monotonic()
            self._append_fetch_receipt(
                robots_url,
                robots_cache,
                kind="robots",
                bytes_written=len(robots_text.encode("utf-8")),
            )
            lines = robots_text.splitlines()

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(lines)
        self._robots[origin] = parser
        return parser

    def _http_get_with_retries(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._http_get(url)
            except HTTPError as exc:
                if not self._is_retryable_http(exc) or attempt >= self.retries:
                    raise FetchError(f"HTTP {exc.code} while fetching {url}") from exc
                last_error = exc
                self._sleep_before_retry(exc, attempt)
            except URLError as exc:
                if attempt >= self.retries:
                    raise FetchError(f"Network error while fetching {url}: {exc}") from exc
                last_error = exc
                self._sleep_before_retry(None, attempt)
        raise FetchError(f"Network error while fetching {url}: {last_error}")

    def _http_get(self, url: str) -> str:
        req = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
            },
        )
        with urlopen(req, timeout=self.timeout_seconds) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, "replace")

    def _sleep_before_retry(self, exc: HTTPError | None, attempt: int) -> None:
        retry_after = retry_after_seconds(exc) if exc is not None else None
        delay = retry_after if retry_after is not None else self.retry_delay_seconds * (attempt + 1)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _is_retryable_http(exc: HTTPError) -> bool:
        return exc.code == 429 or 500 <= exc.code <= 599

    def _wait(self, url: str) -> None:
        origin = self._origin(url)
        waits: list[float] = []
        last = self._last_request_at.get(origin)
        if last is not None:
            waits.append(self.delay_seconds - (time.monotonic() - last))
        persisted_last = self._last_receipt_at(origin)
        if persisted_last is not None:
            elapsed = max(0.0, (datetime.now(UTC) - persisted_last).total_seconds())
            waits.append(self.delay_seconds - elapsed)
        if not waits:
            return
        remaining = max(waits)
        if remaining > 0:
            time.sleep(remaining)

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / "pages" / f"{digest}.html"

    def receipt_log_path(self) -> Path:
        return self.cache_dir / "fetch-log.jsonl"

    def _last_receipt_at(self, origin: str) -> datetime | None:
        log_path = self.receipt_log_path()
        if not log_path.exists():
            return None

        latest: datetime | None = None
        with log_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    receipt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = receipt.get("url")
                fetched_at = receipt.get("fetched_at_utc")
                if not isinstance(url, str) or not isinstance(fetched_at, str):
                    continue
                if self._origin(url) != origin:
                    continue
                timestamp = parse_utc_datetime(fetched_at)
                if timestamp is None:
                    continue
                if latest is None or timestamp > latest:
                    latest = timestamp
        return latest

    def _append_fetch_receipt(self, url: str, path: Path, kind: str, bytes_written: int) -> None:
        receipt = {
            "fetched_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "kind": kind,
            "url": url,
            "cache_path": str(path),
            "bytes": bytes_written,
            "user_agent": self.user_agent,
            "delay_seconds": self.delay_seconds,
            "retries": self.retries,
            "retry_delay_seconds": self.retry_delay_seconds,
        }
        log_path = self.receipt_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, sort_keys=True))
            handle.write("\n")

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)


def retry_after_seconds(exc: HTTPError) -> float | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, retry_at.timestamp() - time.time())


def parse_utc_datetime(value: str) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)
