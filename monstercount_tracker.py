import os
import re
import sys
import random
from pathlib import Path

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


# --------------------------------- Hilfsfunktionen ---------------------------------
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
            # zu kurze Zeile, überspringen
            continue
        name = tds[1].get_text(strip=True)           # <-- Name fest: td[1]
        guild = tds[-1].get_text(" ", strip=True)    # <-- Gilde ist letzte td
        if not name:
            continue
        if GUILD_NAME.lower() in (guild or "").lower():
            bequiet.add(name.lower())                # nur lower, kein Space-Gefummel

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
        name = tds[0].get_text(strip=True)           # <-- Name fest: td[0]
        kills = only_digits(tds[1].get_text(strip=True))
        if name:
            out.append((name, kills))
    return out


# -------------------------------------- Main --------------------------------------
def main():
    # 0) Optional: kurzer Test-Post
    if TEST:
        post_discord("🧪 Testlauf (Monstercount): Script läuft und kann posten.")

    # 1) beQuiet-Liste aus Ranking (Netherworld)
    bequiet = load_bequiet_names_from_ranking()

    # 2) Tagesliste aus Monstercount (Netherworld)
    all_counts = load_monstercount()

    # 3) Join (nur beQuiet, kills > 0)
    joined = [(n, k) for (n, k) in all_counts if n.lower() in bequiet and k > 0]
    joined.sort(key=lambda x: x[1], reverse=True)

    # 4) Debug-Ausgabe (optional ins Discord, sonst nur ins Log)
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
        # auch ohne DEBUG: eine kleine Diagnose dazu, damit du Ursachen siehst
        diagnose = f"(Ranking beQuiet={len(bequiet)}, Monstercount Zeilen={len(all_counts)})"
        post_discord(f"{header}\n{spruch}\n\nHeute leider keine beQuiet-Kills gefunden. {diagnose}")
        return

    lines = []
    for i, (name, kills) in enumerate(joined[:MAX_LINES], start=1):
        if i % 2:
            lines.append(f"{i}. **{name}** hunted **{kills}** mobs")
        else:
            lines.append(f"{i}. **{name}** killed **{kills}** monsters")

    msg = f"{header}\n{spruch}\n\n" + "\n".join(lines)
    post_discord(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Klarer Fehlertext für Actions-Log + optionaler Post
        err = f"⚠️ Monstercount-Tracker Fehler: {e}"
        print(err, file=sys.stderr)
        try:
            post_discord(err)
        except Exception:
            pass
        sys.exit(1)
