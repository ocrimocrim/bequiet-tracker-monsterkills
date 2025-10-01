import os
import sys
import json
import time
import random
import logging
import requests
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

# -------------------------------------------------
# Grundeinstellungen
# -------------------------------------------------

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

BERLIN = ZoneInfo("Europe/Berlin")

STATE_FILE = "monstercount_state.json"
ROSTER_FILE = "members_bequiet.txt"
SPRUCH_FILE = "texts_monsterkills.txt"

# Quellen
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
MONSTERCOUNT_URL = os.getenv("MONSTERCOUNT_URL", "").strip()  # optional, sonst kein Web-Fetch
BEQUIET_URL = "https://www.bequiet.com/de/news"

# Gildenname exakt so, wie er in der Tabelle steht
GUILD_NAME = "beQuiet"

# Ausgabe-Parameter
MAX_LINES = 40        # Hartes Maximum der Listenzeilen
DISCORD_SAFE_LIMIT = 1900  # Sicherheitslimit für den Text (Discord 2000 Zeichen)

# -------------------------------------------------
# State laden/speichern
# -------------------------------------------------

def _empty_state():
    return {
        "last_daily": "",
        "last_weekly": "",
        "last_monthly": "",
        # Pro Tag, Woche, Monat pro Member akkumuliert
        "daily_per_member": {},   # "YYYY-MM-DD": {member: kills}
        "weekly_per_member": {},  # "YYYY-Www":   {member: kills}  ISO-Woche
        "monthly_per_member": {}, # "YYYY-MM":    {member: kills}
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

# -------------------------------------------------
# Utils
# -------------------------------------------------

def berlin_now_utc():
    return datetime.now(timezone.utc)

def is_last_day_of_month(local_dt: datetime) -> bool:
    next_day = local_dt + timedelta(days=1)
    return next_day.month != local_dt.month

def in_evening_window_berlin(dt_utc: datetime) -> bool:
    local = dt_utc.astimezone(BERLIN)
    # Exakt 23:50:00 bis 23:59:59.999
    start = local.replace(hour=23, minute=50, second=0, microsecond=0)
    end = local.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start <= local <= end

def in_daily_slot(dt_utc: datetime) -> bool:
    return in_evening_window_berlin(dt_utc)

def in_weekly_slot(dt_utc: datetime) -> bool:
    local = dt_utc.astimezone(BERLIN)
    return local.isoweekday() == 7 and in_evening_window_berlin(dt_utc)  # Sonntag

def in_monthly_slot(dt_utc: datetime) -> bool:
    local = dt_utc.astimezone(BERLIN)
    return is_last_day_of_month(local) and in_evening_window_berlin(dt_utc)

# -------------------------------------------------
# Discord
# -------------------------------------------------

def post_to_discord(message: str, retries: int = 3):
    if not DISCORD_WEBHOOK:
        logging.warning("Kein DISCORD_WEBHOOK gesetzt. Nachricht wäre gewesen:\n%s", message)
        return
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(DISCORD_WEBHOOK, json={"content": message}, timeout=20)
            if resp.status_code == 204:
                return
            logging.error("Discord HTTP %s Versuch %s", resp.status_code, attempt)
        except Exception as e:
            logging.error("Discord Ausnahme Versuch %s %s", attempt, e)
        time.sleep(2 * attempt)

# -------------------------------------------------
# Roster
# -------------------------------------------------

def load_roster() -> set[str]:
    names: set[str] = set()
    if not os.path.exists(ROSTER_FILE):
        return names
    with open(ROSTER_FILE, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if name:
                names.add(name)
    return names

def persist_new_members(new_names: set[str]):
    if not new_names:
        return
    # Füge deterministisch am Ende an
    with open(ROSTER_FILE, "a", encoding="utf-8") as f:
        for name in sorted(new_names):
            f.write(f"{name}\n")
    logging.info("Neue Mitglieder ergänzt: %s", ", ".join(sorted(new_names)))

# -------------------------------------------------
# Homepage-Scan für neue Namen
# -------------------------------------------------

def scrape_homepage_members() -> set[str]:
    """
    Sammelt Kandidaten von der beQuiet-Homepage.
    Logik: Suche in <h2> und <li> nach möglichen Char-Namen, filtere triviale Wörter raus.
    Das ist bewusst tolerant. Endgültige Filterung erfolgt gegen Ranking-Namen.
    """
    names = set()
    try:
        r = requests.get(BEQUIET_URL, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Kandidaten aus H2 und LI
        for tag in soup.find_all(["h2", "li"]):
            txt = tag.get_text(" ", strip=True)
            for token in txt.split():
                # primitive Heuristik für Char-Namen
                if 2 <= len(token) <= 16 and token.isalnum():
                    names.add(token)
    except Exception as e:
        logging.error("Homepage-Scan fehlgeschlagen: %s", e)
    return names

# -------------------------------------------------
# Monstercount-Tabelle parsen
# -------------------------------------------------

def parse_monstercount_table() -> dict[str, int]:
    """
    Gibt ein Mapping {Name -> Kills} zurück, ausschließlich für Gilde beQuiet.
    Erwartet eine HTML-Tabelle mit Spalten ähnlich 'Name', 'Guild', 'Kills'.
    Selektoren bitte anpassen, falls deine Quelle anders strukturiert ist.
    Wenn MONSTERCOUNT_URL leer ist oder etwas schiefgeht, gibt es ein leeres Dict.
    """
    results: dict[str, int] = {}
    if not MONSTERCOUNT_URL:
        logging.info("Keine MONSTERCOUNT_URL gesetzt. Überspringe Web-Fetch.")
        return results

    try:
        r = requests.get(MONSTERCOUNT_URL, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Beispiel: erste Tabelle auf der Seite
        table = soup.find("table")
        if not table:
            logging.warning("Keine Tabelle gefunden.")
            return results

        # Kopfzeile inspizieren, Spaltenindex bestimmen
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        idx_name = next((i for i, h in enumerate(headers) if "name" in h), None)
        idx_guild = next((i for i, h in enumerate(headers) if "guild" in h or "gilde" in h), None)
        idx_kills = next((i for i, h in enumerate(headers) if "kill" in h), None)

        # Fallback, falls keine thead/th
        if idx_name is None or idx_kills is None or idx_guild is None:
            # versuche generisch: nimm td[0]=Name, td[1]=Guild, td[2]=Kills
            idx_name, idx_guild, idx_kills = 0, 1, 2

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < max(idx_name, idx_guild, idx_kills) + 1:
                continue
            name = tds[idx_name].get_text(strip=True)
            guild = tds[idx_guild].get_text(strip=True)
            kills_txt = tds[idx_kills].get_text(strip=True).replace(",", "").replace(".", "")
            try:
                kills = int("".join(ch for ch in kills_txt if ch.isdigit()))
            except Exception:
                kills = 0

            if guild.strip().lower() == GUILD_NAME.lower() and name:
                results[name] = max(results.get(name, 0), kills)

        return results
    except Exception as e:
        logging.error("Monstercount-Fetch/Parse fehlgeschlagen: %s", e)
        return {}

# -------------------------------------------------
# Aggregation
# -------------------------------------------------

def add_today_to_aggregates(state: dict, local_dt: datetime, per_member_today: dict[str, int]):
    day_key = local_dt.strftime("%Y-%m-%d")
    week_key = local_dt.strftime("%G-W%V")
    month_key = local_dt.strftime("%Y-%m")

    # Daily
    dmap = state["daily_per_member"].get(day_key, {})
    for n, v in per_member_today.items():
        dmap[n] = dmap.get(n, 0) + v
    state["daily_per_member"][day_key] = dmap

    # Weekly
    wmap = state["weekly_per_member"].get(week_key, {})
    for n, v in per_member_today.items():
        wmap[n] = wmap.get(n, 0) + v
    state["weekly_per_member"][week_key] = wmap

    # Monthly
    mmap = state["monthly_per_member"].get(month_key, {})
    for n, v in per_member_today.items():
        mmap[n] = mmap.get(n, 0) + v
    state["monthly_per_member"][month_key] = mmap

# -------------------------------------------------
# Spruch
# -------------------------------------------------

def pick_spruch() -> str:
    if not os.path.exists(SPRUCH_FILE):
        return "The mobs fell, the loot rolled, and morale stayed high."
    lines = []
    with open(SPRUCH_FILE, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t:
                lines.append(t)
    if not lines:
        return "The mobs fell, the loot rolled, and morale stayed high."
    return random.choice(lines)

# -------------------------------------------------
# Formatierung und Kürzung
# -------------------------------------------------

def format_lines_from_map(d: dict[str, int]) -> list[str]:
    items = [(k, v) for k, v in d.items() if v > 0]
    items.sort(key=lambda kv: (-kv[1], kv[0].lower()))
    lines = []
    for i, (name, val) in enumerate(items[:MAX_LINES], start=1):
        verb = "hunted" if i % 2 else "killed"
        lines.append(f"{i}. {name} {verb} {val} mobs")
    if not lines:
        lines = ["keine Einträge"]
    return lines

def build_message(title: str, per_member_map: dict[str, int]) -> str:
    header = f"**Netherworld {title} ({GUILD_NAME})**"
    spruch = pick_spruch()
    body_lines = format_lines_from_map(per_member_map)
    body = "\n".join(body_lines)
    msg = f"{header}\n{spruch}\n\n{body}"

    # Sicherheitskürzung auf DISCORD_SAFE_LIMIT Zeichen
    if len(msg) <= DISCORD_SAFE_LIMIT:
        return msg

    # binäre Suche nach maximaler Zeilenzahl die passt
    low, high = 0, len(body_lines)
    best_body = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = "\n".join(body_lines[:mid]) if mid > 0 else "keine Einträge"
        candidate_msg = f"{header}\n{spruch}\n\n{candidate}"
        if len(candidate_msg) <= DISCORD_SAFE_LIMIT:
            best_body = candidate
            low = mid + 1
        else:
            high = mid - 1
    return f"{header}\n{spruch}\n\n{best_body}"

# -------------------------------------------------
# Main
# -------------------------------------------------

def main():
    now_utc = berlin_now_utc()
    local = now_utc.astimezone(BERLIN)

    # 1) Roster laden
    roster = load_roster()

    # 2) Ranking laden und auf beQuiet filtern
    name_to_kills = parse_monstercount_table()

    # 3) Neue Namen von der Homepage ergänzen, aber nur falls sie auch im Ranking erscheinen
    homepage_candidates = scrape_homepage_members()
    new_names = {n for n in homepage_candidates if n in name_to_kills and n not in roster}
    if new_names:
        persist_new_members(new_names)
        post_to_discord("🧭 Neue beQuiet-Namen von der Homepage aufgenommen")

    # 4) Effektive Menge der zu zählenden Spieler
    effective_names = set(name_to_kills.keys()) | roster
    # auf beQuiet filtert parse_monstercount_table bereits. Roster ergänzt Alt-Namen ohne aktuelle Kills.

    # 5) Heutige Kills pro Person (nur Namen, die wir tatsächlich in der Tabelle finden)
    per_member_today: dict[str, int] = {n: name_to_kills.get(n, 0) for n in effective_names if n in name_to_kills}

    state = load_state()
    add_today_to_aggregates(state, local, per_member_today)

    # Schlüssel für Flags
    day_key = local.strftime("%Y-%m-%d")
    week_key = local.strftime("%G-W%V")
    month_key = local.strftime("%Y-%m")

    # Daily
    if in_daily_slot(now_utc) and state.get("last_daily") != day_key:
        day_map = state["daily_per_member"].get(day_key, {})
        post_to_discord(build_message("Daily Monstercount", day_map))
        state["last_daily"] = day_key

    # Weekly (Sonntag)
    if in_weekly_slot(now_utc) and state.get("last_weekly") != week_key:
        wmap = state["weekly_per_member"].get(week_key, {})
        post_to_discord(build_message(f"Weekly Monstercount {week_key}", wmap))
        state["last_weekly"] = week_key
        # Woche für den neuen Zyklus leeren
        state["weekly_per_member"][week_key] = {}

    # Monthly (letzter Tag des Monats)
    if in_monthly_slot(now_utc) and state.get("last_monthly") != month_key:
        mmap = state["monthly_per_member"].get(month_key, {})
        post_to_discord(build_message(f"Monthly Monstercount {month_key}", mmap))
        state["last_monthly"] = month_key
        # Monat für den neuen Zyklus leeren
        state["monthly_per_member"][month_key] = {}

    save_state(state)

if __name__ == "__main__":
    main()
