"""sportshuber.blogspot.com adapter.

Chain: home page (div.post-outer) -> match page ("WATCH LIVE" row) ->
channel page (anchors "Link N"). Ported from sportshuber_blogspot/rest.py.
"""

import re

from .base import (
    SourceResult, get_soup, text_of, best_match, dedupe_links,
)

NAME = "sportshuber"
BASE_URL = "https://sportshuber.blogspot.com/"

_LINK_RE = re.compile(r"^Link\s+\d+", re.IGNORECASE)


def fetch(home, away):
    result = SourceResult(source=NAME)
    soup = get_soup(BASE_URL)
    if soup is None:
        result.error = "home page unreachable"
        return result

    candidates = []
    for post in soup.select("div.post-outer"):
        anchor = post.select_one("h2.post-title a")
        if anchor and anchor.get("href"):
            candidates.append((anchor, text_of(anchor)))

    anchor, title, _ = best_match(candidates, home, away)
    if not anchor:
        result.error = "match not listed"
        return result

    result.matched_title = title
    result.match_url = anchor["href"]

    match_soup = get_soup(result.match_url)
    if match_soup is None:
        result.error = "match page unreachable"
        return result

    watch_url = None
    for td in match_soup.find_all("td"):
        if "WATCH LIVE" in td.get_text(" ", strip=True):
            next_td = td.find_next_sibling("td")
            a = next_td.find("a", href=True) if next_td else None
            if a:
                watch_url = a["href"]
            break

    if not watch_url:
        result.error = "no 'WATCH LIVE' link"
        return result

    channel_soup = get_soup(watch_url)
    if channel_soup is None:
        result.error = "channel page unreachable"
        return result

    links = []
    for a in channel_soup.find_all("a", href=True):
        text = text_of(a)
        if _LINK_RE.match(text):
            links.append({"title": text, "url": a["href"]})

    result.links = dedupe_links(links)
    result.ok = True
    return result
