"""Rebuild everything, in order.  Safe to re-run: HTTP responses are cached
under data/raw, so a second run is fast.  Pass --refresh to re-download.
"""
from __future__ import annotations

import sys

import adjusted_xgot
import bps_remodel
import cs_from_odds
import build_master
import build_panel
import export_excel
import fetch_fotmob
import fetch_fpl
import fetch_gameweeks
import fetch_pulselive
import fetch_understat
import fetch_vaastav
import verify_master
import verify_xpts
import xg_bakeoff
import xpts_model

SEASONS = ("2021/22", "2022/23", "2023/24", "2024/25", "2025/26")


def main() -> int:
    refresh = "--refresh" in sys.argv

    for label, fn in [
        ("FPL API", lambda: fetch_fpl.fetch(refresh)),
        ("Understat", lambda: fetch_understat.fetch(refresh)),
        ("FotMob", lambda: fetch_fotmob.fetch(refresh)),
        ("Premier League (Pulselive)", lambda: fetch_pulselive.fetch(SEASONS, refresh)),
        ("FPL season snapshots", lambda: fetch_vaastav.fetch(refresh)),
        ("Gameweek detail", lambda: fetch_gameweeks.fetch(refresh)),
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

    print("\n=== clean sheets from market odds ===")
    cs_from_odds.main()

    print("\n=== workbook ===")
    export_excel.main()
    xpts_model.main()
    print("\n=== verify xPts formulas ===")
    return verify_xpts.main()


if __name__ == "__main__":
    sys.exit(main())
