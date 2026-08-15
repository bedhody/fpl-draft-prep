"""The xPts sheet, rebuilt from first principles as live Excel formulas.

    xPts per 90  =  appearance + xG + xA + CS + DC + bonus
    xPts season  =  xPts per 90  x  (xMins / 90)

Everything is written as a real formula referencing a visible input, so the
sheet can be reasoned about and changed in Excel rather than re-run here.
Blue-filled columns are inputs meant to be overwritten with your own judgement;
everything else is computed from them.

The scoring multipliers live on the Assumptions sheet and are looked up by
position, so a rule change is one edit rather than 700.
"""
from __future__ import annotations

import sys

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import OUT, PROC

INPUT_FILL = PatternFill("solid", fgColor="DDEBF7")     # editable
DERIVED_FILL = PatternFill("solid", fgColor="F2F2F2")   # from the data
HEAD_FILL = PatternFill("solid", fgColor="404040")

# Scoring, straight from the FPL API's game_config for 2026/27.
SCORING = [
    # position, goal, assist, clean sheet, defcon, appearance(60+), defcon threshold
    ("GKP", 10, 3, 4, 0, 2, 99),
    ("DEF", 6, 3, 4, 2, 2, 10),
    ("MID", 5, 3, 1, 2, 2, 12),
    ("FWD", 4, 3, 0, 2, 2, 12),
]

NOTES = [
    ("xMins", "Your minutes forecast for 2026/27. Defaults to 2025/26 minutes. "
              "This is the single biggest lever in the model and the one number "
              "no dataset can give you."),
    ("P(CS)", "Probability the player's club keeps a clean sheet in a given match. "
              "Defaults to the club's 2025/26 clean sheet rate. Promoted clubs "
              "are blank on purpose. Reference values, including the rate implied "
              "by expected goals against, are on the Teams sheet."),
    ("Assist uplift", "FPL awards assists more loosely than Opta (rebounds, "
                      "deflections, won penalties). Across 2025/26 that was 765 "
                      "FPL assists vs 580 Opta assists, so 1.32. Both xG models "
                      "measure the Opta definition, so xA needs this to become "
                      "FPL assists. Set to 1 to switch it off."),
    ("xG source", "Adjusted xGOT where a player has shot-placement history, "
                  "otherwise blended xG. Column 'xG basis' says which was used."),
    ("DefCon", "Not a frozen rate. 'DefCon actions/90' is the player's underlying "
               "rate of qualifying defensive actions, shrunk toward the position "
               "mean for thin samples. The sheet turns that into 'DefCon hit %' "
               "with a Poisson over 'Mins per start' against the threshold on the "
               "table above -- so if you change minutes per start, the hit rate "
               "moves with it, steeply. A defender on 10 actions/90 clears a "
               "threshold of 10 in 14% of 60-minute starts and 54% of 90-minute "
               "starts. Both minutes inputs matter: xMins sets how many starts "
               "he gets, mins per start sets how likely each one pays."),
    ("Bonus", "Bonus per 90 from 2025/26 replayed under the 2026/27 BPS weighting. "
              "Modelled as a rate rather than a share of points, so it does not "
              "depend on the rest of the model."),
    ("VORP", "xPts season minus the replacement level at that position — the "
             "best player still on the board once every team has filled its "
             "slots. Set the league size and slots below. Raw xPts asks who "
             "scores most; VORP asks who scores most above what you would get "
             "at that slot anyway."),
    ("VORP per £m", "VORP divided by price. Pure VORP measures scarcity alone; "
                    "this measures scarcity against the £100m budget that "
                    "still binds you in a normal FPL season."),
    ("Appearance", "2 points for 60+ minutes, 1 below. Modelled here as the "
                   "player's actual 2025/26 appearance points per 90, which "
                   "captures substitutes properly. Regular starters land near 2."),
]


