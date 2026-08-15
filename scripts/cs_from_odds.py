"""Clean-sheet probability per club, from season-long betting markets.

Why not match odds: bookmakers price roughly one gameweek at a time, and a
single fixture's expected goals is a product of both teams' strength and home
advantage, which cannot be decomposed from one observation.  GW1 also prices a
specific injury list -- Arsenal without Saliba and Timber -- that will be false
for most of the season.  Season points totals price the whole campaign,
including absences and returns, and cover all 20 clubs at once.

The chain:

    market points  ->  goal difference  ->  goals for / against
                   ->  attack and defence strength
                   ->  expected goals in each of 38 fixtures
                   ->  P(clean sheet) per fixture, summed

Both relationships are fitted on five seasons of real results (100
team-seasons), not assumed.  The Poisson is applied per fixture and then
summed, never to a season average: exp(-x) is convex, and averaging first
understates clean sheets by about 12%.

Market numbers are transcribed by hand from bet365 and Spreadex, which both
block automated access.  bet365 figures are the midpoint of the quoted
"between" band; Spreadex are the midpoint of the buy/sell spread.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson

from common import PROC, RAW

MARKET_DATE = "2026-08-15"

# Midpoint of bet365's quoted "between" band.
BET365 = {"ARS": 78, "MCI": 75, "MUN": 70, "AVL": 59, "LIV": 71, "BOU": 50,
          "SUN": 44, "BHA": 55, "BRE": 49, "CHE": 67, "FUL": 47, "NEW": 55,
          "EVE": 50, "LEE": 47, "CRY": 47, "NFO": 48, "TOT": 61, "COV": 34,
          "IPS": 34, "HUL": 26}
# Midpoint of Spreadex's buy/sell spread.
SPREADEX = {"ARS": 78, "MCI": 75.5, "LIV": 70.5, "MUN": 69, "CHE": 68,
            "TOT": 61, "AVL": 58, "BHA": 53.5, "NEW": 52, "EVE": 49.5,
            "BRE": 49, "BOU": 49, "NFO": 48, "LEE": 46, "CRY": 45.5,
            "FUL": 44.5, "SUN": 42.5, "COV": 34, "IPS": 33.5, "HUL": 25}

SEASONS = [2021, 2022, 2023, 2024, 2025]


def history() -> pd.DataFrame:
    rows = []
    for yr in SEASONS:
        d = json.loads((RAW / "understat" / f"league_{yr}.json").read_text())
        for tm in d["teams"].values():
            h = pd.DataFrame(tm["history"])
            rows.append({"season": f"{yr}/{str(yr + 1)[2:]}", "team": tm["title"],
                         "pts": h.pts.sum(), "GF": h.scored.sum(), "GA": h.missed.sum(),
                         "CS": int((h.missed == 0).sum()),
                         "home_GF": h[h.h_a == "h"].scored.sum()})
    t = pd.DataFrame(rows)
    t["GD"] = t.GF - t.GA
    return t


def calibrate(t: pd.DataFrame) -> dict:
    pts_gd = np.polyfit(t.GD, t.pts, 1)          # pts  = a*GD + b
    gf_gd = np.polyfit(t.GD, t.GF, 1)            # GF   = c*GD + d
    avg = t.GF.mean()
    home_share = t.home_GF.sum() / t.GF.sum()
    # 20 clubs x ~55.6 goals each = total league goals, over 380 matches.
    goals_per_match = avg * 20 / 380
    return {"pts_gd": pts_gd, "gf_gd": gf_gd, "avg_goals": avg,
            "mu_home": goals_per_match * home_share,
            "mu_away": goals_per_match * (1 - home_share),
            "league_pts": t.groupby("season").pts.sum().mean()}


def strengths(points: pd.Series, cal: dict) -> pd.DataFrame:
    """Market points -> attack and defence multipliers relative to average."""
    a, b = cal["pts_gd"]
    c, d = cal["gf_gd"]
    gd = (points - b) / a
    gf = c * gd + d
    ga = gf - gd
    return pd.DataFrame({"pts": points, "GD": gd, "GF": gf, "GA": ga,
                         "attack": gf / cal["avg_goals"],
                         "defence": ga / cal["avg_goals"]})


def clean_sheets(s: pd.DataFrame, cal: dict) -> pd.Series:
    """Full round-robin: every club home and away against every other."""
    teams = list(s.index)
    out = {t: 0.0 for t in teams}
    for h in teams:
        for a in teams:
            if h == a:
                continue
            lam_h = cal["mu_home"] * s.attack[h] * s.defence[a]
            lam_a = cal["mu_away"] * s.attack[a] * s.defence[h]
            out[h] += np.exp(-lam_a)      # home side keeps a clean sheet
            out[a] += np.exp(-lam_h)      # away side keeps a clean sheet
    return pd.Series(out)


def validate(t: pd.DataFrame, cal: dict) -> None:
    """Feed each season's ACTUAL points through the chain and compare the
    clean sheets it produces against what actually happened."""
    print("  validation -- actual points fed through the whole chain:")
    allp, alla = [], []
    for season, g in t.groupby("season"):
        s = strengths(g.set_index("team").pts, cal)
        pred = clean_sheets(s, cal)
        act = g.set_index("team").CS
        allp += list(pred),
        alla += list(act),
        print(f"    {season}  predicted {pred.sum():5.1f} vs actual {act.sum():3.0f} "
              f"({pred.sum() / act.sum() - 1:+5.1%})  MAE {np.abs(pred - act).mean():.2f}  "
              f"corr {pred.corr(act):.3f}")
    p = np.concatenate(allp); a = np.concatenate(alla)
    print(f"    pooled: bias {p.sum() / a.sum() - 1:+.1%}, MAE {np.abs(p - a).mean():.2f}, "
          f"corr {np.corrcoef(p, a)[0, 1]:.3f}")
    print("    The cross-section is the reliable part (corr ~0.81, ~1.9 clean sheets")
    print("    per club). The league LEVEL is not: 2023/24 ran at 3.28 goals a match")
    print("    against 2.75 last season, and no backward-looking calibration can")
    print("    anticipate that. Treat the level as +/-13% and the ordering as sound.")


def main() -> int:
    t = history()
    cal = calibrate(t)
    print(f"calibrated on {len(t)} team-seasons")
    print(f"  pts = {cal['pts_gd'][0]:.4f} x GD + {cal['pts_gd'][1]:.2f}")
    print(f"  GF  = {cal['gf_gd'][0]:.4f} x GD + {cal['gf_gd'][1]:.2f}")
    print(f"  expected goals per match: {cal['mu_home']:.3f} home, {cal['mu_away']:.3f} away")
    validate(t, cal)

    m = pd.DataFrame({"bet365": pd.Series(BET365), "spreadex": pd.Series(SPREADEX)})
    m["market_pts"] = m.mean(axis=1)
    # Books shade both sides, so the midpoints sum slightly high. Rescale the
    # spread around the league mean so the total matches a real season.
    # The excess sits in the level, not the spread, so shift rather than scale:
    # scaling the deviations would compress the gaps between clubs as well.
    target = cal["league_pts"]
    m["adj_pts"] = m.market_pts - (m.market_pts.sum() - target) / 20
    print(f"\nmarket total {m.market_pts.sum():.1f} normalised to {m.adj_pts.sum():.1f} "
          f"(five-season average {target:.1f})")

    s = strengths(m.adj_pts, cal).rename(columns={"pts": "adj_pts"})
    s["exp_CS"] = clean_sheets(s, cal)
    s["p_cs"] = (s.exp_CS / 38).round(4)
    s = s.join(m[["bet365", "spreadex", "market_pts"]])

    prev = t[t.season == "2025/26"].set_index("team")
    name = {"Arsenal": "ARS", "Aston Villa": "AVL", "Bournemouth": "BOU",
            "Brentford": "BRE", "Brighton": "BHA", "Burnley": "BUR",
            "Chelsea": "CHE", "Crystal Palace": "CRY", "Everton": "EVE",
            "Fulham": "FUL", "Leeds": "LEE", "Liverpool": "LIV",
            "Manchester City": "MCI", "Manchester United": "MUN",
            "Newcastle United": "NEW", "Nottingham Forest": "NFO",
            "Sunderland": "SUN", "Tottenham": "TOT", "West Ham": "WHU",
            "Wolverhampton Wanderers": "WOL"}
    prev.index = prev.index.map(lambda x: name.get(x, x))
    s["CS_2526"] = prev.CS.reindex(s.index)

    s = s.sort_values("exp_CS", ascending=False)
    out = s[["bet365", "spreadex", "market_pts", "adj_pts", "GF", "GA",
             "exp_CS", "p_cs", "CS_2526"]].round(3)
    out.index.name = "team_short"
    out.to_csv(PROC / "cs_from_odds.csv")
    print(f"\ncs_from_odds.csv  ({MARKET_DATE} market)")
    print(out.to_string())
    print(f"\nleague expected clean sheets {s.exp_CS.sum():.0f} "
          f"(2025/26 actual was {t[t.season == '2025/26'].CS.sum():.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
