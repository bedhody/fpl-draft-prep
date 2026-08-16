"""Transfermarkt: Premier League squads by season, and per-player injury history.

Transfermarkt is the only free source that publishes a structured injury
history -- one row per spell, with the injury description, the dates, the days
out and the number of club matches missed.  Nothing in the five sources this
repo already uses carries injury data at all.

Two pages are scraped:

* ``/premier-league/startseite/wettbewerb/GB1/plus/?saison_id=N`` -> the 20 club
  ids for season N, and from each club's ``/kader/`` page the squad: player id,
  name, date of birth and position.  Squads across several seasons are what
  give the model its *exposure* -- an injury page lists only seasons in which a
  player was hurt, so a season with no rows is indistinguishable from a season
  that was never observed unless squad membership is known independently.
* ``/verletzungen/spieler/<id>`` -> the injury history, paginated at 25 rows.

Everything is cached as raw HTML under ``data/raw/transfermarkt/`` so re-runs
cost nothing, and requests are spaced by PAUSE seconds with exponential backoff
on 429/403.  Transfermarkt served every page requested here without a block,
but it is entitled to change that; ``--refresh`` is the only thing that will go
back to the network for a page already on disk.
"""
from __future__ import annotations

import html
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from common import PROC, RAW, UA

TM = "https://www.transfermarkt.com"
CACHE = RAW / "transfermarkt"

# Politeness. Transfermarkt is a free service with no published API; one
# request every ~1.3s is slower than a human browsing and well under anything
# that could be called hammering.
PAUSE = 1.3
MAX_RETRIES = 4

