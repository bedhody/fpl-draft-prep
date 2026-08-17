"""How wrong a minutes forecast is, measured rather than guessed.

The model gives every player one number for 2026/27 minutes.  A draft needs
two more: how bad it could reasonably get, and how good.  Those are not the
same question at every pick.  An early pick is a core player you cannot
replace, so what matters is his floor; a fifteenth-round pick costs almost
nothing to drop for somebody off the free pool, so what matters is his ceiling.
Ranking the whole board on the expected value answers neither.

The band is measured from four season-to-season pairs, 2022/23 through
2025/26.  For every player who was in the league in both seasons:

1. Fit what a reasonable forecast of next season's minutes would have been,
   from this season's minutes and the player's age.  Binned means, not a
   regression, because the shape is not linear -- minutes are censored at
   3,420 and the bottom of the range behaves nothing like the top.
2. Take the ratio of what actually happened to that forecast.
3. Read the 20th and 80th percentiles of that ratio, per bucket.

The dispersion is enormous and very uneven, which is the point of measuring it.
Conditioning on the player still being in the league is deliberate: everyone in
the draft pool is registered for 2026/27, so the risk of leaving the division
is not the risk being priced here.

The bands are checked out of sample -- fit on three pairs, test coverage on the
fourth -- because a band that claims 60% and delivers 40% is worse than no band.

Output: data/processed/minutes_risk.csv -- a low and high multiplier per
player, to be applied to whatever xMins he is being scored on.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import PROC

LO_Q, HI_Q = 0.20, 0.80
# Minutes buckets, chosen so each holds a few hundred player-seasons and so
# the boundaries mean something: 900 is roughly ten starts, 2,000 a regular,
# 2,800 close to ever-present.
MIN_EDGES = [0, 900, 1700, 2400, 2900, 3421]
AGE_EDGES = [0, 24, 29, 33, 99]
MIN_CELL = 40
SEASON_ORDER = ["2022/23", "2023/24", "2024/25", "2025/26"]
TODAY = pd.Timestamp("2026-08-17")


def pairs() -> pd.DataFrame:
    """One row per player per consecutive pair of seasons he appears in."""
    p = pd.read_csv(PROC / "panel.csv")
    p = p[["code", "season", "minutes", "element_type"]].dropna(subset=["code"])
    p["code"] = p["code"].astype("int64")
    p["si"] = p["season"].map({s: i for i, s in enumerate(SEASON_ORDER)})
    p = p.dropna(subset=["si"])

    nxt = p.copy()
    nxt["si"] = nxt["si"] - 1
    j = p.merge(nxt[["code", "si", "minutes"]], on=["code", "si"],
                suffixes=("", "_next"))

    m = pd.read_csv(PROC / "master_2025_26.csv", low_memory=False)
    birth = m.set_index("code")["birth_date"]
    j["age"] = j["code"].map(
        (TODAY - pd.to_datetime(birth, errors="coerce")).dt.days / 365.25)
    # Age at the time of the pair, not today.
    j["age"] = j["age"] - (len(SEASON_ORDER) - 1 - j["si"])
    j["mbucket"] = pd.cut(j["minutes"], MIN_EDGES, labels=False, right=False)
    j["abucket"] = pd.cut(j["age"], AGE_EDGES, labels=False, right=False)
    return j.dropna(subset=["mbucket", "abucket", "minutes_next"])


def fit_bands(j: pd.DataFrame):
    """Expected next-season minutes per cell, and the spread around it."""
    cell = j.groupby(["mbucket", "abucket"])
    exp = cell["minutes_next"].mean()
    n = cell.size()
    # Cells too thin to mean anything fall back to the minutes bucket alone.
    fallback = j.groupby("mbucket")["minutes_next"].mean()
    exp = exp.where(n >= MIN_CELL)
    exp = exp.fillna(pd.Series(
        {k: fallback.get(k[0], j["minutes_next"].mean()) for k in exp.index}))

    r = j.copy()
    r["expected"] = [exp.get((mb, ab), np.nan)
                     for mb, ab in zip(r["mbucket"], r["abucket"])]
    r["ratio"] = r["minutes_next"] / r["expected"].replace(0, np.nan)

    # Bucket the spread by the FORECAST, not by last season.  The band is
    # applied to a forecast, so it has to be indexed by one: a promoted club's
    # first-choice centre-half has no Premier League minutes behind him and
    # would otherwise be handed the band of a fringe player, which put six of
    # them on a ceiling of 5,400 minutes.
    r["ebucket"] = pd.cut(r["expected"], MIN_EDGES, labels=False, right=False)
    q = r.dropna(subset=["ebucket"]).groupby("ebucket")["ratio"].quantile(
        [LO_Q, HI_Q]).unstack()
    q.columns = ["lo", "hi"]
    return exp, q, r


def _clip_bucket(b: pd.Series, q: pd.DataFrame) -> pd.Series:
    return b.fillna(q.index.min()).clip(q.index.min(), q.index.max())


def coverage_check(j: pd.DataFrame) -> None:
    """Fit on the first three pairs, test the band on the last.

    A band is a claim about how often the truth lands inside it.  60% is
    claimed here; anything much under that and the floor is not a floor.
    """
    train, test = j[j.si >= 1], j[j.si == 0]
    if len(test) < 50 or len(train) < 100:
        print("  (not enough pairs for an out-of-sample coverage check)")
        return
    exp, q, _ = fit_bands(train)
    t = test.copy()
    t["expected"] = [exp.get((mb, ab), np.nan)
                     for mb, ab in zip(t["mbucket"], t["abucket"])]
    t = t.dropna(subset=["expected"])
    eb = _clip_bucket(pd.cut(t["expected"], MIN_EDGES, labels=False, right=False), q)
    t["lo"] = t["expected"] * eb.map(q["lo"])
    t["hi"] = t["expected"] * eb.map(q["hi"])
    t = t.dropna(subset=["lo", "hi"])
    inside = ((t["minutes_next"] >= t["lo"]) & (t["minutes_next"] <= t["hi"])).mean()
    below = (t["minutes_next"] < t["lo"]).mean()
    print(f"  out-of-sample coverage on {len(t)} players: "
          f"{inside:.1%} inside the band (claimed {HI_Q - LO_Q:.0%}), "
          f"{below:.1%} below the floor (claimed {LO_Q:.0%})")


def main() -> int:
    j = pairs()
    print(f"minutes risk from {len(j)} player-season pairs across "
          f"{j.si.nunique()} season transitions")
    coverage_check(j)

    exp, q, r = fit_bands(j)
    print(f"\n{'forecast minutes':<22}{'n':>6}{'floor':>8}{'ceiling':>9}"
          f"{'  what that means'}")
    labels = ["under 900", "900-1,700", "1,700-2,400", "2,400-2,900", "2,900+"]
    for b in range(len(MIN_EDGES) - 1):
        s = r[r.ebucket == b]
        if not len(s) or b not in q.index:
            continue
        print(f"  {labels[b]:<20}{len(s):>6}{q.loc[b, 'lo']:>8.2f}"
              f"{q.loc[b, 'hi']:>9.2f}")
    print("  read as multipliers on a forecast: 0.55 means one player in five "
          "lands at 55% of it or worse")

    # ---- apply to the 2026/27 pool ---------------------------------------
    m = pd.read_csv(PROC / "master_2025_26.csv", low_memory=False)
    out = pd.DataFrame({"code": m["code"], "player": m["player"]})
    age = (TODAY - pd.to_datetime(m["birth_date"], errors="coerce")).dt.days / 365.25
    # Bucket on the forecast the band will be applied to, falling back to last
    # season's minutes only where there is no forecast at all.
    fc = m.get("solio_season_xmins")
    fc = m["minutes"] if fc is None else fc.fillna(m["minutes"])
    # Binned means regress to the middle, so the fitted forecast never lands
    # in the top or bottom bucket and those two have no band of their own.
    # Clip to the nearest bucket that does rather than dropping to a default:
    # an ever-present belongs with the 2,400-2,900 band, not with a fallback.
    mb = _clip_bucket(pd.cut(fc.fillna(0), MIN_EDGES, labels=False, right=False), q)
    out["mins_lo_mult"] = mb.map(q["lo"]).round(3)
    out["mins_hi_mult"] = mb.map(q["hi"]).round(3)
    out["forecast_mins"] = fc.round(0)
    out["age"] = age.round(1)

    # The injury model reads a player's actual history of games missed.  It
    # covers 151 players, so it cannot set the band -- but where it exists it
    # moves the floor, since a man who has missed 15 games a season for three
    # seasons has a worse floor than his minutes bucket says.
    inj_path = PROC / "injury_model.csv"
    if inj_path.exists():
        inj = pd.read_csv(inj_path)[["code", "expected_games_missed_2627"]]
        out = out.merge(inj, on="code", how="left")
        g = out["expected_games_missed_2627"]
        med = g.median()
        # Relative to the median of the covered players, and capped, so one
        # unusual history cannot halve somebody's floor on its own.
        shift = ((med - g) / 38).clip(-0.15, 0.15)
        out["mins_lo_mult"] = (out["mins_lo_mult"] * (1 + shift)).round(3)
        n = int(g.notna().sum())
        print(f"\n  injury history moved the floor for {n} players "
              f"(median {med:.1f} games missed; range of the shift "
              f"{shift.min():+.2f} to {shift.max():+.2f})")
    else:
        out["expected_games_missed_2627"] = np.nan

    out["mins_lo_mult"] = out["mins_lo_mult"].fillna(0.55).clip(0.2, 1.0)
    out["mins_hi_mult"] = out["mins_hi_mult"].fillna(1.25).clip(1.0, 1.8)
    dest = PROC / "minutes_risk.csv"
    out.to_csv(dest, index=False)
    print(f"\nwrote {dest}  ({len(out)} players)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
