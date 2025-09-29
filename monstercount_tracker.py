import os
import random
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Seiten
RANKING_URL = "https://pr-underworld.com/website/ranking/"
MONSTER_URL = "https://pr-underworld.com/website/monstercount/"

# Settings
GUILD_NAME = "beQuiet"
TIMEOUT = 20
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
TEXT_FILE = Path("texts_monsterkills.txt")   # deine Sprüche-Datei
MAX_LINES = 25                                # wie viele Zeilen im Ranking
TEST_RUN = os.getenv("TEST_MONSTERCOUNT", "false").lower() == "true"  # Testlauf?

# ----------------- Helpers -----------------

def pick_line(path: Path) -> str:
    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return random.choice(lines) if lines else "Hunt well, hunt often."
    except Exception:
        return "Hunt well, hunt often."

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())

def find_netherworld_table(soup: BeautifulSoup):
    """Nimmt die Tabelle direkt unter der Überschrift 'Netherworld'."""
    for h in soup.find_all(["h1","h2","h3","h4","h5","h6"]):
        if h.get_text(strip=True).lower().startswith("netherworld"):
            return h.find_next("table")
    return None

def post_discord(content: str):
    if not WEBHOOK:
        print("WARN: DISCORD_WEBHOOK_URL fehlt – kein Post zu Discord.")
        print(content)
        return
    r = requests.post(WEBHOOK, json={"content": content}, timeout=15)
    r.raise_for_status()

# ----------------- Parsing -----------------

def load_bequiet_names_from_ranking() -> set[str]:
    """Parst /ranking/ (Netherworld) und nimmt NUR beQuiet-Spieler.
       Spalten laut deinem Beispiel: 0:pos(th), 1:icon, 2:name, 3:level, 4:jobimg, 5:percent, 6:guild"""
    r = requests.get(RANKING_URL, timeout=TIMEOUT, headers={"User-Agent": "beQuiet monstercount"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    table = find_netherworld_table(soup)
    if not table:
        print("Ranking: Netherworld-Tabelle nicht gefunden", file=sys.stderr)
        return set()

    names = set()
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        name = tds[2].get_text(strip=True)
        guild = tds[6].get_text(" ", strip=True)
        if GUILD_NAME.lower() in guild.lower():
            names.add(norm(name))
    return names

def load_monstercount() -> list[tuple[str, int]]:
    """Parst /monstercount/ (Netherworld).
       Spalten laut deinem Beispiel: 0:name, 1:kills"""
    r = requests.get(MONSTER_URL, timeout=TIMEOUT, headers={"User-Agent": "beQuiet monstercount"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    table = find_netherworld_table(soup)
    if not table:
        print("Monstercount: Netherworld-Tabelle nicht gefunden", file=sys.stderr)
        return []

    out = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(strip=True)
        kills_txt = tds[1].get_text(strip=True)
        # nur Ziffern
        m = re.findall(r"\d+", kills_txt)
        kills = int("".join(m)) if m else 0
        out.append((name, kills))
    return out

# ----------------- Main -----------------

def main():
    # 1) Spruch
    line = pick_line(TEXT_FILE)

    # 2) beQuiet-Spieler aus Ranking ziehen (Netherworld)
    bequiet_names = load_bequiet_names_from_ranking()

    # 3) Monstercount-Liste ziehen (Netherworld)
    all_counts = load_monstercount()

    # 4) join: nur Spieler, die in beQuiet sind
    filtered = [(name, kills) for (name, kills) in all_counts if norm(name) in bequiet_names]

    # 5) sortiert (kills desc) + Top-N
    filtered.sort(key=lambda x: x[1], reverse=True)
    top = filtered[:MAX_LINES]

    # 6) Discord-Text
    header = "🧪 Testlauf (Monstercount): Script läuft und kann posten.\n" if TEST_RUN else ""
    title = f"**Netherworld – Daily Monstercount ({GUILD_NAME})**\n"
    body = f"{line}\n\n"

    if not top:
        body += "Heute leider keine beQuiet-Kills gefunden."
    else:
        lines = []
        rank = 1
        for name, kills in top:
            # zwei Varianten, wie gewünscht
            if rank % 2 == 1:
                lines.append(f"**{rank}.** {name} hunted **{kills}** mobs")
            else:
                lines.append(f"**{rank}.** {name} killed **{kills}** monsters")
            rank += 1
        body += "\n".join(lines)

    content = header + title + body

    post_discord(content)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)
