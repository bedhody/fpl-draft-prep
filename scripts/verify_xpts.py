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

import pandas as pd

from common import OUT

SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
SCORING = {"GKP": (10, 3, 4, 0), "DEF": (6, 3, 4, 2),
           "MID": (5, 3, 1, 2), "FWD": (4, 3, 0, 2)}
UPLIFT = 1.32
TOL = 1e-6


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

    g = d["Pos"].map(lambda p: SCORING.get(p, (0, 0, 0, 0))[0])
    a = d["Pos"].map(lambda p: SCORING.get(p, (0, 0, 0, 0))[1])
    c = d["Pos"].map(lambda p: SCORING.get(p, (0, 0, 0, 0))[2])
    dc = d["Pos"].map(lambda p: SCORING.get(p, (0, 0, 0, 0))[3])

    expected = {
        "App pts/90": d["Appearance/90"].fillna(2.0),
        "xG pts/90": d["xG/90"].fillna(0) * g,
        "xA pts/90": d["xA/90"].fillna(0) * a * UPLIFT,
        "CS pts/90": d["P(CS)"].fillna(0) * c,
        "DC pts/90": d["DefCon rate"].fillna(0) * dc,
        "Bonus pts/90": d["Bonus/90"].fillna(0),
    }

    failures = []
    for col, want in expected.items():
        got = d[col].fillna(0)
        bad = (got - want).abs() > TOL
        print(f"  {col:<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
        if bad.any():
            failures.append((col, d.loc[bad, ["Player", "Pos", col]].head(8),
                             want[bad].head(8)))

    total = sum(expected.values())
    bad = (d["xPts/90"].fillna(0) - total).abs() > 1e-5
    print(f"  {'xPts/90 = sum':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("xPts/90", d.loc[bad, ["Player", "Pos", "xPts/90"]].head(8),
                         total[bad].head(8)))

    season = total * d["xMins 26/27"].fillna(0) / 90
    bad = (d["xPts season"].fillna(0) - season).abs() > 1e-4
    print(f"  {'xPts season':<16} {len(d) - int(bad.sum()):>4}/{len(d)} match")
    if bad.any():
        failures.append(("xPts season",
                         d.loc[bad, ["Player", "Pos", "xPts season"]].head(8),
                         season[bad].head(8)))

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
    print("\ntop of the sheet as it stands (xMins defaulted to 2025/26 minutes):")
    print(top.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
