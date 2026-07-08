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
    if len(probes) >= 2:
        items.append(compare_item(normalized_base, probes=probes[:2], timeout=timeout))
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
    share_link = 'id="share-link"' in response.body and "Copy link" in response.body
    share_state = "function uiStateParams" in response.body and "function currentStatePath" in response.body
    share_copy = "navigator.clipboard" in response.body and 'document.execCommand("copy")' in response.body
    result_match_notes = 'renderMatchNote(result.match_kind, "Result")' in response.body
    ok = (
        response.status == 200
        and "noindex" in robots.lower()
        and "nofollow" in robots.lower()
        and "frame-ancestors 'none'" in csp
        and "'unsafe-inline'" not in csp
        and meta_robots
        and share_link
        and share_state
        and share_copy
        and result_match_notes
    )
    return SmokeItem(
        key="html_shell",
        status="pass" if ok else "fail",
        summary="HTML shell has public noindex, CSP, share-link, and result-match controls."
        if ok
        else "HTML shell is missing public safety, share-link, or result-match controls.",
        evidence={
            "status": response.status,
            "x_robots_tag": robots,
            "content_security_policy": csp,
            "meta_robots": meta_robots,
            "share_link": share_link,
            "share_state": share_state,
            "share_copy": share_copy,
            "result_match_notes": result_match_notes,
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
        and payload.get("public_access_mode") == "snippets_and_source_links"
        and payload.get("private_fulltext") is False
        and payload.get("chunk_results_enabled") is False
        and bool(payload.get("source_reader_url"))
        and not contact_errors
        and manifest_ok
    )
    return SmokeItem(
        key="stats_public_contract",
        status="pass" if ok else "fail",
        summary="Stats endpoint reports public snippet/source-link mode."
        if ok
        else "Stats endpoint does not match public snippet/source-link/contact/manifest mode.",
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
        items.append(prefix_variants_search_item(base_url, probe, timeout=timeout))
        items.append(terms_item(base_url, probe, timeout=timeout))
        items.append(explain_item(base_url, probe, timeout=timeout))
        items.append(mentions_item(base_url, probe, timeout=timeout))
        items.append(coverage_item(base_url, probe, timeout=timeout))
        items.append(dossier_item(base_url, probe, timeout=timeout))
        items.append(evidence_pack_item(base_url, probe, timeout=timeout))
        items.append(recap_item(base_url, probe, timeout=timeout))
    return items, first_post_id


def probe_search_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/search", {"q": probe, "limit": "3"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    first = results[0] if results and isinstance(results[0], dict) else {}
    total_threadmarks = int(payload.get("total_threadmarks") or 0)
    ok = (
        response.status == 200
        and bool(results)
        and total_threadmarks > 0
        and int(payload.get("total_chunks") or 0) > 0
        and not forbidden
        and bool(first.get("source_url"))
    )
    return SmokeItem(
        key=f"search_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Probe search for {probe!r} returns bounded source-linked results."
        if ok
        else f"Probe search for {probe!r} is not public-ready.",
        evidence={
            "status": response.status,
            "result_count": len(results),
            "total_threadmarks": payload.get("total_threadmarks"),
            "total_chunks": payload.get("total_chunks"),
            "match_kind": payload.get("match_kind"),
            "forbidden_keys": forbidden[:8],
            "first_post_id": first.get("post_id"),
            "first_source_url": first.get("source_url"),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
        },
    )


def prefix_variants_search_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(
        base_url,
        "/api/search",
        {"q": probe, "limit": "3", "prefix_variants": "1"},
        timeout=timeout,
    )
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    ok = (
        response.status == 200
        and bool(results)
        and payload.get("prefix_variants") is True
        and payload.get("match_kind") in {"prefix-variants", "exact"}
        and not forbidden
    )
    return SmokeItem(
        key=f"search_prefix_variants_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Prefix-variant search for {probe!r} remains bounded and source-linked."
        if ok
        else f"Prefix-variant search for {probe!r} is not public-ready.",
        evidence={
            "status": response.status,
            "result_count": len(results),
            "prefix_variants": payload.get("prefix_variants"),
            "match_kind": payload.get("match_kind"),
            "forbidden_keys": forbidden[:8],
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
        },
    )


def coverage_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/coverage", {"q": probe, "limit": "10"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text", "snippet", "best_snippet"})
    ok = response.status == 200 and int(payload.get("total_threadmarks") or 0) > 0 and not forbidden
    return SmokeItem(
        key=f"coverage_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Coverage for {probe!r} is metadata-only."
        if ok
        else f"Coverage for {probe!r} is missing or exposes text fields.",
        evidence={
            "status": response.status,
            "total_threadmarks": payload.get("total_threadmarks"),
            "item_count": len(items),
            "match_kind": payload.get("match_kind"),
            "forbidden_keys": forbidden[:8],
        },
    )


