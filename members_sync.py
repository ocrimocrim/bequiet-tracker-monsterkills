# members_sync.py
# Pflege der Datei members_bequiet.txt
# Ergänzt beQuiet-Spieler von der Online-Liste und entfernt Ex-Mitglieder anhand des Rankings.

from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Set, Dict, List

import requests
from bs4 import BeautifulSoup

# Konstanten
GUILD_NAME = "beQuiet"
HOMEPAGE_URL = "https://pr-underworld.com/website/"
RANKING_URL = "https://pr-underworld.com/website/ranking/"
REPO_DIR = Path(__file__).resolve().parent
MEMBERS_FILE = REPO_DIR / "members_bequiet.txt"
TIMEOUT = 20

def fetch(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "bequiet-bot/1.0"})
    r.raise_for_status()
    return r.text

def parse_online_bequiet_names(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    result: Set[str] = set()
    # Es gibt eine Tabelle mit <tr>. Zweite Spalte enthält den Namen. Letzte Spalte enthält den Gildennamen.
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(strip=True) if tds and tds[0].name == "td" else None
        # Einige Tabellen haben in der ersten Spalte die Nummer. In deinen Beispielen steht der Name in der zweiten Spalte.
        # Deshalb fallback auf zweite Spalte, falls die erste Spalte wie eine Nummer aussieht.
        if name and name.isdigit() and len(tds) >= 2:
            name = tds[1].get_text(strip=True)
        if not name:
            continue
        guild_cell = tds[-1].get_text(" ", strip=True)
        if GUILD_NAME.lower() in guild_cell.lower():
            result.add(name)
    return result

def parse_ranking_bequiet_names(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    result: Set[str] = set()
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(strip=True) if tds and tds[0].name == "td" else None
        if name and name.isdigit() and len(tds) >= 2:
            name = tds[1].get_text(strip=True)
        if not name:
            continue
        guild_cell = tds[-1].get_text(" ", strip=True)
        if GUILD_NAME.lower() in guild_cell.lower():
            result.add(name)
    return result

def load_members() -> Dict[str, str]:
    mem: Dict[str, str] = {}
    if MEMBERS_FILE.exists():
        for line in MEMBERS_FILE.read_text(encoding="utf-8").splitlines():
            n = line.strip()
            if n:
                mem[n.lower()] = n
    return mem

def save_members(mem: Dict[str, str]) -> None:
    names_sorted = sorted(mem.values(), key=lambda s: s.lower())
    MEMBERS_FILE.write_text("\n".join(names_sorted) + "\n", encoding="utf-8")

def add_missing(current: Dict[str, str], to_add: Iterable[str]) -> List[str]:
    added: List[str] = []
    for n in to_add:
        low = n.lower()
        if low not in current:
            current[low] = n
            added.append(n)
    return added

def remove_ex_members(current: Dict[str, str], still_in_guild: Set[str]) -> List[str]:
    still_low = {n.lower() for n in still_in_guild}
    removed: List[str] = []
    for low, orig in list(current.items()):
        if low not in still_low:
            removed.append(orig)
            current.pop(low)
    return removed

def run_once() -> tuple[list[str], list[str]]:
    # Online-Liste lesen
    home_html = fetch(HOMEPAGE_URL)
    online_bequiet = parse_online_bequiet_names(home_html)

    # Ranking lesen
    rank_html = fetch(RANKING_URL)
    ranked_bequiet = parse_ranking_bequiet_names(rank_html)

    # Mitgliederdatei laden
    mem = load_members()

    # Ergänzen
    added = add_missing(mem, online_bequiet)

    # Entfernen
    removed = remove_ex_members(mem, ranked_bequiet)

    # Speichern, falls geändert
    if added or removed:
        save_members(mem)

    return added, removed

if __name__ == "__main__":
    try:
        added, removed = run_once()
        if added or removed:
            print("Gildenliste aktualisiert")
        if added:
            print("Hinzugefügt " + ", ".join(sorted(added)))
        if removed:
            print("Entfernt " + ", ".join(sorted(removed)))
    except Exception as e:
        print(f"Fehler bei members_sync.py {e}", file=sys.stderr)
        sys.exit(1)
