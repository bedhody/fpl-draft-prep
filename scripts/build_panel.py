"""Multi-season panel (2022/23 - 2025/26) used to test the xG models.

Same join logic as build_master, run once per season, so every season has both
Opta xG (FPL) and Understat xG on the same player.
"""
from __future__ import annotations

import pandas as pd

from build_master import alias_table, match_source
from common import PROC

SEASONS = ["2022/23", "2023/24", "2024/25", "2025/26"]


def build() -> pd.DataFrame:
    pulse_all = pd.read_csv(PROC / "pulselive_players.csv")
    fpl_all = pd.read_csv(PROC / "fpl_seasons.csv")
    und_all = pd.read_csv(PROC / "understat_players.csv")
    fm_all = pd.read_csv(PROC / "fotmob_players.csv")

    out = []
    for season in SEASONS:
        pulse = pulse_all[pulse_all.season == season].drop(columns=["season"])
        fpl = fpl_all[fpl_all.season == season].drop(columns=["season"])
        und = und_all[und_all.season == season].drop(columns=["season"])
        fm = fm_all[fm_all.season == season].drop(columns=["season"])

        spine = pulse.merge(
            fpl[["code", "web_name", "first_name", "second_name",
                 "element_type", "minutes", "starts", "total_points",
                 "goals_scored", "assists", "expected_goals",
                 "expected_assists", "expected_goals_conceded", "bonus"]],
            on="code", how="left")

        aliases = alias_table(spine)
        und_ok, _ = match_source(und, spine, aliases,
                                 src_name_col="understat_name",
                                 src_min_col="minutes", label="understat")
        fm_ok, _ = match_source(fm, spine, aliases,
                                src_name_col="fotmob_name",
                                src_min_col="fm_mins", label="fotmob")

        und_ok = und_ok[["code", "goals", "xG", "npxG", "assists", "xA",
                         "shots", "key_passes", "xGChain", "xGBuildup"]].rename(
            columns=lambda c: c if c == "code" else f"us_{c}")
        fm_ok = fm_ok[["code", "fm_xG", "fm_xA", "fm_xGOT", "fm_goals_prevented",
                       "fotmob_rating", "fm_big_chances_missed"]]

        df = (spine.merge(und_ok, on="code", how="left")
                   .merge(fm_ok, on="code", how="left"))
        df["season"] = season
        out.append(df)
        print(f"  {season}: {len(df)} players, "
              f"Opta xG {df.expected_goals.notna().sum()}, "
              f"Understat xG {df.us_xG.notna().sum()}, "
              f"xGOT {df.fm_xGOT.notna().sum()}")

    panel = pd.concat(out, ignore_index=True)
    panel = panel.rename(columns={"expected_goals": "xG_opta",
                                  "expected_assists": "xA_opta",
                                  "mins_played": "minutes_pl"})
    panel.to_csv(PROC / "panel.csv", index=False)
    print(f"panel.csv              {len(panel)} player-seasons")
    return panel


if __name__ == "__main__":
    build()
