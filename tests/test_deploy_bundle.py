import json
from pathlib import Path
import tarfile

from planquest import cli
from planquest.artifact import ALLOWED_ARTIFACT_FILES, PUBLIC_API_ENDPOINTS, sha256_file
from planquest.audit import DEPLOYMENT_RUNTIME_CONTRACT
from planquest.deploy_bundle import APP_INCLUDE_PATHS, create_deploy_bundle, verify_deploy_bundle


def write_app_tree(root: Path) -> None:
    for raw in APP_INCLUDE_PATHS:
        path = root / raw
        if raw in {".dockerignore", ".gitignore", "Dockerfile", "README.md", "compose.yaml", "pyproject.toml"}:
            path.write_text(f"{raw}\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "placeholder.txt").write_text(f"{raw}\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_placeholder.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    private = root / "data"
    private.mkdir()
    (private / "raw.html").write_text("private story cache", encoding="utf-8")
    dist = root / "dist"
    dist.mkdir()
    (dist / "private.sqlite").write_text("private index", encoding="utf-8")
    cache = root / "src" / "__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"cache")


def write_valid_artifact(artifact_dir: Path, expected_threadmarks: int = 269) -> None:
    artifact_dir.mkdir(parents=True)
    database = artifact_dir / "thread-search.sqlite"
    database.write_bytes(b"sqlite placeholder")
    (artifact_dir / "README.deploy.txt").write_text("private backend artifact\n", encoding="utf-8")
    manifest = {
        "artifact": "thread-search-public-search-backend",
        "public_contact": "mailto:operator@thread-search.example",
        "removal_request_url": "https://thread-search.example/removal",
        "database": {
            "path": "thread-search.sqlite",
            "sha256": sha256_file(database),
            "size_bytes": database.stat().st_size,
        },
        "index": {"threadmarks": expected_threadmarks},
        "validation": {"ok": True},
        "public_server_defaults": {
            "private_fulltext": False,
            "allow_public_chunk_results": False,
            "public_search_limit": 30,
            "public_report_limit": 100,
            "public_mention_limit": 50,
            "public_threadmark_limit": 300,
            "max_query_chars": 120,
            "mention_window_chars": 320,
            "public_snippet_budget_chars": 6000,
            "public_rate_limit_per_minute": 60,
        },
        "content_handling": {
            "database_must_not_be_static_or_downloadable": True,
            "raw_html_included": False,
            "jsonl_included": False,
            "public_responses_are_source_linked_hits": True,
            "public_ui_source_attribution": True,
            "public_ui_contact_or_removal_notice_supported": True,
        },
        "public_api_contract": {
            "public_endpoints": list(PUBLIC_API_ENDPOINTS),
            "grouped_search_endpoint_enabled": True,
            "word_variants_always_enabled": True,
            "private_fulltext_endpoint_public": False,
            "legacy_evidence_endpoints_public": False,
        },
        "deployment_runtime_contract": dict(DEPLOYMENT_RUNTIME_CONTRACT),
        "permission_note": {"ok": True, "sha256": "abc"},
    }
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def tar_names(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def write_single_file_tar(path: Path, member_name: str, content: bytes) -> None:
    import io

    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member_name)
        info.size = len(content)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(content))


def test_create_deploy_bundle_separates_public_app_and_private_artifact(tmp_path: Path) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)

    result = create_deploy_bundle(root=tmp_path, artifact_dir=artifact_dir, out_dir=tmp_path / "bundle-out")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    app_names = tar_names(Path(result.app_bundle.path))
    artifact_names = tar_names(Path(result.private_artifact_bundle.path))

    assert manifest["app_bundle"]["contains_private_corpus"] is False
    assert manifest["private_artifact_bundle"]["contains_full_text_server_side"] is True
    assert all(not name.startswith("thread-search-app/data/") for name in app_names)
    assert all(not name.startswith("thread-search-app/dist/") for name in app_names)
    assert "thread-search-app/.github/placeholder.txt" in app_names
    assert "thread-search-app/src/placeholder.txt" in app_names
    assert "thread-search-app/src/__pycache__/ignored.pyc" not in app_names
    assert sorted(name.removeprefix("thread-search-private-artifact/") for name in artifact_names) == sorted(
        ALLOWED_ARTIFACT_FILES
    )
    check = verify_deploy_bundle(result.manifest_path)
    assert check.ok is True
    assert check.errors == ()


