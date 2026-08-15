"""Shared helpers: cached HTTP, name normalisation, fuzzy matching."""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
OUT = ROOT / "output"
for _d in (RAW, PROC, OUT):
    _d.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Season the analysis is built on, and the season being drafted for.
DATA_SEASON = "2025-26"
DRAFT_SEASON = "2026-27"


def get_json(url, cache_name, *, headers=None, params=None, data=None,
             refresh=False, pause=0.4):
    """GET/POST a URL and cache the parsed JSON under data/raw/."""
    path = RAW / cache_name
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    if data is not None:
        r = requests.post(url, headers=h, params=params, data=data, timeout=45)
    else:
        r = requests.get(url, headers=h, params=params, timeout=45)
    r.raise_for_status()
    payload = r.json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    time.sleep(pause)
    return payload


# --------------------------------------------------------------------------
# Name handling
# --------------------------------------------------------------------------

# Accented / stylised spellings that strip-accents alone does not resolve.
_MANUAL_ALIASES = {
    "gabriel magalhaes": "gabriel",
    "gabriel dos santos magalhaes": "gabriel",
    "son heung min": "son",
    "heung min son": "son",
    "joao pedro junqueira de jesus": "joao pedro",
    "igor thiago": "thiago",
    "thiago santos da silva": "thiago",
}


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def norm_name(s: str) -> str:
    """Lowercase, de-accent, drop punctuation and stray initials."""
    if not s:
        return ""
    # Understat serves names HTML-escaped, e.g. "Jake O&#039;Brien".
    s = html.unescape(str(s))
    s = strip_accents(s).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _MANUAL_ALIASES.get(s, s)


def name_keys(first: str, last: str, web: str) -> set[str]:
    """Every reasonable spelling a third-party source might use."""
    first, last, web = norm_name(first), norm_name(last), norm_name(web)
    keys = {web, last, f"{first} {last}".strip()}
    if first and last:
        keys.add(f"{first[0]} {last}")
        keys.add(f"{first[0]}{last}")
        # Sources often keep only the final surname token.
        keys.add(last.split()[-1])
        keys.add(f"{first} {last.split()[-1]}")
    return {k for k in keys if k}


def similarity(a: str, b: str) -> float:
    """0-100 name similarity, tuned for how football sources mangle names.

    Straight edit distance is not enough: sources disagree by adding or dropping
    given names ("Amad Diallo" / "Amad Diallo Traore"), by using a different
    given name entirely ("Lesley" / "Chimuanya" Ugochukwu), and by accent
    stripping.  So we also reward token containment and surname equality.
    """
    if a == b:
        return 100.0
    ta, tb = a.split(), b.split()
    if ta and tb:
        short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if set(short) <= set(long_) and (len(short) >= 2 or len(short[0]) >= 5):
            return 95.0
        if ta[-1] == tb[-1] and len(ta[-1]) >= 5:
            return 92.0
    return SequenceMatcher(None, a, b).ratio() * 100


# --------------------------------------------------------------------------
# Team handling
# --------------------------------------------------------------------------

# Maps every spelling seen across sources onto the FPL three-letter code.
TEAM_ALIASES = {
    "arsenal": "ARS",
    "aston villa": "AVL",
    "bournemouth": "BOU", "afc bournemouth": "BOU",
    "brentford": "BRE",
    "brighton": "BHA", "brighton and hove albion": "BHA", "brighton hove albion": "BHA",
    "burnley": "BUR",
    "chelsea": "CHE",
    "coventry": "COV", "coventry city": "COV",
    "crystal palace": "CRY",
    "everton": "EVE",
    "fulham": "FUL",
    "hull": "HUL", "hull city": "HUL",
    "ipswich": "IPS", "ipswich town": "IPS",
    "leeds": "LEE", "leeds united": "LEE",
    "leicester": "LEI", "leicester city": "LEI",
    "liverpool": "LIV",
    "luton": "LUT", "luton town": "LUT",
    "manchester city": "MCI", "man city": "MCI",
    "manchester united": "MUN", "man united": "MUN", "manchester utd": "MUN",
    "newcastle": "NEW", "newcastle united": "NEW",
    "nottingham forest": "NFO", "nott m forest": "NFO", "nottm forest": "NFO",
    "sheffield united": "SHU", "sheffield utd": "SHU",
    "southampton": "SOU",
    "sunderland": "SUN",
    "tottenham": "TOT", "tottenham hotspur": "TOT", "spurs": "TOT",
    "west ham": "WHU", "west ham united": "WHU",
    "wolverhampton wanderers": "WOL", "wolves": "WOL", "wolverhampton": "WOL",
}


def team_code(name: str) -> str:
    return TEAM_ALIASES.get(norm_name(name), norm_name(name).upper()[:3])
