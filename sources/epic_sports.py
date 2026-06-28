"""epicsports.in adapter.

Chain: home page (.match-event cards) -> match page ("Click Here" button) ->
links page (button.live-btn). Ported from epic_sports/epic_sports.py.
"""

import re
from urllib.parse import urljoin

from .base import (
    SourceResult, get_soup, soup_of, get_html, text_of, best_match, dedupe_links,
)

NAME = "epic_sports"
BASE_URL = "https://www.epicsports.in/"

_HREF_RE = re.compile(r"window\.location\.href=['\"]([^'\"]+)['\"]")


def fetch(home, away):
    result = SourceResult(source=NAME)
    soup = get_soup(BASE_URL)
    if soup is None:
        result.error = "home page unreachable"
        return result

    candidates = []
    for card in soup.select(".match-event"):
        anchor = card.select_one("a[title], a#match-live")
        if not anchor:
            continue
        title = (anchor.get("title") or text_of(anchor)).strip()
        if title:
            candidates.append((anchor, title))

    anchor, title, _ = best_match(candidates, home, away)
    if not anchor:
        result.error = "match not listed"
        return result

    result.matched_title = title
    match_url = urljoin(BASE_URL, (anchor.get("href") or "").strip())
    result.match_url = match_url

    match_soup = get_soup(match_url)
    if match_soup is None:
        result.error = "match page unreachable"
        return result

    button = match_soup.find("button", string=lambda s: s and "Click Here" in s)
    if not button:
        result.error = "no 'Click Here' button"
        return result

    m = _HREF_RE.search(button.get("onclick", ""))
    if not m:
        result.error = "no link in 'Click Here'"
        return result

    links_html = get_html(m.group(1))
    if links_html is None:
        result.error = "links page unreachable"
        return result

    links_soup = soup_of(links_html)
    links = []
    for btn in links_soup.select("button.live-btn"):
        m = _HREF_RE.search(btn.get("onclick", ""))
        if not m:
            continue
        links.append({"title": text_of(btn) or "Link", "url": m.group(1)})

    result.links = dedupe_links(links)
    result.ok = True
    return result
