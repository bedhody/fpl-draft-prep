"""Expected Premier League games missed to injury, from Transfermarkt histories.

The short version: injury history predicts next season's absence, but weakly.
The model beats a flat league average by 5% of MAE (5.38 against 5.68 games
over 245 held-out player-seasons) and carries a cross-sectional correlation of
+0.25.  That is real and it is small, and most of what determines how much
football a player misses next season is not in his past record.

Source and units.  Transfermarkt publishes one row per injury spell: season,
description, from/to dates, days, and games missed across all competitions.
1,966 players who appeared in a Premier League squad between 2021/22 and
2026/27 were scraped, yielding 11,669 spells; the panel is 3,996 player-seasons
over 1,876 players, of which 1,017 are "regulars" -- 900+ league minutes the
PREVIOUS season, which is the population the model is applied to and therefore
the one every number below is measured on.  Selecting on the target season's
minutes would condition on the outcome, since missing games through injury is
itself the main way a player's minutes fall.

Games missed is NOT taken from Transfermarkt's own column, which counts all
competitions and so runs high for clubs in Europe.  Each spell is intersected
with the player's club's real 38-date league fixture list.  Two checks:
91.4% of the league fixtures the model places inside an injury window are ones
FPL independently records the player playing 0 minutes in, and the ratio comes
out at 0.62 league games per all-competition game.  Allocation is by DATE, not
by Transfermarkt's season label -- Kovacic's 2025/26 absence is 26 games, 6 of
them from a spell Transfermarkt files under 24/25 because it began in June.

What was tested, and what survived:

1. PERSISTENCE.  Lag-1 correlation of league games missed is +0.223 over 1,017
   regular player-season pairs (+0.253 over all 1,923).  Monotone but shallow:
   a clean season is followed by 4.07 games missed, 1-4 by 5.61, 5-14 by 7.52,
   15+ by 9.94, against a population mean of 5.90.

2. INJURY TYPE.  Muscle is 36% of all spells (median 22 days), joint/ligament
   21% (median 36), impact 12%, illness 10%, back 4%.  Recurrence is measured
   against players who were injured but not in that category, which strips out
   general injury-proneness: muscle recurs at 1.84x, back 1.52x, illness 1.50x,
   joint 1.46x, impact 1.26x.  So yes -- a past hamstring predicts a future
   hamstring considerably better than a past fracture predicts a future
   fracture, which is what you would expect and is the one clean finding here.
   It does not survive into the model: splitting history by category makes
   out-of-sample prediction WORSE than using the undifferentiated count
   (muscle-only 5.49, joint-only 5.54, both against 5.38 for all spells).

3. HISTORY LENGTH.  Almost nothing rides on it.  Every combination of currency
   and depth lands between 5.38 and 5.53 held out, against 5.68 for a flat
   prior.  Four equally weighted seasons wins the selection year; one season
   alone is 0.12 worse; recency-tapering is indistinguishable from flat.  This
   is a shallow optimum, and it is reported as such rather than tuned.

4. "CURED".  No, but the effect is smaller than the folklore.  Players carrying
   60+ days out at t-3/t-2 who then had a clean t-1 miss 4.59 league games at t
   (n=125) against 3.73 for players with a light early history and the same
   clean t-1 (n=245).  The gap is +0.86 games with a 95% bootstrap CI of
   [-0.63, +2.44] -- 87% of resamples positive, so suggestive and NOT
   significant.  Both groups sit below the 5.87 population mean.  With two
   clean seasons the residue all but vanishes: 3.80 (n=44) against 3.54
   (n=121).  The practical reading is that one clean season does most of the
   regressing and a second finishes it; an injury-prone reputation outlives the
   evidence for it.

5. AGE.  Rejected.  Correlation with games missed is -0.024, and the age
   gradient among regulars runs the wrong way (6.06 under 23, 5.49 over 32) --
   survivorship, since a 33-year-old still playing 900 minutes has proven
   something a 21-year-old has not.  Adding age makes the back-test worse
   (5.45 against 5.38, correlation +0.205 against +0.253).

5b. PRIOR-SEASON MINUTES.  Rejected, but it is the closest call and the most
   promising thing here.  Absence falls steadily with last season's minutes
   (6.81 at 900-1200 up through 4.02 at 3000+).  Adding it misses the selection
   year by 0.01 (5.16 against 5.15) and helps held out by 0.03 (5.35 against
   5.38).  That is noise in both directions, so the pre-stated rule rejects it.

6. BURDEN vs SPELLS.  A tie that had to be broken.  Days out and spell count
   both score 5.15 on the selection year; the documented tie-break is selection
   correlation, which goes to spells (+0.265 against +0.234).  Held out, spells
   is 5.38/+0.253 against 5.44/+0.210.  Worth 0.06 games of MAE -- so the
   honest claim is that the two are indistinguishable, and specifically that
   one six-month cruciate rupture says little more about next season than one
   short muscle strain does.

CALIBRATION -- read this before using the level.  On the 81 target players who
were in a 2025/26 squad the model runs 24.8% HIGH: they actually missed 4.40
league games on average against 6.00 for the panel's regulars.  The model beats
a naive flat prior (MAE 4.80 against 5.10) and is the only one of the two with
any cross-sectional signal (+0.272), but it loses on MAE to a flat prior placed
at that group's own mean (4.46), which is an oracle and not available in
advance.  So: treat the ORDERING as the usable part and the LEVEL as roughly a
quarter too high for heavy-minutes players.  Prior-season minutes is the
obvious correction and did not clear the bar; a bigger panel would settle it.

Not folded in: `current_injury` and `current_injury_games` record an injury a
player is carrying on 16 August 2026.  That is a fact about today, not a
propensity, and adding it to the projection would double-count, since the model
already prices the average chance of being hurt at any moment.
"""
from __future__ import annotations

import re
import sys

import numpy as np
import pandas as pd

import fetch_transfermarkt as tmf
from common import PROC, RAW, norm_name, similarity, team_code

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

N_TARGET = 150

# Seasons with a real Premier League fixture calendar, which is what turns an
# injury spell into a count of league games missed.
CAL_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
TARGET_YEARS = [2021, 2022, 2023, 2024, 2025]
TEST_YEAR = 2025                 # held out: fit on <= 2024/25, predict 2025/26
HIST_DEPTH = 5
# A Transfermarkt squad is ~40 registered players; ~25 of them ever play. The
# model is applied to 150 heavy-minutes players, so it is fitted and judged on
# players who were regulars the season BEFORE the one being predicted.
REGULAR_MINUTES = 900

# The history currency and its depth are chosen jointly on the 2024/25
# selection year, by MAE with correlation as the tie-break.  Days out and spell
# count TIE on selection MAE at 5.15, and the tie-break goes to spells
# (+0.265 against +0.234), which is also the more stable of the two held out.
# Honest reading: the two are indistinguishable, and the whole choice is worth
# about 0.06 games of MAE.  One six-month cruciate rupture apparently says
# little more about next season than one short muscle strain does.
FEATURE = "spells"
HISTORY_WEIGHTS = (1.0, 1.0, 1.0, 1.0)
# Weight of the population prior, in seasons.  The back-test curve is almost
# flat in K, so this is a tie-break rather than a tuned parameter.
PRIOR_SEASONS = 1.0

TM_POS = {"Goalkeeper": "GKP", "Defender": "DEF", "Midfield": "MID",
          "Attack": "FWD"}

