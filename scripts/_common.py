"""Shared helpers for the transcript fetchers.

Both `fetch_youtube_transcript.py` and `fetch_transcript_api.py` speak the same
protocol: one JSON object on stdout, errors on stderr, meaning in the exit
code.  That keeps them callable from anywhere -- a shell pipeline, a subagent,
another script -- without anyone having to parse prose.

Loading .env here rather than in each caller means the API key never has to be
passed on a command line, where it would end up in shell history and in any
transcript of the run.  This repo is public; the key lives in .env, which is
gitignored.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# A YouTube id is exactly 11 characters of URL-safe base64.
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# The paths that carry the id directly in the URL rather than in ?v=.
_PATH_FORMS = ("/embed/", "/v/", "/shorts/", "/live/")


def load_env(path: Path | None = None) -> None:
    """Read .env from the repo root into os.environ, without overwriting.

    Deliberately does not overwrite: a variable already exported in the shell
    is a deliberate act and should win over a file.
    """
    path = path or Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def extract_video_id(value: str) -> str:
    """Pull the 11-character video id out of a URL, or validate a bare id.

    Raises ValueError rather than guessing.  A wrong id fetches somebody
    else's video, which is worse than failing.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("no video URL or id given")
    if _VIDEO_ID.match(value):
        return value

    url = urlparse(value if "//" in value else f"https://{value}")
    host = url.hostname or ""
    if host.endswith("youtu.be"):
        candidate = url.path.lstrip("/").split("/")[0]
        if _VIDEO_ID.match(candidate):
            return candidate
        raise ValueError(f"no video id in {value!r}")

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        v = parse_qs(url.query).get("v", [None])[0]
        if v and _VIDEO_ID.match(v):
            return v
        for form in _PATH_FORMS:
            if form in url.path:
                candidate = url.path.split(form, 1)[1].split("/")[0].split("?")[0]
                if _VIDEO_ID.match(candidate):
                    return candidate

    raise ValueError(f"could not find a YouTube video id in {value!r}")


def write_json_result(payload: dict) -> None:
    """The single JSON object each fetcher promises on stdout."""
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def write_error(message: str) -> None:
    """Errors go to stderr so stdout stays parseable as JSON."""
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


load_env()
