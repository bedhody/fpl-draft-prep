"""Weight the end of a season more heavily than the start -- but only where
the change is bigger than the noise.

The objection to recency weighting is that it throws away sample for nothing.
Halve a player's minutes and you double the standard error on every rate he
has; if his role never changed, all you have bought is a worse estimate of the
same number.  That objection is right for most players, so the question is not
"how much should recency count" but "for whom".

The test is a z-score.  For each rate, split a player's season into two halves
of equal *minutes* -- not equal calendar, because a man who missed until
February has no first half -- and ask how far apart the two halves are relative
to how far apart they would be by chance:

    z = (late - early) / SE(late - early)

The standard error is not assumed.  It is measured: for a rate that is pure
noise, the variance of (late - early) across players falls as 1/n, so the
noise level is fitted by regressing the observed squared gap on 1/n90 over
every player in the pool.  That gives an SE that already knows a 400-minute
player cannot generate evidence a 3,000-minute player can.

The weight then comes straight off the normal:

    w = 2*Phi(|z|) - 1        the two-sided confidence that the halves differ
    estimate = w*late + (1-w)*pooled

At z = 0 the player keeps his whole season, which is what he should keep.  At
z = 2 he is 95% on the late half.  Nothing is thresholded, so nobody falls off
a cliff between 1.95 and 2.05 -- the thresholds in this file are only for
*flagging*, which is a different job: a flag says "go and find out why", and
for that a hard line is what you want.

Whether any of this helps is not assumed either.  `validate()` builds both
estimates from one season and scores them against the next, twice
(2023/24 -> 2024/25 and 2024/25 -> 2025/26).  If recency weighting does not
beat using the whole season, that is printed and the multipliers ship at 1.0.

Output: data/processed/recency.csv -- a multiplier on xG/90 and xA/90 and on
the DefCon action rate, the z-score behind each, and a research flag.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

from common import PROC, RAW

# Flagging thresholds, from the normal.  1.96 is the usual two-sided 5%; the
# second condition stops a statistically clean but tiny move from generating
# work.  A flag costs a human twenty minutes, so it has to earn them.
FLAG_Z = 1.96
FLAG_REL = 0.30                 # and at least a 30% move in the rate
MIN_N90 = 6.0                   # below this there is no split worth taking
SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]

# (column in the gameweek file, name, whether the season file has it at all)
METRICS = [
    ("expected_goals", "xg"),
    ("expected_assists", "xa"),
    ("defensive_contribution", "dc"),
]


def load_season(season: str) -> pd.DataFrame | None:
    p = RAW / "vaastav" / f"merged_gw_{season}.csv"
    if not p.exists():
        return None
    g = pd.read_csv(p).drop_duplicates(subset=["element", "fixture"])
    g = g[g["minutes"] > 0].copy()
    if "kickoff_time" in g.columns:
        g = g.sort_values(["element", "kickoff_time"])
    else:
        g = g.sort_values(["element", "GW"])
    for col, _ in METRICS:
        if col not in g.columns:
            g[col] = np.nan
    return g


def split_halves(g: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Each player's season cut at the match where he passes half his minutes.

    Cut on minutes rather than on gameweek 19 because the point of the split is
    to compare two comparable samples.  A player who was injured until February
    has all his football in the second half of the calendar and none of the
    question this file is asking.
    """
    g = g.copy()
    cum = g.groupby("element")["minutes"].cumsum()
    tot = g.groupby("element")["minutes"].transform("sum")
    g["half"] = np.where(cum <= tot / 2, "early", "late")

    w = g.pivot_table(index="element", columns="half",
                      values=["minutes", metric], aggfunc="sum").fillna(0)
    out = pd.DataFrame({
        "element": w.index,
        "m_early": w[("minutes", "early")] if ("minutes", "early") in w else 0.0,
        "m_late": w[("minutes", "late")] if ("minutes", "late") in w else 0.0,
        "v_early": w[(metric, "early")] if (metric, "early") in w else 0.0,
        "v_late": w[(metric, "late")] if (metric, "late") in w else 0.0,
    }).reset_index(drop=True)
    out["n_early"] = out["m_early"] / 90
    out["n_late"] = out["m_late"] / 90
    out = out[(out.n_early >= MIN_N90 / 2) & (out.n_late >= MIN_N90 / 2)]
    out["r_early"] = out["v_early"] / out["n_early"]
    out["r_late"] = out["v_late"] / out["n_late"]
    out["r_pooled"] = ((out["v_early"] + out["v_late"])
                       / (out["n_early"] + out["n_late"]))
    return out


