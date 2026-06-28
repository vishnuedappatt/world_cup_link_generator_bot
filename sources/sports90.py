"""sports90.in adapter.

Chain: home page (article.blog-post) -> candidate pages -> stream links
(a.stream-btn + keyword anchors). Ported from sports90/sports90.py.
"""

from urllib.parse import urljoin

from .base import (
    SourceResult, get_soup, text_of, best_match, dedupe_links,
)

NAME = "sports90"
BASE_URL = "https://www.sports90.in/"

KEYWORDS = ("stream", "watch", "live", "play", "embed", "blogspot", "m3u8", "link")
IMAGE_EXT = (".jpg", ".png", ".gif", ".svg")


def _normalize_url(raw, page_url):
    if not raw:
        return None
    href = raw.strip()
    if not href or href.startswith("javascript:"):
        return None
    return urljoin(page_url, href)


def _looks_like_stream(url, text):
    blob = (url + " " + (text or "")).lower()
    return any(k in blob for k in KEYWORDS)


def _candidate_urls(soup, page_url):
    candidates = []
    seen = set()

    full_time = soup.find("b", string=lambda s: s and "FULL TIME" in s)
    if full_time:
        for container in (full_time.find_parent("tr"), full_time.find_parent("table"), full_time.find_parent("div")):
            if not container:
                continue
            for a in container.find_all("a", href=True):
                href = _normalize_url(a.get("href"), page_url)
                if href and href not in seen:
                    seen.add(href)
                    candidates.append(href)

    for a in soup.find_all("a", href=True):
        href = _normalize_url(a.get("href"), page_url)
        if not href or href in seen:
            continue
        text = text_of(a).lower()
        if "/p/" in href or "/2026/" in href or "blogspot" in href.lower() or _looks_like_stream(href, text):
            if not href.endswith(IMAGE_EXT):
                seen.add(href)
                candidates.append(href)
    return candidates


def _extract_stream_links(soup, page_url):
    links = []
    seen = set()

    for a in soup.select("a.stream-btn"):
        href = _normalize_url(a.get("href"), page_url)
        if not href or href in seen:
            continue
        seen.add(href)
        links.append({
            "title": text_of(a.select_one(".stream-title")) or text_of(a) or "Stream Link",
            "url": href,
        })

    for a in soup.find_all("a", href=True):
        href = _normalize_url(a.get("href"), page_url)
        text = text_of(a)
        if not href or href in seen or not _looks_like_stream(href, text):
            continue
        if href.startswith(page_url) and not href.endswith((".html", ".htm")):
            continue
        seen.add(href)
        links.append({"title": text or "Stream Link", "url": href})
    return links


def fetch(home, away):
    result = SourceResult(source=NAME)
    soup = get_soup(BASE_URL)
    if soup is None:
        result.error = "home page unreachable"
        return result

    candidates = []
    for article in soup.select("article.blog-post"):
        anchor = article.select_one("h2.entry-title a")
        if anchor and anchor.get("href"):
            candidates.append((anchor, text_of(anchor)))

    anchor, title, _ = best_match(candidates, home, away)
    if not anchor:
        result.error = "match not listed"
        return result

    result.matched_title = title
    match_url = anchor.get("href")
    result.match_url = match_url

    match_soup = get_soup(match_url)
    if match_soup is None:
        result.error = "match page unreachable"
        return result

    # Walk the match page itself + its candidate pages until one yields links.
    for candidate in [match_url, *_candidate_urls(match_soup, match_url)]:
        cand_soup = match_soup if candidate == match_url else get_soup(candidate)
        if cand_soup is None:
            continue
        links = _extract_stream_links(cand_soup, candidate)
        if links:
            result.match_url = candidate
            result.links = dedupe_links(links)
            result.ok = True
            return result

    result.error = "no stream links found"
    return result