def test_create_deploy_bundle_rejects_unexpected_artifact_files(tmp_path: Path) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)
    (artifact_dir / "thread-search-threadmarks.jsonl").write_text("private text", encoding="utf-8")

    try:
        create_deploy_bundle(root=tmp_path, artifact_dir=artifact_dir, out_dir=tmp_path / "bundle-out")
    except Exception as exc:
        message = str(exc)
    else:
        message = ""

    assert "Artifact manifest does not satisfy the public deployment contract" in message
    assert "thread-search-threadmarks.jsonl" in message


def test_verify_deploy_bundle_rejects_checksum_mismatch(tmp_path: Path) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)
    result = create_deploy_bundle(root=tmp_path, artifact_dir=artifact_dir, out_dir=tmp_path / "bundle-out")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest["app_bundle"]["sha256"] = "0" * 64
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    check = verify_deploy_bundle(result.manifest_path)

    assert check.ok is False
    assert any("app_bundle sha256 mismatch" in error for error in check.errors)


def test_verify_deploy_bundle_rejects_private_path_in_app_tarball(tmp_path: Path) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)
    result = create_deploy_bundle(root=tmp_path, artifact_dir=artifact_dir, out_dir=tmp_path / "bundle-out")
    app_path = Path(result.app_bundle.path)
    write_single_file_tar(app_path, "thread-search-app/data/private.html", b"private story cache")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest["app_bundle"]["sha256"] = sha256_file(app_path)
    manifest["app_bundle"]["size_bytes"] = app_path.stat().st_size
    manifest["app_bundle"]["files"] = ["data/private.html"]
    manifest["app_bundle"]["file_count"] = 1
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    check = verify_deploy_bundle(result.manifest_path)

    assert check.ok is False
    assert any("contains forbidden private path" in error for error in check.errors)


def test_verify_deploy_bundle_reports_corrupt_tarball(tmp_path: Path) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)
    result = create_deploy_bundle(root=tmp_path, artifact_dir=artifact_dir, out_dir=tmp_path / "bundle-out")
    app_path = Path(result.app_bundle.path)
    app_path.write_bytes(b"not a complete tarball")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest["app_bundle"]["sha256"] = sha256_file(app_path)
    manifest["app_bundle"]["size_bytes"] = app_path.stat().st_size
    result.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    check = verify_deploy_bundle(result.manifest_path)

    assert check.ok is False
    assert any("app_bundle could not be read as tar.gz" in error for error in check.errors)


def test_deploy_bundle_cli_outputs_json(tmp_path: Path, monkeypatch, capsys) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)
    monkeypatch.chdir(tmp_path)

    result = cli.main(["deploy-bundle", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["app_bundle"]["path"].endswith("thread-search-app.tar.gz")
    assert payload["private_artifact_bundle"]["path"].endswith("thread-search-private-artifact.tar.gz")
    assert payload["manifest_path"].endswith("deploy-bundle-manifest.json")


def test_deploy_bundle_check_cli_outputs_json(tmp_path: Path, monkeypatch, capsys) -> None:
    write_app_tree(tmp_path)
    artifact_dir = tmp_path / "dist" / "thread-search-public"
    write_valid_artifact(artifact_dir)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["deploy-bundle"]) == 0
    capsys.readouterr()

    result = cli.main(["deploy-bundle-check", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["ok"] is True
    assert "app_bundle sha256 matches" in payload["checks"]
