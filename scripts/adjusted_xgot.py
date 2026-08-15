"""Adjusted xGOT — xG re-scaled by a player's own historic shot placement.

Idea: xG says how good the chance was.  xGOT says how good the shot was once
struck, so xGOT/xG is a measure of shot placement.  Some players beat 1.0
repeatedly; most sit near it.  Adjusted xGOT is the xGOT a player would post
for a given xG, using that player's own multi-season placement ratio:

    adjusted_xGOT = xG  x  placement_ratio

The ratio must be shrunk toward 1.0, or a striker with 40 minutes and one
well-placed shot gets a ratio of 3.  Shrinkage is done in xG units:

    placement_ratio = (sum xGOT + k) / (sum xG + k)

k is a pseudo-count of league-average xG.  A player with k xG of history sits
halfway between his own ratio and 1.0; with 4k he is 80% of the way to his own.
This is why no hard "minimum sample" cutoff is needed — small samples pull
themselves back to 1.0 automatically — but the raw sample size is reported so
the shrinkage can be seen rather than trusted.

The bottom of this file tests whether the column earns its place: does
adjusted xGOT from season N beat plain xG at predicting goals in N+1?
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import OUT, PROC

SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26"]
DEFAULT_K = 8.0          # chosen below; ~2 seasons of a regular starter's xG
MIN_MINUTES = 900


def placement_ratio(hist: pd.DataFrame, k: float) -> pd.DataFrame:
    """One row per player: shrunk xGOT/xG ratio over the supplied seasons."""
    g = hist.groupby("code").agg(
        hist_xG=("xG_opta", "sum"),
        hist_xGOT=("fm_xGOT", "sum"),
        hist_seasons=("season", "nunique"),
        hist_minutes=("minutes_pl", "sum"),
    ).reset_index()
    g["raw_ratio"] = (g["hist_xGOT"] / g["hist_xG"]).replace(
        [np.inf, -np.inf], np.nan)
    g["placement_ratio"] = (g["hist_xGOT"] + k) / (g["hist_xG"] + k)
    return g


def validate(panel: pd.DataFrame, ks=(2, 4, 6, 8, 12, 20, 40)) -> pd.DataFrame:
    """For each k and each season transition, compare predictors of next-season
    goals/90.  History for the ratio is strictly seasons <= N, so nothing from
    the season being predicted leaks in."""
    rows = []
    for k in ks:
        recs = []
        for i in range(1, len(SEASONS) - 1):     # need >=1 prior season of history
            s0, s1 = SEASONS[i], SEASONS[i + 1]
            hist = panel[panel.season.isin(SEASONS[: i + 1])]
            ratios = placement_ratio(hist, k)[["code", "placement_ratio"]]

            a = panel[(panel.season == s0) & (panel.minutes_pl >= MIN_MINUTES)]
            b = panel[(panel.season == s1) & (panel.minutes_pl >= MIN_MINUTES)]
            m = a.merge(b, on="code", suffixes=("_0", "_1")).merge(
                ratios, on="code", how="left")
            m["placement_ratio"] = m["placement_ratio"].fillna(1.0)
            m["adj_xGOT_0"] = m["xG_opta_0"] * m["placement_ratio"]
            recs.append(m)
        m = pd.concat(recs, ignore_index=True)
        n90_0 = m["minutes_pl_0"] / 90
        n90_1 = m["minutes_pl_1"] / 90
        y = m["goals_1"] / n90_1
        for label, x in [
            ("xG/90 (plain)", m["xG_opta_0"] / n90_0),
            ("adjusted xGOT/90", m["adj_xGOT_0"] / n90_0),
            ("raw xGOT/90", m["fm_xGOT_0"] / n90_0),
        ]:
            ok = x.notna() & y.notna()
            rows.append({"k": k, "predictor": label, "n": int(ok.sum()),
                         "spearman": round(x[ok].corr(y[ok], method="spearman"), 4),
                         "r2": round(np.corrcoef(x[ok], y[ok])[0, 1] ** 2, 4)})
    return pd.DataFrame(rows)


def main() -> int:
    panel = pd.read_csv(PROC / "panel.csv")
    panel["minutes_pl"] = panel["minutes_pl"].fillna(panel["minutes"])

    print("=" * 74)
    print("Does adjusted xGOT beat plain xG at predicting next-season goals/90?")
    print("=" * 74)
    v = validate(panel)
    piv = v.pivot_table(index="k", columns="predictor", values="spearman")
    print(piv.to_string())
    print("\n(raw xGOT and plain xG do not depend on k; they repeat as a control)")

    best = v[v.predictor == "adjusted xGOT/90"].sort_values("spearman", ascending=False)
    plain = v[v.predictor == "xG/90 (plain)"].spearman.iloc[0]
    print(f"\nplain xG/90:            {plain:.4f}")
    print(f"best adjusted xGOT/90:  {best.spearman.iloc[0]:.4f}  (k={best.k.iloc[0]})")
    verdict = "beats" if best.spearman.iloc[0] > plain else "does NOT beat"
    print(f"-> adjusted xGOT {verdict} plain xG on this sample")

    # Is that margin real, or noise?  Paired bootstrap at the chosen k.
    recs = []
    for i in range(1, len(SEASONS) - 1):
        s0, s1 = SEASONS[i], SEASONS[i + 1]
        ratios = placement_ratio(panel[panel.season.isin(SEASONS[: i + 1])],
                                 DEFAULT_K)[["code", "placement_ratio"]]
        a = panel[(panel.season == s0) & (panel.minutes_pl >= MIN_MINUTES)]
        b = panel[(panel.season == s1) & (panel.minutes_pl >= MIN_MINUTES)]
        recs.append(a.merge(b, on="code", suffixes=("_0", "_1"))
                     .merge(ratios, on="code", how="left"))
    m = pd.concat(recs, ignore_index=True)
    m["placement_ratio"] = m["placement_ratio"].fillna(1.0)
    n0, n1 = m["minutes_pl_0"] / 90, m["minutes_pl_1"] / 90
    d = pd.DataFrame({"y": m["goals_1"] / n1,
                      "plain": m["xG_opta_0"] / n0,
                      "adj": m["xG_opta_0"] * m["placement_ratio"] / n0}).dropna()
    rng = np.random.default_rng(0)
    gaps = np.empty(2000)
    for i in range(2000):
        s = d.iloc[rng.integers(0, len(d), len(d))]
        gaps[i] = (s["adj"].corr(s["y"], method="spearman")
                   - s["plain"].corr(s["y"], method="spearman"))
    lo, hi = np.percentile(gaps, [2.5, 97.5])
    print(f"   paired bootstrap (n={len(d)}): adjusted wins {100 * (gaps > 0).mean():.1f}% "
          f"of resamples, 95% CI [{lo:+.4f}, {hi:+.4f}]")

    # ---- production column: use every season of history available ---------
    ratios = placement_ratio(panel[panel.season.isin(SEASONS)], DEFAULT_K)
    latest = panel[panel.season == "2025/26"][
        ["code", "xG_opta", "fm_xGOT", "minutes_pl", "goals"]]
    out = latest.merge(ratios, on="code", how="left")
    out["placement_ratio"] = out["placement_ratio"].fillna(1.0)
    out["adjusted_xGOT"] = (out["xG_opta"] * out["placement_ratio"]).round(3)
    out["placement_ratio"] = out["placement_ratio"].round(4)
    out["raw_ratio"] = out["raw_ratio"].round(4)
    out = out[["code", "hist_seasons", "hist_minutes", "hist_xG", "hist_xGOT",
               "raw_ratio", "placement_ratio", "adjusted_xGOT"]]
    out.to_csv(PROC / "adjusted_xgot.csv", index=False)

    print(f"\nadjusted_xgot.csv      {len(out)} players  (k={DEFAULT_K})")
    top = out[out.hist_xG >= 15].nlargest(10, "placement_ratio")
    names = pd.read_csv(PROC / "master_2025_26.csv")[["code", "player", "position"]]
    print("\nbest placement (15+ xG of history):")
    print(top.merge(names, on="code")[
        ["player", "position", "hist_seasons", "hist_xG", "hist_xGOT",
         "raw_ratio", "placement_ratio", "adjusted_xGOT"]].to_string(index=False))
    print("\nworst placement (15+ xG of history):")
    print(out[out.hist_xG >= 15].nsmallest(8, "placement_ratio").merge(names, on="code")[
        ["player", "position", "hist_seasons", "hist_xG", "hist_xGOT",
         "raw_ratio", "placement_ratio", "adjusted_xGOT"]].to_string(index=False))

    with pd.ExcelWriter(OUT / "adjusted_xgot_validation.xlsx") as xl:
        v.to_excel(xl, sheet_name="k sweep", index=False)
        piv.to_excel(xl, sheet_name="summary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
