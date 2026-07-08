from pathlib import Path

from planquest.nextstep import NextStep
from planquest.runbook import render_runbook


def payload() -> dict[str, object]:
    return {
        "crawl": {
            "reader_root": "https://example.invalid/reader/",
            "robots_allowed": True,
            "user_agent": "thread-search-test",
            "page_count": 27,
            "cached_pages": 1,
            "network_pages_if_run_now": 26,
        },
        "corpus": {
            "exists": True,
            "ok": True,
            "path": "records.jsonl",
            "threadmarks": 10,
            "words": 12345,
            "categories": [1],
        },
        "index": {
            "exists": True,
            "ok": True,
            "path": "records.sqlite",
            "threadmarks": 10,
            "chunks": 89,
            "stored_chunks": 89,
            "words": 12345,
            "categories": [1],
        },
        "fetch_log": {
            "exists": True,
            "ok": True,
            "path": "data/raw/fetch-log.jsonl",
            "entries": 2,
            "page_fetches": 1,
            "robots_fetches": 1,
            "bytes": 1234,
        },
        "validation": {
            "ok": False,
            "checks": ["probe 'Cuba': 0 result(s)"],
            "errors": ["expected 269 threadmarks, found 10"],
        },
        "launch_check": {
            "ok": False,
            "checks": ["public full-text routes: disabled"],
            "errors": ["expected 269 threadmarks, found 10"],
        },
    }


def test_render_runbook_includes_current_state_and_next_command() -> None:
    rendered = render_runbook(
        payload(),
        NextStep(
            key="prefetch_next_page",
            summary="Fetch reader page 2 into cache.",
            command=".venv/bin/thread-search prefetch --from-page 2 --to-page 2 --limit 1 --delay 30",
            reasons=["cached_pages=1"],
        ),
        expected_threadmarks=269,
        probes=("Cuba",),
        artifact_manifest=Path("dist/thread-search-public/manifest.json"),
    )

    assert "# Thread Search Operator Runbook" in rendered
    assert "Cached reader pages: `1` / `27`" in rendered
    assert "Fetch receipt entries: `2`" in rendered
    assert "Logged page fetches: `1`" in rendered
    assert "Extracted threadmarks: `10` / `269`" in rendered
    assert ".venv/bin/thread-search prefetch --from-page 2 --to-page 2 --limit 1 --delay 30" in rendered
    assert ".venv/bin/thread-search permission-note --out data/permission-note.md" in rendered
    assert ".venv/bin/thread-search permission-request --out data/permission-request.md" in rendered
    assert ".venv/bin/thread-search site-review --refresh --delay 30 --out data/site-policy-review.md" in rendered
    assert ".venv/bin/thread-search site-review --offline --out data/site-policy-review.md" in rendered
    assert ".venv/bin/thread-search permission-note --check --out data/permission-note.md" in rendered
    assert "--json --out data/final-audit.json" in rendered
    assert ".venv/bin/thread-search deploy-bundle" in rendered
    assert ".venv/bin/thread-search deploy-bundle-check --manifest dist/deploy-bundles/deploy-bundle-manifest.json" in rendered
    assert "dist/deploy-bundles/" in rendered
    assert "verifies checksums, tar contents, and public/private separation" in rendered
    assert "Do not publish the private artifact tarball" in rendered
    assert '--public-contact "$THREAD_SEARCH_PUBLIC_CONTACT"' in rendered
    assert '--removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"' in rendered
    assert "Set THREAD_SEARCH_PUBLIC_CONTACT and THREAD_SEARCH_REMOVAL_REQUEST_URL" in rendered
    assert ".venv/bin/thread-search audit --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json" in rendered
    assert (
        ".venv/bin/thread-search serve --db dist/thread-search-public/thread-search.sqlite --host 127.0.0.1 "
        "--port 8765 --require-launch-ready --require-artifact-manifest "
        '--artifact-manifest dist/thread-search-public/manifest.json --public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" '
        '--removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL" --probe Cuba'
    ) in rendered
    assert "compose.yaml" in rendered
    assert "deploy/nginx-thread-search.conf.example" in rendered
    assert "deploy/systemd/thread-search.service.example" in rendered
    assert "deploy/systemd/thread-search.env.example" in rendered
    assert (
        ".venv/bin/thread-search public-smoke --base-url http://127.0.0.1:8765 "
        "--require-artifact-manifest --probe Cuba"
    ) in rendered
    assert ".venv/bin/thread-search preview-start --probe Cuba" in rendered
    assert ".venv/bin/thread-search preview-status --smoke --probe Cuba --claim-pair Cuba communist" in rendered
    assert ".venv/bin/thread-search preview-stop" in rendered
    assert "default 60 requests/minute per-IP limiter" in rendered
    assert "add `--claim-pair Cuba communist`" in rendered
    assert (
        ".venv/bin/thread-search audit --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json "
        "--public-base-url http://127.0.0.1:8765"
    ) in rendered
    assert (
        ".venv/bin/thread-search author-review --offline --public-base-url http://127.0.0.1:8765 "
        "--artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md "
        "--deploy-bundle-manifest dist/deploy-bundles/deploy-bundle-manifest.json --probe Cuba "
        "--out data/author-review.md"
    ) in rendered


