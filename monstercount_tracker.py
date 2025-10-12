import argparse
import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import urllib.request

TZ = ZoneInfo("Europe/Berlin")
DATA_DIR = "data"
STATE_DIR = os.path.join(DATA_DIR, "state")
LOG_DIR = os.path.join(DATA_DIR, "logs")
LOCK_DIR = os.path.join(DATA_DIR, "locks")

GUILD_MEMBERS_FILE = "members_bequiet.txt"
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
NO_POST = os.environ.get("NO_POST", "") == "1"

# ----------------- Helpers -----------------

def ensure_dirs():
    for p in (STATE_DIR, LOG_DIR, LOCK_DIR):
        os.makedirs(p, exist_ok=True)

def now_berlin():
    return datetime.now(TZ)

def iso_week_key(dt):
    iso = dt.isocalendar()
    return f"Y{iso.year}-W{iso.week:02d}"

def month_key(dt):
    return f"Y{dt.year}-M{dt.month:02d}"

def year_key(dt):
    return f"Y{dt.year}"

def last_day_of_month(dt):
    first_next = (dt.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_next - timedelta(days=1)

def is_last_day_of_month(dt):
    return dt.day == last_day_of_month(dt).day

def is_last_day_of_year(dt):
    return dt.month == 12 and dt.day == 31

def load_members(path=GUILD_MEMBERS_FILE):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {l.strip() for l in f if l.strip()}

def slack_post(text):
    if NO_POST:
        print("NO_POST aktiv – kein Slack-Post. Inhalt wäre:\n", text[:300], "...")
        return
    if not SLACK_WEBHOOK:
        print("Slack Webhook fehlt. Ausgabe in Konsole.")
        print(text)
        return
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(SLACK_WEBHOOK, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        r.read()

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def sort_desc(mapping):
    return OrderedDict(sorted(mapping.items(), key=lambda kv: kv[1], reverse=True))

def try_lock(period, key):
    os.makedirs(LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(LOCK_DIR, f"{period}_{key}.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(now_berlin().isoformat())
        return True
    except FileExistsError:
        return False

def state_path(period):
    return os.path.join(STATE_DIR, f"{period}.json")

def reset_if_new(period, current_key):
    state = load_json(state_path(period), {})
    meta = state.get("_meta", {})
    if meta.get("key") != current_key:
        state = {"_meta": {"key": current_key}, "scores": {}}
        save_json(state_path(period), state)
    return state

def add_scores(state, data):
    scores = state.setdefault("scores", {})
    for n, v in data.items():
        scores[n] = scores.get(n, 0) + int(v)

def write_daily_logs(dt, all_players, guild_only):
    y = f"{dt.year:04d}"
    m = f"{dt.month:02d}"
    d = f"{dt.day:02d}"
    base = os.path.join(LOG_DIR, y, m)
    save_json(os.path.join(base, f"{d}_all.json"), sort_desc(all_players))
    save_json(os.path.join(base, f"{d}_guild.json"), sort_desc(guild_only))

def header_daily(dt, guild):
    return f"Netherworld Daily Monstercount ({guild})\nToday you crushed monsters like butterflies under steel boots. But hey, butterflies respawn too."

def header_weekly(dt, guild):
    iso = dt.isocalendar()
    return f"🗓️ Netherworld Weekly Monstercount {iso.year}-W{iso.week:02d} ({guild})\nHector thought he was a boss. You proved he was just a tutorial with extra HP."

def header_monthly(dt, guild):
    return f"📅 Netherworld Monthly Monstercount {dt.year}-{dt.month:02d} ({guild})\nYour blades hummed for an entire moon. The mobs learned to fear bedtime."

def header_yearly(dt, guild):
    return f"🏆 Netherworld Yearly Monstercount {dt.year} ({guild})\nRecords fell. Corpses piled. Legends grew."

def body(scores):
    lines = []
    for i, (name, kills) in enumerate(sort_desc(scores).items(), start=1):
        lines.append(f"{i}. {name} hunted {kills} mobs")
    return "\n".join(lines)

# ----------------- Läufe -----------------

def run_daily():
    ensure_dirs()
    dt = now_berlin()

    day_key = dt.strftime("%Y-%m-%d")
    if not try_lock("daily", day_key):
        print("Daily bereits gepostet. Beende.")
        return

    members = load_members()

    # Direkt von der Website laden – deine bestehende Funktion MUSS vorhanden sein
    try:
        all_players = load_monstercount()  # liefert idealerweise {name: kills}
    except NameError as e:
        raise RuntimeError("load_monstercount fehlt. Bitte deine bestehende Web-Scrape-Funktion hier definieren/einfügen.") from e

    # Notfalls Liste -> Dict normalisieren
    if isinstance(all_players, list):
        tmp = {}
        for row in all_players:
            if isinstance(row, dict):
                name = row.get("name") or row.get("player")
                kills = int(row.get("kills", 0))
            else:
                if len(row) >= 3:
                    name = str(row[1]).strip()
                    kills = int(row[2])
                else:
                    continue
            if name:
                tmp[name] = kills
        all_players = tmp

    guild_only = {n: v for n, v in all_players.items() if n in members}

    write_daily_logs(dt, all_players, guild_only)

    ws = reset_if_new("weekly", iso_week_key(dt))
    ms = reset_if_new("monthly", month_key(dt))
    ys = reset_if_new("yearly", year_key(dt))
    add_scores(ws, guild_only)
    add_scores(ms, guild_only)
    add_scores(ys, guild_only)
    save_json(state_path("weekly"), ws)
    save_json(state_path("monthly"), ms)
    save_json(state_path("yearly"), ys)

    text = header_daily(dt, "beQuiet") + "\n\n" + body(guild_only)
    slack_post(text)

def run_weekly():
    ensure_dirs()
    dt = now_berlin()
    if dt.weekday() != 6:
        print("Kein Sonntag in Berlin. Beende.")
        return

    wkey = iso_week_key(dt)
    if not try_lock("weekly", wkey):
        print("Weekly bereits gepostet. Beende.")
        return

    ws = reset_if_new("weekly", wkey)
    scores = ws.get("scores", {})
    if not scores:
        print("Weekly ohne Daten. Beende.")
        return

    text = header_weekly(dt, "beQuiet") + "\n\n" + body(scores)
    slack_post(text)
    save_json(state_path("weekly"), {"_meta": {"key": wkey}, "scores": {}})

def run_monthly():
    ensure_dirs()
    dt = now_berlin()
    if not is_last_day_of_month(dt):
        print("Nicht der letzte Tag des Monats in Berlin. Beende.")
        return

    mkey = month_key(dt)
    if not try_lock("monthly", mkey):
        print("Monthly bereits gepostet. Beende.")
        return

    ms = reset_if_new("monthly", mkey)
    scores = ms.get("scores", {})
    if not scores:
        print("Monthly ohne Daten. Beende.")
        return

    text = header_monthly(dt, "beQuiet") + "\n\n" + body(scores)
    slack_post(text)
    save_json(state_path("monthly"), {"_meta": {"key": mkey}, "scores": {}})

def run_yearly():
    ensure_dirs()
    dt = now_berlin()
    if not is_last_day_of_year(dt):
        print("Nicht der letzte Tag des Jahres in Berlin. Beende.")
        return

    ykey = year_key(dt)
    if not try_lock("yearly", ykey):
        print("Yearly bereits gepostet. Beende.")
        return

    ys = reset_if_new("yearly", ykey)
    scores = ys.get("scores", {})
    if not scores:
        print("Yearly ohne Daten. Beende.")
        return

    text = header_yearly(dt, "beQuiet") + "\n\n" + body(scores)
    slack_post(text)
    save_json(state_path("yearly"), {"_meta": {"key": ykey}, "scores": {}})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly", "yearly"], required=True)
    args = parser.parse_args()
    if args.mode == "daily":
        run_daily()
    elif args.mode == "weekly":
        run_weekly()
    elif args.mode == "monthly":
        run_monthly()
    elif args.mode == "yearly":
        run_yearly()

if __name__ == "__main__":
    main()
