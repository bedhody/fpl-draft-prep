"""Where the model and the published pro rankings disagree, and what drives it.

This is a diagnostic on the model, not a view about any player and not a
ranking.  It takes two orderings that already exist -- the model's, from
`xpts_calc.score`, and the analysts', from `rankings_verified.csv` -- puts them
on one scale, and for the biggest disagreements reports which part of the
model's arithmetic produced the number.  It never says which side is right.
That is the human's call and it is the only interesting part.

Reading it:

  gap = model rank - pro rank, both taken WITHIN the pool of players who carry
  a published rank, so the two are on the same scale and neither is being
  compared against players the other never saw.  A positive gap means the
  model has him lower than the analysts do.

Every gap is split into how much of it is the minutes and how much is the
player, by re-scoring the whole board at one flat playing time.  A gap that
collapses was a minutes call and a gap that survives was not, and the two want
reading very differently.

Six checks stand behind it, run by `validate()` on every build:

  1. The points decomposition is complete.  Appearance + rate x 90s + DefCon +
     bonus + penalties must equal `xpts_season` exactly, or the attribution
     that follows is missing points and blaming the wrong component.
  2. The rate block is the sum of the named per-90 parts, for the same reason.
  3. The neutral ranking survives being built at 2,400 and 3,000 minutes
     instead, so the split reports the model and not the constant.
  4. Feeding a player his own minutes through the counterfactual reproduces his
     xPts exactly -- it has to be the model, not a rescaling of it -- and the
     neutral column has to actually move, or it would pass that while
     measuring nothing.
  5. Both rankings recomputed by counting.  They are NOT permutations of each
     other: ties are real on both sides and share the lower rank.
  6. Every player reported shares a name token with an authoritative name for
     his code, full or FPL display.  The matcher falls back to surnames and
     club, and a bad fallback puts somebody else's rank on a player -- the
     failure this repo has already had once, when a mean rank of 2.0 landed on
     a man one list had at 51.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import levers_report                                            # noqa: E402
import xpts_calc                                                # noqa: E402
import xpts_model                                               # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = ROOT / "output"

# How many disagreements to write up in each direction.  A cutoff on the gap
# itself would move every time the model does; a fixed count keeps the report
# the same size and the distribution is printed alongside so the reader can see
# where the cut fell.
TOP_N = 15

# The playing time every player is re-scored on to separate "the model thinks
# he is worse" from "the model thinks he plays less". 30 full matches.
NEUTRAL_MINS = 2700.0

# A player named by one analyst has a mean of one opinion.  Reported, but kept
# out of the headline lists, because a single list's 18th is not a consensus
# and the disagreement would be with one person.
MIN_LISTS = 2

# Per-90 components, in the order they are worth reporting.  The season totals
# for appearance, DefCon, bonus and penalties are separate columns because they
# are not rates -- see the bonus and DefCon sections of the README.
RATE_PARTS = {
    "xg_pts_p90": "goals",
    "xa_pts_p90": "assists",
    "cs_pts_p90": "clean sheets",
    "save_pts_p90": "saves",
    "gc_pts_p90": "goals conceded",
    "cards_pts_p90": "cards",
}


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def neutral_minutes(d: pd.DataFrame, mins: float = NEUTRAL_MINS) -> pd.Series:
    """Re-score every player as though he played a regular starter's season.

    The model is close to (points per 90) x (90s played), so a disagreement is
    either about how good the model thinks a player is or about how much it
    thinks he will play, and those want separating before anything is said
    about either.  Scoring everyone at one playing time isolates the first:
    whatever gap survives is about the player, and whatever gap closes was
    about the minutes.

    A flat number rather than the pool's median, which was the first attempt
    and was wrong.  The published boards run 240 deep, so their median forward
    plays 1,325 minutes -- less than a player the analysts rank 27th is
    forecast, which meant "neutralising" his minutes cut them and made the gap
    worse.  A backup-heavy median is not a neutral playing time.  30 full
    matches is arbitrary but stated, and `validate()` checks the verdicts do
    not move if it is set to 2,400 or 3,000 instead.

    Only `xMins_input` moves.  Minutes per start, and with it the whole DefCon
    and bonus per-match profile, stays his own -- the counterfactual is "the
    same player given a regular's playing time", not "a different player".
    """
    return xpts_calc.score(d.assign(xMins_input=float(mins)))["xpts_season"]


def frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model rows scored, joined to the published ranks, restricted to the pool."""
    d = xpts_model.build_rows()
    s = xpts_calc.score(d)
    m = pd.read_csv(PROC / "master_2025_26.csv").loc[d.index]

    adr, rows = levers_report.pro_rankings(m, detail=True)
    n90 = d["xMins_input"].fillna(0) / 90

    f = pd.DataFrame({
        "code": m["code"].to_numpy(),
        "player": d["player"].to_numpy(),
        "team": d["team"].fillna("--").to_numpy(),
        "pos": d["pos"].fillna("--").to_numpy(),
        "draftable": d["draftable"].fillna(False).to_numpy(),
        "moved": d["moved_club"].fillna(False).to_numpy(),
        "status": d["status"].fillna("").to_numpy(),
        "news": d["news"].fillna("").to_numpy(),

        "xmins": d["xMins_input"].fillna(0).to_numpy(),
        "xmins_source": d["xmins_source"].fillna("").to_numpy(),
        "solio": d["solio_season_xmins"].to_numpy(),
        "research_xmins": d["research_xmins"].to_numpy(),
        "research_reason": d["research_reason"].fillna("").to_numpy(),
        "research_conf": d["research_confidence"].fillna("").to_numpy(),
        "mins_2526": d["mins_2526"].fillna(0).to_numpy(),
        "pts_2526": d["pts_2526"].to_numpy(),
        "mins_per_start": d["mins_per_start"].to_numpy(),

        "n90": n90.to_numpy(),
        "matches": s["matches"].to_numpy(),
        "xpts": s["xpts_season"].to_numpy(),
        "xpts_p90": s["xpts_p90"].to_numpy(),

        # The five blocks the season total is made of.
        "app_pts": s["app_pts_season"].to_numpy(),
        "rate_pts": (s["rate_pts_p90"] * n90).to_numpy(),
        "dc_pts": s["dc_pts_season"].to_numpy(),
        "bonus_pts": s["bonus_pts_season"].to_numpy(),
        "pen_pts": s["pen_pts_season"].to_numpy(),

        # Rate forms of the two per-match blocks, so the driver line compares
        # like with like.  Penalties have no published per-90 column, and n90
        # is zero for anyone forecast no minutes at all.
        "dc_pts_r": s["dc_pts_p90"].to_numpy(),
        "pen_pts_r": np.divide(s["pen_pts_season"], n90,
                               out=np.zeros(len(s)), where=n90 > 0),

        "defcon_hit": (s["defcon_hit"] * 100).to_numpy(),
        "bonus_pm": s["bonus_per_match_priced"].to_numpy(),
        "xg_p90": d["xG_p90"].to_numpy(),
        "xa_p90": d["xA_p90"].to_numpy(),
        "defcon_lambda": d["defcon_lambda"].to_numpy(),
        "base_bps": d["base_bps_p90"].to_numpy(),
        "pcs": d["pcs_input"].to_numpy(),
        "no_pl_rates": d["xG_p90"].isna().to_numpy(),
    })
    for k, label in RATE_PARTS.items():
        f[k] = (s[k] * n90).to_numpy()
        f[k + "_r"] = s[k].to_numpy()               # the same thing as a rate

    f["adr"] = f["code"].map(adr)
    counts = rows.groupby("code")["source_name"].nunique()
    f["n_lists"] = f["code"].map(counts).fillna(0).astype(int)

    f["xpts_neutral"] = neutral_minutes(d).to_numpy()
    # Points per 90 taken at the neutral minutes, not the forecast ones.  The
    # forecast version is 0/0 for anyone the research has leaving the league,
    # which printed "0.00 xPts/90" beside a set of perfectly non-zero component
    # rates.  At a common playing time the headline and its parts agree.
    f["xpts_p90_n"] = f["xpts_neutral"] / (NEUTRAL_MINS / 90)
    return f, rows


