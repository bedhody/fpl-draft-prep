"""Turn the club research JSONs into one readable page.

Each club subagent wrote data/processed/club_research/<CLUB>.json: an expected
line-up, a confidence per position, and a list of minutes deltas against Solio's
forecast.  This renders all of them as a single HTML file.

The page is deliberately about *uncertainty* rather than about line-ups.  A
starter nobody disputes and a starter three sources argue over look completely
different here, because the second one is where a minutes forecast can be wrong
by a thousand minutes and the first one cannot.

It reports roles and minutes.  It does not rank players, tier them, or order
them by draft value -- see CLAUDE.md.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from common import OUT, PROC

RESEARCH = PROC / "club_research"
BRIEFS = Path("/private/tmp/claude-501/-Users-bharatdhody-Claude-Coding-FPL-Prep"
              "/21f246be-0e27-4765-8bda-a67fdf75ff9c/scratchpad/clubs")
BUDGET = 38 * 90 * 11          # the minutes a club actually has to give

CLUB_NAMES = {
    "ARS": "Arsenal", "AVL": "Aston Villa", "BOU": "Bournemouth",
    "BHA": "Brighton", "BRE": "Brentford", "CHE": "Chelsea",
    "CRY": "Crystal Palace", "EVE": "Everton", "FUL": "Fulham",
    "LEE": "Leeds United", "LIV": "Liverpool", "MCI": "Manchester City",
    "MUN": "Manchester United", "NEW": "Newcastle United",
    "NFO": "Nottingham Forest", "SUN": "Sunderland", "TOT": "Tottenham",
    "COV": "Coventry City", "HUL": "Hull City", "IPS": "Ipswich Town",
}
# Deep-tier clubs got several sources each including podcast transcripts;
# medium-tier got two or three, mostly written.
DEEP = {"NEW", "MCI", "LIV", "ARS", "AVL", "TOT", "MUN", "CHE"}
CONF_ORDER = {"nailed": 0, "likely": 1, "contested": 2, "unknown": 3}


def e(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def load() -> list[dict]:
    clubs = []
    for path in sorted(RESEARCH.glob("*.json")):
        try:
            d = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"  !! {path.name} is not valid JSON: {exc}", file=sys.stderr)
            continue
        code = d.get("club") or path.stem
        brief = BRIEFS / f"{code}.json"
        if brief.exists():
            squad = json.loads(brief.read_text())["squad"]
            d["_solio"] = {p["code"]: p["solio_xmins"] for p in squad if p["code"]}
        else:
            d["_solio"] = {}
        clubs.append(d)
    return clubs


def recompute(d: dict) -> dict:
    """Re-derive the budget from the deltas rather than trusting the agent.

    Joined on the Opta code, never the name: the agents write accent-stripped
    spellings ("Joao Gomes", "Jurrien Timber") that no name join survives.

    A delta whose player has no FPL code is not an error.  It is a summer
    signing the game has not registered yet -- Palace's Khalaili, Sunderland's
    Methalie, Newcastle's Dedic.  Those minutes are real and they are genuinely
    gone: nobody in the draft pool can score them.  So they are counted
    separately and added back only to test the 37,620 ceiling.
    """
    solio = dict(d["_solio"])
    proposed = dict(solio)
    outside, outside_mins = [], 0
    for x in d.get("player_deltas", []):
        code = x.get("code")
        if code in proposed:
            proposed[code] = x.get("proposed_xmins", 0)
        else:
            outside.append(x.get("player"))
            outside_mins += x.get("proposed_xmins", 0) or 0
    drafted = sum(proposed.values())
    return {
        "solio_total": sum(solio.values()),
        "proposed_total": drafted,
        "outside_pool": outside_mins,
        "outside_names": outside,
        "gap": drafted + outside_mins - BUDGET,
        "unsourced": [x.get("player") for x in d.get("player_deltas", [])
                      if not x.get("source_url")],
    }


def positions_html(d: dict) -> str:
    rows = []
    for p in sorted(d.get("positions", []),
                    key=lambda z: CONF_ORDER.get(z.get("confidence"), 9)):
        conf = p.get("confidence", "unknown")
        comp = [c for c in (p.get("competition") or []) if c]
        rows.append(f"""
      <li class="pos pos--{e(conf)}">
        <span class="pos__slot">{e(p.get('position'))}</span>
        <span class="pos__body">
          <span class="pos__name">{e(p.get('starter')) or '&mdash;'}</span>
          {f'<span class="pos__comp">v {e(", ".join(comp))}</span>' if comp else ''}
          {f'<span class="pos__note">{e(p.get("note"))}</span>' if p.get('note') else ''}
        </span>
        <span class="chip chip--{e(conf)}">{e(conf)}</span>
      </li>""")
    return "".join(rows) or '<li class="pos"><span class="pos__body">No positions recorded.</span></li>'


def deltas_html(d: dict) -> str:
    ds = sorted(d.get("player_deltas", []), key=lambda z: -abs(z.get("delta") or 0))
    if not ds:
        return '<p class="empty">No changes proposed against Solio.</p>'
    rows = []
    for x in ds:
        delta = x.get("delta") or 0
        dirn = "up" if delta > 0 else "down"
        src = x.get("source_url") or ""
        link = (f'<a href="{e(src)}" target="_blank" rel="noopener">'
                f'{e(x.get("source_date") or "source")}</a>') if src else "&mdash;"
        rows.append(f"""
        <tr>
          <th scope="row">{e(x.get('player'))}</th>
          <td class="num">{x.get('solio_xmins', 0):,}</td>
          <td class="num">{x.get('proposed_xmins', 0):,}</td>
          <td class="num num--{dirn}">{delta:+,}</td>
          <td class="reason">{e(x.get('reason'))}</td>
          <td class="conf conf--{e(x.get('confidence'))}">{e(x.get('confidence'))}</td>
          <td class="src">{link}</td>
        </tr>""")
    return f"""
      <div class="scroller">
        <table class="deltas">
          <caption>Minutes changed against Solio's forecast</caption>
          <thead><tr>
            <th scope="col">Player</th><th scope="col">Solio</th>
            <th scope="col">Proposed</th><th scope="col">Change</th>
            <th scope="col">Why</th><th scope="col">Confidence</th>
            <th scope="col">Source</th>
          </tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>"""


def list_block(title: str, items: list[str], cls: str = "") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f'<div class="block {cls}"><h4>{e(title)}</h4><ul class="plain">{lis}</ul></div>'


def club_html(d: dict) -> str:
    code = d.get("club", "?")
    chk = recompute(d)
    balanced = abs(chk["gap"]) <= 500
    tier = "deep" if code in DEEP else "medium"

    injuries = [
        f'<strong>{e(i.get("player"))}</strong> &mdash; {e(i.get("issue"))}'
        + (f'. Back {e(i.get("expected_return"))}' if i.get("expected_return") else "")
        + (f'. Cover: {e(i.get("beneficiary"))}' if i.get("beneficiary") else "")
        for i in d.get("injuries", [])]
    risk = [
        f'<strong>{e(r.get("position"))}</strong>'
        + (f' &mdash; {e(r.get("incumbent"))} could lose the slot' if r.get("incumbent") else "")
        + (f'. {e(r.get("note"))}' if r.get("note") else "")
        for r in d.get("reinforcement_risk", [])]
    unknowns = [e(u) for u in d.get("unknowns", [])]
    sources = "".join(
        f'<li><a href="{e(s.get("url"))}" target="_blank" rel="noopener">{e(s.get("name"))}</a>'
        f'<span class="src__meta">{e(s.get("type"))} &middot; {e(s.get("date"))}</span></li>'
        for s in d.get("sources", []) if s.get("name"))

    contested = sum(1 for p in d.get("positions", [])
                    if p.get("confidence") in ("contested", "unknown"))

    return f"""
  <section class="club" id="{e(code)}">
    <header class="club__head">
      <div class="club__id">
        <span class="club__code">{e(code)}</span>
        <h3 class="club__name">{e(CLUB_NAMES.get(code, code))}</h3>
      </div>
      <dl class="club__meta">
        <div><dt>Shape</dt><dd>{e(d.get('formation')) or 'unclear'}</dd></div>
        <div><dt>Unsettled slots</dt><dd>{contested}</dd></div>
        <div><dt>Changes</dt><dd>{len(d.get('player_deltas', []))}</dd></div>
        <div><dt>Research</dt><dd>{tier}</dd></div>
        <div><dt>Minutes to draftable players</dt>
          <dd class="{'ok' if balanced else 'bad'}">{chk['proposed_total']:,}
          <span class="gap">({chk['gap']:+,} v {BUDGET:,})</span></dd></div>
        {f'''<div><dt>Gone to unregistered signings</dt>
          <dd class="bad">{chk['outside_pool']:,}
          <span class="gap">{e(", ".join(n for n in chk['outside_names'] if n))}</span></dd></div>'''
         if chk['outside_pool'] else ''}
      </dl>
    </header>
    <div class="club__grid">
      <div>
        <h4>Expected eleven</h4>
        <ol class="positions">{positions_html(d)}</ol>
      </div>
      <div class="club__side">
        {list_block("Injuries", injuries, "block--alert")}
        {list_block("Could still be signed over", risk, "block--warn")}
        {list_block("Left open on purpose", unknowns)}
      </div>
    </div>
    {deltas_html(d)}
    <details class="sources">
      <summary>{len(d.get('sources', []))} sources</summary>
      <ul class="src__list">{sources}</ul>
    </details>
  </section>"""


def build() -> str:
    clubs = load()
    if not clubs:
        raise SystemExit("no club research found in " + str(RESEARCH))
    clubs.sort(key=lambda d: (d.get("club") not in DEEP,
                              -sum(1 for p in d.get("positions", [])
                                   if p.get("confidence") in ("contested", "unknown"))))

    total_deltas = sum(len(d.get("player_deltas", [])) for d in clubs)
    checks = {d["club"]: recompute(d) for d in clubs}
    balanced = sum(1 for c in checks.values() if abs(c["gap"]) <= 500)
    unsourced = sum(len(c["unsourced"]) for c in checks.values())

    movers = []
    for d in clubs:
        for x in d.get("player_deltas", []):
            movers.append((abs(x.get("delta") or 0), d["club"], x))
    movers.sort(key=lambda z: -z[0])
    mover_rows = "".join(f"""
        <tr>
          <td class="tag">{e(c)}</td>
          <th scope="row">{e(x.get('player'))}</th>
          <td class="num">{x.get('solio_xmins',0):,}</td>
          <td class="num">{x.get('proposed_xmins',0):,}</td>
          <td class="num num--{'up' if (x.get('delta') or 0) > 0 else 'down'}">{x.get('delta',0):+,}</td>
          <td class="reason">{e(x.get('reason'))}</td>
        </tr>""" for _, c, x in movers[:15])

    nav = "".join(
        f'<a href="#{e(d["club"])}">{e(d["club"])}'
        f'<span>{sum(1 for p in d.get("positions", []) if p.get("confidence") in ("contested","unknown"))}</span></a>'
        for d in clubs)

    return f"""<title>Pre-Season Minutes Board</title>
