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

---

## Requirements

- **Claude Code**
- **ffmpeg** (frame + audio extraction) and **yt-dlp** (downloading)
  - macOS: `brew install ffmpeg yt-dlp` (the skill auto-installs these on first run)
- *(Optional)* a **Groq** or **OpenAI** API key for Whisper, used only when a video
  has no native captions. Most YouTube videos have captions, so this is rarely needed.

---

## Install

JoeFastTubeAI is a **loose skill**: clone this repo into your Claude Code skills folder,
keeping the destination folder named `JoeFastTubeAI` (the folder name becomes the
`/JoeFastTubeAI` command):

```bash
git clone https://github.com/JoeBosi/JoeFastTubeAISkill.git ~/.claude/skills/JoeFastTubeAI
```

Then **restart Claude Code** (`/exit` and reopen). Type `/JoeFastTubeAI` — it shows up in
the slash menu as a single entry.

> **Update later:** `git -C ~/.claude/skills/JoeFastTubeAI pull`, then restart.
> **Uninstall:** `rm -rf ~/.claude/skills/JoeFastTubeAI`, then restart.

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

## Configuration — the `.env` file (Whisper keys)

> 📋 A ready-to-copy template lives in **[`.env.example`](./.env.example)** at the repo root.

A Whisper API key is **optional**. It is used **only** to transcribe videos that have **no
captions** — and most YouTube videos already have captions, so you may never need it. When
it *is* needed, the key is read from a small text file:

```
~/.config/JoeFastTubeAI/.env
```

### Step 1 — create the file

**Option A — let the skill create it for you (easiest):**

```bash
python3 ~/.claude/skills/JoeFastTubeAI/scripts/setup.py
```

This creates `~/.config/JoeFastTubeAI/.env` pre-filled with commented placeholders and the
correct private permissions (`600`). Then just open it and paste your key.

**Option B — create it by hand:**

```bash
mkdir -p ~/.config/JoeFastTubeAI
printf 'GROQ_API_KEY=%s\n' 'gsk_paste_your_key_here' > ~/.config/JoeFastTubeAI/.env
chmod 600 ~/.config/JoeFastTubeAI/.env
```

### Step 2 — what to put inside

The file is plain `KEY=value`, one per line — **no quotes, no spaces around the `=`**:

```dotenv
# Whisper transcription — used only when a video has no captions.
# Set just ONE of these (Groq is enough).

GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx
# optional paid fallback:
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

| Provider | Model | Cost | Get a key | Key looks like |
|----------|-------|------|-----------|----------------|
| **Groq** (preferred) | `whisper-large-v3` | free tier, fast | <https://console.groq.com/keys> | `gsk_…` |
| **OpenAI** (fallback) | `whisper-1` | paid | <https://platform.openai.com/api-keys> | `sk-…` |

Leave **both** blank to disable Whisper entirely — caption-less videos then come back
frames-only (the skill still works).

### Where keys are read from (first match wins)

1. environment variables `GROQ_API_KEY` / `OPENAI_API_KEY`
2. `~/.config/JoeFastTubeAI/.env`  ← **the recommended place**
3. `.env` in the current working directory

> 🔒 **Security.** Keep the file `chmod 600` (only you can read it) and never commit it —
> it is already in `.gitignore`. Only the extracted **audio** is ever uploaded, and only
> when captions are missing and `--no-whisper` was not passed. The video itself is never
> sent anywhere, and the key is never written to logs or output.

---

## How it works

```
URL ──► yt-dlp ──► video + captions          (cached per video-id)
                      │
        ffmpeg ◄──────┘
          │
   frames/*.jpg  +  transcript  ──►  Claude reads frames, answers, writes result.md
```

`scripts/joefasttube.py` is the entry point; the heavy lifting is in `download.py`,
`frames.py`, `transcribe.py`, `whisper.py` and `setup.py`.

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

## License

By **Giuseppe Bosi**. Released under the **MIT License** — see [LICENSE](./LICENSE).
