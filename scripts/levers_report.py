"""One page showing every lever that moves a player's projection.

The workbook has 60-odd columns and Excel makes you scroll past most of them to
compare two rows.  This is the same data, filtered down to the inputs that
actually move a number, in one sortable table -- plus the two things the
research knows that no column in the workbook carries: whether a player is
nailed in his side, and whether his club is actively shopping for someone to
take his place.

It renders, it does not decide.  There is no ranking here the model did not
already produce, and no column that says who to take.  The sidebar tray is
empty until you put something in it, and what goes in is your call.

Two places the data does not reach as far as you would want, both surfaced on
the page rather than papered over:

* FPL publishes one combined order for *corners and indirect free kicks*, so a
  corner taker and an indirect free-kick taker cannot be told apart.
* Corner volume is measured, so corner duty can be a percentage.  Nothing in
  any source counts free kicks taken, so free-kick duty can only ever be an
  order.  Penalties likewise -- but there the order is the useful thing.

Output: output/draft_levers.html
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata

import numpy as np
import pandas as pd

import xpts_calc
import xpts_model
from common import PROC, OUT, strip_accents

RESEARCH = PROC / "club_research"

# Share of his club's corners above which a player is treated as taking both
# sides.  A club takes roughly 200 corners a season; the first-choice takers
# split into a group around 90-110 and a group around 45-70.
BOTH_SIDES_SHARE = 0.40
ONE_SIDE_SHARE = 0.15

ORDER_LABEL = {1: "Primary", 2: "Secondary", 3: "Tertiary"}
ROLE_RANK = {"nailed": 0, "likely": 1, "contested": 2, "unknown": 3}

# Names the research uses for a player who does not exist yet, or who has
# already gone.  Matching these against the FPL list is not a failure.
PLACEHOLDER = re.compile(
    r"^(unknown|none|tbc|n/?a|possible late signing|an? unsigned \w+|"
    r"late signing|new signing|no one|nobody)$", re.I)


def norm(s: str) -> str:
    """Accent-stripped, punctuation-free lower case, for name matching.

    The research was written by agents who typed 'Joao Gomes' and 'Jurrien
    Timber'.  The FPL list has 'Joao Gomes' with a tilde and 'Jurrien' with a
    diaeresis.  Nothing joins without this.
    """
    s = strip_accents(s)
    s = re.sub(r"\(.*?\)", " ", s)          # "(deal agreed, unsigned)"
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


def split_names(value: str) -> list[str]:
    parts = re.split(r"\s*[/,]\s*|\s+and\s+", value or "")
    return [p.strip() for p in parts if p and p.strip()]


# --------------------------------------------------------------------------
# Role and transfer threat, out of the club research
# --------------------------------------------------------------------------
def research_roles(m: pd.DataFrame) -> pd.DataFrame:
    """Who is nailed, and whose position his club is still shopping for.

    Both live in the research as names, not codes -- `player_deltas` carries a
    code but `positions` and `reinforcement_risk` do not -- so this is the one
    join in the repo that has to go through a name.  Unmatched names are
    counted and reported rather than dropped quietly.
    """
    lookup: dict[tuple[str, str], int] = {}
    loose: dict[str, int] = {}
    for code, player, t27, t25 in m[["code", "player", "team_2627",
                                     "club_2526"]].itertuples(index=False):
        key = norm(player)
        loose.setdefault(key, code)
        for club in (t27, t25):
            if isinstance(club, str):
                lookup.setdefault((club, key), code)

    def resolve(club: str, name: str) -> int | None:
        key = norm(name)
        if not key or PLACEHOLDER.match(name.strip()):
            return None
        return lookup.get((club, key)) or loose.get(key)

    rows: dict[int, dict] = {}
    unmatched: list[tuple[str, str]] = []

    for path in sorted(RESEARCH.glob("*.json")):
        club = json.loads(path.read_text()).get("club", path.stem)
        doc = json.loads(path.read_text())

        for p in doc.get("positions", []):
            conf = (p.get("confidence") or "unknown").lower()
            slot, note = p.get("position", ""), p.get("note", "")
            starter = p.get("starter") or ""
            for name, is_starter in ([(starter, True)] +
                                     [(c, False) for c in p.get("competition") or []]):
                code = resolve(club, name)
                if code is None:
                    if name and not PLACEHOLDER.match(name.strip()):
                        unmatched.append((club, name))
                    continue
                # A starter carries the slot's confidence.  Someone listed as
                # competition is by definition not the starter, so the best he
                # can be called is rotation -- unless the slot is contested,
                # in which case he is genuinely in the fight.
                role = conf if is_starter else (
                    "contested" if conf == "contested" else "rotation")
                prev = rows.get(code, {}).get("role")
                if prev is None or ROLE_RANK.get(role, 9) < ROLE_RANK.get(prev, 9):
                    rows.setdefault(code, {}).update(
                        role=role, role_slot=slot, role_note=note,
                        role_starter=starter)

        for rr in doc.get("reinforcement_risk", []):
            slot = rr.get("position", "") or ""
            kind = "exit" if slot.lower().startswith("outgoing") else "reinforcement"
            for name in split_names(rr.get("incumbent", "")):
                code = resolve(club, name)
                if code is None:
                    if not PLACEHOLDER.match(name.strip()):
                        unmatched.append((club, name))
                    continue
                r = rows.setdefault(code, {})
                # A club shopping for a replacement threatens more minutes than
                # a club willing to sell, so reinforcement wins the label.
                if r.get("threat") != "reinforcement":
                    r["threat"] = kind
                r["threat_slot"] = slot
                r["threat_note"] = rr.get("note", "")
                r["threat_url"] = rr.get("source_url", "")

    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "code"
    if unmatched:
        seen = sorted(set(unmatched))
        print(f"  {len(seen)} research name(s) with no FPL entry "
              f"(left the league, or never arrived):")
        for club, name in seen[:6]:
            print(f"    {club}: {name}")
        if len(seen) > 6:
            print(f"    ... and {len(seen) - 6} more")
    return out.reset_index()


# --------------------------------------------------------------------------
# Published draft rankings from named analysts
# --------------------------------------------------------------------------
def pro_rankings(m: pd.DataFrame) -> pd.Series:
    """Mean published rank per player, across whichever analysts were found.

    Deliberately averages the rank rather than scoring it: an analyst who ranks
    only a top 30 says nothing about number 31, and inventing a number for him
    would be worse than leaving it blank.  A player ranked by one source out of
    four gets that one source's rank, and the count is what tells you how much
    to trust it.

    Returns an all-NaN column if nothing has been researched yet, which is a
    real answer -- draft-specific rankings are thin this early -- and better
    than a fabricated one.
    """
    # The verified file only.  rankings.csv is what an agent reported; the
    # verified one is what survived re-fetching every source, and the gap
    # between the two is the whole reason verify_pro_rankings.py exists.
    path = PROC / "pro_rankings" / "rankings_verified.csv"
    blank = pd.Series(np.nan, index=m.index, dtype=float)
    if not path.exists():
        print("  no rankings_verified.csv -- run verify_pro_rankings.py; "
              "ADR Pros left empty")
        return blank

    r = pd.read_csv(path)
    if r.empty or "player_name" not in r.columns:
        print("  rankings_verified.csv is empty -- ADR Pros left empty")
        return blank
    if "verified" in r.columns:
        dropped = int((~r["verified"].astype(bool)).sum())
        r = r[r["verified"].astype(bool)]
        if dropped:
            print(f"  {dropped} unverified ranking row(s) excluded")

    # Published lists name players three different ways: in full ("Alisson
    # Becker"), by surname alone ("Gross"), and abbreviated ("A.Becker").  So
    # match on the full name first, then fall back to the surname -- but only
    # where that surname belongs to exactly one player, since "Adams" and
    # "Gabriel" do not identify anybody on their own.
    by_full: dict[str, int] = {}
    surnames: dict[str, set[int]] = {}
    for code, player in m[["code", "player"]].itertuples(index=False):
        key = norm(player)
        by_full.setdefault(key, code)
        parts = key.split()
        if parts:
            surnames.setdefault(parts[-1], set()).add(code)
            if len(parts) > 1:                    # "A.Becker" -> "a becker"
                surnames.setdefault(f"{parts[0][0]} {parts[-1]}", set()).add(code)
            if len(parts) > 2:
                # A hyphenated surname loses its first half to the last-token
                # rule -- "Gibbs-White" would collide with Ben White.
                surnames.setdefault(" ".join(parts[-2:]), set()).add(code)

    def resolve(name: str) -> int | None:
        key = norm(name)
        if key in by_full:
            return by_full[key]
        for k in (key, " ".join(key.split()[-1:])):
            hits = surnames.get(k)
            if hits and len(hits) == 1:
                return next(iter(hits))
        return None

    r["code"] = r["player_name"].map(resolve)
    missed = r[r["code"].isna()]
    r = r.dropna(subset=["code"])
    pos_of = dict(m[["code", "position"]].itertuples(index=False))
    r["pos"] = r["code"].map(pos_of)

    # Some sources rank a whole draft; others rank one position at a time.  The
    # Premier League's own Scout publishes four separate lists, so its #1s are
    # Raya, Gabriel, Bruno Fernandes and Haaland -- four different players all
    # called "1".  Averaging those against a global top 100 makes the best
    # goalkeeper look like the best player alive, which is how Reece James came
    # out rated 1.8 and the correlation with real draft behaviour came out at
    # 0.075.  So classify each list by whether its players share a position,
    # and never mix the two scales.
    positional, glob = [], []
    for name, g in r.groupby("source_name"):
        share = g["pos"].value_counts(normalize=True).max() if g["pos"].notna().any() else 0
        (positional if share >= 0.9 else glob).append(name)

    g_rows = r[r["source_name"].isin(glob)]
    agg = g_rows.groupby("code")["rank"].mean()

    print(f"  pro rankings: {len(glob)} whole-draft list(s), "
          f"{len(positional)} positional list(s) excluded from the mean")
    print(f"    {len(g_rows)} usable rows, {agg.size} players matched, "
          f"{len(missed)} names unmatched")
    if len(missed):
        print("    unmatched:", ", ".join(sorted(missed["player_name"].unique())[:8]))

    # Keyed by player code, never by row position.  Assigning this by index
    # was the bug it replaces: `out` picks up a fresh RangeIndex from the
    # earlier merge, so aligning on index quietly handed each player somebody
    # else's rank -- which is what put a mean rank of 2.0 on a man one list
    # had at 51.
    return agg


# --------------------------------------------------------------------------
# Set pieces
# --------------------------------------------------------------------------
def set_pieces(m: pd.DataFrame) -> pd.DataFrame:
    corner_order = m["corners_and_indirect_freekicks_order"]
    fk_order = m["direct_freekicks_order"]
    taken = m["pl_corner_taken"].fillna(0)

    club_corners = taken.groupby(m["club_2526"]).transform("sum")
    share = pd.Series(
        np.where(club_corners > 0, taken / club_corners.replace(0, np.nan), np.nan),
        index=m.index)

    first, second = corner_order == 1, corner_order == 2
    corner_pts = pd.Series(0.0, index=m.index)
    corner_pts[first] = 2.0
    corner_pts[first & (share >= BOTH_SIDES_SHARE)] = 4.0
    corner_pts[second & (share >= ONE_SIDE_SHARE)] = 1.0
    indirect_pts = np.where(first, 1.0, 0.0)
    direct_pts = np.where(fk_order == 1, 1.0, 0.0)

    return pd.DataFrame({
        "corner_order": corner_order,
        "corner_role": corner_order.map(ORDER_LABEL).fillna(""),
        "corner_share": (share * 100).round(1),
        "corners_taken": taken.where(taken > 0),
        "fk_order": fk_order,
        "fk_role": fk_order.map(ORDER_LABEL).fillna(""),
        "pen_order": m["penalties_order"],
        "pen_role": m["penalties_order"].map(ORDER_LABEL).fillna(""),
        "sp_score": corner_pts + direct_pts + indirect_pts,
    })


def assemble() -> pd.DataFrame:
    d = xpts_model.build_rows()
    s = xpts_calc.score(d)
    m = pd.read_csv(PROC / "master_2025_26.csv").loc[d.index]

    n90 = d["xMins_input"].fillna(0) / 90
    out = pd.DataFrame({
        "code": m["code"],
        "player": d["player"],
        "team": d["team"].fillna("--"),
        "pos": d["pos"].fillna("--"),
        "price": d["price"],
        "draftable": d["draftable"].fillna(False),
        "moved": d["moved_club"].fillna(False),
        "club_2526": d["club_2526"].fillna(""),

        "xmins_solio": d["solio_season_xmins"],
        "xmins_adj": d["xmins_adjusted"],
        "research_delta": d["research_delta"].where(d["research_xmins"].notna()),
        "confidence": d["research_confidence"].fillna(""),
        "reason": d["research_reason"].fillna(""),
        "mins_per_start": d["mins_per_start"],

        "xg_season": (d["xG_p90"].fillna(0) * n90).round(1),
        "xa_season": (d["xA_p90"].fillna(0) * n90).round(1),
        "pens": d["pens_season"].round(6),
        "defcon_hit": (s["defcon_hit"] * 100).round(1),
        # Full precision, not display precision: the browser re-scores every
        # row and verify_levers compares the two.  Rounding here would
        # hide a real difference inside the rounding step.
        "xpts": s["xpts_season"].round(6),
        "adp": m["adp"],

        # Which of a player's rates moved between the halves of last season by
        # more than the noise in them.  Not applied to anything: recency
        # weighting loses out of sample on seven of nine folds, so what ships
        # is the flag, which is a prompt to go and find out why -- the thing
        # only a human can do.
        "rec_z_xg": m.get("recency_z_xg"),
        "rec_z_xa": m.get("recency_z_xa"),
        "rec_z_dc": m.get("recency_z_dc"),
        "rec_flag": m.get("recency_flag", pd.Series(False, index=m.index)).fillna(False),
        "rec_xg_early": m.get("xg_early_p90"),
        "rec_xg_late": m.get("xg_late_p90"),
        "rec_xa_early": m.get("xa_early_p90"),
        "rec_xa_late": m.get("xa_late_p90"),
        "rec_dc_early": m.get("dc_early_p90"),
        "rec_dc_late": m.get("dc_late_p90"),

        # Everything the page needs to re-score a player in the browser when
        # you edit his minutes.  These four are the only parts of xpts_calc
        # that do not depend on xMins or minutes-per-start, so shipping them
        # lets the page reproduce the model exactly rather than approximate it.
        # verify_levers() checks that it does.
        "rate_p90": s["rate_pts_p90"].round(6),
        # The raw ingredients too, so the browser can rebuild the rate from
        # scratch.  Needed because position is editable: the shipped rate is
        # priced at the position the model had, and changing DEF to MID has to
        # re-price goals, clean sheets, saves and goals conceded, not just the
        # appearance and DefCon terms.
        "pcs": d["pcs_input"].fillna(0).round(6),
        # Bonus is won per match, so what ships is one simulated match plus
        # the three slopes that re-price it when xG, xA or P(CS) are edited.
        "bonus_pm": d["bonus_per_match"].fillna(0).round(6),
        "bonus_d_xg": d["bonus_d_xg"].fillna(0).round(6),
        "bonus_d_xa": d["bonus_d_xa"].fillna(0).round(6),
        "bonus_d_pcs": d["bonus_d_pcs"].fillna(0).round(6),
        "bonus_xg_base": d["bonus_xg_base"].fillna(0).round(6),
        "bonus_xa_base": d["bonus_xa_base"].fillna(0).round(6),
        "bonus_pcs_base": d["bonus_pcs_base"].fillna(0).round(6),
        "base_bps_p90": d["base_bps_p90"].fillna(0).round(3),
        "saves_per_match": d["saves_per_match"].fillna(0).round(6),
        "ga_per_match": d["ga_per_match"].fillna(0).round(6),
        "card_pts_p90": d["card_pts_p90"].fillna(0).round(6),
        "pen_pts": s["pen_pts_season"].round(6),
        "pen_save_pts": d["pen_save_pts"].fillna(0).round(6),
        "defcon_lambda": d["defcon_lambda"].fillna(0).round(6),
        "xg_p90": d["xG_p90"].fillna(0).round(6),
        "xa_p90": d["xA_p90"].fillna(0).round(6),
        # The editable twins.  They start life equal to the originals, which
        # stay put in their own column so a change is always reversible by
        # reading across rather than by remembering.
        "xg_p90_adj": d["xG_p90"].fillna(0).round(6),
        "xa_p90_adj": d["xA_p90"].fillna(0).round(6),
    }).join(set_pieces(m))

    # "No rates" means the data cannot supply an attacking rate, not that the
    # rate happens to be zero.  Keying it on xG and xA both being zero caught
    # every goalkeeper in the league, who has no xG for the excellent reason
    # that he is a goalkeeper.  The real test is whether he has a Premier
    # League record at all.
    no_record = d["mins_2526"].isna() | d["mins_2526"].fillna(0).le(0)
    blank_rates = (d["xG_p90"].fillna(0).eq(0) & d["xA_p90"].fillna(0).eq(0)
                   & d["pos"].ne("GKP"))
    out["no_pl_rates"] = (no_record | blank_rates) & out["xmins_adj"].fillna(0).gt(500)

    roles = research_roles(m)
    out = out.merge(roles, on="code", how="left")
    out["role"] = out["role"].fillna("unknown")
    out["threat"] = out["threat"].fillna("none")
    for c in ("role_slot", "role_note", "role_starter",
              "threat_slot", "threat_note", "threat_url"):
        out[c] = out.get(c, pd.Series("", index=out.index)).fillna("")
    out["role_rank"] = out["role"].map(
        {**ROLE_RANK, "rotation": 2.5}).fillna(3).astype(float)

    inj = PROC / "injury_model.csv"
    if inj.exists():
        g = pd.read_csv(inj)[["code", "expected_games_missed_2627"]]
        out = out.merge(g, on="code", how="left").rename(
            columns={"expected_games_missed_2627": "games_missed"})
    else:
        out["games_missed"] = np.nan

    out["adr_pros"] = out["code"].map(pro_rankings(m)).round(1)

    out = out[out["draftable"] | out["xmins_adj"].fillna(0).gt(0)]

    levels = xpts_calc.replacement_levels(
        s["xpts_season"], d["pos"], d["draftable"].fillna(False),
        xpts_model.LEAGUE_TEAMS, xpts_model.SQUAD_SLOTS)
    out["vorp"] = (out["xpts"] - out["pos"].map(levels)).round(6)
    return out.sort_values("xpts", ascending=False, na_position="last")


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------
CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ground:#F4F5F3; --surface:#FFFFFF; --raised:#FAFBF9;
  --ink:#14181A; --muted:#666E6C; --faint:#8D958F;
  --line:#E0E3DF; --line-soft:#EDEFEB;
  --accent:#1F5F4F; --accent-soft:#E3EEE9;
  --up:#2E7D64; --up-soft:#E2EFE8;
  --down:#A8542F; --down-soft:#F6E7DD;
  --warn:#8A6A18; --warn-soft:#F5EEDA;
  --mark:#7A4A86; --mark-soft:#F0E6F3;
  --input:#EFF4F8; --editd:#DCEAF6; --bandc:#1F5F4F;
  --stripe:#EDF1EE; --moved:#A8542F;
  --shadow:0 1px 2px rgba(20,24,26,.06),0 8px 24px -12px rgba(20,24,26,.18);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0F1312; --surface:#161B19; --raised:#1B211E;
    --ink:#E7EBE7; --muted:#98A19C; --faint:#727C77;
    --line:#252C29; --line-soft:#1E2422;
    --accent:#63C0A3; --accent-soft:#17302A;
    --up:#5FC098; --up-soft:#152C25;
    --down:#DC9165; --down-soft:#2E1F16;
    --warn:#D2AE55; --warn-soft:#2A2415;
    --mark:#C39BD0; --mark-soft:#2A1F2E;
    --input:#1C2429; --editd:#243642; --bandc:#63C0A3;
    --stripe:#1A211E; --moved:#DC9165;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 28px -14px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --ground:#0F1312; --surface:#161B19; --raised:#1B211E;
  --ink:#E7EBE7; --muted:#98A19C; --faint:#727C77;
  --line:#252C29; --line-soft:#1E2422;
  --accent:#63C0A3; --accent-soft:#17302A;
  --up:#5FC098; --up-soft:#152C25;
  --down:#DC9165; --down-soft:#2E1F16;
  --warn:#D2AE55; --warn-soft:#2A2415;
  --mark:#C39BD0; --mark-soft:#2A1F2E;
  --input:#1C2429; --editd:#243642; --bandc:#63C0A3;
  --stripe:#1A211E; --moved:#DC9165;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 10px 28px -14px rgba(0,0,0,.7);
}

body{margin:0; background:var(--ground); color:var(--ink);
  font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased}
.wrap{max-width:1780px; margin:0 auto; padding:28px 20px 56px;
      display:flex; flex-direction:column; gap:18px}

header{display:flex; flex-direction:column; gap:6px}
h1{margin:0; font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
   font-size:clamp(25px,3.2vw,36px); font-weight:600; letter-spacing:-.015em;
   text-wrap:balance}
.sub{margin:0; color:var(--muted); max-width:70ch}
.stamp{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; font-size:11px;
       letter-spacing:.08em; text-transform:uppercase; color:var(--faint)}

.controls{display:flex; flex-wrap:wrap; gap:9px; align-items:center;
  padding:13px; background:var(--surface); border:1px solid var(--line);
  border-radius:10px; box-shadow:var(--shadow)}
input[type=search],select{font:inherit; color:var(--ink); background:var(--raised);
  border:1px solid var(--line); border-radius:7px; padding:7px 10px}
input[type=search]{min-width:200px; flex:1 1 200px}
input[type=search]::placeholder{color:var(--faint)}
:focus-visible{outline:2px solid var(--accent); outline-offset:2px}

.chips{display:flex; gap:6px; flex-wrap:wrap}
.chip{font:inherit; font-size:12.5px; cursor:pointer; user-select:none;
  padding:6px 11px; border-radius:999px; border:1px solid var(--line);
  background:var(--raised); color:var(--muted)}
.chip[aria-pressed=true]{background:var(--accent-soft); border-color:var(--accent);
  color:var(--accent); font-weight:600}
.chip.markchip[aria-pressed=true]{background:var(--mark-soft);
  border-color:var(--mark); color:var(--mark)}
.count{margin-left:auto; color:var(--muted);
  font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12.5px}

.layout{display:flex; gap:16px; align-items:flex-start}
.layout.no-side .side{display:none}
.main{flex:1 1 auto; min-width:0}

.tablebox{background:var(--surface); border:1px solid var(--line);
  border-radius:10px; box-shadow:var(--shadow); overflow:auto; max-height:72vh}
table{border-collapse:separate; border-spacing:0; width:100%; font-size:13px}
thead th{position:sticky; top:0; z-index:3; background:var(--raised);
  border-bottom:1px solid var(--line); padding:0}
.grouprow th{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:10px;
  letter-spacing:.1em; text-transform:uppercase; color:var(--faint);
  font-weight:600; padding:8px 10px 4px; text-align:left;
  border-bottom:1px solid var(--line-soft); top:0}
.grouprow th.g{border-left:1px solid var(--line-soft)}
.headrow th{top:27px; z-index:4}
.headrow button{font:inherit; font-size:11.5px; font-weight:600;
  width:100%; text-align:left; background:none; border:0; color:var(--muted);
  padding:7px 9px 9px; cursor:pointer; white-space:nowrap}
.headrow th.num button{text-align:right}
.headrow button:hover{color:var(--ink)}
.headrow button[data-dir]{color:var(--accent)}
.headrow button[data-dir]::after{content:" \\25BE"}
.headrow button[data-dir=asc]::after{content:" \\25B4"}

tbody td{padding:5px 9px; border-bottom:1px solid var(--line-soft); white-space:nowrap}
tbody tr:hover td{background:var(--raised)}
tbody tr.marked td{background:var(--mark-soft)}
td.num,th.num{text-align:right; font-variant-numeric:tabular-nums;
  font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12.5px}
/* Two frozen columns: the star and the name.  The header cells freeze with
   them, or scrolling right leaves the body showing names under a header that
   has moved on to Mins/start. */
td.mark,th.mark{position:sticky; left:0; z-index:2; background:var(--surface);
  width:40px; min-width:40px; padding-left:6px; padding-right:4px}
/* Capped, because a wide name column pushes every number off the screen.  The
   full name is on the cell's title. */
td.name,th.pname{position:sticky; left:40px; z-index:2; background:var(--surface);
  font-weight:550; border-right:1px solid var(--line); cursor:grab;
  max-width:158px; overflow:hidden; text-overflow:ellipsis}
thead th.mark,thead th.pname{z-index:6; background:var(--raised)}
tbody tr:hover td.name,tbody tr:hover td.mark{background:var(--raised)}
tbody tr.marked td.name,tbody tr.marked td.mark{background:var(--mark-soft)}
td.dim{color:var(--faint)}

/* The row's rank doubles as the research toggle: outlined by default, filled
   once you click it.  One control, and the number is useful on its own. */
.ranknum{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:10.5px;
  line-height:1; cursor:pointer; min-width:26px; padding:4px 3px;
  border:1px solid var(--line); border-radius:6px; background:transparent;
  color:var(--faint); font-variant-numeric:tabular-nums; text-align:center}
.ranknum:hover{border-color:var(--mark); color:var(--mark)}
.ranknum.on{background:var(--mark); border-color:var(--mark); color:#fff;
  font-weight:700}
.hasnote{color:var(--mark); font-size:13px; line-height:1; margin-left:1px}
textarea.note{width:100%; margin-top:5px; font:inherit; font-size:11.5px;
  color:var(--ink); background:var(--input); border:1px solid var(--line);
  border-radius:6px; padding:5px 6px; resize:vertical}
textarea.note::placeholder{color:var(--faint)}
textarea.note:focus{border-color:var(--accent); background:var(--surface)}

.pos{display:inline-block; min-width:34px; text-align:center; padding:1px 6px;
  border-radius:5px; font-size:11px; font-weight:700; letter-spacing:.04em;
  background:var(--accent-soft); color:var(--accent)}
.tag{display:inline-block; padding:1px 7px; border-radius:5px; font-size:11px;
  font-weight:650; letter-spacing:.02em}
.tag.nailed{background:var(--up-soft); color:var(--up)}
.tag.likely{background:var(--accent-soft); color:var(--accent)}
.tag.contested{background:var(--warn-soft); color:var(--warn)}
.tag.rotation{background:var(--warn-soft); color:var(--warn)}
.tag.unknown{background:transparent; color:var(--faint); font-weight:400}
.tag.reinforcement{background:var(--down-soft); color:var(--down)}
.tag.exit{background:var(--warn-soft); color:var(--warn)}
.tag.none{background:transparent; color:var(--faint); font-weight:400}
.delta{font-weight:650}
.delta.up{color:var(--up)} .delta.down{color:var(--down)}
.conf{font-size:10.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--faint)}
.flag{display:inline-block; margin-left:5px; font-size:10px; font-weight:700;
  padding:1px 5px; border-radius:4px; letter-spacing:.04em}
.flag.moved{background:var(--warn-soft); color:var(--warn)}
.flag.norates{background:var(--down-soft); color:var(--down)}
.dot{color:var(--faint)}

.side{flex:0 0 320px; display:flex; flex-direction:column; gap:14px;
  position:sticky; top:16px; max-height:calc(100vh - 32px); overflow:auto}
.panel{background:var(--surface); border:1px solid var(--line);
  border-radius:10px; box-shadow:var(--shadow); overflow:hidden}
.panel h2{margin:0; padding:11px 13px 9px; font-size:12px; letter-spacing:.06em;
  text-transform:uppercase; color:var(--faint);
  font-family:ui-monospace,"SF Mono",Menlo,monospace;
  border-bottom:1px solid var(--line-soft);
  display:flex; align-items:center; gap:8px}
.panel h2 .n{margin-left:auto; color:var(--muted)}
.panel .body{padding:9px; display:flex; flex-direction:column; gap:6px;
  max-height:42vh; overflow:auto}
.panel .hint{padding:0 13px 11px; color:var(--faint); font-size:11.5px}

.card{border:1px solid var(--line); border-radius:8px; padding:7px 9px;
  background:var(--raised); cursor:grab; display:flex; flex-direction:column; gap:2px}
.card:active{cursor:grabbing}
.card.drag{opacity:.4}
.card .top{display:flex; align-items:baseline; gap:6px}
.card .who{font-weight:600; font-size:12.5px}
.card .meta{margin-left:auto; font-size:11px; color:var(--faint);
  font-family:ui-monospace,"SF Mono",Menlo,monospace}
.card .row2{font-size:11px; color:var(--muted);
  font-family:ui-monospace,"SF Mono",Menlo,monospace}
.card .row3{display:flex; flex-wrap:wrap; gap:4px; align-items:center; margin-top:1px}
.card .rate{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:10.5px;
  color:var(--muted); background:var(--ground); border:1px solid var(--line-soft);
  padding:0 5px; border-radius:4px}
.card .tag{font-size:10px; padding:0 5px}
.clubgrp{display:flex; flex-direction:column; gap:5px; margin-bottom:11px}
.clubgrp h3{margin:0; font-size:10.5px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--accent); font-family:ui-monospace,"SF Mono",Menlo,monospace;
  display:flex; align-items:center; gap:6px;
  border-bottom:1px solid var(--line-soft); padding-bottom:3px}
.clubgrp h3 span{margin-left:auto; color:var(--faint)}
.card .x{margin-left:6px; cursor:pointer; color:var(--faint); border:0;
  background:none; font:inherit; padding:0 2px}
.card .x:hover{color:var(--down)}

.tray{min-height:90px}
.tray.over{background:var(--accent-soft); outline:2px dashed var(--accent);
  outline-offset:-4px}
.tray .empty{color:var(--faint); font-size:12px; text-align:center; padding:22px 8px}
.tray .card{counter-increment:slot}
.tray .card .who::before{content:counter(slot) ". "; color:var(--faint);
  font-family:ui-monospace,Menlo,monospace; font-weight:400}
.tray .body{counter-reset:slot}
.act{display:flex; gap:6px; padding:0 9px 10px}
.act button{font:inherit; font-size:11.5px; padding:5px 9px; border-radius:6px;
  border:1px solid var(--line); background:var(--raised); color:var(--muted);
  cursor:pointer}
.act button:hover{color:var(--ink); border-color:var(--muted)}

/* Editable cells. Blue, matching the workbook's own convention for a cell
   you are meant to type in. */
.edit{background:none; border:0; font:inherit; color:inherit; width:100%;
  padding:2px 4px; border-radius:4px; text-align:right;
  font-variant-numeric:tabular-nums;
  font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12.5px;
  background:var(--input); border:1px solid transparent}
.edit:hover{border-color:var(--line)}
.edit:focus{border-color:var(--accent); background:var(--surface)}
/* The column sizes itself to the header, and "Role" is shorter than "nailed",
   so without a floor the control clips to "ne". */
select.edit{text-align:left; font-family:inherit; font-size:12px;
  min-width:104px; width:auto}
input.edit.txt{text-align:left; font-family:inherit; min-width:54px; width:54px}
input.edit{min-width:74px}
td.edited{position:relative}
td.edited .edit{background:var(--editd); font-weight:650; padding-right:15px}
/* One click puts a single field back, without touching the rest of the sheet. */
.undo{position:absolute; right:2px; top:50%; transform:translateY(-50%);
  border:0; background:none; cursor:pointer; padding:0 2px; line-height:1;
  font-size:11px; color:var(--muted)}
.undo:hover{color:var(--down)}
.legend{display:flex; flex-wrap:wrap; gap:14px; align-items:center;
  color:var(--faint); font-size:11.5px; padding:0 2px}
.legend b{font-weight:650; color:var(--muted)}
.swatch{display:inline-block; width:11px; height:11px; border-radius:3px;
  vertical-align:-1px; margin-right:4px; border:1px solid var(--line)}

/* Draft bands: which of my picks a player is still projected to be there for.
   One hue, stepped by lightness, so the bands read as a sequence rather than
   as six unrelated categories. */
tr.band td.name{box-shadow:inset 3px 0 0 var(--bandc)}
tr.gone td.name{box-shadow:inset 3px 0 0 var(--faint)}
.bandtag{font-family:ui-monospace,Menlo,monospace; font-size:11px;
  padding:1px 6px; border-radius:5px; background:var(--accent-soft);
  color:var(--accent); font-weight:650}
.bandtag.gone{background:transparent; color:var(--faint); font-weight:400}
.picks{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11.5px;
  color:var(--muted)}
.picks b{color:var(--accent)}

details{background:var(--surface); border:1px solid var(--line);
  border-radius:10px; padding:14px 16px; box-shadow:var(--shadow)}
summary{cursor:pointer; font-weight:600}
details p{color:var(--muted); max-width:80ch}
details h3{font-size:13px; margin:18px 0 6px}
code{font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:12px;
  background:var(--raised); padding:1px 5px; border-radius:4px}
footer{color:var(--faint); font-size:12px}
@media (max-width:1100px){.layout{flex-direction:column}
  .side{position:static; flex:1 1 auto; width:100%; max-height:none}}

/* The rows that land on your picks in a snake -- 4, 13, 20, 29 for slot 4 of
   8.  Not every fourth row: the turn reverses each round, so the gap alternates
   between 9 and 7.  It follows whatever order the table is currently in, which
   is the point -- sort it your way and it shows where your picks fall. */
.striped tbody tr.mypick td{background:var(--stripe)}
.striped tbody tr.mypick td.name,
.striped tbody tr.mypick td.mark{background:var(--stripe)}
.striped tbody tr.mypick td.name{box-shadow:inset 3px 0 0 var(--accent)}
.striped tbody tr.mypick:hover td{background:var(--raised)}
tbody tr.marked td,tbody tr.marked td.name,tbody tr.marked td.mark{background:var(--mark-soft)}

/* A player who changed club: his xG and xA belong to a different team. */
.movedname{color:var(--moved)}
td.name.movedname{color:var(--moved)}

#headrow th[draggable]{cursor:grab}
#headrow th.coldrag{opacity:.45}
#headrow th.colover{box-shadow:inset 3px 0 0 var(--accent)}

details h3{font-size:13px; margin:18px 0 6px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

# (key, label, numeric, group, editable-kind)
COLS = [
    ("mark", "#", False, "", None),
    ("player", "Player", False, "Player", None),
    ("team", "Team", False, "Player", None),
    ("pos", "Pos", False, "Player", None),
    ("role", "Role", False, "Role", "select"),
    ("threat", "Transfer threat", False, "Role", "select"),
    ("xmins_solio", "xMins Solio", True, "Minutes", None),
    ("xmins_adj", "xMins adjusted", True, "Minutes", "num0"),
    ("research_delta", "Delta", True, "Minutes", None),
    ("confidence", "Conf", False, "Minutes", None),
    ("mins_per_start", "Mins/start", True, "Minutes", "num1"),
    ("matches", "Matches", True, "Minutes", None),
    ("xg_p90", "xG/90", True, "Attacking", None),
    ("xg_p90_adj", "xG/90 mine", True, "Attacking", "num3"),
    ("xg_season", "xG season", True, "Attacking", None),
    ("xa_p90", "xA/90", True, "Attacking", None),
    ("xa_p90_adj", "xA/90 mine", True, "Attacking", "num3"),
    ("xa_season", "xA season", True, "Attacking", None),
    ("corner_share", "Corners", True, "Set pieces", None),
    ("corner_role", "Cnr role", False, "Set pieces", None),
    ("fk_role", "Direct FK", False, "Set pieces", None),
    ("pen_role", "Pens", False, "Set pieces", None),
    ("pens", "Pens exp", True, "Set pieces", None),
    ("defcon_hit", "DefCon %", True, "Other levers", None),
    ("base_bps_p90", "Base BPS/90", True, "Other levers", None),
    ("rec_z", "Late vs early", True, "Other levers", None),
    ("bonus_pm", "Bonus/match", True, "Other levers", None),
    ("adr_pros", "ADR Pros", True, "Other levers", None),
    ("adp", "ADP", True, "Draft", "num1"),
    ("band", "There at", True, "Draft", None),
    ("xpts", "xPts", True, "Model", None),
    ("vorp", "VORP", True, "Model", None),
    ("rvorp1", "R1 VORP", True, "Rapid VORP", None),
    ("rvorp2", "R2 VORP", True, "Rapid VORP", None),
    ("rvorp3", "R3 VORP", True, "Rapid VORP", None),
    ("rvorp4", "R4 VORP", True, "Rapid VORP", None),
    ("rvorp5", "R5 VORP", True, "Rapid VORP", None),
]

JS = r"""
const $ = s => document.querySelector(s);
const byCode = new Map(DATA.map(r => [r.code, r]));
const K = {tray:'fpl_tray_v1', mark:'fpl_marked_v1', edit:'fpl_edits_v1',
           draft:'fpl_draft_v2', cols:'fpl_cols_v1', notes:'fpl_notes_v1'};
