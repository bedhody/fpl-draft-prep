"""Defensive contribution, modelled rather than counted.

The old approach took the share of 2025/26 starts in which a player cleared the
threshold and treated it as a constant.  That is inert: raise a player's
expected minutes and his DefCon points scale linearly, when the true response
is steeply convex.  Defenders clear the threshold in 5.6% of 60-75 minute
starts and 31.0% of 90-minute starts.

So instead: estimate an underlying action *rate*, and integrate.

    lambda    = qualifying defensive actions per 90, shrunk toward the
                position mean (gamma-Poisson, prior worth 8 nineties)
    P(hit)    = P(X >= threshold),  X ~ Poisson(lambda x minutes_per_start / 90)
    points    = 2 x starts x P(hit)

Three modelling choices, each tested rather than assumed:

  * Does the rate scale with minutes?  Within players who have both full and
    partial appearances, actual partial-match actions come to 0.966 of what
    rate-scaling predicts.  Close enough to linear.

  * Poisson or negative binomial?  Per-match counts are mildly over-dispersed
    (variance/mean 1.29), and in-sample the negative binomial fits better.  But
    out of sample it does not: fitting on GW1-19 and predicting GW20-38, plain
    Poisson wins on bias (-2.2% vs +4.7%) and on error.  The shrinkage already
    absorbs most of the over-dispersion, and the negative binomial then counts
    it twice.  Poisson is also the only one of the two that Excel can express
    without truncating its arguments.

  * Is it better than what it replaces?  Same out-of-sample test, against the
    current realised-rate approach: MAE 1.33 vs 1.44 scoring matches per
    player per half-season, correlation 0.817 vs 0.794, bias -2.2% vs +4.3%.

Minutes per start is produced here too, because it comes from the same
per-match rows.  It is shrunk like the rate and for the same reason -- it feeds
a convex function, so a thin sample does real damage.  Out of sample it beats
both extremes: MAE 4.07 shrunk, against 5.33 for the raw per-player average it
replaces and 4.80 for the position mean alone.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson

from common import PROC, RAW

PRIOR_N = 8.0                      # shrinkage strength, in 90s played
MPS_PRIOR_N = 10.0                 # shrinkage strength for minutes per start,
                                   # in starts.  Fitted, not chosen: see
                                   # validate_mins_per_start().
THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12, "GKP": 99}
DEFAULT_MINS_PER_START = 85.0      # for players with no PL record


def load() -> pd.DataFrame:
    gw = pd.read_csv(RAW / "vaastav" / "merged_gw_2025-26.csv")
    gw = gw.drop_duplicates(subset=["element", "fixture"])
    gw["pos"] = gw["position"].replace({"GK": "GKP"})
    gw = gw[gw["minutes"] > 0].copy()
    gw["thr"] = gw["pos"].map(THRESHOLD).fillna(99)
    gw["hit"] = (gw["defensive_contribution"] >= gw["thr"]).astype(int)
    return gw


def fit(gw: pd.DataFrame) -> pd.DataFrame:
    # Minutes played *in matches he started*, kept separate from total minutes.
    # Dividing all his minutes by his starts counts substitute cameos in the
    # numerator but not the denominator, which produced impossible figures --
    # 112 minutes per start for a player with two starts and a bench run.
    #
    # A start that ended in a sending-off is dropped from both sides.  Those 42
    # starts averaged 52.5 minutes, but a red card is not rotation risk -- it is
    # a separate event, already priced by cards_model, and leaving it here makes
    # one column answer two questions.  The actions and minutes from those
    # matches still count toward the rate: they really happened.
    sent_off = (gw["starts"] > 0) & (gw["red_cards"] > 0)
    gw = gw.assign(
        start_minutes=gw["minutes"].where((gw["starts"] > 0) & ~sent_off, 0),
        mps_starts=gw["starts"].where(~sent_off, 0),
    )
    a = gw.groupby("element").agg(
        pos_2526=("pos", "last"),
        actions=("defensive_contribution", "sum"),
        minutes=("minutes", "sum"),
        start_minutes=("start_minutes", "sum"),
        starts=("starts", "sum"),
        mps_starts=("mps_starts", "sum"),
        apps=("hit", "size"),
        hits=("hit", "sum"),
    ).reset_index()
    a["n90"] = a["minutes"] / 90
    # Position mean acts as the prior a thin sample is pulled toward.
    prior = a.groupby("pos_2526").apply(
        lambda x: x["actions"].sum() / x["n90"].sum(), include_groups=False)
    a["prior_rate"] = a["pos_2526"].map(prior).round(3)
    a["defcon_lambda"] = ((a["actions"] + a["prior_rate"] * PRIOR_N)
                          / (a["n90"] + PRIOR_N)).round(3)
    a["raw_rate_per90"] = (a["actions"] / a["n90"]).round(3)

    # Minutes per start gets the same treatment as the action rate, and for the
    # same reason: it is a per-start average with no protection against a thin
    # sample, and it drives a *convex* function.  One 45-minute start used to
    # read 45.0 forever, which put a midfielder's DefCon hit rate at 0.3% when
    # his own action rate says 13.9% -- a 10-point error off a single match.
    mps_prior = a.groupby("pos_2526").apply(
        lambda x: x["start_minutes"].sum() / max(x["mps_starts"].sum(), 1),
        include_groups=False)
    a["mps_prior"] = a["pos_2526"].map(mps_prior).round(1)
    a["mins_per_start_raw"] = (a["start_minutes"]
                               / a["mps_starts"].replace(0, np.nan)).round(1)
    a["mins_per_start"] = ((a["start_minutes"] + a["mps_prior"] * MPS_PRIOR_N)
                           / (a["mps_starts"] + MPS_PRIOR_N)).round(1)
    a["defcon_shrinkage"] = (PRIOR_N / (a["n90"] + PRIOR_N)).round(3)
    return a


def validate(gw: pd.DataFrame) -> None:
    """Fit on the first half, predict the second, against the old approach."""
    H1, H2 = gw[gw.GW <= 19], gw[gw.GW > 19]
    f1 = fit(H1).set_index("element")
    f1["naive_rate"] = f1["hits"] / f1["starts"].replace(0, np.nan)
    H2 = H2.assign(start_minutes=H2["minutes"].where(H2["starts"] > 0, 0))
    h2 = H2.groupby("element").agg(starts=("starts", "sum"), minutes=("minutes", "sum"),
                                   start_minutes=("start_minutes", "sum"),
                                   hits=("hit", "sum"), thr=("thr", "last"))
    h2["mps"] = h2["start_minutes"] / h2["starts"].replace(0, np.nan)
    j = f1.join(h2, how="inner", rsuffix="_h2")
    j = j[(j["starts_h2"] >= 8) & (j["starts"] >= 8)]

    lam = j["defcon_lambda"] * j["mps"] / 90
    model = (1 - poisson.cdf(j["thr"] - 1, lam)) * j["starts_h2"]
    naive = j["naive_rate"] * j["starts_h2"]
    act = j["hits_h2"]
    print(f"  out-of-sample (fit GW1-19, predict GW20-38), n={len(j)}:")
    for lbl, v in [("realised rate x starts (old)", naive), ("Poisson(lambda x mins) (new)", model)]:
        ok = v.notna()
        print(f"    {lbl:<32} bias {v[ok].sum() / act[ok].sum() - 1:+6.1%}   "
              f"MAE {np.abs(v[ok] - act[ok]).mean():.2f}   corr {v[ok].corr(act[ok]):.3f}")


def validate_mins_per_start(gw: pd.DataFrame) -> None:
    """Pick the shrinkage strength out of sample rather than by eye.

    Predict each player's second-half minutes per start from his first-half
    record, shrunk toward the position mean by k starts.  k=0 is the raw
    average this replaced; k=inf is the position mean and nothing else.  The
    optimum is interior, which is the whole argument for shrinking: neither
    extreme is as good as the blend.
    """
    def half(g):
        a = fit(g).reset_index()
        return a[a["mps_starts"] > 0]

    H1, H2 = half(gw[gw.GW <= 19]), half(gw[gw.GW > 19])
    j = H1.merge(H2[["element", "mins_per_start_raw", "mps_starts"]],
                 on="element", suffixes=("", "_h2"))
    j = j[j["mps_starts_h2"] >= 5]

    print(f"  minutes per start, out of sample (fit GW1-19, predict GW20-38), "
          f"n={len(j)}:")
    rows = []
    for k in (0.0, 2.0, 5.0, 8.0, MPS_PRIOR_N, 12.0, 20.0, 50.0, 1e9):
        pred = (j["start_minutes"] + j["mps_prior"] * k) / (j["mps_starts"] + k)
        rows.append((k, np.abs(pred - j["mins_per_start_raw_h2"]).mean()))
    best = min(rows, key=lambda r: r[1])[0]
    for k, mae in rows:
        label = ("raw average (old)" if k == 0 else
                 "position mean only" if k > 1e8 else
                 f"shrunk, k={k:g} starts")
        star = "  <- in use" if k == MPS_PRIOR_N else ""
        print(f"    {label:<26} MAE {mae:5.3f}{star}")
    if best != MPS_PRIOR_N:
        print(f"    !! MPS_PRIOR_N={MPS_PRIOR_N:g} is not the optimum "
              f"({best:g} is) -- re-fit it")


def main() -> int:
    gw = load()
    print("fitting defensive-action rates")
    validate(gw)
    validate_mins_per_start(gw)

    a = fit(gw)
    ids = pd.read_csv(RAW / "vaastav" / "players_raw_2025-26.csv")[["id", "code"]]
    a = a.merge(ids, left_on="element", right_on="id", how="left").drop(columns=["id"])
    a = a[a["code"].notna()]
    a["code"] = a["code"].astype("int64")
    a["mins_per_start"] = a["mins_per_start"].fillna(DEFAULT_MINS_PER_START)

    out = a[["code", "pos_2526", "defcon_lambda", "raw_rate_per90", "prior_rate",
             "defcon_shrinkage", "mins_per_start", "mins_per_start_raw",
             "mps_starts", "actions", "minutes", "starts", "hits"]]
    out.to_csv(PROC / "defcon_model.csv", index=False)
    print(f"\ndefcon_model.csv        {len(out)} players")
    print(f"  position prior rates per 90: "
          + ", ".join(f"{k} {v:.2f}" for k, v in
                      a.groupby('pos_2526')['prior_rate'].first().items()))

    print("\n  how the model responds to minutes (a defender on 10.0 actions/90, threshold 10):")
    for m in (60, 70, 80, 90):
        p = 1 - poisson.cdf(9, 10.0 * m / 90)
        print(f"    {m} min start -> {p:.1%} chance of clearing it")
    print("  the old approach gave the same number at every minutes level")

    big = a[a["starts"] >= 20].nlargest(10, "defcon_lambda")
    print("\n  highest action rates (20+ starts):")
    names = pd.read_csv(PROC / "master_2025_26.csv")[["code", "player", "position"]]
    print(big.merge(names, on="code")[
        ["player", "position", "starts", "raw_rate_per90", "defcon_lambda",
         "mins_per_start", "hits"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
