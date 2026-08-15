"""Official Premier League feed (footballapi.pulselive.com) — raw Opta counts.

This is the same feed premierleague.com/stats runs on, it is free, and every
player row carries `altIds.opta` (e.g. "p223094") which is *identical* to the
FPL API's `code` / `opta_code`.  So this joins to FPL on an ID, with no name
matching at all.

It has no xG, but it has counting stats nobody else gives away: big chances,
touches in the opposition box, errors leading to a goal/shot, aerials, duels,
through-balls, high claims, and so on.
"""
from __future__ import annotations

import sys

import pandas as pd

from common import PROC, get_json

URL = "https://footballapi.pulselive.com/football/stats/ranked/players/{stat}"
HEADERS = {"Origin": "https://www.premierleague.com",
           "Referer": "https://www.premierleague.com/"}

# compSeason ids from /football/competitions/1/compseasons
COMP_SEASONS = {"2021/22": 418, "2022/23": 489, "2023/24": 578,
                "2024/25": 719, "2025/26": 777}

STATS = [
    # appearances / time
    "appearances", "mins_played",
    # attacking
    "goals", "goal_assist", "total_scoring_att", "ontarget_scoring_att",
    "att_hd_goal", "att_freekick_goal", "att_pen_goal", "penalty_won",
    "big_chance_created", "big_chance_missed", "total_att_assist",
    "touches_in_opp_box", "total_offside", "goal_fastbreak",
    # passing / carrying
    "touches", "total_pass", "accurate_pass", "total_through_ball",
    "accurate_through_ball", "total_cross", "accurate_cross",
    "total_long_balls", "accurate_long_balls", "won_contest", "total_contest",
    "dispossessed", "corner_taken",
    # defending
    "total_tackle", "won_tackle", "interception", "total_clearance",
    "effective_clearance", "outfielder_block", "blocked_scoring_att",
    "ball_recovery", "duel_won", "duel_lost", "aerial_won", "aerial_lost",
    "error_lead_to_goal", "error_lead_to_shot", "penalty_conceded",
    "own_goals",
    # goalkeeping
    "clean_sheet", "goals_conceded", "saves", "penalty_save",
    "total_high_claim", "punches",
    # discipline
    "yellow_card", "red_card", "fouls", "was_fouled",
]

PAGE = 500


def _pull(stat: str, season: str, cs: int, refresh: bool):
    rows, page = [], 0
    while True:
        d = get_json(
            URL.format(stat=stat), f"pulselive/{cs}_{stat}_p{page}.json",
            headers=HEADERS, refresh=refresh, pause=0.15,
            params={"page": page, "pageSize": PAGE, "compSeasons": cs,
                    "comps": 1, "altIds": "true"},
        )
        s = d.get("stats") or {}
        content = s.get("content") or []
        for c in content:
            o = c["owner"]
            opta = (o.get("altIds") or {}).get("opta")
            if not opta:
                continue
            rows.append({
                "opta_code": opta,
                "pl_name": o["name"]["display"],
                "pl_team": ((o.get("currentTeam") or {}).get("club") or {}).get("abbr"),
                "pl_position": (o.get("info") or {}).get("position"),
                stat: c["value"],
            })
        info = s.get("pageInfo") or {}
        page += 1
        if page >= info.get("numPages", 0):
            return rows


def fetch(seasons=("2025/26",), refresh: bool = False) -> None:
    frames = []
    for season in seasons:
        cs = COMP_SEASONS[season]
        acc: dict[str, dict] = {}
        ok = 0
        for stat in STATS:
            try:
                rows = _pull(stat, season, cs, refresh)
            except Exception as e:
                print(f"  !! {season} {stat}: {e}", file=sys.stderr)
                continue
            if not rows:
                print(f"  -- {season} {stat}: no data", file=sys.stderr)
                continue
            ok += 1
            for r in rows:
                acc.setdefault(r["opta_code"], {}).update(r)
        df = pd.DataFrame(acc.values())
        df["season"] = season
        frames.append(df)
        print(f"  {season}: {len(df)} players, {ok}/{len(STATS)} stats returned data")

    out = pd.concat(frames, ignore_index=True)
    # FPL stores this as a bare integer code; keep both forms.
    out["code"] = out["opta_code"].str.lstrip("p").astype("int64")
    out.to_csv(PROC / "pulselive_players.csv", index=False)
    print(f"pulselive_players.csv  {len(out):>4} player-seasons, {out.shape[1]} columns")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    fetch(tuple(args) or ("2025/26",), refresh="--refresh" in sys.argv)
