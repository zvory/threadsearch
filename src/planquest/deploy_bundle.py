from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import os
import json
from pathlib import Path
import tarfile
from typing import Any

from .artifact import (
    ALLOWED_ARTIFACT_FILES,
    ARTIFACT_MANIFEST_NAME,
    sha256_file,
)
from .audit import artifact_item


DEFAULT_BUNDLE_DIR = Path("dist/deploy-bundles")
APP_BUNDLE_NAME = "thread-search-app.tar.gz"
PRIVATE_ARTIFACT_BUNDLE_NAME = "thread-search-private-artifact.tar.gz"
BUNDLE_MANIFEST_NAME = "deploy-bundle-manifest.json"
APP_BUNDLE_ROOT = "thread-search-app"
PRIVATE_ARTIFACT_ROOT = "thread-search-private-artifact"
APP_INCLUDE_PATHS = (
    ".github",
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "README.md",
    "compose.yaml",
    "pyproject.toml",
    "deploy",
    "docs",
    "src",
)
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
PRIVATE_TOP_LEVEL_NAMES = {"data", "dist", ".git", ".venv"}


class DeployBundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class BundleFile:
    path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeployBundleResult:
    output_dir: Path
    app_bundle: BundleFile
    private_artifact_bundle: BundleFile
    manifest_path: Path
    app_files: tuple[str, ...]
    private_artifact_files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "app_bundle": self.app_bundle.to_dict(),
            "private_artifact_bundle": self.private_artifact_bundle.to_dict(),
            "manifest_path": str(self.manifest_path),
            "app_files": list(self.app_files),
            "private_artifact_files": list(self.private_artifact_files),
        }


@dataclass(frozen=True)
class DeployBundleCheck:
    ok: bool
    manifest_path: Path
    checks: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "manifest_path": str(self.manifest_path),
            "checks": list(self.checks),
            "errors": list(self.errors),
        }


