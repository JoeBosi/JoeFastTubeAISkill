---
name: JoeFastTubeAI
description: Watch a video (URL or local path) — download with yt-dlp, extract frames with ffmpeg, pull the transcript from captions (or Whisper) — with per-video caching and persistent output. Every run is saved under ./JoeFastTubeAI/<video-id>/<N>/ (prompt.md + result.md + frames), and the heavy reusable artifacts (download, transcript) are cached per video so re-prompts skip re-downloading and re-transcribing.
argument-hint: "<video-url-or-path> [question]"
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/JoeBosi/JoeFastTubeAISkill
author: Giuseppe Bosi
license: MIT
user-invocable: true
---

# /JoeFastTubeAI — watch a video, cache it, and save the result to disk

This skill gives Claude a video input: it downloads the video, extracts frames, builds a
transcript (captions or Whisper) and lets Claude answer — and additionally:

1. **Caches per video.** The downloaded video, subtitles and Whisper transcript are
   stored under `JoeFastTubeAI/<video-id>/` and **reused** on any later request for
   the same video — saving bandwidth (no re-download) and tokens/money (no re-transcribe).
2. **Persists every request.** Each run gets its own numbered folder
   `JoeFastTubeAI/<video-id>/<N>/` containing `prompt.md` (the prompt that triggered it),
   `frames/`, `report.md`, and `result.md` (your final answer). `N` auto-increments,
   so multiple questions about the same video never overwrite each other.

```
JoeFastTubeAI/
  <video-id>/                COMMON, reusable across requests
    download/                yt-dlp output (video + info.json + subtitles)
    transcript.json          cached Whisper transcript (if used)
    audio.mp3                cached extracted audio (if Whisper used)
    1/   2/   3/ ...          NON-common, one folder per request
      prompt.md              the prompt that generated this run
      frames/                extracted JPEG frames
      report.md              frames list + transcript
      result.md              <-- YOU save your final answer here
```

## Step 0 — Setup preflight (silent on success)

On **Windows** substitute `python` for `python3`. Before running, verify deps + key:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

Exit 0 → emit nothing, proceed. On non-zero exit:

| Exit | Meaning | Action |
|------|---------|--------|
| `2` | Missing `ffmpeg` / `ffprobe` / `yt-dlp` | Run `python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"` |
| `3` | No Whisper API key | Run installer, then ask the user for a Groq/OpenAI key via `AskUserQuestion` |
| `4` | Both missing | Run installer, then ask for a key |

The installer auto-installs ffmpeg/yt-dlp via Homebrew on macOS and scaffolds
`~/.config/JoeFastTubeAI/.env`. Whisper is optional:
videos with native captions (most of YouTube) work with no key. If the user declines a
key, run with `--no-whisper` and tell them caption-less videos come back frames-only.
Within a session, skip Step 0 on follow-up calls once `--check` returned 0.

## How to invoke

**Step 1 — parse input.** Split the video source (URL or path) from the user's question.
Example: `/JoeFastTubeAI https://youtu.be/abc what language is this?`
→ source = `https://youtu.be/abc`, question = `what language is this?`

