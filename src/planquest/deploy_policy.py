from __future__ import annotations

import re
from typing import Mapping
from urllib.parse import urlparse


PUBLIC_CAP_LIMITS = {
    "public_search_limit": (1, 100),
    "public_report_limit": (1, 300),
    "public_mention_limit": (1, 200),
    "public_threadmark_limit": (1, 500),
    "max_query_chars": (1, 240),
    "mention_window_chars": (1, 600),
    "public_snippet_budget_chars": (1, 20000),
    "public_rate_limit_per_minute": (1, 600),
}
PLACEHOLDER_CONTACT_MARKERS = (
    "todo",
    "replace",
    "example.invalid",
    "example.com",
    "example.org",
    "example.net",
    "your-public-host.example",
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def public_cap_errors(values: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    for name, (minimum, maximum) in PUBLIC_CAP_LIMITS.items():
        raw_value = values.get(name)
        label = name.replace("_", "-")
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            errors.append(f"{label} must be an integer between {minimum} and {maximum}; got {raw_value!r}")
            continue
        if value < minimum:
            errors.append(f"{label} must be at least {minimum} for public deployment; got {value}")
        elif value > maximum:
            errors.append(f"{label} must be at most {maximum} for public deployment; got {value}")
    return errors


def public_contact_errors(
    public_contact: str,
    removal_request_url: str,
    *,
    context: str = "public deployment",
) -> list[str]:
    errors: list[str] = []
    errors.extend(contact_value_errors("public-contact", public_contact, context=context))
    errors.extend(contact_value_errors("removal-request-url", removal_request_url, context=context))
    return errors


def contact_value_errors(label: str, value: str, *, context: str) -> list[str]:
    cleaned = value.strip()
    if not cleaned:
        return [f"{label} is required for {context}"]
    lowered = cleaned.lower()
    if any(marker in lowered for marker in PLACEHOLDER_CONTACT_MARKERS):
        return [f"{label} must not be a placeholder for {context}; got {cleaned!r}"]
    if not is_contact_value(cleaned):
        return [f"{label} must be a mailto:, http(s) URL, or email address for {context}; got {cleaned!r}"]
    return []


def is_contact_value(value: str) -> bool:
    if EMAIL_RE.fullmatch(value):
        return True
    parsed = urlparse(value)
    if parsed.scheme == "mailto":
        return bool(parsed.path and EMAIL_RE.fullmatch(parsed.path))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