const FIXED = ['mark', 'player'];               // frozen left, never reordered

let tray = load(K.tray, []);
let marked = new Set(load(K.mark, []));
let edits = load(K.edit, {});
let notes = load(K.notes, {});
let draft = Object.assign({slot:5, teams:8, rounds:15, on:true}, load(K.draft, {}));
let order = load(K.cols, null) || COLS.map(c => c.k);
// A saved order from an older build can be missing columns or naming ones that
// no longer exist; reconcile rather than rendering a broken table.
order = FIXED.concat(order.filter(k => !FIXED.includes(k) && COLS.some(c => c.k === k)));
COLS.forEach(c => { if (!order.includes(c.k)) order.push(c.k); });

let sortKey = 'xpts', sortDir = 'desc';
const posFilter = new Set(), flags = new Set();
const meta = Object.fromEntries(COLS.map(c => [c.k, c]));

function load(k, d){ try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }
function save(){ try {
  localStorage.setItem(K.tray, JSON.stringify(tray));
  localStorage.setItem(K.mark, JSON.stringify([...marked]));
  localStorage.setItem(K.edit, JSON.stringify(edits));
  localStorage.setItem(K.draft, JSON.stringify(draft));
  localStorage.setItem(K.cols, JSON.stringify(order));
  localStorage.setItem(K.notes, JSON.stringify(notes));
} catch {} }

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const isNum = v => v !== null && v !== undefined && v !== '' && !Number.isNaN(Number(v));
const dash = '<span class="dot">--</span>';
const fmt = (v, dp) => isNum(v) ? Number(v).toFixed(dp) : dash;

