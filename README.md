# FPL draft prep — 2026/27

Everything needed to analyse 2025/26 Premier League player performance in one
workbook, pulled from five sources and joined on the Opta player id.

**Output:** `output/FPL_2026_27_draft_data.xlsx` — a `Players` sheet of 719
players × 146 columns, plus an `xPts model` sheet covering all nine of FPL's
scoring elements. The model runs in Python; the sheet displays it, and keeps
the chain from `xMins` to the season total live so a minutes change still moves
a player's rank.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

One input cannot be fetched: **Solio has no API**. Export its projection CSV by
hand and save it as `~/Downloads/projection.csv` before the first run (or pass
a path to `scripts/fetch_solio.py`). Without it there are no `xMins` baselines
and no penalty shares; `run_all.py` completes anyway and says loudly at the end
that it skipped the step.

Then build everything:

```bash
.venv/bin/python scripts/run_all.py
```

A fresh clone has no cached data, so the first run downloads from all five
sources — roughly 15 minutes, most of it the Premier League feed. After that
every response is cached under `data/raw/` and re-runs take seconds. Pass
`--refresh` to force a re-download.

Two steps sit outside `run_all.py` on purpose, because neither rebuilds itself
tonight: `fetch_transfermarkt.py` → `injury_model.py` is hours of scraping
behind a rate limit, and `club_research_report.py` reads a dated snapshot of
what beat writers were saying in August 2026. Their outputs are committed
instead. See CLAUDE.md.

`data/raw/` and `data/processed/` are deliberately not in git: they are 95MB of
cache that the pipeline rebuilds on demand. The finished workbooks in
`output/` are committed, so you can read the results without running anything.

Verification (`scripts/verify_xpts.py`) shells out to LibreOffice to
recalculate the workbook. Without LibreOffice installed that one step is
skipped; the identity, worked-example and invariant checks still run.

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

```
rate per 90 = xG + xA + clean sheet + saves + goals conceded + cards
xPts season = rate per 90 × xMins / 90        the six that scale with minutes
            + appearance points               earned per match, capped at 38
            + DefCon points                   earned per match, convex in length
            + bonus                           contested once per fixture, so
                                              also per match
            + penalties                       a season count already
```

All nine of FPL's scoring elements are in. **Only the first line is linear in
minutes**, which is why the season total is not one multiplication:

- **Appearance, DefCon and bonus** points are earned per *match*. The number of
  matches is `xMins ÷ Mins per start`, capped at the 38 a season has. The cap
  is not cosmetic: Christian Nørgaard's 45 minutes per start came from a
  cameo-heavy year, and 2,804 forecast minutes divided by it claimed 62
  matches. Once capped he is making 38 longer appearances instead — 74 minutes
  each, which clears the 60-minute line — so he goes from 62 appearance points
  to 76. Sixteen players were over the ceiling before the cap went in.
- **Penalties** sit outside the per-90 term because a penalty goes to whoever
  holds the duty and is on the pitch, so the expected count already prices
  availability and must not be scaled by minutes twice.

### Where the model lives

The scoring is in `scripts/xpts_calc.py`. The workbook displays it; it does not
compute it. Ten columns stay live as real formulas — `CS pts/90`, `Rate pts/90`,
`Matches`, `App pts season`, `DC pts season`, `Pen pts season`, `xPts season`,
`xPts/90`, `VORP`, `VORP per £m` — and all ten are plain arithmetic. Nothing
that needs a probability distribution is expressed in Excel any more.

That leaves four blue input cells worth editing in the sheet:

| Input | Default | Note |
|---|---|---|
| `xMins 26/27` | `xMins Solio adjusted` | The biggest lever, and the one no dataset settles. `xMins source` records which layer it came from |
| `Mins per start` | 2025/26, shrunk toward the position mean | How long a start lasts. See the direction warning below |
| `P(CS)` | Market-implied, all 20 clubs | See below |
| `Pens/season` | Club penalties × this player's share | Season total, not a rate |

**Three columns sit behind `xMins 26/27`.** `xMins Solio` is Solio's raw
forecast. `xMins Solio adjusted` is that figure after the club-by-club
research moved it — 226 players of 719; everyone else keeps Solio's number, or
2025/26 minutes where Solio has no view. `xMins 26/27` starts at the adjusted
figure and is what the model actually scores. Green cells mark a row the
research changed; `Research delta`, `Research confidence` and `Research reason`
give the size, the researcher's own confidence, and the sourced why with a URL
and a date.

