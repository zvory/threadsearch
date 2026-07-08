from planquest.deploy_policy import public_cap_errors, public_contact_errors


def valid_caps() -> dict[str, int]:
    return {
        "public_search_limit": 30,
        "public_report_limit": 100,
        "public_mention_limit": 50,
        "public_threadmark_limit": 300,
        "max_query_chars": 120,
        "mention_window_chars": 320,
        "public_snippet_budget_chars": 6000,
        "public_rate_limit_per_minute": 60,
    }


def test_public_cap_errors_accepts_default_caps() -> None:
    assert public_cap_errors(valid_caps()) == []


def test_public_cap_errors_rejects_disabled_rate_limit() -> None:
    caps = valid_caps()
    caps["public_rate_limit_per_minute"] = 0

    assert public_cap_errors(caps) == [
        "public-rate-limit-per-minute must be at least 1 for public deployment; got 0"
    ]


def test_public_cap_errors_rejects_missing_values() -> None:
    caps = valid_caps()
    del caps["public_search_limit"]

    assert public_cap_errors(caps) == [
        "public-search-limit must be an integer between 1 and 100; got None"
    ]


def test_public_contact_errors_accepts_mailto_and_https() -> None:
    assert public_contact_errors("mailto:operator@thread-search.example", "https://thread-search.example/removal") == []


def test_public_contact_errors_rejects_missing_values() -> None:
    assert public_contact_errors("", "") == [
        "public-contact is required for public deployment",
        "removal-request-url is required for public deployment",
    ]


def test_public_contact_errors_rejects_placeholder_values() -> None:
    assert public_contact_errors("mailto:operator@example.invalid", "https://search.example.invalid/removal") == [
        "public-contact must not be a placeholder for public deployment; got 'mailto:operator@example.invalid'",
        "removal-request-url must not be a placeholder for public deployment; got 'https://search.example.invalid/removal'",
    ]


def test_public_contact_errors_rejects_opaque_values() -> None:
    assert public_contact_errors("local operator", "ask in thread") == [
        "public-contact must be a mailto:, http(s) URL, or email address for public deployment; got 'local operator'",
        "removal-request-url must be a mailto:, http(s) URL, or email address for public deployment; got 'ask in thread'",
    ]