# Ordered: the first pattern that matches wins, so the specific joint and
# muscle sites are tested before the generic impact words.
CATEGORIES = [
    ("illness", r"\bill\b|illness|virus|corona|covid|flu\b|influenza|fever|"
                r"infect|tonsil|appendic|mononucle|stomach|angina|pneumon|"
                r"quarantin|\bcold\b"),
    ("joint",   r"knee|ankle|cruciate|meniscus|cartilage|ligament|patella|\bacl\b"),
    ("muscle",  r"hamstring|groin|calf|thigh|muscle|muscular|adductor|quadricep|"
                r"hip flexor|abductor|strain"),
    ("impact",  r"fractur|broken|\bbreak\b|knock|bruise|contusion|concussion|"
                r"head injury|dead leg|laceration|nose|cheek|jaw\b|rib\b|"
                r"dislocat|tooth|eye |\bfacial\b|\bface\b"),
    ("back",    r"back|spine|disc\b|lumbar|lumbag|pelvi|\bhip\b|sacro"),
]
CATS = [c for c, _ in CATEGORIES] + ["other"]

SCRAPE_DATE = pd.Timestamp("2026-08-16")


def categorise(desc: str) -> str:
    d = str(desc).lower()
    for name, pat in CATEGORIES:
        if re.search(pat, d):
            return name
    return "other"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def slug_to_code(slug: str) -> str:
    n = slug.replace("-amp-", " and ").replace("-", " ")
    n = re.sub(r"^(fc|afc) ", "", n)
    n = re.sub(r" (fc|afc)$", "", n)
    return team_code(n)


def club_codes() -> dict[int, str]:
    """{tm_club_id: FPL three-letter code}, read from the cached comp pages."""
    out = {}
    for season in tmf.SQUAD_SEASONS:
        for cid, slug in tmf.club_ids(season).items():
            out[cid] = slug_to_code(slug)
    return out


def pl_calendar() -> dict[tuple[int, str], np.ndarray]:
    """{(season_start, club): the club's 38 league fixture dates}.

    Kickoff dates come from the vaastav gameweek files, which carry one row per
    player per fixture.  Every one of the 100 club-seasons resolves to exactly
    38 distinct dates, which is the check that this is a complete calendar
    rather than a partial one.
    """
    cal = {}
    for s in CAL_SEASONS:
        y = int(s[:4])
        g = pd.read_csv(RAW / "vaastav" / f"merged_gw_{s}.csv",
                        usecols=["team", "kickoff_time"])
        g["kd"] = pd.to_datetime(g.kickoff_time, format="mixed",
                                 utc=True).dt.tz_convert(None).dt.normalize()
        g["code"] = g.team.map(team_code)
        for code, sub in g.groupby("code"):
            cal[(y, code)] = np.sort(sub.kd.unique())
    return cal


def fpl_minutes(sq: pd.DataFrame, codes: dict) -> pd.DataFrame:
    """League minutes per (Transfermarkt player, season), by name within club.

    A Transfermarkt squad page lists roughly 40 registered players a club --
    academy and loan-listed included -- where only about 25 ever play.  Without
    minutes the panel is half teenagers who miss nothing because they were
    never going to feature, which drags the population mean down and makes the
    model look better than it is.  Matching is scoped to one club-season, so
    the candidate pool is ~30 names rather than ~800.
    """
    rows = []
    for s in CAL_SEASONS:
        y = int(s[:4])
        g = (pd.read_csv(RAW / "vaastav" / f"merged_gw_{s}.csv",
                         usecols=["name", "team", "minutes", "element",
                                  "fixture"])
             .drop_duplicates(subset=["element", "fixture"]))
        g["club"] = g.team.map(team_code)
        f = (g.groupby("element")
               .agg(name=("name", "first"), club=("club", "first"),
                    minutes=("minutes", "sum")).reset_index())
        f["key"] = f.name.map(norm_name)
        tm = sq[sq.season_start == y].copy()
        tm["club"] = tm.club_id.map(codes)
        tm["key"] = tm.tm_name.map(norm_name)
        for club, pool in f.groupby("club"):
            cand = tm[tm.club == club]
            for c in cand.itertuples():
                best, score = None, -1.0
                for q in pool.itertuples():
                    v = similarity(c.key, q.key)
                    if v > score:
                        best, score = q, v
                if best is not None and score >= 90:
                    rows.append({"tm_id": c.tm_id, "y": y,
                                 "minutes": best.minutes, "match": score})
    out = pd.DataFrame(rows)
    # A player can match in two clubs after a transfer; keep his best-evidenced
    # row and sum the minutes he played across them.
    return (out.groupby(["tm_id", "y"], as_index=False)
               .agg(minutes=("minutes", "sum"), match=("match", "max")))


def load_tm():
    sq = pd.read_csv(PROC / "tm_squads.csv")
    inj = pd.read_csv(PROC / "tm_injuries.csv")
    # Players whose injury page could not be read are dropped from the squad
    # table entirely.  Keeping them would enter them into the panel as
    # injury-free, which is the one error that biases the model downward
    # exactly where it matters.
    path = PROC / "tm_unreadable.csv"
    if not path.exists():
        raise SystemExit(
            f"{path} is missing. Run fetch_transfermarkt.py first -- without "
            f"it, players whose injury page could not be read would silently "
            f"enter the panel as injury-free.")
    bad = pd.read_csv(path)
    if len(bad):
        sq = sq[~sq.tm_id.isin(bad.tm_id)].copy()
    inj["frm"] = pd.to_datetime(inj["from"], errors="coerce")
    inj["to"] = pd.to_datetime(inj["until"], errors="coerce")
    inj = inj[inj.frm.notna()].copy()
    # An open-ended spell ("Return unknown") is closed at the scrape date, so it
    # contributes the absence actually observed rather than an infinite one.
    inj["to"] = inj["to"].fillna(SCRAPE_DATE)
    inj["to"] = np.maximum(inj["to"], inj.frm)
    inj["days"] = inj.days.fillna((inj["to"] - inj.frm).dt.days + 1)
    inj["cat"] = inj.injury.map(categorise)
    return sq, inj


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def build_panel(sq, inj, cal, codes) -> pd.DataFrame:
    """One row per (player, season) spent in a Premier League squad.

    The target is league games missed, counted exactly: the number of the
    club's own 38 fixture dates that fall inside an injury window.  That is a
    real count rather than a rescaling of Transfermarkt's all-competition
    figure, which is inflated for clubs in Europe.
    """
    sq = sq.copy()
    sq["club"] = sq.club_id.map(codes)
    exp = (sq[sq.season_start.isin(TARGET_YEARS)]
           .groupby(["tm_id", "season_start"])
           .agg(clubs=("club", lambda s: sorted(set(s))), dob=("dob", "first"))
           .reset_index())

    inj = inj.copy()
    inj["club"] = inj.club_id.map(codes)
    spells = {t: g for t, g in inj.groupby("tm_id")}

    def count(dates, f, t):
        if len(dates) == 0 or len(f) == 0:
            return 0
        return int(((dates[:, None] >= f[None, :]) &
                    (dates[:, None] <= t[None, :])).any(axis=1).sum())

    rows = []
    for r in exp.itertuples():
        pl_clubs = [c for c in r.clubs if (r.season_start, c) in cal]
        if not pl_clubs:
            continue
        # The denominator is one club's 38 fixtures.  A player who moves between
        # two Premier League clubs in January still has 38 league matches
        # available to him, so unioning both calendars overstates his exposure.
        primary = cal[(r.season_start, pl_clubs[0])]
        g = spells.get(r.tm_id)
        missed = 0
        if g is not None and len(g):
            # Each spell is counted against the calendar of the club he was at
            # when it happened; Transfermarkt records that club on the spell.
            for club, gg in g.groupby(g.club.where(g.club.isin(pl_clubs),
                                                   pl_clubs[0])):
                dates = cal.get((r.season_start, club), primary)
                missed += count(dates, gg.frm.values.astype("datetime64[ns]"),
                                gg["to"].values.astype("datetime64[ns]"))
        rows.append({"tm_id": r.tm_id, "y": r.season_start, "club": pl_clubs[0],
                     "n_clubs": len(pl_clubs), "dob": r.dob,
                     "fixtures": len(primary),
                     "pl_missed": min(missed, len(primary))})
    return pd.DataFrame(rows)


