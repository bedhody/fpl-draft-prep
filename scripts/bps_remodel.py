"""Re-run 2025/26 bonus points under the 2026/27 BPS rules.

What changed for 2026/27, and what can be modelled:

  MODELLED   Clearances, blocks and interceptions: 1 BPS per 3 actions,
             down from 1 per 2.  Gameweek data carries per-match CBI, so this
             is recomputed exactly.

  NOT MODELLED  The -1 BPS for being tackled is removed.  No free source
             publishes "times tackled" per match, so this cannot be recovered.
             Its effect is to RAISE the BPS of dribblers and ball-carriers,
             which pushes in the same direction as the CBI change (away from
             defenders) -- so the modelled deltas below understate the shift.

  NOT MODELLED  Goalkeeper save-location changes.  Per-match saves are not
             broken down by shot location anywhere free.

Method: adjust each player's per-match BPS, then re-run FPL's bonus allocation
over all 380 fixtures.  The allocator is validated first by reproducing the
actual 2025/26 bonus from the actual BPS -- if it cannot do that, nothing
downstream is trustworthy.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import OUT, PROC, RAW


def award_bonus(bps: pd.Series) -> pd.Series:
    """FPL's bonus allocation for one fixture, including its tie rules.

    3/2/1 to the top three BPS.  Ties share the higher award and consume the
    slots below them: two tied on top both get 3 and the next player gets 1;
    three tied on top get 3 each and nobody else scores.
    """
    out = pd.Series(0, index=bps.index, dtype=int)
    values = sorted(bps.unique(), reverse=True)
    if not values:
        return out

    first = bps == values[0]
    out[first] = 3
    n1 = int(first.sum())
    if n1 >= 3 or len(values) < 2:
        return out

    second = bps == values[1]
    if n1 == 1:
        out[second] = 2
        n2 = int(second.sum())
        if n2 == 1 and len(values) >= 3:
            out[bps == values[2]] = 1
    else:                       # exactly two tied on top -> 2s are skipped
        out[second] = 1
    return out


def allocate(df: pd.DataFrame, bps_col: str, out_col: str) -> pd.DataFrame:
    played = df[df["minutes"] > 0].copy()
    played[out_col] = (played.groupby("fixture", group_keys=False)[bps_col]
                       .apply(award_bonus))
    return df.merge(played[["element", "fixture", out_col]],
                    on=["element", "fixture"], how="left").fillna({out_col: 0})


def load_gameweeks() -> pd.DataFrame:
    """merged_gw.csv ships a handful of exactly duplicated player-gameweek
    rows (Junior Kroupi and Ben Gannon-Doak in 2025/26).  Left in, they both
    double those players' season totals and fan out any join on
    (element, fixture), so drop them at the door."""
    gw = pd.read_csv(RAW / "vaastav" / "merged_gw_2025-26.csv")
    before = len(gw)
    gw = gw.drop_duplicates(subset=["element", "fixture"], keep="first")
    if before != len(gw):
        print(f"dropped {before - len(gw)} duplicated player-gameweek rows")
    return gw.reset_index(drop=True)


def main() -> int:
    gw = load_gameweeks()

    # ---- validate the allocator against reality -------------------------
    gw = allocate(gw, "bps", "bonus_check")
    match = (gw["bonus_check"] == gw["bonus"])
    print(f"allocator validation: {match.sum()}/{len(gw)} player-gameweeks "
          f"reproduce the real bonus ({match.mean():.4%})")
    if match.mean() < 0.999:
        wrong = gw[~match].nlargest(10, "bps")
        print("\nmismatches:")
        print(wrong[["name", "GW", "fixture", "bps", "bonus", "bonus_check"]]
              .to_string(index=False))
        print("\nallocator is not faithful -- stopping.")
        return 1

    # ---- apply the one modellable BPS change ----------------------------
    cbi = gw["clearances_blocks_interceptions"].fillna(0)
    gw["bps_new"] = gw["bps"] - (cbi // 2) + (cbi // 3)
    gw = allocate(gw, "bps_new", "bonus_new")

    gw["bps_delta"] = gw["bps_new"] - gw["bps"]
    gw["bonus_delta"] = gw["bonus_new"] - gw["bonus"]

    print(f"\nleague-wide: {int(gw.bonus.sum())} bonus points awarded in 2025/26, "
          f"{int(gw.bonus_new.sum())} under 2026/27 CBI weighting")
    changed = gw[gw.bonus_delta != 0]
    print(f"             {len(changed)} player-gameweeks change bonus "
          f"({changed.bonus_delta.gt(0).sum()} up, {changed.bonus_delta.lt(0).sum()} down)")

    # ---- per player ------------------------------------------------------
    agg = gw.groupby("element").agg(
        name=("name", "last"),
        pos_2526=("position", "last"),
        team=("team", "last"),
        minutes=("minutes", "sum"),
        bps_old=("bps", "sum"),
        bps_new=("bps_new", "sum"),
        bonus_old=("bonus", "sum"),
        bonus_new=("bonus_new", "sum"),
        points_old=("total_points", "sum"),
    ).reset_index()
    agg["bps_delta"] = agg["bps_new"] - agg["bps_old"]
    agg["bonus_delta"] = agg["bonus_new"] - agg["bonus_old"]
    agg["points_new"] = agg["points_old"] + agg["bonus_delta"]
    agg["bonus_per_point_old"] = (agg["bonus_old"] / agg["points_old"]).where(
        agg["points_old"] > 0).round(4)
    agg["bonus_per_point_new"] = (agg["bonus_new"] / agg["points_new"]).where(
        agg["points_new"] > 0).round(4)

    ids = pd.read_csv(RAW / "vaastav" / "players_raw_2025-26.csv")[["id", "code"]]
    agg = agg.merge(ids, left_on="element", right_on="id", how="left").drop(columns=["id"])
    agg = agg[agg["code"].notna()]
    agg["code"] = agg["code"].astype("int64")
    agg.to_csv(PROC / "bps_remodel_2025_26.csv", index=False)

    print("\nbonus change by position (players with 900+ minutes):")
    big = agg[agg.minutes >= 900]
    print(big.groupby("pos_2526").agg(
        players=("element", "size"),
        mean_bps_delta=("bps_delta", "mean"),
        mean_bonus_delta=("bonus_delta", "mean"),
        total_bonus_delta=("bonus_delta", "sum"),
    ).round(2).to_string())

    print("\nbiggest losers:")
    print(big.nsmallest(12, "bonus_delta")[
        ["name", "team", "pos_2526", "bps_old", "bps_delta", "bonus_old",
         "bonus_new", "bonus_delta", "points_old", "points_new"]].to_string(index=False))
    print("\nbiggest gainers:")
    print(big.nlargest(10, "bonus_delta")[
        ["name", "team", "pos_2526", "bps_old", "bps_delta", "bonus_old",
         "bonus_new", "bonus_delta", "points_old", "points_new"]].to_string(index=False))

    with pd.ExcelWriter(OUT / "bps_remodel_2026_27.xlsx") as xl:
        agg.sort_values("bonus_delta").to_excel(
            xl, sheet_name="Per player", index=False)
        big.groupby("pos_2526").agg(
            players=("element", "size"),
            mean_bps_delta=("bps_delta", "mean"),
            mean_bonus_delta=("bonus_delta", "mean"),
            total_bonus_delta=("bonus_delta", "sum"),
        ).round(2).to_excel(xl, sheet_name="By position")
    print(f"\nwritten: {OUT / 'bps_remodel_2026_27.xlsx'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
