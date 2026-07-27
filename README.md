# well-let-you-know

> *"Thanks for your time — we'll let you know."*
> They never do. **This does.**

well-let-you-know records your interview on your Mac, transcribes it privately
on-device, and gives you the honest report the company never sends:

- every question asked, and whether your answer was **strong / partial / missed —
  judged for YOUR experience level** (an answer that's fine at 2 years is a red
  flag at 5; the report says so)
- the **hints the interviewer gave you** — and which ones you didn't pick up
- the moments you **deflected** instead of answering (with your exact words)
- **beyond-the-technical competencies** that decide offers: coachability,
  communication, requirement clarification, ownership stories, trade-off thinking
- the **concepts that are actually weak**, ranked, with what to study
- your talk-time ratio, response delays, and the **top 3 things to fix** next time

Panel interview? It tells interviewer voices apart and attributes each question
(using their introductions for names). Over time it also learns *your* voice, so
attribution keeps getting better with every session.

It works in **English, Hindi, and Hinglish**. It gives **no help during the
interview** — this is a mirror, not a cheat tool. Your audio never leaves your Mac.

Built for people restarting interviews after a layoff who keep hearing
*"we went with another candidate"* without ever learning why.

---

## What you need

- A Mac with an Apple chip (M1/M2/M3/M4/M5 — every Mac from 2021 onwards)
- macOS 14.4 or newer
- about 5 GB of free disk space
- a [Claude](https://claude.com/claude-code) login (used to write the report —
  setup will guide you)

## Install (10 minutes, one-time)

**Step 1 — Open Terminal.** Press `Cmd + Space`, type `Terminal`, press Enter.

**Step 2 — Copy-paste this and press Enter:**

```bash
git clone https://github.com/YOUR_USERNAME/well-let-you-know.git && cd well-let-you-know && ./setup.sh
```

The setup script checks your Mac and **installs everything for you** — it will
ask before installing anything. Just answer the questions.

> If it says *"command line developer tools"* need installing, click **Install**
> in the popup, wait for it to finish, then run `./setup.sh` again.

**Step 3 — Allow the two permissions.** At the end, setup runs a 6-second test
recording. macOS will show two popups — click **Allow** on both:

1. **Microphone** (records your voice)
2. **System Audio Recording** (records the interviewer's voice from Zoom/Meet/Teams)

Missed a popup or clicked Don't Allow? Open **System Settings → Privacy &
Security → Screen & System Audio Recording**, switch **Terminal** ON, and run
`./setup.sh` again. Setup tells you if everything worked.

That's it. You never need to touch setup again.

## Tell it who you are (once)

```bash
./wlyk profile
```

One minute of questions — your experience, current role, stack, and the role
you're targeting. Every report is then calibrated to that persona instead of a
generic bar. `./wlyk record` offers this automatically the first time.

## Using it on interview day

**Before the interview starts** (Terminal, inside the `well-let-you-know` folder):

```bash
./wlyk record
```

Minimize the window and take your interview normally — on Zoom, Meet, Teams,
anything. **Headphones are recommended** (cleanest recording). Speakers also
work — the tool detects and filters the echo of the interviewer that your mic
picks up.

**When the interview ends**, click the Terminal window and press `Ctrl + C`.

Then go get a coffee ☕ — the tool transcribes everything on your Mac and writes
your report. For a one-hour interview this takes roughly 15–25 minutes (it is
deliberately thorough, not fast). The report opens in your browser by itself.

### Other commands

```bash
./wlyk history                    # all past interviews + concepts that keep hurting you
./wlyk process <session-name>     # rebuild a report for an old recording
```

## Your privacy

- Audio and transcripts stay in the `sessions/` folder on your Mac. Nothing is uploaded.
- Only the transcript **text** is sent to Claude to write the analysis, after the
  interview is over.
- ⚠️ **Recording laws differ by country/state, and some companies don't allow
  recording interviews. You are responsible for making sure it's okay to record.**
  The tool reminds you every time you start.

## Common problems

| Problem | Fix |
|---|---|
| Report says the interviewer said nothing / `system.wav` is silent | The System Audio Recording permission is off. System Settings → Privacy & Security → Screen & System Audio Recording → turn ON Terminal → run `./setup.sh` again to re-test. |
| `claude: command not found` during analysis | Install Claude Code: `curl -fsSL https://claude.ai/install.sh \| bash`, then run `claude` once to log in. Then `./wlyk process <session>` to finish your report — the recording is safe. |
| First report is very slow | The first run downloads a 3 GB transcription model. Every run after that skips the download. |
| Two interviewers on the call | Detected automatically by voice (INTERVIEWER_A / INTERVIEWER_B) and named from their introductions. If their voices are too similar to separate reliably, they're kept as one INTERVIEWER rather than guessed. |
| It didn't record my headphones call | It records *system audio*, which includes calls on any headphones. If you use exotic audio routing (external DACs, virtual devices), do a 30-second test first: `./wlyk record --duration 30 --no-open` |

---

## For the curious: how it works

```
your mic ───────────► mic.wav      (CANDIDATE)   ─┐
                                                   ├─► Whisper large-v3 (on-device, per
system audio ───────► system.wav   (INTERVIEWER) ─┘    speech-chunk for exact timestamps)
                                                            │
                                                   speaker-labeled transcript
                                                            │
                                        Claude Opus: deep analysis pass
                                                            │
                                        Claude Opus: verification pass
                                        (audits every quote & verdict
                                         against the transcript)
                                                            │
                                                      report.html
```

- Two separate audio channels = perfect candidate/interviewer attribution.
  On top of that, local speaker embeddings (resemblyzer) split multiple
  interviewer voices, and a rolling voiceprint of the candidate (stored in
  `profile/`, never uploaded) catches misattributed speech across sessions.
- Your `profile/profile.json` persona is injected into both analysis passes so
  verdicts are calibrated to your seniority and target role.
- Talk ratio and response-latency numbers are **measured from the audio timings**,
  not estimated by the model.
- Language is auto-detected per speaker; the report is written in the language you
  spoke (English → English, Hindi/Hinglish → easy Hinglish).

### Tuning (optional environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `COACH_WHISPER_MODEL` | `mlx-community/whisper-large-v3-mlx` | `whisper-large-v3-turbo` for speed, `whisper-tiny` for quick tests |
| `COACH_CLAUDE_MODEL` | `claude-opus-4-8` | Model for both analysis passes |
| `COACH_LANGUAGE` | auto-detect | Force transcription language (`hi`, `en`, …) |
| `COACH_REPORT_LANGUAGE` | follow the candidate | e.g. `English` to share a Hindi interview's report with an English-speaking mentor |
| `COACH_VOICE_ID` | `on` | `off` disables voice identification (channel labels only) |

### Session folder layout

```
sessions/20260728-141500/
├── mic.wav, system.wav      # raw audio (never leaves your Mac)
├── transcript.{json,txt}    # speaker-labeled transcript
├── analysis.json            # final verified analysis
├── analysis.draft.json      # pre-verification draft (see what the audit changed)
└── report.html              # your report — open in any browser
```

### Fallback if system audio can't be captured

On setups where the Core Audio tap doesn't work, install
[BlackHole](https://existential.audio/blackhole/) (`brew install blackhole-2ch`),
create a Multi-Output Device in Audio MIDI Setup, and select it as your output
during the interview.