/* ---------- edits ---------- */
function val(r, field){
  const e = edits[r.code];
  if (e && e[field] !== undefined && e[field] !== null) return e[field];
  return r[field];
}
function setVal(code, field, raw, numeric){
  const r = byCode.get(code);
  const v = numeric ? (raw === '' ? null : Number(raw)) : (raw || null);
  if (numeric && v !== null && !Number.isFinite(v)) return;
  edits[code] = edits[code] || {};
  if (v === null || v === r[field]) delete edits[code][field];
  else edits[code][field] = v;
  if (!Object.keys(edits[code]).length) delete edits[code];
  save(); recompute(); render(); renderSide();
}
const isEdited = (r, f) => !!(edits[r.code] && edits[r.code][f] !== undefined);
const editCount = () => Object.values(edits).reduce((n, o) => n + Object.keys(o).length, 0);

/* ---------- the model ---------- */
function poissonAtLeast(k, lam){
  if (lam <= 0) return k <= 0 ? 1 : 0;
  let term = Math.exp(-lam), cdf = term;
  for (let i = 1; i < k; i++){ term *= lam / i; cdf += term; }
  return Math.min(1, Math.max(0, 1 - cdf));
}
/* The per-90 rate with your own xG and xA swapped in.  rate_p90 arrives from
   Python with the original xG and xA already priced into it, so the edit is
   applied as a difference -- subtract what the model used, add what you typed
   -- rather than by rebuilding the whole rate here and risking it drifting
   from xpts_calc. */
