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
        "xpts": s["xpts_season"].round(1),
        "adp": m["adp"],
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

    out = out[out["draftable"] | out["xmins_adj"].fillna(0).gt(0)]
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
const LS_TRAY = 'fpl_tray_v1', LS_MARK = 'fpl_marked_v1';
let tray = load(LS_TRAY, []), marked = new Set(load(LS_MARK, []));
let sortKey = 'xpts', sortDir = 'desc';
const posFilter = new Set(), flags = new Set();

function load(k, d){ try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }
function save(){ try {
  localStorage.setItem(LS_TRAY, JSON.stringify(tray));
  localStorage.setItem(LS_MARK, JSON.stringify([...marked]));
} catch {} }

const fmt = (v, dp) => (v === null || v === undefined || Number.isNaN(v))
  ? '<span class="dot">--</span>' : Number(v).toFixed(dp);
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function deltaCell(v){
  if (v === null || v === undefined || Number.isNaN(v)) return '<span class="dot">--</span>';
  const c = v > 0 ? 'up' : v < 0 ? 'down' : '';
  return `<span class="delta ${c}">${v > 0 ? '+' : ''}${Math.round(v)}</span>`;
}
const tag = (v, title) =>
  `<span class="tag ${v}"${title ? ` title="${esc(title)}"` : ''}>${v}</span>`;

/* ---------------- table ---------------- */
function visible(){
  const q = $('#q').value.trim().toLowerCase();
  const team = $('#team').value;
  return DATA.filter(r => {
    if (q && !(r.player + ' ' + r.team).toLowerCase().includes(q)) return false;
    if (team && r.team !== team) return false;
    if (posFilter.size && !posFilter.has(r.pos)) return false;
    if (flags.has('researched') && r.research_delta === null) return false;
    if (flags.has('setpiece') && !r.sp_score) return false;
    if (flags.has('pens') && !r.pen_order) return false;
    if (flags.has('threat') && r.threat === 'none') return false;
    if (flags.has('marked') && !marked.has(r.code)) return false;
    return true;
  });
}

