"""Average draft position, harvested from real FPL Draft leagues.

draft.premierleague.com exposes completed drafts without authentication:

    /api/draft/{league_id}/choices   ->  every pick, with round and pick number
    /api/bootstrap-static            ->  player list, including `code`

`code` is the Opta player id, so this joins to the rest of the repo exactly.
Note the draft game's own element `id` does NOT match the main game's for
about 4% of players -- always join on `code`.

Two things the raw feed gives us that matter:

  * `was_auto` marks picks made by the autodraft algorithm when a manager
    timed out.  Those reflect FPL's default ordering, not human opinion, so
    they are recorded and can be excluded.
  * `choice_time` dates every draft, so picks can later be weighted by
    recency -- draft opinion moves with transfers, pre-season form and injury
    news, and a draft from three weeks ago is worth less than one from today.

PRIVACY: the feed includes real names and team names for every manager. None
of that is stored. Only league id, league size, draft date, player code, pick,
round and the auto flag are kept.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from common import PROC, RAW, UA

CHOICES = "https://draft.premierleague.com/api/draft/{}/choices"
BOOTSTRAP = "https://draft.premierleague.com/api/bootstrap-static"
CACHE = RAW / "draft"
PAUSE = 0.15          # be a polite guest on someone else's API


def player_map(refresh: bool = False) -> pd.DataFrame:
    path = CACHE / "bootstrap.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not refresh:
        d = json.loads(path.read_text())
    else:
        r = requests.get(BOOTSTRAP, headers={"User-Agent": UA}, timeout=45)
        r.raise_for_status()
        d = r.json()
        path.write_text(json.dumps(d))
    els = pd.DataFrame(d["elements"])
    pos = {t["id"]: t["singular_name_short"] for t in d["element_types"]}
    teams = {t["id"]: t["short_name"] for t in d["teams"]}
    return pd.DataFrame({
        "draft_element": els["id"],
        "code": els["code"],
        "web_name": els["web_name"],
        "position": els["element_type"].map(pos),
        "team": els["team"].map(teams),
        "fpl_draft_rank": els.get("draft_rank"),
    })


def scrape(league_ids, session: requests.Session) -> list[dict]:
    """Return depersonalised pick rows for leagues that have drafted."""
    rows, hits = [], 0
    for n, lid in enumerate(league_ids, 1):
        cache = CACHE / "leagues" / f"{lid}.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            picks = json.loads(cache.read_text())
        else:
            try:
                r = session.get(CHOICES.format(lid),
                                headers={"User-Agent": UA}, timeout=25)
                if r.status_code != 200:
                    picks = []
                else:
                    raw = r.json().get("choices") or []
                    # Keep only non-identifying fields.
                    picks = [{"element": c["element"], "pick": c["pick"],
                              "round": c["round"], "entry": c["entry"],
                              "was_auto": c["was_auto"],
                              "choice_time": c["choice_time"]} for c in raw]
                cache.write_text(json.dumps(picks))
            except Exception:
                picks = []
            time.sleep(PAUSE)

        if not picks:
            continue
        hits += 1
        size = len({p["entry"] for p in picks})
        date = min(p["choice_time"] for p in picks if p["choice_time"])[:10] \
            if any(p["choice_time"] for p in picks) else None
        for p in picks:
            rows.append({"league": lid, "league_size": size, "draft_date": date,
                         "draft_element": p["element"], "pick": p["pick"],
                         "round": p["round"], "was_auto": p["was_auto"]})
        if n % 200 == 0:
            print(f"  scanned {n}, drafted leagues found: {hits}", flush=True)
    print(f"  scanned {len(league_ids)}, drafted leagues found: {hits}")
    return rows


MIN_LEAGUE_SIZE = 6      # 2- and 3-team "leagues" are tests, not drafts
SQUAD = 15


def aggregate(picks: pd.DataFrame, players: pd.DataFrame, *, min_size=MIN_LEAGUE_SIZE):
    p = picks.merge(players, on="draft_element", how="left")
    # was_auto arrives as None on some picks; treat unknown as human-made.
    p["was_auto"] = p["was_auto"].fillna(False).astype(bool)

    # Only complete drafts. A league abandoned after three rounds still reports
    # real picks, but it would drag every drafted_pct denominator around.
    # `pick` restarts at 1 every round, so the global pick number has to be
    # rebuilt from round and league size.
    p["overall_pick"] = (p["round"] - 1) * p["league_size"] + p["pick"]
    total = p.groupby("league")["round"].transform("max")
    p["complete"] = total >= SQUAD
    before = p["league"].nunique()
    p = p[(p["league_size"] >= min_size) & p["complete"]]
    print(f"  usable drafts: {p['league'].nunique()}/{before} "
          f"(dropped leagues under {min_size} teams or not finished)")

    # Normalise to an 8-team board. "How far into the draft did he go" is
    # overall_pick / league_size rounds; multiply by 8 to express it as an
    # 8-team pick number, so a 16-team round 1 compresses into picks 1-8.
    p["adp_8team"] = p["overall_pick"] / p["league_size"] * 8

    human = p[~p["was_auto"]]
    n_leagues = p["league"].nunique()
    n_human = human["league"].nunique()
    eight = human[human["league_size"] == 8]

    def agg(df, suffix):
        return df.groupby("code").agg(**{
            f"adp{suffix}": ("adp_8team", "mean"),
            f"adp_sd{suffix}": ("adp_8team", "std"),
            f"earliest{suffix}": ("adp_8team", "min"),
            f"latest{suffix}": ("adp_8team", "max"),
            f"times_drafted{suffix}": ("league", "nunique"),
        })

    out = (agg(human, "")
           .join(agg(p, "_incl_auto"), how="outer")
           .join(agg(eight, "_8team_only"), how="outer"))
    out["drafted_pct"] = (out["times_drafted"] / n_human * 100).round(1)
    out["auto_pick_pct"] = (
        (out["times_drafted_incl_auto"] - out["times_drafted"].fillna(0))
        / out["times_drafted_incl_auto"] * 100).round(1)
    out = out.join(players.set_index("code")[["web_name", "position", "team",
                                              "fpl_draft_rank"]].drop_duplicates())
    for c in out.columns:
        if out[c].dtype.kind == "f":
            out[c] = out[c].round(2)
    return out.sort_values("adp").reset_index(), n_leagues, n_human, p


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    lo, hi = (int(args[0]), int(args[1])) if len(args) >= 2 else (1, 6000)
    players = player_map("--refresh" in sys.argv)
    print(f"draft player map: {len(players)} players")

    ids = list(range(lo, hi + 1))
    random.Random(0).shuffle(ids)          # spread load, avoid marching in order
    with requests.Session() as s:
        rows = scrape(ids, s)
    if not rows:
        print("no completed drafts found in that id range")
        return 1

    picks = pd.DataFrame(rows)
    picks.to_csv(PROC / "draft_picks_raw.csv", index=False)
    out, n_leagues, n_human, used = aggregate(picks, players)
    out.to_csv(PROC / "draft_adp.csv", index=False)

    sizes = used.groupby("league")["league_size"].first().value_counts().sort_index()
    dates = used.groupby("league")["draft_date"].first().value_counts().sort_index()
    print(f"\n{len(used)} picks from {n_leagues} usable drafts")
    print("league sizes:", sizes.to_dict())
    print("draft dates:", dates.to_dict())
    print(f"auto picks: {used.was_auto.mean():.1%} of picks (excluded from ADP)")
    print(f"\ndraft_adp.csv  {len(out)} players")

    MIN = 10
    solid = out[out.times_drafted >= MIN]
    print(f"\ntop 30 by ADP, drafted in at least {MIN} leagues "
          f"({len(solid)} of {len(out)} players clear that bar):")
    cols = ["web_name", "team", "position", "adp", "adp_sd", "earliest", "latest",
            "times_drafted", "drafted_pct", "adp_8team_only", "fpl_draft_rank"]
    print(solid.head(30)[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
