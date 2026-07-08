from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re
from typing import Any

from .config import TARGET_READER_URL

DEFAULT_PERMISSION_NOTE = Path("data/permission-note.md")
REQUIRED_SECTIONS = (
    "Author Permission",
    "Site Rules Review",
    "Public Deployment Scope",
    "Operator Decision",
)
REQUIRED_CHECKLIST_ITEMS = (
    "Permission source",
    "Permission date",
    "Permission covers public snippet search with source links",
    "Permission does not cover public full-text redistribution unless explicitly recorded here",
    "Sufficient Velocity rules or policy pages reviewed",
    "Review date",
    "Limits affecting deployment, crawling, snippets, indexing, or attribution",
    "Public access is snippet-only and source-linked",
    "Full-text threadmark routes are disabled",
    "SQLite database remains private server-side, not static/downloadable",
    "Search-engine indexing remains blocked unless explicitly allowed",
    "Decision to proceed or not proceed",
    "Operator name or handle",
    "Decision date",
)
PLACEHOLDER_MARKERS = ("TODO", "REPLACE")
UNCHECKED_CHECKBOX_RE = re.compile(r"(?m)^\s*[-*]\s+\[\s\]\s+")
UNCHECKED_CHECKBOX_ITEM_RE = re.compile(r"(?m)^\s*[-*]\s+\[\s\]\s+(.+?)\s*$")
CHECKBOX_ITEM_RE = re.compile(r"(?m)^\s*[-*]\s+\[(?P<checked>[ xX])\]\s+(?P<item>.+?)\s*$")
DEPLOYMENT_DECISION_LABEL = "Decision to proceed or not proceed"
DATE_CHECKLIST_LABELS = frozenset({"Permission date", "Review date", "Decision date"})
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
VAGUE_DETAIL_RE = re.compile(
    r"^(?:recorded evidence|reviewed|done|yes|ok|okay|complete|completed|checked|n/?a|none)\.?$",
    re.IGNORECASE,
)
AFFIRMATIVE_DECISION_RE = re.compile(
    r"\b(?:proceed|approved|allowed|allow|deploy|launch|publish|public\s+deployment|yes)\b",
    re.IGNORECASE,
)
NEGATIVE_DECISION_RE = re.compile(
    r"\b(?:do\s+not|don't|not|no)\s+(?:proceed|deploy|launch|publish|approve|allow|allowed|public)|"
    r"\b(?:denied|rejected|refused|blocked|private\s+only|keep\s+private|not\s+approved|not\s+allowed)\b",
    re.IGNORECASE,
)


def render_permission_note_template(source_reader_url: str = TARGET_READER_URL) -> str:
    return f"""# Thread Search Permission Note

Source reader URL: {source_reader_url}
Prepared at: {datetime.now(UTC).isoformat().replace("+00:00", "Z")}

## Author Permission

- [ ] Permission source: TODO
- [ ] Permission date: TODO
- [ ] Permission covers public snippet search with source links: TODO
- [ ] Permission does not cover public full-text redistribution unless explicitly recorded here: TODO

## Site Rules Review

- [ ] Sufficient Velocity rules or policy pages reviewed: TODO (include the `site-review` snapshot path/hash and the official policy URLs reviewed)
- [ ] Review date: TODO
- [ ] Limits affecting deployment, crawling, snippets, indexing, or attribution: TODO

## Public Deployment Scope

- [ ] Public access is snippet-only and source-linked: TODO
- [ ] Full-text threadmark routes are disabled: TODO
- [ ] SQLite database remains private server-side, not static/downloadable: TODO
- [ ] Search-engine indexing remains blocked unless explicitly allowed: TODO

## Operator Decision

- [ ] Decision to proceed or not proceed: TODO (write "proceed" only after the evidence above allows public snippet search)
- [ ] Operator name or handle: TODO
- [ ] Decision date: TODO
"""


def render_permission_request_template(
    *,
    source_reader_url: str = TARGET_READER_URL,
    public_base_url: str = "not deployed yet",
    operator: str = "local operator",
    contact: str = "",
) -> str:
    contact_line = f"Contact: {contact}\n" if contact else ""
    return f"""# Thread Search Public Snippet Search Permission Request

Hello,

I am preparing a searchable index for the configured main Threadmarks reader so readers can find where topics are mentioned and follow links back to the original posts.

Source reader URL: {source_reader_url}
Planned public URL: {public_base_url}
Operator: {operator}
{contact_line}
Requested permission:

- Public snippet search with links back to the original Sufficient Velocity posts.
- Metadata-only coverage views for matching threadmark titles, dates, authors, source links, and hit counts.
- Metadata-only topic comparison views for coverage and overlap counts.
- Metadata-only indexed-term browsing for vocabulary counts and prefix discovery.
- Metadata-only query explanation views for exact counts, prefix counts, per-term breakdowns, suggestions, and cautions.
- Bounded topic dossiers, evidence packs, recap views, and claim checks that return short snippets and source links, not full chapter text.
- Server-side private SQLite index storage so the database is not offered as a public download.

Safety limits I plan to keep enabled:

- No public full-text threadmark pages unless you explicitly approve full-text redistribution.
- No static download of raw HTML, extracted JSONL, or the SQLite database.
- Search-engine indexing blocked with noindex headers and disallow-all robots.txt.
- Rate limits, query length limits, result caps, metadata-only term/comparison caps, and aggregate snippet-character budgets.
- Source attribution and links to Sufficient Velocity shown in the UI and API output.
- Main Threadmarks only; Sidestory and Apocrypha excluded.
- No hosted LLM or embedding API calls with the thread text unless you and site rules explicitly allow that later.

Please reply with whether this snippet-search deployment is allowed, and note any limits you want me to follow around snippets, indexing, attribution, hosting, commercial use, or takedown/removal requests. If Sufficient Velocity policy or your preference says not to deploy it publicly, I will keep the tool private.

If you approve, please explicitly confirm whether permission covers:

- Public snippet search with source links.
- Public metadata-only term, coverage, comparison, bounded topic, recap, evidence-pack, and claim diagnostics.
- Public full-text redistribution. I will treat this as not approved unless you say yes explicitly.

Thank you.
"""


