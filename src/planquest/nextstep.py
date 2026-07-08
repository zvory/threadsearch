from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from .deploy_bundle import verify_deploy_bundle
from .permission import permission_note_summary


PUBLIC_CONTACT_PLACEHOLDER = "$THREAD_SEARCH_PUBLIC_CONTACT"
REMOVAL_REQUEST_URL_PLACEHOLDER = "$THREAD_SEARCH_REMOVAL_REQUEST_URL"


@dataclass(frozen=True)
class NextStep:
    key: str
    summary: str
    command: str | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def recommend_next_step(
    payload: dict[str, Any],
    *,
    expected_threadmarks: int,
    probes: tuple[str, ...],
    artifact_manifest: Path | None = None,
    permission_note: Path | None = None,
    public_base_url: str | None = None,
    audit_report: Path | None = None,
    deploy_bundle_manifest: Path | None = None,
    delay_seconds: int = 30,
) -> NextStep:
    crawl = payload["crawl"]
    corpus = payload["corpus"]
    index = payload["index"]
    validation = payload["validation"]
    launch = payload["launch_check"]
    probe_args = " ".join(f"--probe {probe}" for probe in probes)

    if crawl.get("robots_allowed") is not True:
        return NextStep(
            key="blocked_by_robots",
            summary="Stop: robots policy does not allow fetching the reader URL for this user agent.",
            command=None,
            reasons=[f"reader_root={crawl.get('reader_root')}", f"user_agent={crawl.get('user_agent')}"],
        )

    pages = crawl.get("pages") or []
    uncached_pages = [page for page in pages if not page.get("cached")]
    if uncached_pages:
        page_number = int(uncached_pages[0]["page"])
        return NextStep(
            key="prefetch_next_page",
            summary=f"Fetch reader page {page_number} into cache, then rerun next-step.",
            command=(
                ".venv/bin/thread-search prefetch "
                f"--from-page {page_number} --to-page {page_number} --limit 1 --delay {delay_seconds}"
            ),
            reasons=[
                f"cached_pages={crawl.get('cached_pages')}",
                f"page_count={crawl.get('page_count')}",
                f"network_pages_if_run_now={crawl.get('network_pages_if_run_now')}",
            ],
        )

    corpus_count = int(corpus.get("threadmarks") or 0)
    index_count = int(index.get("threadmarks") or 0)
    if (
        corpus.get("exists") is not True
        or index.get("exists") is not True
        or corpus_count != expected_threadmarks
        or index_count != corpus_count
        or validation.get("ok") is not True
    ):
        return NextStep(
            key="build_offline",
            summary="All planned pages are cached; rebuild and validate the corpus without network access.",
            command=f".venv/bin/thread-search build --offline {probe_args}".strip(),
            reasons=[
                f"corpus_threadmarks={corpus_count}",
                f"index_threadmarks={index_count}",
                f"expected_threadmarks={expected_threadmarks}",
                *validation.get("errors", []),
            ],
        )

    if launch.get("ok") is not True:
        return NextStep(
            key="launch_check",
            summary="Corpus validation passes; run the public launch check.",
            command=f".venv/bin/thread-search launch-check {probe_args}".strip(),
            reasons=launch.get("errors", []),
        )

    if permission_note is not None:
        permission = permission_note_summary(permission_note)
        if permission.get("exists") is not True:
            return NextStep(
                key="create_permission_note",
                summary="Launch checks pass; create the local permission note template before exporting.",
                command=f".venv/bin/thread-search permission-note --out {permission_note}",
                reasons=[f"permission_note={permission_note}", "permission note is missing"],
            )
        if permission.get("ok") is not True:
            return NextStep(
                key="complete_permission_note",
                summary="Complete the permission note, then check it before exporting.",
                command=f".venv/bin/thread-search permission-note --check --out {permission_note}",
                reasons=[
                    f"permission_note={permission_note}",
                    f"missing_sections={permission.get('missing_sections', [])}",
                    f"missing_required_items={permission.get('missing_required_items', [])}",
                    f"placeholders={permission.get('placeholders', [])}",
                    f"unchecked_checkboxes={permission.get('unchecked_checkboxes', 0)}",
                    f"unchecked_items={permission.get('unchecked_items', [])}",
                    f"invalid_checklist_details={permission.get('invalid_checklist_details', [])}",
                    f"deployment_decision={permission.get('deployment_decision', {})}",
                ],
            )

    if artifact_manifest is not None and artifact_manifest.exists():
        if permission_note is not None and not manifest_has_permission_evidence(artifact_manifest):
            return NextStep(
                key="reexport_artifact",
                summary="Artifact manifest exists but lacks permission-note evidence; re-export the artifact.",
                command=artifact_command(probe_args=probe_args, permission_note=permission_note),
                reasons=[f"artifact_manifest={artifact_manifest}", "permission_note_ok missing from manifest"],
            )
        if audit_report is not None:
            audit = audit_report_summary(
                audit_report,
                artifact_manifest=artifact_manifest,
                public_base_url=public_base_url,
            )
            if audit["ok"]:
                if deploy_bundle_manifest is not None:
                    if not deploy_bundle_manifest.exists():
                        return NextStep(
                            key="create_deploy_bundle",
                            summary="Final audit passes; create deployment bundles before handoff.",
                            command=deploy_bundle_command(),
                            reasons=[
                                f"audit_report={audit_report}",
                                f"deploy_bundle_manifest={deploy_bundle_manifest}",
                                "deployment bundle manifest is missing",
                            ],
                        )
                    bundle = verify_deploy_bundle(deploy_bundle_manifest)
                    if not bundle.ok:
                        return NextStep(
                            key="refresh_deploy_bundle",
                            summary="Deployment bundle exists but verification fails; regenerate the bundles.",
                            command=deploy_bundle_command(),
                            reasons=[
                                f"deploy_bundle_manifest={deploy_bundle_manifest}",
                                *bundle.errors,
                            ],
                        )
                return NextStep(
                    key="ready_for_author_review",
                    summary="Final audit report already passes; generate or share the author-review packet.",
                    command=author_review_command(
                        probes=probes,
                        artifact_manifest=artifact_manifest,
                        permission_note=permission_note,
                        public_base_url=public_base_url,
                        deploy_bundle_manifest=deploy_bundle_manifest,
                    ),
                    reasons=[
                        f"audit_report={audit_report}",
                        f"generated_at_utc={audit.get('generated_at_utc')}",
                        f"artifact_manifest={artifact_manifest}",
                        *(
                            [f"deploy_bundle_manifest={deploy_bundle_manifest}"]
                            if deploy_bundle_manifest is not None
                            else []
                        ),
                    ],
                )
        return NextStep(
            key="final_audit",
            summary="Artifact manifest exists; run the final audit with manifest evidence.",
            command=final_audit_command(
                probe_args=probe_args,
                artifact_manifest=artifact_manifest,
                permission_note=permission_note,
                public_base_url=public_base_url,
                audit_report=audit_report,
            ),
            reasons=[f"artifact_manifest={artifact_manifest}"],
        )

    return NextStep(
        key="export_artifact",
        summary="Launch checks pass; export the private backend artifact for deployment.",
        command=artifact_command(probe_args=probe_args, permission_note=permission_note),
        reasons=[
            "artifact manifest was not provided or does not exist",
            "set THREAD_SEARCH_PUBLIC_CONTACT and THREAD_SEARCH_REMOVAL_REQUEST_URL before exporting",
        ],
    )


