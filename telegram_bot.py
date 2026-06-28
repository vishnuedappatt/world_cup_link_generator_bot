"""Telegram bot for the World Cup link finder.

Flow:
  - User taps "📅 Match Day" (or sends /start / "match day").
  - Bot shows today's + tomorrow's matches as inline buttons.
  - User taps a match -> bot queries all 5 sources in parallel and shows the
    unique stream links as tap-to-open buttons (or "not found, try later").
  - Any other input is ignored with a gentle nudge back to the Match Day button.

A warning is shown on every reply: real links usually appear ~30 min before
kickoff.

Token (do NOT hardcode): set the TELEGRAM_BOT_TOKEN environment variable, or put
the token on the first line of a file named `bot_token.txt` next to this script.

Run:
    ./envv/bin/pip install pyTelegramBotAPI
    TELEGRAM_BOT_TOKEN="123456:ABC..." ./envv/bin/python telegram_bot.py
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import telebot
from telebot import types, apihelper

from sources import SOURCES
from sources.base import dedupe_links, SourceResult
from dotenv import load_dotenv
load_dotenv()  # Load .env file if present, so TELEGRAM_BOT_TOKEN can be set there

# Make Telegram API calls resilient to transient network blips/throttling
# (some ISPs flicker on api.telegram.org). Without this a single ReadTimeout
# would bubble out of a handler.
apihelper.CONNECT_TIMEOUT = 30
apihelper.READ_TIMEOUT = 60
apihelper.RETRY_ON_ERROR = True   # auto-retry failed API requests
apihelper.MAX_RETRIES = 3
apihelper.RETRY_TIMEOUT = 2       # seconds between retries

# --- Config -----------------------------------------------------------------

WARNING = (
    "⚠️ <b>Heads up:</b> working stream links are usually posted about "
    "<b>30 minutes before kickoff</b>. If you don't see links yet, check back "
    "closer to match time."
)

# Things that should open the match list (case-insensitive, stripped).
MATCH_DAY_TRIGGERS = {"📅 match day", "match day", "matchday"}

# Words that make the bot reply with the owner's contact.
OWNER_TRIGGERS = {"owner", "admin", "developer", "dev", "contact"}
# Set OWNER_USERNAME in your .env (e.g. OWNER_USERNAME=@yourname).
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "@vishnuedappatt")

# Shown at the bottom of every reply.
FOOTER = "❤️ <i>Enjoy &amp; love football!</i>"

# Link requests are accepted up to and including this date (IST). After it, the
# bot tells users the World Cup is over.
WORLD_CUP_END = date(2026, 7, 21)
IST = ZoneInfo("Asia/Kolkata")

# FIFA official fixtures feed (source of truth for today's/tomorrow's matches).
FIFA_URL = "https://play.fifa.com/json/dream_eleven/rounds.json"
FIFA_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Telegram caps inline keyboards; keep well under the 100-button limit.
MAX_LINK_BUTTONS = 80

# Loader animation frames shown while links are being generated fresh.
LOADER_FRAMES = ["🔵⚪⚪⚪", "🔵🔵⚪⚪", "🔵🔵🔵⚪", "🔵🔵🔵🔵"]


def load_token():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        token_file = Path(__file__).with_name("bot_token.txt")
        if token_file.exists():
            token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(
            "No bot token found.\n"
            "  - set env var TELEGRAM_BOT_TOKEN, or\n"
            "  - create bot_token.txt with the token on the first line."
        )
    return token


bot = telebot.TeleBot(load_token(), parse_mode="HTML")


# --- Helpers ----------------------------------------------------------------

def _with_footer(text):
    return f"{text}\n\n{FOOTER}"


def safe_send(chat_id, text, **kwargs):
    """send_message that won't crash a handler on a network blip."""
    try:
        return bot.send_message(chat_id, _with_footer(text), **kwargs)
    except Exception as exc:
        print(f"[warn] send_message failed: {exc}")
        return None


def safe_edit(text, chat_id, message_id, **kwargs):
    try:
        return bot.edit_message_text(_with_footer(text), chat_id, message_id, **kwargs)
    except Exception as exc:
        print(f"[warn] edit_message_text failed: {exc}")
        return None


def world_cup_over():
    """True once the current IST date is past the World Cup end date."""
    return datetime.now(IST).date() > WORLD_CUP_END


