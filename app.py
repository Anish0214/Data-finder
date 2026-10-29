# ==========================================================
# DATA FINDER: NBA + NFL MATCHUP LOOKUP
# Pick a sport, a player, and an opponent to see:
#   1. Every past game the player had against that team
#   2. His averages vs that team compared to his overall averages
#   3. Recent form (last few games)
#   4. A "line checker" hit rate (how often he went over a number)
#   5. Injury report for both teams
#
# Data comes from ESPN's free (unofficial) public data feeds.
# Streamlit turns this Python file into a website automatically.
# ==========================================================

import datetime as dt
import html
import io
import unicodedata
import math
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import requests
import streamlit as st

st.set_page_config(page_title="Data Finder", page_icon="📊", layout="wide")

HEADERS = {"User-Agent": "Mozilla/5.0 (matchup lookup hobby app)"}


# ---------- SEASON HELPERS ----------
def nba_season():
    """ESPN names NBA seasons by the year they END (2026/27 = 2027). Season starts in October."""
    today = dt.date.today()
    return today.year + 1 if today.month >= 10 else today.year


def nfl_season():
    """ESPN names NFL seasons by the year they START (2026 season = 2026). Season starts in September."""
    today = dt.date.today()
    return today.year if today.month >= 8 else today.year - 1


# ---------- SPORT SETTINGS (change things here) ----------
SPORTS = {
    "🏀 NBA": {
        "key": "nba",
        "site": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba",
        "web": "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba",
        "season_fn": nba_season,
        "season_label": lambda s: f"{s - 1}/{str(s)[-2:]}",
        "seasons_back": 4,        # how many seasons of history to pull
        "recent_games": 10,       # size of the "recent form" table
        "use_names": False,       # NBA column labels are already unique
        "today_word": "Next game",
        "default_stats": ["MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "PRA"],
        "stats_by_pos": {},
        "default_line": 20.5,
    },
    "🏈 NFL": {
        "key": "nfl",
        "site": "https://site.api.espn.com/apis/site/v2/sports/football/nfl",
        "web": "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl",
        "season_fn": nfl_season,
        "season_label": lambda s: str(s),
        "seasons_back": 6,        # NFL teams meet less often, so look further back
        "recent_games": 5,
        "use_names": True,        # NFL labels repeat (YDS appears 3x), so use ESPN's long names
        "today_word": "Next game",
        "default_stats": [],      # empty = show whatever stats the player actually has
        "stats_by_pos": {
            "QB": ["PASS YDS", "PASS TD", "INT", "CMP", "ATT", "RUSH YDS", "PASS+RUSH YDS"],
            "RB": ["CAR", "RUSH YDS", "RUSH TD", "REC", "TGT", "REC YDS", "RUSH+REC YDS"],
            "FB": ["CAR", "RUSH YDS", "RUSH TD", "REC", "REC YDS", "RUSH+REC YDS"],
            "WR": ["REC", "TGT", "REC YDS", "REC TD", "LNG REC", "RUSH+REC YDS"],
            "TE": ["REC", "TGT", "REC YDS", "REC TD", "LNG REC"],
        },
        "default_line": 49.5,
    },
}

# ESPN's long stat names -> short column names shown in the app (NFL)
NAME_MAP = {
    "completions": "CMP", "passingAttempts": "ATT", "passingYards": "PASS YDS",
    "completionPct": "CMP%", "yardsPerPassAttempt": "YPA", "passingTouchdowns": "PASS TD",
    "interceptions": "INT", "longPassing": "LNG PASS", "sacks": "SACK",
    "QBRating": "RTG", "adjQBR": "QBR",
    "rushingAttempts": "CAR", "rushingYards": "RUSH YDS", "yardsPerRushAttempt": "YPC",
    "rushingTouchdowns": "RUSH TD", "longRushing": "LNG RUSH",
    "receptions": "REC", "receivingTargets": "TGT", "receivingYards": "REC YDS",
    "yardsPerReception": "YPR", "receivingTouchdowns": "REC TD", "longReception": "LNG REC",
    "fumbles": "FUM", "fumblesLost": "FUM LOST",
    "totalTackles": "TKL", "soloTackles": "SOLO", "passesDefended": "PD",
}

# For these stats, a LOWER number is good (so going below average shows green)
LOWER_IS_BETTER = {"TO", "INT", "FUM", "FUM LOST", "SACK"}

META_COLS = ["Date", "Season", "Type", "Opp", "H/A", "Result"]


# ---------- LOOK AND FEEL ----------
THEMES = {
    "nba": {
        "primary": "#1D428A", "accent": "#C8102E", "bg": "#F4F6FA", "edition": "NBA EDITION",
        # hardwood planks + a white court arc
        "banner": "radial-gradient(circle at 88% 50%, transparent 70px, rgba(255,255,255,.85) 71px 74px, transparent 75px),"
                  "linear-gradient(90deg, rgba(29,66,138,.55), rgba(29,66,138,0) 55%),"
                  "repeating-linear-gradient(90deg, #C98D5B 0 58px, #B4773F 58px 60px, #D59D69 60px 130px, #BC7F4C 130px 132px)",
    },
    "nfl": {
        "primary": "#013369", "accent": "#D50A0A", "bg": "#F2F5F3", "edition": "NFL EDITION",
        # turf stripes + white yard lines
        "banner": "linear-gradient(90deg, rgba(1,51,105,.6), rgba(1,51,105,0) 55%),"
                  "repeating-linear-gradient(90deg, transparent 0 66px, rgba(255,255,255,.8) 66px 69px, transparent 69px 135px),"
                  "repeating-linear-gradient(90deg, #2F7D34 0 67px, #286E2D 67px 135px)",
    },
}


THEMES["epl"] = {
    "primary": "#37003C", "accent": "#E90052", "bg": "#F5F3F7", "edition": "PREMIER LEAGUE EDITION",
    "banner": "radial-gradient(circle at 85% 50%, transparent 62px, rgba(255,255,255,.85) 63px 66px, transparent 67px),"
              "linear-gradient(90deg, rgba(55,0,60,.75), rgba(55,0,60,0) 60%),"
              "repeating-linear-gradient(90deg, #3E8E41 0 80px, #357A38 80px 160px)",
}


def _pitch(rgb):
    return (f"radial-gradient(circle at 85% 50%, transparent 62px, rgba(255,255,255,.85) 63px 66px, transparent 67px),"
            f"linear-gradient(90deg, rgba({rgb},.78), rgba({rgb},0) 60%),"
            "repeating-linear-gradient(90deg, #3E8E41 0 80px, #357A38 80px 160px)")


THEMES["laliga"] = {"primary": "#0B2545", "accent": "#FF4B44", "bg": "#F5F6F8", "edition": "LA LIGA EDITION",
                    "banner": _pitch("11,37,69")}
THEMES["bundesliga"] = {"primary": "#1A1A1A", "accent": "#D20515", "bg": "#F6F5F5", "edition": "BUNDESLIGA EDITION",
                        "banner": _pitch("26,26,26")}
THEMES["ucl"] = {"primary": "#0A1E5C", "accent": "#00A3E0", "bg": "#F3F5FA", "edition": "CHAMPIONS LEAGUE EDITION",
                 "banner": _pitch("10,30,92")}
THEMES["uel"] = {"primary": "#1F1F1F", "accent": "#F68E1E", "bg": "#F7F5F2", "edition": "EUROPA LEAGUE EDITION",
                 "banner": _pitch("31,31,31")}


def inject_css(theme):
    t = THEMES[theme]
    css = """<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap');
:root { --primary: PRIMARY; --accent: ACCENT; }
.stApp { background: BG; }
html, body, [class*="css"], .stMarkdown, .stDataFrame { font-family: 'Inter', sans-serif; }
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding-top: 1.5rem; max-width: 1200px; }
.banner { border-radius: 18px; padding: 1.6rem 1.8rem; margin-bottom: 1rem; background: BANNER;
  border-bottom: 6px solid var(--accent); box-shadow: 0 8px 24px rgba(15,23,42,.18); }
.brand-name { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:3rem; line-height:1; color:#fff;
  letter-spacing:.03em; text-shadow: 0 2px 8px rgba(0,0,0,.45); }
.edition { display:inline-block; margin-top:.5rem; background:var(--accent); color:#fff; font-weight:700; font-size:.8rem;
  letter-spacing:.12em; padding:.25rem .7rem; border-radius:6px; }
.brand-tag { color:#fff; opacity:.9; font-size:.9rem; margin-left:.6rem; text-shadow:0 1px 4px rgba(0,0,0,.5); }
.hero { border-radius:18px; padding:1.4rem 1.6rem; margin:1rem 0 .75rem; display:flex; align-items:center;
  justify-content:space-between; color:#fff; box-shadow:0 10px 24px rgba(15,23,42,.2); }
.hero-side { flex:1; }
.hero-side.right { text-align:right; }
.hero-abbr { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:2.8rem; line-height:1; text-shadow:0 2px 6px rgba(0,0,0,.3); }
.hero-name { font-weight:700; font-size:1.15rem; margin-top:.2rem; }
.hero-sub { font-size:.85rem; opacity:.9; }
.hero-vs { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:1.6rem; padding:.4rem .9rem;
  border-radius:999px; background:rgba(255,255,255,.95); color:var(--primary); margin:0 1rem; }
.pill-game { display:inline-block; background:#fff; border:2px solid var(--primary); color:var(--primary); border-radius:999px;
  padding:.3rem .9rem; font-size:.9rem; font-weight:600; margin-bottom:.5rem; }
.section { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:1.55rem; letter-spacing:.03em;
  margin:1.6rem 0 .6rem; color:var(--primary); text-transform:uppercase; border-left:5px solid var(--accent); padding-left:.6rem; }
.section small { font-family:'Inter',sans-serif; font-size:.8rem; font-weight:500; color:#64748B; text-transform:none; letter-spacing:0; margin-left:.5rem; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:.7rem; }
.card { background:#fff; border:1px solid #E2E8F0; border-top:4px solid var(--primary); border-radius:12px; padding:.8rem .9rem;
  box-shadow:0 2px 8px rgba(15,23,42,.06); }
.card-label { color:#64748B; font-size:.75rem; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
.card-value { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:2.1rem; color:#0F172A; line-height:1.1; }
.delta { display:inline-block; font-size:.75rem; font-weight:700; border-radius:999px; padding:.1rem .5rem; margin-top:.2rem; }
.up { background:#DCFCE7; color:#15803D; }
.down { background:#FEE2E2; color:#B91C1C; }
.flat { background:#F1F5F9; color:#64748B; }
.bar { height:6px; border-radius:999px; background:#E2E8F0; margin-top:.5rem; overflow:hidden; }
.bar > div { height:100%; border-radius:999px; background:linear-gradient(90deg,var(--primary),var(--accent)); }
.inj-team { font-weight:700; color:var(--primary); margin-bottom:.4rem; }
.inj { display:flex; justify-content:space-between; gap:.6rem; align-items:center; background:#fff;
  border:1px solid #E2E8F0; border-radius:10px; padding:.5rem .7rem; margin-bottom:.4rem; font-size:.88rem; }
.inj-name { color:#0F172A; font-weight:600; }
.inj-note { color:#64748B; font-size:.78rem; }
.status { font-size:.72rem; font-weight:700; border-radius:999px; padding:.15rem .55rem; white-space:nowrap; text-transform:uppercase; }
.s-out { background:#FEE2E2; color:#B91C1C; }
.s-doubt { background:#FFEDD5; color:#C2410C; }
.s-q { background:#FEF9C3; color:#A16207; }
.s-ok { background:#DCFCE7; color:#15803D; }
.muted { color:#64748B; font-size:.85rem; }
.insights { background:#fff; border:1px solid #E2E8F0; border-left:6px solid var(--accent); border-radius:14px;
  padding:1rem 1.2rem; margin:.8rem 0 .4rem; box-shadow:0 2px 10px rgba(15,23,42,.07); }
.insights-title { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:1.4rem; color:var(--primary);
  text-transform:uppercase; letter-spacing:.03em; display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.grade { font-family:'Inter',sans-serif; font-size:.72rem; font-weight:700; padding:.2rem .6rem; border-radius:999px; letter-spacing:.04em; }
.g-good { background:#DCFCE7; color:#15803D; } .g-bad { background:#FEE2E2; color:#B91C1C; } .g-mid { background:#F1F5F9; color:#475569; }
.ins-line { color:#0F172A; font-size:.95rem; margin:.45rem 0; line-height:1.45; }
.ins-sub { font-weight:700; color:var(--primary); font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; margin-top:.8rem; }
.sneaky { display:flex; justify-content:space-between; align-items:center; gap:.8rem; background:#F8FAFC; border:1px solid #E2E8F0;
  border-radius:10px; padding:.55rem .8rem; margin-top:.4rem; font-size:.9rem; }
.sneaky b { color:#0F172A; }
.edge { font-weight:800; color:#15803D; white-space:nowrap; }
.game-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:.8rem; }
.dmx { width:100%; border-collapse:collapse; background:#fff; border:1px solid #E2E8F0; border-radius:10px; overflow:hidden; font-size:.85rem; }
.dmx th { text-align:left; color:#64748B; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; padding:.45rem .6rem; border-bottom:1px solid #E2E8F0; }
.dmx td { padding:.45rem .6rem; border-bottom:1px solid #F1F5F9; color:#0F172A; font-weight:600; }
.dmx td.up { color:#15803D; } .dmx td.down { color:#B91C1C; } .dmx td.flat { color:#64748B; }
.game-card { margin-bottom:.4rem; background:#fff; border:1px solid #E2E8F0; border-radius:12px; padding:.8rem 1rem; box-shadow:0 2px 8px rgba(15,23,42,.06); }
.game-teams { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:1.7rem; color:#0F172A; }
.game-teams span { color:#94A3B8; font-size:1.1rem; }
.game-meta { color:#334155; font-size:.85rem; margin-top:.4rem; line-height:1.5; }
.game-star { margin-top:.5rem; background:#FEF3C7; color:#92400E; font-weight:700; font-size:.82rem; border-radius:8px; padding:.3rem .5rem; }
.star { background:linear-gradient(135deg,#FFFBEB,#FEF3C7); border:1px solid #FCD34D; border-radius:12px;
  padding:.7rem .9rem; margin:.6rem 0 .3rem; }
.star-label { font-size:.75rem; font-weight:800; color:#B45309; letter-spacing:.08em; text-transform:uppercase; }
.star-name { font-family:'Barlow Condensed',sans-serif; font-weight:800; font-size:1.6rem; color:#0F172A; line-height:1.15; }
.star-name span { font-family:'Inter',sans-serif; font-size:.8rem; font-weight:600; color:#92400E; margin-left:.4rem; }
</style>"""
    css = (css.replace("PRIMARY", t["primary"]).replace("ACCENT", t["accent"])
              .replace("BANNER", t["banner"]).replace("BG", t["bg"]))
    st.markdown(css, unsafe_allow_html=True)


def banner(theme):
    t = THEMES[theme]
    st.markdown(
        f'<div class="banner"><div><span class="brand-name">DATA FINDER</span>'
        f'<span class="brand-tag">matchups · trends · injuries</span></div>'
        f'<span class="edition">{t["edition"]}</span></div>',
        unsafe_allow_html=True,
    )


def section(title, note=""):
    note_html = f"<small>{html.escape(note)}</small>" if note else ""
    st.markdown(f'<div class="section">{html.escape(title)}{note_html}</div>', unsafe_allow_html=True)


# ---------- LOADING DATA ----------
# @st.cache_data remembers results for a while (ttl = seconds)
# so the app is fast and we don't spam ESPN with repeat requests.

def get_json(url, params=None):
    """Download data from ESPN and turn it into Python dictionaries."""
    response = requests.get(url, params=params, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=24 * 3600)
def load_teams(site):
    data = get_json(f"{site}/teams")
    teams = []
    for entry in data["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        teams.append({
            "id": str(t["id"]),
            "name": t["displayName"],
            "abbr": t.get("abbreviation", ""),
            "color": t.get("color") or "334155",
            "alt": t.get("alternateColor") or "0F172A",
        })
    return sorted(teams, key=lambda t: t["name"])


def _load_one_roster(args):
    site, team = args
    try:
        data = get_json(f"{site}/teams/{team['id']}/roster")
    except Exception:
        return []
    flat = []
    for a in data.get("athletes", []):
        # NBA rosters are one list; NFL rosters are grouped (offense, defense, special teams)
        if isinstance(a, dict) and "items" in a:
            flat.extend(a["items"])
        else:
            flat.append(a)
    return [
        {
            "id": str(a["id"]),
            "name": a.get("displayName") or a.get("fullName", ""),
            "pos": (a.get("position") or {}).get("abbreviation", ""),
            # the roster's own injury tag (a second source besides the league injury feed)
            "inj": next((i.get("status") or "" for i in (a.get("injuries") or []) if isinstance(i, dict)), ""),
            "team_id": team["id"],
            "team_abbr": team["abbr"],
        }
        for a in flat
        if isinstance(a, dict) and a.get("id")
    ]


@st.cache_data(ttl=3 * 3600)
def load_players(site, teams):
    with ThreadPoolExecutor(max_workers=10) as pool:  # load all rosters at once (faster)
        results = pool.map(_load_one_roster, [(site, t) for t in teams])
    players = [p for roster in results for p in roster]
    return sorted(players, key=lambda p: p["name"])


def _unique(names):
    """Make column names unique (e.g. a second 'SACK' becomes 'SACK 2')."""
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} {seen[n]}")
    return out


@st.cache_data(ttl=3 * 3600)
def load_gamelog(web, player_id, season, season_text, use_names):
    """Every game a player played in one season, one row per game."""
    try:
        data = get_json(f"{web}/athletes/{player_id}/gamelog", params={"season": season})
    except Exception:
        return pd.DataFrame()

    labels = data.get("labels") or []
    names = data.get("names") or []
    if use_names and len(names) == len(labels) and names:
        columns = [NAME_MAP.get(n, lbl) for n, lbl in zip(names, labels)]
    else:
        columns = labels
    columns = _unique(columns)

    game_info = data.get("events") or {}
    rows, seen = [], set()
    for season_type in data.get("seasonTypes") or []:
        type_name = (season_type.get("displayName") or "").lower()
        if "preseason" in type_name:
            continue
        for category in season_type.get("categories") or []:
            for game in category.get("events") or []:
                game_id = str(game.get("eventId", ""))
                stats = game.get("stats")
                if not game_id or not stats or game_id in seen:
                    continue
                seen.add(game_id)
                info = game_info.get(game_id, {})
                opp = info.get("opponent") or {}
                row = {
                    "event_id": game_id,
                    "Date": (info.get("gameDate") or "")[:10],
                    "Season": season_text,
                    "Type": "Playoffs" if "post" in type_name else "Regular",
                    "opp_id": str(opp.get("id", "")),
                    # which team he played FOR (when ESPN includes it), so trades don't mix teams
                    "team_id": str((info.get("team") or {}).get("id", "") or ""),
                    "Opp": opp.get("abbreviation") or opp.get("displayName", ""),
                    "H/A": "Home" if info.get("atVs") == "vs" else "Away",
                    "Result": f"{info.get('gameResult', '')} {info.get('score', '')}".strip(),
                }
                row.update(dict(zip(columns, stats)))
                rows.append(row)
    return pd.DataFrame(rows)


def clean_numbers(df, sport_key):
    """ESPN sends stats as text. Turn them into numbers and add combo stats."""
    df = df.copy()
    for col in df.columns:
        if col in META_COLS or col in ("opp_id", "event_id", "team_id"):
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().sum() >= 0.5 * df[col].notna().sum():  # mostly numbers? keep as numbers
            df[col] = converted

    def has(*cols):
        return all(c in df.columns and pd.api.types.is_numeric_dtype(df[c]) for c in cols)

    if sport_key == "nba":
        if "3PT" in df:  # "4-9" means 4 made out of 9
            df["3PM"] = pd.to_numeric(df["3PT"].astype(str).str.split("-").str[0], errors="coerce")
            df["3PA"] = pd.to_numeric(df["3PT"].astype(str).str.split("-").str[1], errors="coerce")
        if "FG" in df:
            df["FGA"] = pd.to_numeric(df["FG"].astype(str).str.split("-").str[1], errors="coerce")
            df["FGM"] = pd.to_numeric(df["FG"].astype(str).str.split("-").str[0], errors="coerce")
        if has("PTS", "REB", "AST"):
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    if sport_key == "nfl":
        if has("RUSH YDS", "REC YDS"):
            df["RUSH+REC YDS"] = df["RUSH YDS"].fillna(0) + df["REC YDS"].fillna(0)
        if has("PASS YDS", "RUSH YDS"):
            df["PASS+RUSH YDS"] = df["PASS YDS"].fillna(0) + df["RUSH YDS"].fillna(0)
    return df


@st.cache_data(ttl=3 * 3600)
def load_history(sport_name, player_id):
    cfg = SPORTS[sport_name]
    current = cfg["season_fn"]()
    seasons = [current - i for i in range(cfg["seasons_back"])]

    def one(s):
        return load_gamelog(cfg["web"], player_id, s, cfg["season_label"](s), cfg["use_names"])

    with ThreadPoolExecutor(max_workers=len(seasons)) as pool:
        frames = [f for f in pool.map(one, seasons) if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = clean_numbers(pd.concat(frames, ignore_index=True), cfg["key"])
    return df.sort_values("Date", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=15 * 60)  # injuries refresh every 15 minutes
def load_injuries(site):
    data = get_json(f"{site}/injuries")
    by_team = {}
    for group in data.get("injuries", []):
        rows = []
        for item in group.get("injuries", []):
            athlete = item.get("athlete") or {}
            rows.append({
                "player": athlete.get("displayName", ""),
                "id": str(athlete.get("id", "") or ""),
                "status": item.get("status", ""),
                "note": item.get("shortComment") or item.get("longComment") or "",
            })
        by_team[str(group.get("id", ""))] = rows
        by_team[group.get("displayName", "")] = rows
    return by_team


@st.cache_data(ttl=10 * 60)
def load_next_game(site, team_id):
    """The team's next game that hasn't finished yet (reads the team's schedule,
    so it works any day of the week and skips bye weeks)."""
    data = get_json(f"{site}/teams/{team_id}/schedule")
    upcoming = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        status = (comp.get("status") or event.get("status") or {}).get("type", {})
        if status.get("completed") or status.get("state") == "post":
            continue  # already played
        sides = {c.get("homeAway"): str((c.get("team") or {}).get("id", "")) for c in comp.get("competitors", [])}
        if team_id not in sides.values():
            continue
        upcoming.append({
            "id": str(event.get("id", "")),
            "home": sides.get("home"),
            "away": sides.get("away"),
            "date": event.get("date", ""),
            "opp_id": sides.get("away") if sides.get("home") == team_id else sides.get("home"),
            "label": event.get("shortName", ""),
            "status": status.get("shortDetail", ""),
        })
    upcoming.sort(key=lambda g: g["date"])
    return upcoming[0] if upcoming else None


# ---------- DEFENSE VS POSITION ----------
# "What does this defense give up to WR1s / point guards?"
# We look at the opponent's recent games, open each box score, and pull out
# the players from the OTHER team who played the chosen position.

# NFL slots: position, stat used to rank players in that game, which one (1 = best)
NFL_SLOTS = {
    "QB1": ("QB", "ATT", 1), "RB1": ("RB", "RUSH+REC YDS", 1), "RB2": ("RB", "RUSH+REC YDS", 2),
    "WR1": ("WR", "REC YDS", 1), "WR2": ("WR", "REC YDS", 2), "WR3": ("WR", "REC YDS", 3),
    "TE1": ("TE", "REC YDS", 1),
}
NFL_SLOT_STATS = {
    "QB": ["CMP", "ATT", "PASS YDS", "PASS TD", "INT", "RUSH YDS"],
    "RB": ["CAR", "RUSH YDS", "REC", "REC YDS", "RUSH+REC YDS", "TD"],
    "WR": ["REC", "TGT", "REC YDS", "REC TD"],
    "TE": ["REC", "TGT", "REC YDS", "REC TD"],
}
# NBA groups: which positions count, minimum minutes to count a player
NBA_GROUPS = {"Guards": ("PG", "SG", "G"), "Forwards": ("SF", "PF", "F"), "Centers": ("C",)}
NBA_GROUP_STATS = ["MIN", "PTS", "REB", "AST", "3PM", "PRA"]
NBA_MIN_MINUTES = 15
DVP_GAMES = {"nba": 15, "nfl": 10}   # how many of the opponent's recent games to look at

# Box score column names (ESPN's long names and short labels -> our names)
BOX_KEYS = {**NAME_MAP,
            "completions/passingAttempts": "C/ATT",
            "minutes": "MIN", "points": "PTS", "rebounds": "REB", "totalRebounds": "REB",
            "assists": "AST", "steals": "STL", "blocks": "BLK", "turnovers": "TO",
            "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "3PT",
            "fieldGoalsMade-fieldGoalsAttempted": "FG"}
BOX_LABELS = {
    "passing": {"C/ATT": "C/ATT", "YDS": "PASS YDS", "TD": "PASS TD", "INT": "INT"},
    "rushing": {"CAR": "CAR", "YDS": "RUSH YDS", "TD": "RUSH TD"},
    "receiving": {"REC": "REC", "YDS": "REC YDS", "TD": "REC TD", "TGTS": "TGT", "TGT": "TGT"},
    "": {"MIN": "MIN", "PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "TO": "TO", "3PT": "3PT",
         "FG": "FG"},
}


def _score(competitor):
    """ESPN sends scores as a number, text, or {"value": 27}."""
    sc = competitor.get("score")
    if isinstance(sc, dict):
        sc = sc.get("value", sc.get("displayValue"))
    try:
        return float(sc)
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=6 * 3600)
def load_completed_games(site, team_id, season):
    """A team's finished games in one season (newest first)."""
    data = get_json(f"{site}/teams/{team_id}/schedule", params={"season": season})
    games = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        status = (comp.get("status") or event.get("status") or {}).get("type", {})
        if not (status.get("completed") or status.get("state") == "post"):
            continue
        comps = comp.get("competitors", [])
        sides = [str((c.get("team") or {}).get("id", "")) for c in comps]
        other = next((s for s in sides if s != team_id), "")
        scores = {str((c.get("team") or {}).get("id", "")): _score(c) for c in comps}
        games.append({"id": str(event.get("id", "")), "date": (event.get("date") or "")[:10], "other_id": other,
                      "pts_for": scores.get(team_id), "pts_against": scores.get(other)})
    return sorted(games, key=lambda g: g["date"], reverse=True)


