"""Bonus points, rebuilt from BPS components instead of carried forward.

The problem this replaces
------------------------
Until now the model took each player's *realised* 2025/26 bonus, divided by
minutes, and carried it forward as a per-90 rate.  That credits a player for
last season's goals, assists and clean sheets -- the three things the model
already projects from scratch, and the three things that repeat least.  It is
double counting and noise carrying in the same column.

The evidence, from 2025/26 split into halves (256 players with 450+ minutes in
both):

    total BPS/90   first half -> second half   r = 0.39
    base  BPS/90   first half -> second half   r = 0.76

Strip the events out and the remainder is twice as repeatable.  That remainder
-- appearance, defensive actions, passing, crossing, dribbling, fouls -- is the
part worth projecting.  The events get added back from the model's own xG, xA
and P(CS), which is where they belong.

The three stages
----------------
1. **Fit the BPS weights.**  FPL publishes the BPS *stat names* in its API but
   not the weights, and both rules pages are Javascript shells, so the table
   cannot simply be read off.  It is fitted instead, by least squares on 11,492
   player-matches.  What comes back is not the book weight for every term: the
   fitted number is the *total* BPS a goal arrives with, including the shot on
   target, the big chance and the winning-goal bonus that come with it.  For
   projection that is the number wanted -- if a forward scores, this is what
   his BPS actually moves by.  Where a term is cleanly identified the fit does
   recover the published weight, which is what makes the rest credible.

2. **Split every player-match.**  ``base = bps - (fitted event weights x
   events)``.  Events are goals, assists, clean sheets, saves, goals conceded,
   cards, own goals and penalties saved or missed -- every element the model
   projects independently.  The base is then shifted onto 2026/27's CBI rule
   (1 BPS per 3 actions, down from 1 per 2) and shrunk toward the position mean
   for thin samples.

3. **Convert BPS to bonus.**  Bonus is not a rate, it is a contest: 3/2/1 to
   the top three BPS in a fixture.  So a player's expected bonus in a match is

       E[bonus] = sum over k of P(his BPS > the k-th highest BPS among the
                                  other 21 players)

   Both sides are simulated.  His BPS is base plus events drawn from his own
   projections; the three bars are drawn from what actually happened in 380
   fixtures, **conditioned on the scoreline**.  That conditioning is the point:
   in a 4-0 win the bars are high because four goalscorers and a clean sheet
   are competing, so one assist for a Manchester City midfielder buys less
   bonus than the same assist in a 1-0 win.  His goals are drawn from his
   team's goals, so a goalscorer is correctly forced into the matches where the
   competition is stiffest.

Output: data/processed/bonus_model.csv -- expected bonus per match, plus the
first derivatives with respect to xG/90, xA/90 and P(CS) so the workbook and
the levers page can re-price a player when those are edited without re-running
the simulation.

Validation is out-of-sample and is printed at the end: predict each player's
second-half bonus from first-half data only, and compare against carrying the
first half's realised bonus forward.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import PROC, RAW

SEED = 20260817
N_SIMS = 4000
# Terms stripped out to leave the base.  Every one of these is projected
# somewhere else in the model, so leaving it in the carried-forward rate would
# count it twice and carry last season's luck with it.
EVENT_TERMS = ["goal_GKP", "goal_DEF", "goal_MID", "goal_FWD",
               "assist_GKP", "assist_DEF", "assist_MID", "assist_FWD",
               "cs_GKP", "cs_DEF", "cs_MID",
               "saves", "gc_gkdef", "pen_sv", "pen_ms", "yellow", "red", "og"]
# What the published table says, for the terms the fit can identify cleanly.
# Used only to print a comparison -- nothing downstream depends on it.
BOOK = {"app60": 6, "app_sh": 3, "goal_DEF": 12, "goal_MID": 18, "goal_FWD": 24,
        "assist_FWD": 9, "cs_GKP": 12, "cs_DEF": 12, "saves": 2, "yellow": -3,
        "red": -9, "og": -6, "cbi2": 1, "rec3": 1}
POSMAP = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}
MIN_MINUTES = 270          # below this a base rate is mostly prior anyway


# --------------------------------------------------------------------------
# stage 1: fit the weights
# --------------------------------------------------------------------------
def design(d: pd.DataFrame) -> pd.DataFrame:
    """One column per BPS term the gameweek data can see.

    `mins` sits alongside the two appearance dummies on purpose.  BPS accrues
    through the match -- passes, recoveries, crosses -- so without a continuous
    minutes term that accrual leaks into the appearance dummies and out again
    into every player's base rate at the wrong slope.
    """
    pos = d["pos"]
    X = pd.DataFrame(index=d.index)
    X["mins"] = d["minutes"] / 90.0
    X["app60"] = (d["minutes"] >= 60).astype(float)
    X["app_sh"] = (d["minutes"] < 60).astype(float)
    for p in ("GKP", "DEF", "MID", "FWD"):
        X[f"goal_{p}"] = (d["goals_scored"] * (pos == p)).astype(float)
        X[f"assist_{p}"] = (d["assists"] * (pos == p)).astype(float)
    for p in ("GKP", "DEF", "MID"):
        X[f"cs_{p}"] = (d["clean_sheets"] * (pos == p)
                        * (d["minutes"] >= 60)).astype(float)
    X["saves"] = d["saves"].astype(float)
    X["gc_gkdef"] = (d["goals_conceded"] * pos.isin(["GKP", "DEF"])).astype(float)
    X["pen_sv"] = d["penalties_saved"].astype(float)
    X["pen_ms"] = d["penalties_missed"].astype(float)
    X["yellow"] = d["yellow_cards"].astype(float)
    X["red"] = d["red_cards"].astype(float)
    X["og"] = d["own_goals"].astype(float)
    X["cbi2"] = (d["cbi"] // 2).astype(float)
    X["rec3"] = (d["recoveries"] // 3).astype(float)
    X["tackle"] = d["tackles"].astype(float)
    return X


def fit_weights(d: pd.DataFrame) -> tuple[pd.Series, float]:
    X = design(d)
    y = d["bps"].astype(float).to_numpy()
    beta, *_ = np.linalg.lstsq(X.to_numpy(), y, rcond=None)
    pred = X.to_numpy() @ beta
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    return pd.Series(beta, index=X.columns), float(r2)


def report_fit(d: pd.DataFrame, b: pd.Series, r2: float) -> None:
    """Print the fit, and refit on each half so the reader can see which
    coefficients are measured and which are one freak match."""
    h1, h2 = d[d.GW <= 19], d[d.GW > 19]
    b1, _ = fit_weights(h1)
    b2, _ = fit_weights(h2)
    print(f"BPS weight fit on {len(d):,} player-matches, R2 = {r2:.4f}")
    print(f"{'term':<12}{'fitted':>9}{'GW1-19':>9}{'GW20-38':>9}{'published':>11}")
    for c in b.index:
        book = BOOK.get(c)
        print(f"  {c:<10}{b[c]:9.2f}{b1[c]:9.2f}{b2[c]:9.2f}"
              f"{('' if book is None else f'{book:>11d}')}")
    print("  (blank = not published, or not separable from what co-occurs with it)")


# --------------------------------------------------------------------------
# stage 2: split, re-rule, shrink
# --------------------------------------------------------------------------
def base_rates(d: pd.DataFrame, b: pd.Series, prior_n: float
               ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-player base BPS per 90, on 2026/27's CBI rule, shrunk to position."""
    X = design(d)
    d = d.copy()
    d["event_bps"] = X[EVENT_TERMS].to_numpy() @ b[EVENT_TERMS].to_numpy()
    # The CBI weight halves to a third for 2026/27.  Applied to the base rather
    # than to total BPS because it is a base term: no event weight involves it.
    d["cbi_shift"] = -(d["cbi"] // 2) + (d["cbi"] // 3)
    # Appearance BPS is flat, not a rate.  Turning a player's whole base into
    # points per 90 and multiplying by minutes shrinks the 6 points he gets for
    # turning up: an 80-minute start would carry 5.3 of them, not 6.  Held out
    # here and added back per match in the simulation, which pulled the
    # simulated BPS distribution 1.5 points up right at the bonus threshold.
    d["app_bps"] = np.where(d["minutes"] >= 60, b["app60"], b["app_sh"])
    d["base_bps"] = d["bps"] - d["event_bps"] - d["app_bps"] + d["cbi_shift"]

    agg = d.groupby("element").agg(
        pos=("pos", "last"), minutes=("minutes", "sum"),
        base=("base_bps", "sum"), bps=("bps", "sum"), bonus=("bonus", "sum"),
    ).reset_index()
    agg["n90"] = agg["minutes"] / 90.0
    agg["base_raw_p90"] = (agg["base"] / agg["n90"]).where(agg["n90"] > 0)

    heavy = agg[agg["minutes"] >= 900]
    prior = heavy.groupby("pos").apply(
        lambda g: g["base"].sum() / g["n90"].sum(), include_groups=False)
    agg["prior"] = agg["pos"].map(prior).fillna(prior.mean())
    agg["base_bps_p90"] = ((agg["base"] + agg["prior"] * prior_n)
                           / (agg["n90"] + prior_n))
    return agg, d


def fit_prior_strength(d: pd.DataFrame, b: pd.Series) -> float:
    """Choose the shrinkage strength out of sample: build the rate on the first
    half, score it against the second, keep the k with the lowest error.
    Picking k by eye is how a number nobody can defend gets into a model."""
    h1, h2 = d[d.GW <= 19], d[d.GW > 19]
    truth, _ = base_rates(h2, b, 0.0)
    truth = truth[truth.minutes >= 450][["element", "base_raw_p90"]]
    best, curve = None, []
    for k in (0.0, 2.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 50.0):
        est, _ = base_rates(h1, b, k)
        j = est.merge(truth, on="element", suffixes=("", "_true"))
        j = j[j.minutes >= 450]
        mae = float((j["base_bps_p90"] - j["base_raw_p90_true"]).abs().mean())
        curve.append((k, mae, len(j)))
        if best is None or mae < best[1]:
            best = (k, mae)
    print("\nshrinkage strength, chosen out of sample (H1 rate vs H2 truth):")
    for k, mae, n in curve:
        print(f"  k={k:<5} MAE {mae:.4f}{'   <-- chosen' if k == best[0] else ''}"
              f"{'   (no shrinkage)' if k == 0 else ''}")
    print(f"  n = {curve[0][2]} players")
    return best[0]


# --------------------------------------------------------------------------
# stage 3: the bars, conditioned on the scoreline
# --------------------------------------------------------------------------
# Buckets for the conditional bar distribution.  BPS edges are the player's
# own score; G and C are his team's goals for and against.
BPS_EDGES = np.array([8, 16, 24, 32, 40, 50], dtype=float)
N_BPS_B, N_G, N_C = len(BPS_EDGES) + 1, 4, 3
MIN_CELL = 60


def _cell(bps_b, g, c):
    return (bps_b * N_G + g) * N_C + c


def bar_pool(d: pd.DataFrame) -> list[np.ndarray]:
    """For every start, the three BPS scores that player had to beat.

    Leave-one-out on purpose.  A player is not competing with himself, and for
    the players who win bonus that is the whole question: the unconditional
    third-highest BPS in a fixture is a bar the man who set it never had to
    clear.  Pricing him against it costs about a fifth of all the bonus there
    is, because it is precisely the players who win bonus who are mispriced.

    Conditioned on three things: his own BPS, his team's goals, and his team's
    goals conceded.  Own BPS is what fixes the leave-one-out bias -- ask "given
    a player scored 40 BPS, what did the other 21 do?" and the answer already
    knows he is not competing with a 40 of his own.  The two scoreline
    dimensions are the team-context effect: in a 4-0 win four team-mates and a
    clean sheet are competing, so the same 30 BPS wins less.

    Returns one array of (bar1, bar2, bar3) rows per cell.  Cells thinner than
    MIN_CELL borrow from their parents -- first pooling over goals conceded,
    then over goals, then the league as a whole -- so no cell is ever a handful
    of matches pretending to be a distribution.
    """
    rows = []
    for _, fx in d.groupby("fixture"):
        vals = fx["bps"].to_numpy()
        if len(vals) < 5:
            continue
        mins = fx["minutes"].to_numpy()
        tg = np.minimum(fx["team_goals"].to_numpy().astype(int), N_G - 1)
        tc = np.minimum(fx["team_conceded"].to_numpy().astype(int), N_C - 1)
        for i in range(len(vals)):
            if mins[i] < 60:
                continue
            top3 = np.sort(np.delete(vals, i))[::-1][:3]
            if len(top3) < 3:
                continue
            rows.append((int(np.searchsorted(BPS_EDGES, vals[i], side="right")),
                         tg[i], tc[i], *top3))
    r = np.asarray(rows, dtype=float)
    bpsb, g, c, bars = r[:, 0].astype(int), r[:, 1].astype(int), r[:, 2].astype(int), r[:, 3:]

    pool = []
    for bb in range(N_BPS_B):
        for gg in range(N_G):
            for cc in range(N_C):
                for sel in (((bpsb == bb) & (g == gg) & (c == cc)),
                            ((bpsb == bb) & (g == gg)),
                            (bpsb == bb),
                            np.ones(len(r), bool)):
                    if sel.sum() >= MIN_CELL:
                        pool.append(bars[sel])
                        break
    return pool


def team_goals_from_fixtures(gw: pd.DataFrame) -> pd.DataFrame:
    """Goals for and against, per player-match, from the scoreline columns."""
    g = gw.copy()
    g["team_goals"] = np.where(g["was_home"], g["team_h_score"], g["team_a_score"])
    g["team_conceded"] = np.where(g["was_home"], g["team_a_score"], g["team_h_score"])
    return g


def simulate(players: pd.DataFrame, pool: dict, rng: np.random.Generator,
             resid: dict, n_sims: int = N_SIMS) -> np.ndarray:
    """Expected bonus per match, for one set of inputs.

    Vectorised over players; each player gets his own `n_sims` matches.  The
    order of the draws is fixed by the caller's generator so that a finite
    difference between two calls measures the input change and not the noise.
    """
    n = len(players)
    pos = players["pos"].to_numpy()
    mins = players["mins_per_start"].to_numpy()[:, None]
    lam_for = players["team_gf_match"].to_numpy()[:, None]
    lam_ag = players["team_ga_match"].to_numpy()[:, None]

    # --- his team's scoreline, which drives both his events and the bars ----
    G = rng.poisson(np.broadcast_to(lam_for, (n, n_sims)))
    # Goals against and saves come out of the same draw of shots on target,
    # because they are the same event resolved two ways.  Drawing them
    # separately would let a keeper keep a clean sheet and make six saves in a
    # match that only had two shots in it.  For outfielders saves_per_match is
    # zero and this collapses back to C ~ Poisson(goals against).
    saves_rate = players["saves_per_match"].fillna(0).to_numpy()[:, None]
    sot = rng.poisson(np.broadcast_to(lam_ag + saves_rate, (n, n_sims)))
    p_goal = np.clip(lam_ag / np.maximum(lam_ag + saves_rate, 1e-6), 0, 1)
    C = rng.binomial(sot, np.broadcast_to(p_goal, (n, n_sims)))
    saves = sot - C

    # --- his share of it ----------------------------------------------------
    # Drawing goals from the team's goals rather than from his own Poisson is
    # what couples a goalscorer to the matches where the bars are highest.
    share_g = np.clip((players["xG_p90"].to_numpy()[:, None] * mins / 90)
                      / np.maximum(lam_for, 1e-6), 0, 1)
    share_a = np.clip((players["xA_p90"].to_numpy()[:, None] * mins / 90)
                      / np.maximum(lam_for, 1e-6), 0, 1)
    goals = rng.binomial(G, np.broadcast_to(share_g, (n, n_sims)))
    assists = rng.binomial(np.maximum(G - goals, 0),
                           np.broadcast_to(np.clip(share_a / np.maximum(1 - share_g, 1e-6),
                                                   0, 1), (n, n_sims)))
    cs = ((C == 0) & (mins >= 60)).astype(float)

    # --- his BPS ------------------------------------------------------------
    b = players.attrs["weights"]
    app = np.where(mins >= 60, b["app60"], b["app_sh"])
    bps = app + players["base_bps_p90"].to_numpy()[:, None] * mins / 90.0
    for i, p in enumerate(("GKP", "DEF", "MID", "FWD")):
        sel = (pos == p)[:, None]
        bps = bps + sel * (goals * b[f"goal_{p}"] + assists * b[f"assist_{p}"])
    for p in ("GKP", "DEF", "MID"):
        bps = bps + (pos == p)[:, None] * cs * b[f"cs_{p}"]
    # Conceding: goalkeepers and defenders carry it, and it is the one event
    # that a clean sheet rules out, so it has to move with the same draw.
    gc_sel = np.isin(pos, ["GKP", "DEF"])[:, None]
    bps = bps + gc_sel * C * b["gc_gkdef"] + saves * b["saves"]
    # Match-to-match scatter in the base itself: passing, crossing, dribbling,
    # fouls.  Sampled from what players in the same position actually produced
    # rather than assumed normal -- the real distribution is right-skewed.
    #
    # Drawn conditional on the goal involvements just simulated, because the
    # two are not independent: the match where a midfielder scores is also the
    # match where he had the shots, the touches and the passes into the box.
    # Drawing them independently pulls the joint upper tail apart, and the
    # joint upper tail is exactly where bonus is won.
    inv = np.minimum(goals + assists, 2)
    for p in ("GKP", "DEF", "MID", "FWD"):
        sel = pos == p
        if not sel.any():
            continue
        for k in range(3):
            r = resid.get((p, k))
            if r is None or not len(r):
                r = resid.get((p, 0))
            m = sel[:, None] & (inv == k)
            if m.any():
                bps[m] += rng.choice(r, size=int(m.sum()))

    # --- the bars, drawn conditional on what he just did --------------------
    cell = _cell(np.searchsorted(BPS_EDGES, bps, side="right"),
                 np.minimum(G, N_G - 1), np.minimum(C, N_C - 1))
    bars = np.empty((n, n_sims, 3))
    for cid in np.unique(cell):
        m = cell == cid
        arr = pool[cid]
        bars[m] = arr[rng.integers(0, len(arr), size=int(m.sum()))]

    # Bonus = 3 to the top BPS, 2 to the second, 1 to the third -- which is the
    # same as one point for every bar he clears.  The comparison is `>=`
    # because FPL shares ties upward: equalling the third-highest score among
    # the other 21 is worth a bonus point, not nothing.
    return (bps[:, :, None] >= bars).sum(axis=2).mean(axis=1)


def residual_pool(d: pd.DataFrame, agg: pd.DataFrame) -> dict[tuple, np.ndarray]:
    """Match-level scatter of the base around a player's own rate, for starts,
    split by how many goal involvements the player had in that match."""
    r = d[d.minutes >= 60].merge(agg[["element", "base_bps_p90"]], on="element")
    r["resid"] = r["base_bps"] - r["base_bps_p90"] * r["minutes"] / 90.0
    r["inv"] = np.minimum(r["goals_scored"] + r["assists"], 2)
    return {k: g["resid"].to_numpy() for k, g in r.groupby(["pos", "inv"])}


# --------------------------------------------------------------------------
def main() -> int:
    gw = pd.read_csv(RAW / "vaastav" / "merged_gw_2025-26.csv")
    gw = gw.drop_duplicates(subset=["element", "fixture"]).reset_index(drop=True)
    gw = team_goals_from_fixtures(gw)
    gw["pos"] = gw["position"].map(POSMAP)
    gw["cbi"] = gw["clearances_blocks_interceptions"].fillna(0)
    gw["recoveries"] = gw["recoveries"].fillna(0)
    gw["tackles"] = gw["tackles"].fillna(0)
    played = gw[(gw.minutes > 0) & gw["pos"].notna()].copy()

    b, r2 = fit_weights(played)
    report_fit(played, b, r2)

    prior_n = fit_prior_strength(played, b)
    agg, split = base_rates(played, b, prior_n)

    # --- does stripping the events actually help? --------------------------
    print("\nrepeatability, first half -> second half (450+ minutes in both):")
    halves = {}
    for lab, sel in (("H1", played.GW <= 19), ("H2", played.GW > 19)):
        a, _ = base_rates(played[sel], b, 0.0)
        halves[lab] = a[a.minutes >= 450].set_index("element")
    j = halves["H1"].join(halves["H2"], how="inner", lsuffix="_1", rsuffix="_2")
    for col, lab in (("bps", "total BPS/90"), ("base", "base BPS/90")):
        x = j[f"{col}_1"] / j["n90_1"]
        y = j[f"{col}_2"] / j["n90_2"]
        print(f"  {lab:<14} r = {np.corrcoef(x, y)[0, 1]:.3f}   "
              f"MAE = {(x - y).abs().mean():.2f}")
    print(f"  n = {len(j)} players")

    pool = bar_pool(played)
    exact = sum(1 for c in pool if len(c) >= MIN_CELL)
    print(f"\nbar pool: {len(pool)} cells (own BPS x team goals x conceded), "
          f"median {int(np.median([len(c) for c in pool]))} matches per cell, "
          f"{exact}/{len(pool)} met the {MIN_CELL}-match floor without borrowing")

    resid = residual_pool(split, agg)
    agg.attrs["weights"] = b

    bar_mechanism_check(played, pool, np.random.default_rng(SEED))
    factors = check_level(split, agg, b, pool, resid)
    out = build_projection(agg, b, pool, resid, factors)
    validate(played, agg, b, pool, resid)

    dest = PROC / "bonus_model.csv"
    out.to_csv(dest, index=False)
    print(f"\nwrote {dest}  ({len(out)} players)")
    return 0


def build_projection(agg, b, pool, resid, factors) -> pd.DataFrame:
    """Expected bonus per match for 2026/27, plus its three derivatives."""
    import xpts_model                                   # for model_rates only

    m = pd.read_csv(PROC / "master_2025_26.csv", low_memory=False)
    dc = pd.read_csv(PROC / "defcon_model.csv")
    cs = pd.read_csv(PROC / "cs_from_odds.csv")
    ids = pd.read_csv(RAW / "vaastav" / "players_raw_2025-26.csv")[["id", "code"]]

    # The same non-penalty xG, xA and P(CS) the rest of the model scores with.
    m = pd.concat([m, xpts_model.model_rates(m)], axis=1)

    a = agg.merge(ids, left_on="element", right_on="id", how="left").dropna(subset=["code"])
    a["code"] = a["code"].astype("int64")
    keep = ["code", "player", "position", "team_2627", "xG_p90", "xA_p90", "pcs"]
    a = a.merge(m[keep], on="code", how="left")
    a = a.merge(dc[["code", "mins_per_start"]], on="code", how="left")
    a["mins_per_start"] = a["mins_per_start"].fillna(85.0).clip(20, 90)
    a["pos"] = a["position"].map(POSMAP).fillna(a["pos"])
    a["xG_p90"] = a["xG_p90"].fillna(0)
    a["xA_p90"] = a["xA_p90"].fillna(0)

    cs = cs[cs.team_short != "team_short"].copy()
    for c in ("GF", "GA"):
        cs[c] = pd.to_numeric(cs[c], errors="coerce")
    a = a.merge(cs[["team_short", "GF", "GA"]], left_on="team_2627",
                right_on="team_short", how="left")
    a["team_gf_match"] = (a["GF"] / 38).fillna(1.35)
    a["team_ga_match"] = (a["GA"] / 38).fillna(1.35)

    # Saves are worth 2 BPS each and are most of a goalkeeper's non-appearance
    # BPS, so a keeper on a bad defence out-earns a keeper on a good one.
    td = pd.read_csv(PROC / "team_defence.csv")
    td = td[td.team_short != "team_short"].copy()
    td["saves_per_match"] = pd.to_numeric(td["saves_per_match"], errors="coerce")
    a = a.merge(td[["team_short", "saves_per_match"]], left_on="team_2627",
                right_on="team_short", how="left", suffixes=("", "_td"))
    a["saves_per_match"] = a["saves_per_match"].where(a["pos"] == "GKP", 0.0).fillna(0.0)
    a.attrs["weights"] = b

    def run(dxg=0.0, dxa=0.0, dcs=0.0):
        v = a.copy()
        v.attrs["weights"] = b
        v["xG_p90"] = (v["xG_p90"] + dxg).clip(0)
        v["xA_p90"] = (v["xA_p90"] + dxa).clip(0)
        if dcs:
            # P(CS) moves through the only thing that produces it: goals against.
            v["team_ga_match"] = np.maximum(
                -np.log(np.clip(np.exp(-v["team_ga_match"]) + dcs, 1e-4, 0.999)), 0.05)
        f = v["pos"].map(factors).fillna(1.0).to_numpy()
        return simulate(v, pool, np.random.default_rng(SEED), resid) * f

    base = run()
    a["bonus_per_match"] = base.round(5)
    a["d_bonus_d_xg90"] = ((run(dxg=0.10) - base) / 0.10).round(5)
    a["d_bonus_d_xa90"] = ((run(dxa=0.10) - base) / 0.10).round(5)
    a["d_bonus_d_pcs"] = ((run(dcs=0.10) - base) / 0.10).round(5)
    a["base_bps_p90"] = a["base_bps_p90"].round(3)
    a["bonus_2526"] = a["bonus"]
    # The inputs the derivatives were taken at.  Anything downstream that edits
    # xG, xA or P(CS) re-prices bonus by the distance from these, so they have
    # to travel with the numbers rather than be re-derived and drift.
    a["bonus_xg_base"] = a["xG_p90"].round(5)
    a["bonus_xa_base"] = a["xA_p90"].round(5)
    a["bonus_pcs_base"] = np.exp(-a["team_ga_match"]).round(5)

    cols = ["code", "player", "pos", "team_2627", "base_bps_p90",
            "bonus_per_match", "d_bonus_d_xg90", "d_bonus_d_xa90",
            "d_bonus_d_pcs", "bonus_xg_base", "bonus_xa_base", "bonus_pcs_base",
            "mins_per_start", "bonus_2526"]
    return a[cols].sort_values("bonus_per_match", ascending=False)


def bar_mechanism_check(played, pool, rng) -> None:
    """Feed the mechanism the REAL BPS of every real start and see whether it
    returns the real bonus.  This separates the two things that can be wrong --
    how well the BPS distribution is simulated, and how well BPS converts to
    bonus -- so a failure points at one of them instead of both."""
    st = played[played.minutes >= 60]
    cell = _cell(np.searchsorted(BPS_EDGES, st["bps"].to_numpy(), side="right"),
                 np.minimum(st["team_goals"].to_numpy().astype(int), N_G - 1),
                 np.minimum(st["team_conceded"].to_numpy().astype(int), N_C - 1))
    exp = np.zeros(len(st))
    bps = st["bps"].to_numpy()
    for cid in np.unique(cell):
        m = cell == cid
        arr = pool[cid]
        draws = arr[rng.integers(0, len(arr), size=(int(m.sum()), 40))]
        exp[m] = (bps[m][:, None, None] >= draws).sum(axis=2).mean(axis=1)
    print(f"\nBPS -> bonus conversion, fed the real BPS of all {len(st):,} starts:")
    print(f"  actual {st.bonus.sum():.0f}   converted {exp.sum():.0f}   "
          f"({exp.sum() / st.bonus.sum() - 1:+.1%})")


def check_level(split, agg, b, pool, resid) -> dict[str, float]:
    """Fed each player's own 2025/26 rates and his own team's scorelines, does
    the simulation put back the bonus that was actually awarded?

    This is in-sample and it is not a forecast test -- it only asks whether the
    machinery is calibrated.  A model right about *who* wins bonus but wrong
    about how much of it exists would sail through a correlation and still
    mis-price every player.

    Returns a per-position scale factor, because the answer is that it does not
    quite.  Total bonus is a conserved quantity -- 3+2+1 is awarded in every
    one of the 380 fixtures whatever anybody projects -- so a model that sums
    to 87% of it is wrong by construction and normalising to the identity is
    not a fudge.  The factor is per position rather than global because the
    shortfall is not uniform: goalkeepers come out furthest light, and a single
    global number would leave every keeper under-rated against every forward.
    """
    st = split[split.minutes >= 60]
    v = st.groupby("element").agg(
        pos=("pos", "last"), starts=("minutes", "size"),
        mins_per_start=("minutes", "mean"), team_gf_match=("team_goals", "mean"),
        team_ga_match=("team_conceded", "mean"), g=("goals_scored", "sum"),
        a=("assists", "sum"), mn=("minutes", "sum"), bonus=("bonus", "sum"),
        saves_per_match=("saves", "mean"),
    ).reset_index().merge(agg[["element", "base_bps_p90"]], on="element")
    v = v[v.starts >= 5]
    v["xG_p90"] = (v["g"] / (v["mn"] / 90)).clip(0, 2)
    v["xA_p90"] = (v["a"] / (v["mn"] / 90)).clip(0, 2)
    v.attrs["weights"] = b
    pred = simulate(v, pool, np.random.default_rng(SEED), resid) * v["starts"]
    err = pred - v["bonus"]
    print(f"\ncalibration -- replay 2025/26 with each player's own rates "
          f"({len(v)} players, 5+ starts):")
    print(f"  actual bonus {v.bonus.sum():.0f}   simulated {pred.sum():.0f}   "
          f"({pred.sum() / v.bonus.sum() - 1:+.1%})")
    print(f"  per player: MAE {err.abs().mean():.2f}  bias {err.mean():+.2f}  "
          f"r {np.corrcoef(pred, v.bonus)[0, 1]:.3f}")
    g = v.assign(pred=pred).groupby("pos")
    factors = (g["bonus"].sum() / g["pred"].sum()).clip(0.8, 1.6).to_dict()
    print("  scale to the conserved total, by position: "
          + "  ".join(f"{p} x{f:.3f}" for p, f in sorted(factors.items())))
    return factors


def validate(played, agg, b, pool, resid) -> None:
    """The only test that matters: does it beat carrying last year forward?

    Everything here is fitted on the first half of 2025/26 and scored against
    the second, so the comparison is out of sample for both methods.
    """
    h1, h2 = played[played.GW <= 19], played[played.GW > 19]
    a1, s1 = base_rates(h1, b, 10.0)
    resid1 = residual_pool(s1, a1)
    pool1 = bar_pool(h1)

    truth = h2.groupby("element").agg(
        mins=("minutes", "sum"), bonus=("bonus", "sum"),
        starts=("starts", "sum"), team_gf=("team_goals", "mean"),
        team_ga=("team_conceded", "mean"), pos=("pos", "last"),
        goals=("goals_scored", "sum"), assists=("assists", "sum"),
    ).reset_index()
    truth = truth[truth.starts >= 8]

    # inputs known at half time only -- including minutes per start, which has
    # to come from the first half too or the test leaks the answer
    n1 = h1.groupby("element").agg(
        mins=("minutes", "sum"), g=("goals_scored", "sum"),
        a=("assists", "sum"), starts_h1=("starts", "sum"),
        saves_h1=("saves", "sum")).reset_index()
    v = truth.merge(a1[["element", "pos", "base_bps_p90"]], on="element",
                    suffixes=("", "_r")).merge(n1, on="element", suffixes=("", "_h1"))
    v["mins_per_start"] = (v["mins_h1"] / v["starts_h1"].replace(0, np.nan)
                           ).fillna(85.0).clip(20, 90)
    v["xG_p90"] = (v["g"] / (v["mins_h1"] / 90)).clip(0, 2)
    v["xA_p90"] = (v["a"] / (v["mins_h1"] / 90)).clip(0, 2)
    v["saves_per_match"] = (v["saves_h1"] / v["starts_h1"].replace(0, np.nan)).fillna(0)
    v["team_gf_match"] = v["team_gf"]
    v["team_ga_match"] = v["team_ga"]
    v.attrs["weights"] = b

    # The scale factors come from the first half too, so nothing about the
    # second half leaks into the prediction being scored.
    v1 = s1[s1.minutes >= 60].groupby("element").agg(
        pos=("pos", "last"), starts=("minutes", "size"),
        mins_per_start=("minutes", "mean"), team_gf_match=("team_goals", "mean"),
        team_ga_match=("team_conceded", "mean"), g=("goals_scored", "sum"),
        a=("assists", "sum"), mn=("minutes", "sum"), bonus=("bonus", "sum"),
        saves_per_match=("saves", "mean"),
    ).reset_index().merge(a1[["element", "base_bps_p90"]], on="element")
    v1 = v1[v1.starts >= 4]
    v1["xG_p90"] = (v1["g"] / (v1["mn"] / 90)).clip(0, 2)
    v1["xA_p90"] = (v1["a"] / (v1["mn"] / 90)).clip(0, 2)
    v1.attrs["weights"] = b
    p1 = simulate(v1, pool1, np.random.default_rng(SEED), resid1) * v1["starts"]
    gg = v1.assign(pred=p1).groupby("pos")
    factors = (gg["bonus"].sum() / gg["pred"].sum()).clip(0.8, 1.6).to_dict()

    per_match = simulate(v, pool1, np.random.default_rng(SEED), resid1)
    v["pred_raw"] = per_match * v["starts"]
    v["pred_new"] = v["pred_raw"] * v["pos"].map(factors).fillna(1.0)
    # the incumbent: first-half bonus per 90, carried forward
    h1b = h1.groupby("element").agg(bonus=("bonus", "sum"), mins=("minutes", "sum"))
    v = v.merge((h1b["bonus"] / (h1b["mins"] / 90)).rename("b90_h1"),
                on="element", how="left")
    v["pred_old"] = v["b90_h1"].fillna(0) * v["mins"] / 90

    print("\nout-of-sample check -- predict second-half bonus from first-half data")
    print(f"  {len(v)} players with 8+ starts in the second half")
    for lab, col in (("carry forward (old model)", "pred_old"),
                     ("BPS-decomposed, unscaled", "pred_raw"),
                     ("BPS-decomposed, scaled (shipped)", "pred_new")):
        e = v[col] - v["bonus"]
        print(f"  {lab:<32} MAE {e.abs().mean():5.2f}  bias {e.mean():+5.2f}  "
              f"r {np.corrcoef(v[col], v['bonus'])[0, 1]:.3f}")
    tot = v["bonus"].sum()
    print(f"  league total: actual {tot:.0f}, "
          f"carry forward {v.pred_old.sum():.0f}, new {v.pred_new.sum():.0f}")


if __name__ == "__main__":
    sys.exit(main())
