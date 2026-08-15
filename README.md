# FPL draft prep — 2026/27

Everything needed to analyse 2025/26 Premier League player performance in one
workbook, pulled from five sources and joined on the Opta player id.

**Output:** `output/FPL_2026_27_draft_data.xlsx` — a `Players` sheet of 719
players × 126 columns, plus an `xPts model` sheet built from live Excel
formulas you can edit.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Then build everything:

```bash
.venv/bin/python scripts/run_all.py
```

A fresh clone has no cached data, so the first run downloads from all five
sources — roughly 15 minutes, most of it the Premier League feed. After that
every response is cached under `data/raw/` and re-runs take seconds. Pass
`--refresh` to force a re-download.

`data/raw/` and `data/processed/` are deliberately not in git: they are 95MB of
cache that the pipeline rebuilds on demand. The finished workbooks in
`output/` are committed, so you can read the results without running anything.

Formula verification (`scripts/verify_xpts.py`) shells out to LibreOffice to
recalculate the workbook. Without LibreOffice installed that step is skipped;
nothing else depends on it.

---

## The sources, and what each one is for

| Source | Access | What only it gives you |
|---|---|---|
| **FPL API** | `fantasy.premierleague.com/api` | Opta xG/xA/xGI/xGC, defensive-contribution actions, BPS, 2026/27 prices and positions, and **set-piece order** (penalties, corners, direct free kicks) — nowhere else publishes designated takers |
| **Premier League** | `footballapi.pulselive.com` | 55 raw Opta counting stats: big chances created/missed, touches in the opposition box, errors leading to a goal or shot, aerials, duels, through balls, high claims |
| **Understat** | `understat.com/getLeagueData/EPL/2025` | An xG model **independent of Opta**, plus npxG, xGChain and xGBuildup |
| **FotMob** | `data.fotmob.com/stats/47/season/27110/*.json` | **xGOT** (post-shot xG) and **goals prevented** for keepers — the PSxG family |
| **vaastav/Fantasy-Premier-League** | GitHub raw CSV | Gameweek-level history, and full end-of-season rosters for past seasons |

### Why not FBref

FBref lost its Stats Perform (Opta) feed on 20 January 2026 and removed all
advanced stats — including PSxG, progressive passes and pressures. Results,
basic stats and squad data remain. FotMob is the closest free replacement for
the PSxG family; the Premier League's own feed replaces most of the rest.
(Reported by multiple outlets; FBref currently blocks automated access from
here, so this is second-hand rather than something the pipeline verified.)

### How the sources are joined

FPL's `code` field **is** the Opta player id, and the Premier League feed
exposes the same value as `altIds.opta`. So FPL ↔ Premier League is an exact
ID join with no name matching at all.

Understat and FotMob share no id, so they are matched by name against an alias
set built from both the official PL name and the FPL first/second/web names,
then **verified against minutes played** — which every source reports
independently. `scripts/verify_master.py` then checks that all four sources
agree on goals and minutes for every joined row; a disagreement there means a
bad join, not a data quirk.

Current state: FotMob 537/537 matched, Understat 524/537. The 13 unmatched
players all logged under 15 minutes all season and are listed on the
**Match review** sheet.

---

## Which xG model is best

Tested on 2022/23 → 2025/26, players with 900+ minutes, using season *N* to
predict season *N+1*. Full numbers on the `Model - *` sheets and in
`output/xg_model_bakeoff.xlsx`.

**Goals — Opta wins, narrowly.**

| Predictor of next-season goals/90 | Spearman | R² |
|---|---|---|
| Opta xG/90 | **0.745** | 0.594 |
| 50/50 blend | 0.742 | 0.583 |
| Understat xG/90 | 0.734 | 0.568 |
| FotMob xGOT/90 | 0.720 | 0.536 |
| Past goals/90 (baseline) | 0.693 | 0.495 |

A paired bootstrap puts Opta ahead of Understat in 97% of resamples, but the
95% interval only just clears zero — real, and small. Opta vs the blend is
84%, i.e. indistinguishable.

Calibration is the sharper difference. Against actual goals scored:

| Season | Opta bias | Understat bias |
|---|---|---|
| 2022/23 | +3.2% | +7.8% |
| 2023/24 | −2.1% | +6.8% |
| 2024/25 | +0.7% | +13.7% |
| 2025/26 | +4.1% | +14.9% |

