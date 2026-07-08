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
