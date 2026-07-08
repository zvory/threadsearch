from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from .deploy_policy import public_contact_errors


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: str
    json_payload: Any = None


@dataclass(frozen=True)
class SmokeItem:
    key: str
    status: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SmokeReport:
    ok: bool
    base_url: str
    items: list[SmokeItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "base_url": self.base_url,
            "items": [asdict(item) for item in self.items],
        }


ClaimPair = tuple[str, str]
PRIVATE_DOWNLOAD_PATHS = (
    "/thread-search.sqlite",
    "/data/thread-search.sqlite",
    "/dist/thread-search-public/thread-search.sqlite",
    "/thread-search-threadmarks.jsonl",
    "/data/thread-search-threadmarks.jsonl",
    "/data/raw/fetch-log.jsonl",
    "/manifest.json",
    "/README.deploy.txt",
)


def run_public_smoke(
    base_url: str,
    probes: tuple[str, ...],
    timeout: float = 5.0,
    claim_pairs: tuple[ClaimPair, ...] = (),
    require_artifact_manifest: bool = False,
) -> SmokeReport:
    normalized_base = normalize_base_url(base_url)
    items: list[SmokeItem] = [
        html_shell_item(normalized_base, timeout=timeout),
        robots_item(normalized_base, timeout=timeout),
        health_item(normalized_base, timeout=timeout),
        stats_item(normalized_base, timeout=timeout, require_artifact_manifest=require_artifact_manifest),
    ]
    search_items, first_post_id = probe_items(normalized_base, probes=probes, timeout=timeout)
    items.extend(search_items)
    items.extend(claim_pair_items(normalized_base, claim_pairs=claim_pairs, timeout=timeout))
    items.append(private_threadmark_item(normalized_base, first_post_id=first_post_id, timeout=timeout))
    items.append(private_downloads_item(normalized_base, timeout=timeout))
    return SmokeReport(
        ok=all(item.status == "pass" for item in items),
        base_url=normalized_base,
        items=items,
    )


def normalize_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"base URL must include scheme and host: {base_url!r}")
    return base_url.rstrip("/") + "/"


def html_shell_item(base_url: str, timeout: float) -> SmokeItem:
    response = fetch_text(base_url, "/", timeout=timeout)
    csp = response.headers.get("content-security-policy", "")
    robots = response.headers.get("x-robots-tag", "")
    meta_robots = '<meta name="robots" content="noindex, nofollow">' in response.body
    simple_search_controls = (
        'id="query"' in response.body
        and 'id="from-order"' in response.body
        and 'id="to-order"' in response.body
        and 'id="all-words"' in response.body
        and "function renderThreadmarkGroup" in response.body
    )
    removed_search_slop = not any(
        token in response.body
        for token in (
            'id="prefix-variants"',
            'id="topic-sort"',
            "Topic aliases",
            "Terms JSON",
            "Explain JSON",
            "Recap JSON",
            "Dossier JSON",
            "All matching threadmarks",
        )
    )
    ok = (
        response.status == 200
        and "noindex" in robots.lower()
        and "nofollow" in robots.lower()
        and "frame-ancestors 'none'" in csp
        and "'unsafe-inline'" not in csp
        and meta_robots
        and simple_search_controls
        and removed_search_slop
    )
    return SmokeItem(
        key="html_shell",
        status="pass" if ok else "fail",
        summary="HTML shell has public noindex, CSP, and the simplified search controls."
        if ok
        else "HTML shell is missing public safety controls or still exposes removed search UI.",
        evidence={
            "status": response.status,
            "x_robots_tag": robots,
            "content_security_policy": csp,
            "meta_robots": meta_robots,
            "simple_search_controls": simple_search_controls,
            "removed_search_slop": removed_search_slop,
        },
    )


def robots_item(base_url: str, timeout: float) -> SmokeItem:
    response = fetch_text(base_url, "/robots.txt", timeout=timeout)
    ok = response.status == 200 and response.body.strip() == "User-agent: *\nDisallow: /"
    return SmokeItem(
        key="robots_txt",
        status="pass" if ok else "fail",
        summary="robots.txt disallows public crawling." if ok else "robots.txt does not disallow public crawling.",
        evidence={"status": response.status, "body": response.body},
    )