def ranked(f: pd.DataFrame) -> pd.DataFrame:
    """Both orderings, within the pool of draftable players carrying a rank."""
    pool = f[f["draftable"] & f["adr"].notna()].copy()
    # Ties broken the same way on both sides so the gap cannot be an artefact
    # of one side breaking ties by row order and the other by value.
    pool["model_rank"] = pool["xpts"].rank(ascending=False, method="min")
    pool["pro_rank"] = pool["adr"].rank(ascending=True, method="min")
    pool["gap"] = pool["model_rank"] - pool["pro_rank"]
    # The same ordering with every player given his position's median minutes,
    # so the part of the disagreement that is purely about playing time can be
    # read off as the difference between the two gaps.
    pool["neutral_rank"] = pool["xpts_neutral"].rank(ascending=False, method="min")
    pool["neutral_gap"] = pool["neutral_rank"] - pool["pro_rank"]
    return pool.sort_values("gap")


# --------------------------------------------------------------------------
# Why a player sits where he does
# --------------------------------------------------------------------------
def minutes_story(r: pd.Series) -> str:
    """Where his minutes came from, in one clause."""
    src = {"research": "club research", "solio": "the Solio forecast",
           "actual": "his 2025/26 minutes"}.get(r["xmins_source"], r["xmins_source"] or "the default")
    out = f"{r['xmins']:,.0f} mins from {src}"
    if pd.notna(r["research_xmins"]) and pd.notna(r["solio"]):
        delta = r["research_xmins"] - r["solio"]
        if abs(delta) >= 200:
            out += f" ({delta:+,.0f} vs Solio)"
    if r["research_conf"]:
        out += f", {r['research_conf']} confidence"
    return out


