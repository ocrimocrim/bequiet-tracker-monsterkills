import os
import re
import sys
import random
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# --------------------------------- Einstellungen ---------------------------------
RANKING_URL = "https://pr-underworld.com/website/ranking/"
MONSTER_URL = "https://pr-underworld.com/website/monstercount/"
GUILD_NAME = "beQuiet"

TIMEOUT = 25
HEADERS = {"User-Agent": "beQuiet Monstercount Tracker (+GitHub Actions)"}

WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
DEBUG = os.getenv("DEBUG_MONSTERCOUNT", "false").lower() == "true"
TEST = os.getenv("TEST_MONSTERCOUNT", "false").lower() == "true"

MAX_LINES = 40  # Anzahl Zeilen im Discord-Ranking

# Unterstütze beide möglichen Dateinamen für deine Sprüche
SPRUCH_FILES = ["texts_monsterkills.txt", "Texts for Monsterkills.txt"]

# Zeitlogik
BERLIN = ZoneInfo("Europe/Berlin")
WINDOW_START_MINUTE = 40  # 23:40
WINDOW_END_MINUTE   = 55  # 23:55 (inklusive)

STATE_FILE = Path("state_monstercount.json")


# --------------------------------- Hilfsfunktionen ---------------------------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_daily_date": ""}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def now_berlin():
    return datetime.now(timezone.utc).astimezone(BERLIN)

def is_in_daily_window(dt: datetime) -> bool:
    # genau 23:40–23:55 Europe/Berlin (inklusive)
    return dt.hour == 23 and WINDOW_START_MINUTE <= dt.minute <= WINDOW_END_MINUTE

def post_discord(content: str):
    """Poste Text zu Discord oder logge, wenn kein Webhook gesetzt."""
    if not WEBHOOK:
        print("WARN: DISCORD_WEBHOOK_URL fehlt – poste nur ins Log:\n" + content)
        return
    r = requests.post(WEBHOOK, json={"content": content}, timeout=15)
    r.raise_for_status()

def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def find_netherworld_table(soup: BeautifulSoup):
    """Nimm die Tabelle direkt unter der Überschrift 'Netherworld' (Underworld ignorieren)."""
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if h.get_text(" ", strip=True).lower().startswith("netherworld"):
            return h.find_next("table")
    return None

def pick_spruch() -> str:
    for name in SPRUCH_FILES:
        p = Path(name)
        if p.exists():
            lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                return random.choice(lines)
    return "Hunt well, hunt often."

def only_digits(text: str) -> int:
    nums = re.findall(r"\d+", text or "")
    return int("".join(nums)) if nums else 0

# ------------------------------- Parser: /ranking/ --------------------------------
def load_bequiet_names_from_ranking() -> set[str]:
    """
    Parsen der Netherworld-Tabelle auf der Ranking-Seite.
    Spalten je Zeile (tds):
      0: Online-Icon (img)
      1: Name
      2: Level
      3: Job (img)
      4: Exp %
      5: Guild (img + Text, enthält 'beQuiet')
    """
    soup = get_soup(RANKING_URL)
    table = find_netherworld_table(soup)
    if not table:
        raise RuntimeError("Ranking: Netherworld-Tabelle nicht gefunden.")

    bequiet = set()
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        name = tds[1].get_text(strip=True)           # Name = td[1]
        guild = tds[-1].get_text(" ", strip=True)    # Gilde = letzte td
        if name and GUILD_NAME.lower() in (guild or "").lower():
            bequiet.add(name.lower())
    return bequiet

# ---------------------------- Parser: /monstercount/ ------------------------------
def load_monstercount() -> list[tuple[str, int]]:
    """
    Parsen der Netherworld-Tabelle auf der Monstercount-Seite.
    Spalten je Zeile (tds):
      0: Name
      1: Monsterkills today
    """
    soup = get_soup(MONSTER_URL)
    table = find_netherworld_table(soup)
    if not table:
        raise RuntimeError("Monstercount: Netherworld-Tabelle nicht gefunden.")

    out = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(strip=True)           # Name = td[0]
        kills = only_digits(tds[1].get_text(strip=True))
        if name:
            out.append((name, kills))
    return out

# -------------------------------------- Main --------------------------------------
def main():
    # Zeitfenster & Einmal-pro-Tag Schutz
    state = load_state()
    berlin_now = now_berlin()
    today_berlin = berlin_now.date().isoformat()

    if TEST:
        # Nur zum testen: sofort posten (ignoriert Zeitfenster/State)
        post_discord("🧪 Testlauf (Monstercount): Script läuft und kann posten.")
    else:
        # echte Logik: nur im Zeitfenster, und nur einmal/Tag
        if not is_in_daily_window(berlin_now):
            print(f"Outside daily window (Berlin {berlin_now:%H:%M}). No post.", file=sys.stderr)
            return
        if state.get("last_daily_date") == today_berlin:
            print(f"Already posted today ({today_berlin}). No post.", file=sys.stderr)
            return

    # 1) beQuiet-Liste aus Ranking (Netherworld)
    bequiet = load_bequiet_names_from_ranking()

    # 2) Tagesliste aus Monstercount (Netherworld)
    all_counts = load_monstercount()

    # 3) Join (nur beQuiet, kills > 0)
    joined = [(n, k) for (n, k) in all_counts if n.lower() in bequiet and k > 0]
    joined.sort(key=lambda x: x[1], reverse=True)

    # 4) Debug-Ausgabe (optional)
    if DEBUG:
        sample_beq = ", ".join(sorted(list(bequiet))[:15]) or "-"
        sample_mc = ", ".join(f"{n}:{k}" for n, k in all_counts[:10]) or "-"
        debug_msg = (
            "🧪 DEBUG Monstercount\n"
            f"- beQuiet im Ranking: {len(bequiet)} → {sample_beq}\n"
            f"- Erster Block Monstercount: {sample_mc}\n"
            f"- Join-Ergebnis: {len(joined)} Spieler"
        )
        try:
            post_discord(debug_msg)
        except Exception as e:
            print(f"DEBUG-Post fehlgeschlagen: {e}", file=sys.stderr)

    # 5) Discord-Message bauen
    header = f"**Netherworld – Daily Monstercount ({GUILD_NAME})**"
    spruch = pick_spruch()

    if not joined:
        diagnose = f"(Ranking beQuiet={len(bequiet)}, Monstercount Zeilen={len(all_counts)})"
        post_discord(f"{header}\n{spruch}\n\nHeute leider keine beQuiet-Kills gefunden. {diagnose}")
    else:
        lines = []
        for i, (name, kills) in enumerate(joined[:MAX_LINES], start=1):
            if i % 2:
                lines.append(f"{i}. **{name}** hunted **{kills}** mobs")
            else:
                lines.append(f"{i}. **{name}** killed **{kills}** monsters")
        msg = f"{header}\n{spruch}\n\n" + "\n".join(lines)
        post_discord(msg)

    # 6) „einmal pro Tag“ markieren
    state["last_daily_date"] = today_berlin
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = f"⚠️ Monstercount-Tracker Fehler: {e}"
        print(err, file=sys.stderr)
        try:
            post_discord(err)
        except Exception:
            pass
        sys.exit(1)
