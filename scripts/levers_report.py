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
from common import PROC, OUT

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
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
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
    path = PROC / "pro_rankings" / "rankings.csv"
    blank = pd.Series(np.nan, index=m.index, dtype=float)
    if not path.exists():
        print("  no pro_rankings/rankings.csv yet -- ADR Pros left empty")
        return blank

    r = pd.read_csv(path)
    if r.empty or "player_name" not in r.columns:
        print("  pro_rankings/rankings.csv is empty -- ADR Pros left empty")
        return blank

    by_name: dict[str, int] = {}
    for code, player in m[["code", "player"]].itertuples(index=False):
        by_name.setdefault(norm(player), code)

    r["code"] = r["player_name"].map(lambda n: by_name.get(norm(n)))
    missed = r[r["code"].isna()]
    agg = (r.dropna(subset=["code"])
             .groupby("code")["rank"].agg(["mean", "count"]))

    n_src = r["source_name"].nunique()
    print(f"  pro rankings: {n_src} source(s), {len(r)} rows, "
          f"{len(agg)} players matched, {len(missed)} names unmatched")
    if len(missed):
        for n in sorted(missed["player_name"].unique())[:5]:
            print(f"    no FPL entry: {n}")

    codes = m.set_index("code").index
    return m["code"].map(agg["mean"]).astype(float).round(1)


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
        "pens": d["pens_season"].round(2),
        "defcon_hit": (s["defcon_hit"] * 100).round(1),
        # Full precision, not display precision: the browser re-scores every
        # row and verify_levers compares the two.  Rounding here would
        # hide a real difference inside the rounding step.
        "xpts": s["xpts_season"].round(6),
        "adp": m["adp"],

        # Everything the page needs to re-score a player in the browser when
        # you edit his minutes.  These four are the only parts of xpts_calc
        # that do not depend on xMins or minutes-per-start, so shipping them
        # lets the page reproduce the model exactly rather than approximate it.
        # verify_levers() checks that it does.
        "rate_p90": s["rate_pts_p90"].round(6),
        "pen_pts": s["pen_pts_season"].round(6),
        "defcon_lambda": d["defcon_lambda"].fillna(0).round(6),
        "xg_p90": d["xG_p90"].fillna(0).round(6),
        "xa_p90": d["xA_p90"].fillna(0).round(6),
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

    out["adr_pros"] = pro_rankings(m)

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
  padding-left:6px; padding-right:0}
td.name,th.pname{position:sticky; left:30px; z-index:2; background:var(--surface);
  font-weight:550; border-right:1px solid var(--line); cursor:grab}
thead th.mark,thead th.pname{z-index:6; background:var(--raised)}
tbody tr:hover td.name,tbody tr:hover td.mark{background:var(--raised)}
tbody tr.marked td.name,tbody tr.marked td.mark{background:var(--mark-soft)}
td.dim{color:var(--faint)}

.markbtn{font:inherit; font-size:13px; line-height:1; cursor:pointer;
  background:none; border:0; color:var(--line); padding:2px 4px}
.markbtn:hover{color:var(--mark)}
tr.marked .markbtn{color:var(--mark)}

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
input.edit{min-width:74px}
td.edited .edit{background:var(--editd); font-weight:650}
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
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

