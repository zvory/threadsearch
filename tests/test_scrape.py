from planquest.scrape import discover_page_count, parse_reader_page


HTML = """
<html><body>
  <nav>
    <a href="/threads/example.1/reader/page-2">2</a>
    <a href="/threads/example.1/reader/page-9">9</a>
  </nav>
  <article class="message message--post hasThreadmark threadmark-category-1 js-post"
           data-author="Blackstar" data-content="post-123" id="js-post-123">
    <div class="message-cell message-cell--threadmark-header">
      <span class="primary">
        <label for="threadmark-456">Threadmarks</label>
        <span id="threadmark-456" class="threadmarkLabel">Turn 1</span>
      </span>
      <a href="/threads/example.1/page-1#post-123" class="threadmark-control threadmark-control--viewContent">View content</a>
    </div>
    <header>
      <ul class="message-attribution-main">
        <li><time datetime="2020-01-01T00:00:00-0500">Jan 1, 2020</time></li>
      </ul>
    </header>
    <div class="message-content js-messageContent">
      <div class="bbWrapper">
        First paragraph.<br>
        Cuba remains non-communist here.
        <script>ignored()</script>
      </div>
    </div>
  </article>
  <article class="message message--post hasThreadmark threadmark-category-5 js-post"
           data-author="Blackstar" data-content="post-999">
    <div class="message-cell message-cell--threadmark-header">
      <span class="threadmarkLabel">Omake</span>
    </div>
    <div class="message-content js-messageContent"><div class="bbWrapper">Side text.</div></div>
  </article>
</body></html>
"""


def test_discover_page_count() -> None:
    assert discover_page_count(HTML) == 9


def test_parse_reader_page_filters_to_main_category() -> None:
    records = parse_reader_page(HTML, "https://forums.sufficientvelocity.com/threads/example.1/reader/")

    assert len(records) == 1
    assert records[0].post_id == "123"
    assert records[0].title == "Turn 1"
    assert records[0].threadmark_id == "456"
    assert records[0].author == "Blackstar"
    assert "Cuba remains non-communist" in records[0].text


def test_incremental_refresh_fetches_discovery_tail_and_new_pages(tmp_path) -> None:
    from planquest.fetch import PoliteFetcher
    from planquest.scrape import reader_page_url, scrape_reader_with_stats

    root = "https://example.invalid/threads/example.1/reader/"
    calls = []
    pages = 3
    tail_extra = False

    def page_html(url):
        calls.append(url)
        page = next(n for n in range(1, pages + 1) if reader_page_url(root, n) == url)
        body = HTML.replace("post-123", f"post-{page}").replace("Turn 1", f"Turn {page}")
        body = body.replace("/reader/page-9", f"/reader/page-{pages}")
        if tail_extra and page == 3:
            body += body.replace("post-3", "post-30")
        return body

    fetcher = PoliteFetcher(tmp_path / "cache", "test-agent", delay_seconds=0)
    fetcher._assert_allowed = lambda url: None
    fetcher._http_get_with_retries = page_html
    out = tmp_path / "records.jsonl"

    records, stats = scrape_reader_with_stats(fetcher, root, out)
    assert len(records) == 3
    assert stats.network_pages == 3

    calls.clear()
    pages = 4
    tail_extra = True
    records, stats = scrape_reader_with_stats(fetcher, root, out)
    assert calls == [root, reader_page_url(root, 3), reader_page_url(root, 4)]
    assert [r.post_id for r in records] == ["1", "2", "3", "30", "4"]
    assert [r.order for r in records] == [1, 2, 3, 4, 5]
    assert stats.network_pages == 3
    assert stats.cached_pages == 1

    calls.clear()
    scrape_reader_with_stats(fetcher, root, out)
    assert calls == [root, reader_page_url(root, 4)]

    calls.clear()
    fetcher.offline = True
    records, stats = scrape_reader_with_stats(fetcher, root, out)
    assert calls == []
    assert stats.cached_pages == 4
    assert len(records) == 5

    fetcher.offline = False
    fetcher.refresh = True
    scrape_reader_with_stats(fetcher, root, out)
    assert calls == [reader_page_url(root, n) for n in range(1, 5)]


def test_incremental_failure_keeps_previous_corpus(tmp_path) -> None:
    import pytest
    from planquest.fetch import PoliteFetcher
    from planquest.scrape import scrape_reader_with_stats

    out = tmp_path / "records.jsonl"
    out.write_text("previous corpus")
    fetcher = PoliteFetcher(tmp_path / "cache", "test-agent", delay_seconds=0)
    fetcher._assert_allowed = lambda url: None

    def fail(url):
        raise OSError("network unavailable")

    fetcher._http_get_with_retries = fail
    with pytest.raises(OSError, match="network unavailable"):
        scrape_reader_with_stats(fetcher, "https://example.invalid/threads/example.1/reader/", out)
    assert out.read_text() == "previous corpus"