/* E[floor(X/per)] for X ~ Poisson(lam), as a sum of tails -- the same closed
   form xpts_calc uses. FPL settles saves and goals conceded every match, so
   two saves in a match are worth nothing and the season total is not the
   season count divided by three. */
function floorPoints(lam, per){
  if (!(per > 0) || !(lam > 0)) return 0;
  let total = 0;
  for (let k = 1; k <= FLOOR_TERMS; k++) total += poissonAtLeast(per * k, lam);
  return total;
}

/* The whole per-90 rate, rebuilt rather than adjusted, so that editing the
   position re-prices everything that depends on it. */
function rateOf(r){
  const sc = SCORING[val(r, 'pos')];
  if (!sc) return 0;
  const num = (f, base) => { const v = Number(val(r, f)); return Number.isFinite(v) ? v : base; };
  return num('xg_p90_adj', r.xg_p90) * sc.goal
       + num('xa_p90_adj', r.xa_p90) * sc.assist * UPLIFT
       + num('pcs', r.pcs) * sc.cs
       + floorPoints(num('saves_per_match', r.saves_per_match), sc.save_per)
       - floorPoints(num('ga_per_match', r.ga_per_match), sc.gc_per)
       + num('card_pts_p90', r.card_pts_p90);
}

/* Expected penalties is an input, not an output, so editing it has to re-price
   the points rather than just changing what is printed. Same formula as
   xpts_calc: conversion x goal points, less the misses, plus any saves. */
function penPtsOf(r){
  const sc = SCORING[val(r, 'pos')];
  if (!sc) return r.pen_pts;
  const p = Number(val(r, 'pens'));
  if (!Number.isFinite(p)) return r.pen_pts;
  return p * (PEN_CONV * sc.goal - (1 - PEN_CONV) * MISS_PTS) + r.pen_save_pts;
}

