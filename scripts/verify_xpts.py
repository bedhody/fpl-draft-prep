"""Check that the xPts sheet's live Excel formulas compute what they should.

openpyxl writes formula text but never evaluates it, so a broken reference or
a wrong VLOOKUP column would ship silently.  This recalculates the workbook
with LibreOffice, reads the resulting values, and compares every component
against the same arithmetic done independently in Python.

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

from common import OUT

SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
# goal, assist, clean sheet, defcon, defcon threshold, saves per point,
# conceded per -1, appearance at 60+.  Written out again here on purpose: the
# point of this file is to reproduce the sheet from an independent source, not
# to import it.
SCORING = {"GKP": (10, 3, 4, 0, 99, 3, 2, 2), "DEF": (6, 3, 4, 2, 10, 0, 2, 2),
           "MID": (5, 3, 1, 2, 12, 0, 0, 2), "FWD": (4, 3, 0, 2, 12, 0, 0, 2)}
UPLIFT = 1.32
PENALTY_CONVERSION = 0.8294
MISS_POINTS = 2
TOL = 1e-6


def floor_points(lam, per):
    """E[floor(X/per)] for X ~ Poisson(lam), summed as Poisson tails."""
    lam = np.asarray(lam, dtype=float)
    return sum(1 - poisson.cdf(per * k - 1, lam) for k in range(1, 9))


def recalculate(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(src, tmp / src.name)
    subprocess.run(
        [SOFFICE, "--headless", "--norestore", "--convert-to",
         "xlsx:Calc MS Excel 2007 XML", "--outdir", str(tmp / "out"),
         str(tmp / src.name)],
        check=True, capture_output=True, timeout=600)
    return tmp / "out" / src.name


def main() -> int:
    src = OUT / "FPL_2026_27_draft_data.xlsx"
    calc = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if calc is None:
        if not Path(SOFFICE).exists():
            print("LibreOffice not found; pass a recalculated file as an argument.")
            return 2
        print("recalculating with LibreOffice...")
        calc = recalculate(src)

    d = pd.read_excel(calc, sheet_name="xPts model")
    print(f"read {len(d)} rows from the recalculated workbook")

    failures = []
    Z = (0, 0, 0, 0, 99, 0, 0, 0)
    g = d["Pos"].map(lambda p: SCORING.get(p, Z)[0])
    a = d["Pos"].map(lambda p: SCORING.get(p, Z)[1])
    c = d["Pos"].map(lambda p: SCORING.get(p, Z)[2])
    dc = d["Pos"].map(lambda p: SCORING.get(p, Z)[3])
    thr = d["Pos"].map(lambda p: SCORING.get(p, Z)[4])
    per_sv = d["Pos"].map(lambda p: SCORING.get(p, Z)[5])
    per_gc = d["Pos"].map(lambda p: SCORING.get(p, Z)[6])
    app = d["Pos"].map(lambda p: SCORING.get(p, Z)[7])

    # DefCon: P(actions >= threshold), actions ~ Poisson(lambda x mins/90)
    lam = d["DefCon actions/90"].fillna(0)
    mps = d["Mins per start"].fillna(0)
    spm = d["Saves/match"].fillna(0)
    gapm = d["Goals against/match"].fillna(0)
    mu = lam * mps / 90
    hit = pd.Series(np.where((lam <= 0) | (mps <= 0), 0.0,
                             1 - poisson.cdf(thr - 1, mu)), index=d.index)
    bad = (d["DefCon hit %"].fillna(0) - hit).abs() > 1e-6
    print(f"  {'DefCon hit %':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("DefCon hit %",
                         d.loc[bad, ["Player", "Pos", "DefCon hit %"]].head(8),
                         hit[bad].head(8)))

    expected = {
        # 1 point an appearance, 2 if it lasts 60 minutes, so the rate per 90
        # depends on how long an appearance is.
        "App pts/90": pd.Series(
            np.where(mps <= 0, app,
                     90 / mps.replace(0, np.nan)
                     * np.where(mps >= 60, app, 1.0)), index=d.index),
        "xG pts/90": d["xG/90"].fillna(0) * g,
        "xA pts/90": d["xA/90"].fillna(0) * a * UPLIFT,
        "CS pts/90": d["P(CS)"].fillna(0) * c,
        "DC pts/90": pd.Series(np.where(mps > 0, hit * dc * 90 / mps.replace(0, np.nan), 0),
                               index=d.index),
        "Bonus pts/90": d["Bonus/90"].fillna(0),
        # 1 point per 3 saves and -1 per 2 conceded, both settled per match, so
        # both are E[floor(Poisson/n)] rather than a straight division.
        "Save pts/90": pd.Series(
            np.where((per_sv <= 0) | (spm <= 0), 0.0,
                     floor_points(spm, per_sv.replace(0, 1))), index=d.index),
        "GC pts/90": pd.Series(
            np.where((per_gc <= 0) | (gapm <= 0), 0.0,
                     -floor_points(gapm, per_gc.replace(0, 1))), index=d.index),
        "Cards pts/90": d["Card rate/90"].fillna(0),
    }

    for col, want in expected.items():
        got = d[col].fillna(0)
        bad = (got - want).abs() > TOL
        print(f"  {col:<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
        if bad.any():
            w = pd.Series(np.asarray(want), index=d.index)
            failures.append((col, d.loc[bad, ["Player", "Pos", col]].head(8),
                             w[bad].head(8)))

    total = sum(expected.values())
    bad = (d["xPts/90"].fillna(0) - total).abs() > 1e-5
    print(f"  {'xPts/90 = sum':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("xPts/90", d.loc[bad, ["Player", "Pos", "xPts/90"]].head(8),
                         total[bad].head(8)))

    # Penalties are a season total, added after the minutes scaling because the
    # taker's expected count already prices how much of the season he plays.
    pen = (d["Pens/season"].fillna(0)
           * (PENALTY_CONVERSION * g - (1 - PENALTY_CONVERSION) * MISS_POINTS)
           + d["Pen save pts"].fillna(0))
    bad = (d["Pen pts season"].fillna(0) - pen).abs() > 1e-5
    print(f"  {'Pen pts season':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("Pen pts season",
                         d.loc[bad, ["Player", "Pos", "Pen pts season"]].head(8),
                         pen[bad].head(8)))

    season = total * d["xMins 26/27"].fillna(0) / 90 + pen
    bad = (d["xPts season"].fillna(0) - season).abs() > 1e-4
    print(f"  {'xPts season':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("xPts season",
                         d.loc[bad, ["Player", "Pos", "xPts season"]].head(8),
                         season[bad].head(8)))

    # ---- VORP: replacement level must come from the draftable pool only ----
    # The table's position on the sheet moves whenever a note is added, so find
    # it by its header rather than hard-coding a row number.
    a = pd.read_excel(calc, sheet_name="Assumptions", header=None)
    hdr = a.index[a[1].astype(str).str.strip() == "Squad slots per team"]
    if len(hdr) != 1:
        print("could not find the VORP table on the Assumptions sheet")
        return 1
    top = int(hdr[0]) + 1
    teams_row = a.index[a[0].astype(str).str.strip() == "Teams in league"]
    if len(teams_row) != 1:
        print("could not find the league size on the Assumptions sheet")
        return 1
    n_teams = a.iloc[int(teams_row[0]), 1]
    slots = {a.iloc[top + i, 0]: a.iloc[top + i, 1] for i in range(4)}
    print(f"\nVORP settings read from the sheet: {int(n_teams)} teams, "
          + ", ".join(f"{p} {int(s)}" for p, s in slots.items()))

    pool = d[d["Draftable"] == True]                                # noqa: E712
    repl = {}
    for p, s in slots.items():
        v = pool[pool["Pos"] == p]["xPts season"].dropna().sort_values(ascending=False)
        k = int(n_teams * s)
        repl[p] = v.iloc[k] if len(v) > k else 0.0
    sheet_repl = {a.iloc[top + i, 0]: a.iloc[top + i, 2] for i in range(4)}
    for p in repl:
        ok = abs(sheet_repl[p] - repl[p]) < 0.05
        print(f"  replacement {p:<4} sheet {sheet_repl[p]:>7.1f}  python {repl[p]:>7.1f}"
              f"   {'match' if ok else 'MISMATCH'}")
        if not ok:
            failures.append((f"replacement level {p}",
                             pd.DataFrame({"sheet": [sheet_repl[p]]}),
                             pd.Series([repl[p]])))

    want_vorp = d["xPts season"] - d["Pos"].map(repl)
    got = pd.to_numeric(d["VORP"], errors="coerce")
    bad = (got - want_vorp).abs() > 0.05
    bad &= got.notna() & want_vorp.notna()
    print(f"  {'VORP':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("VORP", d.loc[bad, ["Player", "Pos", "VORP"]].head(8),
                         want_vorp[bad].head(8)))

    want_per = want_vorp / d["Price"]
    gotp = pd.to_numeric(d["VORP per £m"], errors="coerce")
    bad = (gotp - want_per).abs() > 0.05
    bad &= gotp.notna() & want_per.notna()
    print(f"  {'VORP per £m':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("VORP per £m",
                         d.loc[bad, ["Player", "Pos", "VORP per £m"]].head(8),
                         want_per[bad].head(8)))

    if failures:
        print("\nFAILURES")
        for col, rows, want in failures:
            print(f"\n-- {col}")
            rows = rows.copy()
            rows["expected"] = want.values
            print(rows.to_string(index=False))
        return 1

    print("\nEvery formula in the xPts sheet evaluates to the intended value.")
    top = d.nlargest(12, "xPts season")[
        ["Player", "Team 26/27", "Pos", "xMins 26/27", "xPts/90", "xPts season",
         "Pts 25/26"]]
    print("\ntop of the sheet as it stands, on the default xMins (Solio's "
          "forecast where it has one):")
    print(top.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