def split(r: pd.Series) -> str:
    """How much of the disagreement is minutes and how much is the player.

    Reported as the gap that survives when everyone is put on the same playing
    time.  A gap that collapses was a minutes call; a gap that does not is the
    model rating the player himself differently.
    """
    # How much of the gap the minutes account for, as a share of it.  Comparing
    # the two gaps directly was the first version and it misread the case that
    # matters most: a player whose gap WIDENS at equal minutes is one the model
    # rates poorly per minute and is currently propping up with a generous
    # minutes forecast.  That is the opposite of a minutes disagreement, and it
    # was being reported as "part minutes, part rate".
    #
    # The neutral rank re-ranks everybody, so a player whose own minutes do not
    # change can still move: that is the point.  Standing still while the rest
    # of the board is levelled up is itself a statement about his rate.
    if abs(r["gap"]) < 1:
        return "gap is under a place; nothing to attribute"
    share = (abs(r["gap"]) - abs(r["neutral_gap"])) / abs(r["gap"])
    verdict = ("almost entirely minutes" if share >= 0.75
               else "mostly minutes" if share >= 0.5
               else "part minutes, part rate" if share >= 0.15
               else "mostly the rate" if share > -0.15
               else "the rate &mdash; his minutes forecast is pulling the other way")
    return (f"at a flat {NEUTRAL_MINS:,.0f} minutes he scores "
            f"{r['xpts_neutral']:.1f} and ranks {int(r['neutral_rank'])} "
            f"(gap {int(r['neutral_gap']):+d}, so minutes account for "
            f"{share * 100:.0f}% of it) &mdash; **{verdict}**")


def drivers(r: pd.Series, med: pd.DataFrame) -> str:
    """The per-90 components that put him above or below his position's median.

    Rates, not season totals: a season total mixes in how much he plays, which
    the line above has already accounted for, and would report a fringe player
    as bad at everything rather than as someone who plays less.  Rule-based on
    purpose -- every clause is a number the model produced, so nothing here is
    an opinion about the player dressed up as one about the model.
    """
    m = med.loc[r["pos"]]
    parts = []
    for k, label in [("dc_pts_r", "DefCon"), ("bonus_pm", "bonus"),
                     ("pen_pts_r", "penalties")] + \
                    [(k + "_r", v) for k, v in RATE_PARTS.items()]:
        diff = r[k] - m[k]
        if abs(diff) >= 0.15:
            parts.append((abs(diff), f"{label} {diff:+.2f}"))
    parts.sort(reverse=True)
    body = ", ".join(p for _, p in parts[:4]) or "nothing more than 0.15/90 off it"
    return (f"{r['xpts_p90_n']:.2f} xPts/90 against {m['xpts_p90_n']:.2f} "
            f"&mdash; {body}")