def noise_scale(h: pd.DataFrame) -> float:
    """Fit the per-90 sampling variance of a rate: Var(rate) = k * rate / n90.

    Poisson would put k at 1.  Real football is not Poisson -- a striker's xG
    arrives in lumps -- so k is estimated instead, from the identity

        E[(late - early)^2] = k * pooled * (1/n_late + 1/n_early)

    over every player at once, which is a one-parameter fit against a few
    hundred observations.  Nothing here assumes the split is meaningless: if
    real role changes are common the fitted k comes out high, the standard
    errors widen, and the method flags fewer players.  That is the conservative
    direction and it is the right one.
    """
    d = h[(h.r_pooled > 0)]
    if len(d) < 30:
        return 1.0
    x = d["r_pooled"] * (1 / d["n_late"] + 1 / d["n_early"])
    y = (d["r_late"] - d["r_early"]) ** 2
    return float(max((x * y).sum() / (x * x).sum(), 1e-6))


def weight_halves(h: pd.DataFrame, k: float) -> pd.DataFrame:
    h = h.copy()
    se = np.sqrt(k * h["r_pooled"] * (1 / h["n_late"] + 1 / h["n_early"]))
    h["z"] = ((h["r_late"] - h["r_early"]) / se.replace(0, np.nan)).fillna(0)
    # Two-sided confidence that the halves are genuinely different, straight
    # off the normal.  Smooth by construction: no player changes category
    # because he crossed a line by a hundredth.
    h["w"] = 2 * norm.cdf(h["z"].abs()) - 1
    h["r_recency"] = h["w"] * h["r_late"] + (1 - h["w"]) * h["r_pooled"]
    h["mult"] = np.where(h["r_pooled"] > 0, h["r_recency"] / h["r_pooled"], 1.0)
    return h


# --------------------------------------------------------------------------
def within_season_fold(g: pd.DataFrame, col: str, cut: float = 0.6):
    """Train on the first 60% of a player's minutes, score on the last 40%.

    The only holdout available for DefCon, which exists in one season of data
    and so has no next season to be tested against.  It is a harder test than
    the season-to-season one -- less data on both sides -- but it asks exactly
    the right question: does the recent part of what I have seen beat all of
    what I have seen at predicting what comes next?
    """
    g = g.copy()
    cum = g.groupby("element")["minutes"].cumsum()
    tot = g.groupby("element")["minutes"].transform("sum")
    train, test = g[cum <= tot * cut], g[cum > tot * cut]
    h = split_halves(train, col)
    if len(h) < 30:
        return None
    h = weight_halves(h, noise_scale(h))
    t = test.groupby("element").agg(v=(col, "sum"), m=("minutes", "sum"))
    t = t[t.m >= 450]
    t["truth"] = t["v"] / (t["m"] / 90)
    j = h.merge(t["truth"], on="element")
    if len(j) < 30:
        return None
    return (len(j), float((j["r_pooled"] - j["truth"]).abs().mean()),
            float((j["r_recency"] - j["truth"]).abs().mean()))


