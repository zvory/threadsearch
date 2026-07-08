from pathlib import Path

import pytest

from planquest.permission import (
    DEFAULT_PERMISSION_NOTE,
    REQUIRED_CHECKLIST_ITEMS,
    REQUIRED_SECTIONS,
    permission_note_summary,
    render_permission_note_template,
    render_permission_request_template,
    write_permission_note_template,
    write_permission_request_template,
)


def concrete_detail(item: str) -> str:
    details = {
        "Permission source": "Author forum PM confirming snippet search on 2026-07-08.",
        "Permission date": "2026-07-08.",
        "Permission covers public source-linked search": "Author approved source-linked search hits back to Sufficient Velocity.",
        "Permission does not cover public full-text redistribution unless explicitly recorded here": "No public full-text redistribution approved.",
        "Sufficient Velocity rules or policy pages reviewed": "Reviewed Sufficient Velocity terms and rules pages at https://forums.sufficientvelocity.com/ on 2026-07-08.",
        "Review date": "2026-07-08.",
        "Limits affecting deployment, crawling, snippets, indexing, or attribution": "Keep snippets bounded, noindex enabled, and source links visible.",
        "Public access is source-linked search": "Public UI and API expose source-linked search hits.",
        "Full-text threadmark routes are disabled": "Public server runs without --private-fulltext.",
        "SQLite database remains private server-side, not static/downloadable": "Artifact database is mounted privately behind the server.",
        "Search-engine indexing remains blocked unless explicitly allowed": "X-Robots-Tag noindex and disallow-all robots.txt remain enabled.",
        "Decision to proceed or not proceed": "proceed with public source-linked search.",
        "Operator name or handle": "Test Operator.",
        "Decision date": "2026-07-08.",
    }
    return details[item]


def valid_note_text() -> str:
    sections = "\n\n".join(f"## {section}\nReviewed." for section in REQUIRED_SECTIONS)
    checklist_items = [f"- [x] {item}: {concrete_detail(item)}" for item in REQUIRED_CHECKLIST_ITEMS]
    checklist = "\n".join(checklist_items)
    return f"# Thread Search Permission Note\n\n{sections}\n\n{checklist}\n"


def test_permission_note_template_is_not_complete() -> None:
    template = render_permission_note_template("https://example.invalid/reader/")
    assert "https://example.invalid/reader/" in template
    assert "TODO" in template
    assert "- [ ]" in template
    assert str(DEFAULT_PERMISSION_NOTE) == "data/permission-note.md"