def season_features(inj, years) -> pd.DataFrame:
    """Days out, spell count and category splits, per player per season.

    Days out is the currency history is carried in, not games missed, for two
    reasons: it needs no fixture calendar and so reaches back as far as
    Transfermarkt does, and it is not inflated by a club playing in Europe.
    Whether that was the right choice is tested, not assumed -- see test 6.
    """
    out = []
    for y in years:
        a, b = pd.Timestamp(f"{y}-07-01"), pd.Timestamp(f"{y + 1}-06-30")
        lo = np.maximum(inj.frm.values, np.datetime64(a))
        hi = np.minimum(inj["to"].values, np.datetime64(b))
        ov = np.maximum((hi - lo).astype("timedelta64[D]").astype(float) + 1, 0)
        d = inj.assign(ov=ov)
        d = d[d.ov > 0]
        g = d.groupby("tm_id").agg(days=("ov", "sum"), spells=("ov", "size"))
        for cat in CATS:
            sub = d[d.cat == cat].groupby("tm_id").ov
            g[f"d_{cat}"] = sub.sum()
            g[f"n_{cat}"] = sub.size()
        g = g.fillna(0).reset_index()
        g["y"] = y
        out.append(g)
    return pd.concat(out, ignore_index=True)


def assemble(sq, inj, cal, codes, mins=None):
    """Panel plus lagged history columns, ready for the back-test."""
    panel = build_panel(sq, inj, cal, codes)
    if mins is not None:
        panel = panel.merge(mins[["tm_id", "y", "minutes"]],
                            on=["tm_id", "y"], how="left")
        # No FPL row at all means he never appeared in the league that season.
        panel["minutes"] = panel.minutes.fillna(0.0)
    years = range(min(TARGET_YEARS) - HIST_DEPTH, max(TARGET_YEARS) + 1)
    feat = season_features(inj, list(years))
    panel["dob"] = pd.to_datetime(panel.dob, errors="coerce")
    panel["age"] = ((pd.to_datetime(panel.y.astype(str) + "-08-01") - panel.dob)
                    .dt.days / 365.25)

    p = panel.copy()
    cols = ["days", "spells"] + [f"d_{c}" for c in CATS] + \
           [f"n_{c}" for c in CATS]
    for lag in range(1, HIST_DEPTH + 1):
        f = feat.copy()
        f["y"] = f.y + lag
        f = f[["tm_id", "y"] + cols].rename(
            columns={c: f"{c}_L{lag}" for c in cols})
        p = p.merge(f, on=["tm_id", "y"], how="left")
        for c in cols:
            p[f"{c}_L{lag}"] = p[f"{c}_L{lag}"].fillna(0.0)
        # A season only counts as observed history if he was 18 by its start;
        # otherwise "no injuries recorded" is an academy player, not a fit one.
        p[f"obs_L{lag}"] = ((p.age - lag) >= 18).astype(float)
    keep = {"pl_missed": "plm"}
    if "minutes" in panel.columns:
        keep["minutes"] = "minutes"
    pm = panel[["tm_id", "y"] + list(keep)]
    for lag in range(1, 5):
        q = pm.copy()
        q["y"] = q.y + lag
        q = q.rename(columns={c: f"{stem}_L{lag}" for c, stem in keep.items()})
        p = p.merge(q, on=["tm_id", "y"], how="left")
    return p, panel, feat


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def blend(p, feature, weights, K, mu) -> np.ndarray:
    """Recency-weighted own history, shrunk toward the population mean.

    The gamma-Poisson shape used elsewhere in the repo (cards_model.py,
    team_defence.py): K is the weight of the prior, measured in seasons, so a
    player with one season of history is mostly the league and a player with
    five is mostly himself.
    """
    num = np.zeros(len(p))
    den = np.zeros(len(p))
    for i, w in enumerate(weights, start=1):
        obs = p[f"obs_L{i}"].values
        num += w * p[f"{feature}_L{i}"].values * obs
        den += w * obs
    return (num + K * mu) / (den + K)


def metrics(pred, act):
    pred, act = np.asarray(pred, float), np.asarray(act, float)
    mae = np.abs(pred - act).mean()
    corr = np.corrcoef(pred, act)[0, 1] if pred.std() > 1e-12 else np.nan
    bias = pred.sum() / act.sum() - 1 if act.sum() else np.nan
    return mae, corr, bias


def line(label, pred, act, width=38):
    mae, corr, bias = metrics(pred, act)
    # A flat prior has no cross-sectional variation, so its correlation is
    # undefined rather than zero; printing it as n/a keeps that honest.
    c = "   n/a" if np.isnan(corr) else f"{corr:+.3f}"
    print(f"    {label:<{width}s} MAE {mae:5.2f}  corr {c}  bias {bias:+7.1%}")


def target_set(m: pd.DataFrame) -> pd.DataFrame:
    """The ~150 most relevant players: draftable for 2026/27, ranked by
    projected minutes (Solio's forecast, falling back to 2025/26 minutes)."""
    d = m[m.draftable_2627 == True].copy()
    d["xm"] = d.solio_season_xmins.fillna(d.minutes)
    return d[d.xm.notna()].nlargest(N_TARGET, "xm").copy()


def resolve(targets: pd.DataFrame, sq: pd.DataFrame,
            codes: dict) -> pd.DataFrame:
    """Match each target player to a Transfermarkt id.

    Transfermarkt ids are not Opta ids and there is no crosswalk, so the match
    is by name -- but scoped to the player's own 2026/27 club squad, which cuts
    the candidate pool from ~2000 to ~25 and makes the position a genuine
    second check rather than a tie-break.
    """
    s = sq.copy()
    s["club"] = s.club_id.map(codes)
    s["key"] = s.tm_name.map(norm_name)
    s["pos"] = s.tm_pos.map(TM_POS)
    cur = s[s.season_start == 2026]
    # Most recent record per player, for the fall-back search.
    everyone = (s.sort_values("season_start")
                 .drop_duplicates("tm_id", keep="last"))

    def best_in(pool, name):
        b, sc = None, -1.0
        for c in pool.itertuples():
            v = similarity(name, c.key)
            if v > sc:
                b, sc = c, v
        return b, sc

    rows = []
    for p in targets.itertuples():
        name = norm_name(p.player)
        best, score = best_in(cur[cur.club == p.team_2627], name)
        route = "2026/27 club squad"
        pool = int((cur.club == p.team_2627).sum())
        # The two sources can disagree about where a player is: Transfermarkt
        # dropped Trevoh Chalobah from Chelsea's 26/27 squad while FPL still
        # lists him there.  Falling back to a league-wide search on name plus
        # position recovers him instead of discarding a real player over a
        # transfer the two sources timed differently.
        if score < 90:
            alt, asc = best_in(everyone, name)
            if alt is not None and asc >= 90 and alt.pos == p.position:
                best, score, route = alt, asc, "league-wide fall-back"
        rows.append({
            "code": p.code, "player": p.player, "club": p.team_2627,
            "position": p.position, "xmins": p.xm,
            "tm_id": int(best.tm_id) if best is not None else None,
            "tm_name": best.tm_name if best is not None else None,
            "tm_pos": best.pos if best is not None else None,
            "tm_dob": best.dob if best is not None else None,
            "current_injury": best.current_injury if best is not None else None,
            "match_score": score, "pool": pool, "match_route": route,
            "pos_agrees": bool(best is not None and best.pos == p.position),
        })
    return pd.DataFrame(rows)


