# members_sync.py
# Fügt beQuiet-Spieler aus der Online-Tabelle zu members_bequiet.txt hinzu.
# Keine Löschungen. Bei jedem neuen Namen geht eine Discord-Nachricht raus.

from __future__ import annotations
import os
import random
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set

import requests
from bs4 import BeautifulSoup

GUILD_NAME = "beQuiet"
HOMEPAGE_URL = "https://pr-underworld.com/website/"
REPO_DIR = Path(__file__).resolve().parent
MEMBERS_FILE = REPO_DIR / "members_bequiet.txt"
TIMEOUT = 20

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK", "").strip()

# Texte ohne Emoji-Präfix
BASE_MESSAGES = [
    "NAME joined the battlefield for beQuiet.",
    "NAME entered the warzone for beQuiet.",
    "NAME raised the sword for beQuiet.",
    "NAME returned to the frontlines for beQuiet.",
    "NAME unleashed fury for beQuiet.",
    "NAME charged into battle for beQuiet.",
    "NAME claimed another victory for beQuiet.",
    "NAME brought chaos to the enemy for beQuiet.",
    "NAME stood fearless in the fight for beQuiet.",
    "NAME spilled blood for beQuiet.",
    "NAME fought through fire for beQuiet.",
    "NAME broke the enemy’s line for beQuiet.",
    "NAME roared into the war for beQuiet.",
    "NAME conquered the battlefield for beQuiet.",
    "NAME made the ground tremble for beQuiet.",
    "NAME rose again to fight for beQuiet.",
    "NAME shattered shields for beQuiet.",
    "NAME hunted glory for beQuiet.",
    "NAME faced death with honor for beQuiet.",
    "NAME left no survivors for beQuiet.",
    "__EMOJI_ONLY__",  # Spezialfall nur Emoji
]

def fetch_homepage_html() -> str:
    r = requests.get(
        HOMEPAGE_URL,
        timeout=TIMEOUT,
        headers={"User-Agent": "bequiet-bot/1.0"},
    )
    r.raise_for_status()
    return r.text

def parse_online_bequiet_names(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    names: Set[str] = set()

    # Tabelle unter der Überschrift Netherworld
    # Relevante Zeilen liegen im tbody
    for tbody in soup.find_all("tbody"):
        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue

            # In deinem HTML steht der Name in der ersten td,
            # da die Rangnummer als th davor steht.
            player_name = tds[0].get_text(strip=True)

            # Gilde steht als Text in der letzten td
            guild_text = tds[-1].get_text(" ", strip=True)

            if player_name and GUILD_NAME.lower() in guild_text.lower():
                names.add(player_name)

    return names

def load_members() -> Dict[str, str]:
    data: Dict[str, str] = {}
    if MEMBERS_FILE.exists():
        for line in MEMBERS_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s:
                data[s.lower()] = s
    return data

def save_members(mem: Dict[str, str]) -> None:
    MEMBERS_FILE.write_text(
        "\n".join(sorted(mem.values(), key=lambda x: x.lower())) + "\n",
        encoding="utf-8",
    )

def add_missing(current: Dict[str, str], to_add: Iterable[str]) -> List[str]:
    added: List[str] = []
    for n in to_add:
        key = n.lower()
        if key not in current:
            current[key] = n
            added.append(n)
    return added

def post_discord(text: str) -> None:
    if not WEBHOOK_URL:
        return
    try:
        requests.post(WEBHOOK_URL, json={"content": text}, timeout=TIMEOUT)
    except Exception as e:
        print(f"Webhook-Fehler {e}", file=sys.stderr)

def format_message(name: str) -> str:
    template = random.choice(BASE_MESSAGES)
    if template == "__EMOJI_ONLY__":
        return "🧭"
    return "🧭 " + template.replace("NAME", name)

def run_once() -> List[str]:
    html = fetch_homepage_html()
    online = parse_online_bequiet_names(html)

    mem = load_members()
    added = add_missing(mem, online)

    if added:
        save_members(mem)
        for name in added:
            post_discord(format_message(name))

    return added

if __name__ == "__main__":
    try:
        added = run_once()
        if added:
            print("Gildenliste erweitert")
            print("Hinzugefügt " + ", ".join(sorted(added)))
        else:
            print("Keine neuen beQuiet-Spieler gefunden")
    except Exception as e:
        print(f"Fehler in members_sync.py {e}", file=sys.stderr)
        sys.exit(1)
