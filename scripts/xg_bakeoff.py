"""Which expected-goals model should you actually trust?

Two tests, both on the same players so the comparison is like-for-like.

  1. Same-season calibration.  Does the model's xG total match goals scored?
     A model can be well calibrated and still be a poor forecast, so this is a
     sanity check, not the answer.

  2. Out-of-sample forecast (the one that matters for a draft).  Take season
     N, predict goals per 90 in season N+1.  Compare Opta xG/90, Understat
     xG/90, a 50/50 blend, xGOT/90, and plain goals/90 as the baseline you
     have to beat.  Ranking quality (Spearman) matters more than absolute
     error for a draft board, so both are reported.

Repeated for assists, against both the Opta assist definition and the looser
FPL one (FPL is what actually pays points).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import OUT, PROC

MIN_MINUTES = 900          # ~10 full matches, in both seasons
SEASON_ORDER = ["2022/23", "2023/24", "2024/25", "2025/26"]


def per90(df, col, mins="minutes_pl"):
    return (df[col] / (df[mins] / 90)).replace([np.inf, -np.inf], np.nan)


def spearman(a, b):
    return pd.Series(a).corr(pd.Series(b), method="spearman")


def rmse(a, b):
    d = np.asarray(a, float) - np.asarray(b, float)
    return float(np.sqrt(np.nanmean(d ** 2)))


def mae(a, b):
    return float(np.nanmean(np.abs(np.asarray(a, float) - np.asarray(b, float))))


def load() -> pd.DataFrame:
    p = pd.read_csv(PROC / "panel.csv")
    p["minutes_pl"] = p["minutes_pl"].fillna(p["minutes"])
    p["xG_blend"] = p[["xG_opta", "us_xG"]].mean(axis=1)
    p["xA_blend"] = p[["xA_opta", "us_xA"]].mean(axis=1)
    for c in ["xG_opta", "us_xG", "us_npxG", "xG_blend", "fm_xGOT", "pl_goals",
              "xA_opta", "us_xA", "xA_blend", "goals", "goal_assist",
              "assists", "us_key_passes"]:
        if c in p.columns:
            p[f"{c}_p90"] = per90(p, c)
    return p


def calibration(p: pd.DataFrame) -> pd.DataFrame:
    """Same-season: how close is total xG to total goals?"""
    rows = []
    sub = p[p.minutes_pl >= MIN_MINUTES]
    for season in SEASON_ORDER:
        s = sub[sub.season == season]
        s = s[s.xG_opta.notna() & s.us_xG.notna()]
        for label, col in [("Opta (FPL)", "xG_opta"), ("Understat", "us_xG"),
                           ("50/50 blend", "xG_blend")]:
            rows.append({
                "season": season, "model": label, "n": len(s),
                "total_goals": s.goals.sum(), "total_xG": round(s[col].sum(), 1),
                "bias_%": round(100 * (s[col].sum() / s.goals.sum() - 1), 1),
                "player_MAE": round(mae(s[col], s.goals), 3),
            })
    return pd.DataFrame(rows)


def forecast(p: pd.DataFrame, *, targets, predictors, label: str) -> pd.DataFrame:
    """Season N predictors vs season N+1 outcome, pooled over all transitions."""
    rows = []
    for i in range(len(SEASON_ORDER) - 1):
        s0, s1 = SEASON_ORDER[i], SEASON_ORDER[i + 1]
        a = p[(p.season == s0) & (p.minutes_pl >= MIN_MINUTES)]
        b = p[(p.season == s1) & (p.minutes_pl >= MIN_MINUTES)]
        m = a.merge(b, on="code", suffixes=("_0", "_1")).copy()
        m["transition"] = f"{s0} -> {s1}"
        rows.append(m)
    joined = pd.concat(rows, ignore_index=True)

    out = []
    for tgt_label, tgt in targets.items():
        y = joined[f"{tgt}_1"]
        for pred_label, pred in predictors.items():
            x = joined[f"{pred}_0"]
            ok = x.notna() & y.notna()
            if ok.sum() < 50:
                continue
            # Scale-free: rank correlation, plus error after a least-squares
            # rescale so a model is not punished for a constant offset.
            xs, ys = x[ok], y[ok]
            slope = np.polyfit(xs, ys, 1)
            fitted = np.polyval(slope, xs)
            out.append({
                "target": tgt_label, "predictor": pred_label, "n": int(ok.sum()),
                "spearman": round(spearman(xs, ys), 4),
                "r2": round(np.corrcoef(xs, ys)[0, 1] ** 2, 4),
                "rmse_rescaled": round(rmse(fitted, ys), 4),
            })
    df = pd.DataFrame(out)
    df.insert(0, "test", label)
    return df, joined


def head_to_head(joined, target, a, b, *, n_boot=2000, seed=0):
    """Is model `a` really better than `b`, or is the gap sampling noise?

    Paired bootstrap over players: resample the same players for both models,
    recompute the rank correlation gap, and report how often `a` wins.
    """
    rng = np.random.default_rng(seed)
    cols = [f"{target}_1", f"{a}_0", f"{b}_0"]
    d = joined[cols].dropna()
    y, xa, xb = d[cols[0]].to_numpy(), d[cols[1]].to_numpy(), d[cols[2]].to_numpy()
    obs = spearman(xa, y) - spearman(xb, y)
    wins = 0
    gaps = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, len(d), len(d))
        g = spearman(xa[idx], y[idx]) - spearman(xb[idx], y[idx])
        gaps[i] = g
        wins += g > 0
    lo, hi = np.percentile(gaps, [2.5, 97.5])
    return {"n": len(d), "gap": round(obs, 4),
            "ci95": f"[{lo:+.4f}, {hi:+.4f}]",
            "wins_%": round(100 * wins / n_boot, 1)}


def main() -> None:
    p = load()

    cal = calibration(p)
    print("=" * 78)
    print("TEST 1 -- same-season calibration (players with 900+ minutes)")
    print("=" * 78)
    print(cal.to_string(index=False))

    goals, goals_joined = forecast(
        p, label="goals",
        targets={"goals/90 next season": "goals_p90"},
        predictors={
            "goals/90 (baseline)": "goals_p90",
            "Opta xG/90": "xG_opta_p90",
            "Understat xG/90": "us_xG_p90",
            "Understat npxG/90": "us_npxG_p90",
            "50/50 blend xG/90": "xG_blend_p90",
            "FotMob xGOT/90": "fm_xGOT_p90",
        })

    assists, assists_joined = forecast(
        p, label="assists",
        targets={"FPL assists/90 next season": "assists_p90",
                 "Opta assists/90 next season": "goal_assist_p90"},
        predictors={
            "FPL assists/90 (baseline)": "assists_p90",
            "Opta xA/90": "xA_opta_p90",
            "Understat xA/90": "us_xA_p90",
            "50/50 blend xA/90": "xA_blend_p90",
            "Understat key passes/90": "us_key_passes_p90",
        })

    print()
    print("=" * 78)
    print("TEST 2 -- forecasting next season (900+ minutes in BOTH seasons)")
    print("higher spearman/r2 = better;  lower rmse = better")
    print("=" * 78)
    for df in (goals, assists):
        for tgt, grp in df.groupby("target", sort=False):
            print(f"\n{tgt}   (n={grp.n.iloc[0]})")
            print(grp.drop(columns=["test", "target", "n"])
                     .sort_values("spearman", ascending=False)
                     .to_string(index=False))

    print()
    print("=" * 78)
    print("TEST 3 -- is the winner's margin real?  paired bootstrap, 2000 draws")
    print("=" * 78)
    h2h = []
    for tgt, joined, pairs in [
        ("goals/90", goals_joined,
         [("Opta xG", "xG_opta_p90", "Understat xG", "us_xG_p90"),
          ("Opta xG", "xG_opta_p90", "50/50 blend", "xG_blend_p90"),
          ("Opta xG", "xG_opta_p90", "FotMob xGOT", "fm_xGOT_p90"),
          ("Opta xG", "xG_opta_p90", "past goals", "goals_p90")]),
        ("FPL assists/90", assists_joined,
         [("50/50 blend xA", "xA_blend_p90", "Opta xA", "xA_opta_p90"),
          ("50/50 blend xA", "xA_blend_p90", "Understat xA", "us_xA_p90"),
          ("50/50 blend xA", "xA_blend_p90", "past assists", "assists_p90")]),
    ]:
        target = "goals_p90" if tgt == "goals/90" else "assists_p90"
        for a_lbl, a, b_lbl, b in pairs:
            r = head_to_head(joined, target, a, b)
            h2h.append({"target": tgt, "model_A": a_lbl, "model_B": b_lbl, **r})
    h2h = pd.DataFrame(h2h)
    print(h2h.to_string(index=False))
    print("\nwins_% is how often A beat B across resamples. Near 50% means the")
    print("two models are indistinguishable on this much data.")

    res = pd.concat([goals, assists], ignore_index=True)
    with pd.ExcelWriter(OUT / "xg_model_bakeoff.xlsx") as xl:
        cal.to_excel(xl, sheet_name="calibration", index=False)
        res.to_excel(xl, sheet_name="forecast", index=False)
        h2h.to_excel(xl, sheet_name="head_to_head", index=False)
    print(f"\nwritten: {OUT / 'xg_model_bakeoff.xlsx'}")


if __name__ == "__main__":
    main()