function scoreOne(r){
  const sc = SCORING[val(r, 'pos')];
  if (!sc) return {matches:0, app:0, hit:0, dc:0, xpts:0};
  const xm = Math.max(0, Number(val(r, 'xmins_adj')) || 0);
  const mps = Math.max(0, Number(val(r, 'mins_per_start')) || 0);
  const matches = mps > 0 ? Math.min(MATCHES, xm / mps) : 0;
  const mpa = matches > 0 ? xm / matches : 0;
  const app = matches * (mpa >= 60 ? sc.app : 1);
  // A typed DefCon % overrides the Poisson outright: if you know something the
  // action rate does not, the model should not argue with you.
  const typed = Number(val(r, 'defcon_hit'));
  const hit = isEdited(r, 'defcon_hit') && Number.isFinite(typed)
    ? Math.min(1, Math.max(0, typed / 100))
    : (sc.dc_pts > 0 ? poissonAtLeast(sc.dc_thr, r.defcon_lambda * mps / 90) : 0);
  const dc = matches * hit * sc.dc_pts;
  // Bonus is contested once per fixture, so it is won per match like
  // appearance and DefCon points. The three slopes move it when xG, xA or
  // P(CS) are edited away from the values the simulation was run at.
  const num = (f, base) => { const v = Number(val(r, f)); return Number.isFinite(v) ? v : base; };
  const bpm = Math.max(0, r.bonus_pm
    + r.bonus_d_xg  * (num('xg_p90_adj', r.xg_p90) - r.bonus_xg_base)
    + r.bonus_d_xa  * (num('xa_p90_adj', r.xa_p90) - r.bonus_xa_base)
    + r.bonus_d_pcs * (num('pcs', r.pcs)           - r.bonus_pcs_base));
  const bonus = matches * bpm;
  return {matches, app, hit, dc, bpm, bonus,
          xpts: rateOf(r) * xm / 90 + app + dc + bonus + penPtsOf(r)};
}

function myPicks(){
  const out = [];
  for (let r = 1; r <= draft.rounds; r++){
    const inRound = (r % 2) ? draft.slot : (draft.teams - draft.slot + 1);
    out.push((r - 1) * draft.teams + inRound);
  }
  return out;
}

function recompute(){
  DATA.forEach(r => {
    const s = scoreOne(r);
    r._matches = s.matches; r._hit = s.hit * 100; r._xpts = s.xpts;
    r._bpm = s.bpm;
    const xm = Math.max(0, Number(val(r, 'xmins_adj')) || 0);
    const xg = Number(val(r, 'xg_p90_adj')), xa = Number(val(r, 'xa_p90_adj'));
    r._xg = (Number.isFinite(xg) ? xg : r.xg_p90) * xm / 90;
    r._xa = (Number.isFinite(xa) ? xa : r.xa_p90) * xm / 90;
    // Number(null) is 0, not NaN.  Left unguarded that gave every undrafted
    // player an ADP of 0, which sorted them above the first pick in the league
    // and labelled them "gone" instead of unknown.
    const raw = val(r, 'adp');
    const a = (raw === null || raw === undefined || raw === '') ? NaN : Number(raw);
    r._adp = Number.isFinite(a) ? a : null;
    r._role = val(r, 'role'); r._threat = val(r, 'threat');
    // The strongest of the three half-to-half z-scores, so the column can be
    // sorted on the size of the disagreement rather than on its sign.
    r._rec_z = [r.rec_z_xg, r.rec_z_xa, r.rec_z_dc].filter(isNum)
      .reduce((a, b) => Math.abs(b) > Math.abs(a) ? b : a, NaN);
  });

  const repl = {};
  for (const [pos, slots] of Object.entries(SLOTS)){
    const pool = DATA.filter(r => r.draftable && r.pos === pos)
                     .map(r => r._xpts).sort((a, b) => b - a);
    const idx = draft.teams * slots;
    repl[pos] = pool.length > idx ? pool[idx] : (pool.length ? pool[pool.length - 1] : 0);
  }
  DATA.forEach(r => { r._vorp = (r.pos in repl) ? r._xpts - repl[r.pos] : null; });

  const picks = myPicks();
  DATA.forEach(r => {
    r._band = null;
    if (!r.draftable || !isNum(r._adp)) return;
    let last = -1;
    picks.forEach((p, i) => { if (p < r._adp) last = i; });
    r._band = last;
  });

  /* Rapid VORP, at five depths.  Not "better than a replacement-level body at
     the end of the draft" but "better than what I could still get at this
     position one round later", so it prices what waiting actually costs.

     Two things it has to get right, both of which it got wrong first time and
     produced a defender on 120.

     A player with NO ADP is not unavailable -- he is the most available player
     there is, because nobody in the sample drafted him at all. Excluding him
     left only six defenders in the pool past pick 109, the best of them on
     51.8 xPts and the next on 18.0, so the baseline collapsed and every
     late-ADP player looked like a superstar.

     And a player who survives to the LAST pick has no next pick, so there is
     nothing to wait for and the number is undefined rather than enormous. */
  const byPos = {};
  DATA.forEach(r => { if (r.draftable && (r.pos in SLOTS)) (byPos[r.pos] ||= []).push(r); });
  Object.values(byPos).forEach(a => a.sort((x, y) => y._xpts - x._xpts));
  DATA.forEach(r => {
    for (let n = 1; n <= 5; n++){ r['_rvorp' + n] = null; r['_rbase' + n] = null; }
    if (!r.draftable || r._band === null || !(r.pos in byPos)) return;
    if (r._band >= picks.length - 1) return;          // no next pick to wait for
    const nextPick = picks[r._band + 1];
    const left = byPos[r.pos].filter(o =>
      o.code !== r.code && (o._adp === null || o._adp > nextPick));
    if (!left.length) return;
    for (let n = 1; n <= 5; n++){
      const top = left.slice(0, n);
      if (top.length < n) break;                      // do not pad a short pool
      const base = top.reduce((sum, o) => sum + o._xpts, 0) / top.length;
      r['_rbase' + n] = base;
      r['_rvorp' + n] = r._xpts - base;
    }
  });
}

/* ---------- cells ---------- */
const CELL = {
  mark: r => `<td class="mark"><button class="ranknum${marked.has(r.code) ? ' on' : ''}"
      data-mark="${r.code}"
      title="Number ${r._rank} of ${DATA.length} in the current sort. ${marked.has(r.code) ? 'On your research list -- click to remove' : 'Click to put on your research list'}"
      aria-pressed="${marked.has(r.code)}"
      aria-label="Rank ${r._rank}, ${esc(r.player)}, research list toggle">${r._rank}</button>${
      notes[r.code] ? `<span class="hasnote" title="${esc(notes[r.code])}">&bull;</span>` : ''}</td>`,
  player: r => `<td class="name${r.moved ? ' movedname' : ''}" draggable="true" data-drag="${r.code}"
      title="${esc(r.player)}${r.moved ? ' -- changed club this summer, so his xG and xA are rates he produced somewhere else' : ''}">${
      esc(r.player)}${r.no_pl_rates ? '<span class="flag norates" title="No Premier League record, so every attacking rate is empty.">no rates</span>' : ''}</td>`,
  team: r => txtCell(r, 'team', 5),
  pos: r => selCell(r, 'pos', val(r, 'pos'), POS_OPTS),
  role: r => selCell(r, 'role', r._role, ROLE_OPTS),
  threat: r => selCell(r, 'threat', r._threat, THREAT_OPTS),
  xmins_solio: r => numCell(r, 'xmins_solio', 0),
  xmins_adj: r => numCell(r, 'xmins_adj', 0),
  research_delta: r => {
    const d = val(r, 'research_delta');
    return `<td class="num">${!isNum(d) ? dash :
      `<span class="delta ${d > 0 ? 'up' : d < 0 ? 'down' : ''}">${d > 0 ? '+' : ''}${Math.round(d)}</span>`}</td>`;
  },
  rec_z: r => {
    // The strongest of the three z-scores, with the whole comparison in the
    // tooltip. A z is not a correction, it is a question: this rate moved by
    // more than the sample can explain, so go and find out whether the role
    // changed or the finishing just ran hot.
    const parts = [['xG', r.rec_z_xg, r.rec_xg_early, r.rec_xg_late],
                   ['xA', r.rec_z_xa, r.rec_xa_early, r.rec_xa_late],
                   ['DefCon', r.rec_z_dc, r.rec_dc_early, r.rec_dc_late]]
      .filter(p => isNum(p[1]));
    if (!parts.length) return `<td class="num">${dash}</td>`;
    const best = parts.reduce((a, b) => Math.abs(b[1]) > Math.abs(a[1]) ? b : a);
    const tip = parts.map(([n, z, e, l]) =>
      `${n}: ${isNum(e) ? e.toFixed(2) : '?'} -> ${isNum(l) ? l.toFixed(2) : '?'} per 90 (z ${z.toFixed(1)})`)
      .join(' | ');
    const cls = !r.rec_flag ? '' : (best[1] > 0 ? 'up' : 'down');
    return `<td class="num" title="First half of his 2025/26 minutes against the second. ${tip}${
      r.rec_flag ? ' -- flagged: bigger than the noise and bigger than 30%.' : ''}"><span class="delta ${cls}">${
      best[1] > 0 ? '+' : ''}${best[1].toFixed(1)}</span></td>`;
  },
  confidence: r => selCell(r, 'confidence', val(r, 'confidence') || '', CONF_OPTS),
  mins_per_start: r => numCell(r, 'mins_per_start', 1),
  matches: r => `<td class="num">${fmt(r._matches, 1)}</td>`,
  xg_p90: r => `<td class="num dim">${fmt(r.xg_p90, 3)}</td>`,
  xa_p90: r => `<td class="num dim">${fmt(r.xa_p90, 3)}</td>`,
  xg_p90_adj: r => numCell(r, 'xg_p90_adj', 3),
  xa_p90_adj: r => numCell(r, 'xa_p90_adj', 3),
  xg_season: r => `<td class="num">${fmt(r._xg, 1)}</td>`,
  xa_season: r => `<td class="num">${fmt(r._xa, 1)}</td>`,
  corner_share: r => numCell(r, 'corner_share', 0),
  corner_role: r => selCell(r, 'corner_role', val(r, 'corner_role') || '', SP_OPTS),
  fk_role: r => selCell(r, 'fk_role', val(r, 'fk_role') || '', SP_OPTS),
  pen_role: r => selCell(r, 'pen_role', val(r, 'pen_role') || '', SP_OPTS),
  pens: r => numCell(r, 'pens', 2),
  defcon_hit: r => numCell(r, 'defcon_hit', 1, isEdited(r, 'defcon_hit') ? val(r, 'defcon_hit') : r._hit),
  adr_pros: r => numCell(r, 'adr_pros', 1),
  base_bps_p90: r => `<td class="num" title="BPS per 90 with every event the model projects elsewhere stripped out -- goals, assists, clean sheets, saves, goals conceded, cards. What is left is appearance, defensive actions, passing and carrying, and it is twice as repeatable as total BPS.">${fmt(r.base_bps_p90, 1)}</td>`,
  bonus_pm: r => `<td class="num" title="Expected bonus in one match at this player's minutes per start, simulated against the three scores he would have to beat. Moves when you edit xG, xA or P(CS).">${fmt(r._bpm, 2)}</td>`,
  adp: r => numCell(r, 'adp', 1, r._adp),
  band: r => `<td class="num">${r._band === null ? dash :
      r._band < 0 ? '<span class="bandtag gone">gone</span>'
                  : `<span class="bandtag">R${r._band + 1}</span>`}</td>`,
  xpts: r => `<td class="num">${fmt(r._xpts, 1)}</td>`,
  vorp: r => `<td class="num">${fmt(r._vorp, 1)}</td>`,
  rvorp1: r => rvorpCell(r, 1), rvorp2: r => rvorpCell(r, 2),
  rvorp3: r => rvorpCell(r, 3), rvorp4: r => rvorpCell(r, 4),
  rvorp5: r => rvorpCell(r, 5),
};
function rvorpCell(r, n){
  const b = r['_rbase' + n];
  return `<td class="num"${b !== null ?
    ` title="baseline ${b.toFixed(1)} xPts -- the best ${n} at ${r.pos} still on the board at your next pick"` : ''
    }>${fmt(r['_rvorp' + n], 1)}</td>`;
}
function undoBtn(r, f){
  if (!isEdited(r, f)) return '';
  const was = r[f];
  return `<button class="undo" data-undo="${r.code}" data-f="${f}"
    title="Put this back to ${isNum(was) ? Number(was).toFixed(3).replace(/0+$/, '').replace(/\.$/, '') : (was ?? 'its original value')}"
    aria-label="Undo this edit">&#8630;</button>`;
}
function txtCell(r, f, size){
  return `<td class="${isEdited(r, f) ? 'edited' : ''}"><input class="edit txt" size="${size}"
    data-code="${r.code}" data-f="${f}" data-text="1"
    value="${esc(val(r, f) ?? '')}">${undoBtn(r, f)}</td>`;
}
function numCell(r, f, dp, v){
  const x = v === undefined ? val(r, f) : v;
  return `<td class="num ${isEdited(r, f) ? 'edited' : ''}"><input class="edit" inputmode="decimal"
    data-code="${r.code}" data-f="${f}" value="${isNum(x) ? Number(x).toFixed(dp) : ''}">${undoBtn(r, f)}</td>`;
}
function selCell(r, f, v, opts){
  return `<td class="${isEdited(r, f) ? 'edited' : ''}"><select class="edit" data-code="${r.code}" data-f="${f}">
    ${opts.map(o => `<option value="${o}"${o === v ? ' selected' : ''}>${o}</option>`).join('')}</select>${undoBtn(r, f)}</td>`;
}
function revert(code, field){
  if (edits[code]) delete edits[code][field];
  if (edits[code] && !Object.keys(edits[code]).length) delete edits[code];
  save(); recompute(); render(); renderSide();
}