def create_deploy_bundle(
    *,
    root: Path = Path("."),
    artifact_dir: Path = Path("dist/thread-search-public"),
    out_dir: Path = DEFAULT_BUNDLE_DIR,
    expected_threadmarks: int = 269,
    include_tests: bool = True,
) -> DeployBundleResult:
    root = root.resolve()
    artifact_dir = artifact_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    app_files = collect_app_files(root, include_tests=include_tests)
    artifact_files = collect_private_artifact_files(artifact_dir, expected_threadmarks=expected_threadmarks)

    app_bundle_path = out_dir / APP_BUNDLE_NAME
    private_bundle_path = out_dir / PRIVATE_ARTIFACT_BUNDLE_NAME
    manifest_path = out_dir / BUNDLE_MANIFEST_NAME

    write_tarball(app_bundle_path, root, app_files, APP_BUNDLE_ROOT)
    write_tarball(private_bundle_path, artifact_dir, artifact_files, PRIVATE_ARTIFACT_ROOT)

    app_bundle = bundle_file(app_bundle_path)
    private_bundle = bundle_file(private_bundle_path)
    manifest = build_bundle_manifest(
        app_bundle=app_bundle,
        private_artifact_bundle=private_bundle,
        app_files=app_files,
        private_artifact_files=artifact_files,
        artifact_dir=artifact_dir,
        expected_threadmarks=expected_threadmarks,
        include_tests=include_tests,
    )
    write_text_atomic(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return DeployBundleResult(
        output_dir=out_dir,
        app_bundle=app_bundle,
        private_artifact_bundle=private_bundle,
        manifest_path=manifest_path,
        app_files=tuple(path.as_posix() for path in app_files),
        private_artifact_files=tuple(path.as_posix() for path in artifact_files),
    )


def collect_app_files(root: Path, *, include_tests: bool = True) -> list[Path]:
    include_paths = [*APP_INCLUDE_PATHS]
    if include_tests:
        include_paths.append("tests")
    files: list[Path] = []
    missing: list[str] = []
    for raw in include_paths:
        relative = Path(raw)
        path = root / relative
        if not path.exists():
            missing.append(raw)
            continue
        if path.is_symlink():
            raise DeployBundleError(f"refusing to bundle symlink: {relative.as_posix()}")
        if path.is_file():
            if should_include_app_file(relative):
                files.append(relative)
            continue
        for child in sorted(path.rglob("*")):
            rel = child.relative_to(root)
            if child.is_symlink():
                raise DeployBundleError(f"refusing to bundle symlink: {rel.as_posix()}")
            if child.is_file() and should_include_app_file(rel):
                files.append(rel)
    if missing:
        raise DeployBundleError(f"required app bundle path(s) missing: {', '.join(missing)}")
    return sorted(dict.fromkeys(files))


def should_include_app_file(path: Path) -> bool:
    parts = path.parts
    if not parts or parts[0] in PRIVATE_TOP_LEVEL_NAMES:
        return False
    if any(part in CACHE_NAMES or part.endswith(".egg-info") for part in parts):
        return False
    name = path.name
    if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
        return False
    return True


def collect_private_artifact_files(artifact_dir: Path, *, expected_threadmarks: int) -> list[Path]:
    manifest_path = artifact_dir / ARTIFACT_MANIFEST_NAME
    item = artifact_item(manifest_path, expected_threadmarks)
    if item.status != "pass":
        raise DeployBundleError(f"{item.summary} evidence={json.dumps(item.evidence, sort_keys=True)}")
    files = sorted(Path(name) for name in ALLOWED_ARTIFACT_FILES if (artifact_dir / name).is_file())
    missing = sorted(name for name in ALLOWED_ARTIFACT_FILES if not (artifact_dir / name).is_file())
    if missing:
        raise DeployBundleError(f"artifact directory is missing required file(s): {', '.join(missing)}")
    return files


def write_tarball(output: Path, base_dir: Path, files: list[Path], root_name: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.tmp")
    with tarfile.open(temp_output, "w:gz") as tar:
        for relative in files:
            source = base_dir / relative
            if not source.is_file():
                raise DeployBundleError(f"bundle source is not a file: {relative.as_posix()}")
            info = tar.gettarinfo(str(source), arcname=f"{root_name}/{relative.as_posix()}")
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with source.open("rb") as handle:
                tar.addfile(info, handle)
    os.replace(temp_output, output)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def bundle_file(path: Path) -> BundleFile:
    return BundleFile(path=str(path), size_bytes=path.stat().st_size, sha256=sha256_file(path))


def build_bundle_manifest(
    *,
    app_bundle: BundleFile,
    private_artifact_bundle: BundleFile,
    app_files: list[Path],
    private_artifact_files: list[Path],
    artifact_dir: Path,
    expected_threadmarks: int,
    include_tests: bool,
) -> dict[str, Any]:
    return {
        "kind": "thread-search-deploy-bundle",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "expected_threadmarks": expected_threadmarks,
        "include_tests": include_tests,
        "app_bundle": {
            **app_bundle.to_dict(),
            "public_safe": True,
            "contains_private_corpus": False,
            "forbidden_top_level_paths": sorted(PRIVATE_TOP_LEVEL_NAMES),
            "file_count": len(app_files),
            "files": [path.as_posix() for path in app_files],
        },
        "private_artifact_bundle": {
            **private_artifact_bundle.to_dict(),
            "public_safe": False,
            "contains_full_text_server_side": True,
            "must_not_be_static_or_downloadable": True,
            "artifact_dir": str(artifact_dir),
            "allowed_files": sorted(ALLOWED_ARTIFACT_FILES),
            "file_count": len(private_artifact_files),
            "files": [path.as_posix() for path in private_artifact_files],
        },
        "deployment_contract": {
            "serve_requires_launch_ready": True,
            "serve_requires_artifact_manifest": True,
            "app_binds_loopback_behind_proxy": True,
            "private_fulltext_public": False,
            "public_responses_are_bounded_snippets_and_source_links": True,
        },
    }


def verify_deploy_bundle(manifest_path: Path = DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME) -> DeployBundleCheck:
    checks: list[str] = []
    errors: list[str] = []
    if not manifest_path.exists():
        return DeployBundleCheck(
            ok=False,
            manifest_path=manifest_path,
            checks=(),
            errors=(f"bundle manifest is missing: {manifest_path}",),
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return DeployBundleCheck(
            ok=False,
            manifest_path=manifest_path,
            checks=(),
            errors=(f"bundle manifest is invalid JSON: {exc}",),
        )
    if not isinstance(manifest, dict):
        return DeployBundleCheck(
            ok=False,
            manifest_path=manifest_path,
            checks=(),
            errors=("bundle manifest must be a JSON object",),
        )
    if manifest.get("kind") != "thread-search-deploy-bundle":
        errors.append(f"unexpected bundle manifest kind: {manifest.get('kind')!r}")
    else:
        checks.append("bundle manifest kind: thread-search-deploy-bundle")

    base_dir = manifest_path.parent
    app_section = manifest.get("app_bundle") if isinstance(manifest.get("app_bundle"), dict) else {}
    private_section = (
        manifest.get("private_artifact_bundle")
        if isinstance(manifest.get("private_artifact_bundle"), dict)
        else {}
    )
    checks.extend(
        verify_bundle_file_section(
            section=app_section,
            base_dir=base_dir,
            label="app_bundle",
            root_name=APP_BUNDLE_ROOT,
            expected_public_safe=True,
            expected_files=tuple(str(item) for item in app_section.get("files", []) if isinstance(item, str)),
            forbidden_prefixes=tuple(f"{APP_BUNDLE_ROOT}/{name}/" for name in PRIVATE_TOP_LEVEL_NAMES),
            required_exact=(),
            errors=errors,
        )
    )
    checks.extend(
        verify_bundle_file_section(
            section=private_section,
            base_dir=base_dir,
            label="private_artifact_bundle",
            root_name=PRIVATE_ARTIFACT_ROOT,
            expected_public_safe=False,
            expected_files=tuple(str(item) for item in private_section.get("files", []) if isinstance(item, str)),
            forbidden_prefixes=(),
            required_exact=tuple(sorted(f"{PRIVATE_ARTIFACT_ROOT}/{name}" for name in ALLOWED_ARTIFACT_FILES)),
            errors=errors,
        )
    )
    return DeployBundleCheck(
        ok=not errors,
        manifest_path=manifest_path,
        checks=tuple(checks),
        errors=tuple(errors),
    )


def verify_bundle_file_section(
    *,
    section: dict[str, Any],
    base_dir: Path,
    label: str,
    root_name: str,
    expected_public_safe: bool,
    expected_files: tuple[str, ...],
    forbidden_prefixes: tuple[str, ...],
    required_exact: tuple[str, ...],
    errors: list[str],
) -> list[str]:
    checks: list[str] = []
    if not section:
        errors.append(f"{label} section is missing")
        return checks
    raw_path = str(section.get("path") or "")
    if not raw_path:
        errors.append(f"{label}.path is missing")
        return checks
    path = resolve_bundle_path(raw_path, base_dir)
    if not path.exists():
        errors.append(f"{label} tarball is missing: {path}")
        return checks
    expected_sha = str(section.get("sha256") or "")
    expected_size = parse_int(section.get("size_bytes"))
    actual_sha = sha256_file(path)
    actual_size = path.stat().st_size
    if expected_sha != actual_sha:
        errors.append(f"{label} sha256 mismatch: expected {expected_sha}, got {actual_sha}")
    else:
        checks.append(f"{label} sha256 matches")
    if expected_size != actual_size:
        errors.append(f"{label} size mismatch: expected {expected_size}, got {actual_size}")
    else:
        checks.append(f"{label} size matches")
    if section.get("public_safe") is not expected_public_safe:
        errors.append(f"{label}.public_safe should be {expected_public_safe}")
    else:
        checks.append(f"{label} public_safe={expected_public_safe}")

    try:
        names = tar_member_names(path)
    except (EOFError, tarfile.TarError, OSError) as exc:
        errors.append(f"{label} could not be read as tar.gz: {exc}")
        return checks
    unsafe = unsafe_tar_names(names, root_name=root_name)
    if unsafe:
        errors.append(f"{label} contains unsafe tar member(s): {', '.join(unsafe[:10])}")
    else:
        checks.append(f"{label} tar members stay under {root_name}/")
    forbidden = sorted(name for name in names for prefix in forbidden_prefixes if name.startswith(prefix))
    if forbidden:
        errors.append(f"{label} contains forbidden private path(s): {', '.join(forbidden[:10])}")
    elif forbidden_prefixes:
        checks.append(f"{label} excludes private top-level paths")

    declared_names = sorted(f"{root_name}/{path}" for path in expected_files)
    if declared_names and sorted(names) != declared_names:
        errors.append(f"{label} tar members differ from manifest file list")
    elif declared_names:
        checks.append(f"{label} tar members match manifest file list")
    if required_exact:
        actual = sorted(names)
        if actual != sorted(required_exact):
            errors.append(f"{label} tar members are not exactly the allowed private artifact files")
        else:
            checks.append(f"{label} contains exactly the allowed private artifact files")
    return checks


def resolve_bundle_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if path.parent == Path("."):
        return base_dir / path
    if path.exists():
        return path
    return base_dir / path.name


def tar_member_names(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def unsafe_tar_names(names: list[str], *, root_name: str) -> list[str]:
    unsafe: list[str] = []
    prefix = f"{root_name}/"
    for name in names:
        if name.startswith("/") or not name.startswith(prefix):
            unsafe.append(name)
            continue
        normalized = os.path.normpath(name)
        if normalized.startswith("..") or f"/../" in normalized or normalized != name:
            unsafe.append(name)
    return unsafe


def parse_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