@st.cache_data(ttl=24 * 3600)  # finished games never change, so keep them a full day
def load_boxscore(site, event_id):
    """Every player's stat line from one game."""
    data = get_json(f"{site}/summary", params={"event": event_id})
    out = {}
    for team_block in (data.get("boxscore") or {}).get("players", []):
        team_id = str((team_block.get("team") or {}).get("id", ""))
        for group in team_block.get("statistics", []):
            group_name = (group.get("name") or "").lower()
            keys = group.get("keys") or []
            labels = group.get("labels") or group.get("names") or []
            label_map = BOX_LABELS.get(group_name, BOX_LABELS[""])
            for a in group.get("athletes", []):
                if a.get("didNotPlay"):
                    continue
                athlete = a.get("athlete") or {}
                pid = str(athlete.get("id", ""))
                stats = a.get("stats") or []
                if not pid or not stats:
                    continue
                row = out.setdefault(pid, {
                    "team_id": team_id, "pid": pid, "Player": athlete.get("displayName", ""),
                    "pos": (athlete.get("position") or {}).get("abbreviation", ""),
                })
                for i, value in enumerate(stats):
                    k = keys[i] if i < len(keys) else ""
                    lbl = (labels[i] if i < len(labels) else "").upper()
                    name = BOX_KEYS.get(k) or label_map.get(lbl)
                    if name and name not in row:
                        row[name] = value
    return list(out.values())


def _clean_box(df, sport_key):
    df = df.copy()
    for col in df.columns:
        if col in ("team_id", "pid", "Player", "pos", "C/ATT", "3PT", "FG"):
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    num = lambda c: df[c].fillna(0) if c in df else 0
    if sport_key == "nba":
        if "3PT" in df:
            df["3PM"] = pd.to_numeric(df["3PT"].astype(str).str.split("-").str[0], errors="coerce")
        if "FG" in df:
            df["FGA"] = pd.to_numeric(df["FG"].astype(str).str.split("-").str[1], errors="coerce")
        df["PRA"] = num("PTS") + num("REB") + num("AST")
    else:
        if "C/ATT" in df:
            parts = df["C/ATT"].astype(str).str.split("/")
            df["CMP"] = pd.to_numeric(parts.str[0], errors="coerce")
            df["ATT"] = pd.to_numeric(parts.str[1], errors="coerce")
        df["RUSH+REC YDS"] = num("RUSH YDS") + num("REC YDS")
        df["TD"] = num("RUSH TD") + num("REC TD")
    return df


def defense_vs_position(cfg, opponent_id, slot, pos_by_id, team_by_id):
    """One row per (game, player) for players at `slot` who faced `opponent_id`."""
    sport_key = cfg["key"]
    want = DVP_GAMES[sport_key]
    current = cfg["season_fn"]()
    games = []
    for season in (current, current - 1):  # early in the season, borrow last season's games
        try:
            games += load_completed_games(cfg["site"], opponent_id, season)
        except Exception:
            pass
        if len(games) >= want:
            break
    games = list({g["id"]: g for g in games}.values())[:want]  # no game counted twice
    if not games:
        return pd.DataFrame()

    def one(g):
        try:
            return g, load_boxscore(cfg["site"], g["id"])
        except Exception:
            return g, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, games))

    rows = []
    for game, box in results:
        if not box:
            continue
        df = _clean_box(pd.DataFrame(box), sport_key)
        df["pos"] = [p or pos_by_id.get(pid, "") for p, pid in zip(df["pos"], df["pid"])]
        facing = df[df["team_id"] != opponent_id]  # the offense that played against this defense
        if sport_key == "nfl":
            pos, rank_stat, nth = NFL_SLOTS[slot]
            at_pos = facing[facing["pos"] == pos]
            if rank_stat not in at_pos or len(at_pos) < nth:
                continue
            picked = at_pos.sort_values(rank_stat, ascending=False).iloc[[nth - 1]]
        else:
            group = NBA_GROUPS[slot]
            picked = facing[facing["pos"].isin(group)]
            if "MIN" in picked:
                picked = picked[picked["MIN"].fillna(0) >= NBA_MIN_MINUTES]
        for _, r in picked.iterrows():
            row = r.to_dict()
            row["Date"] = game["date"]
            row["Team"] = team_by_id.get(game["other_id"], {}).get("abbr", "")
            rows.append(row)
    return pd.DataFrame(rows)


def default_slot(cfg, pos):
    if cfg["key"] == "nfl":
        return f"{pos}1" if f"{pos}1" in NFL_SLOTS else "WR1"
    return next((g for g, members in NBA_GROUPS.items() if pos in members), "Guards")


# ---------- MATCHUP INSIGHTS ----------
# Compares what the defense allows at each position to each player's own average,
# then writes it up in plain English. Pure math, no AI, so it's free and instant.
INSIGHT_STAT = {"QB": "PASS YDS", "RB": "RUSH+REC YDS", "WR": "REC YDS", "TE": "REC YDS"}
STAT_WORDS = {"PASS YDS": "passing yards", "RUSH+REC YDS": "rush + rec yards",
              "REC YDS": "receiving yards", "PRA": "pts + reb + ast"}
EDGE = 0.15              # 15%+ above/below a player's average counts as a real edge
NBA_STARTER_MINUTES = 24  # NBA comparisons use starter level minutes only


RECENT_FORM_GAMES = {"nfl": 10, "nba": 15}  # baseline = his last N games played, across seasons


@st.cache_data(ttl=3 * 3600)
def recent_avgs(sport_name, player_id):
    """A player's per game averages over his last N games PLAYED (this season plus last season),
    so a star returning from injury still looks like a star (e.g. a WR1 who missed 5 weeks)."""
    cfg = SPORTS[sport_name]
    current = cfg["season_fn"]()
    frames = []
    for s in (current, current - 1):
        df = load_gamelog(cfg["web"], player_id, s, cfg["season_label"](s), cfg["use_names"])
        if not df.empty:
            frames.append(df)
    if not frames:
        return {}, 0, ""
    df = pd.concat(frames, ignore_index=True).sort_values("Date", ascending=False)
    last_played = df["Date"].max()  # most recent game he actually played
    df = clean_numbers(df.head(RECENT_FORM_GAMES[cfg["key"]]), cfg["key"])
    return df.select_dtypes("number").mean().to_dict(), len(df), last_played


def teammate_slots(sport_name, players, team_id, out_names, maybe_names=frozenset()):
    """Figure out each healthy teammate's role (WR1, TE1, Guards...) from this season's numbers."""
    cfg = SPORTS[sport_name]
    nfl = cfg["key"] == "nfl"
    mates = [p for p in players if p["team_id"] == team_id and not is_listed(p, out_names)
             and status_class(p.get("inj", "")) != "s-out"]
    # roster says Questionable/Doubtful: treat like the injury feed does
    maybe_names = set(maybe_names) | {p["id"] for p in mates if p.get("inj") and status_class(p["inj"]) != "s-ok"}
    if nfl:
        mates = [p for p in mates if p["pos"] in INSIGHT_STAT]
    with ThreadPoolExecutor(max_workers=8) as pool:
        avgs = list(pool.map(lambda p: recent_avgs(sport_name, p["id"]), mates))
    # A player only counts if he played in his team's most recent games. This catches
    # IR, long term injuries, suspensions and healthy scratches even if the injury feed misses them.
    cutoff = ""
    try:
        team_games = load_completed_games(cfg["site"], team_id, cfg["season_fn"]())
        look_back = ACTIVE_LOOKBACK[cfg["key"]]
        if len(team_games) >= look_back:
            cutoff = team_games[look_back - 1]["date"]
    except Exception:
        pass
    rows = []
    for p, (a, games, last_played) in zip(mates, avgs):
        returning = False
        if cutoff and last_played < cutoff:
            if not is_listed(p, maybe_names):
                continue  # hasn't played lately and isn't listed as possibly returning
            returning = True  # missed time but is Questionable/Doubtful/Probable: could be back this week
        stat = INSIGHT_STAT.get(p["pos"]) if nfl else "PRA"
        if games < 2 or not a.get(stat):
            continue
        if not nfl and a.get("MIN", 0) < NBA_STARTER_MINUTES:
            continue
        rows.append({**p, "stat": stat, "avg": a[stat], "returning": returning,
                     "rank_val": a.get("ATT" if p["pos"] == "QB" else stat, 0)})
    if nfl:  # WR with most receiving yards = WR1, and so on
        for pos in INSIGHT_STAT:
            group = sorted([r for r in rows if r["pos"] == pos], key=lambda r: r["rank_val"], reverse=True)
            for i, r in enumerate(group):
                r["slot"] = f"{pos}{i + 1}"
        rows = [r for r in rows if r.get("slot") in NFL_SLOTS]
    else:
        for r in rows:
            r["slot"] = default_slot(cfg, r["pos"])
    return rows


STAR_SLOTS = {"QB1", "RB1", "WR1", "WR2", "TE1"}  # "player to watch" must be a main piece of the offense
# Minimum average to be "player to watch" (no +128% off a 19 yard average)
STAR_MIN = {"PASS YDS": 180, "RUSH+REC YDS": 50, "REC YDS": 40, "PRA": 25}
SNEAKY_MIN = {"PASS YDS": 120, "RUSH+REC YDS": 25, "REC YDS": 20, "PRA": 15}
ACTIVE_LOOKBACK = {"nfl": 1, "nba": 3}  # must have played in his team's last N games (unless listed Q/D/P)


def score_matchups(cfg, rows, dvp_for_slot):
    """For each player: what the defense allows at his role vs his own average (the 'edge')."""
    nba = cfg["key"] == "nba"
    items = []
    for r in rows:
        d = dvp_for_slot(r["slot"])
        if d is None or d.empty or r["stat"] not in d:
            continue
        if nba and "MIN" in d:
            d = d[d["MIN"].fillna(0) >= NBA_STARTER_MINUTES]
        if len(d) < 3:
            continue
        allowed = d[r["stat"]].mean()
        items.append({**r, "allowed": allowed, "edge": (allowed - r["avg"]) / r["avg"], "n": len(d)})
    return items


def star_card(cfg, star, team_by_id):
    """'Player to watch' banner: best matchup on paper from EITHER team in the game."""
    if not star or star["edge"] < EDGE:
        return ""
    nba = cfg["key"] == "nba"
    team = team_by_id.get(star["team_id"], {}).get("abbr", "")
    defense = html.escape(star["defense_abbr"])
    role = f"starting {star['slot'].lower()}" if nba else f"{star['slot']}s"
    return (
        f'<div class="star"><div class="star-label">⭐ Player to watch</div>'
        f'<div class="star-name">{html.escape(star["name"])} <span>{html.escape(team)} · {star["slot"]}'
        f'{" · Q, if he plays" if star.get("returning") else ""}</span></div>'
        f'<div class="ins-line">Best matchup on paper in this game: {defense} allows <b>{star["allowed"]:.1f} '
        f'{STAT_WORDS.get(star["stat"], star["stat"])}</b> to {role} vs his <b>{star["avg"]:.1f}</b> average '
        f'<span class="edge">+{star["edge"]:.0%}</span></div></div>'
    )


def build_insights(cfg, player, opponent, items, questionable, star_html="", extra_html=""):
    nba = cfg["key"] == "nba"
    opp = html.escape(opponent["abbr"])
    if not items and not star_html and not extra_html:
        return ""

    def who(slot):
        return f"starting {slot.lower()}" if nba else f"{slot}s"

    me = next((r for r in items if r["id"] == player["id"]), None)
    parts = []
    if me:
        e = me["edge"]
        grade = ("g-good", "GOOD MATCHUP") if e >= EDGE else ("g-bad", "TOUGH MATCHUP") if e <= -EDGE else ("g-mid", "NEUTRAL")
        last = html.escape(short_name(player["name"]))
        word = STAT_WORDS.get(me["stat"], me["stat"])
        parts.append(f'<div class="insights-title">💡 Matchup insights <span class="grade {grade[0]}">{grade[1]}</span></div>')
        parts.append(
            f'<div class="ins-line">{opp} allows <b>{me["allowed"]:.1f} {word}</b> per game to {who(me["slot"])}. '
            f'{last} averages <b>{me["avg"]:.1f}</b>, so this spot is <b>{abs(e):.0%} {"above" if e >= 0 else "below"}</b> his norm.</div>'
        )
    else:
        parts.append('<div class="insights-title">💡 Matchup insights</div>')
    if extra_html:
        parts.append(extra_html)  # game environment + usage trend sentences
    if star_html:
        parts.append(star_html)
    if not items:
        return f'<div class="insights">{"".join(parts)}</div>'

    for r in items:
        if r.get("returning") and r["id"] != player["id"]:
            role = r["slot"] if not nba else f"a starting {r['slot'][:-1].lower()}"
            parts.append(f'<div class="ins-line">⚠️ <b>{html.escape(r["name"])}</b> '
                         f'<span class="status s-q">Q</span> projects as {role} if he plays. '
                         f'If he sits, roles move up.</div>')

    weakest = max(items, key=lambda r: r["edge"])
    if weakest["edge"] >= EDGE:
        parts.append(f'<div class="ins-line">🎯 Biggest soft spot: {opp} has been weakest against '
                     f'<b>{who(weakest["slot"])}</b> ({html.escape(weakest["name"])}\'s role here).</div>')

    sneaky = sorted([r for r in items if r["id"] != player["id"] and r["edge"] >= EDGE
                     and r["avg"] >= SNEAKY_MIN.get(r["stat"], 0)],
                    key=lambda r: r["edge"], reverse=True)[:3]
    parts.append('<div class="ins-sub">👀 Sneaky looks on his team</div>')
    if not sneaky:
        parts.append('<div class="ins-line muted">No teammates with a big edge in this matchup.</div>')
    for r in sneaky:
        q = " <span class='status s-q'>Q</span>" if is_listed(r, questionable) or r.get("returning") else ""
        parts.append(
            f'<div class="sneaky"><div><b>{html.escape(r["name"])}</b> ({r["slot"]}){q}<br>'
            f'<span class="muted">{opp} allows {r["allowed"]:.1f} {STAT_WORDS.get(r["stat"], r["stat"])} to '
            f'{who(r["slot"])} vs his {r["avg"]:.1f} avg</span></div><span class="edge">+{r["edge"]:.0%}</span></div>'
        )
    parts.append(f'<div class="muted" style="margin-top:.7rem">Based on {opp}\'s last {DVP_GAMES[cfg["key"]]} games '
                 f'and each player\'s last {RECENT_FORM_GAMES[cfg["key"]]} games played. Trends, not guarantees.</div>')
    return f'<div class="insights">{"".join(parts)}</div>'


# ---------- GAME ENVIRONMENT ----------
# Is this going to be a shootout or a slog? Uses each team's recent scoring plus the
# sportsbook spread and over/under that ESPN shows on its game pages (free).
ENV_GAMES = {"nba": 10, "nfl": 8}
ENV_EDGE = 0.04  # 4% above/below the league's average total = high/low scoring


def team_form(cfg, team_id):
    """Average points scored and allowed over the team's recent games."""
    current = cfg["season_fn"]()
    games = []
    for season in (current, current - 1):
        try:
            games += [g for g in load_completed_games(cfg["site"], team_id, season)
                      if g.get("pts_for") is not None and g.get("pts_against") is not None]
        except Exception:
            pass
        if len(games) >= 3:
            break
    games = games[:ENV_GAMES[cfg["key"]]]
    if len(games) < 2:
        return None
    return {"for": sum(g["pts_for"] for g in games) / len(games),
            "against": sum(g["pts_against"] for g in games) / len(games), "n": len(games)}


@st.cache_data(ttl=6 * 3600)
def league_avg_total(sport_name):
    cfg = SPORTS[sport_name]
    teams = load_teams(cfg["site"])
    with ThreadPoolExecutor(max_workers=10) as pool:
        forms = [f for f in pool.map(lambda t: team_form(cfg, t["id"]), teams) if f]
    return sum(f["for"] + f["against"] for f in forms) / len(forms) if forms else None


@st.cache_data(ttl=30 * 60)
def load_game_odds(site, event_id):
    """Spread and over/under from ESPN's game page (they list a sportsbook's line)."""
    data = get_json(f"{site}/summary", params={"event": event_id})
    for src in (data.get("pickcenter") or []) + (data.get("odds") or []):
        if not isinstance(src, dict):
            continue
        total = src.get("overUnder")
        try:
            total = float(total) if total not in (None, "") else None
        except (TypeError, ValueError):
            total = None
        details = src.get("details") or ""
        if total or details:
            return {"total": total, "details": details, "provider": (src.get("provider") or {}).get("name", "")}
    return None


def implied_totals(total, details, team_abbr, opp_abbr):
    """Turn 'KC -3.5' and O/U 47.5 into each team's expected points (25.5 and 22)."""
    if not total:
        return None
    if not details or details.upper() in ("EVEN", "PK", "PICK"):
        return {team_abbr: total / 2, opp_abbr: total / 2}
    parts = details.split()
    try:
        fav, spread = parts[0], abs(float(parts[-1]))
    except (ValueError, IndexError):
        return None
    if fav not in (team_abbr, opp_abbr):
        return None
    dog = opp_abbr if fav == team_abbr else team_abbr
    return {fav: (total + spread) / 2, dog: (total - spread) / 2}


def game_environment(cfg, sport_name, team, opp, event_id=None):
    a, b = team_form(cfg, team["id"]), team_form(cfg, opp["id"])
    if not a or not b:
        return None
    env = {
        "proj_team": (a["for"] + b["against"]) / 2,  # what team usually scores + what opp usually allows
        "proj_opp": (b["for"] + a["against"]) / 2,
        "league": league_avg_total(sport_name),
        "odds": None, "implied": None,
    }
    env["proj_total"] = env["proj_team"] + env["proj_opp"]
    if event_id:
        try:
            env["odds"] = load_game_odds(cfg["site"], event_id)
        except Exception:
            pass
    total = (env["odds"] or {}).get("total") or env["proj_total"]
    env["total_used"] = total
    if env["odds"]:
        env["implied"] = implied_totals(env["odds"]["total"], env["odds"]["details"], team["abbr"], opp["abbr"])
    league = env["league"]
    if league:
        ratio = total / league
        env["label"] = ("HIGH SCORING", "g-good") if ratio >= 1 + ENV_EDGE else \
                       ("LOW SCORING", "g-bad") if ratio <= 1 - ENV_EDGE else ("AVERAGE PACE", "g-mid")
    else:
        env["label"] = ("", "g-mid")
    return env


def env_cards(env, team, opp):
    cards = []
    if env["odds"]:
        o = env["odds"]
        if o.get("details"):
            cards.append(("Spread", html.escape(o["details"]), html.escape(o.get("provider") or "sportsbook")))
        if o.get("total"):
            cards.append(("Over/Under", f'{o["total"]:.1f}', "game total"))
    if env["implied"]:
        cards.append((f'{team["abbr"]} implied', f'{env["implied"].get(team["abbr"], 0):.1f}', "points from the line"))
    cards.append(("Projected score", f'{env["proj_team"]:.0f}–{env["proj_opp"]:.0f}',
                  f'{team["abbr"]}–{opp["abbr"]} from recent form'))
    if env["league"]:
        cards.append(("League avg total", f'{env["league"]:.1f}', "points per game"))
    return "".join(f'<div class="card"><div class="card-label">{l}</div><div class="card-value">{v}</div>'
                   f'<div class="muted">{n}</div></div>' for l, v, n in cards)


# ---------- USAGE TRENDS ----------
# Is his role growing? Volume and share of the team's opportunities, last 3 games vs season.
USAGE_RECENT = 3
USAGE_STATS = {
    "nfl": {"QB": ["ATT", "CAR"], "RB": ["CAR", "RUSH SHARE", "TGT", "TGT SHARE"],
            "WR": ["TGT", "TGT SHARE", "REC"], "TE": ["TGT", "TGT SHARE", "REC"]},
    "nba": ["MIN", "FGA", "SHOT SHARE", "3PA"],
}
SHARE_OF = {"TGT SHARE": "TGT", "RUSH SHARE": "CAR", "SHOT SHARE": "FGA"}


def usage_frame(cfg, history, player):
    """This season's games with volume stats plus his share of the team's targets / carries / shots."""
    if history.empty:
        return pd.DataFrame(), []
    wanted = USAGE_STATS["nba"] if cfg["key"] == "nba" else USAGE_STATS["nfl"].get(player["pos"], [])
    season = history[history["Season"] == history["Season"].iloc[0]].copy()
    if season.empty or not wanted:
        return pd.DataFrame(), []
    shares = [w for w in wanted if w in SHARE_OF and SHARE_OF[w] in season.columns]
    if shares and "event_id" in season:
        def one(eid):
            try:
                return eid, load_boxscore(cfg["site"], eid)
            except Exception:
                return eid, []
        with ThreadPoolExecutor(max_workers=8) as pool:
            boxes = dict(pool.map(one, season["event_id"].tolist()))
        for share in shares:
            base = SHARE_OF[share]
            vals = []
            for eid, mine in zip(season["event_id"], season[base]):
                box = boxes.get(eid) or []
                if not box:
                    vals.append(None)
                    continue
                df = _clean_box(pd.DataFrame(box), cfg["key"])
                me = df[df["pid"] == player["id"]]
                if me.empty or base not in df:
                    vals.append(None)
                    continue
                team_total = df[df["team_id"] == me.iloc[0]["team_id"]][base].fillna(0).sum()
                vals.append(mine / team_total * 100 if team_total else None)
            season[share] = vals
    metrics = [w for w in wanted if w in season.columns and season[w].notna().any()]
    return season.sort_values("Date"), metrics


def usage_cards(season, metrics):
    cards, trends = [], {}
    recent = season.tail(USAGE_RECENT)
    for m in metrics:
        full, last = season[m].mean(), recent[m].mean()
        if pd.isna(full) or pd.isna(last):
            continue
        change = (last - full) / full if full else 0
        trends[m] = (last, full, change)
        cls = "up" if change >= EDGE else "down" if change <= -EDGE else "flat"
        arrow = "▲" if change > 0.02 else "▼" if change < -0.02 else "•"
        fmt = (lambda v: f"{v:.0f}%") if "SHARE" in m else (lambda v: f"{v:.1f}")
        cards.append(f'<div class="card"><div class="card-label">{html.escape(m)} · last {USAGE_RECENT}</div>'
                     f'<div class="card-value">{fmt(last)}</div><div class="muted">season {fmt(full)}</div>'
                     f'<span class="delta {cls}">{arrow} {change:+.0%}</span></div>')
    return "".join(cards), trends


def usage_line(player, trends):
    """One sentence for the insights box when his role is clearly growing or shrinking."""
    pick = next((m for m in ("TGT SHARE", "RUSH SHARE", "SHOT SHARE", "TGT", "CAR", "MIN") if m in trends), None)
    if not pick:
        return ""
    last, full, change = trends[pick]
    if abs(change) < EDGE:
        return ""
    fmt = (lambda v: f"{v:.0f}%") if "SHARE" in pick else (lambda v: f"{v:.1f}")
    word = "rising 📈" if change > 0 else "falling 📉"
    return (f'<div class="ins-line">Usage {word}: {html.escape(short_name(player["name"]))}\'s '
            f'{pick.lower()} is <b>{fmt(last)}</b> over his last {USAGE_RECENT} games vs <b>{fmt(full)}</b> '
            f'on the season.</div>')


# ---------- DISPLAY PIECES ----------
def pick_stats(cfg, pos, df):
    wanted = cfg["stats_by_pos"].get(pos) or cfg["default_stats"]
    numeric = [c for c in df.columns if c not in META_COLS and c != "opp_id" and pd.api.types.is_numeric_dtype(df[c])]
    stats = [s for s in wanted if s in numeric and df[s].fillna(0).abs().sum() > 0]
    if not stats:  # nothing matched: show whatever the player actually records
        stats = [c for c in numeric if df[c].fillna(0).abs().sum() > 0][:8]
    return stats


def stat_cards(vs_opp, history, stats):
    cards = []
    for stat in stats:
        opp_avg, all_avg = vs_opp[stat].mean(), history[stat].mean()
        diff = opp_avg - all_avg
        good = -diff if stat in LOWER_IS_BETTER else diff
        cls = "up" if good > 0.05 else "down" if good < -0.05 else "flat"
        arrow = "▲" if diff > 0.05 else "▼" if diff < -0.05 else "•"
        cards.append(
            f'<div class="card"><div class="card-label">{html.escape(stat)}</div>'
            f'<div class="card-value">{opp_avg:.1f}</div>'
            f'<span class="delta {cls}">{arrow} {diff:+.1f} vs avg</span></div>'
        )
    st.markdown(f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)


def hit_card(title, series, line):
    played = series.dropna()
    if played.empty:
        body, pct = '<div class="card-value">—</div><div class="muted">No games</div>', 0
    else:
        overs = int((played > line).sum())
        pct = overs / len(played)
        body = f'<div class="card-value">{pct:.0%}</div><div class="muted">{overs} of {len(played)} over</div>'
    return (f'<div class="card"><div class="card-label">{html.escape(title)}</div>{body}'
            f'<div class="bar"><div style="width:{pct * 100:.0f}%"></div></div></div>')


def status_class(status):
    s = status.lower().strip()
    out_words = ("out", "reserve", "pup", "physically unable", "suspen", "non-football", "nfi", "inactive")
    if any(w in s for w in out_words) or s in ("ir", "ir-r", "o"):
        return "s-out"
    if "doubt" in s:
        return "s-doubt"
    if "question" in s or "day" in s:
        return "s-q"
    return "s-ok"


def _norm(name):
    """'Jacory Croskey-Merritt' == 'jacory croskey merritt', and 'Atlético' == 'Atletico'."""
    plain = unicodedata.normalize("NFKD", name or "")
    return "".join(ch for ch in plain.lower() if ch.isalnum() and not unicodedata.combining(ch))


def injured_keys(rows, out):
    """IDs and normalized names of players who are Out (out=True) or Q/Doubtful/Probable (out=False)."""
    keys = set()
    for r in rows:
        if (status_class(r["status"]) == "s-out") == out:
            keys.update(k for k in (r.get("id"), "n:" + _norm(r["player"])) if k and k != "n:")
    return keys


def is_listed(p, keys):
    return p.get("id") in keys or ("n:" + _norm(p.get("name", ""))) in keys


