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
