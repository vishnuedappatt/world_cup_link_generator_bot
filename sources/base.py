"""Shared helpers for source adapters.

Every adapter exposes ``fetch(home, away) -> SourceResult`` and reuses the HTTP
session + team-name matching defined here. The matching is what lets a single
picked match (e.g. "Argentina" vs "Jordan") be found across sources that all use
different title formats.
"""

import re
import unicodedata
from dataclasses import dataclass, field, asdict

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json"}

# One pooled session shared across adapters for connection reuse.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

DEFAULT_TIMEOUT = 15


def get_html(url, timeout=DEFAULT_TIMEOUT):
    """GET a URL and return its text, or None on any failure."""
    if not url:
        return None
    try:
        resp = SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None


def get_soup(url, timeout=DEFAULT_TIMEOUT):
    """GET a URL and return a parsed soup, or None on any failure."""
    html = get_html(url, timeout=timeout)
    if html is None:
        return None
    return BeautifulSoup(html, "html.parser")


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def text_of(node):
    if not node:
        return ""
    return node.get_text(" ", strip=True)


# --- Team-name matching -----------------------------------------------------

# Generic words that appear in many national-team names and must NOT count as a
# match on their own (else "Korea Republic" would collide with "Czech Republic").
# This is a small stop-word list, not a per-country mapping.
NOISE_TOKENS = {"republic", "fc", "afc", "sc", "dr", "ir", "pr", "national", "team"}


def normalize(value):
    """Lowercase, strip accents, drop everything but a-z0-9."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def significant_tokens(team):
    """Distinctive word-tokens of a team name (drops generic noise words).

    Derived dynamically from the name itself — no hardcoded country list. So
    "Congo DR" -> {"congo"}, "Korea Republic" -> {"korea"}, "Costa Rica" ->
    {"costa", "rica"}. Lets differently-worded spellings of the same team match
    on a shared distinctive word.
    """
    tokens = re.findall(r"[a-z]+", (team or "").lower())
    significant = [t for t in tokens if len(t) >= 3 and t not in NOISE_TOKENS]
    return significant or tokens  # keep something even if all looked generic


def _team_score(team, title_norm):
    """How strongly a single team name appears in a normalized title (0..1)."""
    team_norm = normalize(team)
    if not team_norm:
        return 0.0
    # Whole name present -> strongest signal.
    if team_norm in title_norm:
        return 1.0
    # Otherwise match on distinctive word-tokens (handles re-orderings and
    # alternate spellings like "Congo DR" vs "DR Congo" vs "Congo").
    tokens = significant_tokens(team)
    hits = sum(1 for t in tokens if t in title_norm)
    if hits:
        return 0.8 * (hits / len(tokens))
    return 0.0


def match_score(title, home, away):
    """Combined score that the title refers to the home-vs-away fixture.

    Returns 0 unless *both* teams are at least partially present, so we never
    match a card that only shares one team.
    """
    title_norm = normalize(title)
    home_score = _team_score(home, title_norm)
    away_score = _team_score(away, title_norm)
    if home_score == 0 or away_score == 0:
        return 0.0
    return home_score + away_score


def best_match(candidates, home, away, threshold=0.8):
    """Pick the (item, title) with the highest score above threshold.

    ``candidates`` is an iterable of (item, title) pairs. Returns (item, title,
    score) or (None, None, 0.0) when nothing clears the threshold.
    """
    best = (None, None, 0.0)
    for item, title in candidates:
        score = match_score(title, home, away)
        if score >= threshold and score > best[2]:
            best = (item, title, score)
    return best


# --- Result container -------------------------------------------------------

@dataclass
class SourceResult:
    source: str
    ok: bool = False
    matched_title: str = ""
    match_url: str = ""
    links: list = field(default_factory=list)  # list of {"title", "url"}
    error: str = ""

    def to_dict(self):
        return asdict(self)


def dedupe_links(links):
    """Drop duplicate URLs while preserving order."""
    seen = set()
    out = []
    for link in links:
        url = link.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(link)
    return out