def injury_list(team, rows):
    parts = [f'<div class="inj-team">{html.escape(team["name"])}</div>']
    if not rows:
        parts.append('<div class="muted">No reported injuries ✅</div>')
    for r in rows:
        parts.append(
            f'<div class="inj"><div><div class="inj-name">{html.escape(r["player"])}</div>'
            f'<div class="inj-note">{html.escape(r["note"])}</div></div>'
            f'<span class="status {status_class(r["status"])}">{html.escape(r["status"])}</span></div>'
        )
    st.markdown("".join(parts), unsafe_allow_html=True)


def hero(player, my_team, opponent, games_vs, cfg, sub=None):
    c1, c2 = my_team["color"], opponent["color"]
    pos = f'{player["pos"]} · ' if player["pos"] else ""
    if sub is None:
        sub = f'{games_vs} games in the last {cfg["seasons_back"]} seasons'
    st.markdown(
        f'<div class="hero" style="background:linear-gradient(110deg,#{c1} 0%,#{c1} 40%,#{c2} 60%,#{c2} 100%)">'
        f'<div class="hero-side"><div class="hero-abbr">{html.escape(my_team["abbr"])}</div>'
        f'<div class="hero-name">{html.escape(player["name"])}</div>'
        f'<div class="hero-sub">{html.escape(pos + my_team["name"])}</div></div>'
        f'<div class="hero-vs">VS</div>'
        f'<div class="hero-side right"><div class="hero-abbr">{html.escape(opponent["abbr"])}</div>'
        f'<div class="hero-name">{html.escape(opponent["name"])}</div>'
        f'<div class="hero-sub">{html.escape(sub)}</div></div></div>',
        unsafe_allow_html=True,
    )


# ---------- PLAYER LOOKUP PAGE ----------
def render_player(sport_name):
    cfg = SPORTS[sport_name]
    key = cfg["key"]

    try:
        teams = load_teams(cfg["site"])
        players = load_players(cfg["site"], teams)
    except Exception as e:
        st.error(f"Couldn't reach ESPN right now. Try refreshing in a minute. ({e})")
        return
    team_by_id = {t["id"]: t for t in teams}

    col1, col2 = st.columns(2)
    with col1:
        player = st.selectbox(
            "Player", players, index=None, key=f"{key}_player",
            placeholder="Start typing a name",
            format_func=lambda p: f"{p['name']} ({p['team_abbr']}{', ' + p['pos'] if p['pos'] else ''})",
        )

    upcoming = None  # the player's next game, used to pre select the opponent
    if player:
        try:
            upcoming = load_next_game(cfg["site"], player["team_id"])
        except Exception:
            upcoming = None

    with col2:
        default_index = None
        if upcoming:
            default_index = next((i for i, t in enumerate(teams) if t["id"] == upcoming["opp_id"]), None)
        opponent = st.selectbox(
            "Opponent", teams, index=default_index, placeholder="Pick a team",
            format_func=lambda t: t["name"],
            key=f"{key}_opp_{player['id'] if player else 'none'}",
        )

    if not player or not opponent:
        st.markdown('<div class="muted">Pick a player and an opponent to get started.</div>', unsafe_allow_html=True)
        return
    if opponent["id"] == player["team_id"]:
        st.warning("That's the player's own team. Pick a different opponent.")
        return

    with st.spinner("Pulling game logs from ESPN..."):
        history = load_history(sport_name, player["id"])
    if history.empty:
        st.warning("No game log found for this player yet.")
        return

    my_team = team_by_id.get(player["team_id"], {"id": player["team_id"], "name": player["team_abbr"],
                                                  "abbr": player["team_abbr"], "color": "334155"})
    vs_opp = history[history["opp_id"] == opponent["id"]]
    stats = pick_stats(cfg, player["pos"], history)

    hero(player, my_team, opponent, len(vs_opp), cfg)
    if upcoming:
        st.markdown(f'<span class="pill-game">🗓️ {cfg["today_word"]}: <b>{html.escape(upcoming["label"])}</b> · '
                    f'{html.escape(upcoming["status"])}</span>', unsafe_allow_html=True)
    insights_box = st.empty()  # filled in further down, once the defense numbers are loaded

    # Game environment (shootout or slog?)
    env_line = ""
    try:
        event_id = upcoming["id"] if upcoming and upcoming.get("opp_id") == opponent["id"] else None
        env = game_environment(cfg, sport_name, my_team, opponent, event_id)
    except Exception:
        env = None
    if env:
        section("Game environment", "spread, total and projected score")
        lbl, cls = env["label"]
        badge = f'<div style="margin-bottom:.5rem"><span class="grade {cls}">{lbl}</span></div>' if lbl else ""
        st.markdown(f'{badge}<div class="cards">{env_cards(env, my_team, opponent)}</div>', unsafe_allow_html=True)
        if lbl in ("HIGH SCORING", "LOW SCORING") and env["league"]:
            src = (f'O/U {env["odds"]["total"]:.1f}' if (env["odds"] or {}).get("total")
                   else f'projected total {env["proj_total"]:.0f}')
            env_line = (f'<div class="ins-line">{"🔥" if lbl == "HIGH SCORING" else "🧊"} {lbl.title()} game: '
                        f'{src} vs league average {env["league"]:.1f}. '
                        f'{"Good for everyone&#39;s numbers." if lbl == "HIGH SCORING" else "Expect fewer chances to go around."}</div>')

    # Averages vs this opponent
    section(f"Averages vs {opponent['abbr']}", f"compared to his average across all {len(history)} games")
    if vs_opp.empty:
        st.markdown('<div class="muted">No games against this team in the time window.</div>', unsafe_allow_html=True)
    else:
        stat_cards(vs_opp, history, stats)

    # Usage trends (is his role growing?)
    usage_html = ""
    try:
        useason, umetrics = usage_frame(cfg, history, player)
    except Exception:
        useason, umetrics = pd.DataFrame(), []
    if umetrics:
        section("Usage trends", f"last {USAGE_RECENT} games vs this season")
        cards_html, trends = usage_cards(useason, umetrics)
        st.markdown(f'<div class="cards">{cards_html}</div>', unsafe_allow_html=True)
        chart_metric = next((m for m in umetrics if "SHARE" in m), umetrics[0])
        st.line_chart(useason.set_index("Date")[[chart_metric]], height=220, color=THEMES[key]["primary"])
        usage_html = usage_line(player, trends)

    # Defense vs position
    slots = list(NFL_SLOTS) if key == "nfl" else list(NBA_GROUPS)
    slot_default = default_slot(cfg, player["pos"])
    section(f"{opponent['abbr']} defense vs position",
            f"what {opponent['abbr']} allowed in its last {DVP_GAMES[key]} games")
    slot = st.segmented_control("Position", slots, default=slot_default, label_visibility="collapsed",
                                key=f"{key}_slot_{player['id']}_{opponent['id']}") or slot_default
    pos_by_id = {p["id"]: p["pos"] for p in players}
    with st.spinner(f"Reading {opponent['abbr']} box scores..."):
        dvp = defense_vs_position(cfg, opponent["id"], slot, pos_by_id, team_by_id)
    if key == "nfl":
        dvp_stats = NFL_SLOT_STATS[NFL_SLOTS[slot][0]]
        who = f"{slot}s"
    else:
        dvp_stats = NBA_GROUP_STATS
        who = slot.lower()
    dvp_stats = [s for s in dvp_stats if not dvp.empty and s in dvp.columns]
    if dvp.empty or not dvp_stats:
        st.markdown('<div class="muted">No box score data for this defense yet.</div>', unsafe_allow_html=True)
    else:
        cards = []
        for s in dvp_stats:
            mine = (f'<div class="muted">{html.escape(short_name(player["name"]))} avg: {history[s].mean():.1f}</div>'
                    if s in history.columns else "")
            cards.append(f'<div class="card"><div class="card-label">{html.escape(s)} allowed</div>'
                         f'<div class="card-value">{dvp[s].mean():.1f}</div>{mine}</div>')
        unit = "player games" if key == "nba" else "games"
        st.markdown(f'<div class="muted" style="margin-bottom:.5rem">Average per {who[:-1] if who.endswith("s") else who} '
                    f'vs {opponent["abbr"]} · {len(dvp)} {unit}</div>'
                    f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)
        with st.expander(f"Game by game: {who} vs {opponent['abbr']}"):
            st.dataframe(dvp[["Date", "Team", "Player", "pos"] + dvp_stats].rename(columns={"pos": "Pos"}),
                         hide_index=True, width="stretch")

    # Matchup insights (shown at the top of the page)
    try:
        injuries = load_injuries(cfg["site"])
    except Exception:
        injuries = {}
    team_inj = injuries.get(my_team["id"]) or injuries.get(my_team["name"]) or []
    out_names = injured_keys(team_inj, out=True)
    questionable = injured_keys(team_inj, out=False)  # Q, Doubtful, Probable
    dvp_cache = {slot: dvp}

    def dvp_for_slot(s):
        if s not in dvp_cache:
            dvp_cache[s] = defense_vs_position(cfg, opponent["id"], s, pos_by_id, team_by_id)
        return dvp_cache[s]

    # The other side of the game: the opponent's players vs OUR player's team defense
    opp_inj = injuries.get(opponent["id"]) or injuries.get(opponent["name"]) or []
    opp_out = injured_keys(opp_inj, out=True)
    opp_maybe = frozenset(injured_keys(opp_inj, out=False))
    my_def_cache = {}

    def my_def_for_slot(s):
        if s not in my_def_cache:
            my_def_cache[s] = defense_vs_position(cfg, my_team["id"], s, pos_by_id, team_by_id)
        return my_def_cache[s]

    with st.spinner("Writing matchup insights..."):
        try:
            my_items = score_matchups(cfg, teammate_slots(sport_name, players, player["team_id"], out_names,
                                                          frozenset(questionable)),
                                      dvp_for_slot)
            for r in my_items:
                r["defense_abbr"] = opponent["abbr"]
            opp_items = score_matchups(cfg, teammate_slots(sport_name, players, opponent["id"], opp_out, opp_maybe),
                                       my_def_for_slot)
            for r in opp_items:
                r["defense_abbr"] = my_team["abbr"]
            star_pool = [r for r in my_items + opp_items
                         if (key == "nba" or r["slot"] in STAR_SLOTS) and r["avg"] >= STAR_MIN.get(r["stat"], 0)]
            star = max(star_pool, key=lambda r: r["edge"]) if star_pool else None
            insight_html = build_insights(cfg, player, opponent, my_items, questionable,
                                          star_card(cfg, star, team_by_id), env_line + usage_html)
        except Exception:
            insight_html = ""
    if insight_html:
        insights_box.markdown(insight_html, unsafe_allow_html=True)

    # Line checker
    section("Line checker", "how often he went over a number")
    c1, c2 = st.columns(2)
    line_stat = c1.selectbox("Stat", stats, key=f"{key}_line_stat")
    # Default line = his average rounded to the nearest .5 (like a sportsbook), e.g. 5.3 rec -> 5.5, TDs -> 0.5
    avg_recent = history.head(cfg["recent_games"] * 2)[line_stat].mean()
    suggested = float(int(avg_recent)) + 0.5 if pd.notna(avg_recent) and avg_recent >= 1 else 0.5
    if "TD" in line_stat or line_stat == "INT":  # touchdown and interception props are almost always 0.5
        suggested = 0.5
    line_value = c2.number_input("Line", min_value=0.0, value=suggested, step=0.5,
                                 key=f"{key}_line_{player['id']}_{line_stat}")
    this_season = history[history["Season"] == history["Season"].iloc[0]]
    n = cfg["recent_games"]
    st.markdown(
        '<div class="cards">'
        + hit_card(f"vs {opponent['abbr']}", vs_opp[line_stat], line_value)
        + hit_card(f"Last {n}", history.head(n)[line_stat], line_value)
        + hit_card("This season", this_season[line_stat], line_value)
        + hit_card("All games", history[line_stat], line_value)
        + (hit_card(f"{who} vs {opponent['abbr']}", dvp[line_stat], line_value)
           if not dvp.empty and line_stat in dvp.columns else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    # Game tables
    table_cols = META_COLS + stats
    section(f"Every game vs {opponent['abbr']}")
    if not vs_opp.empty:
        st.dataframe(vs_opp[table_cols], hide_index=True, width="stretch")
    section(f"Last {n} games", "any opponent")
    st.dataframe(history.head(n)[table_cols], hide_index=True, width="stretch")

    # Injuries
    section("Injury report", "refreshes every 15 minutes")
    try:
        injuries = load_injuries(cfg["site"])
        for col, team in zip(st.columns(2), [my_team, opponent]):
            with col:
                injury_list(team, injuries.get(team["id"]) or injuries.get(team["name"]) or [])
    except Exception:
        st.warning("Injury report is unavailable right now.")


# ---------- SLATE PAGE ----------
ET = ZoneInfo("America/New_York")


def _local(iso):
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    except (ValueError, AttributeError):
        return None


def _safe_next(site, team_id):
    try:
        return load_next_game(site, team_id)
    except Exception:
        return None


def slate_games(cfg, teams):
    """The next slate: today's (or the next) NBA games, or this NFL week's games."""
    with ThreadPoolExecutor(max_workers=10) as pool:
        nexts = list(pool.map(lambda t: _safe_next(cfg["site"], t["id"]), teams))
    games = {g["id"]: g for g in nexts if g and g.get("id") and g.get("home") and g.get("away") and _local(g["date"])}
    ordered = sorted(games.values(), key=lambda g: g["date"])
    if not ordered:
        return []
    first = _local(ordered[0]["date"])
    if cfg["key"] == "nba":
        return [g for g in ordered if _local(g["date"]).date() == first.date()]
    return [g for g in ordered if (_local(g["date"]) - first).days < 6]  # Thursday through Monday


@st.cache_data(ttl=3600, show_spinner=False)
def slate_watchlist(sport_name, games_key):
    """Every player on the slate with his matchup edge (cached for an hour)."""
    cfg = SPORTS[sport_name]
    teams = load_teams(cfg["site"])
    players = load_players(cfg["site"], teams)
    team_by_id = {t["id"]: t for t in teams}
    pos_by_id = {p["id"]: p["pos"] for p in players}
    try:
        injuries = load_injuries(cfg["site"])
    except Exception:
        injuries = {}
    out = []
    for event_id, home, away, label in games_key:
        for team_id, opp_id in ((home, away), (away, home)):
            team, opp = team_by_id.get(team_id), team_by_id.get(opp_id)
            if not team or not opp:
                continue
            inj = injuries.get(team_id) or injuries.get(team["name"]) or []
            out_names = injured_keys(inj, out=True)
            maybe = frozenset(injured_keys(inj, out=False))
            memo = {}

            def dvp_fn(s, opp_id=opp_id, memo=memo):
                if s not in memo:
                    memo[s] = defense_vs_position(cfg, opp_id, s, pos_by_id, team_by_id)
                return memo[s]

            try:
                rows = teammate_slots(sport_name, players, team_id, out_names, maybe)
                items = score_matchups(cfg, rows, dvp_fn)
            except Exception:
                continue
            for r in items:
                out.append({"name": r["name"], "slot": r["slot"], "stat": r["stat"], "avg": r["avg"],
                            "allowed": r["allowed"], "edge": r["edge"], "returning": r.get("returning", False),
                            "questionable": is_listed(r, maybe), "team_abbr": team["abbr"],
                            "defense_abbr": opp["abbr"], "game": label, "event_id": event_id})
    return out


def render_slate(sport_name):
    cfg = SPORTS[sport_name]
    key = cfg["key"]
    nba = key == "nba"
    try:
        teams = load_teams(cfg["site"])
    except Exception as e:
        st.error(f"Couldn't reach ESPN right now. Try refreshing in a minute. ({e})")
        return
    team_by_id = {t["id"]: t for t in teams}

    with st.spinner("Loading the schedule..."):
        games = slate_games(cfg, teams)
    if not games:
        st.markdown('<div class="muted">No upcoming games found.</div>', unsafe_allow_html=True)
        return
    open_id = st.session_state.get(f"{key}_game")
    if open_id:
        chosen = next((x for x in games if x["id"] == open_id), None)
        if chosen:
            render_game(sport_name, chosen)
            return
        st.session_state.pop(f"{key}_game", None)

    first = _local(games[0]["date"])
    title = f'{first.strftime("%A, %b")} {first.day}' if nba else f'Week of {first.strftime("%b")} {first.day}'
    section(f"Slate · {title}", f"{len(games)} games")
    st.caption("Tap 📊 Game insights on any game to simulate it 10,000 times.")

    go_key = f"{key}_slate_go"
    if not st.session_state.get(go_key):
        if st.button("⭐ Find the best matchups on this slate", key=f"{key}_slate_btn", type="primary"):
            st.session_state[go_key] = True
        st.caption("The first run takes a minute or two (it reads every team's box scores). "
                   "After that it's instant for the next hour.")

    best_by_game = {}
    if st.session_state.get(go_key):
        games_key = tuple((g["id"], g["home"], g["away"], g["label"]) for g in games)
        with st.spinner("Analyzing every matchup on the slate..."):
            try:
                items = slate_watchlist(sport_name, games_key)
            except Exception:
                items = []
        pool = sorted([r for r in items if (nba or r["slot"] in STAR_SLOTS)
                       and r["avg"] >= STAR_MIN.get(r["stat"], 0) and r["edge"] >= EDGE],
                      key=lambda r: r["edge"], reverse=True)
        for r in pool:
            best_by_game.setdefault(r["event_id"], r)
        section("Top plays on the slate", "biggest matchup edges on paper")
        if not pool:
            st.markdown('<div class="muted">No big edges on this slate.</div>', unsafe_allow_html=True)
        rows = []
        for i, r in enumerate(pool[:10], 1):
            q = " <span class='status s-q'>Q</span>" if r["questionable"] or r["returning"] else ""
            role = f"starting {r['slot'].lower()}" if nba else f"{r['slot']}s"
            rows.append(
                f'<div class="sneaky"><div><b>{i}. {html.escape(r["name"])}</b> '
                f'<span class="muted">{html.escape(r["team_abbr"])} · {r["slot"]} · {html.escape(r["game"])}</span>{q}<br>'
                f'<span class="muted">{html.escape(r["defense_abbr"])} allows {r["allowed"]:.1f} '
                f'{STAT_WORDS.get(r["stat"], r["stat"])} to {role} vs his {r["avg"]:.1f} avg</span></div>'
                f'<span class="edge">+{r["edge"]:.0%}</span></div>')
        st.markdown("".join(rows), unsafe_allow_html=True)

    section("Games", "spread, total, and scoring outlook")

    def card(g):
        home, away = team_by_id.get(g["home"]), team_by_id.get(g["away"])
        if not home or not away:
            return ""
        try:
            env = game_environment(cfg, sport_name, home, away, g["id"])
        except Exception:
            env = None
        when = _local(g["date"])
        when_txt = f'{when.strftime("%a")} {when.strftime("%I:%M %p").lstrip("0")} ET' if when else ""
        meta = []
        if env and env["odds"]:
            if env["odds"].get("details"):
                meta.append(html.escape(env["odds"]["details"]))
            if env["odds"].get("total"):
                meta.append(f'O/U {env["odds"]["total"]:.1f}')
        if env:
            meta.append(f'Proj {away["abbr"]} {env["proj_opp"]:.0f} · {home["abbr"]} {env["proj_team"]:.0f}')
        badge = f'<span class="grade {env["label"][1]}">{env["label"][0]}</span>' if env and env["label"][0] else ""
        star = best_by_game.get(g["id"])
        star_html = (f'<div class="game-star">⭐ {html.escape(star["name"])} ({html.escape(star["team_abbr"])} '
                     f'{star["slot"]}) +{star["edge"]:.0%}</div>') if star else ""
        return (f'<div class="game-card" style="border-top:5px solid #{home["color"]}">'
                f'<div class="game-teams">{html.escape(away["abbr"])} <span>@</span> {html.escape(home["abbr"])}</div>'
                f'<div class="muted">{when_txt}</div>{badge}'
                f'<div class="game-meta">{"<br>".join(meta)}</div>{star_html}</div>')

    with ThreadPoolExecutor(max_workers=8) as pool:
        cards = list(pool.map(card, games))
    per_row = 3
    for i in range(0, len(games), per_row):
        cols = st.columns(per_row)
        for col, g, c in zip(cols, games[i:i + per_row], cards[i:i + per_row]):
            with col:
                st.markdown(c, unsafe_allow_html=True)
                if st.button("📊 Game insights", key=f'{key}_open_{g["id"]}', width="stretch"):
                    st.session_state[f"{key}_game"] = g["id"]
                    st.rerun()


# ==========================================================
# GAME INSIGHTS: tap a game on the slate and the app plays it out 10,000 times
#
# For every player: his recent minutes / volume and production rates (this season),
#   x what tonight's defense allows to his position (pulled toward average when the sample is small)
#   x the game's pace (from the Vegas total).
# Each simulation draws a random but realistic stat line; 10,000 of them give a full range of outcomes.
# We compare against a "normal night" (same player, average opponent, his usual minutes),
# so a prop only shows up when tonight's numbers actually move it.
# ==========================================================
SIMS = 10_000
SHRINK_GAMES = {"nba": 8, "nfl": 5}          # a defense needs this many games before we fully trust it
FACTOR_CAP = {"nba": (0.85, 1.18), "nfl": (0.80, 1.25)}
TEAM_SD = {"nba": 12.0, "nfl": 10.0}         # how much a team's score swings game to game
TEAM_CORR = {"nba": 0.30, "nfl": 0.15}       # fast games lift both teams
PROP_MIN_MOVE = 0.04                         # a prop needs to move 4+ points of probability to be listed

NBA_SIM_STATS = ["PTS", "REB", "AST", "3PM"]
NBA_DISPERSION = {"PTS": 2.2, "REB": 1.3, "AST": 1.4, "3PM": 1.25}   # how streaky each stat is
NBA_PACE_WEIGHT = {"PTS": 1.0, "3PM": 1.0, "AST": 0.7, "REB": 0.4}
NBA_PROP_MIN = {"PTS": 8, "REB": 3, "AST": 2, "3PM": 0.8, "PRA": 15}
NBA_SIM_MIN_MINUTES = 14
NBA_GROUP_OF = {p: g for g, members in NBA_GROUPS.items() for p in members}

NFL_SIM_STATS = {
    "QB": ["PASS YDS", "PASS TD", "INT", "RUSH YDS"],
    "RB": ["RUSH YDS", "REC", "REC YDS", "RUSH+REC YDS"],
    "WR": ["REC", "REC YDS"],
    "TE": ["REC", "REC YDS"],
}
NFL_MAIN = {"QB": "PASS YDS", "RB": "RUSH+REC YDS", "WR": "REC YDS", "TE": "REC YDS"}
NFL_COUNT_STATS = {"REC", "CAR", "PASS TD", "INT"}
NFL_TD_PRIOR = {"QB1": 0.15, "RB1": 0.55, "RB2": 0.25, "WR1": 0.45, "WR2": 0.30, "WR3": 0.18, "TE1": 0.30}
NFL_PROP_MIN = {"PASS YDS": 150, "RUSH YDS": 20, "REC": 2, "REC YDS": 20, "RUSH+REC YDS": 30, "PASS TD": 0.5, "INT": 0.3}


def _seed(text):
    return sum((i + 1) * ord(c) for i, c in enumerate(str(text))) % (2 ** 32)


def _shrink(raw, n, sport_key):
    """Pull a matchup number toward 1.0 (average) when it's built on few games, then cap it."""
    if raw is None or not np.isfinite(raw) or raw <= 0:
        return 1.0
    k = SHRINK_GAMES[sport_key]
    lo, hi = FACTOR_CAP[sport_key]
    return float(min(hi, max(lo, 1 + (raw - 1) * n / (n + k))))


def _nb(rng, lam, disp):
    """Counts with realistic streakiness (a negative binomial)."""
    lam = np.maximum(np.asarray(lam, dtype=float), 1e-9)
    if disp <= 1.01:
        return rng.poisson(lam)
    return rng.poisson(rng.gamma(lam / (disp - 1), disp - 1))


def _gamma(rng, mean, cv, size):
    """Yardage: always positive, with the occasional big game."""
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-6)
    shape = 1 / cv ** 2
    return rng.gamma(shape, mean / shape, size)


def book_line(avg, stat=""):
    """Where a sportsbook would likely hang the line: his average, at the nearest .5."""
    if stat in ("PASS TD",):
        return 1.5 if avg >= 1.3 else 0.5
    if stat == "INT" or avg < 1:
        return 0.5
    return float(int(avg)) + 0.5


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ---------- box scores for both teams (defense vs position + league baseline) ----------
def team_box_rows(cfg, team_id, n_games, pos_by_id):
    """Every player line from a team's last n games (both sides). This season counts full, last season half."""
    current = cfg["season_fn"]()
    games = []
    for season in (current, current - 1):
        try:
            got = load_completed_games(cfg["site"], team_id, season)
        except Exception:
            got = []
        games += [{**g, "weight": 1.0 if season == current else 0.5} for g in got]
        if len(games) >= n_games:
            break
    games = list({g["id"]: g for g in games}.values())[:n_games]

    def one(g):
        try:
            return g, load_boxscore(cfg["site"], g["id"])
        except Exception:
            return g, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, games))
    frames = []
    for g, box in results:
        if not box:
            continue
        df = _clean_box(pd.DataFrame(box), cfg["key"])
        df["pos"] = [p or pos_by_id.get(pid, "") for p, pid in zip(df["pos"], df["pid"])]
        df["game_id"], df["weight"] = g["id"], g["weight"]
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _games_seen(rows):
    return float(rows.drop_duplicates("game_id")["weight"].sum()) if not rows.empty else 0.0


def nba_defense_table(pool, def_rows, def_id):
    """{group: {stat: factor}}: what this defense allows per minute to guards / forwards / centers vs average."""
    def rates(df):
        d = df[(df.get("MIN", pd.Series(dtype=float)).fillna(0) >= NBA_MIN_MINUTES)].copy()
        d["group"] = d["pos"].map(NBA_GROUP_OF)
        out = {}
        for grp, gdf in d.dropna(subset=["group"]).groupby("group"):
            mins = gdf["MIN"].sum()
            out[grp] = {s: gdf[s].fillna(0).sum() / mins for s in NBA_SIM_STATS if s in gdf and mins}
        return out
    if pool.empty or def_rows.empty:
        return {}, 0
    base = rates(pool)
    facing = def_rows[def_rows["team_id"] != def_id]
    n = _games_seen(facing)
    allowed = rates(facing)
    table = {}
    for grp in NBA_GROUPS:
        table[grp] = {s: _shrink(allowed.get(grp, {}).get(s, 0) / base[grp][s], n, "nba")
                      if base.get(grp, {}).get(s) else 1.0 for s in NBA_SIM_STATS}
    return table, n


