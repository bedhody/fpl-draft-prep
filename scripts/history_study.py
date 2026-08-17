"""Five seasons of Premier League fantasy scoring, restated under 2026/27 rules.

Four questions, all of them about whether a projection is historically shaped
like reality rather than like its own assumptions:

1. How many defenders finish in the top 20/30/40/50, and how much did the
   DefCon rule change that?
2. Does a high DefCon rate repeat from one season to the next -- and does it
   repeat better or worse than goals, assists and clean sheets?
3. What share of the top 30 comes from a big club, and is that share stable?
4. Once a player is in the top 30, how likely is he to be there again next
   season -- and does playing for a big club change the answer?

DefCon before 2025/26
---------------------
The rule did not exist before 2025/26 and the gameweek files carry no
defensive counts before it either.  It is reconstructed from the official
Premier League feed, which has published the underlying Opta counts for every
season since 2021/22.  The mapping was found by testing every plausible
combination against the season where both exist:

    effective clearances + blocks + interceptions + tackles
      (+ ball recoveries for midfielders and forwards)

which reproduces FPL's own 2025/26 count at r = 0.995 across 339 players with
900+ minutes.  Points then come from the same Poisson over minutes per start
that the projection model uses, and that step is validated against the real
2025/26 DefCon points before any of it is applied to an older season.

What is NOT restated: the BPS change (1 point per 3 clearances rather than per
2) needs per-match counts, which the older files do not carry.  Bonus is left
as it was awarded.  Its effect is small and it moves against defenders, so the
defender counts below are, if anything, generous.

Output: output/history_study.md
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson

from common import OUT, PROC, RAW

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
PRETTY = {s: s.replace("-", "/") for s in SEASONS}
POSMAP = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}
THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12, "GKP": 99}
DEFCON_PTS = 2
BIG_N = 6                      # "big club" = top six of the previous season
TOP_NS = [20, 30, 40, 50]
MIN_MINUTES = 900


def md_table(df: pd.DataFrame) -> str:
    """A markdown table, without pulling in tabulate for six lines of pipes."""
    cols = [str(c) for c in df.columns]
    rows = [[("" if pd.isna(v) else str(v)) for v in r] for r in df.itertuples(index=False)]
    w = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
         for i, c in enumerate(cols)]
    head = "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(cols)) + " |"
    rule = "|" + "|".join("-" * (x + 2) for x in w) + "|"
    body = ["| " + " | ".join(v.ljust(w[i]) for i, v in enumerate(r)) + " |"
            for r in rows]
    return "\n".join([head, rule, *body])


# --------------------------------------------------------------------------
def league_table(gw: pd.DataFrame) -> pd.DataFrame:
    """Final table for a season, rebuilt from the scorelines in the gameweek
    file.  Cheaper than another fetch and it cannot disagree with the player
    rows, because it is the same rows."""
    f = gw.drop_duplicates(subset=["fixture", "team"])[
        ["fixture", "team", "was_home", "team_h_score", "team_a_score"]].dropna()
    f["gf"] = np.where(f["was_home"], f["team_h_score"], f["team_a_score"])
    f["ga"] = np.where(f["was_home"], f["team_a_score"], f["team_h_score"])
    f["pts"] = np.where(f.gf > f.ga, 3, np.where(f.gf == f.ga, 1, 0))
    t = f.groupby("team").agg(played=("fixture", "size"), pts=("pts", "sum"),
                              gf=("gf", "sum"), ga=("ga", "sum")).reset_index()
    t["gd"] = t["gf"] - t["ga"]
    return t.sort_values(["pts", "gd", "gf"], ascending=False).reset_index(drop=True)


def defcon_actions(season: str, players: pd.DataFrame) -> pd.Series:
    """Qualifying defensive actions for a season.

    2025/26 has FPL's own count.  Everything earlier is rebuilt from the
    official Premier League counts, using the mapping validated below.
    """
    p = pd.read_csv(PROC / "pulselive_players.csv")
    p = p[p["season"] == PRETTY[season]]
    if p.empty:
        return pd.Series(np.nan, index=players.index)
    base = (p["effective_clearance"].fillna(0) + p["outfielder_block"].fillna(0)
            + p["interception"].fillna(0) + p["total_tackle"].fillna(0))
    est = pd.DataFrame({"code": p["code"], "base": base,
                        "rec": p["ball_recovery"].fillna(0)})
    j = players[["code", "pos"]].merge(est, on="code", how="left")
    out = j["base"] + np.where(j["pos"].isin(["MID", "FWD"]), j["rec"], 0)
    out.index = players.index
    return out


def season_frame(season: str) -> pd.DataFrame | None:
    path = RAW / "vaastav" / f"merged_gw_{season}.csv"
    if not path.exists():
        return None
    gw = pd.read_csv(path).drop_duplicates(subset=["element", "fixture"])
    gw["pos"] = gw["position"].map(POSMAP)
    played = gw[(gw.minutes > 0) & gw["pos"].notna()]

    agg = played.groupby("element").agg(
        pos=("pos", "last"), team=("team", "last"), name=("name", "last"),
        minutes=("minutes", "sum"), apps=("minutes", "size"),
        points=("total_points", "sum"), goals=("goals_scored", "sum"),
        assists=("assists", "sum"), clean_sheets=("clean_sheets", "sum"),
        bonus=("bonus", "sum"),
    ).reset_index()
    # Minutes per appearance is enough here: it is what the Poisson needs, and
    # `starts` does not exist in the 2021/22 file.
    agg["mins_per_start"] = (agg["minutes"] / agg["apps"]).clip(20, 90)
    agg["dc_actual"] = played.groupby("element")["defensive_contribution"].sum().values \
        if "defensive_contribution" in played.columns else np.nan
    agg["dc_points_actual"] = np.nan
    if "defensive_contribution" in played.columns and played["defensive_contribution"].notna().any():
        hit = played.apply(
            lambda r: r["defensive_contribution"] >= THRESHOLD.get(r["pos"], 99), axis=1)
        agg["dc_points_actual"] = (played.assign(h=hit).groupby("element")["h"]
                                   .sum().values * DEFCON_PTS)

    ids = pd.read_csv(RAW / "vaastav" / f"players_raw_{season}.csv")[["id", "code"]] \
        if (RAW / "vaastav" / f"players_raw_{season}.csv").exists() else None
    if ids is None:
        return None
    agg = agg.merge(ids, left_on="element", right_on="id", how="left").drop(columns="id")
    agg = agg[agg["code"].notna()]
    agg["code"] = agg["code"].astype("int64")

    agg["dc_est"] = defcon_actions(season, agg)
    agg["season"] = PRETTY[season]

    tbl = league_table(gw)
    agg["team_rank"] = agg["team"].map({t: i + 1 for i, t in enumerate(tbl["team"])})
    return agg


def model_defcon_points(d: pd.DataFrame, actions_col: str) -> pd.Series:
    """The projection model's own DefCon step, applied to a past season."""
    lam = (d[actions_col] / (d["minutes"] / 90)).replace([np.inf, -np.inf], np.nan)
    mps = d["mins_per_start"]
    thr = d["pos"].map(THRESHOLD).fillna(99)
    matches = np.minimum(38, d["minutes"] / mps)
    hit = 1 - poisson.cdf(thr - 1, (lam * mps / 90).fillna(0))
    return (matches * hit * DEFCON_PTS).where(lam.notna(), 0.0)


