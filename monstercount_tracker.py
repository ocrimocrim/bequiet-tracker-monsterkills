import os
import json
import time
import sys
import re
from pathlib import Path
from datetime import datetime, timezone
import random

import requests
from bs4 import BeautifulSoup

# ---- Konfiguration ----
URL = "https://pr-underworld.com/website/monstercount/"
GUILD_NAME = "beQuiet"                 # nur diese Gilde
STATE_FILE = Path("state_monster.json")
TEXTS_FILE = Path("texts_monsterkills.txt")
TIMEOUT = 20
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

# Schalter für Tests
SEND_TEST_MESSAGE = os.getenv("SEND_TEST_MESSAGE", "").lower() == "true"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_posted_utc_date": None}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def post_discord(text: str):
    if not WEBHOOK:
        print("Webhook fehlt. Secret DISCORD_WEBHOOK_URL setzen.", file=sys.stderr)
        return
    r = requests.post(WEBHOOK, json={"content": text}, timeout=10)
    r.raise_for_status()
    print("[OK] Nachricht an Discord gesendet.")

def pick_random_line():
    if TEXTS_FILE.exists():
        lines = [ln.strip() for ln in TEXTS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            return random.choice(lines)
    # Fallback
    return "You hunted where others hesitated—respect."

def find_netherworld_table(soup: BeautifulSoup):
    # suche Überschrift "Netherworld" und nimm die nächste Tabelle
    for h in soup.find_all(["h1","h2","h3","h4","h5","h6"]):
        if h.get_text(strip=True).lower().startswith("netherworld"):
            return h.find_next("table")
    return None

def header_index_map(table):
    """
    Versuche die Spalten dynamisch über den thead zu erkennen.
    Erwartete Header enthalten 'Name' und etwas wie 'Count', 'Kills', 'Monsters' etc.
    """
    idx = {}
    thead = table.find("thead")
    if thead:
        ths = [th.get_text(strip=True).lower() for th in thead.find_all("th")]
        for i, label in enumerate(ths):
            if "name" in label:
                idx["name"] = i
            if "count" in label or "kill" in label or "monster" in label:
                idx["kills"] = i
            if "guild" in label:
                idx["guild"] = i
    return idx

def extract_rows(table):
    rows = []
    idx = header_index_map(table)
    tbody = table.find("tbody")
    if not tbody:
        return rows

    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        # Fallback-Indizes, falls Header nicht erkannt wird:
        # Beobachtet: häufig: #, Online, Name, ???, ???, ???, Guild  (Level-Seite)
        # Für Monstercount nehmen wir pragmatisch:
        name_i = idx.get("name", 2 if len(tds) > 2 else 0)
        kills_i = idx.get("kills", 3 if len(tds) > 3 else min(len(tds)-1, 3))
        guild_i = idx.get("guild", 6 if len(tds) > 6 else len(tds)-1)

        try:
            name = tds[name_i].get_text(strip=True)
        except Exception:
            continue
        try:
            kills_txt = tds[kills_i].get_text(strip=True)
            kills = int(re.sub(r"[^\d]", "", kills_txt) or "0")
        except Exception:
            kills = 0
        try:
            guild = tds[guild_i].get_text(" ", strip=True)
        except Exception:
            guild = ""

        rows.append({"name": name, "kills": kills, "guild": guild})
    return rows

def main():
    state = load_state()

    # Optional: Testmeldung unabhängig vom Ranking
    if SEND_TEST_MESSAGE:
        post_discord("🧪 Testlauf (Monstercount): Script läuft und kann posten.")

    # Seite laden
    r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": "beQuiet monstercount tracker"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    table = find_netherworld_table(soup)
    if not table:
        print("Netherworld-Tabelle (Monstercount) nicht gefunden.", file=sys.stderr)
        return

    rows = [row for row in extract_rows(table) if GUILD_NAME.lower() in row["guild"].lower()]
    # sortiere nach Kills, absteigend
    rows.sort(key=lambda r: r["kills"], reverse=True)

    # Datumskontrolle: poste nur 1x pro Tag (UTC-Tag, da Cron in UTC läuft)
    today_utc = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_posted_utc_date") == today_utc and not SEND_TEST_MESSAGE:
        print("Heute bereits gepostet. Abbruch.")
        return

    # Nachricht bauen
    opener = pick_random_line()
    lines = [f"**Netherworld – Daily Monstercount (beQuiet)**", opener, ""]
    if not rows:
        lines.append("_Keine beQuiet-Spieler gefunden._")
    else:
        for i, r in enumerate(rows, start=1):
            # zwei Varianten zufällig mischen
            if random.choice([True, False]):
                lines.append(f"{i}. **{r['name']}** hunted **{r['kills']}** mobs")
            else:
                lines.append(f"{i}. **{r['name']}** killed **{r['kills']}** monsters")
    message = "\n".join(lines)

    post_discord(message)

    # Zustand merken, damit wir nicht doppelt posten
    state["last_posted_utc_date"] = today_utc
    save_state(state)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)
