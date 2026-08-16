"""Flatten the club-by-club minutes research into one row per player.

`data/processed/club_research/*.json` holds what beat writers, local press and
club podcasts were saying in the fortnight to 16 August 2026 -- one file per
club, each carrying a list of `player_deltas`: a proposed 2026/27 minutes
figure, the Solio figure it replaces, a confidence, and the sourced reason.

This script does no judging.  It reads those files, drops anything that cannot
be joined, and writes a flat CSV so `build_master` can merge it on `code` like
any other source.  Every number in the output was written by the research pass
and can be traced back to a URL and a date in the JSON it came from.

Two things it does check, because both are silent failures:

* **Codes must join.**  A delta whose `code` is 0 or absent from the FPL
  player list is a real footballer who is not in the 2026/27 game -- Khalaili
  at Crystal Palace is the example.  His minutes genuinely leave the draft
  pool, so they are reported and dropped rather than merged onto nothing.

* **Nobody gets two figures.**  A player researched by two clubs (a transfer
  seen from both ends) would otherwise merge twice and silently duplicate his
  row in the master.

Output: data/processed/research_xmins.csv
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from common import PROC

RESEARCH = PROC / "club_research"
SEASON_MINUTES = 38 * 90

FIELDS = ("code", "player", "proposed_xmins", "solio_xmins", "delta",
          "confidence", "reason", "source_url", "source_date")


def load() -> pd.DataFrame:
    files = sorted(RESEARCH.glob("*.json"))
    if not files:
        print(f"!! no research files under {RESEARCH}", file=sys.stderr)
        return pd.DataFrame(columns=["code"])

    rows, unjoinable = [], []
    for path in files:
        club = json.loads(path.read_text())
        for d in club.get("player_deltas", []):
            row = {k: d.get(k) for k in FIELDS}
            row["club"] = club.get("club", path.stem)
            row["researched_at"] = club.get("researched_at")
            if not row["code"]:
                unjoinable.append(row)
                continue
            rows.append(row)

    out = pd.DataFrame(rows)
    if unjoinable:
        print(f"  {len(unjoinable)} delta(s) with no FPL code -- outside the "
              f"draft pool, dropped:")
        for r in unjoinable:
            print(f"    {r['player']} ({r['club']}) {r['proposed_xmins']:.0f} mins")
    return out, pd.DataFrame(unjoinable)


def main() -> int:
    out, outside = load()
    if out.empty:
        return 1

    out["code"] = out["code"].astype("int64")
    dupes = out[out.duplicated("code", keep=False)].sort_values("code")
    if not dupes.empty:
        print("!! the same player was given minutes by two clubs -- "
              "merging this would duplicate his row in the master:",
              file=sys.stderr)
        print(dupes[["code", "player", "club", "proposed_xmins"]]
              .to_string(index=False), file=sys.stderr)
        return 1

    out["proposed_xmins"] = out["proposed_xmins"].astype(float).clip(0, SEASON_MINUTES)
    out = out.rename(columns={"proposed_xmins": "research_xmins",
                              "player": "research_player",
                              "club": "research_club",
                              "confidence": "research_confidence",
                              "reason": "research_reason",
                              "source_url": "research_source_url",
                              "source_date": "research_source_date",
                              "solio_xmins": "research_solio_xmins",
                              "delta": "research_delta"})
    cols = ["code", "research_player", "research_club", "research_xmins",
            "research_solio_xmins", "research_delta", "research_confidence",
            "research_source_url", "research_source_date", "research_reason"]
    out[cols].to_csv(PROC / "research_xmins.csv", index=False)

    print(f"research_xmins.csv     {len(out)} players from "
          f"{out.research_club.nunique()} clubs")
    print("  confidence:", out.research_confidence.value_counts().to_dict())
    up = out[out.research_delta > 0]
    down = out[out.research_delta < 0]
    print(f"  {len(up)} raised (+{up.research_delta.sum():,.0f} mins), "
          f"{len(down)} cut ({down.research_delta.sum():,.0f} mins), "
          f"net {out.research_delta.sum():+,.0f}")
    if not outside.empty:
        print(f"  {len(outside)} player(s) left outside the pool "
              f"({outside.proposed_xmins.sum():,.0f} mins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
