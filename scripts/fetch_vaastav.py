"""End-of-season FPL snapshots from vaastav/Fantasy-Premier-League.

The live FPL API only keeps a full season record for players who are still
registered, so pulling history from it quietly drops everyone who left the
league.  vaastav's per-season `players_raw.csv` is the full end-of-season
roster, which is what a fair multi-season comparison needs.

`code` in these files is the Opta player id, so they join to everything else
without name matching.  FPL's Opta expected-goals columns start in 2022-23.
"""
from __future__ import annotations

import io
import sys

import pandas as pd
import requests

from common import PROC, RAW, UA

URL = ("https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/"
       "master/data/{season}/players_raw.csv")

SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]

COLS = ["code", "id", "web_name", "first_name", "second_name", "element_type",
        "team", "minutes", "starts", "total_points", "goals_scored", "assists",
        "clean_sheets", "goals_conceded", "bonus", "bps", "yellow_cards",
        "red_cards", "saves", "expected_goals", "expected_assists",
        "expected_goal_involvements", "expected_goals_conceded",
        "clearances_blocks_interceptions", "recoveries", "tackles",
        "defensive_contribution", "now_cost", "selected_by_percent"]


def fetch(refresh: bool = False) -> None:
    frames = []
    for season in SEASONS:
        cache = RAW / "vaastav" / f"players_raw_{season}.csv"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists() and not refresh:
            df = pd.read_csv(cache)
        else:
            r = requests.get(URL.format(season=season),
                             headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.to_csv(cache, index=False)
        have = [c for c in COLS if c in df.columns]
        missing = [c for c in COLS if c not in df.columns]
        df = df[have].copy()
        df["season"] = f"{season[:4]}/{season[5:]}"
        frames.append(df)
        print(f"  {season}: {len(df)} players"
              + (f"   missing: {', '.join(missing)}" if missing else ""))

    out = pd.concat(frames, ignore_index=True)
    out.to_csv(PROC / "fpl_seasons.csv", index=False)
    print(f"fpl_seasons.csv        {len(out):>4} player-seasons")


if __name__ == "__main__":
    fetch(refresh="--refresh" in sys.argv)
