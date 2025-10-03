import os
import sys
import re
import json
import time
import random
import logging
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

# -------------------- Einstellungen --------------------

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

# Feste Quellen
RANKING_URL  = "https://pr-underworld.com/website/ranking/"
MONSTER_URL  = "https://pr-underworld.com/website/monstercount/"
HOMEPAGE_URL = "https://pr-underworld.com/website/"
GUILD_NAME   = "beQuiet"

# Zeitfenster (Berlin)
BERLIN = ZoneInfo("Europe/Berlin")
DAILY_START_MIN = 50    # 23:50
DAILY_END_MIN   = 59    # 23:59 inkl.

WEEKLY_START_MIN = 30   # 23:30
WEEKLY_END_MIN   = 50   # 23:50 inkl.

MONTHLY_START_MIN = 20  # 23:20
MONTHLY_END_MIN   = 59  # 23:59 inkl.

# Dateien
REPO_DIR = Path(__file__).resolve().parent
STATE_DIR = REPO_DIR / "data"
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "state_monstercount.json"  # fester Pfad im Repo

MEMBERS_FILE = Path("members_bequiet.txt")
SPRUCH_FILES = ["texts_monsterkills.txt", "Texts for Monsterkills.txt"]

# Discord
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
MAX_LINES = 40
DISCORD_SAFE_LIMIT = 1900  # Sicherheitskürzung unter 2000

# -------------------- Hilfen --------------------

def berlin_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(BERLIN)

def is_in_window(dt: datetime, start_m: int, end_m: int) -> bool:
    return dt.hour == 23 and start_m <= dt.minute <= end_m

def end_of_month(dt: datetime) -> bool:
    return (dt + timedelta(days=1)).day == 1

def only_digits(text: str) -> int:
    nums = re.findall(r"\d+", text or "")
    return int("".join(nums)) if nums else 0

# -------------------- State --------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_daily_date": "",
        "weekly":  {"year_week": "",  "kills": {}},
        "monthly": {"year_month": "", "kills": {}},
        "yearly":  {"year": "", "kills": {}},
    }

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def save_daily_snapshot(state: dict, now_local):
    snap = STATE_DIR / f"daily_{now_local.date().isoformat()}.json"
    try:
        snap.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Snapshot-Fehler {e}", file=sys.stderr)

# -------------------- Discord --------------------

def post_discord(content: str):
    if not DISCORD_WEBHOOK:
        print("WARN: DISCORD_WEBHOOK fehlt – Ausgabe nur im Log\n" + content)
        return
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=20)
    r.raise_for_status()

# -------------------- HTML Utilities --------------------

def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def find_netherworld_table(soup: BeautifulSoup):
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if h.get_text(" ", strip=True).lower().startswith("netherworld"):
            return h.find_next("table")
    return None

# -------------------- Mitglieder --------------------

def load_members() -> dict:
    out = {}
    if MEMBERS_FILE.exists():
        for ln in MEMBERS_FILE.read_text(encoding="utf-8").splitlines():
            name = ln.strip()
            if not name:
                continue
            low = name.lower()
            if low not in out:
                out[low] = name
    return out

def save_members(members: dict):
    canonical_sorted = sorted(set(members.values()), key=lambda s: s.lower())
    MEMBERS_FILE.write_text("\n".join(canonical_sorted) + "\n", encoding="utf-8")

def add_members(new_names: set[str]):
    mem = load_members()
    added = []
    for n in new_names:
        low = n.lower()
        if low not in mem:
            mem[low] = n
            added.append(n)
    if added:
        save_members(mem)
    return added

# -------------------- Parser: Ranking / Homepage --------------------

def load_bequiet_names_from_ranking() -> set[str]:
    soup = get_soup(RANKING_URL)
    table = find_netherworld_table(soup)
    if not table:
        raise RuntimeError("Ranking Netherworld-Tabelle nicht gefunden")

    bequiet = set()
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        name_idx = 2 if (tds[0].find("img") is not None and len(tds) >= 3) else 1
        name = tds[name_idx].get_text(strip=True)
        guild = tds[-1].get_text(" ", strip=True)
        if name and GUILD_NAME.lower() in (guild or "").lower():
            bequiet.add(name.lower())
    return bequiet