def write_permission_request_template(
    path: Path,
    *,
    overwrite: bool = False,
    source_reader_url: str = TARGET_READER_URL,
    public_base_url: str = "not deployed yet",
    operator: str = "local operator",
    contact: str = "",
) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_permission_request_template(
            source_reader_url=source_reader_url,
            public_base_url=public_base_url,
            operator=operator,
            contact=contact,
        ),
        encoding="utf-8",
    )


def write_permission_note_template(path: Path, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_permission_note_template(), encoding="utf-8")


def permission_note_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"provided": False, "exists": False, "ok": False, "path": str(path)}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return {"provided": True, "exists": True, "ok": False, "path": str(path), "error": str(exc)}

    missing_sections = [section for section in REQUIRED_SECTIONS if f"## {section}" not in text]
    placeholders = [marker for marker in PLACEHOLDER_MARKERS if marker in text]
    unchecked_items = [item.strip() for item in UNCHECKED_CHECKBOX_ITEM_RE.findall(text)]
    unchecked_checkboxes = len(unchecked_items)
    checklist_items = checkbox_items_by_label(text)
    missing_required_items = [
        item
        for item in REQUIRED_CHECKLIST_ITEMS
        if item not in checklist_items
    ]
    invalid_checklist_details = checklist_detail_issues(checklist_items)
    deployment_decision = deployment_decision_summary(checklist_items)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "provided": True,
        "exists": True,
        "ok": (
            not missing_sections
            and not placeholders
            and unchecked_checkboxes == 0
            and not missing_required_items
            and not invalid_checklist_details
            and deployment_decision["ok"]
        ),
        "path": str(path),
        "sha256": digest,
        "bytes": len(text.encode("utf-8")),
        "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "missing_sections": missing_sections,
        "missing_required_items": missing_required_items,
        "placeholders": placeholders,
        "unchecked_checkboxes": unchecked_checkboxes,
        "unchecked_items": unchecked_items,
        "invalid_checklist_details": invalid_checklist_details,
        "deployment_decision": deployment_decision,
    }


def checkbox_items_by_label(text: str) -> dict[str, str]:
    items: dict[str, str] = {}
    for match in CHECKBOX_ITEM_RE.finditer(text):
        item = match.group("item").strip()
        label = item.split(":", 1)[0].strip()
        if label:
            items[label] = item
    return items


def deployment_decision_summary(checklist_items: dict[str, str]) -> dict[str, Any]:
    item = checklist_items.get(DEPLOYMENT_DECISION_LABEL, "")
    if not item:
        return {"ok": False, "item": "", "detail": "", "reason": "missing"}
    detail = checklist_item_detail(item)
    if not detail:
        return {"ok": False, "item": item, "detail": detail, "reason": "empty"}
    if NEGATIVE_DECISION_RE.search(detail):
        return {"ok": False, "item": item, "detail": detail, "reason": "negative"}
    if AFFIRMATIVE_DECISION_RE.search(detail):
        return {"ok": True, "item": item, "detail": detail, "reason": "affirmative"}
    return {"ok": False, "item": item, "detail": detail, "reason": "unclear"}


def checklist_detail_issues(checklist_items: dict[str, str]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for label in REQUIRED_CHECKLIST_ITEMS:
        item = checklist_items.get(label)
        if not item:
            continue
        detail = checklist_item_detail(item)
        normalized = " ".join(detail.split())
        if not normalized:
            issues.append({"label": label, "reason": "missing_detail", "detail": detail})
            continue
        if VAGUE_DETAIL_RE.fullmatch(normalized):
            issues.append({"label": label, "reason": "vague_detail", "detail": detail})
        if label in DATE_CHECKLIST_LABELS and ISO_DATE_RE.search(detail) is None:
            issues.append({"label": label, "reason": "missing_iso_date", "detail": detail})
    return issues


def checklist_item_detail(item: str) -> str:
    return item.split(":", 1)[1].strip() if ":" in item else ""
