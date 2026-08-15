"""Write the single workbook to draft from.

Sheets
  Players          one row per player, grouped left-to-right by what it is for
  Data dictionary  every column: what it means and which source it came from
  Teams            2025/26 team attack/defence, for clean-sheet work
  Fixtures         the 2026/27 fixture list
  Model bakeoff    which xG/xA model won, and by how much
  Match review     the handful of rows no source could be matched automatically
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from common import OUT, PROC

# (output column, source column, description, source)
SPEC: list[tuple[str, str, str, str]] = [
    # -- identity ---------------------------------------------------------
    ("Player", "player", "Official Premier League display name", "PL"),
    ("Team 26/27", "team_2627", "Club for the season being drafted", "FPL"),
    ("Pos", "position", "FPL position for 2026/27: GKP/DEF/MID/FWD", "FPL"),
    ("Pos 25/26", "pos_2526", "Position held last season. If it differs from Pos, last season's DefCon points were earned against a different threshold", "gameweek data"),
    ("Price 26/27", "price_2627", "FPL price at season start (£m)", "FPL"),
    ("Draftable", "draftable_2627", "Registered in the FPL game for 2026/27", "FPL"),
    ("Played 25/26", "played_2526", "Made at least one PL appearance in 2025/26", "PL"),
    ("Age", "age", "Age in years at 2026/27 GW1", "FPL"),
    ("Status", "status", "a=available, i=injured, s=suspended, d=doubtful, u=unavailable", "FPL"),
    ("News", "news", "FPL injury/availability note", "FPL"),
    ("Chance next GW %", "chance_of_playing_next_round", "FPL's stated chance of playing", "FPL"),
    ("Opta ID", "opta_code", "Opta player id; the key every source is joined on", "PL/FPL"),
    ("FPL ID", "fpl_id", "FPL element id", "FPL"),
    # -- set-piece duty ---------------------------------------------------
    ("Pens order", "penalties_order", "1 = first-choice penalty taker", "FPL"),
    ("Corners/IFK order", "corners_and_indirect_freekicks_order", "1 = first-choice corner taker", "FPL"),
    ("Direct FK order", "direct_freekicks_order", "1 = first-choice direct free-kick taker", "FPL"),
    # -- playing time -----------------------------------------------------
    ("Mins", "minutes", "Minutes played in 2025/26", "FPL"),
    ("Apps", "pl_appearances", "Appearances in 2025/26", "PL"),
    ("Starts", "fpl_starts", "Starts in 2025/26", "FPL"),
    ("90s", "nineties", "Minutes / 90", "derived"),
    ("Mins per app", "mins_per_app", "Minutes divided by appearances", "derived"),
    # -- FPL points -------------------------------------------------------
    ("FPL points", "fpl_total_points", "Total FPL points scored in 2025/26", "FPL"),
    ("FPL pts/90", "fpl_points_p90", "FPL points per 90 minutes", "derived"),
    ("Goals", "fpl_goals_scored", "Goals scored", "FPL"),
    ("Assists (FPL)", "fpl_assists", "FPL assists - looser than Opta's, and what pays points", "FPL"),
    ("Assists (Opta)", "pl_goal_assist", "Opta assists; ~32% fewer than FPL's across the league", "PL"),
    ("Clean sheets", "fpl_clean_sheets", "Clean sheets credited by FPL", "FPL"),
    ("Goals conceded", "fpl_goals_conceded", "Goals conceded while on the pitch", "FPL"),
    ("Bonus", "fpl_bonus", "Bonus points actually earned in 2025/26", "FPL"),
    ("Bonus (new rules)", "bonus_new", "Bonus if 2025/26 were replayed under the 2026/27 CBI weighting", "BPS re-model"),
    ("Bonus change", "bonus_delta", "Bonus (new rules) minus Bonus", "BPS re-model"),
    ("BPS", "fpl_bps", "Bonus points system total as scored in 2025/26", "FPL"),
    ("BPS (new rules)", "bps_new", "BPS total under the 2026/27 CBI weighting (1 per 3 actions, was 1 per 2)", "BPS re-model"),
    ("BPS change", "bps_delta", "BPS (new rules) minus BPS", "BPS re-model"),
    ("Points (new rules)", "points_new", "2025/26 total points restated with the new bonus", "BPS re-model"),
    ("Bonus per point", "bonus_per_point", "Bonus divided by total points", "derived"),
    ("Bonus per non-bonus point", "bonus_per_base_point", "Bonus divided by (total points - bonus). The consistent multiplier to apply to a bonus-exclusive xPts", "derived"),
    ("DefCon actions", "fpl_defensive_contribution", "Qualifying defensive actions: CBI+tackles for DEF, plus recoveries for MID/FWD. NOT points", "FPL"),
    ("DefCon actions/90", "fpl_defensive_contribution_p90", "Qualifying defensive actions per 90; the threshold is 10 (DEF) / 12 (MID,FWD) per match", "derived"),
    ("DefCon matches", "defcon_matches", "Matches that actually cleared the threshold - this is what paid points", "gameweek data"),
    ("DefCon points", "defcon_points", "2 x DefCon matches: the points the DefCon rule actually delivered in 2025/26", "gameweek data"),
    ("DefCon hit rate", "defcon_hit_rate", "DefCon matches divided by starts, at the threshold the player faced in 2025/26", "gameweek data"),
    ("DefCon rate at 10", "defcon_rate_at_10", "Share of starts clearing 10 actions - the defender threshold", "gameweek data"),
    ("DefCon rate at 12", "defcon_rate_at_12", "Share of starts clearing 12 actions - the midfielder/forward threshold", "gameweek data"),
    # -- minutes risk -----------------------------------------------------
    ("Start rate", "start_rate", "Starts divided by 38", "gameweek data"),
    ("60+ min rate", "full_60_rate", "Share of appearances lasting 60+ minutes (the 2-point threshold)", "gameweek data"),
    ("Blanks", "blanks", "Matches played returning 2 points or fewer", "gameweek data"),
    ("Hauls", "hauls", "Matches returning 10+ points", "gameweek data"),
    ("Points SD", "points_sd", "Standard deviation of gameweek points", "gameweek data"),
    ("Best GW", "best_gw", "Highest single-gameweek score", "gameweek data"),
    # -- expected goals ---------------------------------------------------
    ("xG (Opta)", "xG_opta", "Opta expected goals - best next-season goal predictor tested", "FPL"),
    ("xG (Understat)", "xG_understat", "Understat expected goals - runs ~8-15% high vs actual", "Understat"),
    ("xG (blend)", "xG_blend", "Mean of the two xG models", "derived"),
    ("npxG (Understat)", "us_npxG", "Non-penalty expected goals", "Understat"),
    ("xG/90 (Opta)", "xG_opta_p90", "Opta xG per 90", "derived"),
    ("xG/90 (Understat)", "xG_understat_p90", "Understat xG per 90", "derived"),
    ("xG/90 (blend)", "xG_blend_p90", "Blended xG per 90", "derived"),
    ("xGOT", "fm_xGOT", "Post-shot xG: shot quality AFTER placement. FBref's PSxG equivalent", "FotMob"),
    ("xGOT/90", "fm_xGOT_p90", "xGOT per 90", "derived"),
    ("Placement ratio", "placement_ratio", "Career xGOT/xG, shrunk toward 1.0 by sample size. Above 1 = places shots better than the chance deserved", "derived"),
    ("Placement sample xG", "hist_xG", "Total xG behind the placement ratio, across up to 4 seasons. Low numbers mean the ratio is mostly shrunk back to 1", "derived"),
    ("Adjusted xGOT", "adjusted_xGOT", "xG re-scaled by the placement ratio: the xGOT this player would post for that xG", "derived"),
    ("Adjusted xGOT/90", "adjusted_xGOT_p90", "Adjusted xGOT per 90 - the xG input used by the xPts model", "derived"),
    ("Goals - xG", "goals_minus_xG", "Over/under-performance of expected goals", "derived"),
    ("xGOT - xG", "xGOT_minus_xG", "Shot placement skill: how much better than average the shots were aimed", "derived"),
    ("Goals - xGOT", "goals_minus_xGOT", "Pure finishing residual after placement is accounted for", "derived"),
    ("xG model gap", "xG_model_gap", "Opta xG minus Understat xG - big gaps flag disputed shot quality", "derived"),
    ("Shots", "pl_total_scoring_att", "Total shots", "PL"),
    ("Shots on target", "pl_ontarget_scoring_att", "Shots on target", "PL"),
    ("Big chances missed", "pl_big_chance_missed", "Clear chances not converted", "PL"),
    ("Touches in box", "pl_touches_in_opp_box", "Touches in the opposition penalty area", "PL"),
    ("Touches in box/90", "pl_touches_in_opp_box_p90", "Touches in the box per 90", "derived"),
    ("Pen goals", "pl_att_pen_goal", "Goals from penalties", "PL"),
    ("Headed goals", "pl_att_hd_goal", "Headed goals", "PL"),
    # -- expected assists -------------------------------------------------
    ("xA (Opta)", "xA_opta", "Opta expected assists", "FPL"),
    ("xA (Understat)", "xA_understat", "Understat expected assists", "Understat"),
    ("xA (blend)", "xA_blend", "Mean of the two xA models - best assist predictor tested", "derived"),
    ("xA/90 (blend)", "xA_blend_p90", "Blended xA per 90", "derived"),
    ("xGI (blend)", "xGI_blend", "Blended xG + xA", "derived"),
    ("xGI/90 (blend)", "xGI_blend_p90", "Blended xG + xA per 90", "derived"),
    ("Key passes", "us_key_passes", "Passes leading to a shot", "Understat"),
    ("Big chances created", "pl_big_chance_created", "Clear chances created", "PL"),
    ("Chances created", "pl_total_att_assist", "All chances created", "PL"),
    ("Crosses (acc)", "pl_accurate_cross", "Accurate crosses", "PL"),
    ("Through balls (acc)", "pl_accurate_through_ball", "Accurate through balls", "PL"),
    ("Corners taken", "pl_corner_taken", "Corners taken", "PL"),
    ("xGChain", "us_xGChain", "Total xG of every possession the player was involved in", "Understat"),
    ("xGBuildup", "us_xGBuildup", "xGChain excluding the player's own shots and key passes", "Understat"),
    # -- defending --------------------------------------------------------
    ("CBI", "fpl_clearances_blocks_interceptions", "Clearances + blocks + interceptions (the DefCon input)", "FPL"),
    ("Recoveries", "fpl_recoveries", "Ball recoveries (counts toward DefCon for MID/FWD)", "FPL"),
    ("Tackles", "fpl_tackles", "Tackles (counts toward DefCon)", "FPL"),
    ("Tackles won", "pl_won_tackle", "Tackles won", "PL"),
    ("Interceptions", "pl_interception", "Interceptions", "PL"),
    ("Clearances", "pl_effective_clearance", "Effective clearances", "PL"),
    ("Blocks", "pl_outfielder_block", "Outfield blocks", "PL"),
    ("Duels won", "pl_duel_won", "Duels won", "PL"),
    ("Aerials won", "pl_aerial_won", "Aerial duels won", "PL"),
    ("Poss won att 3rd/90", "fm_poss_won_att3rd_p90", "Possession won in the final third per 90", "FotMob"),
    ("Errors -> goal", "pl_error_lead_to_goal", "Errors leading directly to a goal", "PL"),
    ("Errors -> shot", "pl_error_lead_to_shot", "Errors leading directly to a shot", "PL"),
    ("xGC (Opta)", "fpl_expected_goals_conceded", "Expected goals conceded while on the pitch", "FPL"),
    ("xGC/90", "xGC_p90", "Expected goals conceded per 90 - the clean-sheet driver", "derived"),
    # -- goalkeeping ------------------------------------------------------
    ("Saves", "fpl_saves", "Saves made", "FPL"),
    ("Save %", "fm_save_pct", "Save percentage", "FotMob"),
    ("Goals prevented", "fm_goals_prevented", "xGOT faced minus goals conceded: shot-stopping value", "FotMob"),
    ("Pens saved", "fpl_penalties_saved", "Penalties saved", "FPL"),
    ("High claims", "pl_total_high_claim", "High claims", "PL"),
    ("Punches", "pl_punches", "Punched clearances", "PL"),
    # -- discipline / misc ------------------------------------------------
    ("FotMob rating", "fotmob_rating", "FotMob average match rating", "FotMob"),
    ("Yellows", "fpl_yellow_cards", "Yellow cards", "FPL"),
    ("Reds", "fpl_red_cards", "Red cards", "FPL"),
    ("Fouls", "pl_fouls", "Fouls committed", "PL"),
    ("Fouled", "pl_was_fouled", "Times fouled", "PL"),
    ("Pens won", "pl_penalty_won", "Penalties won", "PL"),
    ("Pens conceded", "pl_penalty_conceded", "Penalties conceded", "PL"),
    ("Dribbles won", "pl_won_contest", "Take-ons completed", "PL"),
    ("Dispossessed", "pl_dispossessed", "Times dispossessed", "PL"),
    ("Offsides", "pl_total_offside", "Times caught offside", "PL"),
    ("Selected by %", "selected_by_percent", "FPL ownership at time of pull", "FPL"),
    # -- other people's projections and the draft market ------------------
    ("Solio season pts", "solio_season_pts", "Solio's 19-GW projection extended to a full season, with the second half rebuilt at settled minutes", "Solio"),
    ("Solio H1 pts (GW1-19)", "solio_H1_pts", "Solio's projection as published, first half only", "Solio"),
    ("Solio naive x2", "solio_naive_double", "What simply doubling GW1-19 would have given", "Solio"),
    ("Solio correction", "solio_correction", "Season points minus naive doubling. Positive = injury/World Cup return the doubling would have double-counted", "derived"),
    ("Solio xMins (season)", "solio_season_xmins", "Settled minutes per match x 38 - a ready-made xMins forecast", "Solio"),
    ("Solio xMins (settled)", "solio_settled_xmins", "Solio's projected minutes per match by GW17-19, once the season has settled", "Solio"),
    ("Solio xMins pattern", "xmins_pattern", "flat, ramp-up (injury or late World Cup return), or fade (projected to lose his place)", "derived"),
    ("ADP", "adp", "Average draft position in real FPL Draft leagues, human picks only, normalised to an 8-team board", "FPL Draft"),
    ("ADP (8-team only)", "adp_8team_only", "Same, using only 8-team leagues", "FPL Draft"),
    ("ADP spread", "adp_sd", "Standard deviation of draft position - high means the market disagrees about him", "FPL Draft"),
    ("ADP earliest", "earliest", "Earliest he was taken in any league", "FPL Draft"),
    ("ADP latest", "latest", "Latest he was taken in any league", "FPL Draft"),
    ("Times drafted", "times_drafted", "Leagues in which a human drafted him. Low numbers make the ADP unreliable", "FPL Draft"),
    ("Drafted %", "drafted_pct", "Share of leagues in which he was drafted at all", "FPL Draft"),
    ("Auto-pick %", "auto_pick_pct", "Share of his picks made by the autodraft algorithm rather than a human", "FPL Draft"),
    ("FPL draft rank", "fpl_draft_rank", "The default ordering FPL's own autodraft uses", "FPL Draft"),
]

# Columns the old workbook expressed as "minutes per X", kept so the existing
# sheet logic still works without rewriting it.
MINS_PER = {
    "Mins per shot": "pl_total_scoring_att",
    "Mins per touch in box": "pl_touches_in_opp_box",
    "Mins per chance created": "pl_total_att_assist",
    "Mins per xG (blend)": "xG_blend",
    "Mins per xA (blend)": "xA_blend",
    "Mins per xGI (blend)": "xGI_blend",
    "Mins per point": "fpl_total_points",
}


def derive(m: pd.DataFrame) -> pd.DataFrame:
    m = m.copy()
    gw1 = pd.Timestamp("2026-08-21")
    m["age"] = ((gw1 - pd.to_datetime(m["birth_date"], errors="coerce")).dt.days / 365.25).round(1)
    m["mins_per_app"] = (m["minutes"] / m["pl_appearances"]).round(1)
    m["fpl_points_p90"] = (m["fpl_total_points"] / m["nineties"]).where(m["nineties"] > 0)
    m["bonus_per_point"] = (m["fpl_bonus"] / m["fpl_total_points"]).where(m["fpl_total_points"] > 0)
    base = m["fpl_total_points"] - m["fpl_bonus"]
    m["bonus_per_base_point"] = (m["fpl_bonus"] / base).where(base > 0)
    m["xGC_p90"] = (m["fpl_expected_goals_conceded"] / m["nineties"]).where(m["nineties"] > 0)
    meta = pd.read_csv(PROC / "fpl_meta.csv")[["code", "selected_by_percent"]]
    m = m.merge(meta, on="code", how="left")
    return m


def main() -> None:
    m = derive(pd.read_csv(PROC / "master_2025_26.csv"))

    players = pd.DataFrame()
    for out_col, src, _, _ in SPEC:
        players[out_col] = m[src] if src in m.columns else pd.NA
    for out_col, src in MINS_PER.items():
        players[out_col] = (m["minutes"] / m[src]).round(1).replace(
            [float("inf"), float("-inf")], pd.NA)

    players = players.sort_values(["FPL points"], ascending=False, na_position="last")
    for c in players.select_dtypes("float").columns:
        players[c] = players[c].round(3)

    dictionary = pd.DataFrame(
        [{"Column": c, "Meaning": d, "Source": s} for c, _, d, s in SPEC]
        + [{"Column": c, "Meaning": f"Minutes divided by {src}; matches the "
            f"'mins per' convention used in last year's sheet", "Source": "derived"}
           for c, src in MINS_PER.items()])

    teams = pd.read_csv(PROC / "understat_teams.csv")
    teams = teams[teams.season == "2025/26"].drop(columns=["season", "understat_team_id"])
    fpl_teams = pd.read_csv(PROC / "fpl_teams.csv")
    teams = teams.merge(fpl_teams, left_on="team_short", right_on="short_name",
                        how="outer", suffixes=("", "_fpl"))

    fixtures = pd.read_csv(PROC / "fpl_fixtures_2627.csv")
    tid = dict(zip(fpl_teams["id"], fpl_teams["short_name"]))
    fixtures = fixtures[["event", "kickoff_time", "team_h", "team_a",
                         "team_h_difficulty", "team_a_difficulty"]].copy()
    fixtures["home"] = fixtures["team_h"].map(tid)
    fixtures["away"] = fixtures["team_a"].map(tid)

    review = pd.read_csv(PROC / "match_review.csv")

    bakeoff = OUT / "xg_model_bakeoff.xlsx"
    cal = pd.read_excel(bakeoff, "calibration") if bakeoff.exists() else pd.DataFrame()
    fc = pd.read_excel(bakeoff, "forecast") if bakeoff.exists() else pd.DataFrame()
    h2h = pd.read_excel(bakeoff, "head_to_head") if bakeoff.exists() else pd.DataFrame()

    path = OUT / "FPL_2026_27_draft_data.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        players.to_excel(xl, sheet_name="Players", index=False)
        dictionary.to_excel(xl, sheet_name="Data dictionary", index=False)
        teams.to_excel(xl, sheet_name="Teams 25-26", index=False)
        fixtures.to_excel(xl, sheet_name="Fixtures 26-27", index=False)
        cal.to_excel(xl, sheet_name="Model - calibration", index=False)
        fc.to_excel(xl, sheet_name="Model - forecast", index=False)
        h2h.to_excel(xl, sheet_name="Model - head to head", index=False)
        review.to_excel(xl, sheet_name="Match review", index=False)

        ws = xl.book["Players"]
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions

    print(f"written: {path}")
    print(f"  Players sheet: {len(players)} rows x {players.shape[1]} columns")
    print(f"  built {dt.date.today()}")


if __name__ == "__main__":
    main()
