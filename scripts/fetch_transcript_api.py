#!/usr/bin/env python3
"""Fetch a YouTube transcript via the hosted transcript API (server-side).

Unlike fetch_youtube_transcript.py (which uses youtube-transcript-api / yt-dlp and is blocked
from data-center IPs *and*, increasingly, from residential IPs too), this hits a hosted service
that fetches the captions server-side. So it works from ANY IP, needs NO venv (standard library
only), and is safe to call in parallel.

Usage:
    fetch_transcript_api.py <video_url_or_id> [--timeout 120]

Environment:
    YT_TRANSCRIPT_API_KEY   (required) — API key for the transcript service
    YT_TRANSCRIPT_API_URL   (optional) — endpoint; defaults to the studio's hosted service

Prints a single JSON object to stdout:
    { video_id, url, transcript, char_count, source }
Exit codes:
    0 success
    2 bad args / missing API key
    3 network/API failure
    4 no transcript returned
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from _common import extract_video_id, write_json_result, write_error

DEFAULT_API_URL = "https://yt-index-nzwhf.ondigitalocean.app/video-transcript"


def _exit(code: int, msg: str = "") -> None:
    if msg:
        write_error(msg)
    sys.exit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a YouTube transcript via the hosted API.")
    parser.add_argument("video", help="YouTube URL or video ID")
    parser.add_argument("--timeout", type=int, default=120, help="Request timeout in seconds (default 120)")
    args = parser.parse_args()

    api_key = os.environ.get("YT_TRANSCRIPT_API_KEY")
    if not api_key:
        _exit(2, "YT_TRANSCRIPT_API_KEY is not set in the environment")
    api_url = os.environ.get("YT_TRANSCRIPT_API_URL", DEFAULT_API_URL)

    try:
        video_id = extract_video_id(args.video)
    except ValueError as e:
        _exit(2, str(e))

    url = f"https://www.youtube.com/watch?v={video_id}"
    payload = json.dumps({"urls": [url]}).encode("utf-8")
    req = urllib.request.Request(api_url, data=payload, method="POST")
    req.add_header("X-API-KEY", api_key)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _exit(3, f"Transcript API HTTP {e.code}: {e.reason}")
    except Exception as e:
        _exit(3, f"Transcript API request failed: {e}")

    transcript = ""
    if isinstance(data, dict):
        transcript = data.get("transcript") or ""
    if not transcript:
        _exit(4, f"No transcript returned for {video_id}")

    write_json_result({
        "video_id": video_id,
        "url": url,
        "transcript": transcript,
        "char_count": len(transcript),
        "source": "yt-index-api",
    })


if __name__ == "__main__":
    main()
