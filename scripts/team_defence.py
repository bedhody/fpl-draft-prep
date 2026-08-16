"""What each club concedes: goals, shots on target, and the FPL points both cost.

Two of FPL's nine scoring elements depend only on how much a club concedes, not
on the individual: 1 point per 3 saves for a goalkeeper, and -1 per 2 goals
conceded for a goalkeeper or defender.  Both are settled per match, which is
the whole difficulty -- two saves in a match are worth nothing, and one goal
conceded costs nothing.  Ignoring that makes goals conceded 57% too harsh and
saves 56% too generous.

FPL pays 1 point per 3 saves, awarded *per match*, and a save is simply a shot
on target that did not go in.  So the chain is:

    market points -> goals against  (cs_from_odds already does this)
    goals against / xG per shot on target faced  ->  shots on target faced
    shots on target faced - goals  ->  saves

The middle step is the interesting one.  Clubs do differ in the quality of the
shots they concede -- a side that funnels attackers into long-range efforts
faces more shots for the same expected goals than one that gives up tap-ins.
That difference is real but only partly repeatable: measured across 51
club-season pairs the year-on-year correlation of shot quality conceded is
+0.08, against +0.49 for shot volume.  So the club's own quality is shrunk
hard toward the league mean; QUALITY_WEIGHT was picked by back-test, not taste.

Goalkeeper shot-stopping skill is deliberately NOT modelled.  Goals prevented
per 90 has a year-on-year correlation of -0.03 over 33 repeat keeper-seasons,
which is noise.  Adding it would move save points by about a point a season
anyway, since a prevented goal is worth one extra save.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson

from common import PROC, RAW

# Two-season blend of a club's own shot quality, most recent first.
QUALITY_HISTORY_WEIGHTS = (0.6, 0.4)
# How much of the club's own quality to believe, the rest being the league mean.
# The back-test below is flat between 0.3 and 0.6 and clearly worse at 0 and 1.
QUALITY_WEIGHT = 0.4

SEASON_ORDER = ["2022/23", "2023/24", "2024/25", "2025/26"]


def team_history() -> pd.DataFrame:
    """Shots on target faced and expected goals against, per club per season.

    A shot on target faced is reconstructed as saves + goals conceded, summed
    over every goalkeeper the club used.  Own goals are counted here as shots
    on target and are not; they are 1.5% of goals, so the error is negligible.
    """
    s = pd.read_csv(PROC / "fpl_seasons.csv")
    tm = pd.read_csv(PROC / "fpl_team_seasons.csv")
    # FPL's `team` is a within-season id in alphabetical order, so it renumbers
    # whenever a club goes up or down. Resolve it against that season's table.
    s["club"] = s.set_index(["season", "team"]).index.map(
        tm.set_index(["season", "id"]).short_name)
    gk = s[s.element_type == 1]
    t = (gk.groupby(["season", "club"])
           .agg(saves=("saves", "sum"), goals=("goals_conceded", "sum"),
                xGA=("expected_goals_conceded", "sum"))
           .reset_index())
    t["sot"] = t.saves + t.goals
    t["xg_per_sot"] = t.xGA / t.sot
    t["yr"] = t.season.map({s_: i for i, s_ in enumerate(SEASON_ORDER)})
    return t


def quality_index(hist: pd.DataFrame, clubs, upto: int, weight: float) -> pd.Series:
    """Expected goals per shot on target faced, per club, shrunk to the mean.

    `upto` is the index of the most recent completed season to use.  Clubs with
    no Premier League history -- the promoted three -- get the league mean,
    which is the only defensible prior for them.
    """
    league = (hist[hist.yr == upto].xGA.sum() / hist[hist.yr == upto].sot.sum())
    num = den = 0.0
    for i, w in enumerate(QUALITY_HISTORY_WEIGHTS):
        h = hist[hist.yr == upto - i].set_index("club")
        num = num + w * h.xGA.reindex(clubs).fillna(0)
        den = den + w * h.sot.reindex(clubs).fillna(0)
    own = (num / den).where(den > 0, league).fillna(league)
    return league * (1 + weight * (own / league - 1)), league


def floor_points(lam, per: int):
    """E[floor(X / per)] for X ~ Poisson(lam): the FPL "1 point per `per`" rule.

    Both of FPL's counting rules settle every match, so the season total is not
    the season count divided by `per`: two saves in a match are worth nothing,
    and so is one goal conceded.  Over 2025/26 that per-match flooring cost
    keepers 36% of their naive save total -- 0.213 points per save, not 0.333 --
    and spared defenders 36% of their naive goals-conceded penalty, -0.32 per
    goal rather than -0.50.

    E[floor(X/m)] = sum over k>=1 of P(X >= m*k), a sum of Poisson tail
    probabilities, so the sheet can carry the same thing as a live formula.
    Eight terms is exact to 1e-4 at any realistic rate.
    """
    lam = np.asarray(lam, dtype=float)
    return sum(1 - poisson.cdf(per * k - 1, lam) for k in range(1, 9))


def validate(hist: pd.DataFrame) -> None:
    """Predict 2025/26 keeper save points using only 2022/23-2024/25.

    Expected goals against is fed in as known, because the question here is how
    well shots and save points are recovered FROM expected goals; how well the
    market predicts expected goals is cs_from_odds.py's back-test, not this one.
    """
    from common import team_code

    g = (pd.read_csv(RAW / "vaastav" / "merged_gw_2025-26.csv")
           .drop_duplicates(subset=["element", "fixture"]))
    k = g[(g.position == "GK") & (g.minutes > 0)].copy()
    k["club"] = k.team.map(team_code)
    k["pts"] = np.floor(k.saves / 3)          # the real per-match rule
    act = (k.groupby(["element", "club"])
             .agg(pts=("pts", "sum"), mins=("minutes", "sum"))
             .reset_index())
    act = act[act.mins >= 1350]

    tgt = hist[hist.yr == 3].set_index("club")
    q, _ = quality_index(hist[hist.yr < 3], tgt.index, upto=2,
                         weight=QUALITY_WEIGHT)
    lam = (tgt.xGA / q - tgt.xGA) / 38
    pred = (act.club.map(pd.Series(floor_points(lam, 3), index=lam.index))
            * act.mins / 90).values
    a = act.pts.values
    naive = np.full_like(a, a.sum() / act.mins.sum() * 90) * act.mins.values / 90
    bias = pred.sum() / a.sum() - 1
    print(f"\n  validation: 2025/26 save points for {len(act)} keepers on 1350+ minutes")
    print(f"    model  MAE {np.abs(pred - a).mean():4.2f}  corr "
          f"{np.corrcoef(pred, a)[0, 1]:+.3f}  bias {bias:+.1%}")
    print(f"    naive  MAE {np.abs(naive - a).mean():4.2f}  corr "
          f"{np.corrcoef(naive, a)[0, 1]:+.3f}         "
          f"(every keeper on the league average)")
    print(f"    model, level bias removed:  MAE "
          f"{np.abs(pred / (1 + bias) - a).mean():4.2f}")
    print("    Read this the same way as the clean-sheet model: the ordering is")
    print("    the reliable part, the league level is not. Save points here run")
    print("    high because shots per goal drifted -- 3.08 saves a match in")
    print("    2024/25 against 2.78 in 2025/26 -- and anchoring to the previous")
    print("    season makes it worse, not better (tested: bias +16.0%).")
    validate_gc(g)


def validate_gc(g: pd.DataFrame) -> None:
    """Goals-conceded points, given each club's real 2025/26 goals against.

    Unlike saves there is nothing to estimate in between -- goals against go
    straight into the Poisson -- so this checks the per-match flooring itself,
    and what it costs to skip it.
    """
    from common import team_code

    d = g[g.position.isin(["GK", "DEF"]) & (g.minutes > 0)].copy()
    d["club"] = d.team.map(team_code)
    d["pts"] = -np.floor(d.goals_conceded / 2)
    ga = d.groupby(["club", "fixture"]).goals_conceded.max().groupby("club").mean()
    act = (d.groupby(["element", "club"])
             .agg(pts=("pts", "sum"), mins=("minutes", "sum")).reset_index())
    act = act[act.mins >= 1800]
    per_match = pd.Series(-floor_points(ga, 2), index=ga.index)
    pred = (act.club.map(per_match) * act.mins / 90).values
    a = act.pts.values
    naive = np.full_like(a, a.sum() / act.mins.sum() * 90) * act.mins.values / 90
    linear = (act.club.map(-ga / 2) * act.mins / 90).values
    print(f"\n  validation: 2025/26 goals-conceded points for {len(act)} "
          f"keepers and defenders on 1800+ minutes")
    print(f"    model  MAE {np.abs(pred - a).mean():4.2f}  corr "
          f"{np.corrcoef(pred, a)[0, 1]:+.3f}  bias {pred.sum() / a.sum() - 1:+.1%}")
    print(f"    naive  MAE {np.abs(naive - a).mean():4.2f}  corr "
          f"{np.corrcoef(naive, a)[0, 1]:+.3f}         "
          f"(every player on the league average)")
    print(f"    -GA/2, no per-match floor:  MAE {np.abs(linear - a).mean():4.2f}  "
          f"bias {linear.sum() / a.sum() - 1:+.1%}  <- what skipping the floor costs")


def backtest(hist: pd.DataFrame) -> None:
    """Sweep the shrinkage weight, holding expected goals against known.

    Feeding in each season's real xGA isolates the question being asked -- how
    well club shot quality converts goals into shots -- from the separate
    question of how well the market predicts goals.
    """
    print("  back-test: predicting shots on target faced, xGA held known")
    print(f"    {'weight':>7} " + "".join(f"{SEASON_ORDER[y]:>11}" for y in (2, 3))
          + f"{'mean MAE':>11}")
    for w in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        maes = []
        for ty in (2, 3):
            tgt = hist[hist.yr == ty].set_index("club")
            q, _ = quality_index(hist[hist.yr < ty], tgt.index, ty - 1, w)
            maes.append(float(np.abs(tgt.xGA / q - tgt.sot).mean()))
        mark = "  <- used" if abs(w - QUALITY_WEIGHT) < 1e-9 else ""
        print(f"    {w:7.1f} " + "".join(f"{m:11.1f}" for m in maes)
              + f"{np.mean(maes):11.1f}{mark}")
    lag = hist.copy()
    lag["yr"] += 1
    j = hist.merge(lag[["club", "yr", "xg_per_sot", "sot"]], on=["club", "yr"],
                   suffixes=("", "_prev"))
    print(f"    year-on-year persistence over {len(j)} club-season pairs: "
          f"shot quality {np.corrcoef(j.xg_per_sot, j.xg_per_sot_prev)[0, 1]:+.2f}, "
          f"shot volume {np.corrcoef(j.sot, j.sot_prev)[0, 1]:+.2f}")


def main() -> int:
    hist = team_history()
    print(f"club shot quality from {len(hist)} club-seasons")
    backtest(hist)
    validate(hist)

    odds = pd.read_csv(PROC / "cs_from_odds.csv").set_index("team_short")
    q, league = quality_index(hist, odds.index, upto=len(SEASON_ORDER) - 1,
                              weight=QUALITY_WEIGHT)
    raw, _ = quality_index(hist, odds.index, upto=len(SEASON_ORDER) - 1, weight=1.0)

    out = pd.DataFrame({
        "GA": odds.GA,
        "ga_per_match": odds.GA / 38,
        "xg_per_sot_raw": raw,
        "xg_per_sot": q,
        "sot_per_match": odds.GA / q / 38,
        # A save is a shot on target that did not go in, so the goals have to
        # come back out before the Poisson sees it.
        "saves_per_match": (odds.GA / q - odds.GA) / 38,
    })
    out["save_pts_per_match"] = floor_points(out.saves_per_match, 3)
    out["gc_pts_per_match"] = -floor_points(out.ga_per_match, 2)
    out["seasons_of_history"] = [
        int(((hist.club == c) & (hist.yr >= len(SEASON_ORDER) - 2)).sum())
        for c in out.index]
    out.index.name = "team_short"
    out = out.sort_values("saves_per_match", ascending=False).round(4)
    out.to_csv(PROC / "team_defence.csv")
    print(f"\nleague mean xG per shot on target faced: {league:.4f}")
    print(out.to_string())
    print(f"\nleague shots on target faced per match: "
          f"{out.sot_per_match.mean():.2f} "
          f"(2025/26 actual {hist[hist.yr == 3].sot.sum() / 20 / 38:.2f}); "
          f"saves {out.saves_per_match.mean():.2f} "
          f"(actual {hist[hist.yr == 3].saves.sum() / 20 / 38:.2f})")
    print("Clubs with 0 seasons of history get the league mean, the only "
          "defensible prior for a promoted side.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
