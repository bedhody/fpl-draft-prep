"""FotMob — Opta-derived stats that the FPL API does not expose.

The prize here is xGOT (expected goals on target, i.e. post-shot xG) for
outfielders and "goals prevented" for keepers.  FBref used to be the free home
of PSxG; Stats Perform pulled FBref's advanced feed in January 2026, so FotMob
is now the most accessible free route to the same family of metric.

Season stats live on a plain JSON CDN:
    https://data.fotmob.com/stats/47/season/{seasonId}/{stat}.json
"""
from __future__ import annotations

import sys

import pandas as pd

from common import PROC, get_json, norm_name

LEAGUE = "https://www.fotmob.com/api/data/leagues?id=47&season={season}"
CDN = "https://data.fotmob.com/stats/47/season/{sid}/{stat}.json"

SEASONS = ["2021/2022", "2022/2023", "2023/2024", "2024/2025", "2025/2026"]

# Stat -> output column.  "_"-prefixed names are FotMob composites.
STATS = {
    "rating": "fotmob_rating",
    "expected_goals": "fm_xG",
    "expected_assists": "fm_xA",
    "expected_goalsontarget": "fm_xGOT",
    "_goals_prevented": "fm_goals_prevented",
    "_save_percentage": "fm_save_pct",
    "big_chance_created": "fm_big_chances_created",
    "big_chance_missed": "fm_big_chances_missed",
    "penalty_won": "fm_penalties_won",
    "total_att_assist": "fm_chances_created",
    "won_contest": "fm_succ_dribbles_p90",
    "poss_won_att_3rd": "fm_poss_won_att3rd_p90",
    "defensive_contributions": "fm_def_actions_p90",
    "penalty_conceded": "fm_penalties_conceded",
    "mins_played": "fm_minutes",
    "goals": "fm_goals",
    "goal_assist": "fm_assists",
}


def season_ids(refresh: bool = False) -> dict[str, int]:
    doc = get_json(LEAGUE.format(season=SEASONS[-1].replace("/", "%2F")),
                   "fotmob/league.json", refresh=refresh)
    return {l["Name"]: l["TournamentId"] for l in doc["stats"]["seasonStatLinks"]}


def fetch(refresh: bool = False) -> None:
    ids = season_ids(refresh)
    frames = []
    for season in SEASONS:
        sid = ids.get(season)
        if sid is None:
            print(f"  !! no FotMob season id for {season}", file=sys.stderr)
            continue
        cols = {}
        meta = {}
        for stat, col in STATS.items():
            try:
                d = get_json(CDN.format(sid=sid, stat=stat),
                             f"fotmob/{sid}_{stat.strip('_')}.json", refresh=refresh)
            except Exception as e:                     # stat missing for that season
                print(f"  !! {season} {stat}: {e}", file=sys.stderr)
                continue
            rows = d.get("TopLists", d) if isinstance(d, dict) else d
            if isinstance(rows, dict):
                rows = rows.get("statsData") or rows.get("StatList") or []
            if rows and isinstance(rows[0], dict) and "StatList" in rows[0]:
                rows = rows[0]["StatList"]
            for r in rows:
                pid = r.get("ParticiantId") or r.get("ParticipantId") or r.get("id")
                if pid is None:
                    continue
                cols.setdefault(pid, {})[col] = r.get("StatValue", r.get("value"))
                # Every stat list repeats name/team/minutes, so take them once.
                meta.setdefault(pid, {
                    "fotmob_name": r.get("ParticipantName") or r.get("name"),
                    "fotmob_team_id": r.get("TeamId") or r.get("teamId"),
                    "fotmob_team": r.get("TeamName") or r.get("teamName"),
                    "fm_mins": r.get("MinutesPlayed"),
                    "fm_matches": r.get("MatchesPlayed"),
                })
        df = pd.DataFrame([{"fotmob_id": p, **meta.get(p, {}), **v} for p, v in cols.items()])
        df["season"] = f"{season[:4]}/{season[-2:]}"
        frames.append(df)
        print(f"  {season}: {len(df)} players, {df.shape[1] - 5} stats")

    out = pd.concat(frames, ignore_index=True)
    out["name_key"] = out["fotmob_name"].map(norm_name)
    out.to_csv(PROC / "fotmob_players.csv", index=False)
    print(f"fotmob_players.csv     {len(out):>4} player-seasons")


if __name__ == "__main__":
    fetch(refresh="--refresh" in sys.argv)
