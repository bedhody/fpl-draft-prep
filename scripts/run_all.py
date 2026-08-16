"""Rebuild everything, in order.  Safe to re-run: HTTP responses are cached
under data/raw, so a second run is fast.  Pass --refresh to re-download.

Two inputs are deliberately NOT rebuilt here, because neither is cheap and
neither is reproducible tonight:

* ``fetch_transfermarkt`` -> ``injury_model``.  Hours of scraping behind a rate
  limit for a 244MB cache.  ``data/processed/injury_model.csv`` is committed
  instead; see CLAUDE.md.
* ``club_research_report``.  Reads a dated snapshot of what beat writers said
  in the fortnight to 16 Aug 2026.  Re-running the research gives different
  answers, not the same ones.

Everything else runs, and anything that has to be skipped says so loudly at the
end.  A pipeline that quietly drops a column is worse than one that crashes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import adjusted_xgot
import bps_remodel
import cs_from_odds
import build_master
import build_panel
import cards_model
import defcon_model
import export_excel
import fetch_draft_adp
import fetch_fotmob
import fetch_fpl
import fetch_gameweeks
import fetch_pulselive
import fetch_solio
import fetch_understat
import fetch_vaastav
import penalties_model
import research_xmins
import team_defence
import verify_master
import verify_xpts
import xg_bakeoff
import xpts_model
from common import PROC

SEASONS = ("2021/22", "2022/23", "2023/24", "2024/25", "2025/26")

skipped: list[str] = []


def solio(refresh: bool) -> None:
    """Solio's forecast, from a CSV the user exports by hand.

    There is no API.  The file has to be downloaded from Solio and dropped in
    ~/Downloads/projection.csv (or passed to fetch_solio.py directly).  Without
    it the master has no `solio_season_xmins`, and penalties_model -- which
    prices a taker's share by availability -- dies on the missing column.
    """
    if not fetch_solio.SRC_DEFAULT.exists():
        skipped.append(
            f"Solio projections -- {fetch_solio.SRC_DEFAULT} not found. "
            "Export it from Solio, then re-run. Without it there are no "
            "xMins baselines and no penalty shares.")
        print(f"!! skipping: {fetch_solio.SRC_DEFAULT} not found")
        return
    fetch_solio.main()


def draft_adp(refresh: bool) -> None:
    """Average draft position, scraped from completed public draft leagues.

    Unlike the other fetches this one has no per-response cache -- it walks
    6000 league ids and keeps the picks, not the HTTP bodies.  So it is skipped
    when its output already exists; --refresh re-scrapes.
    """
    if (PROC / "draft_adp.csv").exists() and not refresh:
        print("draft_adp.csv already present -- pass --refresh to re-scrape")
        return
    fetch_draft_adp.main()


def main() -> int:
    refresh = "--refresh" in sys.argv

    for label, fn in [
        ("FPL API", lambda: fetch_fpl.fetch(refresh)),
        ("Understat", lambda: fetch_understat.fetch(refresh)),
        ("FotMob", lambda: fetch_fotmob.fetch(refresh)),
        ("Premier League (Pulselive)", lambda: fetch_pulselive.fetch(SEASONS, refresh)),
        ("FPL season snapshots", lambda: fetch_vaastav.fetch(refresh)),
        ("Gameweek detail", lambda: fetch_gameweeks.fetch(refresh)),
        ("Solio projections", lambda: solio(refresh)),
        ("Draft ADP", lambda: draft_adp(refresh)),
    ]:
        print(f"\n=== {label} ===")
        fn()

    print("\n=== merge ===")
    build_master.build()
    print("\n=== verify ===")
    rc = verify_master.main()
    if rc:
        print("!! join integrity failed -- stopping before export")
        return rc

    print("\n=== 2026/27 BPS re-model ===")
    bps_remodel.main()

    print("\n=== multi-season panel ===")
    build_panel.build()
    print("\n=== xG model bakeoff ===")
    xg_bakeoff.main()

    # Adjusted xGOT needs the panel; the master then needs adjusted xGOT and the
    # BPS re-model, so the merge runs a second time to pick them up.
    print("\n=== adjusted xGOT ===")
    adjusted_xgot.main()
    print("\n=== re-merge with derived columns ===")
    build_master.build()

    # The club-level models need the market-implied goals from cs_from_odds;
    # the player-level ones need the master.  Both then go back into the master,
    # so the merge runs a third time to pick them up.
    print("\n=== clean sheets from market odds ===")
    cs_from_odds.main()
    print("\n=== club shots faced, saves and goals conceded ===")
    team_defence.main()
    # DefCon reads the raw gameweek rows and the master's names, and its output
    # carries `mins_per_start` -- which xpts_model needs for every player's
    # match count.  It has to land in the master before the third merge.
    print("\n=== defensive contributions ===")
    defcon_model.main()
    # Flattens the committed club research.  It reads only files in git, so
    # unlike the research itself this step does rebuild from scratch tonight.
    print("\n=== club research -> adjusted xMins ===")
    research_xmins.main()
    print("\n=== cards ===")
    cards_model.main()
    print("\n=== penalties ===")
    penalties_model.main()
    print("\n=== re-merge with the scoring models ===")
    build_master.build()

    print("\n=== workbook ===")
    export_excel.main()
    xpts_model.main()
    print("\n=== verify xPts formulas ===")
    rc = verify_xpts.main()

    if skipped:
        print(f"\n!! {len(skipped)} step(s) SKIPPED -- the workbook is built "
              f"without them:")
        for s in skipped:
            print(f"  - {s}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