def test_render_runbook_includes_permission_note_when_provided(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    rendered = render_runbook(
        payload(),
        NextStep(key="create_permission_note", summary="Create note.", command=f".venv/bin/thread-search permission-note --out {note}"),
        expected_threadmarks=269,
        probes=("Cuba",),
        artifact_manifest=Path("dist/thread-search-public/manifest.json"),
        permission_note=note,
    )

    assert f"Permission note: `{note}`" in rendered
    assert "## Permission Evidence Gate" in rendered
    assert "- Exists: `False`" in rendered
    assert "- Passed: `False`" in rendered
    assert "- Artifact export blocked: create the permission note template before exporting." in rendered
    assert (
        f'.venv/bin/thread-search artifact --probe Cuba --permission-note {note} '
        '--public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" --removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"'
    ) in rendered
    assert f"--artifact-manifest dist/thread-search-public/manifest.json --permission-note {note}" in rendered


def test_render_runbook_shows_incomplete_permission_note_blockers(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    note.write_text(
        """# Thread Search Permission Note

## Author Permission

- [ ] Permission source: TODO
- [ ] Permission date: TODO

## Site Rules Review

- [x] Review date: yesterday

## Public Deployment Scope

- [x] Public access is snippet-only and source-linked: bounded snippets with links.

## Operator Decision

- [x] Decision to proceed or not proceed: TODO
""",
        encoding="utf-8",
    )

    rendered = render_runbook(
        payload(),
        NextStep(key="complete_permission_note", summary="Complete note.", command=f".venv/bin/thread-search permission-note --check --out {note}"),
        expected_threadmarks=269,
        probes=("Cuba",),
        artifact_manifest=Path("dist/thread-search-public/manifest.json"),
        permission_note=note,
    )

    assert "## Permission Evidence Gate" in rendered
    assert "- Exists: `True`" in rendered
    assert "- Passed: `False`" in rendered
    assert "- Artifact export blocked: complete the note, keep the named checklist items, then rerun the check." in rendered
    assert f".venv/bin/thread-search permission-note --check --out {note}" in rendered
    assert "Missing checklist items:" in rendered
    assert "Placeholders:" in rendered
    assert "Unchecked checklist items:" in rendered
    assert "`Permission source: TODO`" in rendered
    assert "Invalid checklist details:" in rendered
    assert "`Review date (missing_iso_date)`" in rendered
    assert "Deployment decision:" in rendered
    assert "- `unclear`; detail: `TODO`" in rendered


def test_render_runbook_uses_custom_public_base_url() -> None:
    rendered = render_runbook(
        payload(),
        NextStep(key="final_audit", summary="Audit.", command=".venv/bin/thread-search audit"),
        expected_threadmarks=269,
        probes=("Cuba",),
        artifact_manifest=Path("dist/thread-search-public/manifest.json"),
        public_base_url="https://search.example.invalid",
    )

    assert (
        ".venv/bin/thread-search public-smoke --base-url https://search.example.invalid "
        "--require-artifact-manifest --probe Cuba"
    ) in rendered
    assert "--public-base-url https://search.example.invalid --operator \"Your handle\"" in rendered
    assert "--public-base-url https://search.example.invalid" in rendered


def test_render_runbook_avoids_story_body_text() -> None:
    rendered = render_runbook(
        payload(),
        NextStep(key="build_offline", summary="Build offline.", command=".venv/bin/thread-search build --offline"),
        expected_threadmarks=269,
        probes=("Cuba",),
        artifact_manifest=Path("dist/thread-search-public/manifest.json"),
    )

    assert "bbWrapper" not in rendered
    assert "First paragraph" not in rendered
    assert "source_url" not in rendered
