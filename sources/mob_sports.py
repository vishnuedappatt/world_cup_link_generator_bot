"""mobisports.live adapter.

Chain: home page (article.blog-post) -> match page ("Click here" image) ->
channel page (anchors "Link N"). Ported from mob_sports/mob_sports.py.
"""

import re

from .base import (
    SourceResult, get_soup, text_of, best_match, dedupe_links,
)

NAME = "mob_sports"
BASE_URL = "https://www.mobisports.live/"

_LINK_RE = re.compile(r"^Link\s+\d+", re.IGNORECASE)


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
    result.match_url = anchor.get("href")

    match_soup = get_soup(result.match_url)
    if match_soup is None:
        result.error = "match page unreachable"
        return result

    img = match_soup.find("img", alt="Click here")
    channel_anchor = img.find_parent("a") if img else None
    channel_url = channel_anchor.get("href") if channel_anchor else None
    if not channel_url:
        result.error = "no 'Click here' channel link"
        return result

    channel_soup = get_soup(channel_url)
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
