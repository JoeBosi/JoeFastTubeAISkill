#!/usr/bin/env python3
"""JoeFastTubeAI — watch a (YouTube) video with per-video caching + saved results.

Output layout, created under the CURRENT working directory (or --base-dir):

  JoeFastTubeAI/
    <video_id>/                  reusable cache for this video (shared by every request)
      download/                  yt-dlp output (video, info.json, subtitles) — reused, saves bandwidth
      transcript.json            full transcript segments — reused, saves Whisper API calls + tokens
      audio.mp3                  extracted audio for Whisper (reused)
      <N>/                       one folder per request (progressive: 1, 2, 3, ...)
        prompt.md                the prompt that generated this run
        report.md                frames list + transcript handed to Claude
        result.md                Claude writes its final answer here
        frames/                  extracted JPEG frames (request-specific)

The truly COMMON, reusable artifacts (download, transcript, audio) live at the
<video_id> level so a re-prompt on the same video does NOT re-download or
re-transcribe. The request-specific artifacts (frames, prompt, result) live in
the numbered request folder.

Prints the same markdown as report.md to stdout so Claude can Read the frames,
answer the user, and then save that answer into result.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import (  # noqa: E402
    _pick_subtitle,
    _pick_video,
    download_url,
    is_url,
    resolve_local,
)
from frames import (  # noqa: E402
    MAX_FPS,
    auto_fps,
    auto_fps_focus,
    extract,
    format_time,
    get_metadata,
    parse_time,
)
from transcribe import filter_range, format_transcript, parse_vtt  # noqa: E402
from whisper import load_api_key, transcribe_video  # noqa: E402


_YT_PATTERNS = (
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
)


def youtube_id(url: str) -> str | None:
    """Pull the 11-char YouTube id from any common URL shape."""
    for pat in _YT_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def compute_video_id(source: str, url_flag: bool) -> str:
    """A stable folder name for this video — used as the cache key."""
    if url_flag:
        yid = youtube_id(source)
        if yid:
            return yid
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:11]
        return f"url-{digest}"
    stem = Path(source).stem
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:40]
    return f"file-{safe or 'video'}"


def next_progressive(cache_dir: Path) -> int:
    """Next free numbered request folder (1, 2, 3, ...) inside the video cache."""
    nums = [
        int(p.name)
        for p in cache_dir.iterdir()
        if p.is_dir() and p.name.isdigit()
    ]
    return max(nums) + 1 if nums else 1


def _read_info(download_dir: Path, source: str) -> dict:
    info_path = download_dir / "video.info.json"
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            return {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or source,
            }
        except Exception:
            pass
    return {"url": source}


def cached_download(source: str, url_flag: bool, download_dir: Path) -> tuple[dict, bool]:
    """Reuse an existing download when present; otherwise fetch via yt-dlp.

    Returns (download_result, from_cache).
    """
    if not url_flag:
        return resolve_local(source), False

    existing = _pick_video(download_dir) if download_dir.exists() else None
    if existing is not None:
        subtitle = _pick_subtitle(download_dir)
        return (
            {
                "video_path": str(existing),
                "subtitle_path": str(subtitle) if subtitle else None,
                "info": _read_info(download_dir, source),
                "downloaded": False,
            },
            True,
        )
    return download_url(source, download_dir), False


def build_transcript(
    dl: dict,
    cache_dir: Path,
    start_sec: float | None,
    end_sec: float | None,
    focused: bool,
    no_whisper: bool,
    whisper_backend: str | None,
) -> tuple[list[dict], str | None, str | None]:
    """Return (segments_in_range, transcript_text, source_label).

    Caches the FULL Whisper transcript to cache_dir/transcript.json so re-prompts
    on the same video never re-hit the API (saving money + tokens). Native
    captions are re-parsed from the cached VTT each time (free + fast).
    """
    transcript_cache = cache_dir / "transcript.json"

    # 1) Native captions (free) — re-parsed from the cached VTT.
    if dl.get("subtitle_path"):
        try:
            all_segments = parse_vtt(dl["subtitle_path"])
            seg = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
            return seg, format_transcript(seg), "captions"
        except Exception as exc:
            print(f"[JoeFastTubeAI] subtitle parse failed: {exc}", file=sys.stderr)

    # 2) Cached Whisper transcript from a previous request on this video.
    if transcript_cache.exists():
        try:
            cached = json.loads(transcript_cache.read_text(encoding="utf-8"))
            all_segments = cached.get("segments") or []
            if all_segments:
                label = f"whisper ({cached.get('backend', 'cached')}, cached)"
                seg = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                return seg, format_transcript(seg), label
        except Exception as exc:
            print(f"[JoeFastTubeAI] transcript cache read failed: {exc}", file=sys.stderr)

    # 3) Fresh Whisper call, then cache it for next time.
    if not no_whisper:
        backend, api_key = load_api_key(whisper_backend)
        if backend and api_key:
            try:
                all_segments, used_backend = transcribe_video(
                    dl["video_path"],
                    cache_dir / "audio.mp3",
                    backend=backend,
                    api_key=api_key,
                )
                try:
                    transcript_cache.write_text(
                        json.dumps({"backend": used_backend, "segments": all_segments}),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                seg = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                return seg, format_transcript(seg), f"whisper ({used_backend})"
            except SystemExit as exc:
                print(f"[JoeFastTubeAI] whisper fallback failed: {exc}", file=sys.stderr)
        else:
            setup_py = SCRIPT_DIR / "setup.py"
            hint = (
                f"--whisper {whisper_backend} set but its API key is missing"
                if whisper_backend else
                "no subtitles and no Whisper API key found"
            )
            print(
                f"[JoeFastTubeAI] {hint} — run `python3 {setup_py}` to enable Whisper",
                file=sys.stderr,
            )

    return [], None, None


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="JoeFastTubeAI",
        description="Watch a video with per-video caching; save prompt + result to disk.",
    )
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument(
        "--prompt", default=None,
        help="User prompt/question that triggered this run (saved verbatim to prompt.md)",
    )
    ap.add_argument(
        "--base-dir", default="JoeFastTubeAI",
        help="Root output folder (default: ./JoeFastTubeAI in the current directory)",
    )
    ap.add_argument("--max-frames", type=int, default=80, help="Cap on frame count (default 80, hard max 100)")
    ap.add_argument(
        "--resolution", type=int, default=None,
        help="Frame width in pixels. Default: 512 for a full scan, 1920 (Full HD) for a "
             "focused --start/--end pass so on-screen text stays readable.",
    )
    ap.add_argument("--fps", type=float, default=None, help="Override auto-fps (max 2)")
    ap.add_argument("--start", default=None, help="Range start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", default=None, help="Range end (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--no-whisper", action="store_true", help="Disable the Whisper fallback")
    ap.add_argument(
        "--whisper", choices=["groq", "openai"], default=None,
        help="Force a Whisper backend (default: prefer Groq, fall back to OpenAI)",
    )
    args = ap.parse_args()

    max_frames = min(args.max_frames, 100)
    url_flag = is_url(args.source)
    video_id = compute_video_id(args.source, url_flag)

    base_dir = Path(args.base_dir).expanduser().resolve()
    cache_dir = base_dir / video_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    download_dir = cache_dir / "download"

    progressive = next_progressive(cache_dir)
    req_dir = cache_dir / str(progressive)
    frames_dir = req_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"[JoeFastTubeAI] video id: {video_id}", file=sys.stderr)
    print(f"[JoeFastTubeAI] request #{progressive} -> {req_dir}", file=sys.stderr)

    # --- write prompt.md up front so it survives even if a later step fails ---
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt_text = (args.prompt or "").strip() or "(nessun prompt: riassunto predefinito del video)"
    (req_dir / "prompt.md").write_text(
        f"# Prompt — richiesta #{progressive}\n\n"
        f"- **Data:** {now}\n"
        f"- **Video id:** `{video_id}`\n"
        f"- **Sorgente:** {args.source}\n\n"
        f"## Testo del prompt\n\n{prompt_text}\n",
        encoding="utf-8",
    )
    # Placeholder so the request folder is structurally complete before Claude answers.
    result_path = req_dir / "result.md"
    if not result_path.exists():
        result_path.write_text("_(in attesa della risposta di Claude...)_\n", encoding="utf-8")

    # --- download (reuse cache when possible) ---
    if url_flag:
        print("[JoeFastTubeAI] checking download cache...", file=sys.stderr)
    dl, from_cache = cached_download(args.source, url_flag, download_dir)
    if from_cache:
        print("[JoeFastTubeAI] reused cached download — 0 bandwidth used", file=sys.stderr)
    video_path = dl["video_path"]

    meta = get_metadata(video_path)
    full_duration = meta["duration_seconds"]

    start_sec = parse_time(args.start)
    end_sec = parse_time(args.end)
    if start_sec is not None and start_sec < 0:
        raise SystemExit("--start must be non-negative")
    if end_sec is not None and start_sec is not None and end_sec <= start_sec:
        raise SystemExit("--end must be greater than --start")
    if full_duration > 0 and start_sec is not None and start_sec >= full_duration:
        raise SystemExit(f"--start {start_sec:.1f}s is past end of video ({full_duration:.1f}s)")

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_sec is not None or end_sec is not None

    # Auto-zoom (Problema 2): a focused pass exists to read detail, so default it to
    # Full HD (1920px) unless --resolution was set explicitly. The panoramic full-video
    # pass stays at 512px to keep the token budget sane.
    resolution = args.resolution if args.resolution is not None else (1920 if focused else 512)

    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=max_frames)
    else:
        fps, target = auto_fps(effective_duration, max_frames=max_frames)
    if args.fps is not None:
        fps = min(args.fps, MAX_FPS)
        target = max(1, int(round(fps * effective_duration)))

    scope = (
        f"{format_time(effective_start)}-{format_time(effective_end)} ({effective_duration:.1f}s)"
        if focused else f"full {effective_duration:.1f}s"
    )
    print(f"[JoeFastTubeAI] extracting ~{target} frames at {fps:.3f} fps over {scope}...", file=sys.stderr)

    frames = extract(
        video_path,
        frames_dir,
        fps=fps,
        resolution=resolution,
        max_frames=max_frames,
        start_seconds=start_sec,
        end_seconds=end_sec,
    )

    segments, transcript_text, transcript_source = build_transcript(
        dl, cache_dir, start_sec, end_sec, focused, args.no_whisper, args.whisper,
    )

    info = dl.get("info") or {}

    # ---- build the markdown report (printed to stdout AND saved to report.md) ----
    out: list[str] = []

    def w(line: str = "") -> None:
        out.append(line)

    w("# JoeFastTubeAI — video report")
    w()
    w(f"- **Richiesta:** #{progressive}")
    w(f"- **Video id:** `{video_id}`")
    w(f"- **Source:** {args.source}")
    if info.get("title"):
        w(f"- **Title:** {info['title']}")
    if info.get("uploader"):
        w(f"- **Uploader:** {info['uploader']}")
    w(f"- **Duration:** {format_time(full_duration)} ({full_duration:.1f}s)")
    if from_cache:
        w("- **Download:** riusato dalla cache (nessuna banda consumata)")
    if focused:
        w(
            f"- **Focus range:** {format_time(effective_start)} -> {format_time(effective_end)} "
            f"({effective_duration:.1f}s)"
        )
    if meta.get("width") and meta.get("height"):
        w(f"- **Resolution:** {meta['width']}x{meta['height']} ({meta.get('codec') or 'unknown codec'})")
    mode = "focused" if focused else "full"
    w(f"- **Frames:** {len(frames)} @ {fps:.3f} fps, {mode} mode (budget {target}, max {max_frames})")
    w(f"- **Frame size:** {resolution}px wide{' (Full HD, auto-zoom)' if focused and resolution >= 1920 else ''}")
    if segments:
        in_range = " in range" if focused else ""
        w(f"- **Transcript:** {len(segments)} segments{in_range} (via {transcript_source or 'captions'})")
    else:
        w("- **Transcript:** none available")

    if not focused and full_duration > 600:
        mins = int(full_duration // 60)
        w()
        w(
            f"> **Warning:** {mins}-minute video — frame coverage is sparse at this length. "
            "Re-run with `--start HH:MM:SS --end HH:MM:SS` to zoom into a specific section."
        )

    w()
    w("## Frames")
    w()
    w(f"Frames live at: `{frames_dir}`")
    w()
    w(
        "**Read each frame path below with the Read tool to view the image.** "
        "Frames are in chronological order; `t=MM:SS` is the absolute timestamp in the source video."
    )
    w()
    for frame in frames:
        w(f"- `{frame['path']}` (t={format_time(frame['timestamp_seconds'])})")

    w()
    w("## Transcript")
    w()
    if transcript_text:
        label = transcript_source or "captions"
        if focused:
            w(f"_Source: {label}. Filtered to {format_time(effective_start)} -> {format_time(effective_end)}:_")
        else:
            w(f"_Source: {label}._")
        w()
        w("```")
        w(transcript_text)
        w("```")
    else:
        w("_No transcript available — proceed with frames only._")

    w()
    w("---")
    w(f"_Cache dir (reusable): `{cache_dir}`_")
    w(f"_Request dir: `{req_dir}`_")

    report = "\n".join(out)
    print()
    print(report)

    (req_dir / "report.md").write_text(report + "\n", encoding="utf-8")

    # Explicit machine-readable marker telling Claude where to save the final answer.
    print()
    print(f"[JoeFastTubeAI] SAVE-RESULT-TO: {result_path}", file=sys.stderr)
    print(
        "[JoeFastTubeAI] Overwrite the placeholder in result.md with your final answer to the user.",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
