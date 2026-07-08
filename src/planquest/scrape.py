from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import re
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .config import MAIN_THREADMARK_CATEGORY_ID
from .fetch import PoliteFetcher
from .models import Threadmark


@dataclass(frozen=True)
class CrawlPlan:
    reader_root: str
    category_id: int
    category_name: str
    page_count: int
    page_urls: list[str]


@dataclass(frozen=True)
class ScrapeStats:
    plan: CrawlPlan
    threadmarks: int
    words: int
    network_pages: int
    cached_pages: int
    out_path: Path


ProgressCallback = Callable[[int, int, str, bool, int], None]


def normalize_reader_root(url: str, category_id: int = MAIN_THREADMARK_CATEGORY_ID) -> str:
    base = re.sub(r"/reader/page-\d+/?$", "", url.rstrip("/"))
    base = re.sub(r"/reader/?$", "", base)
    base = re.sub(r"/\d+/reader/?$", "", base)
    if category_id == MAIN_THREADMARK_CATEGORY_ID:
        return f"{base}/reader/"
    return f"{base}/{category_id}/reader/"


def reader_page_url(reader_root: str, page_number: int) -> str:
    if page_number <= 1:
        return reader_root
    return f"{reader_root.rstrip('/')}/page-{page_number}"


def discover_page_count(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    page_numbers = {1}
    for link in soup.select("a[href*='/reader/page-']"):
        href = link.get("href") or ""
        match = re.search(r"/reader/page-(\d+)", href)
        if match:
            page_numbers.add(int(match.group(1)))
    return max(page_numbers)


def discover_categories(html: str) -> list[dict[str, str | int | None]]:
    soup = BeautifulSoup(html, "html.parser")
    categories: dict[int, dict[str, str | int | None]] = {
        1: {"id": 1, "name": "Threadmarks", "count": None, "reader_url": None},
    }

    for link in soup.select("a[href*='threadmark_category=']"):
        href = link.get("href") or ""
        match = re.search(r"threadmark_category=(\d+)", href)
        if not match:
            continue
        category_id = int(match.group(1))
        name = normalized_text(link.get_text(" ", strip=True))
        if not name or name.lower().startswith("view all"):
            continue
        categories.setdefault(
            category_id,
            {"id": category_id, "name": name, "count": None, "reader_url": None},
        )

    for link in soup.select("a[href*='/reader/']"):
        href = link.get("href") or ""
        match = re.search(r"\.(\d+)/(\d+)/reader/?", href)
        if match:
            category_id = int(match.group(2))
            categories.setdefault(
                category_id,
                {"id": category_id, "name": f"Category {category_id}", "count": None, "reader_url": None},
            )
            categories[category_id]["reader_url"] = urljoin("https://forums.sufficientvelocity.com", href)
        elif href.endswith("/reader/") or "/reader/page-" in href:
            categories[1]["reader_url"] = urljoin("https://forums.sufficientvelocity.com", href)

    for link in soup.select("a.blockLink"):
        text = normalized_text(link.get_text(" ", strip=True))
        match = re.search(r"View all\s+(\d+)\s+threadmarks", text, flags=re.I)
        if not match:
            continue
        menu = link.find_parent(class_="menu-content")
        if menu is None:
            continue
        category_link = menu.find("a", href=re.compile(r"threadmark_category="))
        if category_link:
            href = category_link.get("href") or ""
            cat_match = re.search(r"threadmark_category=(\d+)", href)
            if cat_match:
                categories[int(cat_match.group(1))]["count"] = int(match.group(1))
                continue
        if "Threadmarks" in text or categories[1].get("count") is None:
            categories[1]["count"] = int(match.group(1))

    return [categories[key] for key in sorted(categories)]


def plan_reader_crawl(
    first_page_html: str,
    reader_root: str,
    category_id: int = MAIN_THREADMARK_CATEGORY_ID,
    category_name: str = "Threadmarks",
    max_pages: int | None = None,
) -> CrawlPlan:
    page_count = discover_page_count(first_page_html)
    if max_pages is not None:
        page_count = min(page_count, max_pages)
    page_urls = [reader_page_url(reader_root, page) for page in range(1, page_count + 1)]
    return CrawlPlan(
        reader_root=reader_root,
        category_id=category_id,
        category_name=category_name,
        page_count=page_count,
        page_urls=page_urls,
    )


def select_page_urls(
    page_urls: list[str],
    from_page: int = 1,
    to_page: int | None = None,
) -> list[tuple[int, str]]:
    if from_page < 1:
        raise ValueError("from_page must be at least 1")
    if to_page is not None and to_page < from_page:
        raise ValueError("to_page must be greater than or equal to from_page")

    last_page = len(page_urls) if to_page is None else min(to_page, len(page_urls))
    if from_page > last_page:
        return []
    return [(page, page_urls[page - 1]) for page in range(from_page, last_page + 1)]


def parse_reader_page(
    html: str,
    page_url: str,
    category_id: int = MAIN_THREADMARK_CATEGORY_ID,
    category_name: str = "Threadmarks",
) -> list[Threadmark]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[Threadmark] = []
    category_class = f"threadmark-category-{category_id}"

    for article in soup.select("article.message--post.hasThreadmark"):
        classes = set(article.get("class") or [])
        if category_class not in classes:
            continue

        post_id = post_id_from_article(article)
        title_el = article.select_one(".message-cell--threadmark-header .threadmarkLabel")
        title = normalized_text(title_el.get_text(" ", strip=True) if title_el else f"Post {post_id}")
        threadmark_id = None
        if title_el and title_el.get("id"):
            threadmark_id = str(title_el["id"]).removeprefix("threadmark-")

        time_el = article.select_one(".message-attribution-main time[datetime]") or article.select_one("time[datetime]")
        published_at = str(time_el.get("datetime")) if time_el and time_el.get("datetime") else None
        author = str(article.get("data-author") or "")

        source_link = article.select_one("a.threadmark-control--viewContent[href*='#post-']")
        source_url = urljoin(page_url, source_link["href"]) if source_link and source_link.get("href") else page_url

        body = article.select_one(".message-content .bbWrapper")
        text = extract_body_text(body) if body else ""
        if not text:
            continue

        records.append(
            Threadmark(
                order=0,
                category_id=category_id,
                category_name=category_name,
                threadmark_id=threadmark_id,
                post_id=post_id,
                title=title,
                author=author,
                published_at=published_at,
                source_url=source_url,
                reader_url=page_url,
                text=text,
                word_count=len(re.findall(r"\S+", text)),
            )
        )

    return records


def scrape_reader(
    fetcher: PoliteFetcher,
    url: str,
    out_path: Path,
    category_id: int = MAIN_THREADMARK_CATEGORY_ID,
    category_name: str = "Threadmarks",
    max_pages: int | None = None,
    progress: ProgressCallback | None = None,
) -> list[Threadmark]:
    records, _stats = scrape_reader_with_stats(
        fetcher=fetcher,
        url=url,
        out_path=out_path,
        category_id=category_id,
        category_name=category_name,
        max_pages=max_pages,
        progress=progress,
    )
    return records


def scrape_reader_with_stats(
    fetcher: PoliteFetcher,
    url: str,
    out_path: Path,
    category_id: int = MAIN_THREADMARK_CATEGORY_ID,
    category_name: str = "Threadmarks",
    max_pages: int | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[list[Threadmark], ScrapeStats]:
    reader_root = normalize_reader_root(url, category_id=category_id)
    first = fetcher.fetch_text(reader_root)
    plan = plan_reader_crawl(
        first.text,
        reader_root=reader_root,
        category_id=category_id,
        category_name=category_name,
        max_pages=max_pages,
    )

    records: list[Threadmark] = []
    seen_posts: set[str] = set()
    network_pages = 0 if first.from_cache else 1
    cached_pages = 1 if first.from_cache else 0

    for page, page_url in enumerate(plan.page_urls, start=1):
        if page == 1:
            fetched = first
        else:
            fetched = fetcher.fetch_text(page_url)
            if fetched.from_cache:
                cached_pages += 1
            else:
                network_pages += 1

        html = fetched.text
        page_records = parse_reader_page(html, page_url, category_id=category_id, category_name=category_name)
        for record in page_records:
            if record.post_id in seen_posts:
                continue
            seen_posts.add(record.post_id)
            records.append(replace(record, order=len(records) + 1))
        if progress is not None:
            progress(page, plan.page_count, page_url, fetched.from_cache, len(page_records))

    write_jsonl(records, out_path)
    stats = ScrapeStats(
        plan=plan,
        threadmarks=len(records),
        words=sum(record.word_count for record in records),
        network_pages=network_pages,
        cached_pages=cached_pages,
        out_path=out_path,
    )
    return records, stats


def write_jsonl(records: Iterable[Threadmark], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.to_json())
            handle.write("\n")
    tmp.replace(out_path)


def read_jsonl(path: Path) -> list[Threadmark]:
    with path.open("r", encoding="utf-8") as handle:
        return [Threadmark.from_json(line) for line in handle if line.strip()]


def post_id_from_article(article: Tag) -> str:
    data_content = article.get("data-content")
    if data_content:
        return str(data_content).removeprefix("post-")
    article_id = article.get("id")
    if article_id:
        return str(article_id).removeprefix("js-post-")
    anchor = article.select_one("span[id^='post-']")
    if anchor and anchor.get("id"):
        return str(anchor["id"]).removeprefix("post-")
    return "unknown"


def extract_body_text(body: Tag) -> str:
    soup = BeautifulSoup(str(body), "html.parser")
    for selector in [
        "script",
        "style",
        "noscript",
        ".js-unfurl-figure",
        ".bbCodeBlock-title",
        ".message-lastEdit",
    ]:
        for tag in soup.select(selector):
            tag.decompose()

    for br in soup.find_all("br"):
        br.replace_with("\n")

    text = soup.get_text("\n")
    return normalized_text(text, preserve_paragraphs=True)


def normalized_text(text: str, preserve_paragraphs: bool = False) -> str:
    text = text.replace("\xa0", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r"^\[\](?=\S)", "[ ]", line)
        lines.append(line)
    if not preserve_paragraphs:
        return " ".join(line for line in lines if line)

    output: list[str] = []
    blank = False
    for line in lines:
        if not line:
            blank = True
            continue
        if blank and output:
            output.append("")
        output.append(line)
        blank = False
    return "\n".join(output).strip()