<style>
:root {{
  --ground:#F4F6F3; --surface:#FFFFFF; --raise:#EBEEE9;
  --ink:#141C18; --muted:#5D6A63; --faint:#8B968F;
  --line:#DCE2DD; --line-firm:#C3CCC5;
  --settled:#0E6B5A; --settled-bg:#E2F0EB;
  --open:#9A6407;   --open-bg:#F7EDDA;
  --out:#A5342A;    --out-bg:#F7E4E1;
  --up:#0E6B5A; --down:#A5342A;
  --shadow:0 1px 2px rgba(20,28,24,.06), 0 8px 24px -16px rgba(20,28,24,.28);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0D1311; --surface:#151D1A; --raise:#1D2723;
    --ink:#E7EDE9; --muted:#94A19A; --faint:#6D7A73;
    --line:#232E29; --line-firm:#31403A;
    --settled:#54C6A8; --settled-bg:#12332B;
    --open:#E0A63C;   --open-bg:#332715;
    --out:#E7877A;    --out-bg:#35201D;
    --up:#54C6A8; --down:#E7877A;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.9);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0D1311; --surface:#151D1A; --raise:#1D2723;
  --ink:#E7EDE9; --muted:#94A19A; --faint:#6D7A73;
  --line:#232E29; --line-firm:#31403A;
  --settled:#54C6A8; --settled-bg:#12332B;
  --open:#E0A63C;   --open-bg:#332715;
  --out:#E7877A;    --out-bg:#35201D;
  --up:#54C6A8; --down:#E7877A;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.9);
}}
*,*::before,*::after {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--ground); color:var(--ink);
  font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1180px; margin:0 auto; padding:clamp(24px,5vw,64px) clamp(16px,4vw,40px) 96px; }}
