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
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

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


THEMES["epl"] = {
    "primary": "#37003C", "accent": "#E90052", "bg": "#F5F3F7", "edition": "PREMIER LEAGUE EDITION",
    "banner": "radial-gradient(circle at 85% 50%, transparent 62px, rgba(255,255,255,.85) 63px 66px, transparent 67px),"
              "linear-gradient(90deg, rgba(55,0,60,.75), rgba(55,0,60,0) 60%),"
              "repeating-linear-gradient(90deg, #3E8E41 0 80px, #357A38 80px 160px)",
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
.game-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:.8rem; }
.game-card { background:#fff; border:1px solid #E2E8F0; border-radius:12px; padding:.8rem 1rem; box-shadow:0 2px 8px rgba(15,23,42,.06); }
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
        if col in META_COLS or col in ("opp_id", "event_id"):
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
        last = html.escape(player["name"].split()[-1])
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
    return (f'<div class="ins-line">Usage {word}: {html.escape(player["name"].split()[-1])}\'s '
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
    """'Jacory Croskey-Merritt' and 'jacory croskey merritt' should match."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


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
    first = _local(games[0]["date"])
    title = f'{first.strftime("%A, %b")} {first.day}' if nba else f'Week of {first.strftime("%b")} {first.day}'
    section(f"Slate · {title}", f"{len(games)} games")

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
    st.markdown(f'<div class="game-grid">{"".join(cards)}</div>', unsafe_allow_html=True)
    st.markdown('<div class="muted" style="margin-top:.8rem">Open <b>Player lookup</b> for the full breakdown on anyone.</div>',
                unsafe_allow_html=True)


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
FD_URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
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
def fd_matches(code):
    r = requests.get(FD_URL.format(code=code), headers=HEADERS, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig", errors="replace")))
    df = df.dropna(subset=["HomeTeam", "AwayTeam"])
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df


def fd_all():
    """This season's matches plus last season's (for referees and early season team form)."""
    frames = []
    for ago in (0, 1):
        try:
            d = fd_matches(fd_season_code(ago))
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
def epl_team_table():
    """Each team's averages over its last 10 league matches, plus the league average."""
    long = fd_long(fd_all())
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


@st.cache_data(ttl=6 * 3600)
def espn_referee(home_name, away_name, kickoff_iso):
    """Best effort: the announced referee from ESPN's match page (PL names refs early in the week)."""
    when = _local(kickoff_iso)
    if not when:
        return ""
    base = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1"
    data = get_json(f"{base}/scoreboard", params={"dates": when.strftime("%Y%m%d")})

    def norm(x):
        return _norm(x).replace("united", "utd").replace("manchester", "man")

    for event in data.get("events", []):
        names = " ".join(norm((c.get("team") or {}).get("displayName", ""))
                         for c in (event.get("competitions") or [{}])[0].get("competitors", []))
        if norm(home_name)[:6] in names and norm(away_name)[:6] in names:
            summary = get_json(f"{base}/summary", params={"event": event.get("id")})
            for o in (summary.get("gameInfo") or {}).get("officials", []) or []:
                pos = ((o.get("position") or {}).get("name") or o.get("position") or "")
                if "referee" in str(pos).lower() and "assistant" not in str(pos).lower():
                    return o.get("displayName") or o.get("fullName") or ""
    return ""


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
        out.append({**p, "opp_abbr": opp["abbr"], "factor": factor, "threat": threat, "fixture": fx})
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
    played = hist[hist["MIN"].fillna(0) > 0]
    stats = [s for s in EPL_STATS.get(player["pos"], EPL_STATS["MID"]) if s in hist.columns]
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
                         f'{t["opp_abbr"]} defense conceding <span class="edge">+{t["factor"] - 1:.0%}</span> more than average.</div></div>')
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
    line_stats = [x for x in stats if x != "MIN"] + (["MIN"] if "MIN" in stats else [])
    line_stat = l1.selectbox("Stat", line_stats, key="epl_line_stat")
    avg = played.head(10)[line_stat].mean()
    suggested = 0.5 if line_stat in ("G", "A", "YC", "CS") or not avg or avg < 1 else float(int(avg)) + 0.5
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
                    f'{t["opp_abbr"]} concede {t["factor"]:.0%} of league average</span></div>'
                    f'<span class="edge">{t["threat"]:.2f}</span></div>')
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
        card_rows.append({**p, "score": p["yc90"] * env, "env": env, "opp_abbr": teams[opp_id]["abbr"]})
    card_rows = sorted(card_rows, key=lambda r: r["score"], reverse=True)[:8]
    st.markdown("".join(
        f'<div class="sneaky"><div><b>{i}. {html.escape(r["name"])}</b> '
        f'<span class="muted">{r["team_abbr"]} · {r["pos"]} · vs {r["opp_abbr"]}</span><br>'
        f'<span class="muted">{r["yc90"]:.2f} yellows per 90 · match projects {r["env"]:.0%} of a normal card count</span></div>'
        f'<span class="edge">{r["score"]:.2f}</span></div>' for i, r in enumerate(card_rows, 1))
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
    views = ["🔎 Player lookup", "📋 Fixtures & refs"]
    view = st.segmented_control("View", views, default=views[0], label_visibility="collapsed",
                                key="epl_view") or views[0]
    if view == views[1]:
        render_epl_fixtures()
    else:
        render_epl_player()


# ---------- THE WEBSITE ----------
SPORT_NAMES = list(SPORTS.keys()) + [EPL_KEY]
chosen = st.session_state.get("sport") or SPORT_NAMES[0]   # which sport is picked decides the theme
theme = "epl" if chosen == EPL_KEY else SPORTS[chosen]["key"]
inject_css(theme)
banner(theme)

sport = st.segmented_control("Sport", SPORT_NAMES, default=SPORT_NAMES[0],
                             label_visibility="collapsed", key="sport")
if (sport or SPORT_NAMES[0]) == EPL_KEY:
    render_epl()
else:
    render_sport(sport or SPORT_NAMES[0])

st.markdown('<div class="muted" style="margin-top:2rem">Data: ESPN public feeds, Fantasy Premier League, football-data.co.uk. '
            'Game logs refresh every 3 hours. For fun and research only.</div>', unsafe_allow_html=True)
