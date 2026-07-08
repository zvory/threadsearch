from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .deploy_bundle import BUNDLE_MANIFEST_NAME, DEFAULT_BUNDLE_DIR
from .nextstep import NextStep, artifact_command, final_audit_command, public_contact_args
from .permission import permission_note_summary


def render_runbook(
    payload: dict[str, Any],
    next_step: NextStep,
    *,
    expected_threadmarks: int,
    probes: tuple[str, ...],
    artifact_manifest: Path,
    permission_note: Path | None = None,
    public_base_url: str = "http://127.0.0.1:8765",
    audit_report: Path = Path("data/final-audit.json"),
    deploy_bundle_manifest: Path = DEFAULT_BUNDLE_DIR / BUNDLE_MANIFEST_NAME,
) -> str:
    crawl = payload["crawl"]
    corpus = payload["corpus"]
    index = payload["index"]
    fetch_log = payload.get("fetch_log", {"exists": False})
    validation = payload["validation"]
    launch = payload["launch_check"]
    permission = permission_note_summary(permission_note) if permission_note is not None else None
    generated = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    probe_args = " ".join(f"--probe {probe}" for probe in probes)
    artifact_export_command = artifact_command(probe_args=probe_args, permission_note=permission_note)
    contact_args = public_contact_args()
    final_audit = final_audit_command(
        probe_args=probe_args,
        artifact_manifest=artifact_manifest,
        permission_note=permission_note,
        public_base_url=None,
        audit_report=audit_report,
    )
    live_audit_command = final_audit_command(
        probe_args=probe_args,
        artifact_manifest=artifact_manifest,
        permission_note=permission_note,
        public_base_url=public_base_url,
        audit_report=audit_report,
    )

    lines = [
        "# Thread Search Operator Runbook",
        "",
        f"Generated: `{generated}`",
        "",
        "## Current State",
        "",
        f"- Reader root: `{crawl.get('reader_root')}`",
        f"- Robots allowed: `{crawl.get('robots_allowed')}`",
        f"- User agent: `{crawl.get('user_agent')}`",
        f"- Cached reader pages: `{crawl.get('cached_pages', 0)}` / `{crawl.get('page_count', 0)}`",
        f"- Network pages if run now: `{crawl.get('network_pages_if_run_now', 0)}`",
        f"- Fetch receipt entries: `{fetch_log.get('entries', 0)}`",
        f"- Logged page fetches: `{fetch_log.get('page_fetches', 0)}`",
        f"- Extracted threadmarks: `{corpus.get('threadmarks', 0)}` / `{expected_threadmarks}`",
        f"- Indexed threadmarks: `{index.get('threadmarks', 0)}`",
        f"- Indexed chunks: `{index.get('chunks', 0)}`",
        f"- Corpus categories: `{corpus.get('categories', [])}`",
        f"- Validation passed: `{validation.get('ok')}`",
        f"- Launch check passed: `{launch.get('ok')}`",
    ]
    if permission_note is not None and permission is not None:
        lines.extend(
            [
                f"- Permission note: `{permission_note}`",
                f"- Permission note passed: `{permission.get('ok')}`",
            ]
        )
    lines.extend(["", "## Next Command", ""])
    if next_step.command:
        lines.extend(
            [
                f"`{next_step.key}`: {next_step.summary}",
                "",
                "```sh",
                next_step.command,
                "```",
            ]
        )
    else:
        lines.extend([f"`{next_step.key}`: {next_step.summary}", "", "No command is recommended."])

    if next_step.reasons:
        lines.extend(["", "Reasons:", ""])
        lines.extend(f"- {reason}" for reason in next_step.reasons)

    if permission_note is not None and permission is not None:
        lines.extend(render_permission_gate(permission_note, permission))

    lines.extend(
        [
            "",
            "## Safe Crawl Loop",
            "",
            "Run this after each step to get the next command:",
            "",
            "```sh",
            ".venv/bin/thread-search next-step --offline",
            "```",
            "",
            "The prefetch phase should fetch at most one uncached reader page per command. After all reader pages are cached, rebuild from cache only:",
            "",
            "```sh",
            f".venv/bin/thread-search build --offline {probe_args}".strip(),
            "```",
            "",
            "## Public Launch Sequence",
            "",
            "After the offline build passes:",
            "",
            "If the permission note does not exist yet, create the template:",
            "",
            "```sh",
            f".venv/bin/thread-search permission-note --out {permission_note}".strip()
            if permission_note is not None
            else ".venv/bin/thread-search permission-note --out data/permission-note.md",
            "```",
            "",
            "Generate a no-story-text permission request draft to send before filling in approval evidence:",
            "",
            "```sh",
            ".venv/bin/thread-search permission-request --out data/permission-request.md "
            f"--public-base-url {public_base_url} --operator \"Your handle\"",
            "```",
            "",
            "Snapshot the current machine-readable robots posture and official policy URLs before filling the site-rules section:",
            "",
            "```sh",
            ".venv/bin/thread-search site-review --refresh --delay 30 --out data/site-policy-review.md",
            ".venv/bin/thread-search site-review --offline --out data/site-policy-review.md",
            "```",
            "",
            "After editing the note, check it and then export the private backend artifact:",
            "",
            "```sh",
            f".venv/bin/thread-search permission-note --check --out {permission_note}".strip()
            if permission_note is not None
            else ".venv/bin/thread-search permission-note --check --out data/permission-note.md",
            f".venv/bin/thread-search launch-check {probe_args}".strip(),
            artifact_export_command,
            final_audit,
            ".venv/bin/thread-search deploy-bundle",
            f".venv/bin/thread-search deploy-bundle-check --manifest {deploy_bundle_manifest}",
            "```",
            "",
            "Set THREAD_SEARCH_PUBLIC_CONTACT and THREAD_SEARCH_REMOVAL_REQUEST_URL before exporting or serving publicly.",
            "The deploy bundle step writes a public-safe app tarball and a private artifact tarball under `dist/deploy-bundles/`; the check step verifies checksums, tar contents, and public/private separation. Do not publish the private artifact tarball.",
            "",
            "For a public web process, mount the artifact database privately and keep full-text routes disabled:",
            "",
            "```sh",
            (
                ".venv/bin/thread-search serve --db dist/thread-search-public/thread-search.sqlite "
                f"--host 127.0.0.1 --port 8765 --require-launch-ready "
                f"--require-artifact-manifest --artifact-manifest {artifact_manifest} "
                f"{contact_args} {probe_args}"
            ).strip(),
            "```",
            "",
            "Production deployment examples are included in the repository:",
            "",
            "- `compose.yaml`: read-only artifact mount, loopback bind, manifest-gated startup.",
            "- `deploy/nginx-thread-search.conf.example`: HTTPS proxy, API rate limit, noindex/security headers, private-path blocks.",
            "- `deploy/systemd/thread-search.service.example`: non-Docker VPS service, loopback bind, manifest-gated startup, systemd hardening.",
            "- `deploy/systemd/thread-search.env.example`: contact/removal metadata template that must be replaced before start.",
            "",
            "After the process is reachable, smoke-test the live HTTP surface:",
            "",
            "```sh",
            f".venv/bin/thread-search public-smoke --base-url {public_base_url} --require-artifact-manifest {probe_args}".strip(),
            "```",
            "",
            "For a short-lived public author-review URL from this local machine, use the preview helper. It starts the same manifest-gated loopback server, then opens an optional localtunnel URL through `npx`:",
            "",
            "```sh",
            f".venv/bin/thread-search preview-start {probe_args}".strip(),
            f".venv/bin/thread-search preview-status --smoke {probe_args}".strip(),
            "```",
            "",
            "When the review window closes:",
            "",
            "```sh",
            ".venv/bin/thread-search preview-stop",
            "```",
            "",
            "The live audit runs its own public smoke pass. With the default 60 requests/minute per-IP limiter, wait a minute between a standalone public-smoke run and a live audit, or restart the local loopback process before the audit.",
            "",
            "For a combined final evidence record, rerun the audit with the live base URL:",
            "",
            "```sh",
            live_audit_command,
            "```",
            "",
            "Generate a no-story-text author review packet with prototype links, safety scope, and verification hashes:",
            "",
            "```sh",
            (
                ".venv/bin/thread-search author-review --offline "
                f"--public-base-url {public_base_url} "
                f"--artifact-manifest {artifact_manifest} "
                f"--permission-note {permission_note or 'data/permission-note.md'} "
                f"--deploy-bundle-manifest {deploy_bundle_manifest} "
                f"{probe_args} "
                "--out data/author-review.md"
            ).strip(),
            "```",
            "",
            "## Guardrails",
            "",
            "- Do not commit or publish `data/`, `dist/`, cached HTML, extracted JSONL, or the SQLite database.",
            "- Do not serve `dist/thread-search-public/` as static files.",
            "- Do not deploy publicly until the permission note is complete and the final audit passes.",
            "- Keep `--private-fulltext` off for public deployments unless explicit permission covers full-text redistribution.",
            "- Keep public result, mention-window, query-length, and rate-limit caps enabled.",
            "- Do not send thread text to hosted embedding or LLM APIs unless author permission and site rules explicitly allow it.",
        ]
    )
    if validation.get("errors"):
        lines.extend(["", "## Current Validation Errors", ""])
        lines.extend(f"- {error}" for error in validation["errors"])
    if launch.get("errors"):
        lines.extend(["", "## Current Launch Errors", ""])
        lines.extend(f"- {error}" for error in launch["errors"])

    return "\n".join(lines)