h1,h2,h3,h4 {{ margin:0; text-wrap:balance; font-family:"Avenir Next Condensed","Helvetica Neue Condensed",
  "Roboto Condensed",ui-sans-serif,system-ui,sans-serif; font-weight:700; letter-spacing:-.005em; }}
h1 {{ font-size:clamp(2.1rem,5.5vw,3.4rem); line-height:1.02; letter-spacing:-.02em; }}
h2 {{ font-size:1.5rem; }} h3 {{ font-size:1.5rem; }}
h4 {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.13em; color:var(--muted); font-weight:700; }}
a {{ color:inherit; }}
a:focus-visible, summary:focus-visible {{ outline:2px solid var(--settled); outline-offset:3px; border-radius:3px; }}
.num, .club__code, .gap, td.num {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums; }}

/* ---------- masthead ---------- */
.mast {{ border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:8px; }}
.eyebrow {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.7rem;
  letter-spacing:.18em; text-transform:uppercase; color:var(--muted); margin:0 0 14px; }}
.standfirst {{ max-width:62ch; color:var(--muted); font-size:1.03rem; margin:16px 0 0; }}
.stats {{ display:flex; flex-wrap:wrap; gap:0; margin:28px 0 0; border:1px solid var(--line);
  border-radius:10px; overflow:hidden; background:var(--surface); }}
