"""Check that every published ranking row can be traced back to its source.

Rankings gathered by an agent are the least trustworthy data in this repo, for
a reason worth writing down: while collecting them, the fetch tooling returned
a complete, plausible, entirely invented ranking table for
``smartdraftboard.com/fpl-draft-rankings`` -- a URL that returns a genuine HTTP
404.  A confident fabrication is indistinguishable from data until you check
it, so nothing here is used until it has been checked.

The check is deliberately dumb, which is the point: re-fetch each source and
confirm the player's name actually appears in it.

* Web pages -- the full name must appear in the page text.
* Video and podcast transcripts -- a person saying a top 20 out loud says
  "Haaland", not "Erling Haaland", and auto-captions mangle names badly.  So a
  row passes if any distinctive part of the name is spoken.  That is a weak
  test, and it is meant to be: it only catches names that are not there at all.

Rows that fail are kept in the output and marked ``verified=False`` rather than
deleted, so the reason for a gap survives.  Only verified rows feed the ADR
Pros column.

Needs the network.  Not part of run_all.py -- it is a one-off audit, like the
Transfermarkt scrape, and its output is committed.

Output: data/processed/pro_rankings/rankings_verified.csv
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd

from common import PROC, strip_accents

DIR = PROC / "pro_rankings"
UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
HERE = Path(__file__).resolve().parent
# Short tokens ("de", "van", "jr") are not distinctive enough to prove anything.
MIN_TOKEN = 4


def norm(s: str) -> str:
    return re.sub(r"[^a-z ]", " ", strip_accents(s).lower()).strip()


def page_text(url: str) -> str | None:
    out = subprocess.run(["curl", "-s", "-m", "45", "-A", UA, "-L", url],
                         capture_output=True)
    if out.returncode != 0 or not out.stdout:
        return None
    t = out.stdout.decode("utf-8", "replace")
    t = re.sub(r"<script.*?</script>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    return norm(html.unescape(re.sub(r"<[^>]+>", " ", t)))


def transcript_text(url: str, tries: int = 3) -> str | None:
    """The transcript API is flaky -- two runs minutes apart disagreed on
    whether a video had captions at all, which would silently downgrade a
    verified row to unverified.  Retry, and let the caller refuse to write
    if it still fails."""
    for attempt in range(tries):
        out = subprocess.run([sys.executable, str(HERE / "fetch_transcript_api.py"), url],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            try:
                t = json.loads(out.stdout).get("transcript")
                if t:
                    return norm(t)
            except ValueError:
                pass
        if attempt < tries - 1:
            time.sleep(3 * (attempt + 1))
    return None


def main() -> int:
    src = DIR / "rankings.csv"
    if not src.exists():
        print(f"!! {src} not found", file=sys.stderr)
        return 1

    r = pd.read_csv(src)
    if r.empty:
        print("rankings.csv is empty -- nothing to verify")
        (DIR / "rankings_verified.csv").write_text(",".join(r.columns) + ",verified\n")
        return 0

    r["verified"] = False
    report = []

    for url, g in r.groupby("ranking_url"):
        is_video = "youtube.com" in url or "youtu.be" in url
        text = transcript_text(url) if is_video else page_text(url)
        name = str(g.source_name.iloc[0])

        if not text:
            report.append((name, len(g), 0, "UNREACHABLE"))
            continue

        for i, player in g.player_name.items():
            parts = [p for p in norm(player).split() if len(p) >= MIN_TOKEN]
            hit = (norm(player) in text if not is_video
                   else any(p in text for p in parts))
            r.loc[i, "verified"] = bool(hit)

        ok = int(r.loc[g.index, "verified"].sum())
        report.append((name, len(g), ok, "video" if is_video else "page"))

    # An unreachable source is not evidence that its rows are wrong.  Writing
    # the file anyway would quietly turn a network blip into a permanent
    # downgrade, so refuse and leave the previous answer in place.
    unreachable = [n for n, _, _, kind in report if kind == "UNREACHABLE"]
    if unreachable:
        print("\n!! could not reach: " + ", ".join(unreachable))
        print("!! nothing written -- re-run when the source is back.")
        return 1

    out = DIR / "rankings_verified.csv"
    r.to_csv(out, index=False)

    print(f"{'source':<40} {'rows':>5} {'ok':>5}  kind")
    for name, n, ok, kind in sorted(report):
        flag = "  <-- CHECK" if ok < n else ""
        print(f"{name[:40]:<40} {n:>5} {ok:>5}  {kind}{flag}")

    bad = r[~r.verified]
    print(f"\n{int(r.verified.sum())} of {len(r)} rows verified")
    if len(bad):
        print(f"{len(bad)} row(s) could not be traced to their source "
              f"and are excluded from ADR Pros:")
        for _, b in bad.iterrows():
            print(f"  {b.source_name[:32]:<34} #{int(b['rank']):<3} {b.player_name}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
