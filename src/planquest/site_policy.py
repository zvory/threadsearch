from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

from .config import BLOCKED_AI_USER_AGENT_TOKENS, MAIN_THREADMARK_CATEGORY_ID, TARGET_READER_URL
from .fetch import PoliteFetcher
from .scrape import normalize_reader_root


POLICY_PAGES = (
    {
        "key": "help",
        "title": "Help and policy landing page",
        "url": "https://forums.sufficientvelocity.com/help/",
    },
    {
        "key": "terms_rules_changelog",
        "title": "Rules, Terms of Service, Staff List, and Changelog",
        "url": "https://forums.sufficientvelocity.com/threads/rules-terms-of-service-staff-list-changelog.575/",
    },
    {
        "key": "rules_procedures",
        "title": "The Rules and Procedures of Sufficient Velocity",
        "url": "https://forums.sufficientvelocity.com/threads/the-rules-and-procedures-of-sufficient-velocity.40100/",
    },
    {
        "key": "commerce",
        "title": "Advertising and Commercial Use Policy",
        "url": "https://forums.sufficientvelocity.com/help/commerce/",
    },
)

PROBE_PATHS = (
    ("reader", "Target thread reader", None),
    ("login", "Login endpoint", "/login/"),
    ("account", "Account endpoint", "/account/"),
    ("search", "Search endpoint", "/search/"),
    ("attachments", "Attachment endpoint", "/attachments/"),
)


@dataclass(frozen=True)
class RobotProbe:
    key: str
    label: str
    url: str
    allowed: bool


@dataclass(frozen=True)
class UserAgentProbe:
    user_agent: str
    url: str
    allowed: bool


@dataclass(frozen=True)
class SitePolicyReview:
    kind: str
    metadata_only: bool
    generated_at_utc: str
    reader_root: str
    robots_url: str
    user_agent: str
    robots_probes: list[RobotProbe]
    ai_user_agent_probes: list[UserAgentProbe]
    policy_pages: tuple[dict[str, str], ...]
    operator_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def make_site_policy_review(
    fetcher: PoliteFetcher,
    *,
    url: str = TARGET_READER_URL,
    category_id: int = MAIN_THREADMARK_CATEGORY_ID,
) -> SitePolicyReview:
    reader_root = normalize_reader_root(url, category_id=category_id)
    robots_url = fetcher.robots_url(reader_root)
    robots_probes = [
        RobotProbe(
            key=key,
            label=label,
            url=reader_root if path is None else same_origin_url(reader_root, path),
            allowed=fetcher.can_fetch(reader_root if path is None else same_origin_url(reader_root, path)),
        )
        for key, label, path in PROBE_PATHS
    ]
    ai_user_agent_probes = [
        UserAgentProbe(user_agent=user_agent, url=reader_root, allowed=fetcher.can_fetch(reader_root, user_agent=user_agent))
        for user_agent in sorted(BLOCKED_AI_USER_AGENT_TOKENS)
    ]
    return SitePolicyReview(
        kind="thread-search-site-policy-review",
        metadata_only=True,
        generated_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        reader_root=reader_root,
        robots_url=robots_url,
        user_agent=fetcher.user_agent,
        robots_probes=robots_probes,
        ai_user_agent_probes=ai_user_agent_probes,
        policy_pages=POLICY_PAGES,
        operator_notes=(
            "Review the official policy pages manually before filling in data/permission-note.md.",
            "This snapshot only records robots.txt decisions and policy URLs; it does not grant redistribution permission.",
            "Keep public deployment source-linked, noindex, and without public corpus/database downloads unless explicit permission says otherwise.",
        ),
    )


def same_origin_url(base_url: str, path: str) -> str:
    parsed = urlparse(base_url)
    normalized_path = path if path.startswith("/") else f"/{path}"
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


def render_site_policy_review_markdown(review: SitePolicyReview) -> str:
    lines = [
        "# Sufficient Velocity Site Policy Review Snapshot",
        "",
        f"Generated: `{review.generated_at_utc}`",
        f"Reader root: `{review.reader_root}`",
        f"Robots URL: `{review.robots_url}`",
        f"Configured user agent: `{review.user_agent}`",
        "",
        "## Robots Checks",
        "",
    ]
    for probe in review.robots_probes:
        status = "allowed" if probe.allowed else "blocked"
        lines.append(f"- `{probe.key}`: {status} - {probe.label} - `{probe.url}`")

    lines.extend(["", "## AI User-Agent Checks", ""])
    for probe in review.ai_user_agent_probes:
        status = "allowed" if probe.allowed else "blocked"
        lines.append(f"- `{probe.user_agent}`: {status} for `{probe.url}`")

    lines.extend(["", "## Official Policy Pages To Review", ""])
    for page in review.policy_pages:
        lines.append(f"- [{page['title']}]({page['url']})")

    lines.extend(["", "## Operator Notes", ""])
    lines.extend(f"- {note}" for note in review.operator_notes)
    return "\n".join(lines)