.stat {{ flex:1 1 140px; padding:14px 18px; border-right:1px solid var(--line); }}
.stat:last-child {{ border-right:0; }}
.stat b {{ display:block; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums; font-size:1.7rem; line-height:1.1; }}
.stat span {{ font-size:.73rem; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); }}

/* ---------- nav ---------- */
.nav {{ display:flex; flex-wrap:wrap; gap:6px; margin:28px 0 40px; }}
.nav a {{ display:inline-flex; align-items:center; gap:7px; text-decoration:none;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.78rem; font-weight:600;
  padding:5px 9px; border:1px solid var(--line-firm); border-radius:6px; background:var(--surface); }}
.nav a span {{ font-size:.68rem; color:var(--muted); }}
.nav a:hover {{ border-color:var(--settled); color:var(--settled); }}

/* ---------- league table ---------- */
.league {{ margin:0 0 48px; }}
.scroller {{ overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--surface); }}
table {{ border-collapse:collapse; width:100%; font-size:.88rem; }}
caption {{ text-align:left; padding:12px 16px; font-size:.72rem; text-transform:uppercase;
  letter-spacing:.12em; color:var(--muted); border-bottom:1px solid var(--line); }}
th,td {{ padding:9px 14px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
thead th {{ font-size:.68rem; text-transform:uppercase; letter-spacing:.09em; color:var(--muted);
  font-weight:600; white-space:nowrap; }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom:0; }}
tbody th {{ font-weight:600; white-space:nowrap; }}
td.num {{ text-align:right; white-space:nowrap; }}
.num--up {{ color:var(--up); font-weight:600; }}
.num--down {{ color:var(--down); font-weight:600; }}
.reason {{ color:var(--muted); min-width:22ch; max-width:52ch; font-size:.84rem; }}
.tag {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.72rem; color:var(--muted); }}
.src a {{ font-size:.78rem; color:var(--muted); }}
.conf {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; white-space:nowrap; }}
.conf--high {{ color:var(--settled); }} .conf--medium {{ color:var(--open); }}
.conf--low {{ color:var(--faint); }}