def render_permission_gate(permission_note: Path, permission: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Permission Evidence Gate",
        "",
        f"- Note path: `{permission_note}`",
        f"- Exists: `{permission.get('exists')}`",
        f"- Passed: `{permission.get('ok')}`",
    ]
    if permission.get("exists") is not True:
        lines.extend(
            [
                "- Artifact export blocked: create the permission note template before exporting.",
                "",
                "```sh",
                f".venv/bin/thread-search permission-note --out {permission_note}",
                "```",
            ]
        )
        return lines

    if permission.get("ok") is True:
        if permission.get("sha256"):
            lines.append(f"- Evidence hash: `{permission.get('sha256')}`")
        if permission.get("bytes") is not None:
            lines.append(f"- Note bytes: `{permission.get('bytes')}`")
        lines.append("- Artifact export may proceed after launch checks and final audit still pass.")
        return lines

    lines.extend(
        [
            "- Artifact export blocked: complete the note, keep the named checklist items, then rerun the check.",
            "- No story text is required in this note; record permission, site-rule review, scope, and operator decision evidence.",
            "",
            "```sh",
            f".venv/bin/thread-search permission-note --check --out {permission_note}",
            "```",
        ]
    )
    extend_issue_list(lines, "Missing sections", permission.get("missing_sections"))
    extend_issue_list(lines, "Missing checklist items", permission.get("missing_required_items"))
    extend_issue_list(lines, "Placeholders", permission.get("placeholders"))
    extend_issue_list(lines, "Unchecked checklist items", permission.get("unchecked_items"))
    invalid_details = [
        f"{item.get('label', 'unknown')} ({item.get('reason', 'invalid')})"
        for item in permission.get("invalid_checklist_details", [])
        if isinstance(item, dict)
    ]
    extend_issue_list(lines, "Invalid checklist details", invalid_details)
    decision = permission.get("deployment_decision")
    if isinstance(decision, dict):
        reason = decision.get("reason", "unknown")
        detail = decision.get("detail", "")
        suffix = f"; detail: `{detail}`" if detail else ""
        lines.extend(["", "Deployment decision:", "", f"- `{reason}`{suffix}"])
    return lines


def extend_issue_list(lines: list[str], title: str, values: object) -> None:
    if not isinstance(values, list) or not values:
        return
    lines.extend(["", f"{title}:", ""])
    lines.extend(f"- `{value}`" for value in values)
