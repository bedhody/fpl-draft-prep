# Is the 2026/27 minutes forecast the right *shape*?

**Verdict: too flat, in exactly the way you described — the top of the
distribution is inflated and the middle is thin.** We forecast **63 players
above 3,000 minutes**; the last two real seasons averaged **43** and the last
five ranged 39–46. The total minutes add up almost perfectly, so this is not
over-allocation — it is misallocation, and **half the excess is goalkeepers**.

Supporting numbers below. Underlying figures in
`output/minutes_distribution_check.csv`.

---

## 1. What was compared, and how

**The forecast.** Reproduced from `data/processed/master_2025_26.csv` the way
`scripts/xpts_model.py::build_rows` does it: `research_xmins` where it exists,
else `solio_season_xmins`, else 2025/26 `minutes`. Restricted to the 587 players
FPL has registered for 2026/27 (`draftable_2627`). Five of those have no figure
from any of the three layers — all at promoted clubs, all with no Premier League
record and no Solio projection — leaving **582 rows** with a forecast.

| Layer | Rows | Of them ≥3,000 mins |
|---|---:|---:|
| Club research (`research_xmins`) | 226 | 11 |
| Solio, unresearched | 353 | 52 |
| 2025/26 actual, as fallback | 3 | 0 |
| **Total** | **582** | **63** |

**History.** `data/raw/vaastav/players_raw_<season>.csv` (end-of-season FPL
roster, zero-minute players included) plus `merged_gw_<season>.csv` for
gameweek-level presence. Seasons 2023/24, 2024/25, 2025/26, with 2025/26 and
2024/25 carrying the weight. `panel.csv` was used only to cross-check the
season totals — it carries 2022/23–2025/26 and its `minutes` column is FPL
minutes, identical to vaastav's.

**Cross-check:** master's 2025/26 `minutes` vs vaastav's, 591 matched players —
89 disagree, **maximum disagreement 20 minutes**. Snapshot timing, not a join
problem; too small to move any band.

---

## 2. The sharpest test: who clears 3,000 minutes

A count of players above a high threshold is immune to how many squad fillers
are registered — adding 250 low-minute players cannot reduce it. So this is a
clean like-for-like comparison even though our roster is smaller than a real
end-of-season one.

| Set | n in set | ≥2,000 | ≥2,500 | ≥3,000 |
|---|---:|---:|---:|---:|
| **FORECAST 2026/27** | 582 | **194** | **139** | **63** |
| actual 2025/26 | 841 | 155 | 105 | 44 |
| actual 2024/25 | 804 | 171 | 106 | 42 |
| actual 2023/24 | 865 | 161 | 103 | 40 |
| *actual 2022/23 (not weighted)* | 554 | 165 | 99 | 46 |
| *actual 2021/22 (not weighted)* | 737 | — | 100 | 39 |

The real figure is remarkably stable: **39, 46, 40, 42, 44** across five
seasons. We are at 63 — **1.47× the two-season average**, and above every
season in the sample by 17 players.

### Where the 20-player excess sits

| Position | Forecast ≥3,000 | 25/26 | 24/25 | 23/24 | Excess vs last two |
|---|---:|---:|---:|---:|---:|
| **GKP** | **20** | 11 | 9 | 8 | **+10.0** |
| DEF | 26 | 20 | 21 | 17 | +5.5 |
| FWD | 4 | 2 | 0 | 2 | +3.0 |
| MID | 13 | 11 | 12 | 13 | +1.5 |
| Total | 63 | 44 | 42 | 40 | +20.0 |

**Midfield is about right. Goalkeeper is the problem.** We give **all 20 clubs**
a keeper above 3,000 minutes; reality delivered 11, 9 and 8. At the 3,200 mark
it is 18 clubs against 7, 7 and 6. The forecast is close to assuming every
club's number one plays every match, and roughly half of them do not — through
injury, a cup-keeper policy, a mid-season drop, or a red card.

It is not that we forecast too few keepers: mean keepers used per club is 2.05
in the forecast against 2.00, 2.20 and 2.00 in the three seasons. The error is
entirely in how the two are split.

Outfield alone: forecast 43 above 3,000, actual 33 / 33 / 32.

---

## 3. The depth curve — the clearest picture of the shape

For each club, sort players by minutes and take the mean across the 20 clubs at
each rank. Roster size cannot distort this.

