import os
import re
import sys
import random
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# URLs
RANKING_URL = "https://pr-underworld.com/website/ranking/"
MONSTER_URL = "https://pr-underworld.com/website/monstercount/"

# Settings
GUILD_NAME = "beQuiet"
TIMEOUT = 25
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
TEXT_FILE = Path("texts_monsterkills.txt")   # deine Sprüche-Datei
MAX_LINES = 40                                # wie viele Zeilen im Ranking
TEST = os.getenv("TEST_MONSTERCOUNT", "false").lower() == "true"

UA = {"User-Agent": "beQuiet Monstercount Tracker (+GitHub Actions)"}

# ----------------- Helpers -----------------

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def pick_line(path: Path) -> str:
    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return random.choice(lines) if lines else "Hunt well, hunt often."
    except Exception:
        return "Hunt well, hunt often."

def post_discord(content: str):
    if not WEBHOOK:
        print("WARN: DISCORD_WEBHOOK_URL fehlt – poste nur ins Log.")
        print(content)
        return
    r = requests.post(WEBHOOK, json={"content": content}, timeout=15)
    r.raise_for_status()

def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, timeout=TIMEOUT, headers=UA)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def find_netherworld_table(soup: BeautifulSoup):
    """Nimm die Tabelle direkt unter der Überschrift 'Netherworld' (Underworld ignorieren)."""
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if h.get_text(strip=True).lower().startswith("netherworld"):
            return h.find_next("table")
    return None

def header_index_map(table, expected):
    """
    Erwartete Labels -> Index-Mapping aus dem thead.
    expected: dict {logical_name: [list of substrings to search]}
    """
    idx = {}
    thead = table.find("thead")
    if thead:
        labels = [th.get_text(" ", strip=True).lower() for th in thead.find_all("th")]
        for key, keysubs in expected.items():
            for i, lab in enumerate(labels):
                if any(sub in lab for sub in keysubs):
                    idx[key] = i
                    break
    return idx

# ----------------- Ranking: beQuiet-Namen ziehen -----------------

def load_bequiet_names_from_ranking() -> set[str]:
    soup = get_soup(RANKING_URL)
    table = find_netherworld_table(soup)
    if not table:
        print("Ranking: Netherworld-Tabelle nicht gefunden", file=sys.stderr)
        return set()

    # Mappe Spalten dynamisch
    idx = header_index_map(table, {
        "name": ["name"],
        "guild": ["guild"],
    })

    names = set()
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        # Fallback-Indizes, falls thead fehlt/abweicht:
        name_i = idx.get("name", 1 if len(tds) > 1 else 0)
        guild_i = idx.get("guild", len(tds) - 1)

        try:
            name = tds[name_i].get_text(strip=True)
        except Exception:
            continue

        try:
            guild = tds[guild_i].get_text(" ", strip=True)
        except Exception:
            guild = ""

        if GUILD_NAME.lower() in guild.lower():
            names.add(norm(name))

    return names

# ----------------- Monstercount ziehen -----------------

def load_monstercount() -> list[tuple[str, int]]:
    soup = get_soup(MONSTER_URL)
    table = find_netherworld_table(soup)
    if not table:
        print("Monstercount: Netherworld-Tabelle nicht gefunden", file=sys.stderr)
        return []

    idx = header_index_map(table, {
        "name": ["name"],
        "kills": ["count", "kill", "monster"],
    })

    out = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        # Fallbacks: Name erste TD, Kills letzte TD
        name_i = idx.get("name", 0)
        kills_i = idx.get("kills", len(tds) - 1)

        try:
            name = tds[name_i].get_text(strip=True)
        except Exception:
            continue

        try:
            kills_txt = tds[kills_i].get_text(strip=True)
            nums = re.findall(r"\d+", kills_txt)
            kills = int("".join(nums)) if nums else 0
        except Exception:
            kills = 0

        if name:
            out.append((name, kills))

    return out

# ----------------- Main -----------------

def main():
    # 1) beQuiet aus Ranking (Netherworld)
    beq = load_bequiet_names_from_ranking()
    # 2) Monstercount (Netherworld)
    counts = load_monstercount()

    # Debug-Ausgabe (hilft beim Abgleich)
    if TEST:
        dbg_names = ", ".join(sorted(list(beq))[:15])
        dbg_top = ", ".join(f"{n}:{k}" for n, k in counts[:10])
        post_discord(f"🧪 DEBUG\nbeQuiet im Ranking: {len(beq)} → {dbg_names or '-'}\nTop Monstercount: {dbg_top or '-'}")

    # 3) Join
    joined = [(n, k) for (n, k) in counts if norm(n) in beq and k > 0]
    joined.sort(key=lambda x: x[1], reverse=True)

    # 4) Text bauen
    header = f"**Netherworld – Daily Monstercount ({GUILD_NAME})**"
    flavor = pick_line(TEXT_FILE)

    if not joined:
        post_discord(f"{header}\n{flavor}\n\nHeute leider keine beQuiet-Kills gefunden.")
        return

    lines = []
    for i, (name, kills) in enumerate(joined[:MAX_LINES], 1):
        # abwechselnde Formulierungen
        if i % 2:
            lines.append(f"{i}. **{name}** hunted **{kills}** mobs")
        else:
            lines.append(f"{i}. **{name}** killed **{kills}** monsters")

    msg = f"{header}\n{flavor}\n\n" + "\n".join(lines)
    post_discord(msg)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        if WEBHOOK:
            try:
                post_discord(f"⚠️ Monstercount-Tracker Fehler: `{e}`")
            except Exception:
                pass
        sys.exit(1)