def terms_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/terms", {"prefix": probe, "limit": "10"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    terms = payload.get("terms") if isinstance(payload.get("terms"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text", "snippet", "best_snippet"})
    first = terms[0] if terms and isinstance(terms[0], dict) else {}
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-term-index"
        and payload.get("metadata_only") is True
        and int(payload.get("result_count") or 0) > 0
        and bool(first.get("term"))
        and not forbidden
    )
    return SmokeItem(
        key=f"terms_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Term index for {probe!r} is metadata-only."
        if ok
        else f"Term index for {probe!r} is missing or exposes text fields.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "metadata_only": payload.get("metadata_only"),
            "prefix": payload.get("prefix"),
            "result_count": payload.get("result_count"),
            "first_term": first.get("term"),
            "forbidden_keys": forbidden[:8],
        },
    )


def explain_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/explain", {"q": probe, "term_limit": "8"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    exact = payload.get("exact") if isinstance(payload.get("exact"), dict) else {}
    prefix = payload.get("prefix") if isinstance(payload.get("prefix"), dict) else {}
    resolved = payload.get("resolved") if isinstance(payload.get("resolved"), dict) else {}
    cautions = payload.get("cautions") if isinstance(payload.get("cautions"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text", "snippet", "best_snippet"})
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-query-explain"
        and payload.get("metadata_only") is True
        and payload.get("query") == probe
        and isinstance(resolved.get("total_threadmarks"), int)
        and int(resolved.get("total_chunks") or 0) > 0
        and not forbidden
    )
    return SmokeItem(
        key=f"explain_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Query explain for {probe!r} is metadata-only."
        if ok
        else f"Query explain for {probe!r} is missing or exposes text fields.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "metadata_only": payload.get("metadata_only"),
            "exact_threadmarks": exact.get("total_threadmarks"),
            "exact_chunks": exact.get("total_chunks"),
            "prefix_threadmarks": prefix.get("total_threadmarks"),
            "prefix_chunks": prefix.get("total_chunks"),
            "resolved_threadmarks": resolved.get("total_threadmarks"),
            "resolved_chunks": resolved.get("total_chunks"),
            "resolved_match_kind": resolved.get("match_kind"),
            "caution_codes": [
                item.get("code")
                for item in cautions
                if isinstance(item, dict) and item.get("code")
            ],
            "forbidden_keys": forbidden[:8],
        },
    )


