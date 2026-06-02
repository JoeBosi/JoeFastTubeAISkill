#!/usr/bin/env python3
"""Transcribe a video via Groq or OpenAI Whisper API.

Strategy: extract audio (mono 16kHz mp3, tiny payload), upload to whichever
API has a key. Returns segments in the same shape as transcribe.parse_vtt so
the rest of the pipeline (filter_range, format_transcript) doesn't care where
the transcript came from.

Pure stdlib — no `pip install groq` or `pip install openai` needed.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). Prefers Groq, falls back to OpenAI.

    If `preferred` is "groq" or "openai", only that backend's key is considered.
    """
    def _from_env(name: str) -> str | None:
        value = os.environ.get(name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        Path.home() / ".config" / "JoeFastTubeAI" / ".env",
        Path.home() / ".config" / "watch" / ".env",  # legacy fallback (upstream `watch` skill)
        Path.cwd() / ".env",
    ]

    candidates = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _from_env(key_name)
        if not value:
            for candidate in dotenv_paths:
                value = _from_dotenv(candidate, key_name)
                if value:
                    break
        if value:
            return backend, value

    return None, None


def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context with a usable CA bundle.

    macOS python.org builds frequently ship without an initialized CA bundle
    (the "Install Certificates.command" step is never run), which makes every
    HTTPS request fail with CERTIFICATE_VERIFY_FAILED. Prefer the certifi bundle
    when available, then fall back to common system bundles, then the default.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for candidate in ("/etc/ssl/cert.pem", "/opt/homebrew/etc/ca-certificates/cert.pem"):
        if os.path.exists(candidate):
            try:
                return ssl.create_default_context(cafile=candidate)
            except Exception:
                continue
    return ssl.create_default_context()


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = _ssl_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _segments_from_response(data: dict) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


WHISPER_DEFAULT_MAX_MB = 24  # safety margin under the 25 MB API hard limit


def _max_upload_bytes() -> int:
    """Max bytes per Whisper request. Overridable via JFT_WHISPER_MAX_MB (used in tests)."""
    override = os.environ.get("JFT_WHISPER_MAX_MB")
    if override:
        try:
            return max(1, int(float(override) * 1024 * 1024))
        except ValueError:
            pass
    return WHISPER_DEFAULT_MAX_MB * 1024 * 1024


def _audio_duration(path: Path) -> float:
    """Seconds of audio in a file (ffprobe). 0.0 if unknown."""
    if shutil.which("ffprobe") is None:
        return 0.0
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _split_audio(audio_path: Path, chunk_seconds: int) -> list[Path]:
    """Split an mp3 into <=chunk_seconds segments with ffmpeg. Returns chunk paths in order."""
    chunk_dir = audio_path.parent / "audio_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for old in chunk_dir.glob("chunk_*.mp3"):
        old.unlink()
    pattern = str(chunk_dir / "chunk_%03d.mp3")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(audio_path.resolve()),
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-c", "copy", pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio split failed: {result.stderr.strip()}")
    return sorted(chunk_dir.glob("chunk_*.mp3"))


def _upload(backend: str, api_key: str, audio_path: Path) -> dict:
    if backend == "groq":
        return _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path)
    if backend == "openai":
        return _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path)
    raise SystemExit(f"Unknown whisper backend: {backend}")


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → (split if needed) → upload → parse segments.

    For caption-less videos whose audio exceeds the Whisper 25 MB upload limit, the
    audio is split into time-based chunks; each chunk is transcribed and its segment
    timestamps are shifted back onto the absolute video timeline, then merged.
    (roadmap "Problema 1")

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper API key available. Set GROQ_API_KEY (preferred) or OPENAI_API_KEY "
            "in the environment or in ~/.config/JoeFastTubeAI/.env. "
            f"Run `python3 {setup_py}` to configure."
        )

    print(f"[JoeFastTubeAI] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    size = audio_path.stat().st_size
    max_bytes = _max_upload_bytes()

    # Small enough → single request (the common case).
    if size <= max_bytes:
        print(f"[JoeFastTubeAI] audio: {size / 1024:.0f} kB — uploading to {backend} Whisper…", file=sys.stderr)
        segments = _segments_from_response(_upload(backend, api_key, audio_path))
        if not segments:
            raise SystemExit("Whisper returned no transcript segments")
        print(f"[JoeFastTubeAI] transcribed {len(segments)} segments via {backend}", file=sys.stderr)
        return segments, backend

    # Too big → chunk it (Problema 1).
    duration = _audio_duration(audio_path)
    bytes_per_sec = (size / duration) if duration > 0 else (64_000 / 8)
    chunk_seconds = max(30, int((max_bytes * 0.9) / bytes_per_sec))
    chunks = _split_audio(audio_path, chunk_seconds)
    if not chunks:
        raise SystemExit("audio splitting produced no chunks")
    print(
        f"[JoeFastTubeAI] audio {size / 1024 / 1024:.1f} MB exceeds {max_bytes / 1024 / 1024:.0f} MB limit — "
        f"split into {len(chunks)} chunks of ~{chunk_seconds}s",
        file=sys.stderr,
    )

    all_segments: list[dict] = []
    offset = 0.0
    for i, chunk in enumerate(chunks, 1):
        print(
            f"[JoeFastTubeAI] transcribing chunk {i}/{len(chunks)} "
            f"({chunk.stat().st_size / 1024 / 1024:.1f} MB)…",
            file=sys.stderr,
        )
        segs = _segments_from_response(_upload(backend, api_key, chunk))
        for s in segs:
            s["start"] = round(s["start"] + offset, 2)
            s["end"] = round(s["end"] + offset, 2)
        all_segments.extend(segs)
        offset += _audio_duration(chunk)

    if not all_segments:
        raise SystemExit("Whisper returned no transcript segments")
    print(
        f"[JoeFastTubeAI] transcribed {len(all_segments)} segments via {backend} "
        f"({len(chunks)} chunks)",
        file=sys.stderr,
    )
    return all_segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper.py <video-path> [<audio-out.mp3>] [--backend groq|openai]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
