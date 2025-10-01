import os
import re
import sys
import random
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# --------------------------------- Einstellungen ---------------------------------
RANKING_URL = "https://pr-underworld.com/website/ranking/"
MONSTER_URL = "https://pr-underworld.com/website/monstercount/"
HOMEPAGE_URL = "https://pr-underworld.com/website/"
GUILD_NAME = "beQuiet"

TIMEOUT = 25
HEADERS = {"User-Agent": "beQuiet Monstercount Tracker (+GitHub Actions)"}

WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
DEBUG = os.getenv("DEBUG_MONSTERCOUNT", "false").lower() == "true"
TEST = os.getenv("TEST_MONSTERCOUNT", "false").lower() == "true"

MAX_LINES = 40

SPRUCH_FILES = ["texts_monsterkills.txt", "Texts for Monsterkills.txt"]
MEMBERS_FILE = Path("members_bequiet.txt")

BERLIN = ZoneInfo("Europe/Berlin")

# Feste Stunden für Homepage-Scan
SCAN_HOURS = {10, 18, 21}

STATE_FILE = Path("state_monstercount.json")


# --------------------------------- Utilities ---------------------------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_daily_date": "",
        "last_weekly_year_week": "",
        "last_monthly_year_month": "",
        "weekly": {"year_week": "", "kills": {}},
        "monthly": {"year_month": "", "kills": {}},
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def now_berlin():
    return datetime.now(timezone.utc).astimezone(BERLIN)


def post_discord(content: str):
    if not WEBHOOK:
        print("WARN: DISCORD_WEBHOOK_URL fehlt – Ausgabe nur im Log\n" + content)
        return
    r = requests.post(WEBHOOK, json={"content": content}, timeout=15)
    r.raise_for_status()


def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def find_netherworld_table(soup: BeautifulSoup):
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


# --------------------------------- Mitgliederliste ---------------------------------
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


# --------------------------------- Parser ---------------------------------
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
        name = tds[2 if tds[0].find("img") else 1].get_text(strip=True) if len(tds) >= 3 else tds[1].get_text(strip=True)
        guild = tds[-1].get_text(" ", strip=True)
        if name and GUILD_NAME.lower() in (guild or "").lower():
            bequiet.add(name.strip())
    return bequiet


def load_bequiet_names_from_homepage() -> set[str]:
    soup = get_soup(HOMEPAGE_URL)
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


# --------------------------------- Zeitfenster nur eine Minute je Tag ---------------------------------
def daily_target_minute(dt: datetime) -> int:
    base = f"{dt.date().isoformat()}-{GUILD_NAME}"
    h = hashlib.sha256(base.encode("utf-8")).hexdigest()
    # ergibt Werte 30 bis 59 inklusive
    return 30 + (int(h, 16) % 30)


def in_daily_slot(dt: datetime) -> bool:
    return dt.hour == 23 and dt.minute == daily_target_minute(dt)


def in_weekly_slot(dt: datetime) -> bool:
    return dt.isoweekday() == 7 and dt.hour == 23 and dt.minute == daily_target_minute(dt)


def in_monthly_slot(dt: datetime) -> bool:
    return end_of_month(dt) and dt.hour == 23 and dt.minute == daily_target_minute(dt)


# --------------------------------- Aggregation ---------------------------------
def iso_year_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def year_month(dt: datetime) -> str:
    return f"{dt.year}-{dt.month:02d}"


def end_of_month(dt: datetime) -> bool:
    tomorrow = dt.date() + timedelta(days=1)
    return tomorrow.day == 1


def aggregate_into(state: dict, joined: list[tuple[str, int]], dt: datetime):
    iw = iso_year_week(dt)
    if state["weekly"].get("year_week") != iw:
        state["weekly"] = {"year_week": iw, "kills": {}}
    for name, kills in joined:
        state["weekly"]["kills"][name] = state["weekly"]["kills"].get(name, 0) + kills

    ym = year_month(dt)
    if state["monthly"].get("year_month") != ym:
        state["monthly"] = {"year_month": ym, "kills": {}}
    for name, kills in joined:
        state["monthly"]["kills"][name] = state["monthly"]["kills"].get(name, 0) + kills


