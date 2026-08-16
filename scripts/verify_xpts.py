"""Check the xPts model, now that it is Python rather than Excel formulas.

The old version of this file reimplemented the sheet's formulas in Python and
compared the two.  That worked because they were genuinely independent: one was
written in Excel, one in Python.  Now that the model is Python, reimplementing
it in Python would prove nothing -- it would only show that a function agrees
with a copy of itself.

So the checks here are of four different kinds, and only the last one is a
comparison against a reimplementation:

  1. Identities.  Two of the model's pieces are closed forms of something with
     a slower definition.  E[floor(X/m)] is computed as a sum of Poisson tails;
     it is checked against the definition of an expectation, sum n*P(X=n) over
     the floor.  P(clearing a threshold) is computed as 1 - cdf; it is checked
     against summing the probability mass above the threshold.  These are real
     proofs: the two routes share no code.

  2. Worked examples.  Whole players with round-numbered inputs, whose points
     can be worked out by hand and are written out here as literals.

  3. Invariants on the real output.  Nobody plays more than 38 matches; an
     outfielder never gets save points; a card is never worth more than zero.
     These catch the class of bug that produced 62 appearances in a 38-match
     season, which no comparison against a reimplementation would have found,
     because both implementations had it.

  4. The workbook.  The ten columns that are still live formulas are
     recalculated through LibreOffice and compared against Python, which is the
     one place a genuine second implementation still exists.

Usage: scripts/verify_xpts.py [path-to-recalculated-xlsx]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

import xpts_calc
import xpts_model
from common import OUT

SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
TOL = 1e-6

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'pass' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(f"{label} {detail}".strip())


# --------------------------------------------------------------------------
# 1. Identities: the closed forms against their definitions
# --------------------------------------------------------------------------
def check_identities() -> None:
    print("\nidentities (closed form vs definition, no shared code)")

    # E[floor(X/m)] the slow way: sum over every outcome of floor(n/m) x P(X=n).
    # The model computes it as sum over k>=1 of P(X >= m*k).
    worst = 0.0
    for lam in (0.4, 0.9, 1.4, 2.0, 2.9, 3.5, 4.4):
        for per in (2, 3):
            n = np.arange(0, 60)
            direct = float((np.floor(n / per) * poisson.pmf(n, lam)).sum())
            fast = float(xpts_calc.floor_points(lam, per))
            worst = max(worst, abs(direct - fast))
    check("E[floor(Poisson/m)] matches sum of n*P(X=n)", worst < 1e-4,
          f"max gap {worst:.2e} over 14 rate/divisor pairs")

    # P(X >= threshold) the slow way: add up the mass at and above it.
    worst = 0.0
    for lam in (6.0, 9.0, 10.0, 13.5):
        for mps in (45.0, 60.0, 75.0, 90.0):
            for thr in (10, 12):
                mu = lam * mps / 90
                direct = float(poisson.pmf(np.arange(thr, 200), mu).sum())
                fast = float(xpts_calc.defcon_hit(lam, mps, thr))
                worst = max(worst, abs(direct - fast))
    check("P(actions >= threshold) matches summed mass above it", worst < 1e-9,
          f"max gap {worst:.2e} over 32 combinations")

    # The floor must never exceed a plain division, and never be negative.
    lam = np.linspace(0.05, 6.0, 60)
    f3 = xpts_calc.floor_points(lam, 3)
    check("floor(X/3) never exceeds X/3", bool((f3 <= lam / 3 + 1e-12).all()))
    check("floor(X/3) is never negative", bool((f3 >= 0).all()))


# --------------------------------------------------------------------------
# 2. Worked examples: whole players, arithmetic done by hand
# --------------------------------------------------------------------------
def worked_examples() -> None:
    print("\nworked examples (inputs chosen so the answer can be done by hand)")

    d = pd.DataFrame([
        # An ever-present defender: 38 starts of 90 minutes.
        dict(pos="DEF", xMins_input=3420.0, mins_per_start=90.0, xG_p90=0.10,
             xA_p90=0.05, pcs_input=0.25, defcon_lambda=0.0, bonus_p90=0.20,
             saves_per_match=0.0, ga_per_match=0.0, card_pts_p90=-0.15,
             pens_season=0.0, pen_save_pts=0.0),
        # A midfielder used only as a late substitute: 20 x 30 minutes.
        dict(pos="MID", xMins_input=600.0, mins_per_start=30.0, xG_p90=0.0,
             xA_p90=0.0, pcs_input=0.0, defcon_lambda=0.0, bonus_p90=0.0,
             saves_per_match=0.0, ga_per_match=0.0, card_pts_p90=0.0,
             pens_season=0.0, pen_save_pts=0.0),
        # The cap: a forecast implying 62 matches, against 38 that exist.
        dict(pos="MID", xMins_input=2804.0, mins_per_start=45.0, xG_p90=0.0,
             xA_p90=0.0, pcs_input=0.0, defcon_lambda=0.0, bonus_p90=0.0,
             saves_per_match=0.0, ga_per_match=0.0, card_pts_p90=0.0,
             pens_season=0.0, pen_save_pts=0.0),
        # A striker with penalty duty and nothing else.
        dict(pos="FWD", xMins_input=0.0, mins_per_start=0.0, xG_p90=0.0,
             xA_p90=0.0, pcs_input=0.0, defcon_lambda=0.0, bonus_p90=0.0,
             saves_per_match=0.0, ga_per_match=0.0, card_pts_p90=0.0,
             pens_season=10.0, pen_save_pts=0.0),
    ])
    s = xpts_calc.score(d)

    # -- the ever-present defender ------------------------------------------
    # 38 matches of 90 minutes, all of them past the hour: 38 x 2 = 76.
    check("38 x 90-min starts give 38 matches", abs(s.matches[0] - 38.0) < TOL)
    check("  and 76 appearance points", abs(s.app_pts_season[0] - 76.0) < TOL)
    # 0.10 xG/90 x 6 points a goal = 0.60.  0.05 xA/90 x 3 x 1.32 = 0.198.
    # 0.25 P(CS) x 4 = 1.00.  Bonus 0.20.  Cards -0.15.  Saves and conceded
    # both zero because the club concedes nothing in this example.
    check("  xG 0.10/90 x 6 = 0.600", abs(s.xg_pts_p90[0] - 0.600) < TOL)
    check("  xA 0.05/90 x 3 x 1.32 = 0.198", abs(s.xa_pts_p90[0] - 0.198) < TOL)
    check("  CS 0.25 x 4 = 1.000", abs(s.cs_pts_p90[0] - 1.000) < TOL)
    rate = 0.600 + 0.198 + 1.000 + 0.20 + 0.0 + 0.0 - 0.15
    check(f"  rate sums to {rate:.3f}", abs(s.rate_pts_p90[0] - rate) < TOL)
    # 3420 minutes is exactly 38 nineties, so the season total is 38 x rate + 76.
    check(f"  season = 38 x rate + 76 = {38 * rate + 76:.3f}",
          abs(s.xpts_season[0] - (38 * rate + 76)) < 1e-9)

    # -- the substitute ------------------------------------------------------
    # 600 minutes at 30 a time is 20 appearances, none of which reach the hour,
    # so 1 point each rather than 2.
    check("20 x 30-min cameos give 20 matches", abs(s.matches[1] - 20.0) < TOL)
    check("  and 20 appearance points, not 40",
          abs(s.app_pts_season[1] - 20.0) < TOL)

    # -- the cap -------------------------------------------------------------
    # 2804 / 45 = 62.3 matches, which do not exist.  Capped at 38, each
    # appearance is 2804 / 38 = 73.8 minutes, which clears the hour, so 2 points
    # each rather than the 1 his 45-minute history implied.
    check("62 implied matches are capped at 38", abs(s.matches[2] - 38.0) < TOL)
    check("  minutes per appearance become 73.8",
          abs(s.mins_per_app[2] - 2804 / 38) < 1e-9)
    check("  so 76 appearance points, not 62",
          abs(s.app_pts_season[2] - 76.0) < TOL)

    # -- the penalty taker ---------------------------------------------------
    # 10 penalties, converted 82.94% of the time.  A forward's goal is worth 4,
    # a miss costs 2.  10 x (0.8294 x 4 - 0.1706 x 2) = 10 x 2.9764 = 29.764.
    want = 10 * (0.8294 * 4 - (1 - 0.8294) * 2)
    check(f"10 penalties are worth {want:.3f} to a forward",
          abs(s.pen_pts_season[3] - want) < 1e-9)
    check("  and are not scaled by his zero minutes",
          abs(s.xpts_season[3] - want) < 1e-9)


# --------------------------------------------------------------------------
# 3. Invariants on the real output
# --------------------------------------------------------------------------
def invariants() -> pd.DataFrame:
    print("\ninvariants on the real player set")
    d = xpts_model.build_rows()
    s = xpts_calc.score(d)
    j = d.join(s)

    check(f"nobody plays more than {xpts_calc.MATCHES} matches",
          bool((s.matches <= xpts_calc.MATCHES + 1e-9).all()),
          f"max {s.matches.max():.2f}")
    check("no negative match count", bool((s.matches >= -1e-12).all()))
    # A player with no 2026/27 position has left the league; he scores nothing
    # rather than being scored as some default position.
    known = j.pos.isin(list(xpts_calc.SCORING))
    check("players with no 2026/27 position score nothing",
          bool((s.xpts_season[~known].abs() < TOL).all()),
          f"{int((~known).sum())} such players")
    # An appearance is worth 1 or 2, so the season total is bounded by both.
    ok = ((s.app_pts_season[known] >= s.matches[known] - 1e-9)
          & (s.app_pts_season[known] <= 2 * s.matches[known] + 1e-9))
    check("appearance points sit between 1 and 2 a match", bool(ok.all()),
          f"{int((~ok).sum())} of {int(known.sum())} rows outside")
    check("appearance points never exceed 76", bool((s.app_pts_season <= 76 + 1e-9).all()),
          f"max {s.app_pts_season.max():.1f}")
    # The regression that filling mins per start fixed: a real minutes forecast
    # must always buy appearance points.
    playing = known & (j.xMins_input.fillna(0) > 900)
    check("everyone forecast 900+ minutes earns appearance points",
          bool((s.app_pts_season[playing] > 0).all()),
          f"{int((s.app_pts_season[playing] <= 0).sum())} of "
          f"{int(playing.sum())} on zero")

    outfield = j.pos.isin(["DEF", "MID", "FWD"])
    check("outfielders get no save points",
          bool((s.save_pts_p90[outfield].abs() < TOL).all()))
    check("midfielders and forwards are not docked for goals conceded",
          bool((s.gc_pts_p90[j.pos.isin(["MID", "FWD"])].abs() < TOL).all()))
    check("keepers earn no DefCon",
          bool((s.dc_pts_season[j.pos == "GKP"].abs() < TOL).all()))
    check("goals conceded is never a positive score",
          bool((s.gc_pts_p90 <= TOL).all()))
    check("cards are never a positive score", bool((s.cards_pts_p90 <= TOL).all()))
    check("no NaN in the season total", bool(s.xpts_season.notna().all()))

    # The floor identity again, but on the real rates rather than a test grid:
    # a save is worth less than a third of a point once per-match flooring bites.
    gk = j[(j.pos == "GKP") & (j.saves_per_match > 0)]
    per_save = (s.save_pts_p90[gk.index] / gk.saves_per_match).mean()
    check("flooring makes a save worth well under 1/3 of a point",
          0.15 < per_save < 0.28, f"mean {per_save:.3f} vs 0.333 unfloored")
    return j


# --------------------------------------------------------------------------
# 4. The workbook: the ten live formulas, recalculated
# --------------------------------------------------------------------------
def recalculate(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(src, tmp / src.name)
    subprocess.run(
        [SOFFICE, "--headless", "--norestore", "--convert-to",
         "xlsx:Calc MS Excel 2007 XML", "--outdir", str(tmp / "out"),
         str(tmp / src.name)],
        check=True, capture_output=True, timeout=600)
    return tmp / "out" / src.name


def check_workbook(calc: Path) -> None:
    print(f"\nlive formulas in the workbook, recalculated")
    d = pd.read_excel(calc, sheet_name="xPts model")
    print(f"  read {len(d)} rows")

    # Rebuild the inputs the sheet was written from and score them again.  The
    # sheet's ten formulas are the second implementation here, so this compares
    # Excel against Python rather than Python against itself.
    src = xpts_model.build_rows()
    s = xpts_calc.score(src)
    want = {
        "Matches": s.matches,
        "App pts season": s.app_pts_season,
        "DC pts season": s.dc_pts_season,
        "Pen pts season": s.pen_pts_season,
        "xPts season": s.xpts_season,
        "xPts/90": s.xpts_p90,
        "Rate pts/90": s.rate_pts_p90,
        "CS pts/90": s.cs_pts_p90,
    }
    if len(d) != len(src):
        check("row counts agree", False, f"sheet {len(d)}, python {len(src)}")
        return
    for name, series in want.items():
        got = pd.to_numeric(d[name], errors="coerce").fillna(0).to_numpy()
        exp = np.asarray(series, dtype=float)
        gap = np.abs(got - exp)
        tol = 1e-4 if "season" in name.lower() or name == "Matches" else 1e-6
        check(f"{name:<15} {len(d) - int((gap > tol).sum())}/{len(d)}",
              bool((gap <= tol).all()), f"max gap {gap.max():.2e}")

    # ---- VORP, read back from wherever the table ended up on the sheet ----
    a = pd.read_excel(calc, sheet_name="Assumptions", header=None)
    hdr = a.index[a[1].astype(str).str.strip() == "Squad slots per team"]
    teams_row = a.index[a[0].astype(str).str.strip() == "Teams in league"]
    if len(hdr) != 1 or len(teams_row) != 1:
        check("VORP table found on the Assumptions sheet", False)
        return
    top = int(hdr[0]) + 1
    n_teams = int(a.iloc[int(teams_row[0]), 1])
    slots = {a.iloc[top + i, 0]: int(a.iloc[top + i, 1]) for i in range(4)}
    print(f"  VORP settings: {n_teams} teams, "
          + ", ".join(f"{p} {n}" for p, n in slots.items()))

    repl = xpts_calc.replacement_levels(
        pd.Series(np.asarray(s.xpts_season, dtype=float), index=src.index),
        src["pos"], src["draftable"], n_teams, slots)
    for i, p in enumerate(slots):
        sheet = float(a.iloc[top + i, 2])
        check(f"replacement {p}", abs(sheet - repl[p]) < 0.05,
              f"sheet {sheet:.1f}, python {repl[p]:.1f}")

    vorp = pd.to_numeric(d["VORP"], errors="coerce")
    want_vorp = pd.Series(np.asarray(s.xpts_season, dtype=float)) - d["Pos"].map(repl)
    ok = vorp.notna() & want_vorp.notna()
    gap = (vorp[ok] - want_vorp[ok]).abs()
    check(f"VORP            {int((gap <= 0.05).sum())}/{int(ok.sum())}",
          bool((gap <= 0.05).all()), f"max gap {gap.max():.3f}")


def main() -> int:
    failures.clear()
    check_identities()
    worked_examples()
    j = invariants()

    calc = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if calc is None and Path(SOFFICE).exists():
        print("\nrecalculating the workbook with LibreOffice...")
        calc = recalculate(OUT / "FPL_2026_27_draft_data.xlsx")
    if calc is None:
        print("\nLibreOffice not found; skipping the workbook check.")
        failures.append("workbook not checked")
    else:
        check_workbook(calc)

    if failures:
        print(f"\n{len(failures)} FAILURES")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nAll checks pass.")
    top = j.nlargest(12, "xpts_season")[
        ["player", "team", "pos", "xMins_input", "matches", "xpts_p90",
         "xpts_season", "pts_2526"]]
    print("\ntop of the sheet on the default xMins (Solio's forecast where it "
          "has one):")
    print(top.round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
