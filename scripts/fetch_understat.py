"""Understat — the only free, genuinely independent xG/xA model left.

understat.com stopped embedding its data in the page HTML (which is why the
usual scraper libraries broke).  The site now calls an AJAX endpoint instead:

    GET https://understat.com/getLeagueData/{league}/{season}

which returns teams (per-match xG/xGA/npxG/PPDA/deep), players (season
aggregates) and dates (fixtures) in one payload.
"""
from __future__ import annotations

import sys

import pandas as pd

from common import PROC, get_json, norm_name, team_code

URL = "https://understat.com/getLeagueData/EPL/{season}"
HEADERS = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://understat.com/league/EPL"}

# Understat labels a season by its starting year: 2025 == 2025/26.
SEASONS = [2021, 2022, 2023, 2024, 2025]

NUM = ["games", "time", "goals", "xG", "assists", "xA", "shots", "key_passes",
       "yellow_cards", "red_cards", "npg", "npxG", "xGChain", "xGBuildup"]


def fetch(refresh: bool = False) -> None:
    players, teams = [], []
    for s in SEASONS:
        d = get_json(URL.format(season=s), f"understat/league_{s}.json",
                     headers=HEADERS, refresh=refresh)

        p = pd.DataFrame(d["players"])
        p["season"] = f"{s}/{str(s + 1)[2:]}"
        players.append(p)

        for tid, t in d["teams"].items():
            h = pd.DataFrame(t["history"])
            teams.append({
                "season": f"{s}/{str(s + 1)[2:]}",
                "understat_team_id": tid,
                "team": t["title"],
                "team_short": team_code(t["title"]),
                "matches": len(h),
                "xG": h["xG"].sum(), "xGA": h["xGA"].sum(),
                "npxG": h["npxG"].sum(), "npxGA": h["npxGA"].sum(),
                "scored": h["scored"].sum(), "missed": h["missed"].sum(),
                "deep": h["deep"].sum(), "deep_allowed": h["deep_allowed"].sum(),
                "xpts": h["xpts"].sum(),
                # Team-level clean sheets, counted from match results rather
                # than from any player's record -- a defender who moved clubs
                # must not carry his old club's defence to his new one.
                "clean_sheets": int((h["missed"] == 0).sum()),
                "cs_rate": round((h["missed"] == 0).mean(), 4),
            })
        print(f"  {s}/{str(s + 1)[2:]}: {len(d['players'])} players, {len(d['teams'])} teams")

    pl = pd.concat(players, ignore_index=True)
    for c in NUM:
        pl[c] = pd.to_numeric(pl[c], errors="coerce")
    pl = pl.rename(columns={"id": "understat_id", "player_name": "understat_name",
                            "team_title": "understat_team", "time": "minutes"})
    pl["name_key"] = pl["understat_name"].map(norm_name)
    # A mid-season transfer shows as "Team A,Team B"; keep the last club.
    pl["team_short"] = pl["understat_team"].str.split(",").str[-1].map(team_code)
    pl["understat_xGI"] = pl["xG"] + pl["xA"]
    pl.to_csv(PROC / "understat_players.csv", index=False)

    tm = pd.DataFrame(teams)
    tm.to_csv(PROC / "understat_teams.csv", index=False)
    print(f"understat_players.csv  {len(pl):>4} player-seasons")
    print(f"understat_teams.csv    {len(tm):>4} team-seasons")


if __name__ == "__main__":
    fetch(refresh="--refresh" in sys.argv)