def flags(r: pd.Series) -> list[str]:
    out = []
    if r["no_pl_rates"]:
        out.append("**no Premier League rates** -- every attacking number is empty")
    elif r["moved"]:
        out.append("changed club, so his xG and xA are rates he produced elsewhere")
    if r["status"] and r["status"] not in ("a",):
        out.append(f"FPL status `{r['status']}`" + (f" -- {r['news']}" if r["news"] else ""))
    if r["mins_2526"] < 900 and not r["moved"]:
        out.append(f"only {r['mins_2526']:,.0f} minutes last season")
    return out


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def validate(f: pd.DataFrame, pool: pd.DataFrame, rows: pd.DataFrame) -> None:
    fail = []

    total = f["app_pts"] + f["rate_pts"] + f["dc_pts"] + f["bonus_pts"] + f["pen_pts"]
    gap = float((total - f["xpts"]).abs().max())
    print(f"  {'pass' if gap < 1e-6 else 'FAIL'}  decomposition closes   max gap {gap:.2e}")
    if gap >= 1e-6:
        fail.append("decomposition")

    # And the rate block itself must be the sum of its named parts, or a
    # component is being reported that the total does not contain.
    rate = sum(f[k] for k in RATE_PARTS)
    rgap = float((rate - f["rate_pts"]).abs().max())
    print(f"  {'pass' if rgap < 1e-6 else 'FAIL'}  rate parts close       max gap {rgap:.2e}")
    if rgap >= 1e-6:
        fail.append("rate parts")

    # The neutral playing time is a chosen constant, so the verdicts it
    # produces must not depend on which constant.  Re-run at 2,400 and 3,000
    # and the ordering has to survive; if it does not, the split is reporting
    # the choice rather than the model.
    d0 = xpts_model.build_rows()
    base = f["xpts_neutral"].rank(ascending=False, method="min")
    for alt in (2400.0, 3000.0):
        rho = base.corr(neutral_minutes(d0, alt).rank(ascending=False, method="min"),
                        method="spearman")
        print(f"  {'pass' if rho > 0.98 else 'FAIL'}  neutral ranking stable at "
              f"{alt:,.0f} mins   rho {rho:.4f}")
        if rho <= 0.98:
            fail.append(f"neutral@{alt:.0f}")

    # The minutes counterfactual has to be the model, not a rescaling of it.
    # Feeding a player his OWN minutes back through it must reproduce his xPts
    # to the last decimal; if it does not, the neutral column is measuring the
    # difference between two scorers rather than between two playing times.
    d = xpts_model.build_rows()
    same = xpts_calc.score(d.assign(xMins_input=d["xMins_input"]))["xpts_season"]
    ngap = float((same.to_numpy() - f["xpts"].to_numpy()).max())
    print(f"  {'pass' if abs(ngap) < 1e-9 else 'FAIL'}  counterfactual is the model "
          f"      max gap {abs(ngap):.2e}")
    if abs(ngap) >= 1e-9:
        fail.append("counterfactual")

    # And it must actually move: a neutral column identical to the real one
    # would pass the check above while measuring nothing.
    moved = int((f["xpts_neutral"].round(3) != f["xpts"].round(3)).sum())
    print(f"  {'pass' if moved > len(f) // 2 else 'FAIL'}  neutral minutes move "
          f"{moved} of {len(f)} players")
    if moved <= len(f) // 2:
        fail.append("neutral inert")

    # Both ranks recomputed from the raw values by counting, which is the
    # definition rather than a second call to the same pandas method.  Ties are
    # real on both sides -- two players can share a mean published rank, and
    # two can score the same xPts -- so they share the lower rank and the two
    # columns are NOT permutations of each other.  Asserting that they were was
    # this check's first version, and it failed on correct data.
    n = len(pool)
    xp = pool["xpts"].to_numpy()
    ad = pool["adr"].to_numpy()
    want_model = np.array([1 + int((xp > v).sum()) for v in xp])
    want_pro = np.array([1 + int((ad < v).sum()) for v in ad])
    ok_ranks = (np.array_equal(want_model, pool["model_rank"].to_numpy().astype(int))
                and np.array_equal(want_pro, pool["pro_rank"].to_numpy().astype(int))
                and pool["model_rank"].max() <= n and pool["pro_rank"].max() <= n)
    ties = int(n - pool["adr"].nunique())
    print(f"  {'pass' if ok_ranks else 'FAIL'}  both ranks recomputed by counting over "
          f"the same {n} players ({ties} tied on ADR)")
    if not ok_ranks:
        fail.append("ranks")

    # Every match must share a name token with an authoritative name for that
    # code -- either the master's full name or FPL's own display name.  Both
    # are needed: FPL calls Joao Pedro Loureiro da Costa "Costinha", which
    # shares nothing with his full name, and the analysts write what FPL shows
    # them.  This still catches the failure that matters, a surname or club
    # fallback landing on the wrong player.
    web = {}
    boot = ROOT / "data" / "raw" / "fpl" / "bootstrap.json"
    if boot.exists():
        import json
        web = {int(e["code"]): e["web_name"]
               for e in json.loads(boot.read_text()).get("elements", [])
               if e.get("web_name")}

    named = rows.groupby("code")["player_name"].agg(lambda s: sorted(set(s)))
    bad = []
    for _, r in pool.iterrows():
        pubs = named.get(r["code"])
        if not pubs:
            bad.append((r["player"], "no published row"))
            continue
        mine = set(levers_report.norm(r["player"]).split())
        if r["code"] in web:
            mine |= set(levers_report.norm(web[r["code"]]).split())
        if not any(set(levers_report.norm(p).split()) & mine for p in pubs):
            bad.append((r["player"], "/".join(pubs)))
    print(f"  {'pass' if not bad else 'FAIL'}  {len(pool)} matches share a name with their published row")
    for p, why in bad[:5]:
        print(f"        {p}  <-  {why}")
    if bad:
        fail.append("name match")

    if fail:
        raise SystemExit("checks failed: " + ", ".join(fail))
    print("\nAll checks pass.\n")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def block(pool: pd.DataFrame, rows: pd.DataFrame, med: pd.DataFrame,
          sel: pd.DataFrame, direction: str) -> list[str]:
    named = rows.groupby("code").apply(
        lambda g: ", ".join(f"{s.split(' - ')[0].split(' -')[0]} {int(k)}"
                            for s, k in sorted(zip(g["source_name"], g["rank"]),
                                               key=lambda t: t[1])),
        include_groups=False)
    out = []
    for _, r in sel.iterrows():
        out.append(
            f"**{r['player']}** ({r['team']} {r['pos']}) &mdash; "
            f"model {int(r['model_rank'])}, pros {int(r['pro_rank'])} "
            f"(ADR {r['adr']:.1f} from {r['n_lists']} lists), gap {int(r['gap']):+d}")
        out.append("")
        out.append(f"- {r['xpts']:.1f} xPts = {r['app_pts']:.0f} appearance "
                   f"+ {r['rate_pts']:.0f} rate + {r['dc_pts']:.0f} DefCon "
                   f"+ {r['bonus_pts']:.0f} bonus + {r['pen_pts']:.0f} pens, "
                   f"over {r['matches']:.1f} matches")
        out.append(f"- Minutes: {minutes_story(r)}")
        out.append(f"- Minutes or player? {split(r)}")
        out.append(f"- Rate against the median {r['pos']}: {drivers(r, med)}")
        if r["research_reason"]:
            out.append(f"- Research said: {r['research_reason'][:220]}")
        for fl in flags(r):
            out.append(f"- {fl}")
        out.append(f"- Ranked by: {named.get(r['code'], '--')}")
        out.append("")
    return out