JS = r"""
const $ = s => document.querySelector(s);
const byCode = new Map(DATA.map(r => [r.code, r]));
const K = {tray:'fpl_tray_v1', mark:'fpl_marked_v1', edit:'fpl_edits_v1', draft:'fpl_draft_v1'};

let tray = load(K.tray, []);
let marked = new Set(load(K.mark, []));
let edits = load(K.edit, {});
let draft = Object.assign({slot: 4, teams: 8, rounds: 15, on: true}, load(K.draft, {}));
let sortKey = 'xpts', sortDir = 'desc';
const posFilter = new Set(), flags = new Set();

function load(k, d){ try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }
function save(){ try {
  localStorage.setItem(K.tray, JSON.stringify(tray));
  localStorage.setItem(K.mark, JSON.stringify([...marked]));
  localStorage.setItem(K.edit, JSON.stringify(edits));
  localStorage.setItem(K.draft, JSON.stringify(draft));
} catch {} }

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const isNum = v => v !== null && v !== undefined && !Number.isNaN(v);
const fmt = (v, dp) => isNum(v) ? Number(v).toFixed(dp) : '<span class="dot">--</span>';

/* ---------- edits: an override layer over the shipped values ---------- */
function val(r, field){
  const e = edits[r.code];
  if (e && e[field] !== undefined && e[field] !== null) return e[field];
  return r[field];
}
function setVal(code, field, raw, numeric){
  const r = byCode.get(code);
  let v = numeric ? (raw === '' ? null : Number(raw)) : (raw || null);
  if (numeric && v !== null && !Number.isFinite(v)) return;
  edits[code] = edits[code] || {};
  // Typing the shipped value back in is not an edit; drop it so the cell
  // stops claiming to be overridden.
  if (v === null || v === r[field]) delete edits[code][field];
  else edits[code][field] = v;
  if (!Object.keys(edits[code]).length) delete edits[code];
  save(); recompute(); render(); renderSide();
}
const isEdited = (r, f) => !!(edits[r.code] && edits[r.code][f] !== undefined);
const editCount = () => Object.values(edits).reduce((n, o) => n + Object.keys(o).length, 0);

/* ---------- the model, ported so an edit actually moves a number ---------- */
function poissonAtLeast(k, lam){
  if (lam <= 0) return k <= 0 ? 1 : 0;
  let term = Math.exp(-lam), cdf = term;          // P(X = 0)
  for (let i = 1; i < k; i++){ term *= lam / i; cdf += term; }
  return Math.min(1, Math.max(0, 1 - cdf));       // 1 - P(X <= k-1)
}

function scoreOne(r){
  const sc = SCORING[r.pos];
  if (!sc) return {matches: 0, app: 0, hit: 0, dc: 0, xpts: 0};
  const xm = Math.max(0, Number(val(r, 'xmins_adj')) || 0);
  const mps = Math.max(0, Number(val(r, 'mins_per_start')) || 0);
  const matches = mps > 0 ? Math.min(MATCHES, xm / mps) : 0;
  const mpa = matches > 0 ? xm / matches : 0;
  const app = matches * (mpa >= 60 ? sc.app : 1);
  const hit = sc.dc_pts > 0 ? poissonAtLeast(sc.dc_thr, r.defcon_lambda * mps / 90) : 0;
  const dc = matches * hit * sc.dc_pts;
  return {matches, app, hit, dc, xpts: r.rate_p90 * xm / 90 + app + dc + r.pen_pts};
}

function recompute(){
  DATA.forEach(r => {
    const s = scoreOne(r);
    r._matches = s.matches; r._hit = s.hit * 100; r._xpts = s.xpts;
    const xm = Math.max(0, Number(val(r, 'xmins_adj')) || 0);
    r._xg = r.xg_p90 * xm / 90; r._xa = r.xa_p90 * xm / 90;
    r._adp = Number(val(r, 'adp'));
    if (!Number.isFinite(r._adp)) r._adp = null;
    r._role = val(r, 'role'); r._threat = val(r, 'threat');
  });
  // Replacement level: the (teams x slots + 1)-th best at each position among
  // players registered for 2026/27.  It moves when you edit minutes, so VORP
  // has to be recomputed here rather than shipped.
  const repl = {};
  for (const [pos, slots] of Object.entries(SLOTS)){
    const pool = DATA.filter(r => r.draftable && r.pos === pos)
                     .map(r => r._xpts).sort((a, b) => b - a);
    const idx = draft.teams * slots;
    repl[pos] = pool.length > idx ? pool[idx] : (pool.length ? pool[pool.length - 1] : 0);
  }
  DATA.forEach(r => { r._vorp = (r.pos in repl) ? r._xpts - repl[r.pos] : null; });
  bands();
}

/* ---------- snake draft: which pick numbers are mine ---------- */
function myPicks(){
  const out = [];
  for (let r = 1; r <= draft.rounds; r++){
    const inRound = (r % 2) ? draft.slot : (draft.teams - draft.slot + 1);
    out.push((r - 1) * draft.teams + inRound);
  }
  return out;
}

/* For each player, the last of my picks he is still projected to be there for,
   read straight off ADP.  This is arithmetic on other people's draft
   behaviour, not a view about the player. */
function bands(){
  const picks = myPicks();
  DATA.forEach(r => {
    r._band = null;
    if (!r.draftable || !isNum(r._adp)) return;
    let last = -1;
    picks.forEach((p, i) => { if (p < r._adp) last = i; });
    r._band = last;            // -1 = projected gone before my first pick
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
    return true;
  });
}

const SORTED = {matches:'_matches', xpts:'_xpts', vorp:'_vorp', adp:'_adp',
                xg_season:'_xg', xa_season:'_xa', defcon_hit:'_hit',
                role_rank:'_role', threat:'_threat', band:'_band'};

function render(){
  const rows = visible();
  const key = SORTED[sortKey] || sortKey;
  const dir = sortDir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    let x = a[key], y = b[key];
    if (key === '_role'){ x = ROLE_RANK[x] ?? 9; y = ROLE_RANK[y] ?? 9; }
    if (typeof x === 'string' || typeof y === 'string')
      return dir * String(x ?? '').localeCompare(String(y ?? ''));
    if (!isNum(x)) return 1;
    if (!isNum(y)) return -1;
    return dir * (x - y);
  });

  const nEdit = editCount();
  $('#editN').textContent = nEdit ? `${nEdit} edit${nEdit === 1 ? '' : 's'}` : 'no edits';
  $('#resetEdits').disabled = !nEdit;

  $('#rows').innerHTML = rows.map(r => {
    const tags =
      (r.moved ? '<span class="flag moved" title="Changed club this summer -- his xG and xA are rates he produced somewhere else">moved</span>' : '') +
      (r.no_pl_rates ? '<span class="flag norates" title="No Premier League record, so every attacking rate is empty. The minutes are researched; the xG and xA are not missing by accident.">no rates</span>' : '');
    const bandCls = !draft.on || r._band === null ? ''
      : (r._band < 0 ? 'gone' : 'band');
    const bandTag = r._band === null ? '<span class="dot">--</span>'
      : r._band < 0 ? '<span class="bandtag gone">gone</span>'
      : `<span class="bandtag">R${r._band + 1}</span>`;
    const num = (f, dp, v) =>
      `<td class="num ${isEdited(r, f) ? 'edited' : ''}"><input class="edit" inputmode="decimal"
        data-code="${r.code}" data-f="${f}" value="${isNum(v) ? Number(v).toFixed(dp) : ''}"></td>`;
    const sel = (f, v, opts) =>
      `<td class="${isEdited(r, f) ? 'edited' : ''}"><select class="edit" data-code="${r.code}" data-f="${f}">
        ${opts.map(o => `<option value="${o}"${o === v ? ' selected' : ''}>${o}</option>`).join('')}</select></td>`;

    return `<tr data-code="${r.code}" class="${marked.has(r.code) ? 'marked ' : ''}${bandCls}">
      <td class="mark"><button class="markbtn" data-mark="${r.code}"
        title="${marked.has(r.code) ? 'Remove from research list' : 'Add to research list'}"
        aria-label="Research list toggle for ${esc(r.player)}">${marked.has(r.code) ? '★' : '☆'}</button></td>
      <td class="name" draggable="true" data-drag="${r.code}">${esc(r.player)}${tags}</td>
      <td>${r.team}</td>
      <td><span class="pos">${r.pos}</span></td>
      <td class="num">${fmt(r.price, 1)}</td>
      ${sel('role', r._role, ROLE_OPTS)}
      ${sel('threat', r._threat, THREAT_OPTS)}
      <td class="num">${fmt(r.xmins_solio, 0)}</td>
      ${num('xmins_adj', 0, val(r, 'xmins_adj'))}
      <td class="num">${(() => { const d = r.research_delta;
        if (!isNum(d)) return '<span class="dot">--</span>';
        return `<span class="delta ${d > 0 ? 'up' : d < 0 ? 'down' : ''}">${d > 0 ? '+' : ''}${Math.round(d)}</span>`; })()}</td>
      <td class="conf">${r.confidence || '<span class="dot">--</span>'}</td>
      ${num('mins_per_start', 1, val(r, 'mins_per_start'))}
      <td class="num">${fmt(r._matches, 1)}</td>
      <td class="num">${fmt(r._xg, 1)}</td>
      <td class="num">${fmt(r._xa, 1)}</td>
      <td class="num">${isNum(r.corner_share) ? Number(r.corner_share).toFixed(0) + '%' : '<span class="dot">--</span>'}</td>
      <td class="dim">${r.corner_role || '<span class="dot">--</span>'}</td>
      <td class="dim">${r.fk_role || '<span class="dot">--</span>'}</td>
      <td class="dim">${r.pen_role || '<span class="dot">--</span>'}</td>
      <td class="num">${fmt(r.pens, 2)}</td>
      <td class="num">${fmt(r._hit, 1)}</td>
      <td class="num">${fmt(r.games_missed, 1)}</td>
      <td class="num">${r.adr_pros === null || r.adr_pros === undefined ? '<span class="dot">--</span>' : Number(r.adr_pros).toFixed(1)}</td>
      ${num('adp', 1, r._adp)}
      <td class="num">${bandTag}</td>
      <td class="num">${fmt(r._xpts, 1)}</td>
      <td class="num">${fmt(r._vorp, 1)}</td>
    </tr>`;
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
      setVal(Number(el.dataset.code), el.dataset.f, el.value, true));
    el.addEventListener('keydown', e => { if (e.key === 'Enter') el.blur(); });
  });
  document.querySelectorAll('select.edit').forEach(el =>
    el.addEventListener('change', () =>
      setVal(Number(el.dataset.code), el.dataset.f, el.value, false)));
  document.querySelectorAll('td.name[data-drag]').forEach(el =>
    el.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', el.dataset.drag);
      e.dataTransfer.effectAllowed = 'copy';
    }));
}

/* ---------- sidebar ---------- */
function cardHtml(r, inTray){
  const rates = r.no_pl_rates ? '<span class="flag norates">no rates</span>' : '';
  return `<div class="card" draggable="true" data-drag="${r.code}" data-card="${r.code}">
    <div class="top"><span class="who">${esc(r.player)}</span>
      <span class="meta">${r.pos} ${r.team}</span>
      ${inTray ? `<button class="x" data-remove="${r.code}" aria-label="Remove ${esc(r.player)}">&times;</button>` : ''}
    </div>
    <div class="row2">${r.club_2526 ? esc(r.club_2526) + ' → ' : ''}${r.team}
      &middot; ${isNum(val(r, 'xmins_adj')) ? Math.round(val(r, 'xmins_adj')) : '--'} mins ${rates}</div>
  </div>`;
}

function renderSide(){
  const q = $('#sideq').value.trim().toLowerCase();
  const inTray = new Set(tray);
  const movers = DATA
    .filter(r => (r.moved || r.no_pl_rates) && !inTray.has(r.code))
    .filter(r => !q || (r.player + ' ' + r.team + ' ' + r.club_2526).toLowerCase().includes(q))
    .sort((a, b) => (Number(val(b, 'xmins_adj')) || 0) - (Number(val(a, 'xmins_adj')) || 0));
  $('#movers').innerHTML = movers.map(r => cardHtml(r, false)).join('')
    || '<div class="empty">Nothing matches.</div>';
  $('#moversN').textContent = movers.length;

  $('#trayBody').innerHTML = tray.length
    ? tray.map(c => cardHtml(byCode.get(c), true)).join('')
    : '<div class="empty">Drag anyone here, from this list or from the table, and drop to place them in your own order.</div>';
  $('#trayN').textContent = tray.length;

  const list = DATA.filter(r => marked.has(r.code));
  $('#markedBody').innerHTML = list.length
    ? list.map(r => `<div class="card"><div class="top"><span class="who">${esc(r.player)}</span>
        <span class="meta">${r.pos} ${r.team}</span>
        <button class="x" data-unmark="${r.code}" aria-label="Remove ${esc(r.player)} from the research list">&times;</button></div></div>`).join('')
    : '<div class="empty">Nothing here yet. Click the star at the left of any row to add a player.</div>';
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
      save(); render(); renderSide();
    }));
}

const trayEl = $('#tray');
trayEl.addEventListener('dragover', e => { e.preventDefault(); trayEl.classList.add('over'); });
trayEl.addEventListener('dragleave', () => trayEl.classList.remove('over'));
trayEl.addEventListener('drop', e => {
  e.preventDefault();
  trayEl.classList.remove('over');
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
    .map(r => `${r.player} (${r.pos}, ${r.team}) -- ${r._role}${r._threat !== 'none' ? ', ' + r._threat + ' risk' : ''}`)
    .join('\n')));

/* ---------- controls ---------- */
document.querySelectorAll('.headrow button').forEach(b =>
  b.addEventListener('click', () => {
    const k = b.dataset.k;
    if (sortKey === k) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
    else { sortKey = k;
           sortDir = ['player','team','pos','role_rank','threat','corner_role','fk_role','pen_role','adp','band','games_missed','adr_pros'].includes(k) ? 'asc' : 'desc'; }
    document.querySelectorAll('.headrow button').forEach(o => o.removeAttribute('data-dir'));
    b.setAttribute('data-dir', sortDir);
    render();
  }));
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
  $('#togglePanels').setAttribute('aria-pressed', String(!off));
});
['slot', 'teams'].forEach(f => $('#' + f).addEventListener('change', e => {
  const v = Number(e.target.value);
  if (Number.isFinite(v) && v > 0) draft[f] = v;
  if (draft.slot > draft.teams) { draft.slot = draft.teams; $('#slot').value = draft.slot; }
  $('#slot').max = draft.teams;
  save(); recompute(); render(); renderSide();
}));
$('#bandsOn').addEventListener('click', e => {
  draft.on = !draft.on;
  e.target.setAttribute('aria-pressed', String(draft.on));
  save(); render();
});
$('#resetEdits').addEventListener('click', () => {
  if (!confirm('Discard every edit and go back to the model\'s own numbers?')) return;
  edits = {}; save(); recompute(); render(); renderSide();
});

$('#slot').value = draft.slot; $('#teams').value = draft.teams;
$('#slot').max = draft.teams;
$('#bandsOn').setAttribute('aria-pressed', String(draft.on));
recompute(); render(); renderSide();
"""