def format_ranking(title: str, entries: list[tuple[str, int]], spruch: str) -> str:
    header = f"**Netherworld {title} ({GUILD_NAME})**"
    if not entries:
        return f"{header}\n{spruch}\n\nKeine Kills gefunden"
    lines = []
    for i, (name, kills) in enumerate(entries[:MAX_LINES], start=1):
        if i % 2:
            lines.append(f"{i}. **{name}** hunted **{kills}** mobs")
        else:
            lines.append(f"{i}. **{name}** killed **{kills}** monsters")
    return f"{header}\n{spruch}\n\n" + "\n".join(lines)


# --------------------------------- Main Steps ---------------------------------
def run_homepage_scan(berlin_now: datetime):
    if berlin_now.hour not in SCAN_HOURS:
        return
    try:
        found = load_bequiet_names_from_homepage()
        added = add_members(found)
        if added:
            post_discord("🧭 Neue beQuiet-Namen von der Homepage aufgenommen\n" + ", ".join(sorted(added)))
        else:
            print("Homepage-Scan ohne neue Namen")
    except Exception as e:
        print(f"Homepage-Scan Fehler: {e}", file=sys.stderr)


def run_daily(state: dict, berlin_now: datetime):
    today = berlin_now.date().isoformat()

    if not TEST:
        if not in_daily_slot(berlin_now):
            return
        if state.get("last_daily_date") == today:
            return

    bequiet_ranking = {n.lower() for n in load_bequiet_names_from_ranking()}
    members_map = load_members()
    bequiet_all = set(members_map.keys()) | bequiet_ranking

    all_counts = load_monstercount()
    joined = [(n, k) for (n, k) in all_counts if n.lower() in bequiet_all and k > 0]
    joined.sort(key=lambda x: x[1], reverse=True)

    if DEBUG:
        sample_mc = ", ".join(f"{n}:{k}" for n, k in all_counts[:10]) or "-"
        debug_msg = (
            "🧪 DEBUG Monstercount\n"
            f"- beQuiet Ranking erkannt {len(bequiet_ranking)}\n"
            f"- Mitgliederdatei {len(members_map)}\n"
            f"- Erster Block Monstercount {sample_mc}\n"
            f"- Join Ergebnis {len(joined)} Spieler\n"
            f"- Zielminute {daily_target_minute(berlin_now)}"
        )
        try:
            post_discord(debug_msg)
        except Exception as e:
            print(f"DEBUG-Post fehlgeschlagen {e}", file=sys.stderr)

    spruch = pick_spruch()
    msg = format_ranking("Daily Monstercount", joined, spruch)
    post_discord(msg)

    aggregate_into(state, joined, berlin_now)

    state["last_daily_date"] = today
    save_state(state)


def run_weekly(state: dict, berlin_now: datetime):
    if not TEST and not in_weekly_slot(berlin_now):
        return

    current_week = iso_year_week(berlin_now)
    if not TEST and state.get("last_weekly_year_week") == current_week:
        return

    wk = state.get("weekly", {})
    kills = wk.get("kills", {})
    ranking = sorted(kills.items(), key=lambda x: x[1], reverse=True)
    spruch = pick_spruch()
    msg = format_ranking("Weekly Monstercount", ranking, spruch)
    post_discord(msg)

    # Reset und Einmaligkeitsflag
    state["weekly"] = {"year_week": iso_year_week(berlin_now + timedelta(days=1)), "kills": {}}
    state["last_weekly_year_week"] = current_week
    save_state(state)


def run_monthly(state: dict, berlin_now: datetime):
    if not TEST and not in_monthly_slot(berlin_now):
        return

    ym_now = year_month(berlin_now)
    if not TEST and state.get("last_monthly_year_month") == ym_now:
        return

    mm = state.get("monthly", {})
    kills = mm.get("kills", {})
    ranking = sorted(kills.items(), key=lambda x: x[1], reverse=True)
    spruch = pick_spruch()
    msg = format_ranking("Monthly Monstercount", ranking, spruch)
    post_discord(msg)

    # Reset und Einmaligkeitsflag
    next_month = (berlin_now.replace(day=1) + timedelta(days=32)).replace(day=1)
    state["monthly"] = {"year_month": year_month(next_month), "kills": {}}
    state["last_monthly_year_month"] = ym_now
    save_state(state)


def main():
    state = load_state()
    berlin_now = now_berlin()

    run_homepage_scan(berlin_now)
    run_daily(state, berlin_now)
    run_weekly(state, berlin_now)
    run_monthly(state, berlin_now)


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
        sys.exit(1)