/* ---------- header, rebuilt so it follows the column order ---------- */
function renderHead(){
  const runs = [];
  order.forEach(k => {
    const g = meta[k].group;
    if (runs.length && runs[runs.length - 1].g === g) runs[runs.length - 1].n++;
    else runs.push({g, n: 1});
  });
  $('#grouprow').innerHTML = runs.map((r, i) =>
    `<th colspan="${r.n}"${i && r.g ? ' class="g"' : ''}>${r.g}</th>`).join('');

  $('#headrow').innerHTML = order.map(k => {
    const c = meta[k];
    if (k === 'mark') return '<th class="mark"></th>';
    const cls = k === 'player' ? 'pname' : (c.num ? 'num' : '');
    const movable = FIXED.includes(k) ? '' : ' draggable="true"';
    const dir = k === sortKey ? ` data-dir="${sortDir}"` : '';
    return `<th class="${cls}"${movable} data-col="${k}">
      <button data-k="${k}"${dir}>${c.label}</button></th>`;
  }).join('');
  wireHead();
}

function wireHead(){
  document.querySelectorAll('#headrow button').forEach(b =>
    b.addEventListener('click', () => {
      const k = b.dataset.k;
      if (sortKey === k) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
      else { sortKey = k;
             sortDir = ['player','team','pos','role','threat','corner_role','fk_role',
                        'pen_role','adp','band','games_missed','adr_pros','confidence'].includes(k)
                        ? 'asc' : 'desc'; }
      renderHead(); render();
    }));
  document.querySelectorAll('#headrow th[draggable]').forEach(th => {
    th.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/col', th.dataset.col);
      e.dataTransfer.effectAllowed = 'move';
      th.classList.add('coldrag');
    });
    th.addEventListener('dragend', () => th.classList.remove('coldrag'));
    th.addEventListener('dragover', e => {
      if (!e.dataTransfer.types.includes('text/col')) return;
      e.preventDefault(); th.classList.add('colover');
    });
    th.addEventListener('dragleave', () => th.classList.remove('colover'));
    th.addEventListener('drop', e => {
      e.preventDefault(); th.classList.remove('colover');
      const from = e.dataTransfer.getData('text/col'), to = th.dataset.col;
      if (!from || from === to) return;
      order = order.filter(k => k !== from);
      order.splice(order.indexOf(to), 0, from);
      save(); renderHead(); render();
    });
  });
}

/* ---------- table ---------- */
function visible(){
  const q = $('#q').value.trim().toLowerCase();
  const team = $('#team').value;
  return DATA.filter(r => {
    if (q && !(r.player + ' ' + r.team).toLowerCase().includes(q)) return false;
    if (team && r.team !== team) return false;
    if (posFilter.size && !posFilter.has(r.pos)) return false;
    if (flags.has('researched') && r.research_delta === null) return false;
    if (flags.has('setpiece') && !r.sp_score) return false;
    if (flags.has('threat') && r._threat === 'none') return false;
    if (flags.has('marked') && !marked.has(r.code)) return false;
    if (flags.has('edited') && !edits[r.code]) return false;
    if (flags.has('recency') && !r.rec_flag) return false;
    return true;
  });
}

const SORTED = {matches:'_matches', xpts:'_xpts', vorp:'_vorp',
                rvorp1:'_rvorp1', rvorp2:'_rvorp2', rvorp3:'_rvorp3',
                rvorp4:'_rvorp4', rvorp5:'_rvorp5',
                adp:'_adp', xg_season:'_xg', xa_season:'_xa', defcon_hit:'_hit',
                role:'_role', threat:'_threat', band:'_band', rec_z:'_rec_z',
                bonus_pm:'_bpm'};

function render(){
  const key = SORTED[sortKey] || sortKey;
  const dir = sortDir === 'asc' ? 1 : -1;
  const cmp = (a, b) => {
    let x = a[key], y = b[key];
    if (key === '_role'){ x = ROLE_RANK[x] ?? 9; y = ROLE_RANK[y] ?? 9; }
    if (typeof x === 'string' || typeof y === 'string')
      return dir * String(x ?? '').localeCompare(String(y ?? ''));
    if (!isNum(x) && x !== 0) return 1;
    if (!isNum(y) && y !== 0) return -1;
    return dir * (x - y);
  };
  // Rank the whole field first, then filter.  Ranking after filtering meant a
  // search for one player numbered him 1, whatever his actual place.
  DATA.slice().sort(cmp).forEach((r, i) => { r._rank = i + 1; });
  const rows = visible().sort(cmp);

  const n = editCount();
  $('#editN').textContent = n ? `${n} edit${n === 1 ? '' : 's'}` : 'no edits';
  $('#resetEdits').disabled = !n;

  // Which rows land on your picks, by their place in the full order.
  const pickSet = new Set(myPicks());
  $('#rows').innerHTML = rows.map(r => {
    const cls = [marked.has(r.code) ? 'marked' : '',
                 pickSet.has(r._rank) ? 'mypick' : '',
                 draft.on && r._band !== null ? (r._band < 0 ? 'gone' : 'band') : ''];
    return `<tr data-code="${r.code}" class="${cls.join(' ').trim()}">${
      order.map(k => CELL[k](r)).join('')}</tr>`;
  }).join('');
  $('#count').textContent = `${rows.length} of ${DATA.length} players`;
  wireRows();
}

function wireRows(){
  document.querySelectorAll('[data-mark]').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation();
      const c = Number(b.dataset.mark);
      marked.has(c) ? marked.delete(c) : marked.add(c);
      save(); render(); renderSide();
    }));
  document.querySelectorAll('input.edit').forEach(el => {
    el.addEventListener('change', () =>
      setVal(Number(el.dataset.code), el.dataset.f, el.value, !el.dataset.text));
    el.addEventListener('keydown', e => { if (e.key === 'Enter') el.blur(); });
  });
  document.querySelectorAll('select.edit').forEach(el =>
    el.addEventListener('change', () => setVal(Number(el.dataset.code), el.dataset.f, el.value, false)));
  document.querySelectorAll('[data-undo]').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation();
      revert(Number(b.dataset.undo), b.dataset.f);
    }));
  document.querySelectorAll('td.name[data-drag]').forEach(el =>
    el.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', el.dataset.drag);
      e.dataTransfer.effectAllowed = 'copy';
    }));
}

/* ---------- sidebar ---------- */
/* An arrival's card carries what it takes to decide whether he is worth
   looking at: where he came from, the minutes the research gave him, his role,
   whether his club is still shopping, and -- the point -- the rates he
   produced at his old club, since those are exactly what the model will use
   for him.  A defender with a DefCon rate already on the card does not need
   opening at all. */
function cardHtml(r, inTray){
  const mins = isNum(val(r, 'xmins_adj')) ? Math.round(val(r, 'xmins_adj')) : '--';
  const rates = r.no_pl_rates
    ? '<span class="flag norates">no rates -- nothing to go on</span>'
    : `<span class="rate">xG ${Number(val(r, 'xg_p90_adj')).toFixed(2)}</span>
       <span class="rate">xA ${Number(val(r, 'xa_p90_adj')).toFixed(2)}</span>
       ${r.pos !== 'GKP' ? `<span class="rate">DefCon ${Number(r.defcon_lambda).toFixed(1)}/90</span>` : ''}`;
  return `<div class="card" draggable="true" data-drag="${r.code}" data-card="${r.code}">
    <div class="top"><span class="who">${esc(r.player)}</span>
      <span class="meta">${r.pos}</span>
      ${inTray ? `<button class="x" data-remove="${r.code}" aria-label="Remove ${esc(r.player)}">&times;</button>` : ''}
    </div>
    <div class="row2">${r.club_2526 ? esc(r.club_2526) + ' &rarr; ' : 'new to the PL &rarr; '}${r.team}
      &middot; ${mins} mins</div>
    <div class="row3"><span class="tag ${r._role}">${r._role}</span>
      ${r._threat !== 'none' ? `<span class="tag ${r._threat}">${r._threat}</span>` : ''}</div>
    <div class="row3">${rates}</div>
  </div>`;
}

