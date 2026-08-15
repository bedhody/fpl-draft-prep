"""Prove the cross-source joins are right, and separate real mis-joins from
known definitional differences between the sources.

Goals and minutes are objective, so a disagreement there means a bad join.
Assists are NOT objective: FPL uses a looser definition than Opta (it credits
rebounds, deflections and won penalties), so FPL assists should always be >=
Opta assists.  That is checked as a one-directional invariant instead.
"""
from __future__ import annotations

import sys

import pandas as pd

from common import PROC

MINUTE_TOL = 0.25       # Understat times substitutions differently
GOAL_SLACK = 2          # a couple of deflection/own-goal judgement calls


def main() -> int:
    m = pd.read_csv(PROC / "master_2025_26.csv")
    failures = []

    def compare(label, a, b, *, exact=True, tol=0.0, slack=0, min_mins=0):
        sub = m[m[a].notna() & m[b].notna()]
        if min_mins:
            sub = sub[sub["pl_mins_played"] >= min_mins]
        if sub.empty:
            print(f"  {label:<34} no overlap")
            return
        if exact:
            miss = sub[sub[a] != sub[b]]
        else:
            denom = sub[[a, b]].abs().max(axis=1).replace(0, 1)
            miss = sub[(sub[a] - sub[b]).abs() / denom > tol]
        print(f"  {label:<34} {len(sub) - len(miss):>4}/{len(sub):<4} agree"
              + (f"   ({len(miss)} off)" if len(miss) else ""))
        if len(miss) > slack:
            failures.append((label, miss[["player", a, b]].head(15)))

    print("JOIN INTEGRITY -- objective facts, must agree")
    compare("goals: PL vs FPL", "pl_goals", "fpl_goals_scored")
    compare("goals: PL vs Understat", "pl_goals", "us_goals", slack=GOAL_SLACK)
    compare("goals: PL vs FotMob", "pl_goals", "fm_goals")
    compare("minutes: PL vs FPL", "pl_mins_played", "fpl_minutes", exact=False, tol=0.03)
    compare("minutes: PL vs FotMob", "pl_mins_played", "fm_mins", exact=False, tol=0.03)
    # Understat clocks a substitute from the announced minute and ignores added
    # time, so it undercounts cameo appearances by a widening fraction.  Only
    # players with real minutes can tell us whether a join is wrong.
    compare("minutes: PL vs Understat (300m+)", "pl_mins_played", "us_minutes",
            exact=False, tol=MINUTE_TOL, min_mins=300)

    print("\nDEFINITIONAL DIFFERENCES -- expected, not errors")
    sub = m[(m["pl_mins_played"] > 0) & m["us_minutes"].notna()]
    print(f"  Understat/PL minutes ratio        median "
          f"{(sub['us_minutes'] / sub['pl_mins_played']).median():.3f}, "
          f"under 300 mins median "
          f"{(sub[sub.pl_mins_played < 300]['us_minutes'] / sub[sub.pl_mins_played < 300]['pl_mins_played']).median():.3f}"
          "  (use FPL minutes as the single per-90 denominator)")
    sub = m[m["pl_goal_assist"].notna() & m["fpl_assists"].notna()]
    diff = sub["fpl_assists"] - sub["pl_goal_assist"]
    print(f"  FPL assists vs Opta assists       {sub['fpl_assists'].sum():.0f} vs "
          f"{sub['pl_goal_assist'].sum():.0f}  "
          f"(+{sub['fpl_assists'].sum() / sub['pl_goal_assist'].sum() - 1:.1%})")
    if (diff < 0).any():
        failures.append(("FPL assists below Opta assists",
                         sub[diff < 0][["player", "pl_goal_assist", "fpl_assists"]]))
    else:
        print("  FPL >= Opta for every player      OK (looser FPL definition)")

    print("\nCOVERAGE")
    tot = len(m)
    for c, label in [("played_2526", "played in the PL in 2025/26"),
                     ("draftable_2627", "registered for 2026/27"),
                     ("fpl_minutes", "FPL 2025/26 season record"),
                     ("us_xG", "Understat xG"),
                     ("fm_xGOT", "FotMob xGOT (needs a shot on target)"),
                     ("pl_touches_in_opp_box", "PL/Opta counting stats")]:
        n = m[c].sum() if m[c].dtype == bool else m[c].notna().sum()
        print(f"  {label:<38} {int(n):>4}/{tot}")

    if failures:
        print("\nFAILED CHECKS")
        for label, rows in failures:
            print(f"\n-- {label}")
            print(rows.to_string(index=False))
        return 1
    print("\nAll join-integrity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