def health_item(base_url: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/healthz", timeout=timeout)
    ok = response.status == 200 and isinstance(response.json_payload, dict) and response.json_payload.get("ok") is True
    return SmokeItem(
        key="healthz",
        status="pass" if ok else "fail",
        summary="Health endpoint reports a ready index." if ok else "Health endpoint does not report a ready index.",
        evidence={"status": response.status, "payload": response.json_payload},
    )


def stats_item(base_url: str, timeout: float, require_artifact_manifest: bool = False) -> SmokeItem:
    response = fetch_json(base_url, "/api/stats", timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    contact_errors = public_contact_errors(
        str(payload.get("public_contact") or ""),
        str(payload.get("removal_request_url") or ""),
    )
    artifact_manifest_validated = payload.get("artifact_manifest_validated") is True
    artifact_manifest_sha256 = str(payload.get("artifact_manifest_sha256") or "")
    artifact_database_sha256 = str(payload.get("artifact_database_sha256") or "")
    fingerprint_ok = bool(artifact_manifest_sha256 and artifact_database_sha256) or not require_artifact_manifest
    manifest_ok = (artifact_manifest_validated and fingerprint_ok) or not require_artifact_manifest
    ok = (
        response.status == 200
        and payload.get("ok") is True
        and payload.get("public_access_mode") == "source_linked_search"
        and payload.get("private_fulltext") is False
        and payload.get("chunk_results_enabled") is False
        and bool(payload.get("source_reader_url"))
        and not contact_errors
        and manifest_ok
    )
    return SmokeItem(
        key="stats_public_contract",
        status="pass" if ok else "fail",
        summary="Stats endpoint reports public source-linked search mode."
        if ok
        else "Stats endpoint does not match public source-linked search/contact/manifest mode.",
        evidence={
            "status": response.status,
            "ok": payload.get("ok"),
            "public_access_mode": payload.get("public_access_mode"),
            "private_fulltext": payload.get("private_fulltext"),
            "chunk_results_enabled": payload.get("chunk_results_enabled"),
            "source_reader_url": payload.get("source_reader_url"),
            "public_contact": payload.get("public_contact"),
            "removal_request_url": payload.get("removal_request_url"),
            "contact_errors": contact_errors,
            "artifact_manifest_validated": payload.get("artifact_manifest_validated"),
            "artifact_manifest_sha256": payload.get("artifact_manifest_sha256"),
            "artifact_database_sha256": payload.get("artifact_database_sha256"),
            "artifact_created_at_utc": payload.get("artifact_created_at_utc"),
            "require_artifact_manifest": require_artifact_manifest,
        },
    )


def probe_items(base_url: str, probes: tuple[str, ...], timeout: float) -> tuple[list[SmokeItem], str | None]:
    items: list[SmokeItem] = []
    first_post_id: str | None = None
    for probe in probes:
        search = probe_search_item(base_url, probe, timeout=timeout)
        items.append(search)
        if first_post_id is None:
            first_post_id = search.evidence.get("first_post_id") or None
    return items, first_post_id


def probe_search_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/search", {"q": probe, "limit": "3"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    threadmarks = payload.get("threadmarks") if isinstance(payload.get("threadmarks"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    first = results[0] if results and isinstance(results[0], dict) else {}
    total_threadmarks = int(payload.get("total_threadmarks") or 0)
    ok = (
        response.status == 200
        and bool(results)
        and bool(threadmarks)
        and total_threadmarks > 0
        and int(payload.get("total_chunks") or 0) > 0
        and payload.get("word_variants") is True
        and not forbidden
        and bool(first.get("source_url"))
    )
    return SmokeItem(
        key=f"search_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Probe search for {probe!r} returns grouped source-linked hits."
        if ok
        else f"Probe search for {probe!r} is not public-ready.",
        evidence={
            "status": response.status,
            "result_count": len(results),
            "threadmark_count": len(threadmarks),
            "total_threadmarks": payload.get("total_threadmarks"),
            "total_chunks": payload.get("total_chunks"),
            "word_variants": payload.get("word_variants"),
            "match_kind": payload.get("match_kind"),
            "forbidden_keys": forbidden[:8],
            "first_post_id": first.get("post_id"),
            "first_source_url": first.get("source_url"),
        },
    )



def claim_pair_items(base_url: str, claim_pairs: tuple[ClaimPair, ...], timeout: float) -> list[SmokeItem]:
    return []

def private_threadmark_item(base_url: str, first_post_id: str | None, timeout: float) -> SmokeItem:
    post_id = first_post_id or "0"
    response = fetch_text(base_url, f"/api/threadmark/{post_id}", timeout=timeout)
    ok = response.status == 404
    return SmokeItem(
        key="private_threadmark_route",
        status="pass" if ok else "fail",
        summary="Private full-text threadmark API is not publicly available."
        if ok
        else "Private full-text threadmark API is publicly available.",
        evidence={"status": response.status, "post_id": post_id},
    )


def private_downloads_item(base_url: str, timeout: float) -> SmokeItem:
    statuses: dict[str, int] = {}
    exposed: list[str] = []
    for path in PRIVATE_DOWNLOAD_PATHS:
        response = fetch_text(base_url, path, timeout=timeout)
        statuses[path] = response.status
        if response.status == 200:
            exposed.append(path)
    ok = not exposed
    return SmokeItem(
        key="private_download_paths",
        status="pass" if ok else "fail",
        summary="Private corpus and artifact files are not publicly downloadable."
        if ok
        else "Private corpus or artifact files are publicly downloadable.",
        evidence={
            "checked_paths": list(PRIVATE_DOWNLOAD_PATHS),
            "statuses": statuses,
            "exposed_paths": exposed,
        },
    )


def fetch_json(
    base_url: str,
    path: str,
    params: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> HttpResponse:
    response = fetch_text(base_url, path, params=params, timeout=timeout)
    try:
        payload = json.loads(response.body) if response.body else None
    except json.JSONDecodeError:
        payload = None
    return HttpResponse(
        status=response.status,
        headers=response.headers,
        body=response.body,
        json_payload=payload,
    )


def fetch_text(
    base_url: str,
    path: str,
    params: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> HttpResponse:
    query = f"?{urlencode(params)}" if params else ""
    url = urljoin(base_url, path.lstrip("/")) + query
    request = Request(url, headers={"User-Agent": "thread-search-public-smoke/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResponse(
                status=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=body,
            )
    except HTTPError as exc:
        return HttpResponse(
            status=exc.code,
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=exc.read().decode("utf-8", errors="replace"),
        )
    except URLError as exc:
        return HttpResponse(status=0, headers={}, body=str(exc.reason))


def forbidden_key_paths(value: Any, forbidden_keys: set[str], path: str = "$") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if key in forbidden_keys:
                matches.append(item_path)
            matches.extend(forbidden_key_paths(item, forbidden_keys, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            matches.extend(forbidden_key_paths(item, forbidden_keys, f"{path}[{index}]"))
    return matches