function renderSide(){
  const q = $('#sideq').value.trim().toLowerCase();
  const inTray = new Set(tray);
  const movers = DATA
    .filter(r => (r.moved || r.no_pl_rates) && !inTray.has(r.code))
    .filter(r => !q || (r.player + ' ' + r.team + ' ' + r.club_2526).toLowerCase().includes(q))
    .sort((a, b) => (Number(val(b, 'xmins_adj')) || 0) - (Number(val(a, 'xmins_adj')) || 0));
  // Grouped by their NEW club, because that is how you actually review them:
  // one squad at a time, deciding whose minutes are real.
  const clubs = {};
  movers.forEach(r => (clubs[r.team] ||= []).push(r));
  $('#movers').innerHTML = Object.keys(clubs).sort().map(club => {
    const list = clubs[club].sort((a, b) =>
      (Number(val(b, 'xmins_adj')) || 0) - (Number(val(a, 'xmins_adj')) || 0));
    // "--" is a player with no 2026/27 club on file; say so rather than
    // printing a dash as if it were a club name.
    const label = club === '--' ? 'No club listed' : club;
    return `<div class="clubgrp"><h3>${label} <span>${list.length}</span></h3>
      ${list.map(r => cardHtml(r, false)).join('')}</div>`;
  }).join('') || '<div class="empty">Nothing matches.</div>';
  $('#moversN').textContent = movers.length;

  $('#trayBody').innerHTML = tray.length
    ? tray.map(c => cardHtml(byCode.get(c), true)).join('')
    : '<div class="empty">Drag anyone here, from this list or from the table, and drop to place them in your own order.</div>';
  $('#trayN').textContent = tray.length;

  const list = DATA.filter(r => marked.has(r.code));
  $('#markedBody').innerHTML = list.length
    ? list.map(r => `<div class="card"><div class="top"><span class="who">${esc(r.player)}</span>
        <span class="meta">${r.pos} ${r.team}</span>
        <button class="x" data-unmark="${r.code}" aria-label="Remove ${esc(r.player)} from the research list">&times;</button></div>
        <textarea class="note" data-note="${r.code}" rows="2"
          placeholder="What do you want checked? e.g. is his xG from set pieces?"
          aria-label="Research note for ${esc(r.player)}">${esc(notes[r.code] || '')}</textarea>
        </div>`).join('')
    : '<div class="empty">Nothing here yet. Click the number at the left of any row to add that player.</div>';
  $('#markedN').textContent = list.length;

  $('#pickList').innerHTML = myPicks()
    .map((p, i) => `<b>${p}</b><span class="dot"> R${i + 1}</span>`).join('  ');
  wireSide();
}

function wireSide(){
  document.querySelectorAll('.side [data-drag]').forEach(el => {
    el.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', el.dataset.drag);
      el.classList.add('drag');
    });
    el.addEventListener('dragend', () => el.classList.remove('drag'));
  });
  document.querySelectorAll('[data-remove]').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation();
      tray = tray.filter(c => c !== Number(b.dataset.remove));
      save(); renderSide();
    }));
  document.querySelectorAll('[data-unmark]').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation();
      marked.delete(Number(b.dataset.unmark));
      delete notes[Number(b.dataset.unmark)];
      save(); render(); renderSide();
    }));
  // Saved on the way out of the box, not on every keystroke, so a long note
  // does not re-render the panel from under the cursor.
  document.querySelectorAll('[data-note]').forEach(t => {
    t.addEventListener('change', () => {
      const c = Number(t.dataset.note);
      if (t.value.trim()) notes[c] = t.value.trim(); else delete notes[c];
      save(); render();
    });
    t.addEventListener('blur', () => t.dispatchEvent(new Event('change')));
  });
}

const trayEl = $('#tray');
trayEl.addEventListener('dragover', e => {
  if (e.dataTransfer.types.includes('text/col')) return;
  e.preventDefault(); trayEl.classList.add('over');
});
trayEl.addEventListener('dragleave', () => trayEl.classList.remove('over'));
trayEl.addEventListener('drop', e => {
  e.preventDefault(); trayEl.classList.remove('over');
  const code = Number(e.dataTransfer.getData('text/plain'));
  if (!byCode.has(code)) return;
  const cards = [...$('#trayBody').querySelectorAll('[data-card]')];
  const at = cards.findIndex(c => e.clientY < c.getBoundingClientRect().top + c.offsetHeight / 2);
  tray = tray.filter(c => c !== code);
  if (at < 0) tray.push(code); else tray.splice(at, 0, code);
  save(); renderSide();
});

function copy(btn, txt){
  navigator.clipboard?.writeText(txt);
  const was = btn.textContent;
  btn.textContent = 'Copied';
  setTimeout(() => { btn.textContent = was; }, 1400);
}
$('#copyTray').addEventListener('click', e =>
  copy(e.target, tray.map((c, i) => {
    const r = byCode.get(c);
    return `${i + 1}. ${r.player} (${r.pos}, ${r.team})`;
  }).join('\n')));
$('#clearTray').addEventListener('click', () => { tray = []; save(); renderSide(); });
$('#copyMarked').addEventListener('click', e =>
  copy(e.target, DATA.filter(r => marked.has(r.code))
    .map(r => `${r.player} (${r.pos}, ${r.team}) -- ${r._role}`
      + `${r._threat !== 'none' ? ', ' + r._threat + ' risk' : ''}`
      + `${notes[r.code] ? '\n    ' + notes[r.code] : ''}`)
    .join('\n')));

/* ---------- controls ---------- */
document.querySelectorAll('.chip[data-pos]').forEach(c =>
  c.addEventListener('click', () => {
    const on = c.getAttribute('aria-pressed') === 'true';
    c.setAttribute('aria-pressed', String(!on));
    on ? posFilter.delete(c.dataset.pos) : posFilter.add(c.dataset.pos);
    render();
  }));
document.querySelectorAll('.chip[data-flag]').forEach(c =>
  c.addEventListener('click', () => {
    const on = c.getAttribute('aria-pressed') === 'true';
    c.setAttribute('aria-pressed', String(!on));
    on ? flags.delete(c.dataset.flag) : flags.add(c.dataset.flag);
    render();
  }));
$('#q').addEventListener('input', render);
$('#team').addEventListener('change', render);
$('#sideq').addEventListener('input', renderSide);
$('#togglePanels').addEventListener('click', () => {
  const off = $('#layout').classList.toggle('no-side');
  $('#togglePanels').textContent = off ? 'Show panels' : 'Hide panels';
});
['slot', 'teams'].forEach(f => $('#' + f).addEventListener('change', e => {
  const v = Number(e.target.value);
  if (Number.isFinite(v) && v > 0) draft[f] = v;
  if (draft.slot > draft.teams){ draft.slot = draft.teams; $('#slot').value = draft.slot; }
  $('#slot').max = draft.teams;
  save(); recompute(); render(); renderSide();
}));
$('#bandsOn').addEventListener('click', e => {
  draft.on = !draft.on;
  e.target.setAttribute('aria-pressed', String(draft.on));
  save(); render();
});
$('#stripes').addEventListener('click', e => {
  const on = $('#layout').classList.toggle('striped');
  e.target.setAttribute('aria-pressed', String(on));
});
$('#resetEdits').addEventListener('click', () => {
  if (!confirm('Discard every edit and go back to the model\'s own numbers?')) return;
  edits = {}; save(); recompute(); render(); renderSide();
});
$('#resetCols').addEventListener('click', () => {
  order = COLS.map(c => c.k); save(); renderHead(); render();
});

$('#slot').value = draft.slot; $('#teams').value = draft.teams;
$('#slot').max = draft.teams;
$('#bandsOn').setAttribute('aria-pressed', String(draft.on));
recompute(); renderHead(); render(); renderSide();
"""


def html(df: pd.DataFrame) -> str:
    teams = sorted(t for t in df["team"].dropna().unique() if t != "--")
    opts = "".join(f'<option value="{t}">{t}</option>' for t in teams)

    records = df.replace({np.nan: None}).to_dict("records")
    for r in records:
        for k, v in r.items():
            if isinstance(v, (np.integer, np.floating)):
                r[k] = None if pd.isna(v) else float(v)
            elif isinstance(v, (np.bool_, bool)):
                r[k] = bool(v)
    data = json.dumps(records, ensure_ascii=True)

    cols = [{"k": k, "label": label, "num": num, "group": group}
            for k, label, num, group, _ in COLS]
    scoring = {pos: {"app": v[4], "dc_thr": v[5], "dc_pts": v[3],
                     "goal": v[0], "assist": v[1], "cs": v[2],
                     "save_per": v[6], "gc_per": v[7]}
               for pos, v in xpts_calc.SCORING.items()}
    consts = (f"const COLS={json.dumps(cols)};"
              f"const SCORING={json.dumps(scoring)};"
              f"const SLOTS={json.dumps(xpts_model.SQUAD_SLOTS)};"
              f"const MATCHES={xpts_calc.MATCHES};"
              f"const UPLIFT={xpts_calc.ASSIST_UPLIFT};"
              f"const PEN_CONV={xpts_calc.PENALTY_CONVERSION};"
              f"const MISS_PTS={xpts_calc.MISS_POINTS};"
              f"const FLOOR_TERMS={xpts_calc.FLOOR_TERMS};"
              f"const ROLE_RANK={json.dumps({**ROLE_RANK, 'rotation': 2.5})};"
              f"const ROLE_OPTS={json.dumps(sorted(set(list(ROLE_RANK) + ['rotation'])))};"
              f"const THREAT_OPTS={json.dumps(['none', 'reinforcement', 'exit'])};"
              f"const POS_OPTS={json.dumps(sorted(xpts_calc.SCORING))};"
              f"const CONF_OPTS={json.dumps(['', 'low', 'medium', 'high'])};"
              f"const SP_OPTS={json.dumps(['', 'Primary', 'Secondary', 'Tertiary'])};")

    n_moved = int((df.moved | df.no_pl_rates).sum())
    n_adr = int(df.adr_pros.notna().sum())
    return f"""<meta charset="utf-8">
<title>Draft Levers</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="stamp">FPL 2026/27 &middot; {len(df)} players &middot; research to 16 Aug 2026</p>
  <h1>Every lever, one table</h1>
  <p class="sub">The inputs that move a projection, side by side &mdash; and
  every one of them editable. Change a player's minutes and his matches, xG,
  DefCon, xPts and both VORPs move with it, through the same model the workbook
  uses. Drag a column header to reorder. Nothing you type leaves this page.</p>