def main_keyboard():
    """Persistent reply keyboard with the single Match Day button."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("📅 Match Day"))
    return kb


def matchday_inline_button():
    """Inline button shown inside message bubbles (always visible)."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📅 Match Day", callback_data="show_matchday"))
    return kb


def load_match_list():
    """Load today's + tomorrow's matches fresh from FIFA (no caching)."""
    resp = requests.get(FIFA_URL, headers=FIFA_HEADERS, timeout=20)
    resp.raise_for_status()
    rounds = resp.json()

    today = datetime.now(IST).date()
    tomorrow = today + timedelta(days=1)

    matches = []
    for round_data in rounds:
        for match in round_data.get("tournaments", []):
            kickoff = datetime.fromisoformat(match["date"]).astimezone(IST)
            if kickoff.date() not in (today, tomorrow):
                continue
            matches.append({
                "home": match["homeSquadName"],
                "away": match["awaySquadName"],
                "kickoff": kickoff.isoformat(),
                "kickoff_label": kickoff.strftime("%a %d %b, %I:%M %p IST"),
                "day": "today" if kickoff.date() == today else "tomorrow",
            })

    matches.sort(key=lambda m: m["kickoff"])
    return matches


def run_loader(stop_event, chat_id, msg_id, home, away):
    """Animate a 'creating links…' loader until stop_event is set."""
    i = 0
    while not stop_event.is_set():
        frame = LOADER_FRAMES[i % len(LOADER_FRAMES)]
        try:
            bot.edit_message_text(
                _with_footer(
                    f"🔄 <b>Creating fresh links…</b>\n{home} vs {away}\n{frame}"
                ),
                chat_id,
                msg_id,
            )
        except Exception:
            pass  # ignore "not modified" / transient edit errors
        i += 1
        stop_event.wait(1.3)  # gentle pace to respect Telegram edit limits


def match_button_label(m):
    # m["kickoff_label"] looks like "Sun 28 Jun, 07:30 AM IST"
    parts = m["kickoff_label"].split(", ")
    day = parts[0].split()[0] if parts else ""
    time_str = parts[1].replace(" IST", "") if len(parts) > 1 else ""
    tag = " (tom)" if m.get("day") == "tomorrow" else ""
    return f"{day} {time_str}{tag} | {m['home']} vs {m['away']}".strip()


def _fetch_one(module, home, away):
    """Call one source adapter; never let it take down the fan-out."""
    try:
        return module.fetch(home, away)
    except Exception as exc:
        return SourceResult(source=module.NAME, error=f"crashed: {exc}")


def collect_unique_links(home, away):
    """Run all sources in parallel and return de-duplicated links + ok count."""
    results = []
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = [pool.submit(_fetch_one, mod, home, away) for mod in SOURCES]
        for future in as_completed(futures):
            results.append(future.result())

    flat = []
    sources_with_links = 0
    for res in results:
        if res.ok and res.links:
            sources_with_links += 1
            flat.extend(res.links)
    unique = [l for l in dedupe_links(flat) if str(l.get("url", "")).startswith("http")]
    return unique, sources_with_links


# --- Handlers ---------------------------------------------------------------

@bot.message_handler(commands=["start", "help"])
def on_start(message):
    # Set the bottom reply keyboard once...
    safe_send(message.chat.id, "👋 Welcome!", reply_markup=main_keyboard())
    # ...then the actionable message with an always-visible inline button.
    safe_send(
        message.chat.id,
        f"{WARNING}\n\n👋 Tap <b>📅 Match Day</b> below to see today's and tomorrow's "
        "World Cup matches, then pick one to get its stream links.",
        reply_markup=matchday_inline_button(),
    )


