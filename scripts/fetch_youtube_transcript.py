#!/usr/bin/env python3
"""Fetch transcript + metadata for a single YouTube video.

Usage:
    fetch_youtube_transcript.py <video_url_or_id> [--lang en]

Prints a single JSON object to stdout. Exit codes:
    0 success
    2 bad args
    3 network/API failure
    4 no transcript available
    5 video not found
"""

import argparse
import sys

from _common import extract_video_id, write_json_result, write_error


def _exit(code: int, msg: str = "") -> "typing.NoReturn":
    if msg:
        write_error(msg)
    sys.exit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a YouTube transcript + metadata.")
    parser.add_argument("video", help="YouTube URL or video ID")
    parser.add_argument("--lang", default="en", help="Preferred caption language (default: en)")
    args = parser.parse_args()

    try:
        video_id = extract_video_id(args.video)
    except ValueError as e:
        _exit(2, str(e))

    # Lazy imports so --help doesn't pay the cost
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
    )
    import yt_dlp

    # Fetch metadata via yt-dlp (extract_info without download)
    metadata = None
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
            metadata = {
                "title": info.get("title"),
                "channel": info.get("uploader") or info.get("channel"),
                "published_at": _format_upload_date(info.get("upload_date")),
                "duration_seconds": info.get("duration"),
            }
    except Exception as e:
        if "Video unavailable" in str(e) or "404" in str(e):
            _exit(5, f"Video not found: {video_id}")
        _exit(3, f"Failed to fetch video metadata: {e}")

    # Fetch transcript (youtube-transcript-api >= 1.0 API)
    try:
        ytt_api = YouTubeTranscriptApi()
        listing = ytt_api.list(video_id)
        # Prefer manually-uploaded in requested language; fall back to auto-generated;
        # fall back to translating from any available transcript.
        transcript_obj = None
        try:
            transcript_obj = listing.find_manually_created_transcript([args.lang])
        except NoTranscriptFound:
            try:
                transcript_obj = listing.find_generated_transcript([args.lang])
            except NoTranscriptFound:
                # Last resort: translate from any available
                for t in listing:
                    if t.is_translatable:
                        transcript_obj = t.translate(args.lang)
                        break
        if transcript_obj is None:
            _exit(4, f"No transcript available for video {video_id} in lang {args.lang}")
        fetched = transcript_obj.fetch()
        # FetchedTranscript.to_raw_data() -> list of {"text","start","duration"} dicts
        transcript = [
            {"start": item["start"], "duration": item["duration"], "text": item["text"]}
            for item in fetched.to_raw_data()
        ]
        actual_lang = transcript_obj.language_code
        is_auto = transcript_obj.is_generated
    except TranscriptsDisabled:
        _exit(4, f"Transcripts disabled for video {video_id}")
    except VideoUnavailable:
        _exit(5, f"Video unavailable: {video_id}")
    except Exception as e:
        _exit(3, f"Failed to fetch transcript: {e}")

    write_json_result({
        "video_id": video_id,
        "url": f"https://youtube.com/watch?v={video_id}",
        "title": metadata["title"],
        "channel": metadata["channel"],
        "published_at": metadata["published_at"],
        "duration_seconds": metadata["duration_seconds"],
        "transcript": transcript,
        "transcript_language": actual_lang,
        "transcript_is_auto_generated": is_auto,
    })


def _format_upload_date(raw: str | None) -> str | None:
    """yt-dlp returns upload_date as 'YYYYMMDD'. Convert to 'YYYY-MM-DD'."""
    if not raw or len(raw) != 8:
        return raw
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


if __name__ == "__main__":
    main()
