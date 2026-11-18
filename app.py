"""Data Finder V4: auditable, time-safe statistical forecast for the RESEARCH slate.

This is deliberately a separate model from the experimental 10,000-draw GAME
simulator.  The same ``forecast_values`` function is used for *live research*
and *historical walk-forward scoring*, so audit metrics actually relate to the
model being shown on the research slate.

Limitations: conditional on a player recording a game; no confirmed lineups,
inactive probability, opponent forecast, market line, or demonstrated edge.
The historical threshold is computed from PAST performance and is not a
sportsbook line. Nothing is claimed to be calibrated without real validation.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any
import numpy as np
import pandas as pd

MODEL_VERSION = "DF-4.0-research-1"
MIN_HISTORY = {"nba": 6, "nfl": 4}
MAX_HISTORY = {"nba": 24, "nfl": 18}
HALF_LIFE = {"nba": 6.0, "nfl": 4.0}
# Non-count per-game stat distributions; zeros are modeled explicitly.
NFL_YARDAGE = {"PASS YDS", "RUSH YDS", "REC YDS", "RUSH+REC YDS", "PASS+RUSH YDS"}
ZERO_PRIOR = {"PASS YDS": .02, "RUSH YDS": .12, "REC YDS": .18,
              "RUSH+REC YDS": .07, "PASS+RUSH YDS": .02}


def _clean(values: Any, sport: str) -> np.ndarray:
    if sport not in MIN_HISTORY:
        raise ValueError("Sport must be nba or nfl")
    x = pd.to_numeric(pd.Series(list(values)), errors="coerce").to_numpy(dtype=float)
    return x[np.isfinite(x) & (x >= 0)][-MAX_HISTORY[sport]:]


def fixed_seed(*parts: Any) -> int:
    text = "|".join(map(str, parts)).encode("utf8")
    return int.from_bytes(hashlib.sha256(text).digest()[:8], "big")


def illustrative_line(prior: Any, stat: str) -> float:
    """A reproducible synthetic threshold built using only PREVIOUS games."""
    x = pd.to_numeric(pd.Series(list(prior)), errors="coerce").dropna().to_numpy(dtype=float)
    if not len(x):
        return .5
    if stat in ("PASS TD", "REC TD", "RUSH TD", "INT", "ANYTIME TD"):
        return 1.5 if stat == "PASS TD" and float(x[-5:].mean()) >= 1.3 else .5
    return max(.5, float(math.floor(float(x[-5:].mean())) + .5))


def forecast_values(values: Any, sport: str, stat: str, line: float | None = None,
                    *, draw_count: int = 3000, seed: int = 100) -> dict | None:
    """Fit using chronologically ordered PAST player results only.

    Forecasts use recency-regularized mean and a conservative, empirically
    regularized variance; NBA/count outcomes use Poisson or Gamma-Poisson;
    NFL yards use a hurdle Gamma. No outcome or date after the cutoff enters.
    """
    x = _clean(values, sport)
    n = len(x)
    if n < MIN_HISTORY[sport]:
        return None
    if draw_count < 100:
        raise ValueError("draw_count must be at least 100")
    # Keep a substantial long-run component so two big games cannot dominate.
    ages = np.arange(n - 1, -1, -1)
    weight = np.exp(-math.log(2) * ages / HALF_LIFE[sport])
    recent_mean = float(np.average(x, weights=weight))
    long_mean = float(x.mean())
    mu = max(0.0, .60 * recent_mean + .40 * long_mean)
    # Uncertainty is regularized rather than pretending 4-6 games establish a distribution.
    empirical_var = float(np.var(x, ddof=1)) if n > 1 else 0.0
    count_stat = stat not in NFL_YARDAGE or sport == "nba"
    if count_stat:
        # Around 30%-40% per-game CV prior, with extra floor at low counts.
        floor_sd = max(.7, (0.35 if sport == "nba" else 0.50) * mu + (1 if sport == "nba" else .5))
        prior_var = max(mu, floor_sd * floor_sd)
        variance = (n * empirical_var + 8 * prior_var) / (n + 8)
        variance = max(mu, variance)
    else:
        floor_sd = max(6.0, 0.50 * mu + 3.0)
        prior_var = floor_sd * floor_sd
        variance = (n * empirical_var + 8 * prior_var) / (n + 8)

    rng = np.random.default_rng(seed)
    if count_stat:
        if mu <= 0:
            draws = np.zeros(draw_count, dtype=float)
        elif variance <= mu * 1.002:
            draws = rng.poisson(mu, size=draw_count).astype(float)
        else:
            shape = np.clip(mu * mu / (variance - mu), .08, 100000.0)
            lam = rng.gamma(shape=shape, scale=mu / shape, size=draw_count)
            draws = rng.poisson(lam).astype(float)
    else:
        # Yardage has more zero outcomes than a continuous Gamma can represent.
        prior_zero = ZERO_PRIOR.get(stat, .12)
        p_zero = (float((x <= 0).sum()) + 6.0 * prior_zero) / (n + 6.0)
        p_zero = float(np.clip(p_zero, 0.0, .90))
        positive_mean = mu / max(1.0 - p_zero, .1)
        positives = x[x > 0]
        pos_var = float(np.var(positives, ddof=1)) if len(positives) > 1 else 0.0
        prior_pos_var = max(16.0, .45 * positive_mean) ** 2
        pos_var = (len(positives) * pos_var + 8 * prior_pos_var) / (len(positives) + 8)
        cv = np.clip(math.sqrt(pos_var) / max(positive_mean, 1), .20, 1.1)
        shape = 1.0 / (cv * cv)
        draws = rng.gamma(shape=shape, scale=max(1e-8, positive_mean / shape), size=draw_count)
        draws[rng.random(draw_count) < p_zero] = 0
        draws = np.rint(draws)

    if line is None:
        line = illustrative_line(x, stat)
    if not np.isfinite(line) or line < 0:
        raise ValueError("line must be a nonnegative finite number")
    lo, med, hi = np.percentile(draws, [10, 50, 90])
    return {"version": MODEL_VERSION, "stat": stat, "n": n,
            "line": float(line), "projected_mean": float(mu),
            "simulated_mean": float(draws.mean()), "median": float(med),
            "p10": float(lo), "p90": float(hi),
            "p_over": float(np.mean(draws > line)),
            "p_under": float(np.mean(draws < line)), "p_push": float(np.mean(draws == line)),
            "history_average": float(long_mean)}


def walk_forward_backtest(games: pd.DataFrame, sport: str, stat: str,
                          *, player_id: str = "", player_name: str = "",
                          draws: int = 1800, max_tests: int = 60) -> pd.DataFrame:
    """Evaluate a **research forecast**, never using each game's own/future stats.

    Filters invalid and DNP rows. Target is conditional-on-playing. Dates are
    strictly ordered and duplicate event IDs are discarded. The illustrative
    threshold changes per event based on LAST FIVE PRIOR games alone.
    """
    if stat not in games or "Date" not in games:
        return pd.DataFrame()
    data = games.copy()
    if "Type" in data:
        data = data[data["Type"].astype(str).str.lower().eq("regular")].copy()
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce", utc=True)
    data[stat] = pd.to_numeric(data[stat], errors="coerce")
    data = data.dropna(subset=["Date", stat])
    data = data[data[stat] >= 0]
    if "event_id" in data:
        data = data.drop_duplicates(subset=["event_id"], keep="first")
    data = data.sort_values("Date", kind="stable").reset_index(drop=True)
    if data.empty:
        return pd.DataFrame()
    rows = []
    earliest = MIN_HISTORY[sport]
    for i in range(earliest, len(data)):
        date = data.at[i, "Date"]
        # Prevent same-day / future results from sneaking into the training set.
        prior = data.loc[data["Date"] < date, stat].to_numpy(dtype=float)
        if len(prior) < earliest:
            continue
        prior = prior[-MAX_HISTORY[sport]:]
        line = illustrative_line(prior, stat)
        seed = fixed_seed(MODEL_VERSION, sport, stat, player_id, date.isoformat())
        forecast = forecast_values(prior, sport, stat, line, seed=seed, draw_count=draws)
        if forecast is None:
            continue
        actual = float(data.at[i, stat])
        # Synthetic lines always end .5 so integer game stat values cannot push.
        if math.isclose(actual, line, abs_tol=1e-9):
            continue
        actual_over = int(actual > line)
        prior_hits = (prior > line).sum()
        # Comparable, leakage-free Laplace-smoothed historical frequency baseline.
        p_naive = float((prior_hits + 2) / (len(prior) + 4))
        p_model = forecast["p_over"]
        rows.append({"Date": date.date().isoformat(), "Player": player_name or player_id,
                     "Player ID": player_id, "Sport": sport.upper(), "Stat": stat,
                     "Prior games": len(prior), "Reference line": line,
                     "Forecast over": p_model, "History baseline over": p_naive,
                     "Actual": actual, "Over result": actual_over,
                     "Projected mean": forecast["projected_mean"],
                     "History mean": forecast["history_average"],
                     "P10": forecast["p10"], "P90": forecast["p90"],
                     "Inside 80%": int(forecast["p10"] <= actual <= forecast["p90"]),
                     "Brier": (p_model - actual_over) ** 2,
                     "Baseline Brier": (p_naive - actual_over) ** 2,
                     "Absolute error": abs(forecast["projected_mean"] - actual),
                     "Baseline absolute error": abs(forecast["history_average"] - actual)})
    return pd.DataFrame(rows[-max_tests:]) if rows else pd.DataFrame()


def audit_summary(frame: pd.DataFrame) -> dict:
    if frame is None or frame.empty:
        return {"n": 0}
    n = len(frame)
    y = frame["Over result"].astype(float).to_numpy()
    p = frame["Forecast over"].astype(float).to_numpy()
    b = frame["History baseline over"].astype(float).to_numpy()
    logloss = -np.mean(y * np.log(np.clip(p, 1e-5, 1 - 1e-5)) + (1 - y) * np.log(np.clip(1 - p, 1e-5, 1 - 1e-5)))
    return {"n": n, "players": frame["Player ID"].nunique(),
            "brier": float(np.mean((p - y) ** 2)),
            "baseline_brier": float(np.mean((b - y) ** 2)),
            "mae": float(frame["Absolute error"].mean()),
            "baseline_mae": float(frame["Baseline absolute error"].mean()),
            "interval_coverage": float(frame["Inside 80%"].mean()),
            "over_frequency": float(y.mean()), "mean_probability": float(p.mean()),
            "logloss": float(logloss)}


def calibration_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    df = frame.copy()
    breaks = np.linspace(0, 1, 6)
    df["Probability band"] = pd.cut(df["Forecast over"], breaks, include_lowest=True)
    groups = df.groupby("Probability band", observed=True)
    return groups.agg(Games=("Over result", "size"),
                      **{"Avg forecast": ("Forecast over", "mean"),
                         "Observed frequency": ("Over result", "mean")}).reset_index().assign(
        **{"Probability band": lambda x: x["Probability band"].astype(str)})


def clustered_brier_gain_interval(frame: pd.DataFrame, *, reps: int = 1200,
                                  seed: int = 4219) -> tuple[float, float] | None:
    """95% bootstrap CI for (naive Brier - model Brier), resampling PLAYERS.

    Resampling entire player series acknowledges correlations between rolling
    forecasts; the interval is exploratory (sample of current-roster players).
    """
    if frame is None or frame.empty or frame["Player ID"].nunique() < 3:
        return None
    g = frame.assign(gain=frame["Baseline Brier"] - frame["Brier"]).groupby("Player ID")
    by_player = g.agg(total_gain=("gain", "sum"), games=("gain", "size"))
    gains = by_player["total_gain"].to_numpy(dtype=float)
    games = by_player["games"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(gains), size=(max(100, int(reps)), len(gains)))
    delta = np.sum(gains[picks], axis=1) / np.maximum(1, np.sum(games[picks], axis=1))
    return tuple(float(x) for x in np.quantile(delta, [.025, .975]))