def validate() -> dict[str, bool]:
    """Build both estimates from one season, score them against the next.

    Returns, per metric, whether recency weighting actually won.  A metric that
    did not win ships a multiplier of 1.0 -- the whole point of measuring is to
    be allowed to say no.
    """
    print("out-of-sample test: build on one season, score against the next")
    print(f"  {'metric':<6}{'fold':<20}{'n':>5}{'pooled':>10}{'recency':>10}"
          f"{'winner':>10}")
    verdict: dict[str, bool] = {}

    for col, name in METRICS:
        wins, rows = 0, 0
        for a, b in zip(SEASONS, SEASONS[1:]):
            ga, gb = load_season(a), load_season(b)
            if ga is None or gb is None or ga[col].isna().all() or gb[col].isna().all():
                continue
            h = split_halves(ga, col)
            if len(h) < 30:
                continue
            h = weight_halves(h, noise_scale(h))

            nxt = gb.groupby("element").agg(v=(col, "sum"), m=("minutes", "sum"))
            nxt = nxt[nxt.m >= 900]
            nxt["truth"] = nxt["v"] / (nxt["m"] / 90)
            j = h.merge(nxt["truth"], on="element")
            if len(j) < 30:
                continue
            e_pool = float((j["r_pooled"] - j["truth"]).abs().mean())
            e_rec = float((j["r_recency"] - j["truth"]).abs().mean())
            better = e_rec < e_pool
            wins += better
            rows += 1
            print(f"  {name:<6}{a + ' -> ' + b:<20}{len(j):>5}{e_pool:>10.4f}"
                  f"{e_rec:>10.4f}{('recency' if better else 'pooled'):>10}")
        # The within-season holdout, which is the only one DefCon can sit.
        g = load_season("2025-26")
        if g is not None and not g[col].isna().all():
            r = within_season_fold(g, col)
            if r:
                n, e_pool, e_rec = r
                better = e_rec < e_pool
                wins += better
                rows += 1
                print(f"  {name:<6}{'25/26 first 60->last 40':<20}{n:>5}"
                      f"{e_pool:>10.4f}{e_rec:>10.4f}"
                      f"{('recency' if better else 'pooled'):>10}")
        verdict[name] = rows > 0 and wins == rows
        if rows == 0:
            print(f"  {name:<6}{'no usable fold':<20}")
    return verdict


def main() -> int:
    verdict = validate()

    g = load_season("2025-26")
    if g is None:
        print("!! no 2025/26 gameweek file", file=sys.stderr)
        return 1

    ids = pd.read_csv(RAW / "vaastav" / "players_raw_2025-26.csv")[["id", "code"]]
    out = ids.rename(columns={"id": "element"})[["element", "code"]]
    names = g.groupby("element")["name"].last()

    print("\n2026/27 adjustment, from 2025/26 split at each player's own "
          "half-minutes mark:")
    flags = []
    for col, name in METRICS:
        if g[col].isna().all():
            print(f"  {name}: not in the 2025/26 file, skipped")
            continue
        h = weight_halves(split_halves(g, col), noise_scale(split_halves(g, col)))
        k = noise_scale(split_halves(g, col))
        keep = verdict.get(name, False)
        if not keep:
            h["mult"] = 1.0
        h["flag"] = ((h["z"].abs() >= FLAG_Z)
                     & ((h["r_late"] - h["r_early"]).abs()
                        >= FLAG_REL * h["r_pooled"].clip(lower=1e-9)))
        print(f"  {name}: noise scale k={k:.2f}, "
              f"{len(h)} players split, {int(h.flag.sum())} flagged for research"
              f"{'' if keep else '   (multiplier forced to 1.0 -- lost its test)'}")
        h = h.rename(columns={
            "mult": f"recency_mult_{name}", "z": f"recency_z_{name}",
            "r_early": f"{name}_early_p90", "r_late": f"{name}_late_p90",
            "flag": f"recency_flag_{name}"})
        cols = ["element", f"recency_mult_{name}", f"recency_z_{name}",
                f"{name}_early_p90", f"{name}_late_p90", f"recency_flag_{name}"]
        out = out.merge(h[cols], on="element", how="left")
        flags.append(f"recency_flag_{name}")

    for c in out.columns:
        if c.startswith("recency_mult_"):
            out[c] = out[c].fillna(1.0).clip(0.5, 2.0).round(4)
        elif c.startswith("recency_z_"):
            out[c] = out[c].round(2)
        elif c.startswith("recency_flag_"):
            out[c] = out[c].fillna(False).astype(bool)
        elif c.endswith("_p90"):
            out[c] = out[c].round(4)

    out["recency_flag"] = out[flags].any(axis=1) if flags else False
    out["player"] = out["element"].map(names)
    dest = PROC / "recency.csv"
    out.to_csv(dest, index=False)

    n = int(out["recency_flag"].sum())
    print(f"\n{n} players flagged for research -- their second half disagrees "
          f"with their first by more than {FLAG_Z} standard errors and by more "
          f"than {FLAG_REL:.0%}")
    show = [c for c in ("recency_z_xg", "recency_z_xa", "recency_z_dc")
            if c in out.columns]
    if n and show:
        top = out[out.recency_flag].copy()
        top["worst"] = top[show].abs().max(axis=1)
        cols = ["player"] + show
        print(top.nlargest(15, "worst")[cols].to_string(index=False))
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
