"""YouTube transcript parser (uses optional youtube-transcript-api [MIT] or transcript JSON)."""
import json
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse
from app.ingest.models import ParsedDoc


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url.lower())
    return parsed.netloc in ("youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com")


def extract_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.netloc in ("youtu.be",):
        return parsed.path.lstrip("/").split("/")[0] or None
    if "youtube.com" in parsed.netloc:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        m = re.match(r"^/(?:embed|shorts|v)/([^/?]+)", parsed.path)
        if m:
            return m.group(1)
    return None


def _fmt_timestamp(seconds: float) -> str:
    total = int(seconds)
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_transcript_segments(
    segments: list[dict[str, Any]],
    video_id: str,
    url: Optional[str] = None,
    title: Optional[str] = None,
) -> ParsedDoc:
    lines: list[str] = []
    timestamps: list[dict[str, Any]] = []
    cursor = 0

    for seg in segments:
        start_sec = float(seg.get("start", 0.0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        ts_str = _fmt_timestamp(start_sec)
        line = f"[{ts_str}] {text}"
        lines.append(line)
        timestamps.append({"timestamp": ts_str, "start_sec": start_sec, "char_start": cursor, "char_end": cursor + len(line)})
        cursor += len(line) + 1

    return ParsedDoc(
        text="\n".join(lines).strip(),
        title=title or f"YouTube Video ({video_id})",
        kind="web",
        metadata={
            "format": "youtube",
            "video_id": video_id,
            "url": url,
            "timestamps": timestamps,
        },
    )


def parse_youtube_transcript_json(raw_bytes: bytes, filename: str = "transcript.json", title: Optional[str] = None) -> ParsedDoc:
    data = json.loads(raw_bytes.decode("utf-8"))
    segments = data if isinstance(data, list) else data.get("segments", [])
    return format_transcript_segments(segments, video_id=filename, title=title)


def fetch_youtube_transcript(url: str, title: Optional[str] = None) -> ParsedDoc:
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Could not extract YouTube video ID from URL: {url}")

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise RuntimeError(
            "youtube-transcript-api is not installed. Install optional extra '[web]' to fetch live YouTube transcripts."
        )

    transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
    return format_transcript_segments(transcript_list, video_id=video_id, url=url, title=title)