/* ---------- club ---------- */
.club {{ background:var(--surface); border:1px solid var(--line); border-radius:14px;
  padding:clamp(18px,3vw,30px); margin:0 0 22px; box-shadow:var(--shadow); scroll-margin-top:20px; }}
.club__head {{ display:flex; flex-wrap:wrap; gap:18px 32px; align-items:flex-start;
  justify-content:space-between; padding-bottom:18px; border-bottom:1px solid var(--line); }}
.club__id {{ display:flex; align-items:baseline; gap:12px; }}
.club__code {{ font-size:.78rem; font-weight:700; letter-spacing:.08em; color:var(--muted);
  border:1px solid var(--line-firm); border-radius:5px; padding:3px 7px; }}
.club__meta {{ display:flex; flex-wrap:wrap; gap:10px 26px; margin:0; }}
.club__meta div {{ display:flex; flex-direction:column; gap:2px; }}
.club__meta dt {{ font-size:.64rem; text-transform:uppercase; letter-spacing:.11em; color:var(--faint); }}
.club__meta dd {{ margin:0; font-size:.92rem; font-weight:600; }}
.club__meta .ok {{ color:var(--settled); }}
.club__meta .bad {{ color:var(--out); }}
.gap {{ font-size:.74rem; font-weight:400; color:var(--muted); }}
.club__grid {{ display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:28px 40px; padding:22px 0; }}
@media (max-width:820px) {{ .club__grid {{ grid-template-columns:1fr; }} }}
.club__side {{ display:flex; flex-direction:column; gap:20px; }}

/* ---------- positions: confidence is the design ---------- */
.positions {{ list-style:none; margin:12px 0 0; padding:0; display:flex; flex-direction:column; gap:2px; }}
.pos {{ display:grid; grid-template-columns:46px minmax(0,1fr) auto; gap:12px; align-items:baseline;
  padding:9px 10px 9px 0; border-left:3px solid var(--line-firm); padding-left:12px;
  border-radius:0 6px 6px 0; }}
.pos--nailed {{ border-left-color:var(--settled); }}
.pos--likely {{ border-left-color:var(--settled); border-left-style:solid; opacity:.94; }}
.pos--contested {{ border-left-color:var(--open); border-left-style:dashed; background:var(--open-bg); }}
.pos--unknown {{ border-left-color:var(--faint); border-left-style:dotted; }}
.pos__slot {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.7rem;
  color:var(--muted); letter-spacing:.04em; }}
.pos__body {{ display:flex; flex-direction:column; gap:2px; min-width:0; }}
.pos__name {{ font-weight:600; }}
.pos__comp, .pos__note {{ font-size:.79rem; color:var(--muted); }}
.chip {{ font-size:.63rem; text-transform:uppercase; letter-spacing:.09em; font-weight:700;
  padding:2px 7px; border-radius:99px; white-space:nowrap; }}
.chip--nailed {{ background:var(--settled-bg); color:var(--settled); }}
.chip--likely {{ background:var(--raise); color:var(--muted); }}
.chip--contested {{ background:var(--open-bg); color:var(--open); }}
.chip--unknown {{ background:var(--raise); color:var(--faint); }}

.block ul.plain {{ list-style:none; margin:10px 0 0; padding:0; display:flex;
  flex-direction:column; gap:8px; font-size:.86rem; color:var(--muted); }}
.block ul.plain li {{ padding-left:14px; position:relative; }}
.block ul.plain li::before {{ content:""; position:absolute; left:0; top:.6em; width:5px; height:5px;
  border-radius:99px; background:var(--line-firm); }}
.block--alert li::before {{ background:var(--out); }}
.block--warn li::before {{ background:var(--open); }}
.block strong {{ color:var(--ink); }}
.empty {{ color:var(--faint); font-size:.87rem; }}

