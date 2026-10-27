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
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
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
            "team_id": team["id"],
            "team_abbr": team["abbr"],
        }
        for a in flat
        if isinstance(a, dict) and a.get("id")
    ]


@st.cache_data(ttl=12 * 3600)
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
                    "Date": (info.get("gameDate") or "")[:10],
                    "Season": season_text,
                    "Type": "Playoffs" if "post" in type_name else "Regular",
                    "opp_id": str(opp.get("id", "")),
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
        if col in META_COLS or col == "opp_id":
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().sum() >= 0.5 * df[col].notna().sum():  # mostly numbers? keep as numbers
            df[col] = converted

    def has(*cols):
        return all(c in df.columns and pd.api.types.is_numeric_dtype(df[c]) for c in cols)

    if sport_key == "nba":
        if "3PT" in df:  # "4-9" means 4 made out of 9
            df["3PM"] = pd.to_numeric(df["3PT"].astype(str).str.split("-").str[0], errors="coerce")
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
            "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "3PT"}
BOX_LABELS = {
    "passing": {"C/ATT": "C/ATT", "YDS": "PASS YDS", "TD": "PASS TD", "INT": "INT"},
    "rushing": {"CAR": "CAR", "YDS": "RUSH YDS", "TD": "RUSH TD"},
    "receiving": {"REC": "REC", "YDS": "REC YDS", "TD": "REC TD", "TGTS": "TGT", "TGT": "TGT"},
    "": {"MIN": "MIN", "PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "TO": "TO", "3PT": "3PT"},
}


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
        sides = [str((c.get("team") or {}).get("id", "")) for c in comp.get("competitors", [])]
        other = next((s for s in sides if s != team_id), "")
        games.append({"id": str(event.get("id", "")), "date": (event.get("date") or "")[:10], "other_id": other})
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
        if col in ("team_id", "pid", "Player", "pos", "C/ATT", "3PT"):
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    num = lambda c: df[c].fillna(0) if c in df else 0
    if sport_key == "nba":
        if "3PT" in df:
            df["3PM"] = pd.to_numeric(df["3PT"].astype(str).str.split("-").str[0], errors="coerce")
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


@st.cache_data(ttl=3 * 3600)
def recent_avgs(sport_name, player_id):
    """A player's per game averages this season (last season if he has barely played)."""
    cfg = SPORTS[sport_name]
    current = cfg["season_fn"]()
    best, last_played = pd.DataFrame(), ""
    for s in (current, current - 1):
        df = load_gamelog(cfg["web"], player_id, s, cfg["season_label"](s), cfg["use_names"])
        if not df.empty and not last_played:
            last_played = df["Date"].max()  # most recent game he actually played
        if len(df) > len(best):
            best = df
        if len(best) >= 3:
            break
    if best.empty:
        return {}, 0, ""
    df = clean_numbers(best, cfg["key"])
    return df.select_dtypes("number").mean().to_dict(), len(df), last_played


def teammate_slots(sport_name, players, team_id, out_names, maybe_names=frozenset()):
    """Figure out each healthy teammate's role (WR1, TE1, Guards...) from this season's numbers."""
    cfg = SPORTS[sport_name]
    nfl = cfg["key"] == "nfl"
    mates = [p for p in players if p["team_id"] == team_id and p["name"] not in out_names]
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
            if p["name"] not in maybe_names:
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
ACTIVE_LOOKBACK = {"nfl": 2, "nba": 3}  # must have played within his team's last N games


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


def build_insights(cfg, player, opponent, items, questionable, star_html=""):
    nba = cfg["key"] == "nba"
    opp = html.escape(opponent["abbr"])
    if not items and not star_html:
        return ""

    def who(slot):
        return f"starting {slot.lower()}" if nba else f"{slot}s"

    me = next((r for r in items if r["id"] == player["id"]), None)
    parts = []
    if me:
        e = me["edge"]
        grade = ("g-good", "GOOD MATCHUP") if e >= EDGE else ("g-bad", "TOUGH MATCHUP") if e <= -EDGE else ("g-mid", "NEUTRAL")
        last = html.escape(player["name"].split()[-1])
        word = STAT_WORDS.get(me["stat"], me["stat"])
        parts.append(f'<div class="insights-title">💡 Matchup insights <span class="grade {grade[0]}">{grade[1]}</span></div>')
        parts.append(
            f'<div class="ins-line">{opp} allows <b>{me["allowed"]:.1f} {word}</b> per game to {who(me["slot"])}. '
            f'{last} averages <b>{me["avg"]:.1f}</b>, so this spot is <b>{abs(e):.0%} {"above" if e >= 0 else "below"}</b> his norm.</div>'
        )
    else:
        parts.append('<div class="insights-title">💡 Matchup insights</div>')
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
        q = " <span class='status s-q'>Q</span>" if r["name"] in questionable or r.get("returning") else ""
        parts.append(
            f'<div class="sneaky"><div><b>{html.escape(r["name"])}</b> ({r["slot"]}){q}<br>'
            f'<span class="muted">{opp} allows {r["allowed"]:.1f} {STAT_WORDS.get(r["stat"], r["stat"])} to '
            f'{who(r["slot"])} vs his {r["avg"]:.1f} avg</span></div><span class="edge">+{r["edge"]:.0%}</span></div>'
        )
    parts.append(f'<div class="muted" style="margin-top:.7rem">Based on {opp}\'s last {DVP_GAMES[cfg["key"]]} games '
                 f'and each player\'s season averages. Trends, not guarantees.</div>')
    return f'<div class="insights">{"".join(parts)}</div>'


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
    s = status.lower()
    if "out" in s or "injured reserve" in s or s == "ir":
        return "s-out"
    if "doubt" in s:
        return "s-doubt"
    if "question" in s or "day" in s:
        return "s-q"
    return "s-ok"


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


def hero(player, my_team, opponent, games_vs, cfg):
    c1, c2 = my_team["color"], opponent["color"]
    pos = f'{player["pos"]} · ' if player["pos"] else ""
    st.markdown(
        f'<div class="hero" style="background:linear-gradient(110deg,#{c1} 0%,#{c1} 40%,#{c2} 60%,#{c2} 100%)">'
        f'<div class="hero-side"><div class="hero-abbr">{html.escape(my_team["abbr"])}</div>'
        f'<div class="hero-name">{html.escape(player["name"])}</div>'
        f'<div class="hero-sub">{html.escape(pos + my_team["name"])}</div></div>'
        f'<div class="hero-vs">VS</div>'
        f'<div class="hero-side right"><div class="hero-abbr">{html.escape(opponent["abbr"])}</div>'
        f'<div class="hero-name">{html.escape(opponent["name"])}</div>'
        f'<div class="hero-sub">{games_vs} games in the last {cfg["seasons_back"]} seasons</div></div></div>',
        unsafe_allow_html=True,
    )


# ---------- ONE SPORT'S PAGE ----------
def render_sport(sport_name):
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

    # Averages vs this opponent
    section(f"Averages vs {opponent['abbr']}", f"compared to his average across all {len(history)} games")
    if vs_opp.empty:
        st.markdown('<div class="muted">No games against this team in the time window.</div>', unsafe_allow_html=True)
    else:
        stat_cards(vs_opp, history, stats)

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
            mine = (f'<div class="muted">{html.escape(player["name"].split()[-1])} avg: {history[s].mean():.1f}</div>'
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
    out_names = {r["player"] for r in team_inj if status_class(r["status"]) == "s-out"}
    questionable = {r["player"] for r in team_inj if status_class(r["status"]) != "s-out"}  # Q, Doubtful, Probable
    dvp_cache = {slot: dvp}

    def dvp_for_slot(s):
        if s not in dvp_cache:
            dvp_cache[s] = defense_vs_position(cfg, opponent["id"], s, pos_by_id, team_by_id)
        return dvp_cache[s]

    # The other side of the game: the opponent's players vs OUR player's team defense
    opp_inj = injuries.get(opponent["id"]) or injuries.get(opponent["name"]) or []
    opp_out = {r["player"] for r in opp_inj if status_class(r["status"]) == "s-out"}
    opp_maybe = frozenset(r["player"] for r in opp_inj if status_class(r["status"]) != "s-out")
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
                                          star_card(cfg, star, team_by_id))
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


# ---------- THE WEBSITE ----------
SPORT_NAMES = list(SPORTS.keys())
chosen = st.session_state.get("sport") or SPORT_NAMES[0]   # which sport is picked decides the theme
theme = SPORTS[chosen]["key"]
inject_css(theme)
banner(theme)

sport = st.segmented_control("Sport", SPORT_NAMES, default=SPORT_NAMES[0],
                             label_visibility="collapsed", key="sport")
render_sport(sport or SPORT_NAMES[0])

st.markdown('<div class="muted" style="margin-top:2rem">Data: ESPN public feeds. '
            'Game logs refresh every 3 hours. For fun and research only.</div>', unsafe_allow_html=True)