| Rank in club | FORECAST | 25/26 | 24/25 | 23/24 | FC − 2yr avg |
|---:|---:|---:|---:|---:|---:|
| 1 | 3,355 | 3,247 | 3,235 | 3,250 | +114 |
| 2 | 3,198 | 3,078 | 3,054 | 3,040 | +132 |
| 3 | 3,053 | 2,966 | 2,926 | 2,809 | +107 |
| 4 | 2,922 | 2,760 | 2,754 | 2,663 | +165 |
| 5 | 2,809 | 2,599 | 2,603 | 2,578 | **+208** |
| 6 | 2,684 | 2,447 | 2,441 | 2,371 | **+240** |
| 7 | 2,528 | 2,279 | 2,317 | 2,266 | **+230** |
| 8 | 2,378 | 2,098 | 2,198 | 2,105 | **+230** |
| 9 | 2,225 | 1,927 | 2,036 | 1,984 | **+244** |
| 10 | 2,100 | 1,802 | 1,891 | 1,848 | **+253** |
| 11 | 1,889 | 1,691 | 1,767 | 1,695 | +160 |
| 12 | 1,595 | 1,569 | 1,612 | 1,518 | +5 |
| 13 | 1,400 | 1,426 | 1,409 | 1,374 | −18 |
| 14 | 1,194 | 1,299 | 1,202 | 1,228 | −57 |
| 15 | 1,006 | 1,155 | 1,047 | 1,149 | −95 |
| 16 | 878 | 1,015 | 942 | 1,048 | −100 |
| 17 | 728 | 903 | 791 | 941 | −119 |
| 18 | 547 | 767 | 650 | 832 | **−162** |
| 19 | 384 | 678 | 574 | 665 | **−242** |
| 20 | 290 | 565 | 478 | 524 | **−232** |
| 21 | 160 | 436 | 418 | 433 | **−267** |
| 22 | 106 | 279 | 314 | 316 | **−190** |
| 23 | 71 | 180 | 233 | 239 | −135 |
| 24 | 40 | 100 | 167 | 180 | −93 |
| 25 | 25 | 79 | 124 | 137 | −76 |
| 26–32 | ~6 → 0 | 42 → 0 | 91 → 3 | 92 → 1 | −8 to −60 |

The signature of a too-flat forecast, and it is textbook: ranks 1–3 are close
(the genuinely nailed players are easy), ranks **5–10 are 200–250 minutes too
high**, ranks 12–13 cross over, and ranks **18–22 are 160–270 minutes too low**.
We are giving a club's sixth-through-tenth choices something close to a starter's
season and leaving nothing for its 18th–22nd, who in reality play 300–700
minutes each.

### The same thing as a concentration statistic

Share of a club's 37,620 available minutes going to its top N, averaged over 20
clubs:

| | FORECAST | 25/26 | 24/25 | 23/24 |
|---|---:|---:|---:|---:|
| top 5 | 0.408 | 0.389 | 0.387 | 0.381 |
| **top 11** | **0.775** | 0.715 | 0.724 | 0.707 |
| top 14 | 0.886 | 0.829 | 0.836 | 0.817 |
| top 18 | 0.970 | 0.931 | 0.927 | 0.922 |
| top 22 | 0.995 | 0.983 | 0.974 | 0.974 |

We hand the first eleven **77.5%** of the club's minutes; reality hands them
**71.5%**. That gap is 6.0 percentage points = **2,257 minutes per club**, about
**45,000 league-wide**, that reality gives to ranks 12+ and we do not.

**A warning about Gini.** Within-club Gini reads *lower* in the forecast (0.502
vs 0.605–0.622), which naively says "less concentrated". That is an artefact:
Gini is dominated by how many zero-minute squad fillers are on the roster, and a
real end-of-season roster carries 40–52 players per club against our 29. On the
roster-size-invariant measures above, the forecast is unambiguously the more
top-heavy of the two. Gini is the wrong statistic here; it is in the CSV for
completeness only.

---

## 4. Bands

Raw band counts are contaminated by roster size, so both cuts are shown.
Restricting history to the **GW1 roster** — players registered before a ball was
kicked, the closest analogue to our pre-season 587 — is the fairer of the two.

| Set | n | 0 | 1–500 | 500–1500 | 1500–2500 | 2500–3000 | 3000+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| **FORECAST 2026/27** | 582 | 108 | 123 | 108 | 108 | **74** | **61** |
| 25/26 GW1 roster | 690 | 228 | 107 | 136 | 119 | 58 | 42 |
| 24/25 GW1 roster | 616 | 138 | 124 | 126 | 125 | 63 | 40 |
| 23/24 GW1 roster | 658 | 186 | 121 | 131 | 121 | 61 | 38 |
| 25/26 full roster | 841 | 304 | 143 | 154 | 135 | 61 | 44 |
| 24/25 full roster | 804 | 242 | 177 | 145 | 134 | 64 | 42 |