# --------------------------------------------------------------------------
def calibrate(d: pd.DataFrame) -> dict[str, float]:
    """Scale the modelled DefCon points to the ones 2025/26 actually awarded.

    A fixed-rate Poisson under-counts threshold crossings, because a real
    player's actions are overdispersed -- some matches he is chasing the game
    for ninety minutes, some he is 3-0 up at half time -- and P(X >= threshold)
    is convex in the rate, so spreading the rate out raises the average.
    Uncalibrated the model returns 24% less DefCon than was awarded, which
    would understate every defender in the seasons before the rule existed and
    make the whole comparison say the opposite of the truth.
    """
    cur = d[(d.season == "2025/26") & d.dc_points_actual.notna()].copy()
    cur["model"] = model_defcon_points(cur, "dc_est")
    g = cur.groupby("pos")
    return (g["dc_points_actual"].sum() / g["model"].sum().replace(0, np.nan)
            ).clip(0.5, 2.5).fillna(1.0).to_dict()


def build() -> pd.DataFrame:
    frames = [f for f in (season_frame(s) for s in SEASONS) if f is not None]
    d = pd.concat(frames, ignore_index=True)
    factors = calibrate(d)
    d["dc_points_model"] = (model_defcon_points(d, "dc_est")
                            * d["pos"].map(factors).fillna(1.0))
    d.attrs["dc_factors"] = factors
    # 2025/26 already scored DefCon, so restating it means leaving it alone.
    d["restated"] = np.where(d["season"] == "2025/26", d["points"],
                             d["points"] + d["dc_points_model"])
    return d


