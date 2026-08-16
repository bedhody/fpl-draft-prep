"""Card points per 90, from a player's own booking record over three seasons.

Bookings are the one negative in FPL scoring that is genuinely a player trait.
Across 766 repeat player-seasons the year-on-year correlation of card points
per 90 is +0.47, rising to +0.59 for players over 1800 minutes in both seasons.
That is well short of expected goals (+0.86) but far from noise, so the rate is
worth carrying per player rather than per position.

Two decisions came out of the back-test rather than judgement:

* Yellows are shrunk toward the position mean with a prior worth PRIOR_NINETIES
  of average play, so a player with 400 minutes is mostly his position and a
  player with 3000 is mostly himself.
* Reds are NOT player-specific.  At roughly one per 200 nineties an individual
  red rate is noise, and letting one sending-off drive a projection made the
  back-test worse.  Everyone gets their position's red rate.

Fitting on 2022/23-2024/25 and predicting 2025/26 (339 players over 900
minutes), that beats a flat position mean on MAE by 11% and lifts the
cross-sectional correlation from +0.24 to +0.47.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import PROC

# Most recent completed season first.  Recency helps a little; the back-test
# separating these from equal weights is worth about 2% of MAE.
HISTORY_WEIGHTS = (0.5, 0.3, 0.2)
PRIOR_NINETIES = 8

SEASON_ORDER = ["2022/23", "2023/24", "2024/25", "2025/26"]
POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def load() -> pd.DataFrame:
    s = pd.read_csv(PROC / "fpl_seasons.csv")
    s["yr"] = s.season.map({x: i for i, x in enumerate(SEASON_ORDER)})
    s["n90"] = s.minutes / 90
    s["pos"] = s.element_type.map(POS)
    return s[s.minutes > 0]


def rates(hist: pd.DataFrame, codes, positions, upto: int) -> pd.DataFrame:
    """Shrunk yellow rate and position-mean red rate, per player, per 90."""
    prior_y = hist.groupby("pos").apply(
        lambda g: g.yellow_cards.sum() / g.n90.sum(), include_groups=False)
    prior_r = hist.groupby("pos").apply(
        lambda g: g.red_cards.sum() / g.n90.sum(), include_groups=False)
    pm_y = positions.map(prior_y).values
    pm_r = positions.map(prior_r).values

    ny = den = 0.0
    for i, w in enumerate(HISTORY_WEIGHTS):
        h = hist[hist.yr == upto - i].set_index("code")
        ny = ny + w * h.yellow_cards.reindex(codes).fillna(0).values
        den = den + w * h.n90.reindex(codes).fillna(0).values

    k = PRIOR_NINETIES
    yellow = (ny + k * pm_y) / (den + k)
    return pd.DataFrame({
        "code": np.asarray(codes),
        "yellow_p90": yellow,
        "red_p90": pm_r,
        "card_pts_p90": -(yellow + 3 * pm_r),
        "card_nineties": den,
        # 0 = entirely the position prior, 1 = entirely the player's own record
        "card_shrinkage": den / (den + k),
    })


def validate(s: pd.DataFrame) -> None:
    hist = s[s.yr < 3]
    tgt = s[(s.yr == 3) & (s.minutes >= 900)].copy()
    act = -((tgt.yellow_cards + 3 * tgt.red_cards) / tgt.n90).values
    r = rates(hist, tgt.code.values, tgt.pos, upto=2)
    pred = r.card_pts_p90.values
    prior_y = hist.groupby("pos").apply(
        lambda g: g.yellow_cards.sum() / g.n90.sum(), include_groups=False)
    prior_r = hist.groupby("pos").apply(
        lambda g: g.red_cards.sum() / g.n90.sum(), include_groups=False)
    flat = -(tgt.pos.map(prior_y) + 3 * tgt.pos.map(prior_r)).values
    print(f"  validation: 2025/26 card points per 90, fitted on 2022/23-2024/25")
    print(f"    {len(tgt)} players over 900 minutes")
    for label, p in (("model", pred), ("position mean only", flat)):
        print(f"    {label:19s} MAE {np.abs(p - act).mean():.4f}  corr "
              f"{np.corrcoef(p, act)[0, 1]:+.3f}  bias {p.sum() / act.sum() - 1:+.1%}")
    lag = s.copy()
    lag["yr"] += 1
    j = s.merge(lag[["code", "yr", "yellow_cards", "red_cards", "n90"]],
                on=["code", "yr"], suffixes=("", "_p"))
    j = j[(j.n90 >= 5) & (j.n90_p >= 5)]
    a = (j.yellow_cards + 3 * j.red_cards) / j.n90
    b = (j.yellow_cards_p + 3 * j.red_cards_p) / j.n90_p
    print(f"    year-on-year persistence over {len(j)} repeat player-seasons: "
          f"{np.corrcoef(a, b)[0, 1]:+.2f}")


def main() -> int:
    s = load()
    print(f"cards from {len(s)} player-seasons")
    validate(s)

    m = pd.read_csv(PROC / "master_2025_26.csv")
    out = rates(s, m.code.values, m.position, upto=len(SEASON_ORDER) - 1)
    out.to_csv(PROC / "cards_model.csv", index=False)

    j = out.merge(m[["code", "player", "position", "minutes"]], on="code")
    print(f"\ncards_model.csv  {len(out)} players")
    print("  most-booked rates among players with 1800+ minutes last season:")
    top = j[j.minutes >= 1800].nsmallest(8, "card_pts_p90")
    print(top[["player", "position", "yellow_p90", "card_pts_p90",
               "card_shrinkage"]].round(3).to_string(index=False))
    print(f"  position means of the fitted rate: "
          + ", ".join(f"{p} {v:.3f}"
                      for p, v in j.groupby("position").card_pts_p90.mean().items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