As a percentage of each set:

| Set | 0 | 1–500 | 500–1500 | 1500–2500 | 2500–3000 | 3000+ |
|---|---:|---:|---:|---:|---:|---:|
| **FORECAST** | 18.6% | 21.1% | 18.6% | 18.6% | **12.7%** | **10.5%** |
| 25/26 GW1 | 33.0% | 15.5% | 19.7% | 17.2% | 8.4% | 6.1% |
| 24/25 GW1 | 22.4% | 20.1% | 20.5% | 20.3% | 10.2% | 6.5% |
| 23/24 GW1 | 28.3% | 18.4% | 19.9% | 18.4% | 9.3% | 5.8% |

The 3000+ share is **10.5% against 5.8–6.5%** — the forecast puts roughly
1.7× the historical proportion of its registered squad on a full season.

*(The 3000+ band is defined as `>3000` while §2 uses `>=3000`, so counts differ
where players sit exactly on 3,000: the forecast has 2 such players (61 vs 63)
and 2023/24 had 2 (38 vs 40). 2024/25 and 2025/26 had none.)*

---

## 5. Per club

| Set | mean total mins | mean players used (>0) | ≥1,000 | ≥2,000 | ≥2,500 | ≥3,000 |
|---|---:|---:|---:|---:|---:|---:|
| **FORECAST 2026/27** | 37,570 | **23.7** | 14.8 | **9.7** | **6.95** | **3.15** |
| actual 2025/26 | 37,428 | 26.9 | 16.1 | 7.8 | 5.25 | 2.20 |
| actual 2024/25 | 37,424 | 28.1 | 15.3 | 8.6 | 5.30 | 2.10 |
| actual 2023/24 | 37,432 | 28.5 | 16.0 | 8.1 | 5.15 | 2.00 |

Two things stand out.

- **The budget is right.** Forecast total 751,407 minutes against a theoretical
  20 × 37,620 = 752,400 (99.9%). Real seasons deliver 748,481–748,630 (99.5%);
  the missing 0.5% is red cards, which leave a team playing with ten. So we are
  ~2,900 minutes (145 per club) above what any recent season actually produced —
  small, but in the same direction, and it means the model leaves no room for
  sendings-off. Per club the forecast ranges 0.967× to 1.008× of 37,620.
- **We use 3–5 fewer players per club than reality does**, while giving more of
  them 2,000+. Every minute has to come from somewhere, so the two are the same
  finding.

## 6. Minutes played by people the forecast cannot see

FPL registers 587 players today. Real seasons opened with 658 / 616 / 690 and
finished with 865 / 804 / 841. The players added after GW1 are not a rounding
error:

| Season | Players added after GW1 | Minutes they played | Share of league budget | Of them with 500+ mins |
|---|---:|---:|---:|---:|
| 2025/26 | 151 | 67,345 | 9.0% | 39 |
| 2024/25 | 188 | 50,825 | 6.8% | 31 |
| 2023/24 | 207 | 61,305 | 8.1% | 44 |

Since our clubs already sum to ~37,620 each, every minute a future arrival takes
must come out of somebody currently forecast. Historically that is **7–9% of the
league**, and it is a structural reason to expect the forecast's top end to be
too high even before any player-level judgement.

---

## 7. How mid-season movers and non-independent rows were handled

**Mid-season arrivals** were identified exactly, from the first gameweek in
which FPL carried the player (`merged_gw`). **Departures** cannot be identified
exactly — FPL never removes a player from the game, so a January exit and a
long-term injury look identical. An upper bound was used instead: registered at
GW1, played in GW1–19, zero minutes in GW20–38.

Excluding both groups changes the headline by 0–2 players:

| Season | ≥3,000, all | ≥3,000, movers excluded | Arrivals | Departures (upper bound) |
|---|---:|---:|---:|---:|
| 2025/26 | 44 | 42 | 151 | 38 |
| 2024/25 | 42 | 40 | 188 | 39 |
| 2023/24 | 40 | 39 | 207 | 56 |

Truncation is therefore not what is driving the gap. (It cuts slightly the wrong
way: a handful of players FPL registered at GW2–4 — late summer signings — still
played 3,000+ minutes, and the blunt "arrived mid-season" flag removes them.)

