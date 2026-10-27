# ==========================================================
# NBA MATCHUP LOOKUP
# Type a player + an opponent and see:
#   1. Every past game the player had against that team
#   2. His averages vs that team compared to his overall averages
#   3. His last 10 games (current form)
#   4. Optional "prop line" hit rate (how often he went over a number)
#   5. Today's injury report for both teams
#
# Data comes from ESPN's free (unofficial) public data feeds.
# Streamlit turns this Python file into a website automatically.
# ==========================================================

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

# ---------- SETTINGS YOU CAN CHANGE ----------
SEASONS_BACK = 4          # how many seasons of history to pull (4 = this season + 3 before)
LAST_N_GAMES = 10         # size of the "recent form" table
STATS_TO_SHOW = ["MIN", "PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "PRA"]
# ---------------------------------------------

st.set_page_config(page_title="Matchup Lookup", page_icon="🏀", layout="wide")

# ESPN addresses ("endpoints") we read data from
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
HEADERS = {"User-Agent": "Mozilla/5.0 (matchup lookup hobby app)"}


def get_json(url, params=None):
    """Download a page of data from ESPN and turn it into Python dictionaries."""
    response = requests.get(url, params=params, headers=HEADERS, timeout=15)
    response.raise_for_status()  # stops here if ESPN returned an error
    return response.json()


def current_season():
    """ESPN names NBA seasons by the year they END (the 2026/27 season = 2027).
    The season starts in October, so from October on we're in next year's season."""
    today = dt.date.today()
    return today.year + 1 if today.month >= 10 else today.year


# ---------- LOADING DATA ----------
# @st.cache_data remembers results for a while (ttl = seconds)
# so the app is fast and we don't spam ESPN with repeat requests.

@st.cache_data(ttl=24 * 3600)
def load_teams():
    data = get_json(f"{SITE}/teams")
    teams = []
    for entry in data["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        teams.append({"id": str(t["id"]), "name": t["displayName"], "abbr": t.get("abbreviation", "")})
    return sorted(teams, key=lambda t: t["name"])


def _load_one_roster(team):
    try:
        data = get_json(f"{SITE}/teams/{team['id']}/roster")
    except Exception:
        return []
    flat = []
    for a in data.get("athletes", []):
        # NBA rosters are a simple list; some sports group players by position
        if isinstance(a, dict) and "items" in a:
            flat.extend(a["items"])
        else:
            flat.append(a)
    return [
        {
            "id": str(a["id"]),
            "name": a.get("displayName") or a.get("fullName", ""),
            "team_id": team["id"],
            "team_abbr": team["abbr"],
        }
        for a in flat
        if isinstance(a, dict) and a.get("id")
    ]


@st.cache_data(ttl=12 * 3600)
def load_players(teams):
    # Grab all 30 rosters at the same time instead of one by one (much faster)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = pool.map(_load_one_roster, teams)
    players = [p for roster in results for p in roster]
    return sorted(players, key=lambda p: p["name"])


@st.cache_data(ttl=3 * 3600)
def load_gamelog(player_id, season):
    """Every game a player played in one season, one row per game."""
    try:
        data = get_json(f"{WEB}/athletes/{player_id}/gamelog", params={"season": season})
    except Exception:
        return pd.DataFrame()

    labels = data.get("labels") or []        # column names like PTS, REB, AST
    game_info = data.get("events") or {}     # date, opponent, result for each game id
    rows, seen = [], set()

    for season_type in data.get("seasonTypes") or []:
        type_name = (season_type.get("displayName") or "").lower()
        if "preseason" in type_name:
            continue  # preseason games don't count
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
                    "Season": f"{season - 1}/{str(season)[-2:]}",
                    "Type": "Playoffs" if "post" in type_name else "Regular",
                    "opp_id": str(opp.get("id", "")),
                    "Opp": opp.get("abbreviation") or opp.get("displayName", ""),
                    "H/A": "Home" if info.get("atVs") == "vs" else "Away",
                    "Result": f"{info.get('gameResult', '')} {info.get('score', '')}".strip(),
                }
                row.update(dict(zip(labels, stats)))
                rows.append(row)
    return pd.DataFrame(rows)


def clean_numbers(df):
    """ESPN sends stats as text. Turn them into numbers we can average."""
    df = df.copy()
    for col in ["MIN", "PTS", "REB", "AST", "STL", "BLK", "TO"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "3PT" in df:  # "3PT" looks like "4-9" (made-attempted); keep the made part
        df["3PM"] = pd.to_numeric(df["3PT"].astype(str).str.split("-").str[0], errors="coerce")
    if {"PTS", "REB", "AST"} <= set(df.columns):
        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return df


@st.cache_data(ttl=3 * 3600)
def load_history(player_id):
    """Combine several seasons into one table, newest game first."""
    season = current_season()
    seasons = [season - i for i in range(SEASONS_BACK)]
    with ThreadPoolExecutor(max_workers=SEASONS_BACK) as pool:
        frames = [f for f in pool.map(lambda s: load_gamelog(player_id, s), seasons) if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = clean_numbers(pd.concat(frames, ignore_index=True))
    return df.sort_values("Date", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=15 * 60)  # injuries refresh every 15 minutes
def load_injuries():
    data = get_json(f"{SITE}/injuries")
    by_team = {}
    for group in data.get("injuries", []):
        rows = []
        for item in group.get("injuries", []):
            athlete = item.get("athlete") or {}
            rows.append({
                "Player": athlete.get("displayName", ""),
                "Status": item.get("status", ""),
                "Details": item.get("shortComment") or item.get("longComment") or "",
                "Updated": (item.get("date") or "")[:10],
            })
        by_team[str(group.get("id", ""))] = rows
        by_team[group.get("displayName", "")] = rows  # backup lookup by team name
    return by_team


@st.cache_data(ttl=10 * 60)
def load_todays_games():
    data = get_json(f"{SITE}/scoreboard")
    games = []
    for event in data.get("events", []):
        comp = event["competitions"][0]
        sides = {c["homeAway"]: str(c["team"]["id"]) for c in comp["competitors"]}
        games.append({
            "home": sides.get("home"),
            "away": sides.get("away"),
            "label": event.get("shortName", ""),
            "status": event.get("status", {}).get("type", {}).get("shortDetail", ""),
        })
    return games


# ---------- THE WEBSITE ----------

st.title("🏀 NBA Matchup Lookup")
st.caption("Pick a player and an opponent to see head to head history, recent form, and injury reports.")

try:
    teams = load_teams()
    players = load_players(teams)
except Exception as e:
    st.error(f"Couldn't reach ESPN right now. Try refreshing in a minute. ({e})")
    st.stop()

team_by_id = {t["id"]: t for t in teams}

try:
    todays_games = load_todays_games()
except Exception:
    todays_games = []

# --- Search boxes ---
col1, col2 = st.columns(2)
with col1:
    player = st.selectbox(
        "Player",
        players,
        index=None,
        placeholder="Start typing a name, e.g. Cade Cunningham",
        format_func=lambda p: f"{p['name']} ({p['team_abbr']})",
    )

# If the player's team plays today, pre select tonight's opponent
tonight = None
if player:
    for g in todays_games:
        if player["team_id"] in (g["home"], g["away"]):
            opp_id = g["away"] if g["home"] == player["team_id"] else g["home"]
            tonight = {"opp_id": opp_id, **g}
            break

with col2:
    default_index = None
    if tonight:
        default_index = next((i for i, t in enumerate(teams) if t["id"] == tonight["opp_id"]), None)
    opponent = st.selectbox(
        "Opponent",
        teams,
        index=default_index,
        placeholder="Pick a team",
        format_func=lambda t: t["name"],
        key=f"opp_{player['id'] if player else 'none'}",
    )

if tonight:
    st.info(f"🗓️ Today: **{tonight['label']}** ({tonight['status']})")

if not player or not opponent:
    st.stop()

if opponent["id"] == player["team_id"]:
    st.warning("That's the player's own team. Pick a different opponent.")
    st.stop()

with st.spinner("Pulling game logs from ESPN..."):
    history = load_history(player["id"])

if history.empty:
    st.warning("No game log found for this player yet.")
    st.stop()

vs_opp = history[history["opp_id"] == opponent["id"]]
stats = [s for s in STATS_TO_SHOW if s in history.columns]

# --- Averages: vs opponent compared to all games ---
st.subheader(f"{player['name']} vs {opponent['name']}")
st.caption(f"Last {SEASONS_BACK} seasons · {len(vs_opp)} games vs {opponent['abbr']} · {len(history)} games total")

if vs_opp.empty:
    st.info("No games against this team in the time window.")
else:
    metric_cols = st.columns(len(stats))
    for col, stat in zip(metric_cols, stats):
        opp_avg = vs_opp[stat].mean()
        all_avg = history[stat].mean()
        col.metric(
            label=f"{stat} vs {opponent['abbr']}",
            value=f"{opp_avg:.1f}",
            delta=f"{opp_avg - all_avg:+.1f} vs avg",
        )
    st.caption("Green/red numbers = difference from his average across all games in the window.")

# --- Prop line hit rate (optional) ---
with st.expander("🎯 Check a line (hit rate)"):
    c1, c2 = st.columns(2)
    line_stat = c1.selectbox("Stat", stats, index=stats.index("PTS") if "PTS" in stats else 0)
    line_value = c2.number_input("Line", min_value=0.0, value=20.5, step=0.5)

    def hit_rate(df):
        played = df[line_stat].dropna()
        if played.empty:
            return "No games"
        overs = int((played > line_value).sum())
        return f"{overs}/{len(played)} over ({overs / len(played):.0%})"

    h1, h2, h3 = st.columns(3)
    h1.metric(f"vs {opponent['abbr']}", hit_rate(vs_opp))
    h2.metric(f"Last {LAST_N_GAMES}", hit_rate(history.head(LAST_N_GAMES)))
    h3.metric("This season", hit_rate(history[history["Season"] == history["Season"].iloc[0]]))

# --- Game tables ---
table_cols = ["Date", "Season", "Type", "Opp", "H/A", "Result"] + stats

st.subheader(f"Every game vs {opponent['abbr']}")
if not vs_opp.empty:
    st.dataframe(vs_opp[table_cols], hide_index=True, width="stretch")

st.subheader(f"Last {LAST_N_GAMES} games (any opponent)")
st.dataframe(history.head(LAST_N_GAMES)[table_cols], hide_index=True, width="stretch")

# --- Injury reports ---
st.subheader("🚑 Injury report")
try:
    injuries = load_injuries()
    player_team = team_by_id.get(player["team_id"], {"id": player["team_id"], "name": player["team_abbr"]})
    for col, team in zip(st.columns(2), [player_team, opponent]):
        with col:
            st.markdown(f"**{team['name']}**")
            rows = injuries.get(team["id"]) or injuries.get(team["name"]) or []
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.write("No reported injuries ✅")
except Exception:
    st.warning("Injury report is unavailable right now.")

st.divider()
st.caption("Data: ESPN public feeds. Injury report refreshes every 15 min, game logs every 3 hours. For fun and research only.")