def build_rows() -> pd.DataFrame:
    m = pd.read_csv(PROC / "master_2025_26.csv")

    # Default P(CS): the CLUB's own 2025/26 clean-sheet rate, counted from match
    # results.  Keyed on the club, not on any player -- otherwise a defender who
    # changed clubs over the summer would import his old defence's record.
    # Promoted clubs have no PL record and are deliberately left blank.
    teams = pd.read_csv(PROC / "understat_teams.csv")
    teams = teams[teams.season == "2025/26"]
    cs = dict(zip(teams["team_short"], teams["cs_rate"].round(3)))

    d = pd.DataFrame({
        "player": m["player"],
        "team": m["team_2627"],
        "pos": m["position"],
        "pos_2526": m.get("pos_2526"),
        "price": m["price_2627"],
        "draftable": m["draftable_2627"],
        "status": m["status"],
        "news": m["news"],
        "mins_2526": m["minutes"],
        "pts_2526": m["fpl_total_points"],
    })

    # --- xG basis: adjusted xGOT where there is placement history ----------
    ratio = m.get("placement_ratio")
    adj90 = m.get("adjusted_xGOT_p90")
    blend90 = m["xG_blend_p90"]
    use_adj = adj90.notna() & ratio.notna() & (m.get("hist_xG", 0).fillna(0) > 0)
    d["xG_p90"] = adj90.where(use_adj, blend90).round(4)
    d["xG_basis"] = use_adj.map({True: "adjusted xGOT", False: "blended xG"})
    d.loc[d["xG_p90"].isna(), "xG_basis"] = ""
    d["placement_ratio"] = ratio.round(3)
    d["hist_xG"] = m.get("hist_xG").round(1)

    d["xA_p90"] = m["xA_blend_p90"].round(4)

    # --- DefCon: an action RATE plus minutes per start, not a frozen rate ---
    # The sheet turns these into P(clearing the threshold) with a live Poisson,
    # so the answer responds to minutes instead of ignoring them.
    d["defcon_lambda"] = m.get("defcon_lambda")
    d.loc[m["position"] == "GKP", "defcon_lambda"] = 0.0
    d["mins_per_start"] = m.get("mins_per_start")

    d["bonus_p90"] = m.get("bonus_new_p90").round(4)

    # --- appearance points per 90, measured rather than assumed ------------
    played, full60 = m.get("gw_played"), m.get("gw_full_60")
    app_pts = 2 * full60 + (played - full60)
    d["appearance_p90"] = (app_pts / m["nineties"]).where(m["nineties"] > 0).round(3)

    # Solio's own minutes forecast, as an alternative to last season's actual.
    # It is the better starting point for anyone who was injured or came back
    # late from the World Cup, since it is forward-looking.
    d["solio_season_xmins"] = m.get("solio_season_xmins")
    d["xmins_pattern"] = m.get("xmins_pattern")
    d["xMins_input"] = m["minutes"]
    d["pcs_input"] = m["team_2627"].map(cs)

    d = d[d["draftable"] | d["mins_2526"].notna()]
    return d.sort_values("pts_2526", ascending=False, na_position="last")


COLUMNS = [
    # (header, source column or None, kind)
    ("Player", "player", "text"),
    ("Team 26/27", "team", "text"),
    ("Pos", "pos", "text"),
    ("Pos 25/26", "pos_2526", "text"),
    ("Draftable", "draftable", "text"),
    ("Price", "price", "num"),
    ("Status", "status", "text"),
    ("News", "news", "text"),
    ("Mins 25/26", "mins_2526", "num"),
    ("Pts 25/26", "pts_2526", "num"),
    ("xMins (Solio)", "solio_season_xmins", "derived"),
    ("xMins pattern", "xmins_pattern", "text"),
    ("xMins 26/27", "xMins_input", "input"),
    ("P(CS)", "pcs_input", "input"),
    ("xG/90", "xG_p90", "derived"),
    ("xG basis", "xG_basis", "text"),
    ("Placement ratio", "placement_ratio", "derived"),
    ("Placement sample xG", "hist_xG", "derived"),
    ("xA/90", "xA_p90", "derived"),
    ("DefCon actions/90", "defcon_lambda", "derived"),
    ("Mins per start", "mins_per_start", "input"),
    ("DefCon hit %", None, "formula"),
    ("Bonus/90", "bonus_p90", "derived"),
    ("Appearance/90", "appearance_p90", "derived"),
    ("App pts/90", None, "formula"),
    ("xG pts/90", None, "formula"),
    ("xA pts/90", None, "formula"),
    ("CS pts/90", None, "formula"),
    ("DC pts/90", None, "formula"),
    ("Bonus pts/90", None, "formula"),
    ("xPts/90", None, "formula"),
    ("xPts season", None, "formula"),
    ("VORP", None, "formula"),
    ("VORP per £m", None, "formula"),
    # Helper columns: xPts season, but blank unless the player is draftable and
    # plays that position. LARGE() over one of these gives the replacement
    # level without needing an array formula, which keeps the sheet portable.
    ("_pool GKP", None, "helper"),
    ("_pool DEF", None, "helper"),
    ("_pool MID", None, "helper"),
    ("_pool FWD", None, "helper"),
]