**Non-independent rows.** Only **3 of 582** forecasts fall back to 2025/26 actual
minutes, and **none of the three is above 3,000**. So essentially none of the
comparison is a season being compared against itself. This is much smaller than
it might have been because Solio covers 568 of the 587 registered players.

---

## 8. Where the excess is concentrated

| | Rows | ≥3,000 | ≥2,500 |
|---|---:|---:|---:|
| Solio's raw forecast, all draftable rows | 568 | **89** | 162 |
| The 215 researched rows — as Solio had them | 215 | 37 | — |
| The 215 researched rows — after research | 215 | **11** | — |
| Solio rows the research has not reached | 353 | **52** | — |
| **Combined forecast as scored** | 582 | **63** | 139 |

Solio's unadjusted projection has 89 players above 3,000 — **2.1× the historical
rate**. The club-by-club research has already pulled the rows it touched from 37
to 11, and it did so at a **mean delta of zero minutes**: it redistributed within
clubs rather than shrinking totals. The residual 52 sit in the 353 rows the
research has not reached.

That is a statement about coverage, not about which players are wrong.

---

## 9. What I would *not* conclude from this

- **Not that any particular player's minutes are wrong.** This is a
  distribution-level result. It says the *count* above 3,000 is high; it says
  nothing about which of the 63 belongs there, and nothing about who should move.
  Choosing that is the human's job under this repo's rule.
- **Not that the forecast is biased upward overall.** It is not — the total is
  within 0.4% of what a real season delivers. The error is in the shape.
- **Not that the low bands are too empty.** The 0 and 1–500 counts are
  contaminated by roster size in both directions (we register 587 where a season
  ends with 800+), and by the fact that our forecast simply has no row for the
  ~150–200 players who will be added later. The reliable comparisons here are the
  high thresholds, the depth curve and the top-N shares — all of which are
  insensitive to how many fillers are on the list.
- **Not that the defender figure (26 vs 17–21) is the same kind of error as the
  keeper figure (20 vs 8–11).** Defenders are a ~30% overshoot on a base that
  itself moved by 4 between 2023/24 and 2024/25; keepers are more than double,
  on a base that never exceeded 11 in five seasons. Only the keeper number is
  outside anything history has produced.
- **Not that a mechanical shrink would fix it.** Applying the research's own
  correction rate (37 → 11) to the 52 untouched Solio rows would land at ~26
  players above 3,000, well *below* the historical 42. The research plainly
  worked on the most doubtful cases first, so that rate does not extrapolate.
- **Nothing about how minutes convert into points.** A too-flat minutes
  distribution inflates the top of the xPts board, but by how much depends on the
  per-match terms in `xpts_calc.py`, which this check did not touch.

**Not checked:** whether Solio's 19-gameweek window, rather than the season
rebuild in `fetch_solio.py`, is the source of the flatness; and whether the
2026/27 registration will grow toward the ~650–690 of a normal GW1 before the
deadline on 21 August, which would change the roster-size comparisons in §4 but
not the threshold, depth-curve or top-N results.

---

## 10. Reproducing this

```bash
# forecast column, exactly as scripts/xpts_model.py::build_rows builds it
.venv/bin/python - <<'PY'
import pandas as pd, numpy as np
m = pd.read_csv('data/processed/master_2025_26.csv', low_memory=False)
s, r = m['solio_season_xmins'], m['research_xmins']
fc = r.where(r.notna(), s.where(s.notna(), m['minutes']))
d = fc[m['draftable_2627']].dropna()
print(len(d), (d >= 3000).sum(), (d >= 2500).sum())        # 582 63 139
PY

# a past season's actuals
.venv/bin/python - <<'PY'
import pandas as pd
pr = pd.read_csv('data/raw/vaastav/players_raw_2025-26.csv', low_memory=False)
print(len(pr), (pr.minutes >= 3000).sum(), (pr.minutes >= 2500).sum())   # 841 44 105
PY
```

Full working: the two scripts that produced every number here wrote
`output/minutes_distribution_check.csv` (1,641 rows, blocks: `bands`, `deciles`,
`thresholds`, `depth_curve`, `cumulative_share`, `top_n_share`, `per_club`,
`club_avg`, `club_thresholds`, `per_position`, `keepers`, `outfield`, `budget`,
`roster`, `late_arrivals`, `zero_rate`, `bands_gw1_roster`, `forecast_source`,
`top_end_by_source`, `stability`, `check`).

Nothing outside `output/` was modified.