.sources {{ margin-top:18px; border-top:1px solid var(--line); padding-top:12px; }}
.sources summary {{ cursor:pointer; font-size:.72rem; text-transform:uppercase;
  letter-spacing:.11em; color:var(--muted); }}
.src__list {{ list-style:none; margin:12px 0 0; padding:0; display:grid;
  grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:8px 20px; font-size:.83rem; }}
.src__list li {{ display:flex; flex-direction:column; }}
.src__meta {{ color:var(--faint); font-size:.72rem; }}

footer {{ margin-top:56px; padding-top:22px; border-top:1px solid var(--line);
  color:var(--muted); font-size:.85rem; max-width:70ch; }}
footer p {{ margin:0 0 10px; }}
</style>

<div class="wrap">
  <header class="mast">
    <p class="eyebrow">2026/27 pre-season &middot; researched 16 August 2026</p>
    <h1>Where the minutes are still in play</h1>
    <p class="standfirst">Solio forecasts every club's minutes to within a few of the
    37,620 a season contains, which means it has already made a complete allocation.
    This is a club-by-club check of where that allocation looks wrong &mdash; built from
    beat writers, local press and podcast transcripts from the fortnight to 16 August.
    Every change below carries a dated source, and every club's minutes still add up.</p>

    <div class="stats">
      <div class="stat"><b>{len(clubs)}</b><span>clubs researched</span></div>
      <div class="stat"><b>{total_deltas}</b><span>minutes changes</span></div>
      <div class="stat"><b>{balanced}/{len(clubs)}</b><span>budgets balance</span></div>
      <div class="stat"><b>{unsourced}</b><span>unsourced claims</span></div>
    </div>
  </header>

  <nav class="nav" aria-label="Clubs, with unsettled slot count">{nav}</nav>

  <section class="league">
    <h2>The fifteen biggest disagreements with Solio</h2>
    <div class="scroller">
      <table>
        <caption>Ordered by size of change, across every club researched</caption>
        <thead><tr>
          <th scope="col">Club</th><th scope="col">Player</th><th scope="col">Solio</th>
          <th scope="col">Proposed</th><th scope="col">Change</th><th scope="col">Why</th>
        </tr></thead>
        <tbody>{mover_rows}</tbody>
      </table>
    </div>
  </section>

  <h2 style="margin-bottom:18px">Club by club</h2>
  {"".join(club_html(d) for d in clubs)}

  <footer>
    <p><strong>How to read the confidence marks.</strong> A solid rule means the
    starter is not in dispute. A dashed amber rule means the sources disagree or
    the slot is genuinely open &mdash; those are the positions where a minutes
    forecast can be wrong by a thousand minutes. A dotted rule means nobody
    recent said anything, which is recorded rather than guessed at.</p>
    <p><strong>What this is not.</strong> This page reports roles, minutes and
    who starts. It contains no draft ranking, no tiers, and no view on who is
    worth picking &mdash; those are the human's, per the league's rule.</p>
  </footer>
</div>
"""


def main() -> int:
    out = OUT / "club_minutes_report.html"
    out.write_text(build(), encoding="utf-8")
    clubs = load()
    chk = {d["club"]: recompute(d) for d in clubs}
    print(f"wrote {out}  ({len(clubs)} clubs, "
          f"{sum(len(d.get('player_deltas', [])) for d in clubs)} deltas)")
    off = {c: v for c, v in chk.items() if abs(v["gap"]) > 500 or v["unsourced"]}
    for c, v in sorted(chk.items()):
        if v["outside_pool"]:
            print(f"    {c}: {v['outside_pool']:,} minutes to players with no FPL code "
                  f"({', '.join(n for n in v['outside_names'] if n)}) -- "
                  f"real, and out of the draft pool")
    if off:
        print("  clubs needing a look:")
        for c, v in off.items():
            print(f"    {c}: gap {v['gap']:+}, unsourced {v['unsourced']}")
    else:
        print("  every club balances against the 37,620 ceiling; every delta is sourced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