def test_permission_note_summary_passes_complete_note(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    note.write_text(valid_note_text(), encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["provided"] is True
    assert summary["exists"] is True
    assert summary["ok"] is True
    assert summary["missing_sections"] == []
    assert summary["missing_required_items"] == []
    assert summary["placeholders"] == []
    assert summary["unchecked_checkboxes"] == 0
    assert summary["unchecked_items"] == []
    assert summary["invalid_checklist_details"] == []
    assert summary["deployment_decision"]["ok"] is True
    assert summary["deployment_decision"]["reason"] == "affirmative"
    assert summary["sha256"]


def test_permission_note_summary_rejects_placeholders_and_missing_sections(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    note.write_text("## Author Permission\nTODO\n", encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert "Site Rules Review" in summary["missing_sections"]
    assert summary["placeholders"] == ["TODO"]


def test_permission_note_summary_rejects_vague_checked_note(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    sections = "\n\n".join(f"## {section}\n- [x] Recorded and reviewed." for section in REQUIRED_SECTIONS)
    note.write_text(f"# Thread Search Permission Note\n\n{sections}\n", encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert "Permission source" in summary["missing_required_items"]
    assert "Decision date" in summary["missing_required_items"]


def test_permission_note_summary_rejects_unchecked_checkboxes(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    sections = "\n\n".join(f"## {section}\n- [ ] Recorded and reviewed." for section in REQUIRED_SECTIONS)
    note.write_text(f"# Thread Search Permission Note\n\n{sections}\n", encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert summary["missing_sections"] == []
    assert summary["placeholders"] == []
    assert summary["unchecked_checkboxes"] == len(REQUIRED_SECTIONS)
    assert summary["unchecked_items"] == ["Recorded and reviewed."] * len(REQUIRED_SECTIONS)


def test_permission_note_summary_rejects_vague_required_detail(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    text = valid_note_text().replace(
        "Permission source: Author forum PM confirming snippet search on 2026-07-08.",
        "Permission source: recorded evidence.",
    )
    note.write_text(text, encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert summary["invalid_checklist_details"] == [
        {"label": "Permission source", "reason": "vague_detail", "detail": "recorded evidence."}
    ]


def test_permission_note_summary_rejects_non_iso_dates(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    text = valid_note_text().replace("Permission date: 2026-07-08.", "Permission date: July 8, 2026.")
    note.write_text(text, encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert summary["invalid_checklist_details"] == [
        {"label": "Permission date", "reason": "missing_iso_date", "detail": "July 8, 2026."}
    ]


def test_permission_note_summary_rejects_negative_deployment_decision(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    text = valid_note_text().replace(
        "Decision to proceed or not proceed: proceed with public source-linked search.",
        "Decision to proceed or not proceed: do not deploy publicly.",
    )
    note.write_text(text, encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert summary["missing_sections"] == []
    assert summary["missing_required_items"] == []
    assert summary["placeholders"] == []
    assert summary["unchecked_checkboxes"] == 0
    assert summary["deployment_decision"]["ok"] is False
    assert summary["deployment_decision"]["reason"] == "negative"


def test_permission_note_summary_rejects_unclear_deployment_decision(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    text = valid_note_text().replace(
        "Decision to proceed or not proceed: proceed with public source-linked search.",
        "Decision to proceed or not proceed: recorded evidence.",
    )
    note.write_text(text, encoding="utf-8")

    summary = permission_note_summary(note)

    assert summary["ok"] is False
    assert summary["deployment_decision"]["ok"] is False
    assert summary["deployment_decision"]["reason"] == "unclear"


def test_write_permission_note_template_refuses_existing_file(tmp_path: Path) -> None:
    note = tmp_path / "permission.md"
    write_permission_note_template(note)

    with pytest.raises(FileExistsError):
        write_permission_note_template(note)

    write_permission_note_template(note, overwrite=True)
    assert "TODO" in note.read_text(encoding="utf-8")


def test_permission_request_template_describes_snippet_only_scope() -> None:
    template = render_permission_request_template(
        source_reader_url="https://example.invalid/reader/",
        public_base_url="https://search.example.invalid",
        operator="Test Operator",
        contact="operator@example.invalid",
    )

    assert "https://example.invalid/reader/" in template
    assert "https://search.example.invalid" in template
    assert "Test Operator" in template
    assert "operator@example.invalid" in template
    assert "Public source-linked search hits grouped by the threadmark" in template
    assert "Search over the main thread text with source links" in template
    assert "Metadata-only topic comparison views" not in template
    assert "Metadata-only indexed-term browsing" not in template
    assert "Metadata-only query explanation views" not in template
    assert "Bounded topic dossiers, evidence packs, recap views, and claim checks" not in template
    assert "metadata-only term/comparison caps" not in template
    assert "term, coverage, comparison, bounded topic, recap, evidence-pack, and claim diagnostics" not in template
    assert "No public full-text threadmark pages" in template
    assert "No hosted LLM or embedding API calls with the thread text" in template
    assert "Main Threadmarks only; Sidestory and Apocrypha excluded" in template


def test_write_permission_request_template_refuses_existing_file(tmp_path: Path) -> None:
    request = tmp_path / "permission-request.md"
    write_permission_request_template(request, operator="Test Operator")

    with pytest.raises(FileExistsError):
        write_permission_request_template(request)

    write_permission_request_template(request, overwrite=True, public_base_url="https://search.example.invalid")
    assert "https://search.example.invalid" in request.read_text(encoding="utf-8")