def public_contact_args() -> str:
    return (
        f'--public-contact "{PUBLIC_CONTACT_PLACEHOLDER}" '
        f'--removal-request-url "{REMOVAL_REQUEST_URL_PLACEHOLDER}"'
    )


def artifact_command(*, probe_args: str, permission_note: Path | None) -> str:
    parts = [f".venv/bin/thread-search artifact {probe_args}".strip()]
    if permission_note is not None:
        parts.append(f"--permission-note {permission_note}")
    parts.append(public_contact_args())
    return " ".join(part for part in parts if part)


def deploy_bundle_command() -> str:
    return ".venv/bin/thread-search deploy-bundle"


def manifest_has_permission_evidence(path: Path) -> bool:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    permission = manifest.get("permission_note", {})
    return permission.get("ok") is True and bool(permission.get("sha256"))


def final_audit_command(
    *,
    probe_args: str,
    artifact_manifest: Path,
    permission_note: Path | None,
    public_base_url: str | None,
    audit_report: Path | None = None,
) -> str:
    parts = [
        f".venv/bin/thread-search audit {probe_args}".strip(),
        f"--artifact-manifest {artifact_manifest}",
    ]
    if permission_note is not None:
        parts.append(f"--permission-note {permission_note}")
    if public_base_url:
        parts.append(f"--public-base-url {public_base_url}")
    if audit_report is not None:
        parts.append("--json")
        parts.append(f"--out {audit_report}")
    return " ".join(parts)


def author_review_command(
    *,
    probes: tuple[str, ...],
    artifact_manifest: Path,
    permission_note: Path | None,
    public_base_url: str | None,
    deploy_bundle_manifest: Path | None = None,
) -> str:
    parts = [".venv/bin/thread-search author-review --offline"]
    if public_base_url:
        parts.append(f"--public-base-url {public_base_url}")
    for probe in probes:
        parts.append(f"--probe {probe}")
    parts.append(f"--artifact-manifest {artifact_manifest}")
    if permission_note is not None:
        parts.append(f"--permission-note {permission_note}")
    if deploy_bundle_manifest is not None:
        parts.append(f"--deploy-bundle-manifest {deploy_bundle_manifest}")
    parts.append("--out data/author-review.md")
    return " ".join(parts)


def audit_report_summary(
    path: Path,
    *,
    artifact_manifest: Path,
    public_base_url: str | None,
) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "exists": False, "path": str(path), "reason": "missing"}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "exists": True, "path": str(path), "reason": "invalid_json", "error": str(exc)}
    if not isinstance(report, dict):
        return {"ok": False, "exists": True, "path": str(path), "reason": "invalid_shape"}
    items = report_items_by_key(report)
    problems: list[str] = []
    if report.get("ok") is not True:
        problems.append("audit report ok is not true")
    artifact_item = items.get("artifact_manifest")
    if artifact_item is None or artifact_item.get("status") != "pass":
        problems.append("artifact manifest audit item is missing or not passing")
    else:
        evidence = artifact_item.get("evidence") if isinstance(artifact_item.get("evidence"), dict) else {}
        if evidence.get("path") != str(artifact_manifest):
            problems.append("artifact manifest path does not match requested manifest")
    if public_base_url:
        public_smoke = items.get("public_smoke")
        if public_smoke is None or public_smoke.get("status") != "pass":
            problems.append("public smoke audit item is missing or not passing")
        else:
            evidence = public_smoke.get("evidence") if isinstance(public_smoke.get("evidence"), dict) else {}
            base_url = str(evidence.get("base_url") or "").rstrip("/")
            if base_url != public_base_url.rstrip("/"):
                problems.append("public smoke base URL does not match requested public base URL")
    return {
        "ok": not problems,
        "exists": True,
        "path": str(path),
        "generated_at_utc": report.get("generated_at_utc"),
        "problems": problems,
    }


def report_items_by_key(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = report.get("items")
    if not isinstance(items, list):
        return {}
    keyed: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            keyed[item["key"]] = item
    return keyed
