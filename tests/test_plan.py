import pytest

from planquest.scrape import plan_reader_crawl, select_page_urls


def test_plan_reader_crawl_lists_expected_page_urls() -> None:
    html = """
    <html><body>
      <a href="/threads/example.1/reader/page-2">2</a>
      <a href="/threads/example.1/reader/page-4">4</a>
    </body></html>
    """

    plan = plan_reader_crawl(html, "https://forums.sufficientvelocity.com/threads/example.1/reader/")

    assert plan.page_count == 4
    assert plan.page_urls == [
        "https://forums.sufficientvelocity.com/threads/example.1/reader/",
        "https://forums.sufficientvelocity.com/threads/example.1/reader/page-2",
        "https://forums.sufficientvelocity.com/threads/example.1/reader/page-3",
        "https://forums.sufficientvelocity.com/threads/example.1/reader/page-4",
    ]


def test_select_page_urls_returns_inclusive_page_range() -> None:
    urls = ["page-1", "page-2", "page-3", "page-4"]

    assert select_page_urls(urls, from_page=2, to_page=3) == [(2, "page-2"), (3, "page-3")]


def test_select_page_urls_caps_to_available_pages() -> None:
    urls = ["page-1", "page-2"]

    assert select_page_urls(urls, from_page=2, to_page=5) == [(2, "page-2")]


def test_select_page_urls_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        select_page_urls(["page-1"], from_page=0)

    with pytest.raises(ValueError):
        select_page_urls(["page-1"], from_page=2, to_page=1)
