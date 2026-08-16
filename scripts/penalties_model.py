"""Penalties: how many each club gets, and who takes them.

Penalties are the most concentrated points in the game and the most dangerous
thing to leave inside a season xG total.  Cole Palmer's 2025/26 expected goals
were 44% penalties, Bruno Fernandes' 38%.  Carrying that forward assumes the
duty carries forward, and duty moves between seasons far more than form does.
So the model scrubs penalties out of expected goals entirely and credits them
back from two explicit pieces: a club's expected penalty count, and who is
listed to take them.

How many penalties a club gets, tested rather than assumed:

* Last season's own penalty count predicts this season's at r = +0.05 across
  51 club-season pairs.  It is worthless.
* Touches in the opposition box -- the driver worth testing, since a side that
  gets into the box more should be fouled in it more -- predicts at r = +0.39
  in one test season and +0.03 in the other.  Out of sample it does not beat
  simply giving every club the league average (MAE 2.25 against 2.20).
* Goals scored does work: same-season r = +0.50, and using it cuts the error
  from 2.18 to 1.80 penalties per club across three test seasons.

That last one is also the only driver available for the promoted three, since
the market forecasts goals for all 20 clubs, so it is what the model uses.  The
honest summary is that a club's penalty count is mostly noise -- variance
across clubs is only 1.6x the mean, where pure chance would give 1.0x -- and
this is a small tilt on a flat prior, not a real forecast.

Allocation to the taker is availability-weighted: the first-choice taker gets
the penalties in the matches he plays, the second-choice gets what is left when
he does not, and so on.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import PROC

# Measured over 2022/23-2025/26: 381 penalties taken, 316 scored.  Slightly
# above the 0.79 that xG models assign a penalty, which is the four-season
# sample being kind rather than a real difference.
CONVERSION = 0.8294
# 50 of those 381 were saved by the goalkeeper, worth +5 each.  The rest of the
# misses went wide or off target and pay the taker -2 but nobody +5.
SAVE_RATE = 0.1312
MISS_POINTS = -2
PENALTY_SAVE_POINTS = 5

# Four-season average of penalties taken per season: 99, 107, 83, 92.
LEAGUE_PENALTIES = 95.25
# How far to move a club off the league average toward its share of goals.
# 1.0 is the back-tested optimum; the curve is flat, so this is a tilt.
GF_WEIGHT = 1.0


def team_penalties() -> pd.DataFrame:
    """Expected penalties for and against, per club, from market goals."""
    odds = pd.read_csv(PROC / "cs_from_odds.csv").set_index("team_short")
    mu = LEAGUE_PENALTIES / 20
    won = mu + GF_WEIGHT * (odds.GF / odds.GF.sum() * LEAGUE_PENALTIES - mu)
    # Penalties conceded scale with how much a club is under pressure, which is
    # the same quantity the clean-sheet model already expresses as goals against.
    conceded = mu + GF_WEIGHT * (odds.GA / odds.GA.sum() * LEAGUE_PENALTIES - mu)
    return pd.DataFrame({"pens_won": won, "pens_conceded": conceded})


def taker_shares(order: pd.Series, availability: pd.Series,
                 club: pd.Series) -> pd.Series:
    """Share of a club's penalties each listed taker expects to take.

    The first choice takes them in the matches he plays; the second choice
    takes what is left over when he is absent, and so on down the list.

    The shares are deliberately NOT normalised to sum to one.  A club whose
    only listed taker plays 85% of minutes really does give 15% of its
    penalties to somebody, but that somebody is unlisted and unknowable, and
    handing the whole club's penalties to the one named player would credit him
    with spot-kicks he will not be on the pitch for.  Where a full first,
    second and third choice are listed the shares add to about 99% anyway.
    """
    a = availability.clip(0, 1).fillna(0)
    share = pd.Series(0.0, index=order.index)
    for _, idx in club.groupby(club).groups.items():
        remaining = 1.0
        for i in order.loc[idx].dropna().sort_values().index:
            share[i] = remaining * a[i]
            remaining *= (1 - a[i])
    return share


def main() -> int:
    m = pd.read_csv(PROC / "master_2025_26.csv")
    teams = team_penalties()
    print(f"expected penalties per club, from market goals "
          f"(league total {teams.pens_won.sum():.0f})")
    print(teams.sort_values("pens_won", ascending=False).round(2).T.to_string())

    # Availability: the share of the season a player is expected to be on the
    # pitch.  Solio's minutes forecast is forward-looking, so it already prices
    # the injuries and late World Cup returns that last season's minutes cannot.
    mins = m.solio_season_xmins.fillna(m.minutes).fillna(0)
    avail = (mins / (38 * 90)).clip(0, 1)

    d = pd.DataFrame({"code": m.code, "player": m.player, "club": m.team_2627,
                      "position": m.position, "order": m.penalties_order})
    d["share"] = taker_shares(d.order, avail, d.club)
    d["pens_expected"] = d.share * d.club.map(teams.pens_won).fillna(0)
    d["pen_xG"] = d.pens_expected * CONVERSION
    d["pen_miss_pts"] = d.pens_expected * (1 - CONVERSION) * MISS_POINTS
    # A goalkeeper's share of his club's conceded penalties follows his own
    # availability directly -- there is no queue to be second in.
    d["pen_save_pts"] = np.where(
        d.position == "GKP",
        avail * d.club.map(teams.pens_conceded).fillna(0) * SAVE_RATE
        * PENALTY_SAVE_POINTS, 0.0)

    # --- scrub penalties out of last season's expected goals ---------------
    # Understat publishes non-penalty xG directly, so the penalty share of a
    # player's expected goals is measured, not assumed.
    pen_xg_2526 = (m.us_xG - m.us_npxG).clip(lower=0)
    share_np = np.where(m.us_xG > 0, 1 - pen_xg_2526 / m.us_xG, 1.0)
    d["np_share"] = np.clip(share_np, 0, 1)
    d["pen_xG_2526"] = pen_xg_2526.round(2)

    d.to_csv(PROC / "penalties_model.csv", index=False)
    listed = d[d.order.notna() & (d.pens_expected > 0)]
    print(f"\npenalties_model.csv  {len(d)} players, {len(listed)} listed takers")
    print("  largest expected penalty loads:")
    print(listed.nlargest(10, "pens_expected")[
        ["player", "club", "position", "order", "share", "pens_expected",
         "pen_xG"]].round(3).to_string(index=False))
    scrub = d[(d.np_share < 0.95) & (m.us_xG > 5)]
    print(f"\n  {len(scrub)} players had more than 5% of their 2025/26 expected "
          f"goals come from penalties:")
    print(scrub.nsmallest(8, "np_share")[
        ["player", "club", "np_share", "pen_xG_2526", "order"]]
        .round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