</header>

<div class="controls">
  <input type="search" id="q" placeholder="Search player or club" aria-label="Search players">
  <select id="team" aria-label="Filter by club"><option value="">All clubs</option>{opts}</select>
  <div class="chips" role="group" aria-label="Filter by position">
    <button class="chip" data-pos="GKP" aria-pressed="false">GKP</button>
    <button class="chip" data-pos="DEF" aria-pressed="false">DEF</button>
    <button class="chip" data-pos="MID" aria-pressed="false">MID</button>
    <button class="chip" data-pos="FWD" aria-pressed="false">FWD</button>
  </div>
  <div class="chips" role="group" aria-label="Filter rows">
    <button class="chip" data-flag="researched" aria-pressed="false">Research moved minutes</button>
    <button class="chip" data-flag="threat" aria-pressed="false">Under transfer threat</button>
    <button class="chip" data-flag="setpiece" aria-pressed="false">Takes set pieces</button>
    <button class="chip markchip" data-flag="marked" aria-pressed="false">&#9733; On my research list</button>
    <button class="chip" data-flag="edited" aria-pressed="false">Edited by me</button>
    <button class="chip" data-flag="recency" aria-pressed="false">Second half disagrees with the first</button>
  </div>
  <span class="count" id="count"></span>
</div>

<div class="controls">
  <label class="picks">My draft slot
    <input type="number" id="slot" min="1" max="8" step="1" style="width:58px"></label>
  <label class="picks">Teams
    <input type="number" id="teams" min="2" max="20" step="1" style="width:58px"></label>
  <button class="chip" id="bandsOn" aria-pressed="true">Shade by draft band</button>
  <button class="chip" id="stripes" aria-pressed="true">Shade my pick rows</button>
  <span class="count"><span id="editN"></span>
    <button class="chip" id="resetEdits" style="margin-left:8px">Reset edits</button>
    <button class="chip" id="resetCols" style="margin-left:6px">Reset columns</button>
    <button class="chip" id="togglePanels" style="margin-left:6px">Hide panels</button>
  </span>
</div>
<div class="controls" style="padding-top:9px;padding-bottom:9px">
  <span class="picks">My picks: <span id="pickList"></span></span>
</div>

<div class="legend">
  <span><span class="swatch" style="background:var(--input)"></span><b>Blue cells</b> are yours to type in</span>
  <span><span class="swatch" style="background:var(--editd)"></span>an edit you have made &mdash; the <b>&#8630;</b> in the cell puts it back</span>
  <span><b class="movedname">Coloured names</b> changed club this summer</span>
  <span><b>The number</b> is the row's place in the current sort &mdash; click it to add him to the research list</span>
  <span><b>Drag a column header</b> to move it</span>
</div>

<div class="layout striped" id="layout">
  <div class="main">
    <div class="tablebox">
      <table>
        <thead>
          <tr class="grouprow" id="grouprow"></tr>
          <tr class="headrow" id="headrow"></tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </div>

  <aside class="side">
    <section class="panel">
      <h2>Summer arrivals <span class="n" id="moversN"></span></h2>
      <div style="padding:9px 9px 0">
        <input type="search" id="sideq" placeholder="Filter arrivals" aria-label="Filter arrivals" style="width:100%">
      </div>
      <div class="body" id="movers"></div>
      <p class="hint">Everyone who changed club, plus anyone new to the league
      &mdash; {n_moved} players, grouped by their new club. The rates shown are
      the ones he produced at his <em>old</em> club, which are exactly what the
      model will use for him, so a defender whose DefCon rate is already on the
      card needs no further review. <strong>no rates</strong> means he has no
      Premier League record at all and the model has nothing to go on.</p>
    </section>

    <section class="panel tray" id="tray">
      <h2>Your tray <span class="n" id="trayN"></span></h2>
      <div class="body" id="trayBody"></div>
      <div class="act">
        <button id="copyTray">Copy list</button>
        <button id="clearTray">Clear</button>
      </div>
      <p class="hint">Drop anyone here and drag to reorder. Saved in this
      browser only.</p>
    </section>

    <section class="panel">
      <h2>Research list <span class="n" id="markedN"></span></h2>
      <div class="body" id="markedBody"></div>
      <div class="act"><button id="copyMarked">Copy list</button></div>
      <p class="hint">Players you flagged for a closer look. Write what you
      want checked in the box &mdash; the note copies out with the list, and a
      dot appears next to his number in the table.</p>
    </section>
  </aside>
</div>

<details>
  <summary>What each column is, what you can edit, and where the data runs out</summary>
  <h3>VORP and Rapid VORP</h3>
  <p><strong>VORP</strong> is the season-long version: xPts above the
  (teams &times; slots + 1)-th best player at that position &mdash; the body
  you could still get once everyone has filled their squad. It answers "how
  much is he worth over the whole draft".</p>
  <p><strong>Rapid VORP</strong> answers the question you actually have with
  the clock running: <em>what does waiting one round cost me here?</em> The
  baseline is not the end of the draft but the best players at his own position
  whose ADP says they will still be there at your <em>next</em> pick. Set
  whether that baseline is the best 1, 2 or 3 of them. A high Rapid VORP means
  the position falls away sharply right after him; near zero means someone just
  as good is likely to survive the round, whatever his season-long VORP says.
  Hover the cell for the baseline it used.</p>
  <p>Both recompute when you edit anything &mdash; and note that Rapid VORP
  also moves when you edit somebody <em>else's</em> ADP, because that changes
  who is still on the board.</p>

  <h3>Editing and columns</h3>
  <p>Seven things are editable: <strong>xMins adjusted</strong>,
  <strong>Mins/start</strong>, <strong>xG/90 mine</strong>, <strong>xA/90
  mine</strong>, <strong>Role</strong>, <strong>Transfer threat</strong> and
  <strong>ADP</strong>. All four numeric ones re-score the player using the
  same arithmetic as <code>xpts_calc.py</code>, not an approximation of it.</p>
  <p>The rates you can change sit in their own columns. <strong>xG/90</strong>
  and <strong>xA/90</strong> are the model's, greyed and untouchable;
  <strong>xG/90 mine</strong> and <strong>xA/90 mine</strong> start equal to
  them and are yours. So the original is always readable next to the change,
  and <strong>xG season</strong> and <strong>xA season</strong> follow whatever
  you set, multiplied by your minutes.</p>
  <p>That matters most for the {n_moved} summer arrivals: their minutes are
  researched but their rates are empty, because nothing in the Premier League
  data can supply them. These are the cells to fill in from what you know about
  what they did elsewhere.</p>
  <p>Every edit shows in a darker blue with a <strong>&#8630;</strong> that puts
  that one field back to the model's number. <em>Edited by me</em> filters to
  the rows you have touched, and <em>Reset edits</em> clears the lot.</p>
  <p>Drag any column header sideways to move it; the order saves too. The
  number and the player name stay pinned at the left so a wide table still
  reads. <em>Reset columns</em> puts them back.</p>
  <p>The leftmost column is the row's place in the current sort, and clicking
  it puts that player on your research list &mdash; outlined for no, filled for
  yes. <em>Shade my pick rows</em> then shades the rows landing on your picks
  in the snake: 4, 13, 20, 29 for slot 4 of 8, not every fourth row, because
  the turn reverses each round so the gap alternates between 9 and 7. It
  follows whatever order the table is in, so sorting it your way shows where
  your own picks fall.</p>

  <h3>Draft slot and bands</h3>
  <p>Set your slot and league size and <strong>My picks</strong> lists your
  overall pick numbers in a snake. <strong>There at</strong> is the last of
  those picks a player is still projected to be available at, read off ADP.
  <code>gone</code> means his ADP is earlier than your first pick. Edit an ADP
  and the bands and Rapid VORP both move. This is arithmetic on other people's
  draft behaviour, not a view about any player, and it does not choose
  anyone.</p>

  <h3>ADR Pros</h3>
  <p>The mean rank a player is given by published draft rankings from named
  analysts &mdash; {n_adr} players carry one. Only whole-draft lists count
  toward it. Position-by-position lists are deliberately excluded: the Premier
  League's own Scout publishes four separate rankings, so its four number ones
  are the best goalkeeper, the best defender, the best midfielder and the best
  forward, and averaging those against a global top 100 makes the best keeper
  look like the best player alive.</p>
  <p>Every row behind this column was re-fetched and checked against its source
  by <code>scripts/verify_pro_rankings.py</code> before being used, because the
  tooling that collected it produced a complete, plausible, entirely invented
  ranking for a URL that returns a 404. Four rows that could not be traced back
  to what was actually published are excluded.</p>

  <h3>Role and transfer threat</h3>
  <p><strong>Role</strong> comes from the club research, which named a starter
  and his competition for every position at 17 clubs. <code>nailed</code> and
  <code>likely</code> are the researcher's words for the starter;
  <code>contested</code> means the slot is in the fight; <code>rotation</code>
  means he was listed as competition for someone else's place;
  <code>unknown</code> means the research did not reach him. <strong>Transfer
  threat</strong> is whether the club is still shopping for that position
  (<code>reinforcement</code>) or willing to sell him (<code>exit</code>).</p>

  <h3>Set pieces &mdash; and the gap</h3>
  <p><strong>Corners</strong> is the share of his club's 2025/26 corners he
  actually took, measured rather than assumed, and the closest thing to a
  left/right split: a first-choice taker on
  <code>{int(BOTH_SIDES_SHARE * 100)}%</code> or more is taking both sides.</p>
  <p>Free kicks and penalties can only be an order, never a percentage. Nothing
  in any source counts free kicks or penalties <em>taken</em>, only who is
  designated. FPL also publishes a single combined order for <em>corners and
  indirect free kicks</em>, so the indirect free-kick taker cannot be told
  apart from the corner taker at all.</p>
</details>

<footer>Generated by <code>scripts/levers_report.py</code> from the same master
the workbook is built from. The research is a dated snapshot to 16 August 2026;
it knows nothing after that, and it cannot see January.</footer>
</div>
<script>const DATA={data};{consts}{JS}</script>
"""


def main() -> int:
    df = assemble()
    path = OUT / "draft_levers.html"
    path.write_text(html(df), encoding="utf-8")

    print(f"draft_levers.html      {len(df)} players")
    print("  role:  ", df.role.value_counts().to_dict())
    print("  threat:", df.threat.value_counts().to_dict())
    print(f"  ADP for {int(df.adp.notna().sum())}, "
          f"ADR Pros for {int(df.adr_pros.notna().sum())}")
    print(f"  summer arrivals: {int((df.moved | df.no_pl_rates).sum())}")
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