GROUPS = [("", 1), ("Player", 4), ("Role", 2), ("Minutes", 6),
          ("Attacking, season", 2), ("Set pieces", 5), ("Other levers", 3),
          ("Draft", 2), ("Model", 2)]

HEADERS = [
    ("", "", False),
    ("player", "Player", False), ("team", "Team", False), ("pos", "Pos", False),
    ("price", "&pound;m", True),
    ("role_rank", "Role", False), ("threat", "Transfer threat", False),
    ("xmins_solio", "xMins Solio", True), ("xmins_adj", "xMins adjusted", True),
    ("research_delta", "Delta", True), ("confidence", "Conf", False),
    ("mins_per_start", "Mins/start", True), ("matches", "Matches", True),
    ("xg_season", "xG", True), ("xa_season", "xA", True),
    ("corner_share", "Corners", True), ("corner_order", "Cnr role", False),
    ("fk_order", "Direct FK", False), ("pen_order", "Pens", False),
    ("pens", "Pens exp", True),
    ("defcon_hit", "DefCon %", True), ("games_missed", "Games lost", True),
    ("adr_pros", "ADR Pros", True),
    ("adp", "ADP", True), ("band", "There at", True),
    ("xpts", "xPts", True), ("vorp", "VORP", True),
]


def html(df: pd.DataFrame) -> str:
    teams = sorted(t for t in df["team"].dropna().unique() if t != "--")
    opts = "".join(f'<option value="{t}">{t}</option>' for t in teams)

    gcells, i = [], 0
    for name, span in GROUPS:
        cls = ' class="g"' if i and name else ""
        gcells.append(f'<th colspan="{span}"{cls}>{name}</th>')
        i += span
    hcells = []
    for key, label, num in HEADERS:
        if not key:
            hcells.append('<th class="mark"></th>')
            continue
        cls = "pname" if key == "player" else ("num" if num else "")
        d = ' data-dir="desc"' if key == "xpts" else ""
        hcells.append(f'<th class="{cls}"><button data-k="{key}"{d}>{label}</button></th>')

    records = df.replace({np.nan: None}).to_dict("records")
    for r in records:
        for k, v in r.items():
            if isinstance(v, (np.integer, np.floating)):
                r[k] = None if pd.isna(v) else float(v)
            elif isinstance(v, (np.bool_, bool)):
                r[k] = bool(v)
    data = json.dumps(records, ensure_ascii=True)

    scoring = {pos: {"app": v[4], "dc_thr": v[5], "dc_pts": v[3]}
               for pos, v in xpts_calc.SCORING.items()}
    consts = (f"const SCORING={json.dumps(scoring)};"
              f"const SLOTS={json.dumps(xpts_model.SQUAD_SLOTS)};"
              f"const MATCHES={xpts_calc.MATCHES};"
              f"const ROLE_RANK={json.dumps({**ROLE_RANK, 'rotation': 2.5})};"
              f"const ROLE_OPTS={json.dumps(sorted(set(list(ROLE_RANK) + ['rotation'])))};"
              f"const THREAT_OPTS={json.dumps(['none', 'reinforcement', 'exit'])};")

    n_moved = int((df.moved | df.no_pl_rates).sum())
    n_adr = int(df.adr_pros.notna().sum())
    adr_note = (f"{n_adr} players carry one." if n_adr else
                "No published draft rankings were found for 2026/27, so the "
                "column is empty rather than filled with a guess.")
    return f"""<meta charset="utf-8">
<title>Draft Levers</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="stamp">FPL 2026/27 &middot; {len(df)} players &middot; research to 16 Aug 2026</p>
  <h1>Every lever, one table</h1>
  <p class="sub">The inputs that move a projection, side by side &mdash; and
  every one of them editable. Change a player's minutes and his matches, xG,
  DefCon, xPts and VORP all move with it, through the same model the workbook
  uses. Nothing you type leaves this page.</p>
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
  </div>
  <span class="count" id="count"></span>
</div>

<div class="controls">
  <label class="picks">My draft slot
    <input type="number" id="slot" min="1" max="8" step="1" style="width:60px">
  </label>
  <label class="picks">Teams
    <input type="number" id="teams" min="2" max="20" step="1" style="width:60px">
  </label>
  <button class="chip" id="bandsOn" aria-pressed="true">Shade by draft band</button>
  <span class="picks">My picks: <span id="pickList"></span></span>
  <span class="count"><span id="editN"></span>
    <button class="chip" id="resetEdits" style="margin-left:8px">Reset edits</button>
    <button class="chip" id="togglePanels" aria-pressed="true" style="margin-left:6px">Hide panels</button>
  </span>
</div>

<div class="legend">
  <span><span class="swatch" style="background:var(--input)"></span><b>Blue cells</b> are yours to type in</span>
  <span><span class="swatch" style="background:var(--editd)"></span>an edit you have made</span>
  <span><b>&#9733;</b> adds a player to the research list in the right-hand panel</span>
  <span><span class="swatch" style="background:var(--bandc)"></span><b>There at</b> = the last of your picks he is projected to survive to, from ADP</span>
</div>

<div class="layout" id="layout">
  <div class="main">
    <div class="tablebox">
      <table>
        <thead>
          <tr class="grouprow">{"".join(gcells)}</tr>
          <tr class="headrow">{"".join(hcells)}</tr>
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
      <p class="hint">Everyone who changed club, plus anyone with no Premier
      League record at all. These are the {n_moved} players whose attacking
      rates the data cannot supply.</p>
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
      <p class="hint">Players you starred as needing a closer look. Copy the
      list out and it comes with each man's role and transfer risk.</p>
    </section>
  </aside>
</div>

<details>
  <summary>What each column is, what you can edit, and where the data runs out</summary>
  <h3>Editing</h3>
  <p>Five things are editable: <strong>xMins adjusted</strong>,
  <strong>Mins/start</strong>, <strong>Role</strong>, <strong>Transfer
  threat</strong> and <strong>ADP</strong>. The first two re-score the player
  on the spot &mdash; matches, xG, xA, DefCon %, xPts and VORP all move, using
  the same arithmetic as <code>xpts_calc.py</code>, not an approximation of it.
  Editing ADP moves him in the draft bands. Edits are saved in this browser,
  marked in a darker blue, filterable with <em>Edited by me</em>, and thrown
  away by <em>Reset edits</em>. Typing a value back to what it was clears the
  edit rather than recording it.</p>
  <p>VORP recomputes too, which matters: replacement level is the
  (teams &times; slots + 1)-th best player at that position, so raising one
  man's minutes can lower everyone else's VORP at his position.</p>

  <h3>Draft slot and bands</h3>
  <p>Set your slot and the league size and <strong>My picks</strong> lists your
  overall pick numbers in an 8-team snake. <strong>There at</strong> is the
  last of those picks a player is still projected to be available at, read
  straight off ADP &mdash; <code>R3</code> means the market has him going after
  your third pick but before your fourth, so your third is the last one where
  he is likely there. <code>gone</code> means his ADP is earlier than your
  first pick. Edit an ADP and the bands move.</p>
  <p>This is arithmetic on other people's draft behaviour, not a view about any
  player, and it does not choose anyone for you.</p>

  <h3>Role and transfer threat</h3>
  <p><strong>Role</strong> comes from the club research, which named a starter
  and his competition for every position at 17 clubs. <code>nailed</code> and
  <code>likely</code> are the researcher's words for the starter;
  <code>contested</code> means the slot is genuinely in the fight;
  <code>rotation</code> means he was listed as competition for someone else's
  place; <code>unknown</code> means the research did not reach him. Hover a row
  for the sourced note. <strong>Transfer threat</strong> is whether the club is
  still shopping for that position (<code>reinforcement</code>, the minutes most
  at risk) or willing to sell him (<code>exit</code>).</p>

  <h3>Set pieces &mdash; and the gap</h3>
  <p><strong>Corners</strong> is the share of his club's 2025/26 corners he
  actually took, measured rather than assumed, and the closest thing available
  to a left/right split: a first-choice taker on
  <code>{int(BOTH_SIDES_SHARE * 100)}%</code> or more is taking both sides.</p>
  <p>Free kicks and penalties can only be an order, never a percentage. Nothing
  in any of the five sources counts free kicks or penalties <em>taken</em>,
  only who is designated, so those columns read Primary, Secondary or Tertiary
  and nothing finer. FPL also publishes a single combined order for
  <em>corners and indirect free kicks</em>, so the indirect free-kick taker
  cannot be told apart from the corner taker at all.</p>

  <h3>ADP and ADR Pros</h3>
  <p><strong>ADP</strong> is average draft position in real public draft
  leagues &mdash; what other managers actually did. <strong>ADR Pros</strong>
  is the average rank given by published draft rankings from named analysts.
  {adr_note}</p>

  <h3>Other levers</h3>
  <p><strong>Matches</strong> is xMins divided by minutes per start, capped at
  38. <strong>DefCon %</strong> is the chance of clearing the
  defensive-contribution threshold in a start. <strong>Games lost</strong> is
  expected games missed to injury from the Transfermarkt history &mdash; 150
  players only, and the level runs about 25% high, so it orders better than it
  sizes.</p>
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
    print(f"  set-piece duty:  {int((df.sp_score > 0).sum())} players")
    print(f"  ADP for {int(df.adp.notna().sum())}, "
          f"ADR Pros for {int(df.adr_pros.notna().sum())}")
    print(f"  summer arrivals: {int((df.moved | df.no_pl_rates).sum())}")
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