Understat has over-predicted goals every season and the gap is widening. Opta
sits within a few percent. If an xG total is used as a points input rather
than just a ranking, that 15% matters.

**Assists — no clear winner, so the blend wins.**

| Predictor of next-season FPL assists/90 | Spearman | R² |
|---|---|---|
| 50/50 blend xA/90 | **0.690** | 0.432 |
| Understat key passes/90 | 0.688 | 0.423 |
| Understat xA/90 | 0.685 | 0.420 |
| Opta xA/90 | 0.667 | 0.417 |
| Past FPL assists/90 (baseline) | 0.588 | 0.329 |

The blend beats Opta xA alone in 99.8% of resamples but beats Understat xA in
only 80% — a genuine tie between the blend and Understat.

The workbook carries both models separately **and** the blend, so either
choice is available. `xG model gap` (Opta minus Understat) flags players the
two models disagree about most.

---

## Data traps worth knowing

**FPL assists ≠ Opta assists.** FPL uses a looser definition (rebounds,
deflections, won penalties). Across 2025/26 that is 765 FPL assists against
580 Opta assists — **32% more**. FPL assists are what pay points; Opta and
Understat xA are calibrated against the *Opta* definition. Both columns are in
the workbook, labelled.

**`DefCon actions` is not DefCon points.** The FPL API's
`defensive_contribution` field is the raw count of qualifying actions (CBI +
tackles for defenders; plus recoveries for midfielders and forwards) — for
2025/26 that ranges up to ~500. Points are awarded per *match*, on a threshold
of 10 (DEF) or 12 (MID/FWD). Two players with identical action counts can earn
very different points depending on how evenly the actions were spread, so the
workbook computes **`DefCon matches`** and **`DefCon points`** from
gameweek-level data. The gap is real: Ryan Sessegnon averaged 10.8 actions/90
but cleared the threshold in only 5 of 20 starts, while Marcos Senesi averaged
11.5 and cleared it in 26 of 37.

**Position changes move the DefCon threshold.** Last season's DefCon points
were earned against last season's position. Ten players were reclassified for
2026/27 — Mats Wieffer and Ryan Sessegnon both moved MID → DEF, so their
threshold drops from 12 to 10. `Pos 25/26` is in the workbook next to `Pos`.

**Understat undercounts substitute minutes.** It clocks a substitute from the
announced minute and ignores added time, so it runs about 8% light on players
under 300 minutes (median ratio 0.92 vs the official count) while matching
exactly for regular starters. Every per-90 column in the workbook therefore
uses the **FPL minutes** as a single denominator, regardless of which source
the numerator came from.

**Pre-season snapshot timing.** During pre-season the FPL bootstrap still
carries last season's totals, and they reset to zero at GW1 (deadline
2026-08-21 17:30 UTC). The pipeline reads the permanent `history_past`
endpoint instead, so it keeps working after the season starts.

---

## 2026/27 rule changes that affect the model

Scoring is unchanged: goals 10/6/5/4, assists 3, clean sheets 4/4/1/0,
defensive contribution +2 with the same 10/12 thresholds. Confirmed directly
from the API's own `game_config.scoring` block.

The **BPS weights did change**, which affects any bonus-points estimate built
on last season's BPS totals:

- Clearances, blocks and interceptions now earn 1 BPS per **three** actions, down from 1 per two
- The −1 BPS penalty for being tackled is removed
- Saves from outside the box no longer score separately; a saved penalty is worth 7 BPS, down from 8

`bps_remodel.py` measures the effect rather than guessing at it: it replays all
380 fixtures of 2025/26 under the new CBI weighting. The bonus allocator is
validated first by reproducing the *actual* 2025/26 bonus from the actual BPS,
including FPL's tie rules — **29,747/29,747 player-gameweeks, 100%**.

The result is much smaller than the BPS deltas suggest:

| Position (900+ mins) | Mean BPS change | Mean bonus change | Total |
|---|---|---|---|
| DEF | −24.9 | **−0.32** | −41 |
| GK | −7.6 | **+0.96** | +23 |
| FWD | −7.0 | +0.26 | +9 |
| MID | −9.9 | +0.09 | +14 |

BPS is ordinal — it only decides who finishes top three in a match. Every
defender's CBI-derived BPS falls together, so the ordering among them barely
moves; the change only bites where a defender was narrowly beating a keeper or
an attacker. Worst affected is Gabriel Magalhães at −6 bonus over a season.
Keepers are the real winners.