**`Mins per start` runs in the direction you would not expect.** It does not
reduce a player's minutes — `xMins` is set independently, and this column only
divides it into matches: `matches = min(38, xMins / mins per start)`. Since
appearance points are earned per match, at a *fixed* `xMins` a lower figure
here means more matches and so more points. On a midfielder holding 2,400
minutes, 62 minutes a start is worth 159.8 xPts against 145.7 at 90 — a
14-point swing, and being hooked early gains. An early substitution costs you
through `xMins`, not through this column.

It is measured as minutes in matches he started over starts, with sendings-off
dropped from both sides — a red card is not rotation risk, and `cards_model`
already prices it. Then shrunk toward the position mean by 10 starts, a
strength fitted out of sample rather than chosen: predicting the second half of
2025/26 from the first, MAE is 4.07 shrunk against 5.20 for the raw per-player
average it replaces and 4.76 for the position mean alone. `Mins/start starts`
is how many starts the figure rests on; a small number means it is mostly the
prior. Without shrinkage one 45-minute start read 45.0 forever, which put a
midfielder whose own action rate implies a 13.9% DefCon hit rate at 0.3% — a
10-point error off a single match.

Everything grey is a value the model wrote and will overwrite on the next run.
`Saves/match` and `Goals against/match` used to be editable; they now feed a
Poisson that lives in Python, so editing them in the sheet moves nothing.
Change them in the model instead. Where a player has no Premier League record
at all — a promoted club's keeper, an incoming signing — `Mins per start`
falls back to 85.

Component sources:

- **xG** — adjusted xGOT where a player has placement history, else blended xG, in both cases scaled to his non-penalty share so penalties are not counted twice. The `xG basis` column says which
- **xA** — blended xA × 3 × the assist uplift. The uplift is there because both xG models measure the *Opta* assist definition while FPL pays on its own looser one
- **Clean sheet** — `P(CS)` × 4/4/1/0 by position
- **DefCon** — the player's action rate, shrunk toward his position, run through a *negative binomial* against the threshold over `Mins per start`. Not a Poisson: within player across starts, the variance of per-match actions is 1.5x the mean, and because P(clearing the threshold) is convex in the rate that spread produces **more** crossings, not fewer. A Poisson returned 16% less DefCon than 2025/26 actually awarded and 12.4% less out of sample; the negative binomial returns −1.9% and −0.0%. The dispersion is fitted per position two-fold in `defcon_model.py` — 8 for defenders, 16 for midfielders — and written into `defcon_model.csv` rather than hard-coded
- **Bonus** — simulated per match from BPS components. See below
- **Appearance** — 1 point an appearance, 2 at 60 minutes. A substitute banks more appearances for the same minutes but only one point each. The 60-minute test uses minutes per appearance recomputed from the *capped* match count, not the raw minutes per start
- **Saves, goals conceded, cards, penalties** — see the four sections below

### Bonus, and why it is not a rate

Bonus used to be last season's realised bonus per 90, carried forward. That
credited every player for the goals, assists and clean sheets he actually
scored — the three things the model already projects from scratch, and the
three that repeat least. It was double counting and noise carrying in the same
column. Split 2025/26 into halves and the size of the problem is visible:

| Carried forward from the first half | Correlation with the second | MAE |
|---|---:|---:|
| Total BPS per 90 | 0.39 | 4.02 |
| Base BPS per 90 (events stripped) | **0.78** | **1.71** |

`bonus_model.py` replaces it in three stages.

**1. Fit the weights.** FPL publishes the BPS *stat names* in its API but not
the weights, and both rules pages are Javascript shells, so the table cannot be
read off — it is fitted, by least squares on 11,492 player-matches (R² 0.879),
and refitted on each half of the season so the reader can see which
coefficients are measured and which are one freak match. Where a term is
cleanly identified the fit recovers the published weight: **assist 9.03**
(published 9), **save 1.89** (2), **clean sheet 11.7** (12), **yellow −3.55**
(−3), **own goal −6.16** (−6), **CBI 1.23 per two actions** (1), **a forward's
goal 23.7** (24). That is the answer to "have the underlying BPS been checked
against the new rules" — they have, against the data rather than against
memory.

