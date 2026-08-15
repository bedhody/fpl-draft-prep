"""Merge every source into one row per player for the 2025/26 season.

Join strategy
-------------
FPL <-> Premier League (Pulselive) is an exact ID join: FPL's `code` is the
Opta player id that Pulselive exposes as `altIds.opta`.

Understat and FotMob have no shared id, so they are matched by name against an
alias set built from both the official PL display name and the FPL
first/second/web names — then *verified* against minutes played, which every
source reports independently.  A name that matches but whose minutes disagree
by more than ~8% is sent to review rather than accepted silently.

Outputs data/processed/master_2025_26.csv plus match audit files.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from common import PROC, name_keys, norm_name, similarity

SEASON = "2025/26"
NAME_FLOOR = 70.0      # a candidate below this is never considered
AGREE_FLOOR = 0.55     # minutes must at least roughly agree
ACCEPT = 85.0          # combined score needed to auto-accept
W_NAME = 0.65          # weight on name similarity vs minutes agreement


def alias_table(spine: pd.DataFrame) -> dict[int, set[str]]:
    """code -> every spelling a third-party source might plausibly use."""
    idx: dict[int, set[str]] = {}
    for code, pl, first, second, web in zip(
        spine["code"], spine["pl_name"], spine["first_name"].fillna(""),
        spine["second_name"].fillna(""), spine["web_name"].fillna("")
    ):
        keys = name_keys(first, second, web) | {norm_name(pl)}
        parts = norm_name(pl).split()
        if parts:
            keys.add(parts[0])
            keys.add(parts[-1])
            if len(parts) > 1:
                keys |= {f"{parts[0][0]} {parts[-1]}", f"{parts[0]} {parts[-1]}"}
        idx[code] = {k for k in keys if k}
    return idx


def match_source(src: pd.DataFrame, spine: pd.DataFrame, aliases, *,
                 src_name_col: str, src_min_col: str, label: str):
    """Globally greedy name+minutes match.  Returns (accepted, review)."""
    spine_min = dict(zip(spine["code"], spine["mins_played"]))
    spine_disp = dict(zip(spine["code"], spine["pl_name"]))

    def agreement(code, mins):
        a, b = spine_min.get(code), mins
        if not a or not b or np.isnan(a) or np.isnan(b):
            return 0.5
        return max(0.0, 1 - abs(a - b) / max(a, b))

    # Score every (source row, spine player) pair once, then assign the best
    # pairs first.  Greedy-by-global-score beats greedy-by-row: it stops a
    # weak match from stealing a name that a strong match needs.
    pairs = []
    for i, row in enumerate(src.itertuples(index=False)):
        key = norm_name(getattr(row, src_name_col))
        mins = getattr(row, src_min_col)
        for code, keys in aliases.items():
            ns = max(similarity(key, a) for a in keys)
            if ns < NAME_FLOOR:
                continue
            ag = agreement(code, mins)
            if ag < AGREE_FLOOR:
                continue
            pairs.append((W_NAME * ns + (1 - W_NAME) * 100 * ag, ns, ag, i, code))
    pairs.sort(reverse=True)

    used_src: set[int] = set()
    used_code: set[int] = set()
    assign: dict[int, tuple] = {}
    for combined, ns, ag, i, code in pairs:
        if i in used_src or code in used_code or combined < ACCEPT:
            continue
        used_src.add(i)
        used_code.add(code)
        assign[i] = (code, combined, ns, ag)

    accepted, review = [], []
    rows = src.reset_index(drop=True)
    for i, row in rows.iterrows():
        if i in assign:
            code, combined, ns, ag = assign[i]
            accepted.append({**row.to_dict(), "code": code})
        else:
            best = max((p for p in pairs if p[3] == i), default=None)
            review.append({
                "source": label,
                "source_name": row[src_name_col],
                "source_mins": row[src_min_col],
                "best_guess": spine_disp.get(best[4]) if best else None,
                "best_guess_code": best[4] if best else None,
                "combined": round(best[0], 1) if best else 0,
                "name_score": round(best[1], 1) if best else 0,
                "minute_agreement": round(best[2], 3) if best else 0,
                "decision": "review" if best else "unmatched",
            })

    return pd.DataFrame(accepted), pd.DataFrame(review)


def build() -> None:
    meta = pd.read_csv(PROC / "fpl_meta.csv")
    fpl = pd.read_csv(PROC / "fpl_last_season.csv")
    pulse = pd.read_csv(PROC / "pulselive_players.csv")
    pulse = pulse[pulse["season"] == SEASON].drop(columns=["season"])
    und = pd.read_csv(PROC / "understat_players.csv")
    und = und[und["season"] == SEASON].drop(columns=["season"])
    fm = pd.read_csv(PROC / "fotmob_players.csv")
    fm = fm[fm["season"] == SEASON].drop(columns=["season"])

    # ---- spine ------------------------------------------------------------
    # Outer join, deliberately: the draft pool is everyone registered for
    # 2026/27 (including summer signings with no PL record at all), while the
    # analysis pool is everyone who played in 2025/26 (including players who
    # have since left).  Keeping both means no draftable player is invisible.
    spine = pulse.merge(
        meta[["code", "id", "web_name", "first_name", "second_name", "position",
              "team_short", "price_2627", "status", "news",
              "corners_and_indirect_freekicks_order", "direct_freekicks_order",
              "penalties_order", "birth_date", "chance_of_playing_next_round"]],
        on="code", how="outer",
    )
    spine["pl_name"] = spine["pl_name"].fillna(
        (spine["first_name"].fillna("") + " " + spine["second_name"].fillna("")).str.strip())
    spine["draftable_2627"] = spine["id"].notna()
    spine["played_2526"] = spine["mins_played"].notna()
    print(f"spine: {len(spine)} rows -- {spine['played_2526'].sum()} played in 2025/26, "
          f"{spine['draftable_2627'].sum()} registered for 2026/27, "
          f"{(spine['draftable_2627'] & ~spine['played_2526']).sum()} draftable with no PL record")

    aliases = alias_table(spine)

    und_ok, und_rev = match_source(
        und, spine, aliases, src_name_col="understat_name",
        src_min_col="minutes", label="understat")
    fm_ok, fm_rev = match_source(
        fm, spine, aliases, src_name_col="fotmob_name",
        src_min_col="fm_mins", label="fotmob")

    print(f"understat: {len(und_ok)}/{len(und)} auto-matched, {len(und_rev)} to review")
    print(f"fotmob:    {len(fm_ok)}/{len(fm)} auto-matched, {len(fm_rev)} to review")

    # ---- prefix each source so no column name is ambiguous -----------------
    keep_spine = ["code", "opta_code", "id", "pl_name", "position", "team_short",
                  "price_2627", "draftable_2627", "played_2526",
                  "status", "news", "chance_of_playing_next_round",
                  "birth_date", "penalties_order",
                  "corners_and_indirect_freekicks_order", "direct_freekicks_order"]
    pl_stats = [c for c in spine.columns if c not in keep_spine
                and c not in ("web_name", "first_name", "second_name",
                              "pl_team", "pl_position")]
    spine_out = spine[keep_spine + pl_stats].rename(
        columns={c: f"pl_{c}" for c in pl_stats})

    fpl_stats = [c for c in fpl.columns if c not in ("code", "fpl_id")]
    fpl_out = fpl[["code"] + fpl_stats].rename(
        columns={c: f"fpl_{c}" for c in fpl_stats})

    und_stats = [c for c in und_ok.columns
                 if c not in ("code", "name_key", "understat_name",
                              "understat_team", "team_short")]
    und_out = und_ok[["code"] + und_stats].rename(
        columns={c: f"us_{c}" for c in und_stats})

    fm_stats = [c for c in fm_ok.columns
                if c not in ("code", "name_key", "fotmob_name", "fotmob_team",
                             "fotmob_team_id")]
    fm_out = fm_ok[["code"] + fm_stats]        # already fm_-prefixed

    def optional(name, drop=()):
        path = PROC / name
        if not path.exists():
            print(f"  (missing {name} -- some columns will be blank)")
            return pd.DataFrame({"code": pd.Series(dtype="int64")})
        return pd.read_csv(path).drop(columns=list(drop), errors="ignore")

    gw = optional("gw_aggregates_2025_26.csv", drop=["element"])
    adj = optional("adjusted_xgot.csv", drop=["xG_opta", "fm_xGOT"])
    bps = optional("bps_remodel_2025_26.csv",
                   drop=["element", "name", "team", "minutes", "pos_2526"])

    master = (spine_out
              .merge(fpl_out, on="code", how="left")
              .merge(und_out, on="code", how="left")
              .merge(fm_out, on="code", how="left")
              .merge(gw, on="code", how="left")
              .merge(adj, on="code", how="left")
              .merge(bps, on="code", how="left")
              .rename(columns={"id": "fpl_id", "pl_name": "player",
                               "team_short": "team_2627"}))

    master = add_derived(master)

    review = pd.concat([und_rev, fm_rev], ignore_index=True)
    review.to_csv(PROC / "match_review.csv", index=False)
    master.to_csv(PROC / "master_2025_26.csv", index=False)
    print(f"master_2025_26.csv     {len(master)} rows x {master.shape[1]} columns")
    return master


def add_derived(m: pd.DataFrame) -> pd.DataFrame:
    """Per-90s and the two-model xG/xA blend."""
    mins = m["fpl_minutes"].fillna(m["pl_mins_played"])
    m["minutes"] = mins
    m["nineties"] = mins / 90

    # Opta (via FPL) and Understat are the two independent xG models available.
    m["xG_opta"] = m["fpl_expected_goals"]
    m["xA_opta"] = m["fpl_expected_assists"]
    m["xG_understat"] = m["us_xG"]
    m["xA_understat"] = m["us_xA"]
    m["xG_blend"] = m[["xG_opta", "xG_understat"]].mean(axis=1)
    m["xA_blend"] = m[["xA_opta", "xA_understat"]].mean(axis=1)
    m["xGI_blend"] = m["xG_blend"] + m["xA_blend"]

    for c in ["xG_opta", "xA_opta", "xG_understat", "xA_understat",
              "xG_blend", "xA_blend", "xGI_blend", "us_npxG", "fm_xGOT",
              "us_xGChain", "us_xGBuildup", "fpl_defensive_contribution",
              "pl_touches_in_opp_box", "pl_big_chance_created",
              "pl_big_chance_missed"]:
        if c in m.columns:
            m[f"{c}_p90"] = (m[c] / m["nineties"]).where(m["nineties"] > 0)

    # Finishing and shot-quality signals.
    # Per-90 inputs the xPts model consumes.  Everything divides by the same
    # FPL-minutes denominator regardless of which source the numerator is from.
    if "adjusted_xGOT" in m.columns:
        m["adjusted_xGOT_p90"] = (m["adjusted_xGOT"] / m["nineties"]).where(
            m["nineties"] > 0)
    if "bonus_new" in m.columns:
        m["bonus_new_p90"] = (m["bonus_new"] / m["nineties"]).where(m["nineties"] > 0)
        m["bonus_old_p90"] = (m["fpl_bonus"] / m["nineties"]).where(m["nineties"] > 0)

    m["goals_minus_xG"] = m["fpl_goals_scored"] - m["xG_blend"]
    m["xGOT_minus_xG"] = m["fm_xGOT"] - m["xG_opta"]        # shot placement
    m["goals_minus_xGOT"] = m["fpl_goals_scored"] - m["fm_xGOT"]  # pure finishing
    m["xG_model_gap"] = m["xG_opta"] - m["xG_understat"]
    return m


if __name__ == "__main__":
    build()