def nfl_slot_lines(df):
    """Who was each team's QB1, RB1, WR1... in each game, and what they did."""
    out = []
    if df.empty:
        return pd.DataFrame()
    for (gid, tid), gdf in df.groupby(["game_id", "team_id"]):
        for slot, (pos, rank_stat, nth) in NFL_SLOTS.items():
            at = gdf[gdf["pos"] == pos]
            if rank_stat not in at or len(at) < nth:
                continue
            r = at.sort_values(rank_stat, ascending=False).iloc[nth - 1]
            out.append({"game_id": gid, "team_id": tid, "slot": slot, "weight": r["weight"],
                        "main": r.get(NFL_MAIN[pos], np.nan)})
    return pd.DataFrame(out)


def nfl_defense_table(pool, def_rows, def_id):
    """{slot: (factor, allowed per game, league per game)} for QB1, RB1, WR1 ... vs this defense."""
    base = nfl_slot_lines(pool)
    facing = nfl_slot_lines(def_rows)
    if base.empty or facing.empty:
        return {}, 0
    facing = facing[facing["team_id"] != def_id]
    n = float(facing.drop_duplicates("game_id")["weight"].sum())
    table = {}
    for slot in NFL_SLOTS:
        b = base[base["slot"] == slot]["main"].mean()
        a = facing[facing["slot"] == slot]["main"].mean()
        table[slot] = (_shrink(a / b if b else None, n, "nfl"), a, b)
    return table, n


# ---------- players ----------
def _player_sample(cfg, p, team_id, n):
    """His last n games THIS season with his current team, plus last season as a light prior."""
    current = cfg["season_fn"]()
    cur = load_gamelog(cfg["web"], p["id"], current, cfg["season_label"](current), cfg["use_names"])
    prev = load_gamelog(cfg["web"], p["id"], current - 1, cfg["season_label"](current - 1), cfg["use_names"])
    cur = clean_numbers(cur, cfg["key"]) if not cur.empty else cur
    prev = clean_numbers(prev, cfg["key"]) if not prev.empty else prev
    if not cur.empty and "team_id" in cur and cur["team_id"].astype(bool).any():
        cur = cur[cur["team_id"] == team_id]  # traded? only count games for this team
    if not cur.empty:
        cur = cur.sort_values("Date", ascending=False).head(n)
    if not prev.empty:
        prev = prev.sort_values("Date", ascending=False)
    return cur, prev


def _blend_rate(cur_sum, cur_den, prev_sum, prev_den, prior_weight):
    """This season's rate, steadied by last season's (worth `prior_weight` games) when we have it."""
    if prev_den and prev_den > 0 and prior_weight:
        return (cur_sum + prev_sum / prev_den * prior_weight) / (cur_den + prior_weight)
    return cur_sum / cur_den if cur_den else 0.0


def _confidence(n_player, n_def, sport_key):
    hi, mid = ((8, 10), (4, 5)) if sport_key == "nba" else ((6, 6), (3, 4))
    if n_player >= hi[0] and n_def >= hi[1]:
        return "High"
    if n_player >= mid[0] and n_def >= mid[1]:
        return "Medium"
    return "Low"


def nba_players(cfg, team, players, injuries, rng, defense, n_def, pace):
    inj = injuries.get(team["id"]) or injuries.get(team["name"]) or []
    out_keys, maybe_keys = injured_keys(inj, out=True), injured_keys(inj, out=False)
    roster = [p for p in players if p["team_id"] == team["id"] and not is_listed(p, out_keys)
              and status_class(p.get("inj", "")) != "s-out"]
    try:  # must have played in one of the team's last 3 games (catches injuries the feed missed)
        recent_team = load_completed_games(cfg["site"], team["id"], cfg["season_fn"]())
        cutoff = recent_team[min(2, len(recent_team) - 1)]["date"] if recent_team else ""
    except Exception:
        cutoff = ""
    with ThreadPoolExecutor(max_workers=8) as pool:
        samples = list(pool.map(lambda p: (p, *_player_sample(cfg, p, team["id"], 10)), roster))
    results = []
    for p, cur, prev in samples:
        if cur.empty or "MIN" not in cur:
            continue
        cur = cur[cur["MIN"].fillna(0) > 0]
        if cur.empty:
            continue
        questionable = is_listed(p, maybe_keys)
        if cutoff and cur["Date"].max() < cutoff and not questionable:
            continue
        mins_recent, mins_normal = cur.head(5)["MIN"].mean(), cur["MIN"].mean()
        if mins_recent < NBA_SIM_MIN_MINUTES:
            continue
        mins_sd = max(3.5, float(cur["MIN"].std(ddof=0) or 0))
        prev_ok = not prev.empty and "MIN" in prev and (prev["MIN"].fillna(0) > 0).sum() >= 10
        group = NBA_GROUP_OF.get(p["pos"], "Forwards")
        mins = np.clip(rng.normal(mins_recent, mins_sd, SIMS), 0, 46)
        mins_n = np.clip(rng.normal(mins_normal, mins_sd, SIMS), 0, 46)
        sim, norm, rates = {}, {}, {}
        for s in NBA_SIM_STATS:
            if s not in cur:
                continue
            rate = _blend_rate(cur[s].fillna(0).sum(), cur["MIN"].sum(),
                               prev[s].fillna(0).sum() if prev_ok and s in prev else 0,
                               prev["MIN"].fillna(0).sum() if prev_ok else 0, 5 * mins_normal if prev_ok else 0)
            rates[s] = rate
            f_def = defense.get(group, {}).get(s, 1.0)
            f_pace = 1 + NBA_PACE_WEIGHT[s] * (pace - 1)
            sim[s] = _nb(rng, rate * mins * f_def * f_pace, NBA_DISPERSION[s])
            norm[s] = _nb(rng, rate * mins_n, NBA_DISPERSION[s])
        if not {"PTS", "REB", "AST"} <= set(sim):
            continue
        sim["PRA"] = sim["PTS"] + sim["REB"] + sim["AST"]
        norm["PRA"] = norm["PTS"] + norm["REB"] + norm["AST"]
        avgs = {s: float(cur[s].mean()) for s in NBA_SIM_STATS if s in cur}
        avgs["PRA"] = float((cur["PTS"] + cur["REB"] + cur["AST"]).mean())
        props = []
        for s in NBA_SIM_STATS + ["PRA"]:
            if s in sim and avgs.get(s, 0) >= NBA_PROP_MIN[s]:
                line = book_line(avgs[s], s)
                props.append({"stat": s, "line": line, "p": float((sim[s] > line).mean()),
                              "p_norm": float((norm[s] > line).mean()), "avg": avgs[s]})
        results.append({
            "name": p["name"], "team": team["abbr"], "role": f'{p["pos"]} · {group[:-1]}' if p["pos"] else group[:-1],
            "group": group,
            "questionable": questionable, "games": len(cur), "conf": _confidence(len(cur), n_def, "nba"),
            "mins_recent": mins_recent, "mins_normal": mins_normal,
            "fgm_rate": float(cur["FGM"].fillna(0).sum() / cur["MIN"].sum()) if "FGM" in cur and cur["MIN"].sum()
                        else float(cur["PTS"].sum() / cur["MIN"].sum() / 2.3),
            "proj": {s: float(np.median(v)) if s != "PRA" else float(v.mean()) for s, v in sim.items()},
            "mean": {s: float(v.mean()) for s, v in sim.items()}, "normal": {s: float(v.mean()) for s, v in norm.items()},
            "f_def": defense.get(group, {}), "pace": pace, "props": props, "main": "PRA",
        })
    return results


def nfl_players(cfg, sport_name, team, players, injuries, rng, defense, n_def, env_factor):
    inj = injuries.get(team["id"]) or injuries.get(team["name"]) or []
    out_keys, maybe_keys = injured_keys(inj, out=True), injured_keys(inj, out=False)
    try:
        roles = teammate_slots(sport_name, players, team["id"], out_keys, frozenset(maybe_keys))
    except Exception:
        roles = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        samples = list(pool.map(lambda r: (r, *_player_sample(cfg, r, team["id"], 8)), roles))
    results = []
    for r, cur, prev in samples:
        pos, slot = r["pos"], r["slot"]
        sample = cur if len(cur) >= 2 else prev.head(8) if not prev.empty else cur
        if sample.empty:
            continue
        n = len(cur)
        f_slot = defense.get(slot, (1.0, None, None))[0]
        stats = [s for s in NFL_SIM_STATS.get(pos, []) if s in sample and sample[s].notna().any()]
        sim, norm, props, avgs = {}, {}, [], {}
        for s in stats:
            vals = sample[s].fillna(0)
            avg = float(vals.mean())
            avgs[s] = avg
            if s in NFL_COUNT_STATS:
                w_def = 1.0 if s in ("PASS TD",) else 0.6
                lam = avg * (1 + w_def * (f_slot - 1)) * (1 + (1.0 if s == "PASS TD" else 0.3) * (env_factor - 1))
                if s == "INT":
                    lam = avg * (2 - f_slot) ** 0.5  # stingier pass defense = more picks
                sim[s] = _nb(rng, np.full(SIMS, lam), 1.3)
                norm[s] = _nb(rng, np.full(SIMS, avg), 1.3)
            else:
                cv = float(min(0.9, max(0.35, (vals.std(ddof=0) / avg) if avg else 0.6)))
                mean = avg * f_slot * (1 + 0.5 * (env_factor - 1))
                sim[s] = _gamma(rng, mean, cv, SIMS)
                norm[s] = _gamma(rng, avg, cv, SIMS)
            if avg >= NFL_PROP_MIN.get(s, 0):
                line = book_line(avg, s)
                props.append({"stat": s, "line": line, "p": float((sim[s] > line).mean()),
                              "p_norm": float((norm[s] > line).mean()), "avg": avg})
        # anytime touchdown (rushing or receiving)
        td_cols = [c for c in ("RUSH TD", "REC TD") if c in sample]
        td = None
        if pos != "QB" or "RUSH TD" in sample:
            tds = sum(sample[c].fillna(0).sum() for c in td_cols) if td_cols else 0
            prior = NFL_TD_PRIOR.get(slot, 0.2)
            td_rate = (tds + prior * 4) / (len(sample) + 4)  # his TD rate, steadied by a typical rate for his role
            td_sim = rng.poisson(td_rate * f_slot * env_factor, SIMS)   # touchdowns he scores in each simulated game
            td_norm = rng.poisson(td_rate, SIMS)
            td = {"p": float((td_sim >= 1).mean()), "p2": float((td_sim >= 2).mean()),
                  "p_norm": float((td_norm >= 1).mean()), "per_game": float(tds / len(sample)), "games": len(sample),
                  "lam": float(td_rate * f_slot * env_factor)}
            props.append({"stat": "ANYTIME TD", "line": None, "p": td["p"], "p_norm": td["p_norm"], "avg": td["per_game"]})
        main = NFL_MAIN.get(pos)
        if main not in sim:
            continue
        results.append({
            "name": r["name"], "team": team["abbr"], "role": slot, "slot": slot,
            "questionable": bool(r.get("returning")) or is_listed(r, maybe_keys),
            "games": n, "conf": _confidence(n, n_def, "nfl"),
            "proj": {s: float(np.median(v)) for s, v in sim.items()},
            "mean": {s: float(v.mean()) for s, v in sim.items()}, "normal": {s: float(v.mean()) for s, v in norm.items()},
            "f_slot": f_slot, "env": env_factor, "props": props, "main": main, "td": td,
        })
    return results


def _team_sims(rng, mean_h, mean_a, sport_key):
    sd, corr = TEAM_SD[sport_key], TEAM_CORR[sport_key]
    z0, z1 = rng.standard_normal(SIMS), rng.standard_normal(SIMS)
    h = mean_h + sd * z0
    a = mean_a + sd * (corr * z0 + math.sqrt(1 - corr ** 2) * z1)
    return h, a


# ---------- first touchdown / first basket ----------
NFL_TDS_PER_POINT = 0.105   # NFL teams score about 2.4 touchdowns for every 23 points
NBA_SCORE_CHANCE = 0.45     # chance a possession ends in a made field goal
NBA_BENCH_SHARE = 0.04      # small chance the first basket comes from someone off the bench


def american(p):
    """Fair odds for a probability (no sportsbook cut): 14% -> +614, 60% -> -150."""
    if p <= 0 or p >= 1:
        return ""
    return f"+{100 * (1 - p) / p:.0f}" if p < 0.5 else f"-{100 * p / (1 - p):.0f}"


def _first_row(name, team, p, player=None):
    return {"name": name, "team": team, "p": p, "role": (player or {}).get("role", ""),
            "questionable": bool((player or {}).get("questionable")), "conf": (player or {}).get("conf", "")}


def first_td_sims(rng, plist, exp_pts, home, away):
    """Every player scores touchdowns at his own rate through the game; whoever's first TD comes earliest wins."""
    cands = []
    for t in (home, away):
        mine = [p for p in plist if p["team"] == t["abbr"] and p.get("td")]
        lam_sum = sum(p["td"]["lam"] for p in mine)
        team_tds = (exp_pts.get(t["id"]) or 0) * NFL_TDS_PER_POINT or lam_sum * 1.3
        cands += [(p["name"], t["abbr"], p["td"]["lam"], p) for p in mine]
        cands.append((f"Any other {t['abbr']} player / defense", t["abbr"], max(0.25, team_tds - lam_sum), None))
    if not cands:
        return None
    rates = np.maximum(np.array([c[2] for c in cands]), 1e-6)
    times = rng.exponential(1 / rates[:, None], size=(len(cands), SIMS))  # when each one's first TD would come
    first, scored = times.argmin(axis=0), times.min(axis=0) < 1           # (1 = the full game)
    rows = [_first_row(c[0], c[1], float(((first == i) & scored).mean()), c[3]) for i, c in enumerate(cands)]
    team = {t["abbr"]: sum(r["p"] for r in rows if r["team"] == t["abbr"]) for t in (home, away)}
    return {"kind": "First touchdown", "rows": sorted(rows, key=lambda r: r["p"], reverse=True),
            "team": team, "none": float((~scored).mean())}


def first_basket_sims(rng, plist, exp_pts, home, away, league_total):
    """Coin flip for the tip, trade possessions until someone makes a shot, the starter who shoots it scores."""
    lineups, make = {}, {}
    for t in (home, away):
        starters = sorted([p for p in plist if p["team"] == t["abbr"]], key=lambda p: p["mins_recent"], reverse=True)[:5]
        if not starters:
            return None
        shares = np.array([max(p.get("fgm_rate", 0), 1e-4) for p in starters])
        shares = shares / shares.sum() * (1 - NBA_BENCH_SHARE)
        lineups[t["id"]] = (starters, np.append(shares, NBA_BENCH_SHARE))
        strength = (exp_pts.get(t["id"]) or 0) / (league_total / 2) if league_total else 1.0
        make[t["id"]] = float(min(0.52, max(0.38, NBA_SCORE_CHANCE * (strength or 1.0))))
    ph, pa = make[home["id"]], make[away["id"]]
    both_miss = (1 - ph) * (1 - pa)
    home_ball_first = rng.random(SIMS) < 0.5                                  # who wins the tip
    p_home_scores_first = np.where(home_ball_first, ph / (1 - both_miss), (1 - pa) * ph / (1 - both_miss))
    home_first = rng.random(SIMS) < p_home_scores_first
    rows = []
    for t, mask in ((home, home_first), (away, ~home_first)):
        starters, shares = lineups[t["id"]]
        pick = rng.choice(len(shares), size=int(mask.sum()), p=shares)
        for i, p in enumerate(starters):
            rows.append(_first_row(p["name"], t["abbr"], float((pick == i).sum() / SIMS), p))
        rows.append(_first_row(f"Any {t['abbr']} bench player", t["abbr"], float((pick == len(starters)).sum() / SIMS)))
    team = {home["abbr"]: float(home_first.mean()), away["abbr"]: float((~home_first).mean())}
    return {"kind": "First basket", "rows": sorted(rows, key=lambda r: r["p"], reverse=True), "team": team, "none": None}


@st.cache_data(ttl=30 * 60, show_spinner=False)
def simulate_game(sport_name, event_id, home_id, away_id):
    """Play the game out 10,000 times. Cached for 30 minutes per game."""
    cfg = SPORTS[sport_name]
    key = cfg["key"]
    nba = key == "nba"
    teams = load_teams(cfg["site"])
    players = load_players(cfg["site"], teams)
    team_by_id = {t["id"]: t for t in teams}
    home, away = team_by_id[home_id], team_by_id[away_id]
    pos_by_id = {p["id"]: p["pos"] for p in players}
    try:
        injuries = load_injuries(cfg["site"])
    except Exception:
        injuries = {}
    rng = np.random.default_rng(_seed(event_id))
    try:
        env = game_environment(cfg, sport_name, home, away, event_id)
    except Exception:
        env = None

    rows = {tid: team_box_rows(cfg, tid, DVP_GAMES[key], pos_by_id) for tid in (home_id, away_id)}
    frames = [r for r in rows.values() if not r.empty]
    pool = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["game_id", "pid"]) if frames else pd.DataFrame()
    build = nba_defense_table if nba else nfl_defense_table
    defense, n_def = {}, {}
    for tid in (home_id, away_id):
        defense[tid], n_def[tid] = build(pool, rows[tid], tid) if not pool.empty else ({}, 0)

    # expected points: half recent form, half the Vegas line (when there is one)
    exp_pts, model_pts = {}, {}
    for t, side in ((home, "proj_team"), (away, "proj_opp")):
        form_proj = env[side] if env else None
        implied = (env.get("implied") or {}).get(t["abbr"]) if env else None
        model_pts[t["id"]] = form_proj
        exp_pts[t["id"]] = (0.5 * form_proj + 0.5 * implied) if form_proj and implied else (form_proj or implied)
    pace = {}
    for t in (home, away):
        form = team_form(cfg, t["id"])
        raw = exp_pts[t["id"]] / form["for"] if form and exp_pts[t["id"]] and form["for"] else 1.0
        pace[t["id"]] = float(min(1.08, max(0.92, raw))) if nba else float(min(1.15, max(0.85, raw)))

    plist = []
    for t, opp in ((home, away), (away, home)):
        if nba:
            plist += nba_players(cfg, t, players, injuries, rng, defense[opp["id"]], n_def[opp["id"]], pace[t["id"]])
        else:
            plist += nfl_players(cfg, sport_name, t, players, injuries, rng, defense[opp["id"]], n_def[opp["id"]], pace[t["id"]])

    game = None
    if exp_pts[home_id] and exp_pts[away_id]:
        h, a = _team_sims(rng, exp_pts[home_id], exp_pts[away_id], key)
        game = {"home_pts": float(np.median(h)), "away_pts": float(np.median(a)), "home_win": float((h > a).mean()),
                "total": float(np.median(h + a)), "odds": (env or {}).get("odds")}
        odds = game["odds"] or {}
        if odds.get("total"):
            game["p_over"] = float((h + a > odds["total"]).mean())
        details = (odds.get("details") or "").split()
        try:
            fav, spread = details[0], abs(float(details[-1]))
            margin = (h - a) if fav == home["abbr"] else (a - h) if fav == away["abbr"] else None
            if margin is not None:
                game["fav"], game["spread"], game["p_cover"] = fav, spread, float((margin > spread).mean())
        except (ValueError, IndexError):
            pass
    league_total = (env or {}).get("league")
    first = (first_basket_sims(rng, plist, exp_pts, home, away, league_total) if nba
             else first_td_sims(rng, plist, exp_pts, home, away))
    return {"players": plist, "defense": defense, "n_def": n_def, "pace": pace, "env": env, "game": game,
            "home": home, "away": away, "first": first}


# ---------- NFL scheme data (nflverse: play by play + FTN charting, free) ----------
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download/{tag}/{name}"
ESPN_TO_NFLVERSE = {"WSH": "WAS", "LAR": "LA"}
PBP_COLS = ("game_id", "play_id", "posteam", "defteam", "season_type", "qb_dropback", "pass_attempt", "rush",
            "epa", "sack", "air_yards", "receiver_player_name", "pass_oe")
FTN_COLS = ("nflverse_game_id", "nflverse_play_id", "n_blitzers", "n_pass_rushers", "is_play_action",
            "is_motion", "is_screen_pass")


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def nflverse_parquet(tag, name, columns):
    r = requests.get(NFLVERSE.format(tag=tag, name=name), headers=HEADERS, timeout=90)
    r.raise_for_status()
    pf = pq.ParquetFile(io.BytesIO(r.content))
    wanted = [c for c in columns if c in pf.schema_arrow.names]  # only load the columns we use
    return pf.read(columns=wanted).to_pandas()


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def nfl_scheme_tables(season):
    """Team tendencies from every play this season: blitz rate, play action, EPA vs blitz, targets vs blitz..."""
    pbp = nflverse_parquet("pbp", f"play_by_play_{season}.parquet", PBP_COLS)
    try:
        ftn = nflverse_parquet("ftn_charting", f"ftn_charting_{season}.parquet", FTN_COLS)
        df = pbp.merge(ftn, left_on=["game_id", "play_id"], right_on=["nflverse_game_id", "nflverse_play_id"], how="left")
    except Exception:
        df = pbp
        for c in FTN_COLS[2:]:
            df[c] = np.nan
    df = df[(df["season_type"] == "REG") & df["posteam"].notna()].copy()
    db = df[df["qb_dropback"] == 1].copy()
    db["blitz"] = np.where(db["n_blitzers"].notna(), db["n_blitzers"] > 0, np.nan)
    db["deep"] = np.where(db["air_yards"].notna(), db["air_yards"] >= 20, np.nan)
    plays = df[(df["qb_dropback"] == 1) | (df["rush"] == 1)]

    def m(x):
        x = pd.to_numeric(x, errors="coerce")
        return float(x.mean()) if x.notna().any() else np.nan

    off, deff = {}, {}
    for team, g in db.groupby("posteam"):
        bl = g[g["blitz"] == 1]
        nb = g[g["blitz"] == 0]
        off[team] = {"epa_db": m(g["epa"]), "epa_blitz": m(bl["epa"]), "epa_noblitz": m(nb["epa"]),
                     "pa_rate": m(g["is_play_action"]), "deep_rate": m(g["deep"]), "sack_rate": m(g["sack"]),
                     "screen_rate": m(g["is_screen_pass"]), "blitzed_rate": m(g["blitz"])}
    for team, g in plays.groupby("posteam"):
        off.setdefault(team, {}).update({"motion_rate": m(g["is_motion"]), "proe": m(g["pass_oe"])})
    for team, g in db.groupby("defteam"):
        bl = g[g["blitz"] == 1]
        deff[team] = {"blitz_rate": m(g["blitz"]), "rushers": m(g["n_pass_rushers"]), "sack_rate": m(g["sack"]),
                      "epa_allowed": m(g["epa"]), "epa_vs_pa": m(g[g["is_play_action"] == 1]["epa"]),
                      "epa_vs_deep": m(g[g["deep"] == 1]["epa"]), "epa_blitzing": m(bl["epa"])}
    off_df, def_df = pd.DataFrame(off).T, pd.DataFrame(deff).T
    tg = db.dropna(subset=["receiver_player_name"])
    targets = tg.groupby(["posteam", "receiver_player_name"]).agg(
        targets=("epa", "size"), blitz_targets=("blitz", lambda x: float((x == 1).sum()))).reset_index()
    return off_df, def_df, targets


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def nfl_coverage(season):
    """Man vs zone and coverage shells by defense (this season if published, otherwise last season)."""
    for s in (season, season - 1):
        try:
            q = nflverse_parquet("pbp_participation", f"pbp_participation_{s}.parquet",
                                 ("nflverse_game_id", "possession_team", "defense_man_zone_type", "defense_coverage_type"))
        except Exception:
            continue
        q = q[q["defense_man_zone_type"].isin(["MAN_COVERAGE", "ZONE_COVERAGE"])].copy()
        if q.empty:
            continue
        parts = q["nflverse_game_id"].str.split("_")
        away, home = parts.str[2], parts.str[3]
        q["defteam"] = np.where(q["possession_team"] == home, away, home)
        prof = {}
        for team, g in q.groupby("defteam"):
            shells = g["defense_coverage_type"].value_counts(normalize=True)
            prof[team] = {"man": float((g["defense_man_zone_type"] == "MAN_COVERAGE").mean()),
                          "shells": [(k.replace("_", " ").title(), float(v)) for k, v in shells.head(2).items()]}
        return prof, s
    return {}, None


def _rank(df, col, team, high_is_1=True):
    if df is None or col not in df or team not in df.index or pd.isna(df.loc[team, col]):
        return None
    return int(df[col].rank(ascending=not high_is_1, method="min")[team])


def _pbp_name(full):
    """'Amon-Ra St. Brown' -> 'astbrown', matching play by play names like 'A.St. Brown'."""
    parts = [x for x in (full or "").replace(".", ". ").split() if _norm(x) not in ("jr", "sr", "ii", "iii", "iv")]
    if len(parts) < 2:
        return _norm(full)
    return _norm(parts[0][:1] + "".join(parts[1:]))


