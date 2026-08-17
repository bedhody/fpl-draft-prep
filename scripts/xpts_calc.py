"""Every FPL scoring element, computed in Python.

This is the model.  The workbook displays what comes out of here; it no longer
computes it.  Splitting the two ways round -- formulas in the sheet, an
independent reimplementation in Python to check them -- worked, but it meant
every change had to be written twice, in a language that cannot express a
minimum or a cap without three layers of IF().

The season total is deliberately not one multiplication:

    xPts season = rate x (xMins / 90)      six elements that scale with
                                           minutes: xG, xA, clean sheets,
                                           saves, goals conceded, cards
                + appearance points        earned per match, and capped at the
                                           38 matches that exist
                + DefCon points            earned per match too, and convex in
                                           how long each one lasts
                + bonus                    contested once per fixture, so also
                                           per match rather than per minute
                + penalties                a season count already, because the
                                           taker's share prices availability

Only the first line is linear in minutes.  The old sheet treated all of it as
linear, which is what let Christian Nørgaard bank 62 appearances in a 38-match
season: his 45 minutes per start came from a cameo-heavy year, Solio then
forecast 2,804 minutes, and dividing one by the other gave a number of matches
that does not exist.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

# A Premier League season is 38 matches, so nobody appears in more than 38.
MATCHES = 38
# What to assume for a player with no Premier League record to measure -- a
# promoted club's keeper, an incoming signing.  Matching defcon_model so there
# is one number rather than two.  Without a fallback these players score no
# appearance points at all, which quietly cost the backup keepers 76 points
# each and dragged the goalkeeper replacement level from 114 to 45.
DEFAULT_MINS_PER_START = 85.0
# FPL awards assists more loosely than Opta -- rebounds, deflections, won
# penalties.  765 FPL assists against 580 Opta assists over 2025/26.
ASSIST_UPLIFT = 1.32
# 381 penalties taken over 2022/23-2025/26, 316 scored.
PENALTY_CONVERSION = 0.8294
MISS_POINTS = 2
# E[floor(X/m)] is an infinite sum of Poisson tails.  Eight terms is exact to
# 1e-4 at any rate a real club produces.
FLOOR_TERMS = 8

# Scoring, straight from the FPL API's game_config for 2026/27.
FIELDS = ("goal", "assist", "clean_sheet", "defcon", "appearance",
          "defcon_threshold", "saves_per_point", "conceded_per_point")
SCORING = {
    "GKP": (10, 3, 4, 0, 2, 99, 3, 2),
    "DEF": (6, 3, 4, 2, 2, 10, 0, 2),
    "MID": (5, 3, 1, 2, 2, 12, 0, 0),
    "FWD": (4, 3, 0, 2, 2, 12, 0, 0),
}
# An unrecognised position scores nothing and can never clear a DefCon
# threshold.  Failing to zero rather than to a default keeps a bad position
# string visible instead of quietly scoring it as a midfielder.
UNKNOWN = (0, 0, 0, 0, 0, 99, 0, 0)

# The per-90 elements, in the order they appear on the sheet.  Appearance,
# DefCon and bonus are absent on purpose: none of them scales with minutes.
RATE_PARTS = ["xg_pts_p90", "xa_pts_p90", "cs_pts_p90",
              "save_pts_p90", "gc_pts_p90", "cards_pts_p90"]


def multipliers(pos: pd.Series) -> pd.DataFrame:
    """The scoring table, broadcast to one row per player."""
    rows = pos.map(lambda p: SCORING.get(p, UNKNOWN))
    return pd.DataFrame(
        {f: rows.map(lambda v, i=i: v[i]).astype(float) for i, f in enumerate(FIELDS)},
        index=pos.index)


def floor_points(lam, per, terms: int = FLOOR_TERMS):
    """E[floor(X/per)] for X ~ Poisson(lam).

    FPL settles "1 point per 3 saves" and "-1 per 2 conceded" every match, not
    once a season, so two saves in a match are worth nothing and the season
    total is not the season count divided by three.  The expectation of a floor
    has a closed form as a sum of tails,

        E[floor(X/m)] = sum over k >= 1 of P(X >= m*k)

    which is what this computes.  Skipping the floor makes saves 56% too
    generous and goals conceded 57% too harsh.
    """
    lam = np.asarray(lam, dtype=float)
    per = np.asarray(per, dtype=float)
    lam, per = np.broadcast_arrays(lam, per)
    out = np.zeros(lam.shape, dtype=float)
    live = (per > 0) & (lam > 0)
    if not live.any():
        return out
    l, p = lam[live], per[live]
    out[live] = sum(1 - poisson.cdf(p * k - 1, l) for k in range(1, terms + 1))
    return out


def defcon_hit(lam, mins_per_start, threshold, size=None):
    """P(a player clears his DefCon threshold in one start).

    Actions arrive at `lam` per 90, so a start of `mins_per_start` sees
    lam x mins/90 of them on average.  The response to minutes is convex: a
    defender on 10 actions per 90 clears a threshold of 10 in 19% of 60-minute
    starts and 49% of 90-minute starts.

    Negative binomial, not Poisson.  A player's defensive actions are not a
    constant-rate process -- some matches he chases the game for ninety
    minutes, some he is three up at half time -- and measured within player
    across starts the variance is 1.5x the mean.  Because P(X >= threshold) is
    convex in the rate, that spread produces MORE threshold crossings than a
    Poisson predicts: the Poisson returned 16% less DefCon than 2025/26
    actually awarded, and 12.4% less out of sample.  `size` is the dispersion,
    fitted per position in defcon_model.py; infinite size is the Poisson, and
    is what a player with no fitted value falls back to.
    """
    lam = np.asarray(lam, dtype=float)
    mps = np.asarray(mins_per_start, dtype=float)
    thr = np.asarray(threshold, dtype=float)
    sz = np.asarray(np.inf if size is None else size, dtype=float)
    out = np.zeros(np.broadcast(lam, mps, thr, sz).shape, dtype=float)
    lam, mps, thr, sz = np.broadcast_arrays(lam, mps, thr, sz)
    live = (lam > 0) & (mps > 0)
    if not live.any():
        return out
    mu = lam[live] * mps[live] / 90
    k = sz[live]
    finite = np.isfinite(k) & (k > 0)
    res = np.empty(mu.shape, dtype=float)
    res[~finite] = 1 - poisson.cdf(thr[live][~finite] - 1, mu[~finite])
    if finite.any():
        kk, mm = k[finite], np.maximum(mu[finite], 1e-9)
        res[finite] = 1 - nbinom.cdf(thr[live][finite] - 1, kk, kk / (kk + mm))
    out[live] = res
    return out


def matches_played(xmins, mins_per_start):
    """How many matches a minutes forecast implies, capped at the 38 that exist."""
    xmins = np.asarray(xmins, dtype=float)
    mps = np.asarray(mins_per_start, dtype=float)
    raw = np.divide(xmins, mps, out=np.zeros_like(xmins, dtype=float),
                    where=mps > 0)
    return np.clip(raw, 0, MATCHES)


def score(d: pd.DataFrame, assist_uplift: float = ASSIST_UPLIFT) -> pd.DataFrame:
    """Score every player.  `d` is the input frame built by xpts_model."""
    s = multipliers(d["pos"])
    xmins = d["xMins_input"].fillna(0).clip(lower=0).to_numpy(dtype=float)
    mps = d["mins_per_start"].fillna(0).clip(lower=0).to_numpy(dtype=float)

    out = pd.DataFrame(index=d.index)

    # --- the seven elements that really are linear in minutes --------------
    out["xg_pts_p90"] = d["xG_p90"].fillna(0) * s["goal"]
    out["xa_pts_p90"] = d["xA_p90"].fillna(0) * s["assist"] * assist_uplift
    out["cs_pts_p90"] = d["pcs_input"].fillna(0) * s["clean_sheet"]
    out["save_pts_p90"] = floor_points(d["saves_per_match"].fillna(0),
                                       s["saves_per_point"])
    out["gc_pts_p90"] = -floor_points(d["ga_per_match"].fillna(0),
                                      s["conceded_per_point"])
    out["cards_pts_p90"] = d["card_pts_p90"].fillna(0)
    out["rate_pts_p90"] = out[RATE_PARTS].sum(axis=1)

    # --- the two that are earned per match ---------------------------------
    matches = matches_played(xmins, mps)
    out["matches"] = matches
    # Minutes per appearance, recomputed from the capped count: a player whose
    # forecast implies more than 38 matches is not making 62 short ones, he is
    # making 38 longer ones, and the 60-minute test has to see the longer figure.
    mins_per_app = np.divide(xmins, matches, out=np.zeros_like(xmins),
                             where=matches > 0)
    out["mins_per_app"] = mins_per_app
    out["app_pts_season"] = matches * np.where(
        mins_per_app >= 60, s["appearance"].to_numpy(), 1.0)

    out["defcon_hit"] = defcon_hit(d["defcon_lambda"].fillna(0), mps,
                                   s["defcon_threshold"],
                                   d["defcon_size"].fillna(np.inf))
    out["dc_pts_season"] = matches * out["defcon_hit"] * s["defcon"]

    # --- bonus, also per match ---------------------------------------------
    # 3/2/1 is awarded once per fixture, so bonus is won per match and not per
    # minute -- the same reason appearance points are not a rate.  bonus_model
    # simulates one match at this player's minutes per start; the three slopes
    # re-price it when xG, xA or P(CS) are edited away from the values the
    # simulation was run at, which is a first-order approximation and is only
    # meant to hold over the size of edit a person makes by hand.
    out["bonus_per_match_priced"] = (
        d["bonus_per_match"].fillna(0)
        + d["bonus_d_xg"].fillna(0) * (d["xG_p90"].fillna(0) - d["bonus_xg_base"].fillna(0))
        + d["bonus_d_xa"].fillna(0) * (d["xA_p90"].fillna(0) - d["bonus_xa_base"].fillna(0))
        + d["bonus_d_pcs"].fillna(0) * (d["pcs_input"].fillna(0) - d["bonus_pcs_base"].fillna(0))
    ).clip(lower=0)
    out["bonus_pts_season"] = matches * out["bonus_per_match_priced"]

    # --- penalties, a season count already ---------------------------------
    # The taker's expected count prices how much of the season he plays, so it
    # must not be scaled by minutes a second time.
    pens = d["pens_season"].fillna(0)
    out["pen_pts_season"] = (
        pens * (PENALTY_CONVERSION * s["goal"]
                - (1 - PENALTY_CONVERSION) * MISS_POINTS)
        + d["pen_save_pts"].fillna(0))

    # --- totals ------------------------------------------------------------
    n90 = xmins / 90
    out["xpts_season"] = (out["rate_pts_p90"] * n90 + out["app_pts_season"]
                          + out["dc_pts_season"] + out["pen_pts_season"]
                          + out["bonus_pts_season"])
    # Shown per 90 for comparability across players.  Penalties are left out:
    # they do not scale with minutes, so folding them in would make a part-time
    # penalty taker look like a high-rate player.
    per90 = out["xpts_season"] - out["pen_pts_season"]
    out["xpts_p90"] = np.divide(per90 * 90, xmins, out=np.zeros(len(d)),
                                where=xmins > 0)
    # Convenience views of the two per-match elements, at the current xMins.
    out["app_pts_p90"] = np.divide(out["app_pts_season"] * 90, xmins,
                                   out=np.zeros(len(d)), where=xmins > 0)
    out["dc_pts_p90"] = np.divide(out["dc_pts_season"] * 90, xmins,
                                  out=np.zeros(len(d)), where=xmins > 0)
    out["bonus_pts_p90"] = np.divide(out["bonus_pts_season"] * 90, xmins,
                                     out=np.zeros(len(d)), where=xmins > 0)

    # A player with no 2026/27 position has left the league.  Most elements
    # already zero themselves through the multiplier table, but bonus and cards
    # are raw per-90 rates with no multiplier, so without this a departed player
    # still carries a season projection.
    gone = ~d["pos"].isin(SCORING)
    if gone.any():
        out.loc[gone, [c for c in out.columns if c != "matches"]] = 0.0
    return out


def replacement_levels(season: pd.Series, pos: pd.Series, draftable: pd.Series,
                       teams: int, slots: dict[str, int]) -> dict[str, float]:
    """The best player at each position still on the board once every team has
    filled its slots -- the (teams x slots + 1)-th best, 1-indexed."""
    repl = {}
    for p, n in slots.items():
        pool = season[(pos == p) & draftable.fillna(False).astype(bool)]
        v = pool.dropna().sort_values(ascending=False)
        k = teams * n
        repl[p] = float(v.iloc[k]) if len(v) > k else 0.0
    return repl
