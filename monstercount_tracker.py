import os
import sys
import json
import pytz
import logging
import requests
from datetime import datetime
from bs4 import BeautifulSoup

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

STATE_FILE = "monstercount_state.json"
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

# Homepage, die gescannt wird
BEQUIET_URL = "https://www.bequiet.com/de/news"

# =========================
# Hilfsfunktionen
# =========================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def post_to_discord(message):
    if not DISCORD_WEBHOOK:
        logging.warning("Kein DISCORD_WEBHOOK gesetzt, Message wäre gewesen:\n%s", message)
        return
    response = requests.post(DISCORD_WEBHOOK, json={"content": message})
    if response.status_code != 204:
        logging.error("Fehler beim Posten nach Discord: %s", response.text)

# =========================
# Slot-Prüfungen
# =========================

def is_daily_slot(dt):
    local = dt.astimezone(pytz.timezone("Europe/Berlin"))
    return local.hour == 23 and local.minute == 55

def is_weekly_slot(dt):
    local = dt.astimezone(pytz.timezone("Europe/Berlin"))
    return local.weekday() == 6 and local.hour == 23 and local.minute == 55  # Sonntag

def is_monthly_slot(dt):
    local = dt.astimezone(pytz.timezone("Europe/Berlin"))
    return local.day == 1 and local.hour == 23 and local.minute == 55

# =========================
# Homepage-Scan
# =========================

def run_homepage_scan(now):
    hour = now.astimezone(pytz.timezone("Europe/Berlin")).hour
    if hour in [10, 18, 21]:
        try:
            response = requests.get(BEQUIET_URL, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            titles = [h2.get_text(strip=True) for h2 in soup.find_all("h2")]
            if titles:
                message = "🧭 Neue beQuiet-Namen von der Homepage aufgenommen"
                post_to_discord(message)
        except Exception as e:
            logging.error("Fehler beim Homepage-Scan: %s", e)

# =========================
# Main
# =========================

def main():
    now = datetime.now(pytz.utc)
    state = load_state()

    # Homepage-Scan
    run_homepage_scan(now)

    local = now.astimezone(pytz.timezone("Europe/Berlin"))
    today_str = local.strftime("%Y-%m-%d")
    week_str = local.strftime("%G-W%V")
    month_str = local.strftime("%Y-%m")

    # Daily
    if is_daily_slot(now):
        if state.get("last_daily") != today_str:
            post_to_discord("📊 Daily Kill-Liste gepostet")
            state["last_daily"] = today_str

    # Weekly
    if is_weekly_slot(now):
        if state.get("last_weekly") != week_str:
            post_to_discord("📈 Weekly Kill-Liste gepostet")
            state["last_weekly"] = week_str

    # Monthly
    if is_monthly_slot(now):
        if state.get("last_monthly") != month_str:
            post_to_discord("📅 Monthly Kill-Liste gepostet")
            state["last_monthly"] = month_str

    save_state(state)

if __name__ == "__main__":
    main()