def validate_conversion(res: pd.DataFrame, inj: pd.DataFrame) -> None:
    """Does a Transfermarkt injury window really mean he did not play?

    This is the check behind the all-competitions -> Premier League conversion.
    Rather than rescaling Transfermarkt's `games missed` by some ratio, the
    model counts the player's own league fixtures inside the injury window --
    so the thing to prove is that those fixtures are ones he actually sat out.
    Ground truth is FPL minutes, which are independent of Transfermarkt.
    """
    g = (pd.read_csv(RAW / "vaastav" / "merged_gw_2025-26.csv",
                     usecols=["element", "fixture", "kickoff_time", "minutes"])
         .drop_duplicates(subset=["element", "fixture"]))
    g["kd"] = pd.to_datetime(g.kickoff_time, format="mixed",
                             utc=True).dt.tz_convert(None).dt.normalize()
    ids = (pd.read_csv(PROC / "fpl_seasons.csv")
             .query("season == '2025/26'")[["code", "id"]])
    r = res.merge(ids, on="code", how="inner")

    spells = inj[(inj.frm <= pd.Timestamp("2026-06-30")) &
                 (inj["to"] >= pd.Timestamp("2025-07-01"))]
    by_player = {t: v for t, v in spells.groupby("tm_id")}

    in_window, in_window_zero, tm_games, pl_games, n = 0, 0, 0, 0, 0
    for row in r.itertuples():
        if row.tm_id not in by_player:
            continue
        gg = g[g.element == row.id]
        if len(gg) < 30:
            continue
        s = by_player[row.tm_id]
        inside = ((gg.kd.values[:, None] >= s.frm.values[None, :]) &
                  (gg.kd.values[:, None] <= s["to"].values[None, :])).any(axis=1)
        in_window += int(inside.sum())
        in_window_zero += int((gg.minutes.values[inside] == 0).sum())
        tm_games += int(s.games_missed.sum())
        pl_games += int(inside.sum())
        n += 1
    print(f"\n  CONVERSION: all-competition games -> Premier League games")
    print(f"    {n} of the {len(res)} target players had both a 2025/26 FPL "
          f"record and an injury spell that season")
    print(f"    league fixtures falling inside an injury window: {in_window}")
    print(f"    ... of which the player actually played 0 minutes: "
          f"{in_window_zero} ({in_window_zero / max(in_window, 1):.1%})")
    print(f"    Transfermarkt games missed (all competitions): {tm_games}")
    print(f"    league games missed as counted here:           {pl_games} "
          f"({pl_games / max(tm_games, 1):.2f} per all-competition game)")
    print(f"    So the conversion is a count against the real fixture list, not")
    print(f"    a fitted ratio; the {in_window_zero / max(in_window, 1):.0%} "
          f"figure is what validates it.")

def worked_example(panel, inj) -> None:
    """One player, hand-checked end to end against the two source pages.

    Mateo Kovacic (Transfermarkt id 51471), 2025/26.  Two spells touch the
    season: an Achilles surgery Transfermarkt labels 24/25, running
    02/06/2025-03/10/2025, and an ankle injury labelled 25/26 running
    27/10/2025-12/03/2026.  Counted against Manchester City's actual fixture
    list those cover 6 and 20 league games; FPL has him on 0 minutes in all 26
    of them and 125 minutes for the season.

    The case earns its place because of the first spell.  Allocating absences
    by Transfermarkt's own season label would charge those 6 games to 2024/25
    and score Kovacic at 20.  The model allocates by DATE, which is the only
    reading that survives a spell straddling the summer -- and long spells
    straddling the summer are exactly the serious injuries that matter most.
    """
    got = panel[(panel.tm_id == 51471) & (panel.y == 2025)]
    n = int(got.pl_missed.iloc[0]) if len(got) else -1
    label_based = int(inj[(inj.tm_id == 51471)
                          & (inj.tm_season == "25/26")].games_missed.sum())
    ok = "OK" if n == 26 else f"MISMATCH (hand-computed 26)"
    print(f"\n    worked example -- Kovacic 2025/26: model {n} league games "
          f"missed, {ok}")
    print(f"      6 from a spell Transfermarkt labels 24/25 + 20 from one it "
          f"labels 25/26;")
    print(f"      he played 0 minutes in all 26. Allocating by Transfermarkt's "
          f"season")
    print(f"      label instead of by date would have scored him 20, and its "
          f"own")
    print(f"      all-competition figure for the 25/26 label alone is "
          f"{label_based}.")


