"""footem.co.in adapter.

Chain: home page (div.containermatch) -> match page (keyword anchors + iframes).
Ported from footem/footem.py.
"""

from urllib.parse import urljoin

from .base import (
    SourceResult, get_soup, text_of, best_match, dedupe_links,
)

NAME = "footem"
BASE_URL = "https://www.footem.co.in/"

KEYWORDS = (
    "stream", "watch", "live", "play", "embed", "m3u8", "telegram",
    "whatsapp", "blogspot", "link", "video", "redirect",
)


def _normalize_url(raw, page_url):
    if not raw:
        return None
    href = raw.strip()
    if not href or href.startswith("javascript:"):
        return None
    return urljoin(page_url, href)


def _looks_relevant(url, text):
    blob = (url + " " + (text or "")).lower()
    return any(k in blob for k in KEYWORDS)


def _extract_links(soup, page_url):
    links = []
    for anchor in soup.find_all("a", href=True):
        href = _normalize_url(anchor.get("href"), page_url)
        if not href or not _looks_relevant(href, text_of(anchor)):
            continue
        links.append({"title": text_of(anchor) or "Link", "url": href})
    for iframe in soup.find_all("iframe", src=True):
        src = _normalize_url(iframe.get("src"), page_url)
        if src:
            links.append({"title": "Embedded iframe", "url": src})
    return links


def fetch(home, away):
    result = SourceResult(source=NAME)
    soup = get_soup(BASE_URL)
    if soup is None:
        result.error = "home page unreachable"
        return result

    candidates = []
    for card in soup.select("div.containermatch"):
        anchor = card.find("a", href=True)
        names = [text_of(x) for x in card.select(".matchname")]
        if not anchor or len(names) != 2:
            continue
        # names are [away, home]; title order doesn't matter for matching.
        title = f"{names[1]} vs {names[0]}"
        candidates.append((anchor, title))

    anchor, title, _ = best_match(candidates, home, away)
    if not anchor:
        result.error = "match not listed"
        return result

    result.matched_title = title
    match_url = _normalize_url(anchor.get("href"), BASE_URL)
    result.match_url = match_url or ""

    match_soup = get_soup(match_url)
    if match_soup is None:
        result.error = "match page unreachable"
        return result

    result.links = dedupe_links(_extract_links(match_soup, match_url))
    result.ok = True
    return result