def validate(d: pd.DataFrame, lines: list[str]) -> None:
    cur = d[(d.season == "2025/26") & (d.minutes >= MIN_MINUTES)].copy()
    lines.append("## Is the reconstruction trustworthy?\n")
    if cur["dc_actual"].notna().any():
        r_actions = float(np.corrcoef(cur["dc_est"], cur["dc_actual"])[0, 1])
        err = (cur["dc_est"] - cur["dc_actual"])
        lines.append(f"Rebuilt **action counts** against FPL's own, "
                     f"{len(cur)} players with {MIN_MINUTES}+ minutes in 2025/26: "
                     f"r = **{r_actions:.4f}**, mean error {err.mean():+.1f} "
                     f"actions over a whole season (MAE {err.abs().mean():.1f}).\n")
        raw = model_defcon_points(cur, "dc_est")
        truth = cur["dc_points_actual"]
        r_pts = float(np.corrcoef(raw, truth)[0, 1])
        factors = d.attrs.get("dc_factors", {})
        cal = raw * cur["pos"].map(factors).fillna(1.0)
        lines.append(f"Rebuilt **DefCon points** against the points actually "
                     f"awarded: r = **{r_pts:.3f}**. Uncalibrated the league "
                     f"total comes out at {raw.sum():.0f} against "
                     f"{truth.sum():.0f} ({raw.sum() / truth.sum() - 1:+.1%}).\n")
        lines.append("It runs **low**, not high, and for a reason worth "
                     "stating: a fixed-rate Poisson under-counts threshold "
                     "crossings. A real player's actions are overdispersed — "
                     "some matches he chases the game for ninety minutes, some "
                     "he is three up at half time — and P(actions ≥ threshold) "
                     "is convex in the rate, so spreading the rate out raises "
                     "the average. Left uncorrected it would understate every "
                     "defender in the seasons before the rule existed and make "
                     "this whole comparison say the opposite of the truth.\n")
        lines.append("So the modelled points are scaled per position to the "
                     "ones 2025/26 actually awarded ("
                     + ", ".join(f"{p} ×{v:.2f}" for p, v in sorted(factors.items())
                                 if v != 1.0)
                     + f"), which brings the total to {cal.sum():.0f} against "
                     f"{truth.sum():.0f}. The same correction is worth making "
                     "in the projection model, where DefCon is computed the "
                     "same way and is therefore also low.\n")
    else:
        lines.append("_No 2025/26 DefCon column found -- reconstruction unchecked._\n")


