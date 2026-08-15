"""Gameweek-level 2025/26 data, for the things season totals cannot tell you.

Two in particular:

  * Defensive contribution points are earned per match, on a threshold (10
    CBIT for defenders, 12 CBIRT for midfielders and forwards).  The season
    API only exposes the raw action *count*, so a player with 380 actions
    spread evenly scores far more than one who piled them into a few games.
    Only gameweek data can tell you how many matches actually paid out.

  * Minutes risk.  A 2,700-minute season looks the same whether it was 30
    full games or 38 sixty-minute cameos; the second is worth much less.

Source: vaastav/Fantasy-Premier-League merged_gw.csv.
"""
from __future__ import annotations

import io
import sys

import pandas as pd
import requests

from common import PROC, RAW, UA

URL = ("https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/"
       "master/data/2025-26/gws/merged_gw.csv")

# FPL 2025/26 and 2026/27 defensive-contribution thresholds.
THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12, "GK": 99, "GKP": 99}


def fetch(refresh: bool = False) -> None:
    cache = RAW / "vaastav" / "merged_gw_2025-26.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and not refresh:
        gw = pd.read_csv(cache)
    else:
        r = requests.get(URL, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
        gw = pd.read_csv(io.StringIO(r.text))
        gw.to_csv(cache, index=False)
    # The source ships a few exactly duplicated player-gameweek rows, which
    # would double those players' season totals.
    before = len(gw)
    gw = gw.drop_duplicates(subset=["element", "fixture"], keep="first")
    print(f"  merged_gw: {len(gw)} player-gameweeks, GW{gw.GW.min()}-{gw.GW.max()}"
          + (f"  ({before - len(gw)} duplicates dropped)" if before != len(gw) else ""))

    gw["position"] = gw["position"].replace({"GK": "GKP"})   # match FPL's label
    gw["threshold"] = gw["position"].map(THRESHOLD).fillna(99)
    gw["defcon_hit"] = (gw["defensive_contribution"] >= gw["threshold"]).astype(int)
    # Also count hits at BOTH thresholds, ignoring last season's position: ten
    # players were reclassified for 2026/27, and a MID->DEF move drops their
    # threshold from 12 to 10.  Their 2025/26 output has to be re-scored at the
    # threshold they will actually face.
    gw["hit_at_10"] = (gw["defensive_contribution"] >= 10).astype(int)
    gw["hit_at_12"] = (gw["defensive_contribution"] >= 12).astype(int)
    gw["played"] = (gw["minutes"] > 0).astype(int)
    gw["full_app"] = (gw["minutes"] >= 60).astype(int)
    gw["blank"] = ((gw["minutes"] > 0) & (gw["total_points"] <= 2)).astype(int)
    gw["haul"] = (gw["total_points"] >= 10).astype(int)

    # A player's DefCon threshold follows the position he held THAT season, so
    # last season's DefCon points must be read against last season's position.
    # Keep it in the output: several players are reclassified each summer.
    agg = gw.groupby("element").agg(
        pos_2526=("position", "last"),
        gw_played=("played", "sum"),
        gw_started=("starts", "sum"),
        gw_full_60=("full_app", "sum"),
        gw_minutes=("minutes", "sum"),
        defcon_matches=("defcon_hit", "sum"),
        defcon_points=("defcon_hit", lambda s: 2 * s.sum()),
        defcon_matches_at_10=("hit_at_10", "sum"),
        defcon_matches_at_12=("hit_at_12", "sum"),
        blanks=("blank", "sum"),
        hauls=("haul", "sum"),
        points_sd=("total_points", "std"),
        best_gw=("total_points", "max"),
    ).reset_index()

    started = agg["gw_started"].where(agg["gw_started"] > 0)
    agg["defcon_hit_rate"] = (agg["defcon_matches"] / started).round(3)
    agg["defcon_rate_at_10"] = (agg["defcon_matches_at_10"] / started).round(3)
    agg["defcon_rate_at_12"] = (agg["defcon_matches_at_12"] / started).round(3)
    agg["start_rate"] = (agg["gw_started"] / 38).round(3)
    agg["full_60_rate"] = (agg["gw_full_60"] / agg["gw_played"]).where(
        agg["gw_played"] > 0).round(3)
    agg["points_sd"] = agg["points_sd"].round(2)

    # merged_gw keys on the season's FPL element id; map it to the Opta code.
    ids = pd.read_csv(RAW / "vaastav" / "players_raw_2025-26.csv")[["id", "code"]]
    agg = agg.merge(ids, left_on="element", right_on="id", how="left").drop(
        columns=["id"])
    missing = agg["code"].isna().sum()
    agg = agg[agg["code"].notna()]
    agg["code"] = agg["code"].astype("int64")

    agg.to_csv(PROC / "gw_aggregates_2025_26.csv", index=False)
    print(f"gw_aggregates_2025_26  {len(agg)} players"
          + (f"  ({missing} unmapped element ids dropped)" if missing else ""))
    top = agg.nlargest(5, "defcon_matches")
    print("  most DefCon-paying matches:",
          ", ".join(f"{int(r.element)}={int(r.defcon_matches)}" for r in top.itertuples()))


if __name__ == "__main__":
    fetch(refresh="--refresh" in sys.argv)