def send_match_list(chat_id):
    """Send today's/tomorrow's matches as tappable inline buttons."""
    if world_cup_over():
        safe_send(
            chat_id,
            f"{WARNING}\n\n🏆 The World Cup is over. Thanks for watching — see you next time!",
            reply_markup=main_keyboard(),
        )
        return
    try:
        matches = load_match_list()
    except Exception:
        safe_send(
            chat_id,
            f"{WARNING}\n\n⚠️ Couldn't load the fixture list right now. Please try again in a moment.",
            reply_markup=main_keyboard(),
        )
        return

    if not matches:
        safe_send(
            chat_id,
            f"{WARNING}\n\nNo World Cup matches scheduled for today or tomorrow.",
            reply_markup=main_keyboard(),
        )
        return

    kb = types.InlineKeyboardMarkup()
    for m in matches:
        # Encode teams directly in callback_data so no server-side cache is needed.
        kb.add(types.InlineKeyboardButton(
            match_button_label(m),
            callback_data=f"m:{m['home']}|{m['away']}",
        ))

    safe_send(
        chat_id,
        f"{WARNING}\n\n⚽ <b>Today &amp; tomorrow — pick a match:</b>",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in MATCH_DAY_TRIGGERS)
def on_match_day(message):
    send_match_list(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "show_matchday")
def on_show_matchday(call):
    bot.answer_callback_query(call.id, "📅 Loading matches…")
    send_match_list(call.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("m:"))
def on_match_pick(call):
    if world_cup_over():
        bot.answer_callback_query(call.id, "🏆 The World Cup is over.", show_alert=True)
        return
    payload = call.data[2:]
    if "|" not in payload:
        bot.answer_callback_query(call.id, "Tap 📅 Match Day again.", show_alert=True)
        return
    home, away = payload.split("|", 1)

    bot.answer_callback_query(call.id, "🔄 Creating links…")
    placeholder = safe_send(
        call.message.chat.id,
        f"🔄 <b>Creating fresh links…</b>\n{home} vs {away}\n{LOADER_FRAMES[0]}",
    )
    if placeholder is None:  # network blip on the placeholder send; nothing to edit
        return

    chat_id = placeholder.chat.id
    msg_id = placeholder.message_id

    # Animate a loader while we fetch live links (no cache — always fresh).
    stop_event = threading.Event()
    loader = threading.Thread(
        target=run_loader, args=(stop_event, chat_id, msg_id, home, away), daemon=True
    )
    loader.start()
    try:
        unique, source_count = collect_unique_links(home, away)
    except Exception:
        unique, source_count = [], 0
    finally:
        stop_event.set()
        loader.join(timeout=2)

    if not unique:
        safe_edit(
            f"{WARNING}\n\n❌ <b>{home} vs {away}</b>\n"
            "No working links found yet. Please try again closer to kickoff.",
            chat_id,
            msg_id,
        )
        return

    shown = unique[:MAX_LINK_BUTTONS]
    kb = types.InlineKeyboardMarkup()
    row = []
    for i, link in enumerate(shown, 1):
        row.append(types.InlineKeyboardButton(f"🔗 Link {i}", url=link["url"]))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)

    extra = "" if len(unique) <= MAX_LINK_BUTTONS else f" (showing first {MAX_LINK_BUTTONS})"
    safe_edit(
        f"{WARNING}\n\n✅ <b>{home} vs {away}</b>\n"
        f"Found <b>{len(unique)}</b> unique link(s) from {source_count} source(s){extra}.\n"
        "Tap a link below:",
        chat_id,
        msg_id,
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in OWNER_TRIGGERS)
def on_owner(message):
    """Reply with the owner's contact."""
    safe_send(
        message.chat.id,
        f"{WARNING}\n\n👑 <b>Bot owner:</b> {OWNER_USERNAME}\nReach out for any help.",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(func=lambda m: True)
def on_other(message):
    """Anything else -> suggest checking today's matches, don't process."""
    safe_send(
        message.chat.id,
        f"{WARNING}\n\n🤖 I only share World Cup stream links.\n"
        "👉 Tap <b>📅 Match Day</b> below to see today's &amp; tomorrow's matches.",
        reply_markup=matchday_inline_button(),
    )


def start_health_server():
    """Tiny HTTP server so hosts like Render (which require an open port) see a
    healthy web service. Also serves as the URL an uptime pinger can hit to keep
    a free instance awake. Runs in a daemon thread; the bot keeps polling."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK - World Cup link bot is running")

        def log_message(self, *args):
            pass  # silence per-request logging

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    # Health/keep-alive web server in the background (needed on Render free tier).
    threading.Thread(target=start_health_server, daemon=True).start()
    print("Bot started. Press Ctrl+C to stop.")
    bot.infinity_polling(skip_pending=True)