def nfl_scheme_lines(off_abbr, def_abbr, season, unavailable=frozenset()):
    """Plain English scheme notes for one side of the ball."""
    o, d = ESPN_TO_NFLVERSE.get(off_abbr, off_abbr), ESPN_TO_NFLVERSE.get(def_abbr, def_abbr)
    try:
        off, deff, targets = nfl_scheme_tables(season)
    except Exception:
        return [], {}
    if o not in off.index or d not in deff.index:
        return [], {}
    O, D = off.loc[o], deff.loc[d]
    lines, facts = [], {}
    n_teams = len(deff)

    # blitz
    br, ob = _rank(deff, "blitz_rate", d), _rank(off, "epa_blitz", o)
    if pd.notna(D["blitz_rate"]):
        facts["Blitz rate"] = f'{D["blitz_rate"]:.0%} ({ordinal(br)} most)'
        txt = f'{def_abbr} blitzes on <b>{D["blitz_rate"]:.0%}</b> of dropbacks ({ordinal(br)} most of {n_teams}).'
        if pd.notna(O["epa_blitz"]) and ob:
            txt += (f' {off_abbr} averages <b>{O["epa_blitz"]:+.2f} EPA</b> per dropback vs the blitz '
                    f'({ordinal(ob)} best).')
            if br <= 10 and ob <= 10:
                txt += f' <span class="edge">Edge {off_abbr}</span>'
            elif br <= 10 and ob >= n_teams - 9:
                txt += f' <span class="edge" style="color:#B91C1C">Edge {def_abbr}</span>'
        lines.append(txt)
    # who gets the ball when blitzed
    t = targets[targets["posteam"] == o]
    if not t.empty:
        t = t[~t["receiver_player_name"].map(_pbp_name).isin(unavailable)]
    if not t.empty and t["blitz_targets"].sum() >= 15:
        t = t.assign(share=t["targets"] / t["targets"].sum(), bshare=t["blitz_targets"] / t["blitz_targets"].sum())
        t = t[t["blitz_targets"] >= 5].assign(lift=lambda x: x["bshare"] - x["share"]).sort_values("lift", ascending=False)
        if not t.empty and t.iloc[0]["lift"] >= 0.04:
            r = t.iloc[0]
            lines.append(f'When blitzed, {off_abbr} looks to <b>{html.escape(r["receiver_player_name"])}</b> on '
                         f'{r["bshare"]:.0%} of throws ({r["share"]:.0%} normally).')
    # play action
    pr, pe = _rank(off, "pa_rate", o), _rank(deff, "epa_vs_pa", d)
    if pd.notna(O["pa_rate"]) and pr:
        txt = f'{off_abbr} uses play action on <b>{O["pa_rate"]:.0%}</b> of dropbacks ({ordinal(pr)} most).'
        if pd.notna(D["epa_vs_pa"]) and pe:
            txt += f' {def_abbr} allows <b>{D["epa_vs_pa"]:+.2f} EPA</b> vs play action ({ordinal(pe)} most).'
        facts["Play action"] = f'{O["pa_rate"]:.0%} ({ordinal(pr)})'
        if pr <= 10 or (pe and pe <= 10):
            lines.append(txt)
    # deep shots
    dr, de = _rank(off, "deep_rate", o), _rank(deff, "epa_vs_deep", d)
    if pd.notna(O["deep_rate"]) and dr and de and (dr <= 10 or de <= 10):
        lines.append(f'{off_abbr} throws deep (20+ air yards) on <b>{O["deep_rate"]:.0%}</b> of passes ({ordinal(dr)} most); '
                     f'{def_abbr} allows <b>{D["epa_vs_deep"]:+.2f} EPA</b> on deep throws ({ordinal(de)} most).')
    # pass rush vs protection
    sr, osr = _rank(deff, "sack_rate", d), _rank(off, "sack_rate", o)
    if pd.notna(D["sack_rate"]) and sr and osr:
        facts["Sack rate"] = f'{D["sack_rate"]:.1%} ({ordinal(sr)})'
        if sr <= 8 and osr <= 12:
            lines.append(f'Pressure alert: {def_abbr} sacks on <b>{D["sack_rate"]:.1%}</b> of dropbacks ({ordinal(sr)} most) '
                         f'and {off_abbr} gives up sacks on {O["sack_rate"]:.1%} ({ordinal(osr)} most).')
    # pass heavy or run heavy
    if pd.notna(O.get("proe", np.nan)):
        lean = "pass heavy" if O["proe"] >= 3 else "run heavy" if O["proe"] <= -3 else "balanced"
        facts["Pass rate vs expected"] = f'{O["proe"]:+.1f}% ({lean})'
    if pd.notna(O.get("motion_rate", np.nan)):
        facts["Motion"] = f'{O["motion_rate"]:.0%} of plays'
    # coverage shells
    cov, cov_season = nfl_coverage(season)
    c = cov.get(d)
    if c:
        when = "this season" if cov_season == season else "last season"
        shells = ", ".join(f"{n} {v:.0%}" for n, v in c["shells"])
        facts[f"Coverage ({when})"] = f'man {c["man"]:.0%} · zone {1 - c["man"]:.0%}'
        lines.append(f'{def_abbr} played <b>{"man" if c["man"] >= 0.5 else "zone"}</b> on '
                     f'{max(c["man"], 1 - c["man"]):.0%} of snaps {when} (most used: {shells}).')
    return lines, facts


# ---------- the Game Insights page ----------
def _who(r):
    q = ' <span class="status s-q">Q</span>' if r["questionable"] else ""
    return f'<b>{html.escape(r["name"])}</b> <span class="muted">{html.escape(r["team"])} · {html.escape(r["role"])}</span>{q}'


def _conf_tag(c):
    cls = {"High": "g-good", "Medium": "g-mid", "Low": "g-bad"}[c]
    return f'<span class="grade {cls}" style="font-size:.62rem">{c.upper()} CONFIDENCE</span>'


def _fmt(stat, v):
    return f"{v:.0f}" if stat.endswith("YDS") or stat == "PRA" or stat == "PTS" else f"{v:.1f}"


def render_game(sport_name, g):
    cfg = SPORTS[sport_name]
    key = cfg["key"]
    nba = key == "nba"
    if st.button("← Back to slate", key=f"{key}_back"):
        st.session_state.pop(f"{key}_game", None)
        st.rerun()

    with st.spinner("Running 10,000 simulations of this game (first open takes 15 to 30 seconds)..."):
        try:
            res = simulate_game(sport_name, g["id"], g["home"], g["away"])
        except Exception as e:
            st.error(f"Couldn't run this game right now. Try again in a minute. ({e})")
            return
    home, away, game = res["home"], res["away"], res["game"]
    when = _local(g["date"])
    when_txt = f'{when.strftime("%a %b")} {when.day}, {when.strftime("%I:%M %p").lstrip("0")} ET' if when else ""
    st.markdown(
        f'<div class="hero" style="background:linear-gradient(110deg,#{away["color"]} 0%,#{away["color"]} 40%,'
        f'#{home["color"]} 60%,#{home["color"]} 100%)">'
        f'<div class="hero-side"><div class="hero-abbr">{html.escape(away["abbr"])}</div>'
        f'<div class="hero-name">{html.escape(away["name"])}</div><div class="hero-sub">Away</div></div>'
        f'<div class="hero-vs">@</div>'
        f'<div class="hero-side right"><div class="hero-abbr">{html.escape(home["abbr"])}</div>'
        f'<div class="hero-name">{html.escape(home["name"])}</div><div class="hero-sub">{when_txt}</div></div></div>',
        unsafe_allow_html=True)

    players = res["players"]
    # ----- headline insights -----
    lines = []
    if game:
        fav_home = game["home_win"] >= 0.5
        lead, pwin = (home, game["home_win"]) if fav_home else (away, 1 - game["home_win"])
        lines.append(f'Simulated score: <b>{away["abbr"]} {game["away_pts"]:.0f}, {home["abbr"]} {game["home_pts"]:.0f}</b>. '
                     f'{lead["abbr"]} wins <b>{pwin:.0%}</b> of 10,000 sims.')
        if game.get("p_over") is not None:
            ou = game["odds"]["total"]
            lean = "over" if game["p_over"] >= 0.5 else "under"
            pl = game["p_over"] if lean == "over" else 1 - game["p_over"]
            if pl < 0.53:
                lines.append(f'Total: model {game["total"]:.1f} vs O/U {ou:.1f}. No lean, it\'s right on the number.')
            else:
                lines.append(f'Total: model {game["total"]:.1f} vs O/U {ou:.1f}. The {lean} hits in <b>{pl:.0%}</b> of sims.')
        if game.get("p_cover") is not None:
            lines.append(f'{game["fav"]} covers {game["spread"]:g} in <b>{game["p_cover"]:.0%}</b> of sims.')
    # biggest positional weak spots
    spots = []
    for def_team, off_team in ((home, away), (away, home)):
        table = res["defense"].get(def_team["id"]) or {}
        if nba:
            for grp, stats in table.items():
                for s, f in stats.items():
                    spots.append((f, f'{def_team["abbr"]} allows <b>{vs_avg(f)}</b> {s} per minute to {grp.lower()}', off_team))
        else:
            for slot, (f, allowed, base) in table.items():
                if allowed and base and slot in STAR_SLOTS:
                    spots.append((f, f'{def_team["abbr"]} allows <b>{allowed:.0f}</b> yards a game to {slot}s '
                                     f'(avg {base:.0f}), {vs_avg(f)}', off_team))
    for f, txt, off_team in sorted(spots, key=lambda x: x[0], reverse=True)[:2]:
        if f >= 1.06:
            lines.append(f'🎯 {txt}. Good spot for {off_team["abbr"]}.')
    scheme = {}
    if not nba:
        season = cfg["season_fn"]()
        try:
            all_players, inj = load_players(cfg["site"], load_teams(cfg["site"])), load_injuries(cfg["site"])
        except Exception:
            all_players, inj = [], {}
        for off_team, def_team in ((away, home), (home, away)):
            rows = inj.get(off_team["id"]) or inj.get(off_team["name"]) or []
            out_keys = injured_keys(rows, out=True)
            gone = frozenset(_pbp_name(p["name"]) for p in all_players if p["team_id"] == off_team["id"]
                             and (is_listed(p, out_keys) or status_class(p.get("inj", "")) == "s-out"))
            gone |= frozenset(_pbp_name(r["player"]) for r in rows if status_class(r["status"]) == "s-out")
            scheme[off_team["abbr"]] = nfl_scheme_lines(off_team["abbr"], def_team["abbr"], season, gone)
        for abbr, (sl, _) in scheme.items():
            lines += [f"🏈 {x}" for x in sl[:1]]
    if lines:
        st.markdown('<div class="insights"><div class="insights-title">💡 Game insights</div>'
                    + "".join(f'<div class="ins-line">{l}</div>' for l in lines)
                    + f'<div class="muted" style="margin-top:.7rem">From {SIMS:,} simulations. Scores blend recent form '
                    f'with the Vegas line. Numbers, not guarantees.</div></div>', unsafe_allow_html=True)

    # ----- players to watch -----
    section("⭐ Players to watch", "biggest boost vs a normal night for them")
    big = [p for p in players if (nba and p["mins_recent"] >= 24) or (not nba and p["slot"] in STAR_SLOTS)]
    for p in big:
        m = p["main"]
        p["boost"] = p["mean"][m] / p["normal"][m] - 1 if p["normal"].get(m) else 0
    watch = sorted([p for p in big if p["boost"] >= 0.03], key=lambda p: p["boost"], reverse=True)[:3]
    cards = []
    for p in watch:
        m = p["main"]
        why = []
        if nba:
            fd = p["f_def"].get("PTS", 1.0)
            if abs(fd - 1) >= 0.03:
                why.append(f'{"soft" if fd > 1 else "tough"} vs {p["group"].lower()} ({vs_avg(fd)} PTS)')
            if p["mins_recent"] - p["mins_normal"] >= 2:
                why.append(f'minutes up ({p["mins_recent"]:.0f} lately vs {p["mins_normal"]:.0f})')
            if abs(p["pace"] - 1) >= 0.02:
                why.append(f'{"fast" if p["pace"] > 1 else "slow"} game')
        else:
            if abs(p["f_slot"] - 1) >= 0.03:
                why.append(f'{vs_avg(p["f_slot"])} yards allowed to {p["slot"]}s')
            if abs(p["env"] - 1) >= 0.03:
                why.append(f'team projected to score {"more" if p["env"] > 1 else "less"} than usual')
        stat_txt = " · ".join(f'{s} {_fmt(s, p["proj"][s])}' for s in (["PTS", "REB", "AST", "3PM"] if nba else
                                                                         [x for x in NFL_SIM_STATS.get(p["slot"][:2], []) if x in p["proj"]]))
        cards.append(f'<div class="star"><div class="star-label">⭐ {html.escape(p["team"])} · {html.escape(p["role"])} '
                     f'{_conf_tag(p["conf"])}</div><div class="star-name">{html.escape(p["name"])}'
                     f'{" <span>Q, if he plays</span>" if p["questionable"] else ""}</div>'
                     f'<div class="ins-line">Projected {stat_txt}. <b>{p["boost"]:+.0%}</b> {m} vs his normal night'
                     f'{": " + ", ".join(why) if why else ""}.</div></div>')
    st.markdown("".join(cards) or '<div class="muted">No one gets a real boost in this game. '
                'Everyone projects close to a normal night.</div>', unsafe_allow_html=True)

    # ----- best props -----
    section("🎯 Props the numbers like", "his usual line, and how tonight moves it")
    props = []
    for p in players:
        for pr in p["props"]:
            move = pr["p"] - pr["p_norm"]
            if abs(move) >= PROP_MIN_MOVE:
                props.append((abs(move), p, pr, move))
    props.sort(key=lambda x: x[0], reverse=True)
    rows = []
    for _, p, pr, move in props[:10]:
        if pr["stat"] == "ANYTIME TD":
            label, prob, normal = "Anytime TD", pr["p"], pr["p_norm"]
        else:
            over = move > 0
            label = f'{"Over" if over else "Under"} {pr["line"]:g} {pr["stat"]}'
            prob, normal = (pr["p"], pr["p_norm"]) if over else (1 - pr["p"], 1 - pr["p_norm"])
        rows.append(f'<div class="sneaky"><div>{_who(p)}<br><span class="muted">{label} · his avg {pr["avg"]:.1f} · '
                    f'normally hits {normal:.0%}</span> {_conf_tag(p["conf"])}</div>'
                    + edge_pill(f"{prob:.0%}", "in sims") + '</div>')
    st.markdown("".join(rows) or '<div class="muted">Nothing moves much in this game. The numbers say play it straight.</div>',
                unsafe_allow_html=True)
    st.caption("Lines here are estimated from each player's average, not real sportsbook lines. "
               "Compare with your book: if the real line is lower than ours for an over, even better.")

    # ----- defense by position -----
    section("🛡️ Defense by position", "what each defense allows vs average (green = good for the offense)")
    c1, c2 = st.columns(2)
    for col, def_team in ((c1, away), (c2, home)):
        table = res["defense"].get(def_team["id"]) or {}
        with col:
            st.markdown(f'<div class="inj-team">vs {html.escape(def_team["name"])} defense '
                        f'<span class="muted">({res["n_def"].get(def_team["id"], 0):g} games)</span></div>',
                        unsafe_allow_html=True)
            if not table:
                st.markdown('<div class="muted">Not enough games yet.</div>', unsafe_allow_html=True)
                continue
            if nba:
                head = "".join(f"<th>{s}</th>" for s in NBA_SIM_STATS)
                body = "".join(f'<tr><td>{grp}</td>' + "".join(
                    f'<td class="{"up" if f >= 1.03 else "down" if f <= 0.97 else "flat"}">{(f - 1):+.0%}</td>'
                    for f in (stats.get(s, 1.0) for s in NBA_SIM_STATS)) + "</tr>" for grp, stats in table.items())
            else:
                head = "<th>Yards allowed</th><th>Avg</th><th>vs avg</th>"
                body = "".join(f'<tr><td>{slot}</td><td>{a:.0f}</td><td>{b:.0f}</td>'
                               f'<td class="{"up" if f >= 1.03 else "down" if f <= 0.97 else "flat"}">{(f - 1):+.0%}</td></tr>'
                               for slot, (f, a, b) in table.items() if a == a and b)
            st.markdown(f'<table class="dmx"><tr><th></th>{head}</tr>{body}</table>', unsafe_allow_html=True)

    # ----- NFL scheme report -----
    if not nba and scheme:
        section("🏈 Scheme report", "blitz, play action, deep shots and coverage from every play this season")
        c1, c2 = st.columns(2)
        for col, (abbr, (sl, facts)) in zip((c1, c2), scheme.items()):
            other = home["abbr"] if abbr == away["abbr"] else away["abbr"]
            with col:
                st.markdown(f'<div class="inj-team">{html.escape(abbr)} offense vs {html.escape(other)} defense</div>'
                            + "".join(f'<div class="ins-line">• {x}</div>' for x in sl)
                            + "".join(f'<div class="muted">{html.escape(k)}: {html.escape(v)}</div>' for k, v in facts.items())
                            or '<div class="muted">No charting data for this matchup yet.</div>', unsafe_allow_html=True)
        st.caption("Play by play and charting data from nflverse (FTN charting). EPA = expected points added per play; "
                   "higher is better for the offense.")

    # ----- full projections -----
    first = res.get("first")
    if first and first["rows"]:
        section(f"🥇 {first['kind']}", f"who scored first in {SIMS:,} simulated games")
        players_only = [r for r in first["rows"] if r["role"]]
        top2 = players_only[:2]
        st.markdown("".join(
            f'<div class="star"><div class="star-label">🥇 Look at #{i} · {html.escape(r["team"])} · {html.escape(r["role"])}'
            f'{" · Q" if r["questionable"] else ""}</div><div class="star-name">{html.escape(r["name"])}</div>'
            f'<div class="ins-line">{first["kind"]} in <b>{r["p"]:.1%}</b> of sims · fair odds <b>{american(r["p"])}</b></div></div>'
            for i, r in enumerate(top2, 1)), unsafe_allow_html=True)
        rest = [r for r in first["rows"] if r not in top2][:8]
        st.markdown("".join(
            f'<div class="sneaky"><div><b>{html.escape(r["name"])}</b> <span class="muted">{html.escape(r["team"])}'
            f'{" · " + html.escape(r["role"]) if r["role"] else ""}</span></div>'
            + edge_pill(f'{r["p"]:.1%}', f'fair {american(r["p"])}') + '</div>' for r in rest), unsafe_allow_html=True)
        team_txt = " · ".join(f'{abbr} scores first {p:.0%}' for abbr, p in first["team"].items())
        none_txt = f' · No touchdown {first["none"]:.1%}' if first.get("none") is not None else ""
        st.markdown(f'<div class="muted" style="margin-top:.4rem">{team_txt}{none_txt}</div>', unsafe_allow_html=True)
        st.caption("Fair odds = the price that matches the sim probability, with no sportsbook cut. If your book offers a "
                   "bigger number (say +900 when fair is +700), the numbers like it. "
                   + ("First basket = first made field goal; starters are projected from recent minutes and the tip is a coin flip."
                      if nba else "First TD counts rushing or receiving touchdowns, plus any other player or the defense."))

    if not nba:
        section("🏈 Touchdown odds", f"how often each player scored in {SIMS:,} simulated games")
        scorers = sorted([p for p in players if p.get("td")], key=lambda p: p["td"]["p"], reverse=True)
        rows = []
        for p in scorers:
            td = p["td"]
            move = td["p"] - td["p_norm"]
            arrow = "▲" if move >= 0.02 else "▼" if move <= -0.02 else "•"
            cls = "up" if move >= 0.02 else "down" if move <= -0.02 else "flat"
            rows.append(f'<div class="sneaky"><div>{_who(p)}<br><span class="muted">2+ TDs in {td["p2"]:.0%} of sims · '
                        f'{td["per_game"]:.2f} TDs per game lately ({td["games"]} games) · </span>'
                        f'<span class="delta {cls}">{arrow} {move:+.0%} vs his normal</span></div>'
                        + edge_pill(f'{td["p"]:.0%}', "scores a TD") + '</div>')
        st.markdown("".join(rows) or '<div class="muted">Not enough data yet.</div>', unsafe_allow_html=True)
        st.caption("Anytime TD = rushing or receiving touchdown (QB passing TDs are in the projections below).")

    with st.expander("📊 Full projections for every player", expanded=not nba):
        role_order = list(NFL_SLOTS)
        for t in (away, home):
            mine = [p for p in players if p["team"] == t["abbr"]]
            if not mine:
                continue
            if nba:
                stats = ["PTS", "REB", "AST", "3PM", "PRA"]
                mine.sort(key=lambda p: p["mins_recent"], reverse=True)
            else:
                stats = ["PASS YDS", "PASS TD", "RUSH YDS", "REC", "REC YDS"]
                mine.sort(key=lambda p: role_order.index(p["role"]) if p["role"] in role_order else 99)
            rows = []
            for p in mine:
                row = {"Player": p["name"], "Role": p["role"]}
                if nba:
                    row["Min"] = round(p["mins_recent"])
                for s in stats:
                    row[s] = f'{p["proj"][s]:.1f}' if s in p["proj"] else ""
                if not nba:
                    row["TD %"] = f'{p["td"]["p"]:.0%}' if p.get("td") else ""
                row["Confidence"] = p["conf"]
                rows.append(row)
            st.markdown(f'**{t["name"]}**')
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Medians from the simulations (half the sims land above, half below). "
                   "TD % = how often he scored at least one rushing or receiving TD.")


# ---------- ONE SPORT ----------
def render_sport(sport_name):
    key = SPORTS[sport_name]["key"]
    views = ["🔎 Player lookup", "📋 Slate"]
    view = st.segmented_control("View", views, default=views[0], label_visibility="collapsed",
                                key=f"{key}_view") or views[0]
    if view == views[1]:
        render_slate(sport_name)
    else:
        render_player(sport_name)


# ==========================================================
# PREMIER LEAGUE
# Players: official Fantasy Premier League (FPL) data (free, from the league itself)
# Teams + referees: football-data.co.uk match files (free; shots, fouls, corners, cards, referee)
# ==========================================================
EPL_KEY = "⚽ Premier League"
FPL = "https://fantasy.premierleague.com/api"
FD_URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
EPL_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
EPL_COLORS = {  # club colors for the matchup banner (unknown clubs fall back to gray)
    "ARS": "EF0107", "AVL": "670E36", "BOU": "DA291C", "BRE": "E30613", "BHA": "0057B8", "BUR": "6C1D45",
    "CHE": "034694", "CRY": "1B458F", "EVE": "003399", "FUL": "111111", "LEE": "1D428A", "LIV": "C8102E",
    "MCI": "6CABDD", "MUN": "DA291C", "NEW": "241F20", "NFO": "DD0000", "SUN": "EB172B", "TOT": "132257",
    "WHU": "7A263A", "WOL": "FDB913", "IPS": "3A64A3", "LEI": "003090", "SOU": "D71920",
}
# FPL team name -> football-data.co.uk team name (only the ones that differ)
FD_NAMES = {"Man Utd": "Man United", "Spurs": "Tottenham", "Sheffield Utd": "Sheffield United",
            "Nott'm Forest": "Nott'm Forest", "Luton": "Luton"}
EPL_STATS = {
    "GK": ["MIN", "SV", "GC", "CS", "YC"],
    "DEF": ["MIN", "G", "A", "xG", "TKL", "CBI", "DC", "YC"],
    "MID": ["MIN", "G", "A", "xG", "xA", "TKL", "REC", "YC"],
    "FWD": ["MIN", "G", "A", "xG", "xA", "YC"],
}
EPL_COLS = {  # FPL field -> short column name
    "minutes": "MIN", "goals_scored": "G", "assists": "A", "expected_goals": "xG", "expected_assists": "xA",
    "yellow_cards": "YC", "red_cards": "RC", "saves": "SV", "goals_conceded": "GC", "clean_sheets": "CS",
    "tackles": "TKL", "clearances_blocks_interceptions": "CBI", "recoveries": "REC",
    "defensive_contribution": "DC", "bps": "BPS", "total_points": "FPL PTS",
}
EPL_ESPN_EXTRA = {"FWD": ["SH", "SOT", "FS", "FC"], "MID": ["SH", "SOT", "FS", "FC"],
                  "DEF": ["SH", "FC", "FS"], "GK": []}  # shots / fouls from ESPN match pages
EPL_MIN_MINUTES = 450      # need 5 full games worth of minutes to rank a player
EPL_FORM_MATCHES = 10      # team form = last 10 league matches
EPL_RECENT = 5             # player "recent form" = last 5 appearances


# ---------- PL DATA ----------
@st.cache_data(ttl=3600)
def fpl_bootstrap():
    return get_json(f"{FPL}/bootstrap-static/")


@st.cache_data(ttl=30 * 60)
def fpl_fixtures():
    return get_json(f"{FPL}/fixtures/")


@st.cache_data(ttl=3 * 3600)
def fpl_player(player_id):
    return get_json(f"{FPL}/element-summary/{player_id}/")


def fd_season_code(seasons_ago=0):
    """football-data.co.uk names seasons like '2627' for 2026/27."""
    today = dt.date.today()
    start = (today.year if today.month >= 8 else today.year - 1) - seasons_ago
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