# TM's saison_id is the calendar year the season starts in, so 2026 = 2026/27.
SQUAD_SEASONS = (2021, 2022, 2023, 2024, 2025, 2026)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_html(url: str, cache_name: str, *, refresh: bool = False) -> str | None:
    """GET a page and cache the raw HTML.  None means the site refused it.

    Returning None rather than raising is deliberate: one player page that
    404s or is rate-limited past its retries should cost that player, not the
    whole run.  main() counts the Nones and reports them, so a partial scrape
    is always visible as a partial scrape.
    """
    path = CACHE / cache_name
    if path.exists() and not refresh:
        return path.read_text()
    path.parent.mkdir(parents=True, exist_ok=True)
    delay = PAUSE
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
        except requests.RequestException as e:
            print(f"    ! {url}: {type(e).__name__}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 200:
            path.write_text(r.text)
            time.sleep(PAUSE)
            return r.text
        if r.status_code in (429, 403, 503):
            print(f"    ! {r.status_code} on {url}, backing off {delay:.0f}s",
                  file=sys.stderr)
            time.sleep(delay)
            delay *= 2
            continue
        print(f"    ! {r.status_code} on {url}, giving up", file=sys.stderr)
        return None
    return None


# --------------------------------------------------------------------------
# Squads
# --------------------------------------------------------------------------

_CLUB_RE = re.compile(r'/([a-z0-9\-]+)/startseite/verein/(\d+)/saison_id/(\d+)')
# The anchor text is NOT plain text: Transfermarkt appends a <span> inside it
# for the club captain and, critically, for anyone carrying an injury right
# now.  Matching the name as "[^<]* up to </a>" therefore drops precisely the
# injured players -- 3 of Manchester United's 30 on the 26/27 page, including
# two long-term absentees -- which for an injury model is the one selection
# bias that would quietly invert the result.  Match through the tags and strip.
_PLAYER_RE = re.compile(
    r'<td class="hauptlink">\s*<a href="/([a-z0-9\-]+)/profil/spieler/(\d+)"'
    r'[^>]*>(.*?)</a>', re.S)
# Same span carries the current injury and the expected return date.
_CURRENT_INJ_RE = re.compile(r'title="([^"]*)" class="verletzt-table')
# Squad rows contain a nested <table class="inline-table">, so matching a row
# as "<tr ...> up to the first </tr>" truncates it at the inner table and loses
# every column after the player's name.  Splitting on row *starts* is the only
# read of this markup that survives the nesting.
_ROW_SPLIT = re.compile(r'<tr class="(?:odd|even)">')
# Date of birth is the one date on the row followed by a bracketed age. The
# /plus/1 view also carries a joined date and a contract expiry, and taking the
# first date on the row picks up whichever of those sorts first -- that read
# gave Gerónimo Rulli a date of birth of 12/08/2026.
_DOB_RE = re.compile(r'(\d\d)/(\d\d)/(\d{4}) \(\d+\)')
_POS_RE = re.compile(r'class="zentriert rueckennummer bg_\w+" title="([^"]+)"')


def club_ids(season: int, refresh: bool = False) -> dict[int, str]:
    """{tm_club_id: slug} for the 20 Premier League clubs in `season`."""
    html = get_html(f"{TM}/premier-league/startseite/wettbewerb/GB1/plus/"
                    f"?saison_id={season}", f"comp_GB1_{season}.html",
                    refresh=refresh)
    if html is None:
        return {}
    return {int(cid): slug for slug, cid, s in _CLUB_RE.findall(html)
            if int(s) == season}


def squad(club_id: int, slug: str, season: int,
          refresh: bool = False) -> list[dict]:
    """One club's squad for one season: id, name, date of birth, position.

    Parsed row by row rather than with three independent findall() calls, so a
    row that is missing a date of birth cannot silently shift every later
    player's birthday up by one.
    """
    html = get_html(f"{TM}/{slug}/kader/verein/{club_id}/saison_id/{season}/plus/1",
                    f"squad_{club_id}_{season}.html", refresh=refresh)
    if html is None:
        return []
    out = []
    for row in _ROW_SPLIT.split(html)[1:]:
        p = _PLAYER_RE.search(row)
        if not p:
            continue
        dob = _DOB_RE.search(row)
        pos = _POS_RE.search(row)
        cur = _CURRENT_INJ_RE.search(row)
        out.append({
            "tm_id": int(p.group(2)),
            "tm_name": _clean(p.group(3)),
            "tm_slug": p.group(1),
            "dob": f"{dob.group(3)}-{dob.group(2)}-{dob.group(1)}" if dob else None,
            "tm_pos": pos.group(1).strip() if pos else None,
            "current_injury": cur.group(1) if cur else None,
            "club_id": club_id,
            "season_start": season,
        })
    return out


# --------------------------------------------------------------------------
# Injuries
# --------------------------------------------------------------------------

_INJ_ROW = re.compile(
    r'<td class="zentriert">(\d\d/\d\d)</td>'          # season
    r'<td class="hauptlink">(.*?)</td>'                # injury description
    r'<td class="zentriert">(.*?)</td>'                # from
    r'<td class="zentriert">(.*?)</td>'                # until
    r'<td class="rechts">(.*?)</td>'                   # days
    r'<td class="rechts hauptlink wappen_verletzung">(.*?)</td>', re.S)
_GAMES_RE = re.compile(r'<span>(\d+)</span>')
_TAG_RE = re.compile(r'<[^>]+>')
_DATE_RE = re.compile(r'(\d\d)/(\d\d)/(\d{4})')


def _clean(s: str) -> str:
    """Strip tags, decode entities, collapse whitespace.

    The entity step matters: the captain and injury spans end in ``&nbsp;``,
    which survives tag stripping and would otherwise be glued onto the name.
    """
    return re.sub(r'\s+', ' ', html.unescape(_TAG_RE.sub('', s))).strip()


def _date(s: str) -> str | None:
    m = _DATE_RE.search(s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def injuries(tm_id: int, refresh: bool = False) -> tuple[list[dict], bool]:
    """Every injury spell on record for one player.

    Returns (spells, ok).  ``ok`` is False when the site refused a page, which
    is what stops a player with a truncated history being treated as a player
    with a short one.  Pagination is 25 rows a page; the loop follows the
    ``/page/N`` links the pager exposes rather than guessing a page count.
    """
    spells, page, ok = [], 1, True
    while True:
        suffix = "" if page == 1 else f"/page/{page}"
        html = get_html(f"{TM}/x/verletzungen/spieler/{tm_id}{suffix}",
                        f"injuries_{tm_id}_{page}.html", refresh=refresh)
        if html is None:
            return spells, False
        for season, desc, frm, until, days, games in _INJ_ROW.findall(html):
            g = _GAMES_RE.search(games)
            spells.append({
                "tm_id": tm_id,
                "tm_season": season,
                "injury": _clean(desc),
                "from": _date(frm),
                "until": _date(until),
                "days": int(m.group(1)) if (m := re.search(r'(\d+)', _clean(days)))
                        else None,
                "games_missed": int(g.group(1)) if g else 0,
            })
        # The pager repeats every page link, so "is there a page after this
        # one" is the only reliable read of it.
        if f"/verletzungen/spieler/{tm_id}/page/{page + 1}" not in html:
            break
        page += 1
        if page > 20:                      # nobody has 500 injury spells
            break
    return spells, ok


# --------------------------------------------------------------------------

def main() -> int:
    refresh = "--refresh" in sys.argv
    CACHE.mkdir(parents=True, exist_ok=True)

    rows = []
    for season in SQUAD_SEASONS:
        clubs = club_ids(season, refresh)
        if len(clubs) != 20:
            print(f"  ! season {season}: {len(clubs)} clubs, expected 20",
                  file=sys.stderr)
        for cid, slug in clubs.items():
            rows.extend(squad(cid, slug, season, refresh))
        print(f"  {season}/{str(season + 1)[2:]}: {len(clubs)} clubs, "
              f"{len(rows)} squad rows so far")
    sq = pd.DataFrame(rows)
    sq.to_csv(PROC / "tm_squads.csv", index=False)
    print(f"tm_squads.csv  {len(sq)} club-season rows, "
          f"{sq.tm_id.nunique()} distinct players")

    # Injury pages for everyone who ever appeared in a Premier League squad in
    # the window. Players seen in only one squad-season are still kept: they
    # are exposure rows for the panel even if they never reach the target set.
    ids = sorted(sq.tm_id.unique())
    spells, failed = [], []
    for i, tid in enumerate(ids, 1):
        s, ok = injuries(int(tid), refresh)
        spells.extend(s)
        if not ok:
            failed.append(int(tid))
        if i % 100 == 0:
            print(f"  injuries {i}/{len(ids)}  spells {len(spells)}  "
                  f"failed {len(failed)}")
    inj = pd.DataFrame(spells)
    inj.to_csv(PROC / "tm_injuries.csv", index=False)
    print(f"tm_injuries.csv  {len(inj)} spells for "
          f"{inj.tm_id.nunique()}/{len(ids)} players")
    if failed:
        print(f"  ! {len(failed)} players had at least one page refused and are "
              f"NOT complete: {failed[:20]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