function render(){
  const rows = visible();
  const dir = sortDir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (typeof x === 'string' || typeof y === 'string')
      return dir * String(x ?? '').localeCompare(String(y ?? ''));
    if (x === null || x === undefined || Number.isNaN(x)) return 1;
    if (y === null || y === undefined || Number.isNaN(y)) return -1;
    return dir * (x - y);
  });

  $('#rows').innerHTML = rows.map(r => {
    const tags =
      (r.moved ? '<span class="flag moved" title="Changed club this summer -- his xG and xA are rates he produced somewhere else">moved</span>' : '') +
      (r.no_pl_rates ? '<span class="flag norates" title="No Premier League record, so every attacking rate is empty. The minutes are researched; the xG and xA are not missing by accident.">no rates</span>' : '');
    return `<tr data-code="${r.code}" class="${marked.has(r.code) ? 'marked' : ''}">
      <td class="mark"><button class="markbtn" data-mark="${r.code}" title="Mark for extra research"
        aria-label="Mark ${esc(r.player)} for extra research">${marked.has(r.code) ? '★' : '☆'}</button></td>
      <td class="name" draggable="true" data-drag="${r.code}">${esc(r.player)}${tags}</td>
      <td>${r.team}</td>
      <td><span class="pos">${r.pos}</span></td>
      <td class="num">${fmt(r.price, 1)}</td>
      <td>${tag(r.role, r.role_note || r.role_slot)}</td>
      <td>${tag(r.threat, r.threat_note || r.threat_slot)}</td>
      <td class="num">${fmt(r.xmins_solio, 0)}</td>
      <td class="num">${fmt(r.xmins_adj, 0)}</td>
      <td class="num">${deltaCell(r.research_delta)}</td>
      <td class="conf">${r.confidence || '<span class="dot">--</span>'}</td>
      <td class="num">${fmt(r.mins_per_start, 1)}</td>
      <td class="num">${fmt(r.xg_season, 1)}</td>
      <td class="num">${fmt(r.xa_season, 1)}</td>
      <td class="num">${r.corner_share === null ? '<span class="dot">--</span>' : fmt(r.corner_share, 0) + '%'}</td>
      <td class="dim">${r.corner_role || '<span class="dot">--</span>'}</td>
      <td class="dim">${r.fk_role || '<span class="dot">--</span>'}</td>
      <td class="dim">${r.pen_role || '<span class="dot">--</span>'}</td>
      <td class="num">${fmt(r.pens, 2)}</td>
      <td class="num">${fmt(r.defcon_hit, 1)}</td>
      <td class="num">${fmt(r.games_missed, 1)}</td>
      <td class="num">${fmt(r.adp, 1)}</td>
      <td class="num">${fmt(r.xpts, 1)}</td>
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
  document.querySelectorAll('[data-drag]').forEach(el => {
    el.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', el.dataset.drag);
      e.dataTransfer.effectAllowed = 'copy';
    });
  });
}

/* ---------------- sidebar ---------------- */
function cardHtml(r, inTray){
  const rates = r.no_pl_rates ? '<span class="flag norates">no rates</span>' : '';
  return `<div class="card" draggable="true" data-drag="${r.code}" data-card="${r.code}">
    <div class="top"><span class="who">${esc(r.player)}</span>
      <span class="meta">${r.pos} ${r.team}</span>
      ${inTray ? `<button class="x" data-remove="${r.code}" aria-label="Remove ${esc(r.player)}">&times;</button>` : ''}
    </div>
    <div class="row2">${r.club_2526 ? esc(r.club_2526) + ' → ' : ''}${r.team}
      &middot; ${r.xmins_adj === null ? '--' : Math.round(r.xmins_adj)} mins ${rates}</div>
  </div>`;
}

function renderSide(){
  const q = $('#sideq').value.trim().toLowerCase();
  const inTray = new Set(tray);
  const movers = DATA
    .filter(r => (r.moved || r.no_pl_rates) && !inTray.has(r.code))
    .filter(r => !q || (r.player + ' ' + r.team + ' ' + r.club_2526).toLowerCase().includes(q))
    .sort((a, b) => (b.xmins_adj ?? 0) - (a.xmins_adj ?? 0));
  $('#movers').innerHTML = movers.map(r => cardHtml(r, false)).join('')
    || '<div class="empty">Nothing matches.</div>';
  $('#moversN').textContent = movers.length;

  $('#trayBody').innerHTML = tray.length
    ? tray.map(c => cardHtml(byCode.get(c), true)).join('')
    : '<div class="empty">Drag anyone here &mdash; from this list or from the table &mdash; and drop to place them in your own order.</div>';
  $('#trayN').textContent = tray.length;

  const list = DATA.filter(r => marked.has(r.code));
  $('#markedBody').innerHTML = list.length
    ? list.map(r => `<div class="card"><div class="top"><span class="who">${esc(r.player)}</span>
        <span class="meta">${r.pos} ${r.team}</span>
        <button class="x" data-unmark="${r.code}" aria-label="Unmark ${esc(r.player)}">&times;</button></div></div>`).join('')
    : '<div class="empty">Nothing marked yet. Use the star in the table.</div>';
  $('#markedN').textContent = list.length;
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
  // Drop position follows the cursor, so the tray is reorderable by dragging
  // a card that is already in it.
  const cards = [...$('#trayBody').querySelectorAll('[data-card]')];
  let at = cards.findIndex(c => e.clientY < c.getBoundingClientRect().top + c.offsetHeight / 2);
  tray = tray.filter(c => c !== code);
  if (at < 0) tray.push(code); else tray.splice(at, 0, code);
  save(); renderSide();
});

$('#copyTray').addEventListener('click', () => {
  const txt = tray.map((c, i) => {
    const r = byCode.get(c);
    return `${i + 1}. ${r.player} (${r.pos}, ${r.team})`;
  }).join('\n');
  navigator.clipboard?.writeText(txt);
  $('#copyTray').textContent = 'Copied';
  setTimeout(() => { $('#copyTray').textContent = 'Copy list'; }, 1400);
});
$('#clearTray').addEventListener('click', () => { tray = []; save(); renderSide(); });
$('#copyMarked').addEventListener('click', () => {
  const txt = DATA.filter(r => marked.has(r.code))
    .map(r => `${r.player} (${r.pos}, ${r.team}) -- ${r.role}${r.threat !== 'none' ? ', ' + r.threat + ' risk' : ''}`)
    .join('\n');
  navigator.clipboard?.writeText(txt);
  $('#copyMarked').textContent = 'Copied';
  setTimeout(() => { $('#copyMarked').textContent = 'Copy list'; }, 1400);
});

/* ---------------- controls ---------------- */
document.querySelectorAll('.headrow button').forEach(b =>
  b.addEventListener('click', () => {
    const k = b.dataset.k;
    if (sortKey === k) sortDir = sortDir === 'desc' ? 'asc' : 'desc';
    else { sortKey = k; sortDir = ['player','team','pos','role','threat','corner_role','fk_role','pen_role'].includes(k) ? 'asc' : 'desc'; }
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
$('#toggleSide').addEventListener('click', () => {
  const l = $('#layout'), off = l.classList.toggle('no-side');
  $('#toggleSide').setAttribute('aria-pressed', String(!off));
});

render(); renderSide();
"""

GROUPS = [("", 1), ("Player", 4), ("Role", 2), ("Minutes", 5),
          ("Attacking, season", 2), ("Set pieces", 5), ("Other levers", 3),
          ("Model", 1)]

HEADERS = [
    ("", "", False),
    ("player", "Player", False), ("team", "Team", False), ("pos", "Pos", False),
    ("price", "&pound;m", True),
    ("role_rank", "Role", False), ("threat", "Transfer threat", False),
    ("xmins_solio", "xMins Solio", True), ("xmins_adj", "xMins adjusted", True),
    ("research_delta", "Delta", True), ("confidence", "Conf", False),
    ("mins_per_start", "Mins/start", True),
    ("xg_season", "xG", True), ("xa_season", "xA", True),
    ("corner_share", "Corners", True), ("corner_order", "Cnr role", False),
    ("fk_order", "Direct FK", False), ("pen_order", "Pens", False),
    ("pens", "Pens exp", True),
    ("defcon_hit", "DefCon %", True), ("games_missed", "Games lost", True),
    ("adp", "ADP", True),
    ("xpts", "xPts", True),
]


def html(df: pd.DataFrame) -> str:
    teams = sorted(t for t in df["team"].dropna().unique() if t != "--")
    opts = "".join(f'<option value="{t}">{t}</option>' for t in teams)

    gcells, i = [], 0
    for name, span in GROUPS:
        gcells.append(f'<th colspan="{span}"{" class=\"g\"" if i and name else ""}>{name}</th>')
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

    n_moved = int((df.moved | df.no_pl_rates).sum())
    return f"""<meta charset="utf-8">
<title>Draft Levers</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="stamp">FPL 2026/27 &middot; {len(df)} players &middot; research to 16 Aug 2026</p>
  <h1>Every lever, one table</h1>
  <p class="sub">The inputs that move a projection, side by side: what Solio
  forecast, what the club-by-club research changed it to, whether the man is
  actually nailed, whether his club is still shopping for someone to replace
  him, and who takes the set pieces. Sort any column. Star anyone you want to
  look at again. Drag the summer arrivals into the tray and put them in
  whatever order you think they belong &mdash; that part is yours.</p>
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
  <div class="chips" role="group" aria-label="Filter by attribute">
    <button class="chip" data-flag="researched" aria-pressed="false">Research moved</button>
    <button class="chip" data-flag="threat" aria-pressed="false">Transfer threat</button>
    <button class="chip" data-flag="setpiece" aria-pressed="false">Set-piece duty</button>
    <button class="chip" data-flag="pens" aria-pressed="false">On penalties</button>
    <button class="chip markchip" data-flag="marked" aria-pressed="false">Marked</button>
  </div>
  <button class="chip" id="toggleSide" aria-pressed="true">Sidebar</button>
  <span class="count" id="count"></span>
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
      rates the data cannot supply &mdash; the model gives them minutes and
      nothing else.</p>
    </section>

    <section class="panel tray" id="tray">
      <h2>Your tray <span class="n" id="trayN"></span></h2>
      <div class="body" id="trayBody"></div>
      <div class="act">
        <button id="copyTray">Copy list</button>
        <button id="clearTray">Clear</button>
      </div>
      <p class="hint">Drop anyone here and drag to reorder. Saved in this
      browser only &mdash; nothing leaves the page.</p>
    </section>

    <section class="panel">
      <h2>Marked for research <span class="n" id="markedN"></span></h2>
      <div class="body" id="markedBody"></div>
      <div class="act"><button id="copyMarked">Copy list</button></div>
    </section>
  </aside>
</div>

<details>
  <summary>What each column is, and where the data runs out</summary>
  <h3>Role and transfer threat</h3>
  <p><strong>Role</strong> comes from the club research, which named a starter
  and his competition for every position at 17 clubs. <code>nailed</code> and
  <code>likely</code> are the researcher's own words for the starter;
  <code>contested</code> means the slot is genuinely in the fight;
  <code>rotation</code> means he was listed as competition for someone else's
  place. <code>unknown</code> means the research did not reach him &mdash;
  three clubs were never covered, and no promoted side got the deep pass.
  Hover any tag for the sourced note behind it.</p>
  <p><strong>Transfer threat</strong> is whether his club is still actively
  shopping. <code>reinforcement</code> means the research found real reporting
  that they want a player for that position, which is the minutes most at
  risk. <code>exit</code> means the club is willing to sell <em>him</em>, which
  moves minutes just as much but in the other direction.</p>

  <h3>Minutes</h3>
  <p><strong>xMins Solio</strong> is the raw forecast; <strong>xMins
  adjusted</strong> is that figure after the research moved it, and is what the
  model scores. <strong>Mins/start</strong> is how long a start lasts, shrunk
  toward the position mean by 10 starts. At a fixed xMins a lower figure means
  more matches, so an early hook costs you through xMins, not here.</p>

  <h3>Attacking</h3>
  <p><strong>xG</strong> and <strong>xA</strong> are season totals &mdash; the
  per-90 rate times the adjusted minutes &mdash; so they move when the minutes
  do. A <span class="flag norates">no rates</span> tag means no Premier League
  record at all, so both read zero for a reason that has nothing to do with the
  player.</p>

  <h3>Set pieces &mdash; and the gap</h3>
  <p><strong>Corners</strong> is the share of his club's 2025/26 corners he
  actually took, which is measured rather than assumed, and is the closest
  thing available to a left/right split: a first-choice taker on
  <code>{int(BOTH_SIDES_SHARE * 100)}%</code> or more is taking both sides.
  <strong>Cnr role</strong> is FPL's published order for 2026/27.</p>
  <p>Free kicks and penalties can only be an order, never a percentage.
  Nothing in any of the five sources counts free kicks or penalties
  <em>taken</em> &mdash; only who is designated to take them &mdash; so
  <strong>Direct FK</strong> and <strong>Pens</strong> read Primary, Secondary
  or Tertiary and nothing finer. One further limit: FPL publishes a single
  combined order for <em>corners and indirect free kicks</em>, so the indirect
  free-kick taker cannot be told apart from the corner taker at all.
  <strong>Pens exp</strong> is the modelled number: club penalties won, times
  his share, times his availability.</p>

  <h3>Other levers</h3>
  <p><strong>DefCon %</strong> is the chance of clearing the defensive-contribution
  threshold in a start. <strong>Games lost</strong> is expected games missed to
  injury from the Transfermarkt history &mdash; 150 players only, and the level
  runs about 25% high, so it orders better than it sizes. <strong>ADP</strong>
  is average draft position in real public draft leagues: the only column here
  that tells you what everyone else thinks.</p>
</details>

<footer>Generated by <code>scripts/levers_report.py</code> from the same master
the workbook is built from. The research is a dated snapshot to 16 August 2026;
it knows nothing after that, and it cannot see January.</footer>
</div>
<script>const DATA={data};{JS}</script>
"""


def main() -> int:
    df = assemble()
    path = OUT / "draft_levers.html"
    path.write_text(html(df), encoding="utf-8")

    print(f"draft_levers.html      {len(df)} players")
    print("  role:  ", df.role.value_counts().to_dict())
    print("  threat:", df.threat.value_counts().to_dict())
    print(f"  set-piece duty:  {int((df.sp_score > 0).sum())} players")
    print(f"  corner share known for {int(df.corner_share.notna().sum())}")
    print(f"  summer arrivals: {int((df.moved | df.no_pl_rates).sum())}")
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
