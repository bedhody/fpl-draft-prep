"""The xPts sheet, rebuilt from first principles as live Excel formulas.

    xPts per 90  =  appearance + xG + xA + CS + DC + bonus
                    + saves + goals conceded + cards
    xPts season  =  xPts per 90  x  (xMins / 90)  +  penalties

All nine of FPL's scoring elements are present.  Penalties sit outside the per
90 term on purpose: a penalty is taken by whoever is on the pitch and holds the
duty, so the expected count already prices availability and must not be scaled
by minutes a second time.

Everything is written as a real formula referencing a visible input, so the
sheet can be reasoned about and changed in Excel rather than re-run here.
Blue-filled columns are inputs meant to be overwritten with your own judgement;
everything else is computed from them.

The scoring multipliers live on the Assumptions sheet and are looked up by
position, so a rule change is one edit rather than 700.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import OUT, PROC

INPUT_FILL = PatternFill("solid", fgColor="DDEBF7")     # editable
DERIVED_FILL = PatternFill("solid", fgColor="F2F2F2")   # from the data
HEAD_FILL = PatternFill("solid", fgColor="404040")

# Scoring, straight from the FPL API's game_config for 2026/27.
SCORING = [
    # position, goal, assist, clean sheet, defcon, appearance(60+),
    # defcon threshold, saves per point, goals conceded per -1 point
    ("GKP", 10, 3, 4, 0, 2, 99, 3, 2),
    ("DEF", 6, 3, 4, 2, 2, 10, 0, 2),
    ("MID", 5, 3, 1, 2, 2, 12, 0, 0),
    ("FWD", 4, 3, 0, 2, 2, 12, 0, 0),
]

NOTES = [
    ("xMins", "Your minutes forecast for 2026/27. Defaults to Solio's own "
              "forecast, which is forward-looking and so already prices "
              "injuries and late World Cup returns; where Solio has no view it "
              "falls back to 2025/26 minutes. Column 'xMins source' says "
              "which. This is the single biggest lever in the model and the "
              "one number no dataset can settle for you."),
    ("Saves", "Goalkeepers only, 1 point per 3 saves settled every match. "
              "'Saves/match' is the club's expected saves, derived from the "
              "goals it is expected to concede and the quality of the shots it "
              "concedes them from. The sheet turns that into points with a "
              "Poisson, because two saves in a match are worth nothing: over "
              "2025/26 that flooring was worth 0.213 points per save, not "
              "0.333."),
    ("Goals conceded", "Goalkeepers and defenders, -1 per 2 conceded, settled "
                       "every match. Same Poisson treatment: ignoring the "
                       "per-match floor makes the penalty 57% too harsh."),
    ("Cards", "-1 a yellow, -3 a red. Booking rate is a real player trait -- "
              "year-on-year correlation +0.47 over 766 repeat player-seasons -- "
              "so yellows are the player's own three-season rate shrunk toward "
              "his position. Reds are not: at one per 200 nineties an "
              "individual red rate is noise, so everyone gets the position's."),
    ("Penalties", "Kept out of xG entirely and credited back here, because "
                  "penalty duty moves between seasons and Cole Palmer's 2025/26 "
                  "expected goals were 44% penalties. 'Pens/season' is the "
                  "club's expected penalties times this player's share of them, "
                  "which follows the listed taker order and how much of the "
                  "season he is expected to play. Season-level, not per 90."),
    ("P(CS)", "Probability the player's club keeps a clean sheet in a given match. "
              "Defaults to the value implied by season-long betting markets: "
              "bet365 and Spreadex points totals, converted to goal difference, "
              "then to goals for and against, then to expected goals in each of "
              "38 fixtures, then Poisson. Covers all 20 clubs including the "
              "promoted three. Validated on five seasons: the ordering across "
              "clubs is sound, the league-wide level carries about +/-13%."),
    ("Assist uplift", "FPL awards assists more loosely than Opta (rebounds, "
                      "deflections, won penalties). Across 2025/26 that was 765 "
                      "FPL assists vs 580 Opta assists, so 1.32. Both xG models "
                      "measure the Opta definition, so xA needs this to become "
                      "FPL assists. Set to 1 to switch it off."),
    ("xG source", "Adjusted xGOT where a player has shot-placement history, "
                  "otherwise blended xG. Column 'xG basis' says which was used. "
                  "Either way it is scaled to the non-penalty share of his "
                  "2025/26 expected goals, so penalties are counted once, in "
                  "the penalty column, and not twice."),
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
    ("Appearance", "1 point for playing, 2 for 60 minutes or more, earned per "
                   "match rather than per 90. Appearances are xMins divided by "
                   "'Mins per start', so a player on short cameos banks more "
                   "appearances for the same minutes but only one point each. "
                   "A regular starter lands just above 2 per 90. 'App pts "
                   "25/26' is what he actually earned last season, for "
                   "comparison only -- it does not feed the model."),
]


def build_rows() -> pd.DataFrame:
    m = pd.read_csv(PROC / "master_2025_26.csv")

    # Default P(CS): derived from season-long betting markets where available,
    # since last season's clean-sheet count was the weakest of the three
    # predictors tested (R2 0.14, against 0.22 for xGA).  The market prices this
    # summer's transfers and covers the promoted clubs, which no backward-looking
    # statistic can.  Falls back to last season's rate if the market file is
    # missing.  Keyed on the club, never on a player, so a defender who moved
    # does not import his old defence.
    odds_path = PROC / "cs_from_odds.csv"
    if odds_path.exists():
        o = pd.read_csv(odds_path)
        cs = dict(zip(o["team_short"], o["p_cs"].round(3)))
        print(f"  P(CS) default: market-implied for {len(cs)} clubs")
    else:
        teams = pd.read_csv(PROC / "understat_teams.csv")
        teams = teams[teams.season == "2025/26"]
        cs = dict(zip(teams["team_short"], teams["cs_rate"].round(3)))
        print("  P(CS) default: last season's clean-sheet rate (no market file)")

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
    # Both models measure expected goals including penalties.  Penalties are
    # credited separately and explicitly, so they come out here first -- by the
    # player's own measured non-penalty share, from Understat's npxG.  The same
    # share is applied to the adjusted-xGOT figure, which assumes shot placement
    # is as good from open play as from the spot; that is close enough to true
    # and errs by a fraction of a goal.
    np_share = m.get("np_share")
    np_share = pd.Series(1.0, index=m.index) if np_share is None else np_share.fillna(1.0)
    d["xG_p90"] = (adj90.where(use_adj, blend90) * np_share).round(4)
    d["xG_basis"] = use_adj.map({True: "adjusted xGOT", False: "blended xG"})
    d.loc[d["xG_p90"].isna(), "xG_basis"] = ""
    d["np_share"] = np_share.round(3)
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

    # --- club defensive volume, and the three remaining scoring elements ----
    d["saves_per_match"] = m.get("saves_per_match")
    d["ga_per_match"] = m.get("ga_per_match")
    d["card_pts_p90"] = m.get("card_pts_p90")
    # Penalties are a season total, not a rate: the expected count already
    # prices how much of the season the taker is available for.
    d["pens_season"] = m.get("pens_expected")
    d["pen_save_pts"] = m.get("pen_save_pts")

    # --- appearance points, from minutes per appearance --------------------
    # Not a frozen per-90 rate.  Appearance points are earned per match, so a
    # substitute who plays 15 minutes at a time banks far more of them per 90
    # than a starter -- Christian Nørgaard's 101 minutes across seven cameos
    # worked out at 6.2 appearance points per 90.  Freezing that and scaling it
    # by a full season's projected minutes claims 187 appearances.  The sheet
    # instead computes appearances as xMins / mins per start, which is the same
    # input the DefCon model already uses, so the answer moves with the minutes
    # assumption instead of ignoring it.
    played, full60 = m.get("gw_played"), m.get("gw_full_60")
    d["app_pts_2526"] = (2 * full60 + (played - full60)).astype("Float64")

    # Solio's own minutes forecast, as an alternative to last season's actual.
    # It is the better starting point for anyone who was injured or came back
    # late from the World Cup, since it is forward-looking.
    d["solio_season_xmins"] = m.get("solio_season_xmins")
    d["xmins_pattern"] = m.get("xmins_pattern")
    # Solio's forecast is the default because it is forward-looking: it prices
    # this summer's injuries and the late World Cup returns, which last
    # season's minutes cannot.  Players Solio has no view on keep their 2025/26
    # minutes, and 'xMins source' records which of the two a row is using.
    solio = m.get("solio_season_xmins")
    d["xMins_input"] = solio.where(solio.notna(), m["minutes"]).round(0)
    d["xmins_source"] = np.where(solio.notna(), "Solio", "25/26 actual")
    d.loc[d["xMins_input"].isna(), "xmins_source"] = ""
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
    ("xMins source", "xmins_source", "text"),
    ("P(CS)", "pcs_input", "input"),
    ("xG/90", "xG_p90", "derived"),
    ("xG basis", "xG_basis", "text"),
    ("Non-pen share", "np_share", "derived"),
    ("Placement ratio", "placement_ratio", "derived"),
    ("Placement sample xG", "hist_xG", "derived"),
    ("xA/90", "xA_p90", "derived"),
    ("DefCon actions/90", "defcon_lambda", "derived"),
    ("Mins per start", "mins_per_start", "input"),
    ("DefCon hit %", None, "formula"),
    ("Bonus/90", "bonus_p90", "derived"),
    ("App pts 25/26", "app_pts_2526", "derived"),
    ("Saves/match", "saves_per_match", "input"),
    ("Goals against/match", "ga_per_match", "input"),
    ("Card rate/90", "card_pts_p90", "derived"),
    ("Pens/season", "pens_season", "input"),
    ("Pen save pts", "pen_save_pts", "derived"),
    ("App pts/90", None, "formula"),
    ("xG pts/90", None, "formula"),
    ("xA pts/90", None, "formula"),
    ("CS pts/90", None, "formula"),
    ("DC pts/90", None, "formula"),
    ("Bonus pts/90", None, "formula"),
    ("Save pts/90", None, "formula"),
    ("GC pts/90", None, "formula"),
    ("Cards pts/90", None, "formula"),
    ("xPts/90", None, "formula"),
    ("Pen pts season", None, "formula"),
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
    # The two "1 point per N" divisors, looked up once per row so the Poisson
    # series below stays short enough to read.
    ("_saves per pt", None, "helper"),
    ("_conceded per pt", None, "helper"),
]

POSITIONS = ["GKP", "DEF", "MID", "FWD"]
SQUAD_SLOTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}   # an FPL 15-man squad
LEAGUE_TEAMS = 8
# Measured over 2022/23-2025/26: 381 penalties taken, 316 scored.
PENALTY_CONVERSION = 0.8294
# First row of the four-position VORP table on the Assumptions sheet.  It sits
# below the notes, so it moves whenever a note is added -- hence the constant.
VORP_ROW = 36
TEAMS_ROW = VORP_ROW - 2        # the "Teams in league" input, referenced by LARGE()


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
              "DefCon threshold", "Saves per point", "Conceded per -1"])
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
    a["A13"] = "Penalty conversion rate"
    a["B13"] = PENALTY_CONVERSION
    a["B13"].fill = INPUT_FILL
    a["A14"] = "Points for a missed penalty"
    a["B14"] = 2

    # ---- VORP settings, below the notes so nothing above shifts ----------
    head = VORP_ROW - 1
    a.cell(row=TEAMS_ROW - 2, column=1, value="VORP settings").font = Font(bold=True)
    a.cell(row=TEAMS_ROW, column=1, value="Teams in league")
    a.cell(row=TEAMS_ROW, column=2, value=LEAGUE_TEAMS).fill = INPUT_FILL
    for c, label in enumerate(("Position", "Squad slots per team",
                               "Replacement xPts"), start=1):
        cell = a.cell(row=head, column=c, value=label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEAD_FILL
    for i, pos in enumerate(POSITIONS):
        a.cell(row=VORP_ROW + i, column=1, value=pos)
        a.cell(row=VORP_ROW + i, column=2,
               value=SQUAD_SLOTS[pos]).fill = INPUT_FILL
        # column C is filled in below, once the helper columns have letters
    tail = VORP_ROW + len(POSITIONS) + 1
    a.cell(row=tail, column=1,
           value=("Replacement level is the best player at that position still "
                  "available once every team has filled its slots: the "
                  "(teams x slots + 1)-th best. Only players registered for "
                  "2026/27 count toward it.")).alignment = Alignment(
        wrap_text=True, vertical="top")
    a.row_dimensions[tail].height = 46

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
    S = "Assumptions!$A$4:$I$7"

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

        mps = f"{col['Mins per start']}{i}"
        # 1 point for playing, 2 for 60 minutes or more, earned per match.
        # Appearances per 90 = 90 / minutes per start, so a shorter shift means
        # more appearances for the same minutes -- but only one point each.
        ws[f"{col['App pts/90']}{i}"] = (
            f"=IF(N({mps})<=0,{mult(6)},"
            f"90/N({mps})*IF(N({mps})>=60,{mult(6)},1))")
        ws[f"{col['xG pts/90']}{i}"] = f"=N({col['xG/90']}{i})*{mult(2)}"
        ws[f"{col['xA pts/90']}{i}"] = (
            f"=N({col['xA/90']}{i})*{mult(3)}*Assumptions!$B$11")
        ws[f"{col['CS pts/90']}{i}"] = f"=N({col['P(CS)']}{i})*{mult(4)}"
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

        # FPL's two counting rules -- 1 point per 3 saves, -1 per 2 conceded --
        # are settled every match, so the season total is not the season count
        # divided by 3 or 2.  E[floor(X/m)] = sum over k>=1 of P(X >= m*k), a
        # sum of Poisson tails, which is what the series below is.  Eight terms
        # is exact to 1e-4 at any rate a real club produces.  Skipping this
        # would make saves 56% too generous and goals conceded 57% too harsh.
        ws[f"{col['_saves per pt']}{i}"] = f"={mult(8)}"
        ws[f"{col['_conceded per pt']}{i}"] = f"={mult(9)}"
        per_sv = f"{col['_saves per pt']}{i}"
        per_gc = f"{col['_conceded per pt']}{i}"
        spm = f"{col['Saves/match']}{i}"
        gapm = f"{col['Goals against/match']}{i}"
        sv_series = "+".join(
            f"(1-POISSON({per_sv}*{k}-1,N({spm}),TRUE))" for k in range(1, 9))
        gc_series = "+".join(
            f"(1-POISSON({per_gc}*{k}-1,N({gapm}),TRUE))" for k in range(1, 9))
        ws[f"{col['Save pts/90']}{i}"] = (
            f"=IF(OR({per_sv}=0,N({spm})<=0),0,{sv_series})")
        ws[f"{col['GC pts/90']}{i}"] = (
            f"=IF(OR({per_gc}=0,N({gapm})<=0),0,-({gc_series}))")
        ws[f"{col['Cards pts/90']}{i}"] = f"=N({col['Card rate/90']}{i})"

        ws[f"{col['xPts/90']}{i}"] = (
            f"=SUM({col['App pts/90']}{i}:{col['Cards pts/90']}{i})")
        # Penalties are already an expected count for the whole season -- the
        # taker's share prices his availability -- so they are added after the
        # minutes scaling, not inside it.
        ws[f"{col['Pen pts season']}{i}"] = (
            f"=N({col['Pens/season']}{i})*(Assumptions!$B$13*{mult(2)}"
            f"-(1-Assumptions!$B$13)*Assumptions!$B$14)"
            f"+N({col['Pen save pts']}{i})")
        ws[f"{col['xPts season']}{i}"] = (
            f"=N({col['xPts/90']}{i})*N({col['xMins 26/27']}{i})/90"
            f"+N({col['Pen pts season']}{i})")

        season = f"{col['xPts season']}{i}"
        for pos in POSITIONS:
            ws[f"{col['_pool ' + pos]}{i}"] = (
                f'=IF(AND(${col["Draftable"]}{i}=TRUE,${col["Pos"]}{i}="{pos}"),'
                f'{season},"")')
        ws[f"{col['VORP']}{i}"] = (
            f'=IFERROR({season}-VLOOKUP(${col["Pos"]}{i},'
            f'Assumptions!$A${VORP_ROW}:$C${VORP_ROW + 3},3,FALSE),"")')
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
        r = VORP_ROW + i
        a.cell(row=r, column=3,
               value=f"=IFERROR(LARGE({pool},$B${TEAMS_ROW}*B{r}+1),0)"
               ).number_format = "0.0"
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
