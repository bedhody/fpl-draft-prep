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
| **FPL Draft API** | `draft.premierleague.com/api` | Real draft results from public leagues — average draft position, with an auto-pick flag and a date on every draft |
| **Solio Analytics** | CSV export | An odds-driven projection: 19 gameweeks of expected points and expected minutes per player |

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

## Clean sheets from the betting market

`P(CS)` no longer defaults to last season's clean-sheet count, which tested as
the weakest of the three available predictors (R² 0.14 against 0.22 for xGA).
It now comes from season-long betting markets, which price this summer's
transfers and cover the promoted clubs — neither of which any backward-looking
statistic can do.

Match odds are the wrong instrument here. Bookmakers price about one gameweek
ahead, and a single fixture's expected goals is a *product* of both teams'
strength and home advantage, so it cannot be decomposed from one observation —
20 numbers against 39 free parameters. GW1 also prices a specific injury list
(Arsenal without Saliba and Timber) that is false for most of the season.
Season points totals price the whole campaign, absences and returns included.

```
market points -> goal difference -> goals for / against
              -> attack and defence strength
              -> expected goals in each of 38 fixtures
              -> P(clean sheet) per fixture, summed
```

Both relationships are fitted on 100 team-seasons, not assumed:
`pts = 0.624 × GD + 52.5` (R² 0.93) and `GF = 0.546 × GD + 55.6` (R² 0.84).

The two books agree closely — correlation 0.998, mean disagreement 0.90 points,
with only Newcastle and Fulham differing by more than 2 — so the input is
averaged across them and shifted so the league total matches a real season.

**Validation, feeding each season's actual points through the whole chain:**

| Season | Predicted CS | Actual | Bias | MAE |
|---|---|---|---|---|
| 2021/22 | 205 | 212 | −3.2% | 1.60 |
| 2022/23 | 202 | 207 | −2.4% | 1.54 |
| 2023/24 | 208 | 157 | **+32.5%** | 3.15 |
| 2024/25 | 204 | 178 | +14.8% | 2.05 |
| 2025/26 | 197 | 194 | +1.6% | 1.38 |

Read that honestly: **the cross-section is reliable and the level is not.**
Correlation across clubs is 0.805 and typical error is 1.9 clean sheets per
club, but 2023/24 ran at 3.28 goals a match against 2.75 last season and no
backward-looking calibration anticipates that. Anchoring the goal rate to the
previous season only moves mean absolute bias from 12.8% to 10.0%, so the
stabler five-season average is kept. Treat the ordering as sound and the level
as ±13%. For ranking players the level largely cancels; for absolute xPts it
does not.

Market numbers are transcribed by hand — bet365, Spreadex and oddschecker all
block automated access, by Cloudflare and by browsing policy.

## Defensive contribution

Not a frozen rate. The old approach — share of 2025/26 starts that cleared the
threshold — is inert: raise a player's minutes and DefCon points scale
linearly, when the real response is steeply convex. Defenders clear the
threshold in **5.6% of 60-75 minute starts and 31.0% of 90-minute starts**.

So the model estimates an underlying action rate and integrates:

```
lambda  = qualifying defensive actions per 90, shrunk toward the position
          mean (gamma-Poisson, prior worth 8 nineties)
P(hit)  = P(X >= threshold),  X ~ Poisson(lambda x mins_per_start / 90)
points  = 2 x starts x P(hit)
```

Written into the sheet as a live `POISSON` formula, so it responds to minutes.
A defender on 10 actions/90 against a threshold of 10:

| Start length | Chance of clearing it |
|---|---|
| 60 min | 13.7% |
| 70 min | 25.6% |
| 80 min | 39.8% |
| 90 min | 54.2% |

The old approach returned the same number at every one of those.

Three choices, each tested rather than assumed:

- **Does the rate scale with minutes?** Within players who have both full and
  partial appearances, actual partial-match actions come to **0.966** of what
  rate-scaling predicts. Close enough to linear.
- **Poisson or negative binomial?** Per-match counts are mildly over-dispersed
  (variance/mean 1.29) and the negative binomial fits better *in sample*. Out
  of sample it does not — the shrinkage already absorbs the over-dispersion and
  the negative binomial then counts it twice. Poisson is also the only one of
  the two Excel can express without truncating its arguments.
- **Is it better than what it replaces?** Fit on GW1-19, predict GW20-38:

| | Bias | MAE | Correlation |
|---|---|---|---|
| Realised rate x starts (old) | +4.3% | 1.31 | 0.809 |
| **Poisson(lambda x mins) (new)** | **-2.2%** | **1.21** | **0.832** |

One Excel trap worth recording: `POISSON.DIST` is a post-2007 function and
needs an `_xlfn.` prefix in the file format, which openpyxl does not add — it
silently evaluates to an error. The legacy `POISSON` works unprefixed in both
Excel and LibreOffice. And the threshold lookup must fail safe to 99, not 0:
an unknown position makes the VLOOKUP fail, and `POISSON(-1, ...)` is a `#NUM!`
that propagates all the way to `xPts season`.

## Average draft position

`draft.premierleague.com` serves completed drafts without authentication.
`fetch_draft_adp.py` scans league ids, keeps the drafts that finished, and
aggregates picks into an ADP.

Three things make the raw feed usable rather than merely available:

- **`pick` restarts at 1 every round.** The global pick number has to be
  rebuilt as `(round − 1) × league_size + pick`. Using `pick` directly makes
  every draft look 15 rounds shallow.
- **`was_auto` marks picks the autodraft algorithm made** when a manager timed
  out — about 17% of them. Those follow FPL's default ordering rather than
  anyone's opinion, so they are excluded from ADP and reported separately as
  `auto_pick_pct`.
- **League sizes vary from 2 to 16.** ADP is normalised onto an 8-team board as
  `overall_pick / league_size × 8`, so a 16-team round 1 compresses into picks
  1–8. `adp_8team_only` uses just the 8-team leagues, which is the exact
  format being drafted for.

Leagues under 6 teams and unfinished drafts are dropped. Every draft carries a
date, so picks can later be weighted for recency — draft opinion moves with
transfers, pre-season form and injury news.

Current sample: **594,465 picks from 4,874 completed drafts** dated 4–15 August,
2,456 of them 8-team leagues. **35% of all picks are autodraft** and are
excluded from ADP — but they still count toward `Drafted %`, otherwise a player
the algorithm always takes looks unwanted. Haaland is drafted in 99.7% of
leagues while only 65% of his picks are human.

`ADP reliable` flags the 385 players drafted by a human in 10+ leagues. Below
that an ADP is one person's opinion, not a market, so those rows sort last.

Most drafts still have not happened — the GW1 deadline is 21 August. Re-running
closer to your own draft gives both a larger and a more current sample, which
resolves most of the recency-vs-size tension by itself.

**Privacy:** the feed returns real manager names and team names on every pick.
None of it is stored. Only league id, size, draft date, player code, pick,
round and the auto flag are kept.

## Solio projections

Solio publishes 19 gameweeks of projected points and minutes. Doubling that for
a season would be wrong, because the early window is depressed by pre-season
injuries and late returns from the summer World Cup — Saliba is projected 0
minutes for GW1–5 and 86 by GW16, and doubling charges him for that absence
twice.

So the second half is rebuilt at each player's settled minutes, read from
GW17–19:

```
rate   = projected points / projected minutes   (over gameweeks he plays)
H2     = rate × settled_xMins × 19
season = H1 as published + H2
```

Using a points-per-minute rate rather than rescaling each gameweek handles the
zero-minute gameweeks, which cannot be rescaled at all.

Checks on the GW17–19 window: **no player's minutes are still moving by then**
(zero of 568), and nobody settles at zero having played meaningfully earlier.
So the window is a clean read of steady state. Of 568 players, 525 are flat,
11 ramp up (injury or World Cup return) and 32 fade because Solio expects them
to lose their place. The corrections run both ways — Saliba +22 points against
naive doubling, Mosquera −21 — and the fade cases are the riskier half, since
they amplify rather than dampen any error in Solio's view of a player's role.

`Solio xMins (season)` is carried into the xPts sheet next to the `xMins`
input, as a forward-looking alternative to last season's actual minutes.

## VORP

`VORP = xPts season − replacement level at that position`, where replacement is
the `(teams × slots + 1)`-th best **draftable** player at that position — the
best one still on the board once every team has filled its slots.

Raw xPts asks who scores most. VORP asks who scores most *above what you would
get at that slot anyway*, which is the question a draft actually poses. It is
not a separate lens; it is an operator you can apply to any projection.

Settings live on `Assumptions` (blue = editable): teams, and squad slots per
position, defaulting to an 8-team league drafting full 15-man FPL squads
(2/5/5/3). Replacement levels recompute from the sheet via `LARGE()`, so
changing the league size updates every VORP cell.

At 8 teams with 15-man squads:

| Pos | Slots | Replacement rank | Replacement xPts | Best | VORP of best |
|---|---|---|---|---|---|
| GKP | 2 | 17th | 83.2 | 159.4 | 76.2 |
| DEF | 5 | 41st | 120.8 | 199.4 | 78.6 |
| MID | 5 | 41st | 120.0 | 239.9 | 119.9 |
| FWD | 3 | 25th | 48.3 | 224.9 | 176.6 |

It reorders things heavily. The top 40 by raw xPts is 20 DEF / 15 MID / 4 FWD
/ 1 GKP; by VORP it is 17 FWD / 9 MID / 8 DEF / 6 GKP.

**Two caveats worth understanding before leaning on it.**

FPL registers 70 forwards against 193 defenders and 259 midfielders, so the
25th forward is a marginal player (48.3 xPts) while the 41st defender is still
a starter (120.8). Part of the forward scarcity is real and part is FPL's
positional labelling.

More importantly, **squad-slot replacement counts bench players**. An FPL squad
carries two keepers but only one plays. At 8 teams the 17th keeper is still
semi-playing; at 10 teams the 21st keeper is a pure backup worth 20.6 xPts, and
GK VORP explodes accordingly — six keepers land in the top twelve. If you would
rather measure against who actually starts, change the slots on `Assumptions`
to 1/4/4/2 and everything recomputes. Both definitions are defensible; they
answer different questions.

`VORP per £m` is also there. Pure VORP measures scarcity alone, which is the
right frame in a pure draft; per-£m measures scarcity against the £100m budget
that still binds you in a normal FPL season.

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