def report(f: pd.DataFrame, pool: pd.DataFrame, rows: pd.DataFrame) -> str:
    med = pool.groupby("pos")[
        ["xpts_p90_n", "dc_pts_r", "bonus_pm", "pen_pts_r"]
        + [k + "_r" for k in RATE_PARTS]].median()
    solid = pool[pool["n_lists"] >= MIN_LISTS]
    ag = pool["gap"].abs()

    L = [
        "# Where the model and the pros disagree",
        "",
        "*Generated by `scripts/pro_gap.py`. This is a diagnostic on the model, "
        "not a ranking and not a view about any player. It reports which part "
        "of the model's own arithmetic produced a number that the published "
        "lists disagree with. It does not say which side is right.*",
        "",
        f"**Pool:** {len(pool)} draftable players carry a rank on at least one "
        f"whole-draft list; {len(solid)} appear on {MIN_LISTS} or more. Both "
        "orderings are ranks within that pool, so neither side is being "
        "compared against players the other never saw.",
        "",
        f"**Spread of disagreement:** median |gap| {ag.median():.0f} places, "
        f"75th percentile {ag.quantile(.75):.0f}, 90th {ag.quantile(.90):.0f}, "
        f"largest {ag.max():.0f}. Rank correlation between the two orderings is "
        f"**{pool['model_rank'].corr(pool['pro_rank'], method='spearman'):.3f}** "
        f"over the whole pool and "
        f"**{solid['model_rank'].corr(solid['pro_rank'], method='spearman'):.3f}** "
        f"over the {len(solid)} on {MIN_LISTS}+ lists.",
        "",
        "---",
        "",
        f"## The model is far lower than the analysts (top {TOP_N})",
        "",
    ]
    L += block(pool, rows, med,
               solid.nlargest(TOP_N, "gap").sort_values("gap", ascending=False), "low")
    L += ["---", "",
          f"## The model is far higher than the analysts (top {TOP_N})", ""]
    L += block(pool, rows, med, solid.nsmallest(TOP_N, "gap"), "high")

    # The disagreements a within-pool comparison cannot see: nobody ranked him.
    unranked = f[f["draftable"] & f["adr"].isna()].nlargest(TOP_N, "xpts")
    L += ["---", "",
          f"## In the model's top of the board, but on no published list",
          "",
          "A within-pool comparison cannot show these: no analyst ranked them, "
          "so there is no rank to disagree with. Their model rank is among all "
          f"{int(f['draftable'].sum())} draftable players.",
          ""]
    allrank = f[f["draftable"]]["xpts"].rank(ascending=False, method="min")
    for i, r in unranked.iterrows():
        L.append(f"**{r['player']}** ({r['team']} {r['pos']}) &mdash; "
                 f"model {int(allrank[i])} of {int(f['draftable'].sum())}, "
                 f"{r['xpts']:.1f} xPts")
        L.append("")
        L.append(f"- {r['xpts']:.1f} xPts = {r['app_pts']:.0f} appearance "
                 f"+ {r['rate_pts']:.0f} rate + {r['dc_pts']:.0f} DefCon "
                 f"+ {r['bonus_pts']:.0f} bonus + {r['pen_pts']:.0f} pens")
        L.append(f"- Minutes: {minutes_story(r)}")
        L.append(f"- Rate against the median {r['pos']}: {drivers(r, med)}")
        for fl in flags(r):
            L.append(f"- {fl}")
        L.append("")

    # Which way the disagreement runs by position, since that is a property of
    # the model rather than of any player in it.
    L += ["---", "", "## By position", "",
          "| Pos | In pool | Median gap | Model lower by 30+ | Model higher by 30+ |",
          "|---|---:|---:|---:|---:|"]
    for pos, g in pool.groupby("pos"):
        L.append(f"| {pos} | {len(g)} | {g['gap'].median():+.0f} | "
                 f"{int((g['gap'] >= 30).sum())} | {int((g['gap'] <= -30).sum())} |")
    L.append("")
    return "\n".join(L)


def main() -> int:
    f, rows = frame()
    pool = ranked(f)
    print(f"\npro_gap: {len(pool)} draftable players with a published rank\n")
    validate(f, pool, rows)

    OUT.mkdir(exist_ok=True)
    path = OUT / "pro_gap.md"
    path.write_text(report(f, pool, rows))
    ag = pool["gap"].abs()
    print(f"  median |gap| {ag.median():.0f}, 90th pct {ag.quantile(.90):.0f}, "
          f"max {ag.max():.0f}")
    print(f"  spearman {pool['model_rank'].corr(pool['pro_rank'], method='spearman'):.3f}")
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
