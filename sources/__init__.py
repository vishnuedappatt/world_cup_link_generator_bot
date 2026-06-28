"""Source adapter registry.

Each adapter module exposes ``NAME`` and ``fetch(home, away) -> SourceResult``.
``SOURCES`` is the ordered list the fan-out iterates over.
"""

from . import epic_sports, footem, mob_sports, sports90, sportshuber

SOURCES = [epic_sports, footem, mob_sports, sports90, sportshuber]

__all__ = ["SOURCES"]
