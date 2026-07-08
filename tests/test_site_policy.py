import json
from pathlib import Path

from planquest import cli
from planquest.config import BLOCKED_AI_USER_AGENT_TOKENS, TARGET_READER_URL
from planquest.fetch import PoliteFetcher
from planquest.site_policy import make_site_policy_review, render_site_policy_review_markdown


def write_cached_robots(cache_dir: Path) -> None:
    robots = cache_dir / "robots" / "forums.sufficientvelocity.com.txt"
    robots.parent.mkdir(parents=True)
    lines: list[str] = []
    for user_agent in sorted(BLOCKED_AI_USER_AGENT_TOKENS):
        lines.extend([f"User-agent: {user_agent}", "Disallow: /", ""])
    lines.extend(
        [
            "User-agent: *",
            "Disallow: /account/",
            "Disallow: /attachments/",
            "Disallow: /login/",
            "Disallow: /search/",
        ]
    )
    robots.write_text("\n".join(lines), encoding="utf-8")


def test_site_policy_review_is_metadata_only_from_cached_robots(tmp_path: Path) -> None:
    write_cached_robots(tmp_path)
    fetcher = PoliteFetcher(tmp_path, "thread-search-test", offline=True)

    review = make_site_policy_review(fetcher, url=TARGET_READER_URL)
    payload = review.to_dict()

    assert payload["kind"] == "thread-search-site-policy-review"
    assert payload["metadata_only"] is True
    assert payload["reader_root"] == TARGET_READER_URL
    robots = {item["key"]: item["allowed"] for item in payload["robots_probes"]}
    assert robots["reader"] is True
    assert robots["login"] is False
    assert robots["account"] is False
    assert robots["search"] is False
    assert robots["attachments"] is False
    assert all(item["allowed"] is False for item in payload["ai_user_agent_probes"])
    assert "https://forums.sufficientvelocity.com/help/" in {
        item["url"] for item in payload["policy_pages"]
    }


def test_site_policy_review_markdown_lists_policy_urls_without_story_text(tmp_path: Path) -> None:
    write_cached_robots(tmp_path)
    fetcher = PoliteFetcher(tmp_path, "thread-search-test", offline=True)

    rendered = render_site_policy_review_markdown(make_site_policy_review(fetcher))

    assert "# Sufficient Velocity Site Policy Review Snapshot" in rendered
    assert "Target thread reader" in rendered
    assert "Official Policy Pages To Review" in rendered
    assert "Advertising and Commercial Use Policy" in rendered
    assert "snippet-only" in rendered
    assert "bbWrapper" not in rendered
    assert "body" not in rendered
    assert "snippet" not in rendered.lower().replace("snippet-only", "")


def test_site_review_cli_emits_json_from_cached_robots(tmp_path: Path, capsys) -> None:
    write_cached_robots(tmp_path)

    result = cli.main(["site-review", "--offline", "--cache-dir", str(tmp_path), "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["kind"] == "thread-search-site-policy-review"
    assert payload["metadata_only"] is True
    assert payload["robots_probes"][0]["key"] == "reader"
    assert "Official Policy Pages" not in captured.out


def test_site_review_cli_writes_markdown(tmp_path: Path, capsys) -> None:
    write_cached_robots(tmp_path)
    out = tmp_path / "site-policy-review.md"

    result = cli.main(["site-review", "--offline", "--cache-dir", str(tmp_path), "--out", str(out)])
    captured = capsys.readouterr()

    assert result == 0
    assert f"wrote: {out}" in captured.out
    assert "Robots Checks" in out.read_text(encoding="utf-8")