def load_bequiet_names_from_homepage() -> set[str]:
    try:
        soup = get_soup(HOMEPAGE_URL)
    except Exception:
        return set()
    table = find_netherworld_table(soup)
    if not table:
        return set()
    out = set()
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        name = tds[0].get_text(strip=True)
        guild = tds[3].get_text(" ", strip=True)
        if name and GUILD_NAME.lower() in (guild or "").lower():
            out.add(name.strip())
    return out

# -------------------- Parser: Monstercount --------------------

def load_monstercount() -> list[tuple[str, int]]:
    soup = get_soup(MONSTER_URL)
    table = find_netherworld_table(soup)
    if not table:
        raise RuntimeError("Monstercount Netherworld-Tabelle nicht gefunden")

    out = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name = tds[0].get_text(strip=True)
        kills = only_digits(tds[1].get_text(strip=True))
        if name:
            out.append((name, kills))
    return out

# -------------------- Aggregation / Format --------------------

def iso_year_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"

def year_month(dt: datetime) -> str:
    return f"{dt.year}-{dt.month:02d}"

def aggregate_into(state: dict, joined: list[tuple[str, int]], dt: datetime):
    # weekly
    iw = iso_year_week(dt)
    if state["weekly"].get("year_week") != iw:
        state["weekly"] = {"year_week": iw, "kills": {}}
    for name, kills in joined:
        state["weekly"]["kills"][name] = state["weekly"]["kills"].get(name, 0) + kills

    # monthly
    ym = year_month(dt)
    if state["monthly"].get("year_month") != ym:
        state["monthly"] = {"year_month": ym, "kills": {}}
    for name, kills in joined:
        state["monthly"]["kills"][name] = state["monthly"]["kills"].get(name, 0) + kills

    # yearly
    y = str(dt.year)
    if state["yearly"].get("year") != y:
        state["yearly"] = {"year": y, "kills": {}}
    for name, kills in joined:
        state["yearly"]["kills"][name] = state["yearly"]["kills"].get(name, 0) + kills

