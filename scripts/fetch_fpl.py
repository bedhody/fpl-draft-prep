"""Official FPL API.

Pulls two things:
  * bootstrap-static  -> player meta, 2026/27 price/position/team, set-piece order
  * element-summary   -> history_past, the permanent per-season record

Why both: during pre-season the bootstrap `elements` block still carries LAST
season's totals, but those reset to zero the moment GW1 starts.  history_past
is permanent, so it is what we treat as the authoritative 2025/26 record.
"""
from __future__ import annotations

import sys

import pandas as pd

from common import DATA_SEASON, PROC, get_json

BOOT = "https://fantasy.premierleague.com/api/bootstrap-static/"
SUMMARY = "https://fantasy.premierleague.com/api/element-summary/{}/"
FIXTURES = "https://fantasy.premierleague.com/api/fixtures/"

META_COLS = [
    "id", "code", "opta_code", "web_name", "first_name", "second_name",
    "element_type", "team", "now_cost", "status", "news", "birth_date",
    "team_join_date", "selected_by_percent", "chance_of_playing_next_round",
    "corners_and_indirect_freekicks_order", "direct_freekicks_order",
    "penalties_order",
]

# Season-total columns on history_past.
SEASON_COLS = [
    "total_points", "minutes", "starts", "goals_scored", "assists",
    "clean_sheets", "goals_conceded", "own_goals", "penalties_saved",
    "penalties_missed", "yellow_cards", "red_cards", "saves", "bonus", "bps",
    "influence", "creativity", "threat", "ict_index",
    "clearances_blocks_interceptions", "recoveries", "tackles",
    "defensive_contribution", "expected_goals", "expected_assists",
    "expected_goal_involvements", "expected_goals_conceded",
    "start_cost", "end_cost",
]


def fetch(refresh: bool = False) -> None:
    boot = get_json(BOOT, "fpl/bootstrap.json", refresh=refresh)

    teams = pd.DataFrame(boot["teams"])[
        ["id", "name", "short_name", "strength_overall_home",
         "strength_overall_away", "strength_attack_home", "strength_attack_away",
         "strength_defence_home", "strength_defence_away"]
    ]
    teams.to_csv(PROC / "fpl_teams.csv", index=False)

    els = pd.DataFrame(boot["elements"])
    pos = {t["id"]: t["singular_name_short"] for t in boot["element_types"]}
    tm = dict(zip(teams["id"], teams["short_name"]))

    meta = els[META_COLS].copy()
    meta["position"] = meta["element_type"].map(pos)
    meta["team_short"] = meta["team"].map(tm)
    meta["price_2627"] = meta.pop("now_cost") / 10
    meta.to_csv(PROC / "fpl_meta.csv", index=False)
    print(f"fpl_meta.csv           {len(meta):>4} players registered for 2026/27")

    # Live bootstrap snapshot of last season's totals (pre-season only).
    snap_cols = [c for c in SEASON_COLS if c in els.columns]
    snap = els[["id", "code"] + snap_cols].copy()
    snap.to_csv(PROC / "fpl_bootstrap_snapshot.csv", index=False)

    rows = []
    n = len(els)
    for i, (eid, code) in enumerate(zip(els["id"], els["code"]), 1):
        d = get_json(SUMMARY.format(eid), f"fpl/element_{eid}.json",
                     refresh=refresh, pause=0.05)
        for h in d.get("history_past", []):
            h = dict(h)
            h["fpl_id"] = eid
            h["code"] = code
            rows.append(h)
        if i % 100 == 0 or i == n:
            print(f"  element-summary {i}/{n}", end="\r", flush=True)
    print()

    hist = pd.DataFrame(rows)
    hist.to_csv(PROC / "fpl_history_past.csv", index=False)
    seasons = sorted(hist["season_name"].unique())
    print(f"fpl_history_past.csv   {len(hist):>4} player-seasons, {seasons[0]}..{seasons[-1]}")

    target = DATA_SEASON.replace("-", "/20")  # 2025-26 -> 2025/2026
    target = f"{DATA_SEASON[:4]}/{DATA_SEASON[5:]}"  # 2025/26
    last = hist[hist["season_name"] == target].copy()
    if last.empty:
        print(f"  !! no rows for season {target}", file=sys.stderr)
    else:
        agg = {c: "sum" for c in SEASON_COLS if c in last.columns}
        agg.update({"start_cost": "first", "end_cost": "last"})
        last = last.groupby(["fpl_id", "code"], as_index=False).agg(agg)
        last.to_csv(PROC / "fpl_last_season.csv", index=False)
        print(f"fpl_last_season.csv    {len(last):>4} players with {target} minutes")

    fx = pd.DataFrame(get_json(FIXTURES, "fpl/fixtures_2627.json", refresh=refresh))
    fx.to_csv(PROC / "fpl_fixtures_2627.csv", index=False)
    print(f"fpl_fixtures_2627.csv  {len(fx):>4} fixtures")


if __name__ == "__main__":
    fetch(refresh="--refresh" in sys.argv)