def compare_item(base_url: str, probes: tuple[str, ...], timeout: float) -> SmokeItem:
    params = {"q": probes[0], "topic": probes[1]}
    response = fetch_json(base_url, "/api/compare", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    topics = payload.get("topics") if isinstance(payload.get("topics"), list) else []
    overlap = payload.get("all_overlap") if isinstance(payload.get("all_overlap"), dict) else {}
    pairwise = payload.get("pairwise_overlaps") if isinstance(payload.get("pairwise_overlaps"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text", "snippet", "best_snippet"})
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-topic-comparison"
        and payload.get("metadata_only") is True
        and len(topics) == len(probes)
        and all(isinstance(topic, dict) and int(topic.get("total_threadmarks") or 0) > 0 for topic in topics)
        and isinstance(overlap.get("total_threadmarks"), int)
        and len(pairwise) >= 1
        and not forbidden
    )
    return SmokeItem(
        key=f"compare_probe:{':'.join(probes)}",
        status="pass" if ok else "fail",
        summary=f"Comparison for {', '.join(repr(probe) for probe in probes)} is metadata-only."
        if ok
        else f"Comparison for {', '.join(repr(probe) for probe in probes)} is missing or exposes text fields.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "topic_count": len(topics),
            "all_overlap_threadmarks": overlap.get("total_threadmarks"),
            "pairwise_count": len(pairwise),
            "forbidden_keys": forbidden[:8],
        },
    )


def mentions_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/mentions", {"q": probe, "limit": "2", "sort": "timeline"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    mentions = payload.get("mentions") if isinstance(payload.get("mentions"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    source_ok = all(isinstance(item, dict) and item.get("source_url") for item in mentions)
    ok = (
        response.status == 200
        and int(payload.get("total_mentions") or 0) > 0
        and not forbidden
        and source_ok
        and payload.get("snippet_budget_chars") is not None
    )
    return SmokeItem(
        key=f"mentions_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Mentions for {probe!r} are bounded and source-linked."
        if ok
        else f"Mentions for {probe!r} are missing, unbounded, or expose forbidden fields.",
        evidence={
            "status": response.status,
            "total_mentions": payload.get("total_mentions"),
            "mention_count": len(mentions),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
            "source_ok": source_ok,
        },
    )


def dossier_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    params = {"q": probe, "threadmark_limit": "2", "mention_limit": "2"}
    response = fetch_json(base_url, "/api/dossier", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    ok = (
        response.status == 200
        and int(payload.get("total_threadmarks") or 0) > 0
        and not forbidden
        and payload.get("snippet_budget_chars") is not None
    )
    return SmokeItem(
        key=f"dossier_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Dossier for {probe!r} is bounded and source-linked."
        if ok
        else f"Dossier for {probe!r} is not bounded or exposes forbidden fields.",
        evidence={
            "status": response.status,
            "total_threadmarks": payload.get("total_threadmarks"),
            "total_mentions": payload.get("total_mentions"),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
        },
    )


def evidence_pack_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    params = {"q": probe, "threadmark_limit": "2", "mention_limit": "2"}
    response = fetch_json(base_url, "/api/evidence-pack", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    dossier = payload.get("dossier") if isinstance(payload.get("dossier"), dict) else {}
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-evidence-pack"
        and payload.get("bounded_retrieval_only") is True
        and int(dossier.get("total_threadmarks") or 0) > 0
        and not forbidden
        and payload.get("snippet_budget_chars") is not None
    )
    return SmokeItem(
        key=f"evidence_pack_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Evidence pack for {probe!r} is bounded and source-linked."
        if ok
        else f"Evidence pack for {probe!r} is missing, unbounded, or exposes forbidden fields.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "total_threadmarks": dossier.get("total_threadmarks"),
            "total_mentions": dossier.get("total_mentions"),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
        },
    )


def recap_item(base_url: str, probe: str, timeout: float) -> SmokeItem:
    params = {"q": probe, "timeline_limit": "2", "mention_limit": "2"}
    response = fetch_json(base_url, "/api/recap", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-topic-recap"
        and payload.get("bounded_retrieval_only") is True
        and int(payload.get("total_threadmarks") or 0) > 0
        and len(timeline) <= 2
        and not forbidden
        and payload.get("snippet_budget_chars") is not None
    )
    return SmokeItem(
        key=f"recap_probe:{probe}",
        status="pass" if ok else "fail",
        summary=f"Recap for {probe!r} is bounded and source-linked."
        if ok
        else f"Recap for {probe!r} is missing, unbounded, or exposes forbidden fields.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "total_threadmarks": payload.get("total_threadmarks"),
            "total_mentions": payload.get("total_mentions"),
            "timeline_count": len(timeline),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
        },
    )


def claim_pair_items(base_url: str, claim_pairs: tuple[ClaimPair, ...], timeout: float) -> list[SmokeItem]:
    items: list[SmokeItem] = []
    for topic, claim in claim_pairs:
        items.append(explain_claim_pair_item(base_url, topic=topic, claim=claim, timeout=timeout))
        items.append(claim_pair_item(base_url, topic=topic, claim=claim, timeout=timeout))
        q_only = q_only_claim_query(topic, claim)
        if q_only is not None:
            items.append(q_only_claim_item(base_url, topic=topic, claim=claim, query=q_only, timeout=timeout))
            items.append(q_only_evidence_pack_item(base_url, topic=topic, claim=claim, query=q_only, timeout=timeout))
            items.append(q_only_recap_item(base_url, topic=topic, claim=claim, query=q_only, timeout=timeout))
    return items


