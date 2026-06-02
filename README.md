# JoeFastTubeAI 🎬

**Give Claude Code a video input — and keep every result on disk.**

JoeFastTubeAI lets Claude *watch* a video (YouTube, Vimeo, TikTok, X, Twitch clips,
or a local file). It downloads the video, extracts frames, pulls the transcript
from captions (or Whisper), and hands both to Claude so it can answer questions or
summarize. Unlike a throwaway approach, **it caches per video and saves every run**:

- 🗂️ **Persistent output.** Each request is saved to
  `JoeFastTubeAI/<video-id>/<N>/` with its `prompt.md`, the extracted `frames/`,
  a `report.md`, and `result.md` (Claude's final answer).
- ♻️ **Per-video cache.** The download and transcript live at
  `JoeFastTubeAI/<video-id>/` and are **reused** on later questions about the same
  video — saving bandwidth (no re-download) and tokens/money (no re-transcription).
- 🔢 **Progressive numbering.** Ask three questions about one video and you get
  folders `1/`, `2/`, `3/` — nothing is overwritten.

> Fork of [`watch`](https://github.com/bradautomates/claude-video) by Bradley
> Bonanno, with caching + persistence added. MIT licensed.

---

## Requirements

- **Claude Code**
- **ffmpeg** (frame + audio extraction) and **yt-dlp** (downloading)
  - macOS: `brew install ffmpeg yt-dlp` (the skill auto-installs these on first run)
- *(Optional)* a **Groq** or **OpenAI** API key for Whisper, used only when a video
  has no native captions. Most YouTube videos have captions, so this is rarely needed.

---

## Install

### As a loose skill (simplest)

```bash
git clone https://github.com/JoeBosi/JoeFastTubeAI.git ~/.claude/skills/JoeFastTubeAI
```

Restart Claude Code. Type `/JoeFastTubeAI` — the command appears in the menu.

### As a plugin (marketplace)

This repo ships `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`,
so it can also be added as a plugin marketplace:

```
/plugin marketplace add JoeBosi/JoeFastTubeAI
/plugin install JoeFastTubeAI@joefasttube-ai
```

---

## Usage

```
/JoeFastTubeAI <video-url-or-path> [your question]
```

Examples:

```
/JoeFastTubeAI https://youtu.be/QZMljuD10sU Summarize this video
/JoeFastTubeAI https://youtu.be/abc123 What is said between 2:00 and 2:30?
/JoeFastTubeAI ./demo.mp4 What's shown on screen at the end?
```

You can also just ask in plain language: *"Use JoeFastTubeAI to watch this video: …"*.

### Useful flags

| Flag | Purpose |
|------|---------|
| `--start T` / `--end T` | Focus a section (`SS`, `MM:SS`, `HH:MM:SS`); denser frames, auto-captured in **Full HD (1920px)** |
| `--max-frames N` | Cap frames (default 80, max 100) for a tighter token budget |
| `--resolution W` | Frame width in px. Default **512** for a full scan, **1920** for a focused pass; override only if needed |
| `--no-whisper` | Disable the Whisper fallback (frames-only if no captions) |
| `--base-dir DIR` | Change the root output folder (default `./JoeFastTubeAI`) |

---

## Output layout

```
JoeFastTubeAI/
└── <video-id>/                 # cache shared by every request for this video
    ├── download/               # yt-dlp output (video, info.json, subtitles) — reused
    ├── transcript.json         # cached Whisper transcript (if Whisper was used)
    ├── audio.mp3               # cached extracted audio (if Whisper was used)
    ├── 1/                      # request #1
    │   ├── prompt.md           # the prompt that generated this run
    │   ├── frames/             # extracted JPEG frames
    │   ├── report.md           # frames list + transcript
    │   └── result.md           # Claude's final answer
    ├── 2/                      # request #2 (download/transcript reused from cache)
    └── ...
```

For a YouTube URL the `<video-id>` is the 11-character YouTube id (e.g. `QZMljuD10sU`);
for other URLs it is a short hash; for a local file it is `file-<name>`.

---

## Configuration

Whisper keys (optional) are read from, in order:

1. environment variables `GROQ_API_KEY` / `OPENAI_API_KEY`
2. `~/.config/JoeFastTubeAI/.env`
3. `~/.config/watch/.env` (legacy fallback)
4. `.env` in the current directory

Groq is preferred (cheaper, faster: `whisper-large-v3`); OpenAI (`whisper-1`) is the
fallback. Only the extracted **audio** is ever sent out, and only when captions are
missing and `--no-whisper` was not passed. The video itself is never uploaded.

---

## How it works

```
URL ──► yt-dlp ──► video + captions          (cached per video-id)
                      │
        ffmpeg ◄──────┘
          │
   frames/*.jpg  +  transcript  ──►  Claude reads frames, answers, writes result.md
```

`scripts/joefasttube.py` is the entry point; it reuses `download.py`, `frames.py`,
`transcribe.py`, `whisper.py` and `setup.py` from the upstream pipeline.

---

## Troubleshooting

- **Whisper fails with `CERTIFICATE_VERIFY_FAILED` on macOS.** Fixed in **1.0.1**: the
  skill now builds its HTTPS context from the `certifi` CA bundle automatically. If you
  still hit it, run `pip install certifi` (or the Python *"Install Certificates.command"*).
- **"No transcript available" on a video without captions.** Add a Groq (preferred) or
  OpenAI key — see [Configuration](#configuration). Without a key, caption-less videos
  come back frames-only.

## Auto-zoom & long videos (since 1.1.0)

- **Auto-zoom in Full HD.** Run a focused pass (`--start/--end`) on the moments that matter;
  frames there are captured at **1920px** automatically, so on-screen text (charts, code,
  terminals) stays readable. The skill scans the whole video first, then zooms into the key
  moments the narrator points at.
- **Long caption-less videos.** If a video has no captions and its audio exceeds the Whisper
  25 MB limit (~50 min), the audio is automatically split into chunks, each transcribed, and
  the timestamps are stitched back onto the absolute timeline — no action needed.

## Credits & License

Forked from **[bradautomates/claude-video](https://github.com/bradautomates/claude-video)**
(the `watch` skill) by Bradley Bonanno. Caching, persistence and the JoeFastTubeAI
packaging by Giuseppe Bosi. Released under the **MIT License** — see [LICENSE](./LICENSE).
