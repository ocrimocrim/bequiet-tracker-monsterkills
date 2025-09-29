import os
import random
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# URLs
RANKING_URL = "https://pr-underworld.com/website/ranking/"
MONSTER_URL = "https://pr-underworld.com/website/monstercount/"

# Einstellungen
GUILD_NAME = "beQuiet"
TIMEOUT = 20
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
TEXT_FILE = Path("texts_monsterkills.txt")  # dein Sprüche-File
MAX_LINES = 25  # wie viele Zeilen ins Posting

# ---- Hilfsfunktionen ---------------------------------------------------------

def _norm_name(s: str) -> str:
    # Name-Vergleich robust: Kleinbuchstaben, mehrere Spaces zu einem
    return re.sub(r"\s+", " ", s.strip().lower())

def _next_netherworld_table(soup: BeautifulSoup):
    """Finde die Tabelle direkt unter der Überschrift 'Netherworld:'."""
    for h in soup.find_all(["h1","h2","h3","h4","h5","h6"]):
        if h.get_text(strip=True).lower().startswith("netherworld"):
            return h.find_next("table")
    return None

def _safe_int(txt: str, default=0) -> int:
    m = re.findall(r"\d+", str(txt))
    return int("".join(m)) if m else default

def _load_random_line(path: Path) -> str:
    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return random.choice(lines) if lines else "Hunt well, hunt often."
    except Exception:
        return "Hunt well, hunt often."

def _post_discord(content: str):
    if not WEBHOOK:
        print("Fehlende ENV DISCORD_WEBHOOK_URL – kein Discord-Post.", file=sys.stderr)
        return
    r = requests.post(WEBHOOK, json={"content": content}, timeout=10)
    r.raise_for_status()

# ---- Scraper -----------------------------------------------------------------

def get_bequiet_names_from_ranking() -> set[str]:
    """Liest die Netherworld-Rangliste und liefert alle Char-Namen der Gilde beQuiet."""
    r = requests.get(RANKING_URL, timeout=TIMEOUT, headers={"User-Agent": "beQuiet monster tracker"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    table = _next_netherworld_table(soup)
    if not table:
        print("Netherworld-Tabelle (Ranking) nicht gefunden.", file=sys.stderr)
        return set()

    names = set()
    tbody = table.find("tbody")
    if not tbody:
        return names

    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        # Erwartetes Layout: 0=online icon, 1=Name, 2=Level, 3=Job img, 4=Exp%, 5=Guild (img + text)
        # Manche Seiten haben 7 Spalten inkl. # => daher defensiv:
        if len(tds) < 6:
            continue

        # Name steckt in Spalte mit Text; im Beispiel ist es tds[2] bei Level-Ranking-Seite.
        # Aus Deinen HTML-Snippets: th#, td(online), td(name), td(level), td(job), td(exp), td(guild)
        # => Name=tds[2], Guild=tds[6] (weil th zählt nicht in tds). Aber nicht überall gleich,
        # deshalb versuchen wir, Name über die Spalte zu finden, die *kein* Bild enthält.
        # Hier nutzen wir die Struktur aus deinen Beispielen:
        try:
            name = tds[2].get_text(strip=True)
        except Exception:
            continue

        # Gildenspalte ist die letzte Textspalte
        guild_text = tds[-1].get_text(" ", strip=True)

        if GUILD_NAME.lower() in guild_text.lower():
            names.add(_norm_name(name))

    return names

def get_netherworld_monstercount() -> list[tuple[str, int]]:
    """Gibt Liste (name, kills) aus der Netherworld-Tabelle der Monstercount-Seite zurück."""
    r = requests.get(MONSTER_URL, timeout=TIMEOUT, headers={"User-Agent": "beQuiet monster tracker"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    table = _next_netherworld_table(soup)
    if not table:
        print("Netherworld-Tabelle (Monstercount) nicht gefunden.", file=sys.stderr)
        return []

    res = []
    tbody = table.find("tbody")
    if not tbody:
        return res

    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(strip=True) if len(tds) == 2 else tds[1].get_text(strip=True)
        kills_txt = tds[-1].get_text(strip=True)
        kills = _safe_int(kills_txt, 0)
        if name:
            res.append((name, kills))

    return res

# ---- Hauptlogik --------------------------------------------------------------

def main():
    bequiet_set = get_bequiet_names_from_ranking()
    if not bequiet_set:
        _post_discord("🧪 Testlauf (Monstercount): Keine beQuiet-Namen im Ranking gefunden.")
        return

    today = get_netherworld_monstercount()
    # Filter auf beQuiet
    filtered = [(n, k) for (n, k) in today if _norm_name(n) in bequiet_set and k > 0]
    filtered.sort(key=lambda x: x[1], reverse=True)

    header = "**Netherworld – Daily Monstercount (beQuiet)**"
    flavor = _load_random_line(TEXT_FILE)

    if not filtered:
        _post_discord(f"{header}\n{flavor}\n\nHeute leider keine beQuiet-Kills gefunden.")
        return

    lines = []
    for idx, (name, kills) in enumerate(filtered, start=1):
        lines.append(f"{idx}. **{name}** — {kills:,} kills".replace(",", "."))

    body = "\n".join(lines[:MAX_LINES])
    msg = f"{header}\n{flavor}\n\n{body}"

    _post_discord(msg)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        if WEBHOOK:
            try:
                _post_discord(f"⚠️ Monstercount-Tracker Fehler: `{e}`")
            except Exception:
                pass
        sys.exit(1)