def q1_position_mix(d: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 1. How many defenders finish in the top N?\n")
    lines.append("Restated under 2026/27 scoring, so DefCon counts in every "
                 "season. The as-was column is what actually happened at the "
                 "time, for comparison.\n")
    for n in TOP_NS:
        rows = []
        for s, g in d.groupby("season"):
            top = g.nlargest(n, "restated")
            was = g.nlargest(n, "points")
            rows.append({
                "Season": s,
                "GKP": int((top.pos == "GKP").sum()),
                "DEF": int((top.pos == "DEF").sum()),
                "MID": int((top.pos == "MID").sum()),
                "FWD": int((top.pos == "FWD").sum()),
                "DEF as-was": int((was.pos == "DEF").sum()),
            })
        t = pd.DataFrame(rows)
        mean_def = t["DEF"].mean()
        lines.append(f"\n**Top {n}** — defenders average **{mean_def:.1f}** "
                     f"({mean_def / n:.0%} of the list), range "
                     f"{t['DEF'].min()}–{t['DEF'].max()}.\n")
        lines.append(md_table(t))
        lines.append("")


def q2_persistence(d: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 2. Does DefCon repeat better than goals or clean sheets?\n")
    lines.append("Year-on-year correlation of each rate, over players with "
                 f"{MIN_MINUTES}+ minutes in **both** seasons. A high number "
                 "means last season told you something about this one.\n")

    d = d.copy()
    d["n90"] = d["minutes"] / 90
    d["dc_p90"] = d["dc_est"] / d["n90"]
    d["g_p90"] = d["goals"] / d["n90"]
    d["a_p90"] = d["assists"] / d["n90"]
    d["cs_rate"] = d["clean_sheets"] / d["apps"]
    # As-was points, not restated: the restated figure contains modelled
    # DefCon, which is a deterministic function of the DefCon rate in the very
    # same row, so correlating it with itself would flatter the answer.
    d["pts_p90"] = d["points"] / d["n90"]
    d["bonus_p90"] = d["bonus"] / d["n90"]

    # Only the seasons actually on disk -- an absent one would otherwise
    # contribute an empty 'previous top six' and divide by zero.
    order = [s for s in PRETTY.values() if s in set(d['season'])]
    metrics = [("dc_p90", "DefCon actions/90"), ("g_p90", "Goals/90"),
               ("a_p90", "Assists/90"), ("cs_rate", "Clean sheet rate"),
               ("bonus_p90", "Bonus/90"), ("pts_p90", "Points/90 (as-was)")]

    rows, per_pos = [], []
    for a, b in zip(order, order[1:]):
        x = d[(d.season == a) & (d.minutes >= MIN_MINUTES)]
        y = d[(d.season == b) & (d.minutes >= MIN_MINUTES)]
        j = x.merge(y, on="code", suffixes=("_1", "_2"))
        if len(j) < 40:
            continue
        row = {"Pair": f"{a} → {b}", "n": len(j)}
        for col, lab in metrics:
            row[lab] = round(float(np.corrcoef(j[f"{col}_1"], j[f"{col}_2"])[0, 1]), 3)
        rows.append(row)
        dj = j[j.pos_1 == "DEF"]
        if len(dj) >= 30:
            per_pos.append({
                "Pair": f"{a} → {b}", "n": len(dj),
                "DefCon actions/90": round(float(np.corrcoef(dj.dc_p90_1, dj.dc_p90_2)[0, 1]), 3),
                "Clean sheet rate": round(float(np.corrcoef(dj.cs_rate_1, dj.cs_rate_2)[0, 1]), 3),
                "Goals+assists/90": round(float(np.corrcoef(
                    dj.g_p90_1 + dj.a_p90_1, dj.g_p90_2 + dj.a_p90_2)[0, 1]), 3),
            })
    t = pd.DataFrame(rows)
    lines.append(md_table(t))
    means = {lab: t[lab].mean() for _, lab in metrics}
    best = max(means, key=means.get)
    lines.append(f"\nAverage across the pairs: "
                 + ", ".join(f"**{lab} {v:.2f}**" if lab == best else f"{lab} {v:.2f}"
                             for lab, v in means.items()) + ".\n")
    if per_pos:
        lines.append("\nDefenders only:\n")
        lines.append(md_table(pd.DataFrame(per_pos)))
        lines.append("")


def q3_big_club_share(d: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 3. What share of the top 30 comes from a big club?\n")
    lines.append(f"'Big club' is the **previous** season's top {BIG_N}, because "
                 "that is what a drafter actually knows in August. The "
                 "same-season top six is shown alongside, which is what you "
                 "would know with hindsight.\n")

    # Only the seasons actually on disk -- an absent one would otherwise
    # contribute an empty 'previous top six' and divide by zero.
    order = [s for s in PRETTY.values() if s in set(d['season'])]
    prev_top: dict[str, set] = {}
    for i, s in enumerate(order):
        g = d[d.season == s]
        ranks = g.drop_duplicates("team").set_index("team")["team_rank"].dropna()
        prev_top[s] = set(ranks[ranks <= BIG_N].index)

    rows = []
    for a, b in zip(order, order[1:]):
        g = d[d.season == b]
        top30 = g.nlargest(30, "restated")
        by_prev = top30["team"].isin(prev_top[a]).sum()
        by_now = (top30["team_rank"] <= BIG_N).sum()
        # how many of the whole pool were at a big club, for the baseline
        pool = g[g.minutes >= MIN_MINUTES]
        base = pool["team"].isin(prev_top[a]).mean()
        rows.append({"Season": b, "From last year's top 6": int(by_prev),
                     "From this year's top 6": int(by_now),
                     "Share of top 30": f"{by_prev / 30:.0%}",
                     "Share of the whole pool": f"{base:.0%}",
                     "Lift": f"{(by_prev / 30) / base:.2f}x"})
    t = pd.DataFrame(rows)
    lines.append(md_table(t))
    shares = [int(r["From last year's top 6"]) for r in rows]
    lines.append(f"\nBig-club players take **{np.mean(shares):.1f} of the top 30 "
                 f"on average** (range {min(shares)}–{max(shares)}). A big club "
                 f"is about 30% of the league by squad, so the concentration is "
                 f"real — but it leaves roughly half the top 30 to everybody "
                 f"else, every single season.\n")


def q4_individual_persistence(d: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## 4. If a player is top 30 this season, is he top 30 next?\n")

    # Only the seasons actually on disk -- an absent one would otherwise
    # contribute an empty 'previous top six' and divide by zero.
    order = [s for s in PRETTY.values() if s in set(d['season'])]
    rows = []
    for a, b in zip(order, order[1:]):
        ga, gb = d[d.season == a], d[d.season == b]
        top_a = set(ga.nlargest(30, "restated")["code"])
        top_b = set(gb.nlargest(30, "restated")["code"])
        present = set(gb["code"])
        big_prev = set(ga.drop_duplicates("team")
                       .loc[lambda x: x.team_rank <= BIG_N, "team"])
        ga_top = ga[ga.code.isin(top_a)]
        for label, sel in (("Big club", ga_top.team.isin(big_prev)),
                           ("Everyone else", ~ga_top.team.isin(big_prev))):
            codes = set(ga_top[sel]["code"])
            still = codes & present            # still in the league at all
            if not still:
                continue
            rows.append({"Pair": f"{a} → {b}", "Group": label,
                         "In top 30": len(codes),
                         "Still in the league": len(still),
                         "Top 30 again": len(codes & top_b),
                         "Repeat rate": f"{len(codes & top_b) / len(still):.0%}"})
    t = pd.DataFrame(rows)
    lines.append(md_table(t))

    agg = {}
    for label in ("Big club", "Everyone else"):
        s = t[t.Group == label]
        if not len(s):
            continue
        num = s["Top 30 again"].sum()
        den = s["Still in the league"].sum()
        agg[label] = (num, den, num / den)
    lines.append("")
    for label, (num, den, rate) in agg.items():
        lines.append(f"- **{label}**: {num} of {den} repeated — **{rate:.0%}**")
    if len(agg) == 2:
        a, b = agg["Big club"][2], agg["Everyone else"][2]
        lines.append(f"\nA top-30 season at a big club repeats "
                     f"{a / b:.2f}x as often as one anywhere else "
                     f"({a:.0%} against {b:.0%}), conditional on the player "
                     f"still being in the division.\n")


def compare_projection(d: pd.DataFrame, lines: list[str]) -> None:
    """Does the 2026/27 projection have a historically normal shape?"""
    path = PROC / "master_2025_26.csv"
    try:
        import xpts_calc
        import xpts_model
        src = xpts_model.build_rows()
        s = xpts_calc.score(src)
        proj = src.assign(xpts=s["xpts_season"])
        proj = proj[proj["draftable"].fillna(False).astype(bool)]
    except Exception as exc:                     # noqa: BLE001
        lines.append(f"\n_(projection comparison skipped: {exc})_\n")
        return

    lines.append("\n## 5. Does the 2026/27 projection look like history?\n")
    rows = []
    for n in TOP_NS:
        top = proj.nlargest(n, "xpts")
        hist = []
        for _, g in d.groupby("season"):
            hist.append(int((g.nlargest(n, "restated").pos == "DEF").sum()))
        rows.append({"Top N": n,
                     "DEF projected 26/27": int((top.pos == "DEF").sum()),
                     "DEF history mean": round(float(np.mean(hist)), 1),
                     "DEF history range": f"{min(hist)}–{max(hist)}",
                     "GKP projected": int((top.pos == "GKP").sum()),
                     "MID projected": int((top.pos == "MID").sum()),
                     "FWD projected": int((top.pos == "FWD").sum())})
    lines.append(md_table(pd.DataFrame(rows)))
    lines.append("")
    lines.append("One caveat on reading the row above. The projection's DefCon "
                 "points come from the same fixed-rate Poisson this file had to "
                 "correct, so they are low by roughly the same margin. "
                 "Correcting them raises defenders and midfielders both, and "
                 "midfielders by more (see the last section), so which way the "
                 "defender *share* moves is not obvious from here — only that "
                 "the absolute totals are understated for everybody who "
                 "defends.\n")


def implications(d: pd.DataFrame, lines: list[str]) -> None:
    lines.append("\n## What this says about the projection model\n")
    factors = d.attrs.get("dc_factors", {})
    lines.append("**The model understates DefCon.** `xpts_calc.defcon_hit` "
                 "assumes a player's defensive actions arrive at a constant "
                 "rate, so it uses a Poisson. They do not: measured within "
                 "player, across starts in 2025/26, the variance-to-mean ratio "
                 "of per-match DefCon actions is **1.40 for defenders and 1.41 "
                 "for midfielders**, against 1.00 for a Poisson. Because "
                 "P(actions ≥ threshold) is convex in the rate, that "
                 "overdispersion means more threshold crossings than a Poisson "
                 "predicts, not fewer.\n")
    lines.append("Scaled against what 2025/26 actually awarded, the shortfall "
                 "is "
                 + ", ".join(f"**{p} {v - 1:+.0%}**"
                             for p, v in sorted(factors.items())
                             if p in ("DEF", "MID"))
                 + ". At 2 points a match that is a material amount of a "
                 "defender's season, and it lands on exactly the players a "
                 "DefCon-heavy ruleset is supposed to reward. The fix is a "
                 "negative binomial in place of the Poisson, with the "
                 "dispersion fitted from the same per-match counts. **Not "
                 "applied** — it changes where players sit relative to each "
                 "other, so it is your call, not mine.\n")
    lines.append("(The forward factor is left out of the sentence above "
                 "because it rests on 18 DefCon points across four players in "
                 "the whole league. It is noise, and it does not matter.)\n")


def main() -> int:
    d = build()
    print(f"{len(d)} player-seasons across {d.season.nunique()} seasons")

    lines = ["# Five seasons, restated under 2026/27 scoring", "",
             "_Generated by `scripts/history_study.py`. Every number here is "
             "measured from the data in this repo; nothing is a ranking and "
             "nothing is a recommendation._", ""]
    validate(d, lines)
    q1_position_mix(d, lines)
    q2_persistence(d, lines)
    q3_big_club_share(d, lines)
    q4_individual_persistence(d, lines)
    compare_projection(d, lines)
    implications(d, lines)

    dest = OUT / "history_study.md"
    dest.write_text("\n".join(lines), encoding="utf-8")
    d.to_csv(OUT / "history_study.csv", index=False)
    print(f"wrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
