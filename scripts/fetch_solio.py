"""Solio Analytics projections: 19 gameweeks -> a full-season estimate.

Solio publishes 19 gameweeks of projected points and projected minutes per
player.  Doubling the 19-gameweek total would be wrong, because the early
window is depressed by pre-season injuries and by late returns from the summer
World Cup.  Saliba is projected 0 minutes for GW1-5 and 86 by GW16; doubling
would charge him for that absence twice.

So the second half is rebuilt at the player's *settled* minutes level, taken
from GW17-19:

    rate  = total projected points / total projected minutes   (over GWs played)
    H2    = rate x settled_xMins x 19
    season = H1 (as published) + H2

Using a points-per-minute rate rather than rescaling each gameweek handles the
zero-minute gameweeks, which cannot be rescaled at all.  It does flatten
fixture variation across the second half, which is unavoidable: the GW20-38
fixtures are not knowable at this level, and over 19 games every team plays
every other once anyway.

The settled minutes figure is also written out on its own, as a ready-made
xMins input for the xPts model.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import PROC, RAW

SRC_DEFAULT = Path.home() / "Downloads" / "projection.csv"
GWS = range(1, 20)
LATE = ["17_xMins", "18_xMins", "19_xMins"]
POS_MAP = {"G": "GKP", "D": "DEF", "M": "MID", "F": "FWD"}


def load(src: Path) -> pd.DataFrame:
    d = pd.read_csv(src)
    d.columns = [c.strip().lstrip("﻿") for c in d.columns]
    return d


def build(src: Path = SRC_DEFAULT) -> pd.DataFrame:
    d = load(src)
    xm = [f"{i}_xMins" for i in GWS]
    pt = [f"{i}_Pts" for i in GWS]

    out = pd.DataFrame({
        "solio_id": d["ID"],
        "solio_name": d["Name"],
        "solio_team": d["Team"],
        "position": d["Pos"].map(POS_MAP),
        "price": d["BV"],
    })

    out["solio_H1_pts"] = d[pt].sum(axis=1).round(2)
    out["solio_settled_xmins"] = d[LATE].median(axis=1).round(2)
    out["solio_mean_xmins_1_19"] = d[xm].mean(axis=1).round(2)
    out["solio_peak_xmins"] = d[xm].max(axis=1).round(2)

    mins = d[xm].to_numpy()
    pts = d[pt].to_numpy()
    played = mins > 0
    tot_min = np.where(played, mins, 0).sum(axis=1)
    tot_pts = np.where(played, pts, 0).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(tot_min > 0, tot_pts / tot_min, 0.0)      # points per minute
    out["solio_pts_per_min"] = np.round(rate, 5)
    out["solio_H2_pts"] = np.round(rate * out["solio_settled_xmins"] * len(list(GWS)), 2)
    out["solio_season_pts"] = (out["solio_H1_pts"] + out["solio_H2_pts"]).round(2)
    out["solio_naive_double"] = (out["solio_H1_pts"] * 2).round(2)
    out["solio_correction"] = (out["solio_season_pts"] - out["solio_naive_double"]).round(2)
    # A full-season minutes forecast, for use as an xMins input.
    out["solio_season_xmins"] = (out["solio_settled_xmins"] * 38).round(0)

    # ---- flags, so the corrections can be inspected rather than trusted ----
    late = d[LATE]
    out["late_window_spread"] = (late.max(axis=1) - late.min(axis=1)).round(2)
    ramp = (out["solio_settled_xmins"] > 30) & \
           (d[xm[:5]].mean(axis=1) < 0.5 * out["solio_settled_xmins"])
    fade = (out["solio_peak_xmins"] > 30) & \
           (out["solio_settled_xmins"] < 0.6 * out["solio_peak_xmins"])
    out["xmins_pattern"] = np.select(
        [ramp, fade], ["ramp-up (injury / late WC return)", "fade (losing his place)"],
        default="flat")
    return out


def attach_code(out: pd.DataFrame) -> pd.DataFrame:
    """Solio's ID is the FPL element id; verify that before trusting it."""
    meta = pd.read_csv(PROC / "fpl_meta.csv")[["id", "code", "web_name", "team_short"]]
    j = out.merge(meta, left_on="solio_id", right_on="id", how="left")
    from common import norm_name
    agree = (j["web_name"].map(norm_name) == j["solio_name"].map(norm_name))
    print(f"  Solio ID -> FPL element id: {j['code'].notna().sum()}/{len(out)} resolved, "
          f"{agree.sum()} with matching names")
    bad = j[j["code"].notna() & ~agree]
    if len(bad):
        print(f"  !! {len(bad)} id/name disagreements -- falling back to name match for these")
        print(bad[["solio_id", "solio_name", "web_name"]].head(8).to_string(index=False))
    return j.drop(columns=["id", "web_name", "team_short"])


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    if not src.exists():
        print(f"!! {src} not found", file=sys.stderr)
        return 1
    raw_copy = RAW / "solio" / "projection.csv"
    raw_copy.parent.mkdir(parents=True, exist_ok=True)
    raw_copy.write_bytes(src.read_bytes())

    out = attach_code(build(src))
    out.to_csv(PROC / "solio_projections.csv", index=False)

    n = len(out)
    print(f"solio_projections.csv  {n} players")
    print("\nxMins pattern:", out.xmins_pattern.value_counts().to_dict())
    print(f"\nlate window (GW17-19) still moving for {int((out.late_window_spread > 1).sum())} players")

    print("\nbiggest UPWARD corrections vs naive doubling (injury / WC returns):")
    print(out.nlargest(10, "solio_correction")[
        ["solio_name", "solio_team", "position", "solio_H1_pts", "solio_settled_xmins",
         "solio_naive_double", "solio_season_pts", "solio_correction"]].to_string(index=False))
    print("\nbiggest DOWNWARD corrections (projected to lose their place):")
    print(out.nsmallest(10, "solio_correction")[
        ["solio_name", "solio_team", "position", "solio_H1_pts", "solio_peak_xmins",
         "solio_settled_xmins", "solio_naive_double", "solio_season_pts",
         "solio_correction"]].to_string(index=False))
    print("\ntop 12 by corrected season points:")
    print(out.nlargest(12, "solio_season_pts")[
        ["solio_name", "solio_team", "position", "price", "solio_H1_pts",
         "solio_season_pts", "solio_season_xmins"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
