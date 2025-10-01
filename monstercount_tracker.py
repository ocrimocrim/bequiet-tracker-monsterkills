import os
import sys
import json
import time
import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

STATE_FILE = "monstercount_state.json"        # liegt direkt im Repo und wird committet
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BEQUIET_URL = "https://www.bequiet.com/de/news"
BERLIN = ZoneInfo("Europe/Berlin")

# -----------------------------------------
# State
# -----------------------------------------

def _empty_state():
    return {
        "last_daily": "",
        "last_weekly": "",
        "last_monthly": "",
        "daily_sums": {},   # "YYYY-MM-DD" -> int
        "week_sums": {},    # "YYYY-Www"   -> int (ISO-Woche)
        "month_sums": {}    # "YYYY-MM"    -> int
    }

def load_state():
    if not os.path.exists(STATE_FILE):
        return _empty_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        base = _empty_state()
        base.update(data)
        return base
    except Exception as e:
        logging.error("State defekt, starte leer: %s", e)
        return _empty_state()

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# -----------------------------------------
# Discord
# -----------------------------------------

def post_to_discord(message, retries=3):
    if not DISCORD_WEBHOOK:
        logging.warning("Kein DISCORD_WEBHOOK gesetzt. Nachricht wäre gewesen:\n%s", message)
        return
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(DISCORD_WEBHOOK, json={"content": message}, timeout=15)
            if resp.status_code == 204:
                return
            logging.error("Discord HTTP %s Versuch %s", resp.status_code, attempt)
        except Exception as e:
            logging.error("Discord Ausnahme Versuch %s %s", attempt, e)
        time.sleep(2 * attempt)

# -----------------------------------------
# Heutige Zahl beziehen
# -----------------------------------------
# Placeholder: liest optional eine Zahl aus kills_today.txt.
# Wenn die Datei fehlt/leer ist, wird 0 verwendet.
def get_kills_today():
    path = "kills_today.txt"
    if os.path.exists(path):
        try:
            content = open(path, "r", encoding="utf-8").read().strip()
            if content:
                return int(content)
        except Exception as e:
            logging.error("kills_today.txt konnte nicht gelesen werden %s", e)
    return 0

# -----------------------------------------
# Aggregation
# -----------------------------------------

def add_to_aggregates(state, local_dt, kills_today):
    day_key = local_dt.strftime("%Y-%m-%d")
    week_key = local_dt.strftime("%G-W%V")
    month_key = local_dt.strftime("%Y-%m")

    state["daily_sums"][day_key] = state["daily_sums"].get(day_key, 0) + kills_today
    state["week_sums"][week_key] = state["week_sums"].get(week_key, 0) + kills_today
    state["month_sums"][month_key] = state["month_sums"].get(month_key, 0) + kills_today

# -----------------------------------------
# Zeitfenster 23:50 bis 23:59 Europa/Berlin
# -----------------------------------------

def in_evening_window(dt_utc):
    local = dt_utc.astimezone(BERLIN)
    start = local.replace(hour=23, minute=50, second=0, microsecond=0)
    end = local.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start <= local <= end

def is_daily_slot(dt_utc):
    return in_evening_window(dt_utc)

def is_weekly_slot(dt_utc):
    local = dt_utc.astimezone(BERLIN)
    return local.weekday() == 6 and in_evening_window(dt_utc)  # Sonntag

def is_monthly_slot(dt_utc):
    local = dt_utc.astimezone(BERLIN)
    return local.day == 1 and in_evening_window(dt_utc)

# -----------------------------------------
# Optionaler Homepage-Scan
# -----------------------------------------

def run_homepage_scan(now_utc):
    local_hour = now_utc.astimezone(BERLIN).hour
    if local_hour in [10, 18, 21]:
        try:
            response = requests.get(BEQUIET_URL, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            titles = [h2.get_text(strip=True) for h2 in soup.find_all("h2")]
            if titles:
                post_to_discord("🧭 Neue beQuiet-Namen von der Homepage aufgenommen")
        except Exception as e:
            logging.error("Fehler beim Homepage-Scan %s", e)

# -----------------------------------------
# Main
# -----------------------------------------

def main():
    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(BERLIN)

    state = load_state()

    # Tageswert holen und direkt addieren
    kills_today = get_kills_today()
    add_to_aggregates(state, local, kills_today)

    # Optionaler Info-Ping
    run_homepage_scan(now_utc)

    today_key = local.strftime("%Y-%m-%d")
    week_key = local.strftime("%G-W%V")
    month_key = local.strftime("%Y-%m")

    # Daily
    if is_daily_slot(now_utc):
        if state.get("last_daily") != today_key:
            value = state["daily_sums"].get(today_key, 0)
            post_to_discord(f"📊 Daily Kills {today_key} insgesamt {value}")
            state["last_daily"] = today_key

    # Weekly
    if is_weekly_slot(now_utc):
        if state.get("last_weekly") != week_key:
            total = state["week_sums"].get(week_key, 0)
            post_to_discord(f"📈 Weekly Kills {week_key} insgesamt {total}")
            state["last_weekly"] = week_key
            # neue Woche neu sammeln
            state["week_sums"][week_key] = 0

    # Monthly
    if is_monthly_slot(now_utc):
        if state.get("last_monthly") != month_key:
            total = state["month_sums"].get(month_key, 0)
            post_to_discord(f"📅 Monthly Kills {month_key} insgesamt {total}")
            state["last_monthly"] = month_key
            # neuer Monat neu sammeln
            state["month_sums"][month_key] = 0

    save_state(state)

if __name__ == "__main__":
    main()