def ols(x, y):
    """Least squares intercept and slope, so each candidate history feature
    gets its own fair conversion into league games rather than being compared
    on a scale it was never in."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    A = np.vstack([np.ones_like(x), x]).T
    b0, b1 = np.linalg.lstsq(A, y, rcond=None)[0]
    return b0, b1


def fit_predict(train, test, feature, weights, K):
    """Fit the shrinkage blend and its days->games map on train, score test."""
    mu = train[f"{feature}_L1"][train.obs_L1 > 0].mean()
    tr = blend(train, feature, weights, K, mu)
    b0, b1 = ols(tr, train.pl_missed.values)
    return b0 + b1 * blend(test, feature, weights, K, mu), (b0, b1, mu)


def test_persistence(p, reg) -> None:
    print("\n  [1] PERSISTENCE -- does last season's absence predict this one?")
    for name, d in (("all squad members", p[p.plm_L1.notna()]),
                    ("regulars (900+ minutes last season)", reg)):
        r = np.corrcoef(d.plm_L1, d.pl_missed)[0, 1]
        print(f"    {name:<38s} r = {r:+.3f}  over {len(d)} player-season pairs")
    print(f"\n    what last season's absence implies, among regulars:")
    for lo, hi, name in [(0, 0, "clean last season"),
                         (1, 4, "1-4 missed last season"),
                         (5, 14, "5-14 missed last season"),
                         (15, 99, "15+ missed last season")]:
        s = reg[(reg.plm_L1 >= lo) & (reg.plm_L1 <= hi)]
        print(f"      {name:<26s} n={len(s):4d}   mean this season "
              f"{s.pl_missed.mean():5.2f}")
    print(f"    regulars' mean {reg.pl_missed.mean():.2f} games missed "
          f"per player-season")
    # Days out is the currency the model actually carries history in, so its
    # persistence is the number that matters more than games missed.
    r = np.corrcoef(reg.days_L1, reg.pl_missed)[0, 1]
    rd = np.corrcoef(reg.days_L1, reg.days_L2)[0, 1]
    print(f"    days out at t-1 vs games missed at t: r = {r:+.3f}; "
          f"days out t-1 vs t-2: r = {rd:+.3f}")


def test_categories(inj, panel, feat) -> None:
    print("\n  [2] INJURY TYPE -- which categories recur?")
    d = inj[inj.frm.dt.year >= 2015]
    print(f"    {len(d)} spells since 2015 by category "
          f"(share of spells, median days):")
    for cat in CATS:
        s = d[d.cat == cat]
        print(f"      {cat:<9s} n={len(s):5d}  {len(s) / len(d):5.1%}  "
              f"median {s.days.median():5.1f} days  mean {s.days.mean():6.1f}")

    # Recurrence: given >=1 spell of this category in the prior three seasons,
    # how often does it come back in the target season?
    #
    # The naive comparison -- "had this category before" against "did not" --
    # is confounded, because anyone with a history of category c also tends to
    # have a history of everything else, and injury-prone players get injured.
    # The third column is the one that isolates the category: players who WERE
    # injured in the prior three seasons, but never in this category.
    f = feat.set_index(["tm_id", "y"])
    print("\n    recurrence -- P(a spell of this category in season t):")
    print(f"      {'category':<9s} {'hist of c':>10s} {'inj, not c':>11s} "
          f"{'no injury':>10s} {'lift vs':>9s} {'n hist':>7s}")
    print(f"      {'':<9s} {'(a)':>10s} {'(b)':>11s} {'(c)':>10s} "
          f"{'(b)':>9s} {'of c':>7s}")
    any_hist = np.array([
        sum(f["spells"].get((r.tm_id, r.y - k), 0) for k in (1, 2, 3)) > 0
        for r in panel.itertuples()])
    for cat in CATS:
        hist = np.array([
            sum(f[f"n_{cat}"].get((r.tm_id, r.y - k), 0) for k in (1, 2, 3)) > 0
            for r in panel.itertuples()])
        tgt = np.array([f[f"n_{cat}"].get((r.tm_id, r.y), 0) > 0
                        for r in panel.itertuples()])
        a = tgt[hist].mean() if hist.any() else np.nan
        other = any_hist & ~hist
        b = tgt[other].mean() if other.any() else np.nan
        c = tgt[~any_hist].mean() if (~any_hist).any() else np.nan
        lift = a / b if b and not np.isnan(b) else np.nan
        print(f"      {cat:<9s} {a:9.1%} {b:10.1%} {c:9.1%} {lift:8.2f}x "
              f"{hist.sum():7d}")

    # Which category's history best predicts total absence next season?
    print("\n    correlation of prior-3-season days in each category with "
          "league games missed in season t:")
    for cat in CATS:
        h = np.array([sum(f[f"d_{cat}"].get((r.tm_id, r.y - k), 0)
                          for k in (1, 2, 3)) for r in panel.itertuples()])
        print(f"      {cat:<9s} r = {np.corrcoef(h, panel.pl_missed)[0, 1]:+.3f}")


SCHEMES = [
    ("1 season",                    (1.0,)),
    ("2 equal",                     (1.0, 1.0)),
    ("3 equal",                     (1.0, 1.0, 1.0)),
    ("4 equal",                     (1.0, 1.0, 1.0, 1.0)),
    ("5 equal",                     (1.0, 1.0, 1.0, 1.0, 1.0)),
    ("3, recency 0.5/0.3/0.2",      (0.5, 0.3, 0.2)),
    ("4, recency .4/.3/.2/.1",      (0.4, 0.3, 0.2, 0.1)),
    ("5, recency .3/.25/.2/.15/.1", (0.3, 0.25, 0.2, 0.15, 0.1)),
]


def test_history_length(p, train_years, test_year) -> None:
    """How deep should the history run, and how should it be weighted?

    Two columns, deliberately.  Picking the weights on the same season they are
    then reported against is selection on the test set, so the scheme is chosen
    on an inner year -- fitted on everything before it -- and only then scored
    on the held-out season.  The two columns disagreeing would mean the choice
    is noise; them agreeing is what licenses the pick.
    """
    print("\n  [3] HISTORY LENGTH -- how many prior seasons, and weighted how?")
    inner = max(train_years)
    inner_tr = p[p.y < inner]
    inner_te = p[p.y == inner]
    tr, te = p[p.y.isin(train_years)], p[p.y == test_year]
    print(f"    select on {inner}/{str(inner + 1)[2:]} (fitted on earlier "
          f"seasons, n={len(inner_te)}), then score on "
          f"{test_year}/{str(test_year + 1)[2:]} (fitted on "
          f"{sorted(train_years)}, n={len(te)})")
    print(f"    Depth and currency interact, so they are swept jointly rather "
          f"than one\n    at a time -- the best depth for days out need not be "
          f"the best for spells.")
    print(f"    {'feature':<8s} {'scheme':<30s} {'sel MAE':>8s} {'sel corr':>9s} "
          f"{'held-out MAE':>13s} {'held-out corr':>14s}")
    # Selection MAE first, then selection CORRELATION as the tie-break. Both
    # are computed on the inner year only. The tie-break is needed and is not
    # cosmetic: days/'3 equal' and spells/'4 equal' both land on 5.15, and
    # breaking that by list order rather than by a stated criterion would be
    # an arbitrary choice dressed up as a back-test.
    best, best_key = None, (np.inf, 0.0)
    for feature in ("days", "spells"):
        for name, w in SCHEMES:
            ip, _ = fit_predict(inner_tr, inner_te, feature, w, PRIOR_SEASONS)
            imae, icorr, _ = metrics(ip, inner_te.pl_missed.values)
            op, _ = fit_predict(tr, te, feature, w, PRIOR_SEASONS)
            omae, ocorr, _ = metrics(op, te.pl_missed.values)
            key = (round(imae, 2), -icorr)
            if key < best_key:
                best, best_key = (feature, name, w), key
            print(f"    {feature:<8s} {name:<30s} {imae:8.2f} {icorr:+9.3f} "
                  f"{omae:13.2f} {ocorr:+14.3f}")
    flat_i = np.full(len(inner_te), inner_tr.pl_missed.mean())
    flat_o = np.full(len(te), tr.pl_missed.mean())
    print(f"    {'':<8s} {'flat prior (league mean)':<30s} "
          f"{np.abs(flat_i - inner_te.pl_missed.values).mean():8.2f} "
          f"{'n/a':>9s} "
          f"{np.abs(flat_o - te.pl_missed.values).mean():13.2f} "
          f"{'n/a':>14s}")
    print(f"    -> selection year picks {best[0]} / '{best[1]}'; "
          f"shipped as FEATURE={FEATURE!r}, HISTORY_WEIGHTS={HISTORY_WEIGHTS}")
    print(f"    Every cell above sits between 5.1 and 5.5 against a flat prior "
          f"of 5.4/5.7,\n    so depth is a shallow optimum and the pick "
          f"between them is close to a toss-up.")

    print("\n    shrinkage sweep on the held-out season, weights as configured:")
    for K in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        pred, _ = fit_predict(tr, te, FEATURE, HISTORY_WEIGHTS, K)
        line(f"K = {K}", pred, te.pl_missed.values, width=30)


def test_cured(p) -> None:
    print("\n  [4] THE 'CURED' QUESTION -- does a clean season wipe the slate?")
    d = p[p.obs_L3 > 0].copy()
    d["early"] = d.days_L3 + d.days_L2         # burden at t-3 and t-2
    d["recent"] = d.days_L1                    # the season in between
    heavy = d.early >= 60                      # roughly two months out
    clean = d.recent <= 7
    mu = d.pl_missed.mean()
    print(f"    {len(d)} player-seasons with three observed prior seasons; "
          f"population mean {mu:.2f} games missed")
    print(f"    'heavy' = 60+ days out across t-3 and t-2; "
          f"'clean' = 7 or fewer days at t-1")
    groups = [
        ("heavy t-3/t-2, clean t-1", heavy & clean),
        ("heavy t-3/t-2, not clean t-1", heavy & ~clean),
        ("light t-3/t-2, clean t-1", ~heavy & clean),
        ("light t-3/t-2, not clean t-1", ~heavy & ~clean),
    ]
    for name, m in groups:
        s = d[m]
        print(f"      {name:<30s} n={len(s):4d}  mean {s.pl_missed.mean():5.2f}  "
              f"median {s.pl_missed.median():4.1f}  "
              f"vs population {s.pl_missed.mean() / mu:.2f}x")
    # The decisive comparison: two groups that BOTH had a clean season at t-1
    # and differ only in what happened before it.  If a clean season cured
    # anyone, these two would be the same.
    a = d[heavy & clean].pl_missed.values
    b = d[~heavy & clean].pl_missed.values
    rng = np.random.default_rng(0)
    diff = np.array([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean()
                     for _ in range(4000)])
    lo, hi = np.percentile(diff, [2.5, 97.5])
    print(f"\n    both groups had a clean t-1 and differ only in t-3/t-2:")
    print(f"      heavy-then-clean {a.mean():.2f} vs light-then-clean "
          f"{b.mean():.2f} games")
    print(f"      difference {a.mean() - b.mean():+.2f}, 95% bootstrap CI "
          f"[{lo:+.2f}, {hi:+.2f}], {(diff > 0).mean():.1%} of resamples "
          f"positive")
    # Two clean seasons, for the "one or two seasons" half of the question.
    d2 = p[p.obs_L4 > 0].copy()
    h2 = (d2.days_L4 + d2.days_L3) >= 60
    c2 = (d2.days_L1 <= 7) & (d2.days_L2 <= 7)
    mu2 = d2.pl_missed.mean()
    print(f"\n    two clean seasons (t-1 and t-2), heavy at t-4/t-3; "
          f"population mean {mu2:.2f}")
    for name, m in [("heavy t-4/t-3, two clean seasons", h2 & c2),
                    ("light t-4/t-3, two clean seasons", ~h2 & c2)]:
        s = d2[m]
        print(f"      {name:<34s} n={len(s):4d}  mean {s.pl_missed.mean():5.2f}"
              f"  vs population {s.pl_missed.mean() / mu2:.2f}x")


def test_age(p, train_years, test_year) -> None:
    print("\n  [5] AGE -- does it add anything on top of injury history?")
    tr = p[p.y.isin(train_years) & p.age.notna()]
    te = p[(p.y == test_year) & p.age.notna()]
    print(f"    mean league games missed by age band (all panel seasons):")
    band = pd.cut(p.age, [0, 23, 26, 29, 32, 99],
                  labels=["<23", "23-25", "26-28", "29-31", "32+"])
    g = p.groupby(band, observed=True).pl_missed.agg(["size", "mean"])
    for k, r in g.iterrows():
        print(f"      {str(k):<7s} n={int(r['size']):4d}  mean {r['mean']:5.2f}")
    print(f"    raw correlation of age with games missed: "
          f"{np.corrcoef(p.age.fillna(p.age.mean()), p.pl_missed)[0, 1]:+.3f}")

    w = HISTORY_WEIGHTS
    mu = tr[f"{FEATURE}_L1"][tr.obs_L1 > 0].mean()
    btr, bte = (blend(tr, FEATURE, w, PRIOR_SEASONS, mu),
                blend(te, FEATURE, w, PRIOR_SEASONS, mu))
    b0, b1 = ols(btr, tr.pl_missed.values)
    line("history only", b0 + b1 * bte, te.pl_missed.values)
    A = np.vstack([np.ones(len(tr)), btr, tr.age.values]).T
    c = np.linalg.lstsq(A, tr.pl_missed.values, rcond=None)[0]
    pred = c[0] + c[1] * bte + c[2] * te.age.values
    line("history + age", pred, te.pl_missed.values)
    print(f"      fitted age coefficient {c[2]:+.4f} games per year of age")


def test_burden_vs_spells(p, train_years, test_year) -> None:
    """Which history currency predicts best -- and is it chosen honestly?

    Same two-column discipline as test 3: every candidate is scored on an
    inner selection year first, and the held-out column is reported but not
    used to choose.  Days out and spell count are genuinely different claims --
    one long cruciate rupture against five short muscle strains -- and the
    answer decides what the shipped model carries.
    """
    print("\n  [6] TOTAL BURDEN vs NUMBER OF SPELLS -- which is the predictor?")
    inner = max(train_years)
    itr, ite = p[p.y < inner], p[p.y == inner]
    tr, te = p[p.y.isin(train_years)], p[p.y == test_year]
    w = HISTORY_WEIGHTS
    print(f"    {'history feature':<34s} {'select MAE':>11s} "
          f"{'held-out MAE':>13s} {'held-out corr':>14s}")
    for feature, label in [("days", "days out"),
                           ("spells", "number of separate spells"),
                           ("d_muscle", "days out, muscle only"),
                           ("d_joint", "days out, joint/ligament only"),
                           ("n_muscle", "muscle spell count"),
                           ("n_joint", "joint/ligament spell count")]:
        ip, _ = fit_predict(itr, ite, feature, w, PRIOR_SEASONS)
        op, _ = fit_predict(tr, te, feature, w, PRIOR_SEASONS)
        omae, ocorr, _ = metrics(op, te.pl_missed.values)
        print(f"    {label:<34s} "
              f"{np.abs(ip - ite.pl_missed.values).mean():11.2f} "
              f"{omae:13.2f} {ocorr:+14.3f}")
    # Games missed as the history currency, available only where the panel has
    # a calendar, so it is a shallower feature by construction.
    for lags, label in [(1, "league games missed, 1 season"),
                        (3, "league games missed, 3 seasons")]:
        cols = [f"plm_L{i}" for i in range(1, lags + 1)]

        def gm(a, b):
            ha, hb = a[cols].mean(axis=1), b[cols].mean(axis=1)
            m = ha.notna()
            c0, c1 = ols(ha[m], a.pl_missed.values[m.values])
            return (c0 + c1 * hb).fillna(a.pl_missed.mean()).values
        ip, op = gm(itr, ite), gm(tr, te)
        omae, ocorr, _ = metrics(op, te.pl_missed.values)
        print(f"    {label:<34s} "
              f"{np.abs(ip - ite.pl_missed.values).mean():11.2f} "
              f"{omae:13.2f} {ocorr:+14.3f}")
    print(f"    {'flat prior (league mean)':<34s} "
          f"{np.abs(itr.pl_missed.mean() - ite.pl_missed.values).mean():11.2f} "
          f"{np.abs(tr.pl_missed.mean() - te.pl_missed.values).mean():13.2f} "
          f"{'n/a':>14s}")


def test_minutes(p, train_years, test_year) -> None:
    """Does last season's playing time predict absence on top of history?

    This is not a curiosity.  The target set is 150 heavy-minutes players and
    the panel's regulars average appreciably more absence than they do, so a
    model fitted on the panel over-predicts them.  Minutes are the natural
    correction, are known before the season starts, and are not circular here
    because they are LAST season's.
    """
    print("\n  [5b] PRIOR-SEASON MINUTES -- does durability carry over?")
    inner = max(train_years)
    itr, ite = p[p.y < inner], p[p.y == inner]
    tr, te = p[p.y.isin(train_years)], p[p.y == test_year]
    print(f"    mean league games missed by last season's minutes:")
    band = pd.cut(p.minutes_L1, [-1, 1200, 1800, 2400, 3000, 3421],
                  labels=["900-1200", "1200-1800", "1800-2400", "2400-3000",
                          "3000+"])
    for k, r in p.groupby(band, observed=True).pl_missed.agg(
            ["size", "mean"]).iterrows():
        if r["size"]:
            print(f"      {str(k):<10s} n={int(r['size']):4d}  "
                  f"mean {r['mean']:5.2f}")
    print(f"    correlation of last season's minutes with games missed: "
          f"{np.corrcoef(p.minutes_L1, p.pl_missed)[0, 1]:+.3f}")

    def spec(a, b, with_min):
        mu = a[f"{FEATURE}_L1"][a.obs_L1 > 0].mean()
        ha = blend(a, FEATURE, HISTORY_WEIGHTS, PRIOR_SEASONS, mu)
        hb = blend(b, FEATURE, HISTORY_WEIGHTS, PRIOR_SEASONS, mu)
        if not with_min:
            c0, c1 = ols(ha, a.pl_missed.values)
            return c0 + c1 * hb
        A = np.vstack([np.ones(len(a)), ha, a.minutes_L1.values]).T
        c = np.linalg.lstsq(A, a.pl_missed.values, rcond=None)[0]
        return c[0] + c[1] * hb + c[2] * b.minutes_L1.values

    print(f"    {'spec':<34s} {'select MAE':>11s} {'held-out MAE':>13s} "
          f"{'held-out corr':>14s}")
    for with_min, label in [(False, f"{FEATURE} history only"),
                            (True, f"{FEATURE} history + last season minutes")]:
        ip, op = spec(itr, ite, with_min), spec(tr, te, with_min)
        omae, ocorr, _ = metrics(op, te.pl_missed.values)
        print(f"    {label:<34s} "
              f"{np.abs(ip - ite.pl_missed.values).mean():11.2f} "
              f"{omae:13.2f} {ocorr:+14.3f}")


def current_injury_games(res: pd.DataFrame, inj: pd.DataFrame) -> pd.Series:
    """League games of 2026/27 already lost to an injury he is carrying today.

    This is not a forecast and is deliberately kept OUT of
    `expected_games_missed_2627`: it is a fact about 16 August 2026 that a
    propensity model fitted on past seasons knows nothing about.  Adding the
    two together would also double-count, because the model already prices the
    average chance of being injured at any given moment.

    An expected return date comes from either of two places -- the injury
    table's `until` column for a live spell, or the "Return expected on ..."
    tooltip on the squad page -- and the table is preferred because it is the
    same row the rest of the history is read from.  Where Transfermarkt says
    the return is unknown, the cell is left empty: a zero would assert he is
    fit and any invented number would be fabrication.
    """
    fx = pd.read_csv(PROC / "fpl_fixtures_2627.csv")
    teams = pd.read_csv(PROC / "fpl_teams.csv").set_index("id").short_name
    fx["kd"] = pd.to_datetime(fx.kickoff_time, format="mixed",
                              utc=True).dt.tz_convert(None).dt.normalize()
    cal = {}
    for col in ("team_h", "team_a"):
        for tid, sub in fx.groupby(col):
            cal.setdefault(teams.get(tid), []).extend(sub.kd.tolist())
    cal = {k: np.sort(np.array(v)) for k, v in cal.items()}

    # A live spell: still running at the scrape date, with a stated return.
    live = inj[(inj["to"] > SCRAPE_DATE)].set_index("tm_id")["to"]
    live = live.groupby(level=0).max()
    tip = pd.to_datetime(res.current_injury.fillna("").str.extract(
        r"Return expected on (\d\d/\d\d/\d{4})")[0],
        format="%d/%m/%Y", errors="coerce")

    out = []
    for r, t in zip(res.itertuples(), tip):
        back = live.get(r.tm_id, pd.NaT)
        if pd.isna(back):
            back = t
        d = cal.get(r.club)
        out.append(np.nan if pd.isna(back) or d is None
                   else float((d < back).sum()))
    return pd.Series(out, index=res.index)


def predict_frame(tm_ids, feat, dobs, year=2026) -> pd.DataFrame:
    """Lagged history for a set of players as at the start of `year`."""
    p = pd.DataFrame({"tm_id": list(tm_ids), "y": year})
    p["dob"] = pd.to_datetime(pd.Series(list(tm_ids)).map(dobs).values,
                              errors="coerce")
    p["age"] = ((pd.Timestamp(f"{year}-08-01") - p.dob).dt.days / 365.25)
    cols = ["days", "spells"] + [f"d_{c}" for c in CATS] + \
           [f"n_{c}" for c in CATS]
    for lag in range(1, HIST_DEPTH + 1):
        f = feat.copy()
        f["y"] = f.y + lag
        f = f[["tm_id", "y"] + cols].rename(
            columns={c: f"{c}_L{lag}" for c in cols})
        p = p.merge(f, on=["tm_id", "y"], how="left")
        for c in cols:
            p[f"{c}_L{lag}"] = p[f"{c}_L{lag}"].fillna(0.0)
        p[f"obs_L{lag}"] = ((p.age - lag) >= 18).astype(float)
    return p


def validate(res, panel, p, inj, feat) -> None:
    train_years = [y for y in TARGET_YEARS if y < TEST_YEAR]
    # Selection is on LAST season's minutes, never this season's: filtering on
    # the target season would condition on the outcome, since the direct effect
    # of missing games through injury is having fewer minutes.
    reg = p[p.minutes_L1 >= REGULAR_MINUTES].copy()
    print("=" * 74)
    print("VALIDATION")
    print("=" * 74)

    print(f"\n  PANEL: {len(panel)} player-seasons, "
          f"{panel.tm_id.nunique()} players, seasons "
          f"{min(TARGET_YEARS)}/{str(min(TARGET_YEARS) + 1)[2:]}-"
          f"{max(TARGET_YEARS)}/{str(max(TARGET_YEARS) + 1)[2:]}")
    for name, d in (("all squad members", panel.pl_missed),
                    (f"regulars ({REGULAR_MINUTES}+ mins last season)",
                     reg.pl_missed)):
        print(f"    {name:<40s} n={len(d):5d}  mean {d.mean():5.2f}  "
              f"median {d.median():4.0f}  90th pct {d.quantile(0.9):4.0f}  "
              f"max {d.max():3.0f}")
    print(f"    zero games missed: {(panel.pl_missed == 0).mean():.1%} of all "
          f"squad members, {(reg.pl_missed == 0).mean():.1%} of regulars")
    print(f"    variance/mean among regulars = "
          f"{reg.pl_missed.var() / reg.pl_missed.mean():.2f} "
          f"(1.0 would be Poisson; this is heavily over-dispersed)")
    print(f"    Everything below is measured on the regulars, because that is "
          f"the\n    population the model gets applied to.")

    validate_conversion(res, inj)
    worked_example(panel, inj)
    test_persistence(p, reg)
    test_categories(inj, reg, feat)
    test_history_length(reg, train_years, TEST_YEAR)
    test_cured(reg)
    test_age(reg, train_years, TEST_YEAR)
    test_minutes(reg, train_years, TEST_YEAR)
    test_burden_vs_spells(reg, train_years, TEST_YEAR)
    p = reg          # the headline below is the regulars' back-test

    print("\n  HEADLINE BACK-TEST -- fitted on 2021/22-2024/25, "
          f"predicting 2025/26")
    tr, te = p[p.y.isin(train_years)], p[p.y == TEST_YEAR]
    pred, (b0, b1, mu) = fit_predict(tr, te, FEATURE, HISTORY_WEIGHTS,
                                     PRIOR_SEASONS)
    print(f"    n = {len(te)} player-seasons")
    line("model", pred, te.pl_missed.values)
    line("flat prior (league mean)",
         np.full(len(te), tr.pl_missed.mean()), te.pl_missed.values)
    print(f"    fitted map: games = {b0:.3f} + {b1:.5f} x shrunk {FEATURE}; "
          f"prior mean {mu:.2f} {FEATURE}/season, K = {PRIOR_SEASONS}")

    # The panel is every Premier League squad member, which is roughly 40 a
    # club -- far more than the 25 who actually play.  The model will be
    # applied to 150 heavy-minutes players, who are a different and much less
    # diluted population, so the back-test that matters is the one restricted
    # to them.  A model that beats the prior only by correctly predicting that
    # academy players miss nothing would be no use here.
    sub = te[te.tm_id.isin(res.tm_id)]
    if len(sub) > 20:
        s_pred, _ = fit_predict(tr, sub, FEATURE, HISTORY_WEIGHTS, PRIOR_SEASONS)
        print(f"\n    restricted to the {len(sub)} target players who were in a "
              f"2025/26 Premier League squad:")
        print(f"      their actual mean absence {sub.pl_missed.mean():.2f} "
              f"games vs {te.pl_missed.mean():.2f} across the whole panel")
        line("model", s_pred, sub.pl_missed.values)
        line("flat prior, whole-panel mean",
             np.full(len(sub), tr.pl_missed.mean()), sub.pl_missed.values)
        # This one peeks at the test season's own mean, so it is not a
        # forecast -- it is the floor a perfectly levelled flat prior could
        # reach, and the number the model has to beat to be worth anything.
        line("flat prior at their own mean (oracle)",
             np.full(len(sub), sub.pl_missed.mean()), sub.pl_missed.values)


def main() -> int:
    m = pd.read_csv(PROC / "master_2025_26.csv")
    sq, inj = load_tm()
    cal, codes = pl_calendar(), club_codes()
    print(f"transfermarkt: {sq.tm_id.nunique()} players in "
          f"{sq.season_start.nunique()} seasons of Premier League squads, "
          f"{len(inj)} injury spells")

    mins = fpl_minutes(sq, codes)
    print(f"minutes: {len(mins)} of the panel's club-season rows matched to an "
          f"FPL player by name within club; the rest never appeared in the "
          f"league that season and are treated as zero minutes")
    p, panel, feat = assemble(sq, inj, cal, codes, mins)
    targets = target_set(m)
    res = resolve(targets, sq, codes)
    # Two factors rather than one threshold. Transliteration splits the same
    # player across sources -- "Yehor Yarmoliuk" against "Yegor Yarmolyuk"
    # scores 87 -- and a name that close, in the same club squad, at the same
    # position, is not a coincidence. A bare 90 cut would drop him; a bare 85
    # cut would start accepting genuine non-matches.
    ok = (res.match_score >= 90) | ((res.match_score >= 85) & res.pos_agrees)
    print(f"resolution: {ok.sum()}/{len(res)} of the target {N_TARGET} matched "
          f"to a Transfermarkt id (score >= 90, or >= 85 with the position "
          f"agreeing); {res.pos_agrees.sum()} of all matches agree on position")
    if (~ok).any():
        print("  unmatched / weak, excluded from the output:")
        print(res[~ok][["player", "club", "position", "tm_name", "tm_pos",
                        "match_score"]].to_string(index=False))
    # Two target players collapsing onto one Transfermarkt id would silently
    # give them the same injury history, which is the failure mode a name match
    # is most likely to produce and least likely to show.
    dup = res[ok][res[ok].tm_id.duplicated(keep=False)]
    if len(dup):
        print(f"  ! {len(dup)} rows share a Transfermarkt id -- bad match:")
        print(dup[["player", "club", "tm_id", "tm_name", "match_score"]]
              .to_string(index=False))
    else:
        print("  no two target players resolved to the same Transfermarkt id")
    for route, n in res[ok].match_route.value_counts().items():
        print(f"  {n:3d} matched via {route}")
    weak = res[ok & (res.match_score < 100)]
    print(f"  {len(weak)} matched on a non-identical name string; "
          f"lowest accepted score {res[ok].match_score.min():.0f}")
    print(f"  {(~res[ok].pos_agrees).sum()} accepted matches disagree on "
          f"position (FPL and Transfermarkt classify roles differently -- "
          f"wing-backs and holding midfielders especially)")

    validate(res[ok], panel, p, inj, feat)

    # ---- fit on every regular player-season and project 2026/27 ----
    fit = p[p.minutes_L1 >= REGULAR_MINUTES]
    mu = fit[f"{FEATURE}_L1"][fit.obs_L1 > 0].mean()
    b0, b1 = ols(blend(fit, FEATURE, HISTORY_WEIGHTS, PRIOR_SEASONS, mu),
                 fit.pl_missed.values)
    dobs = dict(zip(res.tm_id, res.tm_dob))
    fr = predict_frame(res[ok].tm_id, feat, dobs)
    pred = np.clip(b0 + b1 * blend(fr, FEATURE, HISTORY_WEIGHTS,
                                   PRIOR_SEASONS, mu), 0, 38)

    career = inj.groupby("tm_id").agg(
        career_games_missed=("games_missed", "sum"),
        career_days_out=("days", "sum"), spells=("days", "size"),
        first_spell=("frm", "min"))
    out = res[ok].drop(columns=["pool"]).copy()
    out["expected_games_missed_2627"] = pred.round(2)
    out["current_injury_games"] = current_injury_games(out, inj).values
    out["seasons_of_history"] = fr[[f"obs_L{i}" for i in
                                    range(1, HIST_DEPTH + 1)]].sum(axis=1).values
    out = out.merge(career, left_on="tm_id", right_index=True, how="left")
    for c in ["career_games_missed", "career_days_out", "spells"]:
        out[c] = out[c].fillna(0).astype(int)
    for lag in (1, 2, 3):
        out[f"days_out_L{lag}"] = fr[f"days_L{lag}"].values
    for cat in CATS:
        out[f"days_{cat}_L1_3"] = (fr[f"d_{cat}_L1"] + fr[f"d_{cat}_L2"]
                                   + fr[f"d_{cat}_L3"]).values
    out = out.sort_values("expected_games_missed_2627", ascending=False)
    out.to_csv(PROC / "injury_model.csv", index=False)

    print(f"\ninjury_model.csv  {len(out)} players")
    print(f"  mean expected league games missed: "
          f"{out.expected_games_missed_2627.mean():.2f}  "
          f"(panel mean {panel.pl_missed.mean():.2f}); range "
          f"{out.expected_games_missed_2627.min():.2f}-"
          f"{out.expected_games_missed_2627.max():.2f}")
    cur = out[out.current_injury.notna()]
    print(f"  {len(cur)} of them are carrying an injury right now per "
          f"Transfermarkt's squad page; those are a known present fact, not a "
          f"model output, and are reported in `current_injury` rather than "
          f"folded into the projection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