Goals and assists for the other positions come back *above* the book weight
(a midfielder's goal at 20.2 against a published 18) because the fitted number
is the total BPS a goal arrives with — the shot on target, the big chance, the
winning-goal bonus. For projection that is the number wanted.

**2. Split every player-match.** `base = BPS − (fitted event weights ×
events)`, where events are everything the model projects independently: goals,
assists, clean sheets, saves, goals conceded, cards, own goals, penalties
saved and missed. Appearance BPS is held out separately because it is flat, not
a rate — turning 6 points for turning up into a per-90 figure and multiplying
by 85 minutes quietly loses a tenth of it. The remainder is shifted onto
2026/27's CBI rule and shrunk toward the position mean, with the shrinkage
strength chosen out of sample.

**3. Convert BPS to bonus.** Bonus is not a rate, it is a contest: 3/2/1 to the
top three BPS in a fixture. So

```
E[bonus] = Σ over k of P(his BPS ≥ the k-th highest BPS among the other 21)
```

Both sides are simulated. His BPS is his base plus events drawn from his own
projections; the three bars come from what actually happened in 380 fixtures,
**leave-one-out** (a player is not competing with himself — pricing him against
a bar he set costs a fifth of all the bonus there is) and conditioned on his
own BPS and on the scoreline. The scoreline conditioning is the team-context
effect: in a 4–0 win four team-mates and a clean sheet are competing for the
same three points, so the same assist wins less bonus at Manchester City than
at Burnley. His goals are drawn *from his team's goals*, which forces a
goalscorer into the matches where the competition is stiffest.

Three checks are printed on every run:

| Check | Result |
|---|---|
| BPS → bonus conversion, fed the real BPS of all 7,813 starts | −3.1% |
| Replay 2025/26 on each player's own rates | r = 0.918, level −13.3% |
| Predict second-half bonus from first-half data only | MAE 3.03 vs **3.31** carrying forward |

The −13.3% is corrected by a per-position factor (1.06 to 1.34), which is not a
fudge: total bonus is conserved — 3+2+1 is awarded in every one of the 380
fixtures whatever anybody projects — so a model summing to 87% of it is wrong
by construction. It is per position rather than global because goalkeepers come
out furthest light and one global number would leave every keeper under-rated
against every forward. The unscaled version has a slightly better MAE (2.82)
and a −0.51 bias; both are printed so the trade is visible.

**Not modelled**: the −1 for being tackled, removed for 2026/27, since no free
source publishes times-tackled per match; and the goalkeeper save-location
changes.

### Floor, ceiling, and pick value

One expected-points number ranks the whole board the same way, and a draft does
not work like that. A first-round pick is a core player you cannot replace, so
his downside is what costs you. A fifteenth-round pick can be dropped for
somebody off the free pool for almost nothing, so his downside costs almost
nothing and his upside is the only reason to take him.

`minutes_risk.py` measures how wrong a minutes forecast turns out to be, from
four season-to-season pairs. It fits what a reasonable forecast would have been
(binned means on last season's minutes and age — not a regression, because
minutes are censored at 3,420 and the bottom of the range behaves nothing like
the top), then reads the 20th and 80th percentiles of actual ÷ forecast:

| Forecast minutes | Floor | Ceiling |
|---|---:|---:|
| 900–1,700 | 0.32× | 1.62× |
| 1,700–2,400 | 0.59× | 1.39× |
| 2,400–2,900 | 0.80× | 1.26× |

The asymmetry is the whole point. An ever-present's minutes are knowable to
±25%; a squad player's are not knowable at all. Where the injury model has a
player's actual history of games missed it moves his floor, capped at ±15% so
one unusual record cannot halve it on its own.

The band is checked out of sample — fit on three pairs, test coverage on the
fourth: **62.3% of players landed inside a band claiming 60%, and 18.3% below a
floor claiming 20%.**

Two mistakes worth recording, because both looked fine until they were checked.
The band was first indexed on *last season's* minutes, which handed every
promoted-club regular the band of a fringe player and put six of them on a
ceiling of 5,400 minutes in a 3,420-minute season. And "ceiling" cannot simply
mean "more minutes": a promoted club's centre-half with no attacking rates at
all has a *negative* per-90 rate, so more minutes score him fewer points and
the high-minutes case is his downside. Floor and ceiling are named by outcome.

`Pick value` then blends them by the round the pick would be made in:

```
t = +1 at round 1 → 0 at the crossover round → −1 at the last round
value = xPts + (t>0 ? t·(floor − xPts) : −t·(ceiling − xPts))
```

The crossover is a strategy choice, not a constant, so it is an input on the
page — default round 8. At the default, Bukayo Saka in round 1 is judged
entirely on his floor (158.3, not 204.1) and Mats Wieffer in round 12 comes out
at 162.1 against an expected 151.9.

**Which round is "his" round comes from `BD Pick`**, the second column on the
page, which ships empty for every player and is filled in by hand. It is an
overall pick number — 9 in an eight-team league is round 2 — and it only falls
back to ADP where nothing has been entered.

The split matters. ADP is an average of what other people's leagues did, so it
answers *when will he come off the board*: the right question for `There at`
and `Rapid VORP`, and both still read ADP for exactly that reason. It is the
wrong question here. Pick value needs to know when *you* would spend a pick,
because that is what decides whether a player's downside is unaffordable or his
upside is free — and no data source can supply that, which is why the column is
blank rather than defaulted. BD Picks deliberately do not feed the bands: what
you would do cannot make a player survive to your next pick.

They are stored in the browser alongside the other edits, and *Reset edits*
clears them, with a warning that names them — nothing re-runnable can rebuild
a board somebody typed in by hand.

### Five seasons of history, and what the projection is checked against

`history_study.py` restates 2021/22–2025/26 under 2026/27 scoring and writes
`output/history_study.md`. DefCon did not exist before 2025/26, so it is
rebuilt from the official Premier League feed — the combination was found by
testing every plausible one against the season where both exist, and
reproduces FPL's own count at **r = 0.995**. Headlines:

- **Defenders** average 6.4 of the top 30 (range 2–10). The 2026/27 projection
  has 6.
- **DefCon repeats better than anything else**: year-on-year r = **0.91**,
  against 0.75 for goals, 0.58 for assists and **0.28** for clean sheets.
- **Big-club players** take 14.5 of the top 30 on average, a 1.1–1.8× lift on
  their share of the pool — but a top-30 season at a big club repeats only
  slightly more often than one anywhere else (39% vs 35%).
- **Pre-season price** correlates with season points at r ≈ 0.52 across the
  whole pool and **0.49** among players who got 900+ minutes. Of the twenty
  most expensive players each August, **6.4** finish in the top twenty.

### Recency weighting, and why it is off

`recency.py` splits each player's 2025/26 into two halves of equal **minutes**
— not equal calendar, because a man who missed until February has no first
half — and asks how far apart the halves are relative to how far apart they
would be by chance:

```
z = (late − early) / SE(late − early)
w = 2·Φ(|z|) − 1                       the two-sided confidence they differ
estimate = w·late + (1−w)·pooled
```

The standard error is measured rather than assumed: for a rate that is pure
noise the variance of (late − early) falls as 1/n, so the noise scale is fitted
by regressing the observed squared gap on 1/n90 across the whole pool. That
gives an SE which already knows a 400-minute player cannot generate evidence a
3,000-minute player can.

**Then it is tested, and it fails.** Build both estimates on one season, score
them against the next, plus a within-season holdout (first 60% of a player's
minutes → last 40%):

| Metric | Folds won by recency | Folds won by the whole season |
|---|---:|---:|
| xG/90 | 1 | 3 |
| xA/90 | 1 | 3 |
| DefCon actions/90 | 0 | 1 |

DefCon loses worst: MAE 1.27 recency-weighted against 1.06 pooled. So the
multipliers ship at 1.0 and nothing is reweighted. Halving the sample to chase
a change that mostly is not there costs more than it buys — which is the
result, not a failure to implement it.

What does ship is the **flag**. 27 players have a rate whose second half
disagrees with its first by more than 1.96 standard errors *and* by more than
30%. Sortable in the levers page under "Late vs early", with the full
comparison in the tooltip. A z-score is not a correction, it is a question:
this moved by more than the sample can explain, so go and find out whether the
role changed or the finishing just ran hot. That judgement is the human's.

Worth knowing what the flag does *not* catch. Declan Rice's xG/90 ran
0.122 in his first half and 0.062 in his second — but at z = −0.85 that is
inside the noise for the minutes involved, so he is not flagged. The by-thirds
picture looks starker than the split can support.

### The per-match floor, and why it matters

Two FPL rules pay per *match*, not per season: 1 point per 3 saves, and −1 per
2 goals conceded. Two saves in a match are worth nothing, and so is one goal
conceded. Dividing a season total by 3 or by 2 therefore gets both badly wrong
— measured over 2025/26, saves came out **56% too generous** (0.213 points per
save, not 0.333) and goals conceded **57% too harsh** (−0.32 per goal, not
−0.50).

The model computes both as `E[floor(X/m)]` for a Poisson `X`, which equals the
sum over `k ≥ 1` of `P(X ≥ m·k)`. Eight terms is exact to 1e-4 at any rate a
real club produces.

### How it is checked

`verify_xpts.py` used to reimplement the sheet's formulas in Python and compare
the two. That was a real check while the model was written in Excel. Now that
the model is Python, comparing it against a Python reimplementation would only
show that a function agrees with a copy of itself. So the checks are of four
kinds, and only the last is a comparison:

1. **Identities.** `E[floor(X/m)]` is computed as a sum of Poisson tails and
   checked against the definition of an expectation, `Σ floor(n/m)·P(X=n)`.
   `P(clearing a threshold)` is computed as `1 − cdf` and checked against
   summing the mass above it. The two routes share no code, so these are
   proofs rather than comparisons.
2. **Worked examples.** Whole players with round-numbered inputs, hand-computed
   and written into the test as literals — an ever-present defender, a
   20 × 30-minute substitute, the 62-matches case, a penalty taker with no
   minutes.
3. **Invariants.** Nobody plays more than 38 matches; appearance points sit
   between one and two a match; an outfielder never earns save points; a card
   is never worth more than zero; a player with no 2026/27 position scores
   nothing. This is the class of check that catches a bug *both*
   implementations share, which is exactly what the old comparison could not do
   — the 62-appearance error survived it for that reason.
4. **The workbook.** The ten live columns are recalculated through LibreOffice
   and compared against Python, which is where the one genuine second
   implementation still lives.

All of it currently passes: 719/719 on every live column, the four replacement
levels, and VORP.

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
| **Poisson(lambda x mins) (new)** | **-12.4%** | **1.22** | **0.832** |

**Known calibration gap.** `mins_per_start` used to be total minutes ÷ starts,
which counts substitute cameos in the numerator but not the denominator and so
returned impossible figures — 112 minutes per start for a player with two
starts and a long bench run. It is now minutes played *in matches he started* ÷
starts. That is the right number, but it removed an error that had been masking
another: with honest exposure the Poisson under-predicts threshold hits by
12%, because per-match counts are over-dispersed and a Poisson tail is thinner
than reality. The ordering is unaffected (correlation 0.832) and the level is
not, so treat DefCon points as about 12% conservative until this is revisited.

One Excel trap worth recording: `POISSON.DIST` is a post-2007 function and
needs an `_xlfn.` prefix in the file format, which openpyxl does not add — it
silently evaluates to an error. The legacy `POISSON` works unprefixed in both
Excel and LibreOffice. And the threshold lookup must fail safe to 99, not 0:
an unknown position makes the VLOOKUP fail, and `POISSON(-1, ...)` is a `#NUM!`
that propagates all the way to `xPts season`.

## Saves, and the shots behind them

A save is a shot on target that did not go in, so the chain runs from the same
market numbers the clean-sheet model uses:

```
market points -> goals against -> shots on target faced -> saves
```

The middle step needs expected goals per shot on target faced — how good the
chances a club concedes are. Clubs really do differ: a side that funnels
attackers into long-range efforts faces more shots for the same expected goals.
But the difference **barely repeats**. Across 51 club-season pairs the
year-on-year correlation of shot quality conceded is **+0.08**, against **+0.49**
for shot volume. So the club's own quality is shrunk hard toward the league
mean, at a weight of 0.4 chosen by back-test:

| Weight on the club's own quality | Mean error, shots on target faced |
|---|---|
| 0.0 (league mean only) | 13.6 |
| **0.4** | **12.4** |
| 1.0 (club's own, raw) | 14.1 |

Predicting 2025/26 save points for the 22 keepers on 1350+ minutes, fitted only
on 2022/23–2024/25:

| | MAE | Correlation | Bias |
|---|---|---|---|
| Model | 3.63 | **+0.854** | +14.2% |
| Every keeper on the league average | **3.61** | +0.596 | — |

Read that the way you read the clean-sheet model: **the ordering is the
reliable part, the league level is not.** The correlation is far better; the
MAE is a wash because of a +14% level error, and a level error mostly cancels
when comparing keepers. The level drifts because shots per goal drifts — 3.08
saves a match in 2024/25 against 2.78 in 2025/26 — and anchoring to the previous
season makes it worse, not better (tested: bias +16.0%).

**Goalkeeper shot-stopping skill is deliberately not modelled.** Goals prevented
per 90 has a year-on-year correlation of **−0.03** over 33 repeat keeper-seasons.
That is noise, and it would be worth about a point a season anyway, since a
prevented goal is one extra save.

Goals conceded comes off the same expected goals against, with no intermediate
estimate. Against 96 keepers and defenders on 1800+ minutes: MAE 1.56,
correlation +0.904, bias +4.0%, against 2.74 for a flat league average.

## Cards

The one negative in FPL scoring that is a genuine player trait. Year-on-year
correlation of card points per 90 is **+0.47** over 766 repeat player-seasons,
rising to **+0.59** for players over 1800 minutes in both — well short of
expected goals at +0.86, but nowhere near noise.

Two things came out of the back-test rather than judgement:

- **Yellows** are the player's own three-season rate (weights 0.5/0.3/0.2),
  shrunk toward his position mean with a prior worth 8 nineties.
- **Reds are not player-specific.** At about one per 200 nineties an individual
  red rate is noise, and letting one sending-off drive a projection made the
  back-test worse. Everyone gets their position's red rate.

Fitted on 2022/23–2024/25, predicting 2025/26 for 339 players over 900 minutes:
MAE 0.0917 against 0.1026 for a flat position mean, and correlation +0.47
against +0.24.

## Penalties

The most concentrated points in the game, and the most dangerous thing to leave
sitting inside a season xG total. Cole Palmer's 2025/26 expected goals were
**44% penalties**, Bruno Fernandes' 38%, Igor Thiago's 28%. Carrying that
forward assumes the duty carries forward, and duty moves far more than form
does. So penalties come out of expected goals entirely — by each player's own
measured non-penalty share, from Understat's npxG — and are credited back from
two explicit pieces: how many penalties a club gets, and who takes them.

**How many a club gets, tested rather than assumed:**

| Predictor | Result |
|---|---|
| Last season's own penalty count | r = **+0.05** across 51 club-season pairs. Worthless |
| Touches in the opposition box | r = +0.39 in one test season, +0.03 in the other. Out of sample it does **not** beat giving every club the league average (2.25 against 2.20) |
| Goals scored | Same-season r = +0.50; cuts the error from 2.18 to **1.80** penalties per club across three test seasons |

So goals is the driver, not box touches — and it is also the only one available
for the promoted three, since the market forecasts goals for all 20 clubs. The
honest summary is that a club's penalty count is **mostly noise**: variance
across clubs is 1.6× the mean where pure chance would give 1.0×. This is a
small tilt on a flat prior, not a real forecast.

**Who takes them** is availability-weighted down the listed order: the first
choice takes them in the matches he plays, the second choice takes what is left
when he does not. The shares deliberately do not sum to 1 — a club whose only
listed taker plays 85% of minutes really does give 15% of its penalties to
somebody unlisted, and handing him the lot would credit spot-kicks he will not
be on the pitch for.

Each expected penalty is then worth `0.829 × goal points − 0.171 × 2`, from 381
penalties taken and 316 scored over four seasons. Goalkeepers get the other
side: 13.1% of penalties faced are saved, at +5 each.

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
  fetch_solio.py       Solio's projection CSV (hand-exported, no API)
  fetch_draft_adp.py   Average draft position from public draft leagues
  fetch_transfermarkt.py  Squads and injury spells (outside run_all: slow)
  injury_model.py      Injury history -> expected games missed
  club_research_report.py  Club-by-club minutes research -> HTML
  bps_remodel.py       Replay 2025/26 under the 2026/27 BPS weighting
  adjusted_xgot.py     Shot-placement-adjusted xG, with its own validation
  cs_from_odds.py      Clean-sheet probability from season-long betting markets
  team_defence.py      Club shots faced -> saves and goals-conceded points
  cards_model.py       Booking rate per player, shrunk toward position
  penalties_model.py   Club penalty counts and who takes them
  defcon_model.py      Defensive-action rate -> Poisson threshold model,
                       and shrunk minutes per start
  research_xmins.py    Flatten the club research into adjusted xMins
  levers_report.py     One sortable page of every input that moves a number
  build_master.py      Join everything into one row per player
  verify_master.py     Cross-source agreement checks (exits non-zero on failure)
  build_panel.py       Same join, run per season, for the model test
  xg_bakeoff.py        Calibration, forecast and bootstrap comparison
  export_excel.py      Write the workbook
  xpts_calc.py         The scoring model: every element, in Python
  xpts_model.py        Assemble the inputs, score them, lay out the xPts sheet
  verify_xpts.py       Identities, worked examples, invariants, LibreOffice
  run_all.py           All of the above, in order
data/raw/              Cached API responses
data/processed/        Intermediate CSVs
output/                The workbook and the model results
```