POSITIONS = ["GKP", "DEF", "MID", "FWD"]
SQUAD_SLOTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}   # an FPL 15-man squad
LEAGUE_TEAMS = 8


def main() -> int:
    from openpyxl import load_workbook

    d = build_rows()
    path = OUT / "FPL_2026_27_draft_data.xlsx"
    if not path.exists():
        print(f"!! {path} not found -- run export_excel.py first", file=sys.stderr)
        return 1
    wb = load_workbook(path)

    for name in ("xPts model", "Assumptions"):
        if name in wb.sheetnames:
            del wb[name]

    # ---------------- Assumptions ----------------
    a = wb.create_sheet("Assumptions", 1)
    a["A1"] = "Scoring (FPL 2026/27, read from the game's own config)"
    a["A1"].font = Font(bold=True)
    a.append([])
    a.append(["Pos", "Goal", "Assist", "Clean sheet", "DefCon", "Appearance 60+",
              "DefCon threshold"])
    for row in SCORING:
        a.append(list(row))
    for c in a[3]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL

    a["A10"] = "Global inputs"
    a["A10"].font = Font(bold=True)
    a["A11"] = "Assist uplift (FPL assists / Opta assists)"
    a["B11"] = 1.32
    a["B11"].fill = INPUT_FILL
    a["A12"] = "DefCon points per qualifying match"
    a["B12"] = 2

    # ---- VORP settings, below the notes so nothing above shifts ----------
    a["A25"] = "VORP settings"
    a["A25"].font = Font(bold=True)
    a["A26"] = "Teams in league"
    a["B26"] = LEAGUE_TEAMS
    a["B26"].fill = INPUT_FILL
    a["A28"] = "Position"
    a["B28"] = "Squad slots per team"
    a["C28"] = "Replacement xPts"
    for c in (a["A28"], a["B28"], a["C28"]):
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
    for i, pos in enumerate(POSITIONS):
        r = 29 + i
        a.cell(row=r, column=1, value=pos)
        cell = a.cell(row=r, column=2, value=SQUAD_SLOTS[pos])
        cell.fill = INPUT_FILL
        # filled in below, once the xPts sheet's helper columns have letters
    a["A34"] = ("Replacement level is the best player at that position still "
                "available once every team has filled its slots: the "
                "(teams x slots + 1)-th best. Only players registered for "
                "2026/27 count toward it.")
    a["A34"].alignment = Alignment(wrap_text=True, vertical="top")
    a.row_dimensions[34].height = 46

    a["A15"] = "Notes"
    a["A15"].font = Font(bold=True)
    r = 16
    for label, text in NOTES:
        a.cell(row=r, column=1, value=label).font = Font(bold=True)
        c = a.cell(row=r, column=2, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        a.row_dimensions[r].height = 46
        r += 1
    a.column_dimensions["A"].width = 16
    a.column_dimensions["B"].width = 105

    # ---------------- xPts model ----------------
    ws = wb.create_sheet("xPts model", 1)
    headers = [h for h, _, _ in COLUMNS]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="bottom")

    col = {h: get_column_letter(i + 1) for i, (h, _, _) in enumerate(COLUMNS)}
    P = col["Pos"]
    S = "Assumptions!$A$4:$G$7"

    for i, rec in enumerate(d.to_dict("records"), start=2):
        vals = []
        for h, src, kind in COLUMNS:
            if kind == "formula":
                vals.append(None)
            else:
                v = rec.get(src)
                vals.append(None if pd.isna(v) else v)
        ws.append(vals)

        # VLOOKUP the multiplier for this row's position out of Assumptions.
        def mult(n):
            return f"IFERROR(VLOOKUP(${P}{i},{S},{n},FALSE),0)"

        ws[f"{col['App pts/90']}{i}"] = (
            f"=IF({col['Appearance/90']}{i}=\"\",{mult(6)},{col['Appearance/90']}{i})")
        ws[f"{col['xG pts/90']}{i}"] = f"=N({col['xG/90']}{i})*{mult(2)}"
        ws[f"{col['xA pts/90']}{i}"] = (
            f"=N({col['xA/90']}{i})*{mult(3)}*Assumptions!$B$11")
        ws[f"{col['CS pts/90']}{i}"] = f"=N({col['P(CS)']}{i})*{mult(4)}"
        mps = f"{col['Mins per start']}{i}"
        lam = f"{col['DefCon actions/90']}{i}"
        # The threshold must fail safe to 99, not 0: an unknown position makes
        # the VLOOKUP fail, and POISSON(-1, ...) is a #NUM! error that would
        # propagate all the way to xPts season.
        thr = (f"IFERROR(VLOOKUP(${P}{i},{S},7,FALSE),99)")
        ws[f"{col['DefCon hit %']}{i}"] = (
            f'=IF(OR(N({lam})=0,N({mps})=0),0,'
            f'1-POISSON({thr}-1,N({lam})*N({mps})/90,TRUE))')
        # POISSON, not POISSON.DIST: the latter is a post-2007 function and
        # needs an _xlfn. prefix in the file format, which openpyxl does not
        # add, so it silently evaluates to an error. The legacy name works
        # unprefixed in both Excel and LibreOffice.
        # Season DefCon points = 2 x starts x P(hit), and starts = xMins / mins
        # per start, so per 90 that is 2 x P(hit) x 90 / mins per start.
        ws[f"{col['DC pts/90']}{i}"] = (
            f"=IF(N({mps})=0,0,{col['DefCon hit %']}{i}*{mult(5)}*90/N({mps}))")
        ws[f"{col['Bonus pts/90']}{i}"] = f"=N({col['Bonus/90']}{i})"
        ws[f"{col['xPts/90']}{i}"] = (
            f"=SUM({col['App pts/90']}{i}:{col['Bonus pts/90']}{i})")
        ws[f"{col['xPts season']}{i}"] = (
            f"=N({col['xPts/90']}{i})*N({col['xMins 26/27']}{i})/90")

        season = f"{col['xPts season']}{i}"
        for pos in POSITIONS:
            ws[f"{col['_pool ' + pos]}{i}"] = (
                f'=IF(AND(${col["Draftable"]}{i}=TRUE,${col["Pos"]}{i}="{pos}"),'
                f'{season},"")')
        ws[f"{col['VORP']}{i}"] = (
            f'=IFERROR({season}-VLOOKUP(${col["Pos"]}{i},'
            f'Assumptions!$A$29:$C$32,3,FALSE),"")')
        ws[f"{col['VORP per £m']}{i}"] = (
            f'=IFERROR({col["VORP"]}{i}/{col["Price"]}{i},"")')

        for h, _, kind in COLUMNS:
            cell = ws[f"{col[h]}{i}"]
            if kind == "input":
                cell.fill = INPUT_FILL
            elif kind in ("derived", "formula"):
                cell.fill = DERIVED_FILL
            if kind in ("num", "derived", "formula") and h != "Pts 25/26":
                cell.number_format = "0.000" if kind != "num" else "0"

    last = ws.max_row
    for i, pos in enumerate(POSITIONS):
        pool = f"'xPts model'!${col['_pool ' + pos]}$2:${col['_pool ' + pos]}${last}"
        a.cell(row=29 + i, column=3,
               value=f"=IFERROR(LARGE({pool},$B$26*B{29 + i}+1),0)").number_format = "0.0"
    for letter in (col[f"_pool {p}"] for p in POSITIONS):
        ws.column_dimensions[letter].hidden = True

    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{ws.max_row}"
    widths = {"Player": 22, "News": 30, "xG basis": 14,
              "Placement sample xG": 12, "Appearance/90": 12}
    for h, _, _ in COLUMNS:
        ws.column_dimensions[col[h]].width = widths.get(h, 11)

    wb.save(path)
    print(f"added 'xPts model' ({ws.max_row - 1} players) and 'Assumptions' to {path}")
    print("  blue cells are yours to edit: xMins 26/27, P(CS), and the assist uplift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