**Step 2 — run the script.** ALWAYS pass `--prompt` with the user's request verbatim
(or a short description like "riassunto del video" if they didn't ask anything specific),
so it gets saved into `prompt.md`:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/joefasttube.py" "<source>" --prompt "<user question>"
```

The script prints (to stderr) the `video id`, the request number `#N`, and whether the
download was reused from cache. It prints the markdown report to stdout.

Optional flags:
- `--start T` / `--end T` — focus a section (`SS`, `MM:SS`, `HH:MM:SS`); fps auto-densifies.
- `--max-frames N` — lower the cap for a tighter token budget (e.g. `--max-frames 40`).
- `--resolution W` — frame width in px. Default **512** for a full scan; a focused `--start/--end` pass auto-captures **1920 (Full HD)** so on-screen text stays readable (Problema 2). Set explicitly only to override.
- `--fps F` — override auto-fps (max 2).
- `--no-whisper` — disable the Whisper fallback (frames-only if no captions).
- `--whisper groq|openai` — force a backend.
- `--base-dir DIR` — change the root output folder (default: `./JoeFastTubeAI`).

> **Long caption-less videos (Problema 1):** if a video has no captions and its audio
> exceeds the 25 MB Whisper limit (~50 min), the script automatically splits the audio
> into chunks, transcribes each, and stitches the absolute timeline back — no action needed.

**Step 3 — Read every frame path** the report lists, in a single message (parallel
Read calls) so you see them together. Each has a `t=MM:SS` absolute timestamp.

**Step 4 — answer the user** in chat. Combine frames (what's on screen) with the
transcript (what's said), citing timestamps. If they asked nothing, summarize the video.

**Step 4.5 — Auto-zoom, the second pass (Problema 2).** Do this for any "summarize /
explain / make a doc" request, unless the user opted out or the video is very short. The
first run is a *panoramic* scan (sparse 512px frames). Now read the transcript and find
the moments where the narrator **points at something on screen** — deictic cues such as
"look here", "this chart", "this level", "notice…", "guardate qui", "questo grafico", or
any spot where the spoken words imply on-screen detail a sparse 512px frame can't resolve.
Pick up to ~3–5 short windows and re-run the script focused on each; it auto-captures
**Full HD (1920px)** frames there:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/joefasttube.py" "<source>" --prompt "<question>" --start MM:SS --end MM:SS
```

Each focused run reuses the cached download (no re-download) and creates its own request
folder. Read those sharper frames and use them as the key images in the document.

**Step 5 — SAVE YOUR ANSWER as a well-structured `.md` (required).** Use the **panoramic
run's** `result.md` path (the first `[JoeFastTubeAI] SAVE-RESULT-TO:` you were given) and
write your final answer there with the Write tool, overwriting the placeholder. **Unless
the user asks otherwise, `result.md` MUST follow these conventions (Problema 3):**

1. **Clickable table of contents at the top** — a bulleted list linking to every section
   via Markdown heading anchors, e.g. `- [ETF e deflussi](#2-etf-e-deflussi)`.
2. **Reorganized by concept, not by narration order** — group and order content for the
   clearest understanding, not in the sequence the speaker happened to say it.
3. **Keep English technical terms in English** — do not translate jargon (e.g. *support*,
   *inflow*, *bear market*, *prompt*, *commit*); translate only the surrounding prose.
4. **Lose nothing important** — preserve names of people/places, techniques, explained
   procedures, specific commands, key figures and keywords. Reorganize, don't drop.
5. **Embed the key images** with relative paths and a short caption + timestamp: a
   panoramic frame as `![caption](frames/frame_0007.jpg)`; a Full-HD auto-zoom frame from
   another request folder as `![caption](../<M>/frames/frame_0003.jpg)`.

The result must end up on disk next to its `prompt.md` — that is the whole point of the skill.

## Caching behavior (what to tell the user)

- **First time** on a video: it downloads + extracts + (maybe) transcribes. Folder `1/`.
- **Re-prompt** on the same video: the download and transcript are reused from
  `JoeFastTubeAI/<video-id>/`; only new frames + a new `<N>/` folder are produced. Mention
  in your answer when the download was reused ("riusato dalla cache, nessuna banda consumata").
- Do **NOT** delete the `JoeFastTubeAI/` folder — this output is meant to persist; the cache
  is what makes future runs cheap.

## Token efficiency

Frames dominate token cost (~50–80k for 80 frames at 512px; a **Full HD 1920px** frame
costs roughly 10–14× a 512px one, so keep auto-zoom passes to short windows with few
frames). The transcript is cheap.
If you already watched a video this session and the user asks a follow-up that the frames
+ transcript already in context can answer, just answer — no need to re-run the script.

## Security & Permissions

Runs `yt-dlp`/`ffmpeg`/`ffprobe` locally; only the
extracted audio (never the video) is sent to Groq/OpenAI Whisper, and only when native
captions are missing and Whisper is not disabled. Reads/creates `~/.config/JoeFastTubeAI/.env`
(mode 0600) for API keys. The only new behavior is that output is written to
`./JoeFastTubeAI/...` in the working directory instead of a throwaway temp dir.

**Bundled scripts:** `scripts/joefasttube.py` (entry point, caching + persistence),
plus the reused `download.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`.