@st.cache_data(ttl=6 * 3600)
def fd_matches(code, div="E0"):
    r = requests.get(FD_URL.format(code=code, div=div), headers=HEADERS, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig", errors="replace")))
    df = df.dropna(subset=["HomeTeam", "AwayTeam"])
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df


def fd_all(div="E0"):
    """This season's matches plus last season's (for referees and early season team form)."""
    frames = []
    for ago in (0, 1):
        try:
            d = fd_matches(fd_season_code(ago), div)
            d["season_ago"] = ago
            frames.append(d)
        except Exception:
            pass
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


FD_PAIRS = {  # (home column, away column) -> our names
    "goals": ("FTHG", "FTAG"), "shots": ("HS", "AS"), "sot": ("HST", "AST"),
    "fouls": ("HF", "AF"), "corners": ("HC", "AC"), "yellows": ("HY", "AY"), "reds": ("HR", "AR"),
}


def fd_long(df):
    """One row per team per match: what they did ('_for') and what their opponent did ('_against')."""
    if df.empty:
        return pd.DataFrame()
    rows = []
    for _, m in df.iterrows():
        for side, other, h_or_a in (("H", "A", "Home"), ("A", "H", "Away")):
            row = {"team": m["HomeTeam"] if side == "H" else m["AwayTeam"],
                   "opp": m["AwayTeam"] if side == "H" else m["HomeTeam"],
                   "venue": h_or_a, "date": m["Date"], "referee": m.get("Referee", ""),
                   "season_ago": m.get("season_ago", 0)}
            for name, (hcol, acol) in FD_PAIRS.items():
                mine, theirs = (hcol, acol) if side == "H" else (acol, hcol)
                row[f"{name}_for"] = pd.to_numeric(m.get(mine), errors="coerce")
                row[f"{name}_against"] = pd.to_numeric(m.get(theirs), errors="coerce")
            rows.append(row)
    return pd.DataFrame(rows)


@st.cache_data(ttl=6 * 3600)
def fd_long_cached(div="E0"):
    return fd_long(fd_all(div))


@st.cache_data(ttl=6 * 3600)
def epl_team_table(div="E0"):
    """Each team's averages over its last 10 league matches, plus the league average."""
    long = fd_long(fd_all(div))
    if long.empty:
        return {}, {}
    long = long.sort_values("date", ascending=False)
    num_cols = [c for c in long.columns if c.endswith("_for") or c.endswith("_against")]
    table = {}
    for team, g in long.groupby("team"):
        current = g[g["season_ago"] == 0]
        use = current if len(current) >= 4 else g  # early season: borrow last season
        table[team] = use.head(EPL_FORM_MATCHES)[num_cols].mean().to_dict()
        table[team]["n"] = len(use.head(EPL_FORM_MATCHES))
    league = long[long["season_ago"] == 0][num_cols].mean().to_dict() if (long["season_ago"] == 0).any() \
        else long[num_cols].mean().to_dict()
    return table, league


@st.cache_data(ttl=6 * 3600)
def referee_table():
    df = fd_all()
    if df.empty or "Referee" not in df:
        return pd.DataFrame()
    df = df.dropna(subset=["Referee"]).copy()
    for c in ("HY", "AY", "HR", "AR", "HF", "AF"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    g = df.groupby("Referee")
    out = pd.DataFrame({
        "Matches": g.size(),
        "Yellows/game": (g["HY"].sum() + g["AY"].sum()) / g.size(),
        "Reds/game": (g["HR"].sum() + g["AR"].sum()) / g.size(),
        "Fouls/game": (g["HF"].sum() + g["AF"].sum()) / g.size(),
        "Home yellows": g["HY"].mean(),
        "Away yellows": g["AY"].mean(),
        "This season": df[df["season_ago"] == 0].groupby("Referee").size(),
    }).fillna(0)
    out = out[out["Matches"] >= 3].sort_values("Yellows/game", ascending=False)
    return out.round(2)


def fd_team_name(fpl_name):
    return FD_NAMES.get(fpl_name, fpl_name)


ESPN_SOCCER = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1"
ESPN_SOCCER_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}"
TEAM_ALIASES = {  # the same club is spelled differently by FPL, football-data and ESPN
    "manutd": "manchesterunited", "manunited": "manchesterunited", "mancity": "manchestercity",
    "spurs": "tottenham", "tottenhamhotspur": "tottenham", "nottmforest": "nottinghamforest",
    "wolves": "wolverhampton", "wolverhamptonwanderers": "wolverhampton", "newcastleunited": "newcastle",
    "westhamunited": "westham", "brightonhovealbion": "brighton", "brightonandhovealbion": "brighton",
    "leedsunited": "leeds", "afcbournemouth": "bournemouth", "leicestercity": "leicester",
    "ipswichtown": "ipswich", "sheffieldunited": "sheffieldutd", "lutontown": "luton",
}
ESPN_PLAYER_STATS = {  # ESPN stat name -> our column
    "totalShots": "SH", "shotsOnTarget": "SOT", "foulsCommitted": "FC", "foulsSuffered": "FS",
    "offsides": "OFF", "totalGoals": "ESPN G", "goalAssists": "ESPN A",
    "yellowCards": "ESPN YC", "redCards": "ESPN RC", "saves": "ESPN SV",
}


def canon(team_name):
    n = _norm(team_name)
    return TEAM_ALIASES.get(n, n)


@st.cache_data(ttl=6 * 3600)
def espn_day(date_str, code="eng.1"):
    """All matches ESPN lists for one day (YYYYMMDD) in a competition: event id + both clubs."""
    data = get_json(f"{ESPN_SOCCER_BASE.format(code=code)}/scoreboard", params={"dates": date_str})
    out = []
    for event in data.get("events", []):
        clubs = [canon((c.get("team") or {}).get("displayName", ""))
                 for c in (event.get("competitions") or [{}])[0].get("competitors", [])]
        out.append({"id": str(event.get("id", "")), "clubs": clubs})
    return out


def espn_find_event(team_a, team_b, when, code="eng.1"):
    """ESPN's id for the match between two clubs on (or a day either side of) a date."""
    a, b = canon(team_a), canon(team_b)
    for shift in (0, -1, 1):
        day = (when + dt.timedelta(days=shift)).strftime("%Y%m%d")
        try:
            events = espn_day(day, code)
        except Exception:
            continue
        for e in events:
            joined = " ".join(e["clubs"])
            if a[:7] in joined and b[:7] in joined:
                return e["id"]
    return None


@st.cache_data(ttl=24 * 3600)  # finished matches never change
def espn_match(event_id, code="eng.1"):
    """Referee + every player's stat line (shots, shots on target, fouls...) from one match."""
    data = get_json(f"{ESPN_SOCCER_BASE.format(code=code)}/summary", params={"event": event_id})
    referee = ""
    for o in (data.get("gameInfo") or {}).get("officials", []) or []:
        pos = str((o.get("position") or {}).get("name") if isinstance(o.get("position"), dict) else o.get("position") or "")
        if "referee" in pos.lower() and "assistant" not in pos.lower():
            referee = o.get("displayName") or o.get("fullName") or ""
            break
    players = []
    for side in data.get("rosters", []) or []:
        club_name = (side.get("team") or {}).get("displayName", "")
        club = canon(club_name)
        for p in side.get("roster", []) or []:
            row = {"club": club, "club_name": club_name, "name": (p.get("athlete") or {}).get("displayName", ""),
                   "starter": bool(p.get("starter")),
                   "subbed_in": bool(p.get("subbedIn") if isinstance(p.get("subbedIn"), bool)
                                     else (p.get("subbedIn") or {}).get("didSub"))}
            for stat in p.get("stats", []) or []:
                col = ESPN_PLAYER_STATS.get(stat.get("name"))
                if col:
                    row[col] = pd.to_numeric(stat.get("value"), errors="coerce")
            if row["starter"] or row["subbed_in"]:
                players.append(row)
    return {"referee": referee, "players": players}


def same_player(fpl_player, espn_name):
    """FPL and ESPN spell some names differently ('Gabriel dos Santos Magalhães' vs 'Gabriel Magalhães')."""
    e = _norm(espn_name)
    full, web = _norm(fpl_player["name"]), _norm(fpl_player.get("web_name", ""))
    if e == full or (web and (e == web or e.endswith(web))):
        return True
    last = _norm(fpl_player["name"].split()[-1]) if fpl_player["name"] else ""
    return bool(last) and e.endswith(last) and e[:1] == full[:1]


@st.cache_data(ttl=6 * 3600)
def espn_referee(home_name, away_name, kickoff_iso):
    """Best effort: the announced referee from ESPN's match page (PL names refs early in the week)."""
    when = _local(kickoff_iso)
    if not when:
        return ""
    event_id = espn_find_event(home_name, away_name, when)
    return espn_match(event_id)["referee"] if event_id else ""


def espn_player_log(player, team_name, hist, teams):
    """Shots, shots on target and fouls for each match in the player's FPL log."""
    by_abbr = {t["abbr"]: t for t in teams.values()}
    rows = []
    for _, h in hist.iterrows():
        if not h.get("MIN") or not h.get("Date"):
            continue
        opp = by_abbr.get(h["Opp"])
        when = _local(f'{h["Date"]}T12:00:00Z')
        if not opp or not when:
            continue
        try:
            event_id = espn_find_event(team_name, opp["name"], when)
            match = espn_match(event_id) if event_id else None
        except Exception:
            match = None
        if not match:
            continue
        mine = next((p for p in match["players"] if p["club"] == canon(team_name) and same_player(player, p["name"])), None)
        if mine:
            rows.append({"Date": h["Date"], **{k: v for k, v in mine.items() if k in ESPN_PLAYER_STATS.values()}})
    return pd.DataFrame(rows)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def espn_team_shooters(team_name, recent):
    """Per player shooting over a club's recent matches. `recent` = ((YYYY-MM-DD, opponent), ...)."""
    rows = []
    for date_str, opp in recent:
        when = _local(f"{date_str}T12:00:00Z")
        try:
            event_id = espn_find_event(team_name, opp, when)
            match = espn_match(event_id) if event_id else None
        except Exception:
            match = None
        if not match:
            continue
        for p in match["players"]:
            if p["club"] == canon(team_name):
                rows.append(p)
    if not rows:
        return []
    df = pd.DataFrame(rows)
    for c in ("SH", "SOT", "FC", "FS"):
        if c not in df:
            df[c] = 0
    g = df.groupby("name")
    out = pd.DataFrame({"apps": g.size(), "starts": g["starter"].sum(), "SH": g["SH"].mean(),
                        "SOT": g["SOT"].mean(), "FC": g["FC"].mean(), "FS": g["FS"].mean()}).reset_index()
    return out.to_dict("records")


def match_ref_name(espn_name, refs):
    """'Anthony Taylor' (ESPN) -> 'A Taylor' (football-data)."""
    if not espn_name:
        return None
    parts = espn_name.split()
    for r in refs:
        rp = str(r).split()
        if rp and _norm(rp[-1]) == _norm(parts[-1]) and _norm(rp[0])[:1] == _norm(parts[0])[:1]:
            return r
    return None


def epl_context():
    """Players, teams and fixtures in friendly shapes."""
    boot = fpl_bootstrap()
    teams = {t["id"]: {"id": str(t["id"]), "fpl_id": t["id"], "name": t["name"], "abbr": t["short_name"],
                       "color": EPL_COLORS.get(t["short_name"], "475569")} for t in boot["teams"]}
    players = []
    for e in boot["elements"]:
        if e.get("status") == "u" and not e.get("minutes"):
            continue  # left the club / never played
        team = teams.get(e["team"])
        if not team:
            continue
        mins = e.get("minutes") or 0
        f = lambda k: float(e.get(k) or 0)
        players.append({
            "id": str(e["id"]), "name": f'{e.get("first_name", "")} {e.get("second_name", "")}'.strip(),
            "web_name": e.get("web_name", ""), "pos": EPL_POS.get(e.get("element_type"), ""),
            "team_id": team["id"], "team_abbr": team["abbr"], "minutes": mins,
            "status": e.get("status", "a"), "news": e.get("news", ""),
            "chance": e.get("chance_of_playing_next_round"),
            "xg90": f("expected_goals") / mins * 90 if mins else 0,
            "xa90": f("expected_assists") / mins * 90 if mins else 0,
            "yc90": f("yellow_cards") / mins * 90 if mins else 0,
            "goals": e.get("goals_scored", 0), "assists": e.get("assists", 0),
        })
    players.sort(key=lambda p: p["name"])
    return teams, players


def epl_next_fixture(fixtures, team_fpl_id):
    upcoming = [f for f in fixtures if not f.get("finished") and f.get("kickoff_time")
                and team_fpl_id in (f.get("team_h"), f.get("team_a"))]
    upcoming.sort(key=lambda f: f["kickoff_time"])
    return upcoming[0] if upcoming else None


def epl_availability(players, team_id):
    """FPL's injury/suspension flags in the same shape as the NBA/NFL injury report."""
    rows = []
    for p in players:
        if p["team_id"] != team_id or p["status"] in ("a", "u", "n"):
            continue
        chance = p["chance"]
        if p["status"] == "i" or chance == 0:
            status = "Out"
        elif p["status"] == "s":
            status = "Suspended (out)"
        elif chance is not None and chance <= 25:
            status = f"Doubtful ({chance}%)"
        else:
            status = f"Questionable ({chance}%)" if chance is not None else "Questionable"
        rows.append({"player": p["name"], "id": p["id"], "status": status, "note": p["news"]})
    return rows


def epl_history(player, teams):
    """The player's match by match log this season."""
    data = fpl_player(player["id"])
    rows = []
    by_fpl_id = {t["fpl_id"]: t for t in teams.values()}
    for h in data.get("history", []):
        opp = by_fpl_id.get(h.get("opponent_team"), {})
        row = {"GW": h.get("round"), "Date": (h.get("kickoff_time") or "")[:10],
               "Opp": opp.get("abbr", ""), "opp_id": opp.get("id", ""),
               "H/A": "Home" if h.get("was_home") else "Away",
               "Score": f'{h.get("team_h_score", "")}-{h.get("team_a_score", "")}'}
        for k, col in EPL_COLS.items():
            if k in h:
                row[col] = pd.to_numeric(h.get(k), errors="coerce")
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Date", ascending=False).reset_index(drop=True)
    return df


# ---------- PL INSIGHTS ----------
def vs_avg(factor, more="more", fewer="fewer"):
    """1.35 -> '35% more', 0.86 -> '14% fewer', 1.02 -> 'about average'."""
    if factor is None:
        return ""
    diff = factor - 1
    if abs(diff) < 0.05:
        return "about average"
    return f"{abs(diff):.0%} {more if diff > 0 else fewer}"


def edge_pill(value, label):
    return f'<span class="edge" style="text-align:right">{value}<br><span class="muted" style="font-weight:600">{label}</span></span>'
def epl_attack_factor(table, league, opp_fd):
    """How leaky is this defense? 1.30 = concedes 30% more goals than average."""
    t = table.get(opp_fd)
    if not t or not league.get("goals_for"):
        return None
    return t["goals_against"] / league["goals_for"]


def epl_match_projection(table, league, home_fd, away_fd, ref_row=None):
    h, a = table.get(home_fd), table.get(away_fd)
    if not h or not a:
        return None

    def both(stat):
        home = (h[f"{stat}_for"] + a[f"{stat}_against"]) / 2
        away = (a[f"{stat}_for"] + h[f"{stat}_against"]) / 2
        return home, away

    proj = {s: both(s) for s in ("goals", "shots", "corners", "yellows", "fouls")}
    yellows = sum(proj["yellows"])
    if ref_row is not None and league.get("yellows_for"):
        ref_factor = ref_row["Yellows/game"] / (league["yellows_for"] * 2)
        yellows = yellows * (0.5 + 0.5 * ref_factor)  # blend team tendencies with the referee's
    proj["total_yellows"] = yellows
    proj["league_yellows"] = league.get("yellows_for", 0) * 2
    proj["league_goals"] = league.get("goals_for", 0) * 2
    proj["league_corners"] = league.get("corners_for", 0) * 2
    return proj


def epl_threats(players, teams, fixtures_list, table, league, limit=10):
    """Attackers whose scoring rate meets a leaky defense this round."""
    opp_of = {}
    for f in fixtures_list:
        opp_of[str(f["team_h"])] = (str(f["team_a"]), f)
        opp_of[str(f["team_a"])] = (str(f["team_h"]), f)
    out = []
    for p in players:
        if p["pos"] not in ("MID", "FWD") or p["minutes"] < EPL_MIN_MINUTES or p["team_id"] not in opp_of:
            continue
        if p["status"] in ("i", "s") or p["chance"] == 0:
            continue
        opp_id, fx = opp_of[p["team_id"]]
        opp = teams.get(int(opp_id))
        factor = epl_attack_factor(table, league, fd_team_name(opp["name"])) if opp else None
        if not factor:
            continue
        threat = (p["xg90"] + 0.5 * p["xa90"]) * factor
        o = table.get(fd_team_name(opp["name"])) or {}
        out.append({**p, "opp_abbr": opp["abbr"], "factor": factor, "threat": threat, "fixture": fx,
                    "opp_ga": o.get("goals_against"), "lg_ga": league.get("goals_for")})
    return sorted(out, key=lambda r: r["threat"], reverse=True)[:limit]


# ---------- PL PAGES ----------
def render_epl_player():
    try:
        teams, players = epl_context()
        fixtures = fpl_fixtures()
    except Exception as e:
        st.error(f"Couldn't reach the Premier League data right now. Try again in a minute. ({e})")
        return
    team_list = sorted(teams.values(), key=lambda t: t["name"])
    try:
        table, league = epl_team_table()
    except Exception:
        table, league = {}, {}

    c1, c2 = st.columns(2)
    with c1:
        player = st.selectbox("Player", players, index=None, key="epl_player", placeholder="Start typing a name",
                              format_func=lambda p: f'{p["name"]} ({p["team_abbr"]}, {p["pos"]})')
    nxt = None
    if player:
        nxt = epl_next_fixture(fixtures, int(player["team_id"]))
    with c2:
        default = None
        if nxt:
            opp_fpl = nxt["team_a"] if nxt["team_h"] == int(player["team_id"]) else nxt["team_h"]
            default = next((i for i, t in enumerate(team_list) if t["fpl_id"] == opp_fpl), None)
        opp = st.selectbox("Opponent", team_list, index=default, placeholder="Pick a team",
                           format_func=lambda t: t["name"], key=f'epl_opp_{player["id"] if player else "none"}')
    if not player or not opp:
        st.markdown('<div class="muted">Pick a player and an opponent to get started.</div>', unsafe_allow_html=True)
        return
    if opp["id"] == player["team_id"]:
        st.warning("That's the player's own team. Pick a different opponent.")
        return

    my_team = teams[int(player["team_id"])]
    with st.spinner("Loading match history..."):
        try:
            hist = epl_history(player, teams)
        except Exception:
            hist = pd.DataFrame()
    if hist.empty:
        st.warning("No match history for this player yet this season.")
        return
    with st.spinner("Adding shots and fouls from ESPN match pages..."):
        try:
            elog = espn_player_log(player, my_team["name"], hist, teams)
        except Exception:
            elog = pd.DataFrame()
    if not elog.empty:
        hist = hist.merge(elog, on="Date", how="left")
    played = hist[hist["MIN"].fillna(0) > 0]
    stats = [s for s in EPL_STATS.get(player["pos"], EPL_STATS["MID"]) + EPL_ESPN_EXTRA.get(player["pos"], [])
             if s in hist.columns and hist[s].notna().any()]
    vs_opp = played[played["opp_id"] == opp["id"]]

    hero(player, my_team, opp, len(vs_opp), {"seasons_back": 1},
         sub=f"{len(vs_opp)} appearances vs {opp['abbr']} this season")
    if nxt:
        home = teams.get(nxt["team_h"], {}).get("abbr", "")
        away = teams.get(nxt["team_a"], {}).get("abbr", "")
        when = _local(nxt["kickoff_time"])
        when_txt = f'{when.strftime("%a %b")} {when.day}, {when.strftime("%I:%M %p").lstrip("0")} ET' if when else ""
        st.markdown(f'<span class="pill-game">🗓️ Next match: <b>{home} vs {away}</b> · {when_txt}</span>',
                    unsafe_allow_html=True)

    # ----- Insights -----
    lines = []
    my_fd, opp_fd = fd_team_name(my_team["name"]), fd_team_name(opp["name"])
    refs = referee_table()
    ref_name, ref_row = None, None
    if nxt:
        try:
            home_t, away_t = teams.get(nxt["team_h"]), teams.get(nxt["team_a"])
            espn_ref = espn_referee(home_t["name"], away_t["name"], nxt["kickoff_time"])
            ref_name = match_ref_name(espn_ref, list(refs.index)) if not refs.empty else None
            if ref_name:
                ref_row = refs.loc[ref_name]
            elif espn_ref:
                ref_name = espn_ref
        except Exception:
            pass
    grade = None
    factor = epl_attack_factor(table, league, opp_fd)
    o = table.get(opp_fd)
    if o and league:
        if player["pos"] in ("MID", "FWD"):
            e = factor - 1
            grade = ("g-good", "GOOD MATCHUP") if e >= EDGE else ("g-bad", "TOUGH MATCHUP") if e <= -EDGE else ("g-mid", "NEUTRAL")
            lines.append(f'{opp["abbr"]} concede <b>{o["goals_against"]:.1f} goals</b> and allow <b>{o["shots_against"]:.1f} shots</b> '
                         f'per game (league avg {league["goals_for"]:.1f} and {league["shots_for"]:.1f}). '
                         f'That\'s <b>{abs(e):.0%} {"more" if e >= 0 else "fewer"}</b> goals than average.')
        else:
            e = 1 - o["goals_for"] / league["goals_for"]
            grade = ("g-good", "CLEAN SHEET LOOK") if e >= EDGE else ("g-bad", "TOUGH MATCHUP") if e <= -EDGE else ("g-mid", "NEUTRAL")
            lines.append(f'{opp["abbr"]} score <b>{o["goals_for"]:.1f}</b> and take <b>{o["shots_for"]:.1f} shots</b> per game '
                         f'(league avg {league["goals_for"]:.1f} and {league["shots_for"]:.1f}).')
    if o and league and player["pos"] in ("MID", "FWD") and "SOT" in played and played["SOT"].notna().sum() >= 3:
        sot = played["SOT"].dropna()
        lines.append(f'🎯 {opp["abbr"]} allow <b>{o["sot_against"]:.1f} shots on target</b> per game '
                     f'(league avg {league["sot_for"]:.1f}). {html.escape(player["web_name"])} averages '
                     f'<b>{sot.mean():.1f}</b> on target ({sot.head(EPL_RECENT).mean():.1f} over his last {min(EPL_RECENT, len(sot))}) '
                     f'and has hit 1+ in {int((sot >= 1).sum())} of {len(sot)} matches.')
    if len(played) >= 3 and {"xG", "xA", "MIN"} <= set(played.columns):
        recent = played.head(EPL_RECENT)
        r90 = (recent["xG"].sum() + recent["xA"].sum()) / max(recent["MIN"].sum(), 1) * 90
        s90 = (played["xG"].sum() + played["xA"].sum()) / max(played["MIN"].sum(), 1) * 90
        if s90 > 0 and abs(r90 - s90) / s90 >= EDGE:
            lines.append(f'Form {"rising 📈" if r90 > s90 else "cooling 📉"}: <b>{r90:.2f}</b> xG+xA per 90 over his last '
                         f'{len(recent)} vs <b>{s90:.2f}</b> on the season.')
    proj = None
    if nxt:
        home_t, away_t = teams.get(nxt["team_h"]), teams.get(nxt["team_a"])
        proj = epl_match_projection(table, league, fd_team_name(home_t["name"]), fd_team_name(away_t["name"]), ref_row)
    if proj:
        cards_word = ("🟨 Card heavy" if proj["total_yellows"] >= proj["league_yellows"] * (1 + EDGE)
                      else "Calm" if proj["total_yellows"] <= proj["league_yellows"] * (1 - EDGE) else "Normal")
        ref_txt = (f' Referee <b>{html.escape(str(ref_name))}</b> averages {ref_row["Yellows/game"]:.1f} yellows a game.'
                   if ref_row is not None else (f' Referee: {html.escape(str(ref_name))}.' if ref_name else ""))
        lines.append(f'{cards_word} match: projected <b>{proj["total_yellows"]:.1f} yellows</b> '
                     f'(league avg {proj["league_yellows"]:.1f}).{ref_txt} '
                     f'{html.escape(player["web_name"])} picks up {player["yc90"]:.2f} yellows per 90.')
    # player to watch in this fixture (both teams)
    star_html = ""
    if nxt:
        fx_list = [nxt]
        threats = epl_threats(players, teams, fx_list, table, league, limit=1)
        if threats and threats[0]["factor"] >= 1 + EDGE:
            t = threats[0]
            star_html = (f'<div class="star"><div class="star-label">⭐ Player to watch</div>'
                         f'<div class="star-name">{html.escape(t["name"])} <span>{t["team_abbr"]} · {t["pos"]}</span></div>'
                         f'<div class="ins-line">Best goal threat in this match: {t["xg90"]:.2f} xG per 90 against a '
                         f'{t["opp_abbr"]} defense conceding {t["opp_ga"]:.1f} goals per game (league avg {t["lg_ga"]:.1f}), '
                         f'<span class="edge">{vs_avg(t["factor"])}</span>.</div></div>')
    if lines or star_html:
        grade_html = f'<span class="grade {grade[0]}">{grade[1]}</span>' if grade else ""
        title = f'<div class="insights-title">💡 Matchup insights {grade_html}</div>'
        body = "".join(f'<div class="ins-line">{l}</div>' for l in lines)
        st.markdown(f'<div class="insights">{title}{body}{star_html}'
                    f'<div class="muted" style="margin-top:.7rem">Team numbers from each side\'s last {EPL_FORM_MATCHES} league matches. '
                    f'Trends, not guarantees.</div></div>', unsafe_allow_html=True)

    # ----- Match outlook -----
    if proj:
        section("Match outlook", "projected from both teams' recent matches")
        h_ab = teams.get(nxt["team_h"], {}).get("abbr", "")
        a_ab = teams.get(nxt["team_a"], {}).get("abbr", "")
        cards = [("Goals", f'{sum(proj["goals"]):.1f}', f'{h_ab} {proj["goals"][0]:.1f} · {a_ab} {proj["goals"][1]:.1f}'),
                 ("Shots", f'{sum(proj["shots"]):.1f}', f'{h_ab} {proj["shots"][0]:.1f} · {a_ab} {proj["shots"][1]:.1f}'),
                 ("Corners", f'{sum(proj["corners"]):.1f}', f'league avg {proj["league_corners"]:.1f}'),
                 ("Yellow cards", f'{proj["total_yellows"]:.1f}', f'league avg {proj["league_yellows"]:.1f}'),
                 ("Fouls", f'{sum(proj["fouls"]):.1f}', f'{h_ab} {proj["fouls"][0]:.1f} · {a_ab} {proj["fouls"][1]:.1f}')]
        st.markdown('<div class="cards">' + "".join(
            f'<div class="card"><div class="card-label">{l}</div><div class="card-value">{v}</div><div class="muted">{n}</div></div>'
            for l, v, n in cards) + '</div>', unsafe_allow_html=True)

    # ----- Season numbers -----
    section("Season numbers", f"{len(played)} appearances · per 90 minutes in small print")
    mins = played["MIN"].sum() if "MIN" in played else 0
    cards = []
    for s in stats:
        avg = played[s].mean()
        per90 = (played[s].sum() / mins * 90) if mins and s != "MIN" else None
        shown = f"{avg:.0f}" if s == "MIN" else f"{avg:.2f}"
        cards.append(f'<div class="card"><div class="card-label">{s} per game</div><div class="card-value">{shown}</div>'
                     f'{f"<div class=muted>{per90:.2f} per 90</div>" if per90 is not None else ""}</div>')
    st.markdown(f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)

    # ----- Line checker -----
    section("Line checker", "how often he went over a number")
    l1, l2 = st.columns(2)
    first = [x for x in ("SOT", "SH") if x in stats] if player["pos"] in ("MID", "FWD") else []
    line_stats = first + [x for x in stats if x != "MIN" and x not in first] + (["MIN"] if "MIN" in stats else [])
    line_stat = l1.selectbox("Stat", line_stats, key="epl_line_stat")
    avg = played.head(10)[line_stat].mean()
    suggested = 0.5 if line_stat in ("G", "A", "YC", "CS") or not avg or avg < 1 else float(int(avg)) + 0.5
    if line_stat in ("SOT", "SH", "FC", "FS") and avg and avg >= 1:  # low count props sit at the nearest .5
        suggested = float(int(avg)) - 0.5 if avg - int(avg) < 0.5 else float(int(avg)) + 0.5
    line_value = l2.number_input("Line", min_value=0.0, value=suggested, step=0.5, key=f'epl_line_{player["id"]}_{line_stat}')
    st.markdown('<div class="cards">'
                + hit_card(f"vs {opp['abbr']}", vs_opp[line_stat], line_value)
                + hit_card(f"Last {EPL_RECENT}", played.head(EPL_RECENT)[line_stat], line_value)
                + hit_card("This season", played[line_stat], line_value)
                + "</div>", unsafe_allow_html=True)

    # ----- Tables -----
    section("Match log", "this season")
    cols = ["GW", "Date", "Opp", "H/A", "Score"] + stats + [c for c in ("BPS", "FPL PTS") if c in hist.columns]
    st.dataframe(hist[cols], hide_index=True, width="stretch")

    section("Availability", "from the official FPL injury and suspension flags")
    a1, a2 = st.columns(2)
    for col, team in ((a1, my_team), (a2, opp)):
        with col:
            injury_list(team, epl_availability(players, team["id"]))


def render_epl_fixtures():
    try:
        teams, players = epl_context()
        fixtures = fpl_fixtures()
    except Exception as e:
        st.error(f"Couldn't reach the Premier League data right now. Try again in a minute. ({e})")
        return
    try:
        table, league = epl_team_table()
    except Exception:
        table, league = {}, {}
    refs = referee_table()

    upcoming = [f for f in fixtures if not f.get("finished") and f.get("kickoff_time") and f.get("event")]
    if not upcoming:
        st.markdown('<div class="muted">No upcoming fixtures found.</div>', unsafe_allow_html=True)
        return
    gw = min(f["event"] for f in upcoming)
    round_fx = sorted([f for f in upcoming if f["event"] == gw], key=lambda f: f["kickoff_time"])
    section(f"Gameweek {gw}", f"{len(round_fx)} matches")

    # Top goal threats + card watch
    threats = epl_threats(players, teams, round_fx, table, league, limit=10)
    section("Top goal threats", "scoring rate × how leaky the opponent's defense is")
    rows = []
    for i, t in enumerate(threats, 1):
        q = " <span class='status s-q'>Q</span>" if t["status"] == "d" else ""
        rows.append(f'<div class="sneaky"><div><b>{i}. {html.escape(t["name"])}</b> '
                    f'<span class="muted">{t["team_abbr"]} · {t["pos"]} · vs {t["opp_abbr"]}</span>{q}<br>'
                    f'<span class="muted">{t["xg90"]:.2f} xG + {t["xa90"]:.2f} xA per 90 · '
                    f'{t["opp_abbr"]} concede {t["opp_ga"]:.1f} goals per game (avg {t["lg_ga"]:.1f}), {vs_avg(t["factor"])}</span></div>'
                    + edge_pill(f'{t["xg90"] * t["factor"]:.2f}', "proj xG") + '</div>')
    st.markdown("".join(rows) or '<div class="muted">Not enough data yet.</div>', unsafe_allow_html=True)

    def card_env(f):
        h, a = teams.get(f["team_h"]), teams.get(f["team_a"])
        if not h or not a:
            return None, ""
        ref_name, ref_row = "", None
        try:
            ref_name = espn_referee(h["name"], a["name"], f["kickoff_time"])
            matched = match_ref_name(ref_name, list(refs.index)) if not refs.empty else None
            if matched:
                ref_name, ref_row = matched, refs.loc[matched]
        except Exception:
            pass
        return epl_match_projection(table, league, fd_team_name(h["name"]), fd_team_name(a["name"]), ref_row), ref_name

    with ThreadPoolExecutor(max_workers=8) as pool:
        envs = dict(zip([f["id"] for f in round_fx], pool.map(card_env, round_fx)))
    projs = {k: v[0] for k, v in envs.items()}
    ref_names = {k: v[1] for k, v in envs.items()}
    section("Shots on target leaders", "shots on target per game × how many the opponent allows")
    go_key = "epl_sot_go"
    if not st.session_state.get(go_key):
        if st.button("🎯 Load shots on target leaders", key="epl_sot_btn", type="primary"):
            st.session_state[go_key] = True
        st.caption("Reads each club's last 5 match pages, so the first load takes a minute. Then it's saved for hours.")
    if st.session_state.get(go_key):
        long = fd_long_cached()
        out_by_team = {}
        for p in players:
            if p["status"] in ("i", "s") or p["chance"] == 0:
                out_by_team.setdefault(p["team_id"], []).append(p)
        shooters = []
        with st.spinner("Reading match pages..."):
            for f in round_fx:
                for side, other in (("team_h", "team_a"), ("team_a", "team_h")):
                    team, opp_t = teams.get(f[side]), teams.get(f[other])
                    if not team or not opp_t or long.empty:
                        continue
                    recent = long[long["team"] == fd_team_name(team["name"])].sort_values("date", ascending=False).head(5)
                    key_recent = tuple((d.strftime("%Y-%m-%d"), o) for d, o in zip(recent["date"], recent["opp"]) if pd.notna(d))
                    try:
                        rows = espn_team_shooters(team["name"], key_recent)
                    except Exception:
                        rows = []
                    o = table.get(fd_team_name(opp_t["name"]))
                    factor = (o["sot_against"] / league["sot_for"]) if o and league.get("sot_for") else 1
                    for r in rows:
                        if r["apps"] < 3 or r["starts"] < 2 or not r["SOT"]:
                            continue
                        if any(same_player(x, r["name"]) for x in out_by_team.get(team["id"], [])):
                            continue
                        shooters.append({**r, "team": team["abbr"], "opp": opp_t["abbr"], "factor": factor,
                                         "opp_sot": (o or {}).get("sot_against"), "lg_sot": league.get("sot_for"),
                                         "score": r["SOT"] * factor})
        shooters = sorted(shooters, key=lambda r: r["score"], reverse=True)[:10]
        st.markdown("".join(
            f'<div class="sneaky"><div><b>{i}. {html.escape(r["name"])}</b> '
            f'<span class="muted">{r["team"]} · vs {r["opp"]}</span><br>'
            f'<span class="muted">{r["SOT"]:.1f} on target, {r["SH"]:.1f} shots per game (last {r["apps"]}) · '
            + (f'{r["opp"]} allow {r["opp_sot"]:.1f} on target per game (avg {r["lg_sot"]:.1f}), {vs_avg(r["factor"])}'
               if r.get("opp_sot") else "") + '</span></div>'
            + edge_pill(f'{r["score"]:.1f}', "proj SOT") + '</div>' for i, r in enumerate(shooters, 1))
            or '<div class="muted">No shooting data found yet.</div>', unsafe_allow_html=True)

    section("Card watch", "players who pick up yellows, in the most card heavy matches")
    card_rows = []
    for p in players:
        if p["minutes"] < EPL_MIN_MINUTES or p["status"] in ("i", "s"):
            continue
        fx = next((f for f in round_fx if int(p["team_id"]) in (f["team_h"], f["team_a"])), None)
        pr = projs.get(fx["id"]) if fx else None
        if not pr or not pr["league_yellows"]:
            continue
        env = pr["total_yellows"] / pr["league_yellows"]
        opp_id = fx["team_a"] if fx["team_h"] == int(p["team_id"]) else fx["team_h"]
        card_rows.append({**p, "score": p["yc90"] * env, "env": env, "opp_abbr": teams[opp_id]["abbr"],
                          "proj_y": pr["total_yellows"], "lg_y": pr["league_yellows"]})
    card_rows = sorted(card_rows, key=lambda r: r["score"], reverse=True)[:8]
    st.markdown("".join(
        f'<div class="sneaky"><div><b>{i}. {html.escape(r["name"])}</b> '
        f'<span class="muted">{r["team_abbr"]} · {r["pos"]} · vs {r["opp_abbr"]}</span><br>'
        f'<span class="muted">{r["yc90"]:.2f} yellows per 90 · match projects {r["proj_y"]:.1f} yellows (avg {r["lg_y"]:.1f})</span></div>'
        + edge_pill(f'{1 - math.exp(-r["score"]):.0%}', "card chance") + '</div>' for i, r in enumerate(card_rows, 1))
        or '<div class="muted">Not enough data yet.</div>', unsafe_allow_html=True)

    section("Matches", "projected goals, corners and cards")
    cards = []
    for f in round_fx:
        h, a = teams.get(f["team_h"]), teams.get(f["team_a"])
        if not h or not a:
            continue
        pr = projs.get(f["id"])
        when = _local(f["kickoff_time"])
        when_txt = f'{when.strftime("%a")} {when.strftime("%I:%M %p").lstrip("0")} ET' if when else ""
        meta, badge = [], ""
        if pr:
            meta += [f'Goals {sum(pr["goals"]):.1f} ({h["abbr"]} {pr["goals"][0]:.1f} · {a["abbr"]} {pr["goals"][1]:.1f})',
                     f'Corners {sum(pr["corners"]):.1f} · Yellows {pr["total_yellows"]:.1f}']
            if ref_names.get(f["id"]):
                meta.append(f'Ref: {html.escape(str(ref_names[f["id"]]))}')
            if pr["total_yellows"] >= pr["league_yellows"] * (1 + EDGE):
                badge = '<span class="grade g-bad">CARD HEAVY</span>'
            elif sum(pr["goals"]) >= pr["league_goals"] * (1 + EDGE):
                badge = '<span class="grade g-good">GOALS EXPECTED</span>'
        cards.append(f'<div class="game-card" style="border-top:5px solid #{h["color"]}">'
                     f'<div class="game-teams">{h["abbr"]} <span>vs</span> {a["abbr"]}</div>'
                     f'<div class="muted">{when_txt}</div>{badge}<div class="game-meta">{"<br>".join(meta)}</div></div>')
    st.markdown(f'<div class="game-grid">{"".join(cards)}</div>', unsafe_allow_html=True)

    section("Referees", "this season and last · sorted by yellows per game")
    if refs.empty:
        st.markdown('<div class="muted">Referee data unavailable right now.</div>', unsafe_allow_html=True)
    else:
        st.dataframe(refs, width="stretch")


def render_epl():
    views = ["🔎 Player lookup", "📋 Slate"]
    view = st.segmented_control("View", views, default=views[0], label_visibility="collapsed",
                                key="epl_view") or views[0]
    if view == views[1]:
        render_epl_fixtures()
    else:
        render_epl_player()


# ==========================================================
# MORE SOCCER: LA LIGA, BUNDESLIGA, CHAMPIONS LEAGUE, EUROPA LEAGUE
# ESPN        -> fixtures, lineups, shots, shots on target, fouls, cards, injuries, referees
# Understat   -> xG, xA, key passes, minutes (big 5 domestic leagues)
# football-data.co.uk -> team corners, fouls, cards, shots (domestic leagues)
# Champions / Europa League clubs borrow their DOMESTIC numbers (that's where most of their games are)
# ==========================================================
SOCCER_KEY = "⚽ Soccer"
SOCCER_LEAGUES = {
    "Premier League": {"key": "epl", "espn": "eng.1"},  # uses the richer FPL pages above
    "La Liga": {"key": "laliga", "espn": "esp.1", "us": "La_liga", "fd": "SP1"},
    "Bundesliga": {"key": "bundesliga", "espn": "ger.1", "us": "Bundesliga", "fd": "D1"},
    "Champions League": {"key": "ucl", "espn": "uefa.champions", "europe": True},
    "Europa League": {"key": "uel", "espn": "uefa.europa", "europe": True},
}
DOMESTIC = [  # (ESPN code, Understat slug, football-data division)
    ("eng.1", "EPL", "E0"), ("esp.1", "La_liga", "SP1"), ("ger.1", "Bundesliga", "D1"),
    ("ita.1", "Serie_A", "I1"), ("fra.1", "Ligue_1", "F1"),
]
UNDERSTAT = "https://understat.com"
SOCCER_POS = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD", "A": "FWD", "S": "FWD"}
SOCCER_LOG_GAMES = 12        # player log = his club's last 12 matches in this competition
SOCCER_WINDOW_DAYS = 4       # a "round" = matches within 4 days of the first one
SOCCER_STATS = {
    "GK": ["MIN", "SV", "YC"],
    "DEF": ["MIN", "G", "A", "SH", "FC", "FS", "YC", "xG"],
    "MID": ["MIN", "G", "A", "SH", "SOT", "KP", "FC", "FS", "YC", "xG", "xA"],
    "FWD": ["MIN", "G", "A", "SH", "SOT", "KP", "FC", "FS", "YC", "xG", "xA"],
}


# ---------- club and player name matching ----------
TEAM_STOP = {"fc", "sc", "vfb", "vfl", "tsg", "cf", "rcd", "cd", "ud", "rc", "ca", "afc", "ac", "as", "ss",
             "sv", "fsv", "bsc", "1", "ogc", "osc", "sd", "us", "sk", "fk", "nk", "bk", "if", "aik"}
TEAM_KEY_ALIASES = {
    "rasenballsportleipzig": "rbleipzig", "leipzig": "rbleipzig",
    "borussiamgladbach": "monchengladbach", "borussiamonchengladbach": "monchengladbach",
    "mgladbach": "monchengladbach", "cologne": "koln", "einfrankfurt": "eintrachtfrankfurt",
    "athbilbao": "athleticclub", "athleticbilbao": "athleticclub", "athmadrid": "atleticomadrid",
    "espanol": "espanyol", "parissg": "parissaintgermain", "psg": "parissaintgermain",
    "internazionale": "inter", "intermilan": "inter", "bayernmunchen": "bayernmunich",
    "sportinglisbon": "sportingcp", "sporting": "sportingcp",
}


def team_key(name):
    """'1. FC Köln' -> 'koln', 'Atlético Madrid' -> 'atleticomadrid', 'Ath Madrid' -> 'atleticomadrid'."""
    plain = unicodedata.normalize("NFKD", name or "").lower().replace("-", " ").replace(".", " ")
    tokens = ["".join(ch for ch in t if ch.isalnum() and not unicodedata.combining(ch)) for t in plain.split()]
    key = "".join(t for t in tokens if t and t not in TEAM_STOP) or _norm(name)
    key = TEAM_ALIASES.get(key, key)
    return TEAM_KEY_ALIASES.get(key, key)


def same_team(a, b):
    ka, kb = team_key(a), team_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long = sorted((ka, kb), key=len)
    return len(short) >= 4 and short in long  # 'betis' in 'realbetis', 'dortmund' in 'borussiadortmund'


def find_team(name, candidates):
    """Best matching club name from a list (exact key first, then 'contains')."""
    exact = [c for c in candidates if team_key(c) == team_key(name)]
    if exact:
        return exact[0]
    loose = [c for c in candidates if same_team(c, name)]
    return min(loose, key=len) if loose else None


def same_person(a, b):
    """'Vinícius Júnior' == 'Vinicius Junior'; 'Kylian Mbappé' == 'K. Mbappe'."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    pa, pb = (a or "").split(), (b or "").split()
    if pa and pb and _norm(pa[-1]) == _norm(pb[-1]) and na[:1] == nb[:1] and len(_norm(pa[-1])) >= 3:
        return True
    short, long = sorted((na, nb), key=len)
    return len(short) >= 6 and (long.startswith(short) or long.endswith(short))


# ---------- ESPN (any competition) ----------
def soccer_site(code):
    return ESPN_SOCCER_BASE.format(code=code)


@st.cache_data(ttl=24 * 3600)
def soccer_teams(code):
    data = get_json(f"{soccer_site(code)}/teams")
    teams = []
    for entry in data["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        name = t.get("displayName", "")
        abbr = t.get("abbreviation") or (t.get("shortDisplayName") or name)[:3].upper()
        teams.append({"id": str(t["id"]), "name": name, "abbr": abbr, "color": t.get("color") or "475569"})
    return sorted(teams, key=lambda t: t["name"])


def _soccer_roster(args):
    code, team = args
    try:
        data = get_json(f"{soccer_site(code)}/teams/{team['id']}/roster")
    except Exception:
        return []
    flat = []
    for a in data.get("athletes", []):
        if isinstance(a, dict) and "items" in a:
            flat.extend(a["items"])
        else:
            flat.append(a)
    rows = []
    for a in flat:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        pos = a.get("position") or {}
        letter = (pos.get("name") or pos.get("abbreviation") or "?")[:1].upper()
        rows.append({"id": str(a["id"]), "name": a.get("displayName", ""), "pos": SOCCER_POS.get(letter, "MID"),
                     "team_id": team["id"], "team_abbr": team["abbr"], "team_name": team["name"],
                     "inj": next((i.get("status") or "" for i in (a.get("injuries") or []) if isinstance(i, dict)), "")})
    return rows


@st.cache_data(ttl=3 * 3600)
def soccer_players(code, teams):
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = pool.map(_soccer_roster, [(code, t) for t in teams])
    return sorted([p for r in results for p in r], key=lambda p: p["name"])


def _soccer_event(e):
    comp = (e.get("competitions") or [{}])[0]
    status = (comp.get("status") or e.get("status") or {}).get("type", {})
    sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    h, a = sides.get("home") or {}, sides.get("away") or {}
    ht, at = h.get("team") or {}, a.get("team") or {}
    done = bool(status.get("completed") or status.get("state") == "post")
    return {
        "id": str(e.get("id", "")), "date": e.get("date", ""),
        "label": e.get("shortName") or f'{at.get("abbreviation", "")} @ {ht.get("abbreviation", "")}',
        "done": done,
        "home_id": str(ht.get("id", "")), "away_id": str(at.get("id", "")),
        "home_name": ht.get("displayName", ""), "away_name": at.get("displayName", ""),
        "home_abbr": ht.get("abbreviation", ""), "away_abbr": at.get("abbreviation", ""),
        "home_score": _score(h) if done else None, "away_score": _score(a) if done else None,
    }


@st.cache_data(ttl=30 * 60)
def soccer_upcoming(code):
    """Backup: every match ESPN lists in the next 14 days for this competition."""
    today = dt.date.today()
    span = f'{today.strftime("%Y%m%d")}-{(today + dt.timedelta(days=14)).strftime("%Y%m%d")}'
    data = get_json(f"{soccer_site(code)}/scoreboard", params={"dates": span, "limit": 300})
    return [_soccer_event(e) for e in data.get("events", [])]


@st.cache_data(ttl=30 * 60)
def soccer_schedule(code, team_id):
    """A club's matches in this competition (played and upcoming), oldest first.
    For soccer, ESPN only lists PLAYED matches unless you also ask for fixture=true."""
    url = f"{soccer_site(code)}/teams/{team_id}/schedule"
    events = list(get_json(url).get("events", []))
    try:
        events += get_json(url, params={"fixture": "true"}).get("events", [])
    except Exception:
        pass
    games = {}
    for e in events:
        g = _soccer_event(e)
        if g["id"] and g["home_id"] and g["away_id"]:
            games[g["id"]] = g
    if not any(not g["done"] for g in games.values()):  # still no upcoming match? check the scoreboard
        try:
            for g in soccer_upcoming(code):
                if team_id in (g["home_id"], g["away_id"]) and not g["done"]:
                    games[g["id"]] = g
        except Exception:
            pass
    return sorted(games.values(), key=lambda g: g["date"])


# ---------- Understat ----------
@st.cache_resource
def us_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(UNDERSTAT, timeout=15)  # Understat wants the homepage cookie first
    except Exception:
        pass
    return s


def us_get(path):
    r = us_session().get(UNDERSTAT + path, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=20)
    r.raise_for_status()
    return r.json()


def us_season():
    today = dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


@st.cache_data(ttl=6 * 3600)
def us_league(slug, season):
    d = us_get(f"/getLeagueData/{slug}/{season}")
    return {"dates": d.get("dates", []), "players": d.get("players", []), "teams": d.get("teams", {})}


@st.cache_data(ttl=24 * 3600)
def us_match(match_id):
    rosters = us_get(f"/getMatchData/{match_id}").get("rosters", {})
    return [dict(p) for side in ("h", "a") for p in (rosters.get(side) or {}).values()]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


@st.cache_data(ttl=6 * 3600)
def us_players(slug):
    """Every player's season rates (per 90) in a big 5 league."""
    try:
        data = us_league(slug, us_season())
    except Exception:
        return []
    out = []
    for p in data["players"]:
        mins = _f(p.get("time"))
        per90 = (lambda k: _f(p.get(k)) / mins * 90) if mins else (lambda k: 0.0)
        out.append({"name": p.get("player_name", ""), "team": p.get("team_title", ""), "pos": p.get("position", ""),
                    "minutes": mins, "games": _f(p.get("games")), "xg90": per90("xG"), "xa90": per90("xA"),
                    "sh90": per90("shots"), "kp90": per90("key_passes"), "yc90": per90("yellow_cards"),
                    "goals": _f(p.get("goals"))})
    return out


@st.cache_data(ttl=6 * 3600)
def us_team_xg(slug):
    """Each club's xG for / against per game over its last 10 league matches, plus the league average."""
    try:
        teams = us_league(slug, us_season())["teams"]
    except Exception:
        return {}, 0
    rows = {}
    for t in teams.values():
        hist = sorted(t.get("history", []), key=lambda h: h.get("date", ""), reverse=True)[:10]
        if len(hist) >= 2:
            rows[t.get("title", "")] = {"xg": sum(_f(h.get("xG")) for h in hist) / len(hist),
                                        "xga": sum(_f(h.get("xGA")) for h in hist) / len(hist), "n": len(hist)}
    avg = sum(r["xg"] for r in rows.values()) / len(rows) if rows else 0
    return rows, avg


def us_find_match(slug, home, away, when):
    """Understat's id for a finished match (same two clubs, within a day)."""
    try:
        dates = us_league(slug, us_season())["dates"]
    except Exception:
        return None
    for m in dates:
        if not m.get("isResult"):
            continue
        d = _local((m.get("datetime") or "").replace(" ", "T") + "Z")
        if d and when and abs((d.date() - when.date()).days) <= 1 \
                and same_team((m.get("h") or {}).get("title", ""), home) and same_team((m.get("a") or {}).get("title", ""), away):
            return m.get("id")
    return None


@st.cache_data(ttl=24 * 3600)
def domestic_index():
    """Which big 5 league each club plays in (for Champions / Europa League clubs)."""
    idx = []
    for code, slug, div in DOMESTIC:
        try:
            titles = [t.get("title", "") for t in us_league(slug, us_season())["teams"].values()]
        except Exception:
            titles = []
        idx += [{"title": t, "code": code, "us": slug, "fd": div} for t in titles]
    return idx


def club_home_league(team_name, cfg):
    """The domestic league whose numbers describe this club."""
    if cfg.get("us"):
        return {"code": cfg["espn"], "us": cfg["us"], "fd": cfg["fd"]}
    idx = domestic_index()
    title = find_team(team_name, [i["title"] for i in idx])
    return next((i for i in idx if i["title"] == title), None) if title else None


def club_form(team_name, cfg):
    """(team row, league average, name in that table) from football-data for the club's domestic league."""
    home = club_home_league(team_name, cfg)
    if not home:
        return None, None, None
    try:
        table, league = epl_team_table(home["fd"])
    except Exception:
        return None, None, None
    key = find_team(team_name, list(table.keys()))
    return (table[key], league, key) if key else (None, None, None)


def club_xg(team_name, cfg):
    home = club_home_league(team_name, cfg)
    if not home:
        return None
    rows, avg = us_team_xg(home["us"])
    title = find_team(team_name, list(rows.keys()))
    return {**rows[title], "lg": avg, "us": home["us"], "title": title} if title and avg else None


def soccer_projection(home_name, away_name, cfg, ref_row=None):
    h, lh, _ = club_form(home_name, cfg)
    a, la, _ = club_form(away_name, cfg)
    if not h or not a:
        return None
    league = {k: (lh.get(k, 0) + la.get(k, 0)) / 2 for k in lh}  # two different leagues in Europe: average them
    return epl_match_projection({"H": h, "A": a}, league, "H", "A", ref_row)


def club_threats(team_name, opp_name, cfg, roster, limit=None):
    """A club's attackers: Understat xG per 90 x how many xG the opponent concedes vs its league average."""
    home = club_home_league(team_name, cfg)
    opp = club_xg(opp_name, cfg)
    if not home or not opp or not opp["lg"]:
        return []
    factor = opp["xga"] / opp["lg"]
    title = find_team(team_name, list({p["team"].split(",")[-1] for p in us_players(home["us"])}))
    out_names = [p["name"] for p in roster if status_class(p.get("inj", "")) == "s-out"]
    maybe = [p["name"] for p in roster if p.get("inj") and status_class(p["inj"]) not in ("s-out", "s-ok")]
    rows = []
    for p in us_players(home["us"]):
        if not title or not same_team(p["team"].split(",")[-1], title) or p["minutes"] < EPL_MIN_MINUTES:
            continue
        if any(same_person(p["name"], n) for n in out_names):
            continue
        rows.append({**p, "name": roster_name(p["name"], roster), "factor": factor, "proj_xg": p["xg90"] * factor,
                     "opp_xga": opp["xga"], "lg_xg": opp["lg"],
                     "questionable": any(same_person(p["name"], n) for n in maybe)})
    rows.sort(key=lambda r: r["proj_xg"], reverse=True)
    return rows[:limit] if limit else rows


def short_name(full):
    """'Vinícius Júnior' -> 'Vinícius', 'Harry Kane' -> 'Kane', 'Pedri' -> 'Pedri'."""
    parts = (full or "").split()
    if not parts:
        return ""
    if len(parts) > 1 and _norm(parts[-1]) in ("junior", "jr", "sr", "ii", "iii", "iv", "filho", "neto", "sobrinho"):
        return parts[-2] if len(parts) > 2 else parts[0]
    return parts[-1]


def roster_name(name, roster):
    """Use ESPN's spelling (with accents) when we can match the Understat name to the roster."""
    return next((p["name"] for p in roster if same_person(p["name"], name)), name)


def soccer_availability(roster):
    return [{"player": p["name"], "id": p["id"], "status": p["inj"], "note": ""} for p in roster if p.get("inj")]


# ---------- player match log ----------
def soccer_player_log(cfg, player, schedule):
    """One row per match he played: ESPN shots/fouls/cards + Understat xG/xA/key passes/minutes."""
    code = cfg["espn"]
    done = [g for g in schedule if g["done"]][-SOCCER_LOG_GAMES:]

    def one(g):
        try:
            m = espn_match(g["id"], code)
        except Exception:
            return None
        mine = next((p for p in m["players"]
                     if same_person(p["name"], player["name"]) and same_team(p.get("club_name", ""), player["team_name"])), None)
        if not mine:
            return None
        home = g["home_id"] == player["team_id"]
        row = {"Date": g["date"][:10], "Opp": g["away_abbr"] if home else g["home_abbr"],
               "opp_id": g["away_id"] if home else g["home_id"], "H/A": "Home" if home else "Away",
               "Score": f'{_fmt_score(g["home_score"])}-{_fmt_score(g["away_score"])}',
               "Started": "Yes" if mine.get("starter") else "Sub",
               "G": mine.get("ESPN G"), "A": mine.get("ESPN A"), "SH": mine.get("SH"), "SOT": mine.get("SOT"),
               "FC": mine.get("FC"), "FS": mine.get("FS"), "YC": mine.get("ESPN YC"), "SV": mine.get("ESPN SV")}
        if cfg.get("us"):  # domestic big 5: add Understat's xG, xA, key passes and minutes
            try:
                us_id = us_find_match(cfg["us"], g["home_name"], g["away_name"], _local(g["date"]))
                us_row = next((p for p in us_match(us_id) if same_person(p.get("player", ""), player["name"])), None) if us_id else None
            except Exception:
                us_row = None
            if us_row:
                row.update({"MIN": _f(us_row.get("time")), "xG": _f(us_row.get("xG")), "xA": _f(us_row.get("xA")),
                            "KP": _f(us_row.get("key_passes"))})
        return row

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(one, done) if r]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in df.columns:
        if c not in ("Date", "Opp", "opp_id", "H/A", "Score", "Started"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("Date", ascending=False).reset_index(drop=True)


def _fmt_score(x):
    return "" if x is None else f"{x:.0f}"


# ---------- player lookup page ----------
def render_soccer_player(league_name):
    cfg = SOCCER_LEAGUES[league_name]
    code, key = cfg["espn"], cfg["key"]
    try:
        teams = soccer_teams(code)
        players = soccer_players(code, teams)
    except Exception as e:
        st.error(f"Couldn't reach ESPN right now. Try refreshing in a minute. ({e})")
        return
    team_by_id = {t["id"]: t for t in teams}

    c1, c2 = st.columns(2)
    with c1:
        player = st.selectbox("Player", players, index=None, key=f"{key}_player", placeholder="Start typing a name",
                              format_func=lambda p: f'{p["name"]} ({p["team_abbr"]}, {p["pos"]})')
    schedule, nxt = [], None
    if player:
        try:
            schedule = soccer_schedule(code, player["team_id"])
        except Exception:
            schedule = []
        nxt = next((g for g in schedule if not g["done"]), None)
    with c2:
        default = None
        if nxt:
            opp_id = nxt["away_id"] if nxt["home_id"] == player["team_id"] else nxt["home_id"]
            default = next((i for i, t in enumerate(teams) if t["id"] == opp_id), None)
        opp = st.selectbox("Opponent", teams, index=default, placeholder="Pick a team", format_func=lambda t: t["name"],
                           key=f'{key}_opp_{player["id"] if player else "none"}')
    if not player or not opp:
        st.markdown('<div class="muted">Pick a player and an opponent to get started.</div>', unsafe_allow_html=True)
        return
    if opp["id"] == player["team_id"]:
        st.warning("That's the player's own team. Pick a different opponent.")
        return
    my_team = team_by_id.get(player["team_id"], {"id": player["team_id"], "name": player["team_name"],
                                                  "abbr": player["team_abbr"], "color": "475569"})

    with st.spinner("Reading his recent match pages (first time takes a few seconds)..."):
        log = soccer_player_log(cfg, player, schedule)
    vs_opp = log[log["opp_id"] == opp["id"]] if not log.empty else log
    hero(player, my_team, opp, len(vs_opp), {"seasons_back": 1},
         sub=f"{len(vs_opp)} meetings in his last {SOCCER_LOG_GAMES} {league_name} matches")
    fixture = nxt if nxt and opp["id"] in (nxt["home_id"], nxt["away_id"]) else None
    if fixture:
        when = _local(fixture["date"])
        when_txt = f'{when.strftime("%a %b")} {when.day}, {when.strftime("%I:%M %p").lstrip("0")} ET' if when else ""
        st.markdown(f'<span class="pill-game">🗓️ Next match: <b>{html.escape(fixture["label"])}</b> · {when_txt}</span>',
                    unsafe_allow_html=True)

    # ----- insights -----
    lines, grade = [], None
    o, lg, _ = club_form(opp["name"], cfg)
    oxg = club_xg(opp["name"], cfg)
    attacker = player["pos"] in ("MID", "FWD")
    if oxg:
        factor = oxg["xga"] / oxg["lg"] if attacker else oxg["xg"] / oxg["lg"]
        e = factor - 1 if attacker else 1 - factor
        if attacker:
            grade = ("g-good", "GOOD MATCHUP") if e >= EDGE else ("g-bad", "TOUGH MATCHUP") if e <= -EDGE else ("g-mid", "NEUTRAL")
            lines.append(f'{html.escape(opp["abbr"])} concede <b>{oxg["xga"]:.2f} xG</b> per game '
                         f'(their league avg {oxg["lg"]:.2f}), {vs_avg(factor)}.')
        else:
            grade = ("g-good", "CLEAN SHEET LOOK") if e >= EDGE else ("g-bad", "TOUGH MATCHUP") if e <= -EDGE else ("g-mid", "NEUTRAL")
            lines.append(f'{html.escape(opp["abbr"])} create <b>{oxg["xg"]:.2f} xG</b> per game '
                         f'(their league avg {oxg["lg"]:.2f}), {vs_avg(factor)}.')
    elif o and lg:
        lines.append(f'{html.escape(opp["abbr"])} concede <b>{o["goals_against"]:.1f} goals</b> per game '
                     f'(league avg {lg["goals_for"]:.1f}).')
    if attacker and o and lg and not log.empty and "SOT" in log and log["SOT"].notna().sum() >= 3:
        sot = log["SOT"].dropna()
        lines.append(f'🎯 {html.escape(opp["abbr"])} allow <b>{o["sot_against"]:.1f} shots on target</b> per game '
                     f'(league avg {lg["sot_for"]:.1f}). {html.escape(short_name(player["name"]))} averages '
                     f'<b>{sot.mean():.1f}</b> on target and has hit 1+ in {int((sot >= 1).sum())} of {len(sot)} matches.')
    if not log.empty and {"xG", "xA", "MIN"} <= set(log.columns) and log["MIN"].notna().sum() >= 3:
        lm = log.dropna(subset=["MIN"])
        recent = lm.head(5)
        r90 = (recent["xG"].sum() + recent["xA"].sum()) / max(recent["MIN"].sum(), 1) * 90
        s90 = (lm["xG"].sum() + lm["xA"].sum()) / max(lm["MIN"].sum(), 1) * 90
        if s90 > 0 and abs(r90 - s90) / s90 >= EDGE:
            lines.append(f'Form {"rising 📈" if r90 > s90 else "cooling 📉"}: <b>{r90:.2f}</b> xG+xA per 90 over his last '
                         f'{len(recent)} vs <b>{s90:.2f}</b> across his last {len(lm)}.')
    ref_name = ""
    proj = None
    if fixture:
        try:
            ref_name = espn_match(fixture["id"], code)["referee"]
        except Exception:
            ref_name = ""
        proj = soccer_projection(fixture["home_name"], fixture["away_name"], cfg)
    if proj:
        cards_word = ("🟨 Card heavy" if proj["total_yellows"] >= proj["league_yellows"] * (1 + EDGE)
                      else "Calm" if proj["total_yellows"] <= proj["league_yellows"] * (1 - EDGE) else "Normal")
        ref_txt = f' Referee: <b>{html.escape(ref_name)}</b>.' if ref_name else ""
        yc = log["YC"].dropna() if not log.empty and "YC" in log else pd.Series(dtype=float)
        mine = f' He\'s been booked in {int((yc > 0).sum())} of his last {len(yc)}.' if len(yc) else ""
        lines.append(f'{cards_word} match: projected <b>{proj["total_yellows"]:.1f} yellows</b> '
                     f'(avg {proj["league_yellows"]:.1f}).{ref_txt}{mine}')
    star_html = ""
    if fixture:
        cands = []
        for side, other in (("home", "away"), ("away", "home")):
            roster = [p for p in players if p["team_id"] == fixture[f"{side}_id"]]
            for t in club_threats(fixture[f"{side}_name"], fixture[f"{other}_name"], cfg, roster, limit=1):
                cands.append({**t, "abbr": fixture[f"{side}_abbr"], "opp_abbr": fixture[f"{other}_abbr"]})
        star = max(cands, key=lambda r: r["proj_xg"], default=None)
        if star and star["factor"] >= 1 + EDGE:
            star_html = (f'<div class="star"><div class="star-label">⭐ Player to watch</div>'
                         f'<div class="star-name">{html.escape(star["name"])} <span>{html.escape(star["abbr"])}'
                         f'{" · Q" if star["questionable"] else ""}</span></div>'
                         f'<div class="ins-line">Best goal threat in this match: {star["xg90"]:.2f} xG per 90 against a '
                         f'{html.escape(star["opp_abbr"])} defense conceding {star["opp_xga"]:.2f} xG per game '
                         f'(avg {star["lg_xg"]:.2f}), <span class="edge">{vs_avg(star["factor"])}</span>.</div></div>')
    if lines or star_html:
        grade_html = f'<span class="grade {grade[0]}">{grade[1]}</span>' if grade else ""
        body = "".join(f'<div class="ins-line">{l}</div>' for l in lines)
        st.markdown(f'<div class="insights"><div class="insights-title">💡 Matchup insights {grade_html}</div>{body}{star_html}'
                    f'<div class="muted" style="margin-top:.7rem">Team numbers from each club\'s last 10 domestic league '
                    f'matches. Trends, not guarantees.</div></div>', unsafe_allow_html=True)

    if proj:
        section("Match outlook", "projected from both clubs' recent league matches")
        h_ab, a_ab = fixture["home_abbr"], fixture["away_abbr"]
        cards = [("Goals", f'{sum(proj["goals"]):.1f}', f'{h_ab} {proj["goals"][0]:.1f} · {a_ab} {proj["goals"][1]:.1f}'),
                 ("Shots", f'{sum(proj["shots"]):.1f}', f'{h_ab} {proj["shots"][0]:.1f} · {a_ab} {proj["shots"][1]:.1f}'),
                 ("Corners", f'{sum(proj["corners"]):.1f}', f'avg {proj["league_corners"]:.1f}'),
                 ("Yellow cards", f'{proj["total_yellows"]:.1f}', f'avg {proj["league_yellows"]:.1f}'),
                 ("Fouls", f'{sum(proj["fouls"]):.1f}', f'{h_ab} {proj["fouls"][0]:.1f} · {a_ab} {proj["fouls"][1]:.1f}')]
        st.markdown('<div class="cards">' + "".join(
            f'<div class="card"><div class="card-label">{l}</div><div class="card-value">{v}</div><div class="muted">{n}</div></div>'
            for l, v, n in cards) + '</div>', unsafe_allow_html=True)

    if log.empty:
        st.info("No match stats found for him in this competition yet.")
    else:
        stats = [s for s in SOCCER_STATS.get(player["pos"], SOCCER_STATS["MID"]) if s in log.columns and log[s].notna().any()]
        section("Recent numbers", f"per game over his last {len(log)} {league_name} appearances")
        cards = []
        for s in stats:
            avg = log[s].mean()
            cards.append(f'<div class="card"><div class="card-label">{s} per game</div>'
                         f'<div class="card-value">{avg:.0f}</div></div>' if s == "MIN" else
                         f'<div class="card"><div class="card-label">{s} per game</div><div class="card-value">{avg:.2f}</div></div>')
        st.markdown(f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)

        section("Line checker", "how often he went over a number")
        first = [x for x in ("SOT", "SH") if x in stats] if attacker else []
        line_stats = first + [x for x in stats if x != "MIN" and x not in first] + (["MIN"] if "MIN" in stats else [])
        l1, l2 = st.columns(2)
        line_stat = l1.selectbox("Stat", line_stats, key=f"{key}_line_stat")
        avg = log[line_stat].head(10).mean()
        suggested = 0.5
        if pd.notna(avg) and avg >= 1 and line_stat not in ("G", "A", "YC"):
            suggested = float(int(avg)) - 0.5 if avg - int(avg) < 0.5 else float(int(avg)) + 0.5
            if line_stat in ("MIN",):
                suggested = float(int(avg)) + 0.5
        line_value = l2.number_input("Line", min_value=0.0, value=suggested, step=0.5,
                                     key=f'{key}_line_{player["id"]}_{line_stat}')
        st.markdown('<div class="cards">'
                    + hit_card(f"vs {opp['abbr']}", vs_opp[line_stat] if not vs_opp.empty else log[line_stat].iloc[0:0], line_value)
                    + hit_card("Last 5", log.head(5)[line_stat], line_value)
                    + hit_card(f"Last {len(log)}", log[line_stat], line_value)
                    + "</div>", unsafe_allow_html=True)

        section("Match log", f"his last {len(log)} {league_name} matches")
        cols = ["Date", "Opp", "H/A", "Score", "Started"] + stats
        st.dataframe(log[[c for c in cols if c in log.columns]], hide_index=True, width="stretch")

    section("Availability", "from ESPN's team rosters")
    a1, a2 = st.columns(2)
    for col, team in ((a1, my_team), (a2, opp)):
        with col:
            injury_list(team, soccer_availability([p for p in players if p["team_id"] == team["id"]]))


# ---------- slate page ----------
@st.cache_data(ttl=30 * 60, show_spinner=False)
def soccer_round(code, team_ids):
    """The next round: every club's next match, grouped to the first few days."""
    def nxt(tid):
        try:
            return next((g for g in soccer_schedule(code, tid) if not g["done"]), None)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        games = {g["id"]: g for g in pool.map(nxt, team_ids) if g and _local(g["date"])}
    ordered = sorted(games.values(), key=lambda g: g["date"])
    if not ordered:
        return []
    first = _local(ordered[0]["date"])
    return [g for g in ordered if (_local(g["date"]) - first).days < SOCCER_WINDOW_DAYS]


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def soccer_referee_table(code, team_ids):
    """Referee stats built from this season's ESPN match pages in this competition."""
    events = set()
    for tid in team_ids:
        try:
            events |= {g["id"] for g in soccer_schedule(code, tid) if g["done"]}
        except Exception:
            pass

    def one(eid):
        try:
            m = espn_match(eid, code)
        except Exception:
            return None
        if not m["referee"]:
            return None
        pl = pd.DataFrame(m["players"])
        tot = lambda c: float(pl[c].fillna(0).sum()) if c in pl else 0.0
        return {"Referee": m["referee"], "yellows": tot("ESPN YC"), "reds": tot("ESPN RC"), "fouls": tot("FC")}

    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = [r for r in pool.map(one, sorted(events)) if r]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    g = df.groupby("Referee")
    out = pd.DataFrame({"Matches": g.size(), "Yellows/game": g["yellows"].mean(),
                        "Reds/game": g["reds"].mean(), "Fouls/game": g["fouls"].mean()})
    return out[out["Matches"] >= 2].sort_values("Yellows/game", ascending=False).round(2)


def render_soccer_slate(league_name):
    cfg = SOCCER_LEAGUES[league_name]
    code, key = cfg["espn"], cfg["key"]
    try:
        teams = soccer_teams(code)
        players = soccer_players(code, teams)
    except Exception as e:
        st.error(f"Couldn't reach ESPN right now. Try refreshing in a minute. ({e})")
        return
    team_ids = tuple(t["id"] for t in teams)
    with st.spinner("Loading the fixtures..."):
        games = soccer_round(code, team_ids)
    if not games:
        st.markdown('<div class="muted">No upcoming matches found.</div>', unsafe_allow_html=True)
        return
    first = _local(games[0]["date"])
    section(f'{league_name} · {first.strftime("%b")} {first.day}', f"{len(games)} matches")

    def roster(tid):
        return [p for p in players if p["team_id"] == tid]

    # goal threats
    with st.spinner("Ranking goal threats..."):
        threats = []
        for g in games:
            for side, other in (("home", "away"), ("away", "home")):
                for t in club_threats(g[f"{side}_name"], g[f"{other}_name"], cfg, roster(g[f"{side}_id"]), limit=3):
                    threats.append({**t, "abbr": g[f"{side}_abbr"], "opp_abbr": g[f"{other}_abbr"]})
    threats = sorted(threats, key=lambda r: r["proj_xg"], reverse=True)[:10]
    section("Top goal threats", "xG per 90 × how much xG the opponent concedes")
    q_tag = ' <span class="status s-q">Q</span>'
    st.markdown("".join(
        f'<div class="sneaky"><div><b>{i}. {html.escape(t["name"])}</b> '
        f'<span class="muted">{html.escape(t["abbr"])} · vs {html.escape(t["opp_abbr"])}</span>'
        + (q_tag if t["questionable"] else "") + '<br>'
        + f'<span class="muted">{t["xg90"]:.2f} xG + {t["xa90"]:.2f} xA per 90 · {html.escape(t["opp_abbr"])} concede '
        f'{t["opp_xga"]:.2f} xG per game (avg {t["lg_xg"]:.2f}), {vs_avg(t["factor"])}</span></div>'
        + edge_pill(f'{t["proj_xg"]:.2f}', "proj xG") + '</div>' for i, t in enumerate(threats, 1))
        or '<div class="muted">Not enough data yet.</div>', unsafe_allow_html=True)

    # match cards + card environments
    def card_data(g):
        try:
            ref = espn_match(g["id"], code)["referee"]
        except Exception:
            ref = ""
        return g["id"], soccer_projection(g["home_name"], g["away_name"], cfg), ref
    with ThreadPoolExecutor(max_workers=8) as pool:
        info = {gid: (pr, ref) for gid, pr, ref in pool.map(card_data, games)}

    # card watch (Understat yellows per 90 x how card heavy the match projects)
    section("Card watch", "players who pick up yellows, in the most card heavy matches")
    card_rows = []
    for g in games:
        pr = info[g["id"]][0]
        if not pr or not pr["league_yellows"]:
            continue
        env = pr["total_yellows"] / pr["league_yellows"]
        for side, other in (("home", "away"), ("away", "home")):
            home = club_home_league(g[f"{side}_name"], cfg)
            if not home:
                continue
            outs = [p["name"] for p in roster(g[f"{side}_id"]) if status_class(p.get("inj", "")) == "s-out"]
            for p in us_players(home["us"]):
                if p["minutes"] < EPL_MIN_MINUTES or not same_team(p["team"].split(",")[-1], g[f"{side}_name"]):
                    continue
                if any(same_person(p["name"], n) for n in outs):
                    continue
                card_rows.append({**p, "name": roster_name(p["name"], roster(g[f"{side}_id"])),
                                  "score": p["yc90"] * env, "abbr": g[f"{side}_abbr"], "opp_abbr": g[f"{other}_abbr"],
                                  "proj_y": pr["total_yellows"], "lg_y": pr["league_yellows"]})
    card_rows = sorted(card_rows, key=lambda r: r["score"], reverse=True)[:8]
    st.markdown("".join(
        f'<div class="sneaky"><div><b>{i}. {html.escape(r["name"])}</b> '
        f'<span class="muted">{html.escape(r["abbr"])} · vs {html.escape(r["opp_abbr"])}</span><br>'
        f'<span class="muted">{r["yc90"]:.2f} yellows per 90 · match projects {r["proj_y"]:.1f} yellows (avg {r["lg_y"]:.1f})</span></div>'
        + edge_pill(f'{1 - math.exp(-r["score"]):.0%}', "card chance") + '</div>' for i, r in enumerate(card_rows, 1))
        or '<div class="muted">Not enough data yet.</div>', unsafe_allow_html=True)

    # shots on target leaders (reads match pages, so behind a button)
    section("Shots on target leaders", "shots on target per game × how many the opponent allows")
    go_key = f"{key}_sot_go"
    if not st.session_state.get(go_key):
        if st.button("🎯 Load shots on target leaders", key=f"{key}_sot_btn", type="primary"):
            st.session_state[go_key] = True
        st.caption("Reads each club's last 5 match pages, so the first load takes a minute. Then it's saved for hours.")
    if st.session_state.get(go_key):
        shooters = []
        with st.spinner("Reading match pages..."):
            for g in games:
                for side, other in (("home", "away"), ("away", "home")):
                    tid = g[f"{side}_id"]
                    try:
                        recent = [x["id"] for x in soccer_schedule(code, tid) if x["done"]][-5:]
                    except Exception:
                        recent = []
                    rows = []
                    for eid in recent:
                        try:
                            rows += [p for p in espn_match(eid, code)["players"] if same_team(p.get("club_name", ""), g[f"{side}_name"])]
                        except Exception:
                            pass
                    if not rows:
                        continue
                    df = pd.DataFrame(rows)
                    for c in ("SH", "SOT"):
                        if c not in df:
                            df[c] = 0
                    o, lg, _ = club_form(g[f"{other}_name"], cfg)
                    factor = o["sot_against"] / lg["sot_for"] if o and lg and lg.get("sot_for") else 1
                    outs = [p["name"] for p in roster(tid) if status_class(p.get("inj", "")) == "s-out"]
                    for name, grp in df.groupby("name"):
                        if len(grp) < 3 or grp["starter"].sum() < 2 or any(same_person(name, n) for n in outs):
                            continue
                        sot = grp["SOT"].fillna(0).mean()
                        if sot:
                            shooters.append({"name": name, "abbr": g[f"{side}_abbr"], "opp_abbr": g[f"{other}_abbr"],
                                             "sot": sot, "sh": grp["SH"].fillna(0).mean(), "apps": len(grp), "factor": factor,
                                             "opp_sot": o["sot_against"] if o else None, "lg_sot": lg["sot_for"] if lg else None})
        shooters = sorted(shooters, key=lambda r: r["sot"] * r["factor"], reverse=True)[:10]
        st.markdown("".join(
            f'<div class="sneaky"><div><b>{i}. {html.escape(r["name"])}</b> '
            f'<span class="muted">{html.escape(r["abbr"])} · vs {html.escape(r["opp_abbr"])}</span><br>'
            f'<span class="muted">{r["sot"]:.1f} on target, {r["sh"]:.1f} shots per game (last {r["apps"]})'
            + (f' · {html.escape(r["opp_abbr"])} allow {r["opp_sot"]:.1f} on target per game (avg {r["lg_sot"]:.1f}), '
               f'{vs_avg(r["factor"])}' if r["opp_sot"] else "") + '</span></div>'
            + edge_pill(f'{r["sot"] * r["factor"]:.1f}', "proj SOT") + '</div>' for i, r in enumerate(shooters, 1))
            or '<div class="muted">No shooting data found yet.</div>', unsafe_allow_html=True)

    # match cards
    section("Matches", "projected goals, corners and cards")
    cards = []
    for g in games:
        pr, ref = info[g["id"]]
        when = _local(g["date"])
        when_txt = f'{when.strftime("%a")} {when.strftime("%I:%M %p").lstrip("0")} ET' if when else ""
        meta, badge = [], ""
        if pr:
            meta += [f'Goals {sum(pr["goals"]):.1f} ({g["home_abbr"]} {pr["goals"][0]:.1f} · {g["away_abbr"]} {pr["goals"][1]:.1f})',
                     f'Corners {sum(pr["corners"]):.1f} · Yellows {pr["total_yellows"]:.1f}']
            if pr["total_yellows"] >= pr["league_yellows"] * (1 + EDGE):
                badge = '<span class="grade g-bad">CARD HEAVY</span>'
            elif sum(pr["goals"]) >= pr["league_goals"] * (1 + EDGE):
                badge = '<span class="grade g-good">GOALS EXPECTED</span>'
        if ref:
            meta.append(f"Ref: {html.escape(ref)}")
        color = next((t["color"] for t in teams if t["id"] == g["home_id"]), "475569")
        cards.append(f'<div class="game-card" style="border-top:5px solid #{color}">'
                     f'<div class="game-teams">{html.escape(g["home_abbr"])} <span>vs</span> {html.escape(g["away_abbr"])}</div>'
                     f'<div class="muted">{when_txt}</div>{badge}<div class="game-meta">{"<br>".join(meta)}</div></div>')
    st.markdown(f'<div class="game-grid">{"".join(cards)}</div>', unsafe_allow_html=True)

    # referees
    section("Referees", f"this season's {league_name} matches · sorted by yellows per game")
    ref_key = f"{key}_ref_go"
    if not st.session_state.get(ref_key):
        if st.button("🟨 Load referee stats", key=f"{key}_ref_btn"):
            st.session_state[ref_key] = True
        st.caption("Builds the table from every match page this season, so the first load takes a minute or two.")
    if st.session_state.get(ref_key):
        with st.spinner("Reading this season's match pages..."):
            refs = soccer_referee_table(code, team_ids)
        if refs.empty:
            st.markdown('<div class="muted">No referee data found yet.</div>', unsafe_allow_html=True)
        else:
            st.dataframe(refs, width="stretch")


def render_soccer(league_name):
    if league_name == "Premier League":
        render_epl()
        return
    key = SOCCER_LEAGUES[league_name]["key"]
    views = ["🔎 Player lookup", "📋 Slate"]
    view = st.segmented_control("View", views, default=views[0], label_visibility="collapsed",
                                key=f"{key}_view") or views[0]
    if view == views[1]:
        render_soccer_slate(league_name)
    else:
        render_soccer_player(league_name)


# ---------- THE WEBSITE ----------
SPORT_NAMES = list(SPORTS.keys()) + [SOCCER_KEY]
chosen = st.session_state.get("sport") or SPORT_NAMES[0]   # which sport (and league) is picked decides the theme
if chosen == SOCCER_KEY:
    theme = SOCCER_LEAGUES[st.session_state.get("soccer_league") or "Premier League"]["key"]
else:
    theme = SPORTS[chosen]["key"]
inject_css(theme)
banner(theme)

sport = st.segmented_control("Sport", SPORT_NAMES, default=SPORT_NAMES[0],
                             label_visibility="collapsed", key="sport")
if (sport or SPORT_NAMES[0]) == SOCCER_KEY:
    league = st.segmented_control("League", list(SOCCER_LEAGUES), default="Premier League",
                                  label_visibility="collapsed", key="soccer_league") or "Premier League"
    render_soccer(league)
else:
    render_sport(sport or SPORT_NAMES[0])

st.markdown('<div class="muted" style="margin-top:2rem">Data: ESPN public feeds, Fantasy Premier League, Understat, football-data.co.uk. '
            'Game logs refresh every 3 hours. For fun and research only.</div>', unsafe_allow_html=True)
