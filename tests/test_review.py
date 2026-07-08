import json
from pathlib import Path
from types import SimpleNamespace

from planquest import cli
from planquest.review import render_author_review_packet


def payload() -> dict[str, object]:
    return {
        "crawl": {
            "reader_root": "https://forums.sufficientvelocity.com/threads/example.1/reader/",
            "robots_allowed": True,
            "user_agent": "thread-search-test",
            "category_id": 1,
            "page_count": 27,
            "cached_pages": 27,
            "network_pages_if_run_now": 0,
        },
        "corpus": {
            "exists": True,
            "ok": True,
            "path": "records.jsonl",
            "threadmarks": 269,
            "words": 1537395,
            "categories": [1],
        },
        "index": {
            "exists": True,
            "ok": True,
            "path": "records.sqlite",
            "threadmarks": 269,
            "chunks": 4932,
            "stored_chunks": 4932,
            "words": 1537395,
            "categories": [1],
        },
        "fetch_log": {
            "exists": True,
            "ok": True,
            "path": "data/raw/fetch-log.jsonl",
            "entries": 27,
            "page_fetches": 26,
            "robots_fetches": 1,
            "bytes": 16571312,
        },
        "validation": {
            "ok": True,
            "checks": ["probe 'Soviet': 1 result(s)", "probe 'Cuba': 1 result(s)"],
            "errors": [],
        },
        "launch_check": {
            "ok": True,
            "checks": ["public full-text routes: disabled"],
            "errors": [],
        },
    }


def write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "artifact": "thread-search-public-search-backend",
                "public_contact": "mailto:operator@example.test",
                "removal_request_url": "https://example.test/removal",
                "database": {"sha256": "abc123", "size_bytes": 123},
                "validation": {"ok": True},
            }
        ),
        encoding="utf-8",
    )


def write_bundle_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "kind": "thread-search-deploy-bundle",
                "app_bundle": {"sha256": "appabc", "path": "thread-search-app.tar.gz"},
                "private_artifact_bundle": {
                    "sha256": "privateabc",
                    "path": "thread-search-private-artifact.tar.gz",
                },
            }
        ),
        encoding="utf-8",
    )


def test_render_author_review_packet_is_metadata_only(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest)
    bundle = tmp_path / "deploy-bundle-manifest.json"
    write_bundle_manifest(bundle)
    monkeypatch.setattr("planquest.review.verify_deploy_bundle", lambda _path: SimpleNamespace(ok=True))

    rendered = render_author_review_packet(
        payload(),
        public_base_url="https://search.example.test/",
        probes=("Soviet", "Cuba"),
        artifact_manifest=manifest,
        deploy_bundle_manifest=bundle,
        claim_pairs=(("Cuba", "communist"),),
    )

    assert "# Thread Search Author Review Packet" in rendered
    assert "https://search.example.test" in rendered
    assert "main `Threadmarks` category only" in rendered
    assert "Full-text threadmark routes stay disabled" in rendered
    assert "Artifact database SHA-256: `abc123`" in rendered
    assert "Deploy bundle check passed: `True`" in rendered
    assert "Public app bundle SHA-256: `appabc`" in rendered
    assert "Private artifact bundle SHA-256: `privateabc`" in rendered
    assert "Private artifact bundle handling: keep server-side only" in rendered
    assert "[Claim diagnostics JSON for `Cuba` / `communist`]" in rendered
    assert rendered.count("[Search `Cuba`]") == 1
    assert "/api/claim?q=Cuba&claim=communist" in rendered
    assert "/api/compare?q=Cuba&topic=communist" in rendered
    assert "/api/explain?q=Cuba+communist" in rendered
    assert "bbWrapper" not in rendered
    assert "First paragraph" not in rendered
    assert "chunks.body" not in rendered
    assert "data/thread-search-threadmarks.jsonl" not in rendered
    assert "data/raw/" not in rendered


def test_author_review_cli_writes_packet(tmp_path: Path, monkeypatch, capsys) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest)
    bundle = tmp_path / "deploy-bundle-manifest.json"
    write_bundle_manifest(bundle)
    out = tmp_path / "author-review.md"
    monkeypatch.setattr(cli, "make_status_payload", lambda _args, probes: payload())
    monkeypatch.setattr("planquest.review.verify_deploy_bundle", lambda _path: SimpleNamespace(ok=True))

    result = cli.main(
        [
            "author-review",
            "--offline",
            "--artifact-manifest",
            str(manifest),
            "--permission-note",
            str(tmp_path / "missing-permission.md"),
            "--deploy-bundle-manifest",
            str(bundle),
            "--public-base-url",
            "https://search.example.test",
            "--probe",
            "Soviet",
            "--probe",
            "Cuba",
            "--out",
            str(out),
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert f"wrote: {out}" in captured.out
    rendered = out.read_text(encoding="utf-8")
    assert "Thread Search Author Review Packet" in rendered
    assert "Prototype URL: [https://search.example.test]" in rendered
    assert "Search `Soviet`" in rendered
