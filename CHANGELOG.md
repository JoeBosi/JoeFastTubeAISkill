# Changelog

All notable changes to JoeFastTubeAI are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [1.1.0] — 2026-06-02

### Added
- **Chunked Whisper (Problema 1).** Caption-less videos whose audio exceeds the 25 MB
  Whisper upload limit (~50 min) are transcribed by splitting the audio into time-based
  chunks; each chunk is transcribed and its segment timestamps are shifted back onto the
  absolute video timeline, then merged. The per-request limit is overridable with
  `JFT_WHISPER_MAX_MB` (used in tests).
- **Auto-zoom in Full HD (Problema 2).** A focused `--start/--end` pass now defaults to
  **1920px (Full HD)** capture (panoramic full-video scans stay at 512px). `SKILL.md` gained
  a "Step 4.5 — Auto-zoom" two-pass workflow: scan the whole video, then re-capture the
  moments the narrator points at on screen, sharply.
- **`result.md` output conventions (Problema 3).** `SKILL.md` now requires, by default: a
  clickable table of contents, concept-first reorganization (not narration order), English
  technical terms kept untranslated, no loss of key details (names, procedures, commands,
  figures), and embedded key images.

## [1.0.1] — 2026-06-02

### Fixed
- **macOS SSL verification (`CERTIFICATE_VERIFY_FAILED`).** python.org Python builds
  ship without an initialized CA bundle, so every Whisper call (Groq/OpenAI) failed
  before authentication. The HTTPS context now uses the `certifi` bundle when
  available, falling back to common system bundles (`/etc/ssl/cert.pem`, Homebrew's).

## [1.0.0] — 2026-06-02

First release. Fork of the upstream [`watch`](https://github.com/bradautomates/claude-video)
skill by Bradley Bonanno, extended with caching and persistent output.

### Added
- **Per-video cache.** The downloaded video, subtitles and (if used) the Whisper
  transcript are stored under `JoeFastTubeAI/<video-id>/` and reused on any later
  request for the same video — no re-download, no re-transcription.
- **Persistent, numbered results.** Every request gets its own folder
  `JoeFastTubeAI/<video-id>/<N>/` containing `prompt.md` (the prompt that triggered
  it), `frames/`, `report.md`, and `result.md` (Claude's final answer). `N`
  auto-increments so multiple questions about the same video never overwrite.
- **Independent configuration** at `~/.config/JoeFastTubeAI/.env`, with a read-only
  fallback to the legacy `~/.config/watch/.env` so existing Whisper keys keep working.
- New entry point `scripts/joefasttube.py` and slash command `/JoeFastTubeAI`.

### Unchanged from upstream
- yt-dlp download, ffmpeg auto-scaled frame extraction, caption/Whisper transcript
  pipeline, `--start`/`--end` focus mode, and the macOS/Homebrew setup preflight.