Two of the three BPS changes cannot be modelled: removing the −1 for being
tackled needs per-match "times tackled", and the goalkeeper save changes need
saves split by shot location. Neither is published free. Both push further away
from defenders, so −0.32 is a floor rather than a point estimate.

`Bonus (new rules)`, `BPS (new rules)` and `Points (new rules)` are in the
workbook next to the originals.

---

## The xPts model

`xPts per 90 = appearance + xG + xA + clean sheet + defensive contribution +
bonus`, then `xPts season = xPts per 90 × xMins / 90`.

It is written as **live Excel formulas**, not baked-in numbers, so every
assumption is visible and changeable in the sheet. Blue cells are inputs:

| Input | Default | Note |
|---|---|---|
| `xMins 26/27` | 2025/26 minutes | The biggest lever, and the one no dataset provides |
| `P(CS)` | The club's own 2025/26 clean-sheet rate | Counted from match results, keyed on the club — a defender who moved does not import his old defence. Promoted clubs are blank |
| Assist uplift | 1.32 | On the `Assumptions` sheet |

Scoring multipliers sit on `Assumptions` and are pulled in by `VLOOKUP` on
position, so a rule change is one edit rather than 700.

Component sources:

- **xG** — adjusted xGOT where a player has placement history, else blended xG. The `xG basis` column says which
- **xA** — blended xA × 3 × the assist uplift. The uplift is there because both xG models measure the *Opta* assist definition while FPL pays on its own looser one
- **Clean sheet** — `P(CS)` × 4/4/1/0 by position
- **DefCon** — share of 2025/26 starts that cleared the threshold, **re-scored at the 2026/27 threshold for the player's 2026/27 position**, × 2
- **Bonus** — bonus per 90 from the BPS re-model, as a rate rather than a share of points, so it does not depend on the rest of the model
- **Appearance** — the player's measured 2025/26 appearance points per 90, which handles substitutes properly rather than assuming 2

`verify_xpts.py` recalculates the workbook with LibreOffice and checks all
eight formula columns against the same arithmetic done independently in
Python. It currently passes 719/719 on every column.

## Adjusted xGOT

`adjusted_xGOT = xG × placement_ratio`, where `placement_ratio =
(Σ xGOT + k) / (Σ xG + k)` over up to four seasons, k = 8 xG.

The shrinkage is what makes it usable: a player with one well-placed shot gets
pulled back to 1.0, and a player with four seasons keeps most of his own
signal. No hard minimum-sample cutoff is needed, but `Placement sample xG` is
in the workbook so the shrinkage can be seen.

Tested on 2023/24→2024/25 and 2024/25→2025/26 it edges plain xG at predicting
next-season goals/90 — Spearman 0.758 vs 0.754 — and the k-curve is flat
between 4 and 12, so it is not an artefact of tuning. But a paired bootstrap
puts it ahead in only **83% of resamples, 95% CI [−0.004, +0.012]**. Real
enough to prefer, too small to lean on.

## Layout

```
scripts/
  common.py            HTTP cache, name normalisation, fuzzy matching
  fetch_fpl.py         FPL API: meta, set-piece order, permanent season history
  fetch_understat.py   Understat league data (2021/22-2025/26)
  fetch_fotmob.py      FotMob deep stats incl. xGOT and goals prevented
  fetch_pulselive.py   Premier League feed, 55 Opta stats x 5 seasons
  fetch_vaastav.py     End-of-season FPL rosters, 2022/23-2025/26
  fetch_gameweeks.py   Gameweek detail -> DefCon matches, minutes risk
  bps_remodel.py       Replay 2025/26 under the 2026/27 BPS weighting
  adjusted_xgot.py     Shot-placement-adjusted xG, with its own validation
  build_master.py      Join everything into one row per player
  verify_master.py     Cross-source agreement checks (exits non-zero on failure)
  build_panel.py       Same join, run per season, for the model test
  xg_bakeoff.py        Calibration, forecast and bootstrap comparison
  export_excel.py      Write the workbook
  xpts_model.py        Add the xPts sheet as live Excel formulas
  verify_xpts.py       Recalculate with LibreOffice, check every formula
  run_all.py           All of the above, in order
data/raw/              Cached API responses
data/processed/        Intermediate CSVs
output/                The workbook and the model results
```