def explain_claim_pair_item(base_url: str, topic: str, claim: str, timeout: float) -> SmokeItem:
    query = f"{topic} {claim}".strip()
    response = fetch_json(base_url, "/api/explain", {"q": query, "term_limit": "8"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    breakdown = payload.get("term_breakdown") if isinstance(payload.get("term_breakdown"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text", "snippet", "best_snippet"})
    summary_rows = explain_breakdown_summary(breakdown)
    breakdown_terms = {row.get("query") for row in summary_rows}
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-query-explain"
        and payload.get("metadata_only") is True
        and payload.get("query") == query
        and topic in breakdown_terms
        and claim in breakdown_terms
        and all(isinstance(row.get("resolved_threadmarks"), int) for row in summary_rows)
        and not forbidden
    )
    return SmokeItem(
        key=f"explain_pair:{topic}:{claim}",
        status="pass" if ok else "fail",
        summary=f"Query explain for {query!r} reports metadata-only per-term breakdowns."
        if ok
        else f"Query explain for {query!r} is missing term breakdowns or exposes text fields.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "metadata_only": payload.get("metadata_only"),
            "query": payload.get("query"),
            "term_breakdown": summary_rows,
            "caution_codes": caution_codes(payload),
            "forbidden_keys": forbidden[:8],
        },
    )


def explain_breakdown_summary(items: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        exact = item.get("exact") if isinstance(item.get("exact"), dict) else {}
        prefix = item.get("prefix") if isinstance(item.get("prefix"), dict) else {}
        resolved = item.get("resolved") if isinstance(item.get("resolved"), dict) else {}
        rows.append(
            {
                "query": item.get("query"),
                "exact_threadmarks": exact.get("total_threadmarks"),
                "exact_chunks": exact.get("total_chunks"),
                "prefix_threadmarks": prefix.get("total_threadmarks"),
                "prefix_chunks": prefix.get("total_chunks"),
                "resolved_threadmarks": resolved.get("total_threadmarks"),
                "resolved_chunks": resolved.get("total_chunks"),
                "resolved_match_kind": resolved.get("match_kind"),
            }
        )
    return rows


def q_only_claim_query(topic: str, claim: str) -> str | None:
    topic_terms = topic.strip().split()
    claim_terms = claim.strip().split()
    if len(topic_terms) != 1 or len(claim_terms) != 1:
        return None
    return f"{topic_terms[0]}'s {claim_terms[0]}"


def claim_pair_item(base_url: str, topic: str, claim: str, timeout: float) -> SmokeItem:
    params = {"q": topic, "claim": claim, "limit": "3"}
    response = fetch_json(base_url, "/api/claim", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    evidence_source_ok, evidence_proximity_ok = claim_evidence_checks(evidence)
    ok = (
        response.status == 200
        and payload.get("topic_query") == topic
        and payload.get("claim_query") == claim
        and int(payload.get("topic_threadmarks") or 0) > 0
        and int(payload.get("claim_threadmarks") or 0) > 0
        and isinstance(payload.get("evidence_level"), str)
        and payload.get("negation_cue_evidence") is not None
        and payload.get("snippet_budget_chars") is not None
        and evidence_source_ok
        and evidence_proximity_ok
        and not forbidden
    )
    return SmokeItem(
        key=f"claim_pair:{topic}:{claim}",
        status="pass" if ok else "fail",
        summary=f"Claim check for {topic!r} / {claim!r} returns bounded source-linked diagnostics."
        if ok
        else f"Claim check for {topic!r} / {claim!r} is missing, unbounded, or does not resolve both terms.",
        evidence={
            "status": response.status,
            "evidence_level": payload.get("evidence_level"),
            "topic_threadmarks": payload.get("topic_threadmarks"),
            "claim_threadmarks": payload.get("claim_threadmarks"),
            "topic_query_exact_threadmarks": payload.get("topic_query_exact_threadmarks"),
            "topic_query_exact_chunks": payload.get("topic_query_exact_chunks"),
            "claim_query_exact_threadmarks": payload.get("claim_query_exact_threadmarks"),
            "claim_query_exact_chunks": payload.get("claim_query_exact_chunks"),
            "overlapping_threadmarks": payload.get("overlapping_threadmarks"),
            "overlapping_chunks": payload.get("overlapping_chunks"),
            "negation_cue_evidence": payload.get("negation_cue_evidence"),
            "caution_codes": caution_codes(payload),
            "evidence_count": len(evidence),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
            "evidence_source_ok": evidence_source_ok,
            "evidence_proximity_ok": evidence_proximity_ok,
        },
    )


def q_only_claim_item(base_url: str, topic: str, claim: str, query: str, timeout: float) -> SmokeItem:
    response = fetch_json(base_url, "/api/claim", {"q": query, "limit": "3"}, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    evidence_source_ok, evidence_proximity_ok = claim_evidence_checks(evidence)
    ok = (
        response.status == 200
        and payload.get("claim_inferred_from_query") is True
        and payload.get("original_query") == query
        and payload.get("topic_query") == topic
        and payload.get("claim_query") == claim
        and int(payload.get("topic_threadmarks") or 0) > 0
        and int(payload.get("claim_threadmarks") or 0) > 0
        and isinstance(payload.get("evidence_level"), str)
        and payload.get("snippet_budget_chars") is not None
        and evidence_source_ok
        and evidence_proximity_ok
        and not forbidden
    )
    return SmokeItem(
        key=f"claim_q_only:{topic}:{claim}",
        status="pass" if ok else "fail",
        summary=f"Q-only claim check for {query!r} infers bounded source-linked diagnostics."
        if ok
        else f"Q-only claim check for {query!r} does not infer a bounded {topic!r} / {claim!r} claim.",
        evidence={
            "status": response.status,
            "query": query,
            "claim_inferred_from_query": payload.get("claim_inferred_from_query"),
            "original_query": payload.get("original_query"),
            "topic_query": payload.get("topic_query"),
            "claim_query": payload.get("claim_query"),
            "evidence_level": payload.get("evidence_level"),
            "topic_threadmarks": payload.get("topic_threadmarks"),
            "claim_threadmarks": payload.get("claim_threadmarks"),
            "topic_query_exact_threadmarks": payload.get("topic_query_exact_threadmarks"),
            "topic_query_exact_chunks": payload.get("topic_query_exact_chunks"),
            "claim_query_exact_threadmarks": payload.get("claim_query_exact_threadmarks"),
            "claim_query_exact_chunks": payload.get("claim_query_exact_chunks"),
            "caution_codes": caution_codes(payload),
            "evidence_count": len(evidence),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
            "evidence_source_ok": evidence_source_ok,
            "evidence_proximity_ok": evidence_proximity_ok,
        },
    )


def q_only_evidence_pack_item(base_url: str, topic: str, claim: str, query: str, timeout: float) -> SmokeItem:
    params = {"q": query, "threadmark_limit": "3", "mention_limit": "3", "claim_limit": "3"}
    response = fetch_json(base_url, "/api/evidence-pack", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    dossier = payload.get("dossier") if isinstance(payload.get("dossier"), dict) else {}
    claims = payload.get("claims") if isinstance(payload.get("claims"), list) else []
    first_claim = claims[0] if claims and isinstance(claims[0], dict) else {}
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-evidence-pack"
        and payload.get("bounded_retrieval_only") is True
        and payload.get("claim_inferred_from_query") is True
        and payload.get("original_query") == query
        and payload.get("query") == topic
        and dossier.get("query") == topic
        and int(dossier.get("total_threadmarks") or 0) > 0
        and first_claim.get("claim_query") == claim
        and isinstance(first_claim.get("evidence_level"), str)
        and payload.get("snippet_budget_chars") is not None
        and not forbidden
    )
    return SmokeItem(
        key=f"evidence_pack_q_only:{topic}:{claim}",
        status="pass" if ok else "fail",
        summary=f"Q-only evidence pack for {query!r} infers a bounded {topic!r} / {claim!r} pack."
        if ok
        else f"Q-only evidence pack for {query!r} is missing, unbounded, or not inferred.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "query": query,
            "resolved_query": payload.get("query"),
            "claim_inferred_from_query": payload.get("claim_inferred_from_query"),
            "original_query": payload.get("original_query"),
            "dossier_query": dossier.get("query"),
            "total_threadmarks": dossier.get("total_threadmarks"),
            "claim_query": first_claim.get("claim_query"),
            "evidence_level": first_claim.get("evidence_level"),
            "topic_query_exact_threadmarks": first_claim.get("topic_query_exact_threadmarks"),
            "topic_query_exact_chunks": first_claim.get("topic_query_exact_chunks"),
            "claim_query_exact_threadmarks": first_claim.get("claim_query_exact_threadmarks"),
            "claim_query_exact_chunks": first_claim.get("claim_query_exact_chunks"),
            "caution_codes": caution_codes(first_claim),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
        },
    )


def q_only_recap_item(base_url: str, topic: str, claim: str, query: str, timeout: float) -> SmokeItem:
    params = {"q": query, "timeline_limit": "3", "mention_limit": "3", "claim_limit": "3"}
    response = fetch_json(base_url, "/api/recap", params, timeout=timeout)
    payload = response.json_payload if isinstance(response.json_payload, dict) else {}
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    claims = payload.get("claims") if isinstance(payload.get("claims"), list) else []
    first_claim = claims[0] if claims and isinstance(claims[0], dict) else {}
    forbidden = forbidden_key_paths(payload, {"body", "text"})
    ok = (
        response.status == 200
        and payload.get("kind") == "thread-search-topic-recap"
        and payload.get("bounded_retrieval_only") is True
        and payload.get("claim_inferred_from_query") is True
        and payload.get("original_query") == query
        and payload.get("query") == topic
        and int(payload.get("total_threadmarks") or 0) > 0
        and len(timeline) <= 3
        and first_claim.get("claim_query") == claim
        and isinstance(first_claim.get("evidence_level"), str)
        and payload.get("snippet_budget_chars") is not None
        and not forbidden
    )
    return SmokeItem(
        key=f"recap_q_only:{topic}:{claim}",
        status="pass" if ok else "fail",
        summary=f"Q-only recap for {query!r} infers a bounded {topic!r} / {claim!r} recap."
        if ok
        else f"Q-only recap for {query!r} is missing, unbounded, or not inferred.",
        evidence={
            "status": response.status,
            "kind": payload.get("kind"),
            "query": query,
            "resolved_query": payload.get("query"),
            "claim_inferred_from_query": payload.get("claim_inferred_from_query"),
            "original_query": payload.get("original_query"),
            "total_threadmarks": payload.get("total_threadmarks"),
            "timeline_count": len(timeline),
            "claim_query": first_claim.get("claim_query"),
            "evidence_level": first_claim.get("evidence_level"),
            "topic_query_exact_threadmarks": first_claim.get("topic_query_exact_threadmarks"),
            "topic_query_exact_chunks": first_claim.get("topic_query_exact_chunks"),
            "claim_query_exact_threadmarks": first_claim.get("claim_query_exact_threadmarks"),
            "claim_query_exact_chunks": first_claim.get("claim_query_exact_chunks"),
            "caution_codes": caution_codes(first_claim),
            "snippet_budget_chars": payload.get("snippet_budget_chars"),
            "forbidden_keys": forbidden[:8],
        },
    )


def caution_codes(payload: dict[str, Any]) -> list[str]:
    cautions = payload.get("cautions") if isinstance(payload.get("cautions"), list) else []
    return [
        str(item.get("code"))
        for item in cautions
        if isinstance(item, dict) and item.get("code")
    ]


def claim_evidence_checks(evidence: list[Any]) -> tuple[bool, bool]:
    evidence_with_sources = [
        item
        for item in evidence
        if isinstance(item, dict) and item.get("source_url")
    ]
    evidence_source_ok = not evidence or len(evidence_with_sources) == len(evidence)
    evidence_proximity_ok = all(
        isinstance(item, dict)
        and isinstance(item.get("proximity"), str)
        and item.get("proximity")
        and isinstance(item.get("chunk_distance"), int)
        and isinstance(item.get("proximity_note"), str)
        and item.get("proximity_note")
        for item in evidence
    )
    return evidence_source_ok, evidence_proximity_ok


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
