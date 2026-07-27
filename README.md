# interview-coach

Record your technical interview locally on your Mac, transcribe it **on-device**, and
get an honest post-interview gap report: every question asked, how you answered,
which hints the interviewer gave that you missed, where you deflected, which
concepts were weak, and exactly what to study before the next one.

Built for people re-entering interviews after a layoff who keep hearing "we went
with another candidate" without knowing why.

**This is a self-improvement tool, not a cheating tool.** It gives no live help
during the interview — it only analyzes afterward.

## How it works

```
mic ────────────► mic.wav      (you = CANDIDATE)          ─┐
                                                            ├─► mlx-whisper (on-device)
system audio ───► system.wav   (Zoom/Meet = INTERVIEWER)  ─┘        │
                                                            merged transcript
                                                                    │
                                                            Claude gap analysis
                                                                    │
                                                            report.html (local)
```

Capturing the two sides as **separate audio channels** gives perfect speaker
attribution with zero ML diarization. Audio and transcripts never leave your Mac;
only the transcript text is sent to Claude for the analysis step.

## Requirements

- Apple Silicon Mac, macOS 14.4+
- Xcode Command Line Tools (`xcode-select --install`)
- [uv](https://docs.astral.sh/uv/) and ffmpeg (`brew install uv ffmpeg`)
- Claude Code installed (`claude` on PATH) **or** `ANTHROPIC_API_KEY` set

## Setup

```bash
git clone <this-repo> && cd interview-coach
./setup.sh
```

## Usage

```bash
./coach record          # start before the interview; Ctrl-C when it ends
```

After you stop, it transcribes (first run downloads the Whisper model, ~1.5 GB),
runs the Claude analysis, and opens `report.html`.

```bash
./coach process <session> [--force]   # re-run the pipeline on a past session
./coach history                        # sessions overview + recurring weak concepts
```

### First-run permissions

macOS will prompt twice, for your **terminal app**:

1. **Microphone**
2. **System Audio Recording** (System Settings → Privacy & Security → Screen & System Audio Recording)

Grant both, then re-run `./coach record`. Do a 30-second test before a real
interview: `./coach record --duration 30 --no-open` while playing any video.

### Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `COACH_WHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | Any mlx-community Whisper repo (use `whisper-tiny` for quick tests) |
| `COACH_CLAUDE_MODEL` | Claude Code's default / `claude-opus-4-8` (SDK) | Model for the analysis step |

## ⚠️ Consent and legality

You are recording a conversation. Many jurisdictions (e.g. several US states)
require **all parties' consent** to record, and some companies prohibit recording
interviews regardless. **You are responsible for ensuring the recording is lawful
and permitted.** The tool reminds you at every recording start, stores everything
locally under `sessions/` (gitignored), and never uploads audio.

## Troubleshooting

- **system.wav is silent** — the System Audio Recording permission wasn't granted
  to your terminal, or you're on macOS < 14.4. Fallback: install
  [BlackHole](https://existential.audio/blackhole/) (`brew install blackhole-2ch`),
  create a Multi-Output Device in Audio MIDI Setup, and record from it.
- **Analysis fails** — make sure `claude` works in your terminal, or export
  `ANTHROPIC_API_KEY`.
- **Multiple interviewers** all appear as one `INTERVIEWER` speaker (they share the
  system-audio channel). Known MVP limitation.

## Session layout

```
sessions/20260727-141500/
├── mic.wav, system.wav    # raw audio (local only)
├── transcript.{json,txt}  # speaker-labeled transcript
├── analysis.json          # structured gap analysis
└── report.html            # open in any browser
```