def pick_spruch() -> str:
    for p in SPRUCH_FILES:
        if Path(p).exists():
            lines = [ln.strip() for ln in Path(p).read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                return random.choice(lines)
    return "The mobs fell, the loot rolled, and morale stayed high."

def format_ranking(title: str, entries: list[tuple[str, int]], spruch: str) -> str:
    header = f"**Netherworld {title} ({GUILD_NAME})**"
    if not entries:
        msg = f"{header}\n{spruch}\n\nKeine Kills gefunden"
    else:
        lines = []
        for i, (name, kills) in enumerate(entries[:MAX_LINES], start=1):
            verb = "hunted" if i % 2 else "killed"
            lines.append(f"{i}. **{name}** {verb} **{kills}** mobs")
        msg = f"{header}\n{spruch}\n\n" + "\n".join(lines)

    if len(msg) <= DISCORD_SAFE_LIMIT:
        return msg
    body_lines = msg.split("\n")[2:] if "\n\n" in msg else msg.split("\n")
    low, high = 0, len(body_lines)
    best = "Keine Kills gefunden"
    while low <= high:
        mid = (low + high) // 2
        candidate_body = "\n".join(body_lines[:mid]) if mid > 0 else "Keine Kills gefunden"
        cand = f"{header}\n{spruch}\n\n{candidate_body}"
        if len(cand) <= DISCORD_SAFE_LIMIT:
            best = candidate_body
            low = mid + 1
        else:
            high = mid - 1
    return f"{header}\n{spruch}\n\n{best}"

# -------------------- Runs --------------------

def run_homepage_scan(now_local: datetime):
    if now_local.hour not in {10, 18, 21}:
        return
    try:
        found = load_bequiet_names_from_homepage()
        added = add_members(found)
        if added:
            post_discord("🧭 Neue beQuiet-Namen von der Homepage aufgenommen\n" + ", ".join(sorted(added)))
    except Exception as e:
        print(f"Homepage-Scan Fehler: {e}", file=sys.stderr)

def run_daily(state: dict, now_local: datetime):
    today = now_local.date().isoformat()

    if not is_in_window(now_local, DAILY_START_MIN, DAILY_END_MIN):
        print(f"Außerhalb Daily-Fenster {now_local:%H:%M}", file=sys.stderr)
        return
    if state.get("last_daily_date") == today:
        print(f"Heute schon gepostet {today}", file=sys.stderr)
        return

    bequiet_ranking = {n.lower() for n in load_bequiet_names_from_ranking()}
    members_map = load_members()
    bequiet_all = set(members_map.keys()) | bequiet_ranking

    all_counts = load_monstercount()

    joined = [(n, k) for (n, k) in all_counts if n.lower() in bequiet_all and k > 0]
    joined.sort(key=lambda x: x[1], reverse=True)

    spruch = pick_spruch()
    msg = format_ranking("Daily Monstercount", joined, spruch)
    post_discord(msg)

    aggregate_into(state, joined, now_local)

    state["last_daily_date"] = today
    save_state(state)
    save_daily_snapshot(state, now_local)

def run_weekly(state: dict, now_local: datetime):
    if now_local.isoweekday() != 7:
        return
    if not is_in_window(now_local, WEEKLY_START_MIN, WEEKLY_END_MIN):
        return

    wk = state.get("weekly", {})
    kills_map = wk.get("kills", {})
    ranking = sorted(kills_map.items(), key=lambda x: x[1], reverse=True)
    spruch = pick_spruch()
    post_discord(format_ranking(f"Weekly Monstercount {wk.get('year_week','')}", ranking, spruch))

    next_week = (now_local + timedelta(days=1))
    state["weekly"] = {"year_week": iso_year_week(next_week), "kills": {}}
    save_state(state)

def run_monthly(state: dict, now_local: datetime):
    if not end_of_month(now_local):
        return
    if not is_in_window(now_local, MONTHLY_START_MIN, MONTHLY_END_MIN):
        return

    mm = state.get("monthly", {})
    kills_map = mm.get("kills", {})
    ranking = sorted(kills_map.items(), key=lambda x: x[1], reverse=True)
    spruch = pick_spruch()
    post_discord(format_ranking(f"Monthly Monstercount {mm.get('year_month','')}", ranking, spruch))

    first_next_month = (now_local.replace(day=1) + timedelta(days=32)).replace(day=1)
    state["monthly"] = {"year_month": year_month(first_next_month), "kills": {}}
    save_state(state)

def run_yearly(state: dict, now_local: datetime):
    if not (now_local.month == 12 and now_local.day == 31):
        return
    if not is_in_window(now_local, 0, 59):
        return

    yr = state.get("yearly", {})
    kills_map = yr.get("kills", {})
    ranking = sorted(kills_map.items(), key=lambda x: x[1], reverse=True)
    spruch = pick_spruch()
    post_discord(format_ranking(f"Yearly Monstercount {yr.get('year','')}", ranking, spruch))

    next_year = now_local.year + 1
    state["yearly"] = {"year": str(next_year), "kills": {}}
    save_state(state)

# -------------------- Main --------------------

def main():
    state = load_state()
    now_local = berlin_now()
    print(f"State-Datei Pfad {STATE_FILE.resolve()}", file=sys.stderr)

    run_homepage_scan(now_local)
    run_daily(state, now_local)
    run_weekly(state, now_local)
    run_monthly(state, now_local)
    run_yearly(state, now_local)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = f"⚠️ Monstercount-Tracker Fehler {e}"
        print(err, file=sys.stderr)
        try:
            post_discord(err)
        except Exception:
            pass
