import os
import sys
import json
import time
import pytz
import logging
import requests
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

STATE_FILE = "monstercount_state.json"
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BEQUIET_URL = "https://www.bequiet.com/de/news"

BERLIN = pytz.timezone("Europe/Berlin")

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def post_to_discord(message, retries=3):
    if not DISCORD_WEBHOOK:
        logging.warning("Kein DISCORD_WEBHOOK gesetzt. Nachricht wäre gewesen:\n%s", message)
        return
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(DISCORD_WEBHOOK, json={"content": message}, timeout=15)
            if resp.status_code == 204:
                return
            logging.error("Discord Fehler HTTP %s Versuch %s", resp.status_code, attempt)
        except Exception as e:
            logging.error("Discord Ausnahme Versuch %s %s", attempt, e)
        time.sleep(2 * attempt)

# -----------------------------------------
# Zeitfenster 23:50–23:59 Europa/Berlin
# -----------------------------------------

def in_2355_window(dt_utc):
    local = dt_utc.astimezone(BERLIN)
    start = local.replace(hour=23, minute=50, second=0, microsecond=0)
    end = local.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start <= local <= end

def is_daily_slot(dt_utc):
    return in_2355_window(dt_utc)

def is_weekly_slot(dt_utc):
    local = dt_utc.astimezone(BERLIN)
    return local.weekday() == 6 and in_2355_window(dt_utc)

def is_monthly_slot(dt_utc):
    local = dt_utc.astimezone(BERLIN)
    return local.day == 1 and in_2355_window(dt_utc)

# -----------------------------------------
# Homepage-Scan
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
            logging.error("Fehler beim Homepage-Scan: %s", e)

# -----------------------------------------
# Main
# -----------------------------------------

def main():
    now_utc = datetime.now(pytz.utc)
    state = load_state()

    # Homepage-Scan
    run_homepage_scan(now_utc)

    local = now_utc.astimezone(BERLIN)
    today_str = local.strftime("%Y-%m-%d")
    week_str = local.strftime("%G-W%V")
    month_str = local.strftime("%Y-%m")

    if is_daily_slot(now_utc):
        if state.get("last_daily") != today_str:
            post_to_discord("📊 Daily Kill-Liste gepostet")
            state["last_daily"] = today_str

    if is_weekly_slot(now_utc):
        if state.get("last_weekly") != week_str:
            post_to_discord("📈 Weekly Kill-Liste gepostet")
            state["last_weekly"] = week_str

    if is_monthly_slot(now_utc):
        if state.get("last_monthly") != month_str:
            post_to_discord("📅 Monthly Kill-Liste gepostet")
            state["last_monthly"] = month_str

    save_state(state)

if __name__ == "__main__":
    main()
