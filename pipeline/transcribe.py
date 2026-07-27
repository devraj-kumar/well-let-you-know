"""On-device transcription of the two capture channels via mlx-whisper.

mic.wav    -> CANDIDATE   (you)
system.wav -> INTERVIEWER (everyone on the call)

Produces transcript.json and transcript.txt in the session directory.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

# Quality-first default: full large-v3 is noticeably more accurate than -turbo,
# especially for Hindi/Hinglish. Latency is an accepted cost.
DEFAULT_MODEL = "mlx-community/whisper-large-v3-mlx"

# Nudges Whisper toward technical-interview vocabulary. Includes a code-switched
# Hinglish sentence so mixed Hindi-English speech is transcribed naturally instead
# of being forced into one language.
INITIAL_PROMPT = (
    "Technical software engineering interview. The conversation may be in English, "
    "Hindi, or Hinglish (code-switched Hindi and English). "
    "Interview mein Big-O, O(n log n), hash map, binary tree, SQL, REST API, "
    "Kubernetes, Docker, React, microservices, load balancer, TCP, DNS, CI/CD, "
    "unit tests, refactoring jaise technical terms aa sakte hain."
)


def whisper_model() -> str:
    return os.environ.get("COACH_WHISPER_MODEL", DEFAULT_MODEL)


def audio_duration(wav_path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(wav_path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out) if out else 0.0


def speech_regions(wav_path: Path) -> list[tuple[float, float]]:
    """Locate speech via ffmpeg silencedetect so segment timestamps stay exact.

    Whisper compresses long silences, which drifts timestamps and can scramble the
    cross-channel ordering the analysis relies on. Transcribing each speech region
    separately and offsetting by its true start keeps timestamps accurate.
    """
    duration = audio_duration(wav_path)
    probe = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav_path),
         "-af", "silencedetect=noise=-38dB:d=0.8", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr

    silences = []
    start = None
    for line in probe.splitlines():
        if "silence_start:" in line:
            start = float(line.rsplit("silence_start:", 1)[1].split("|")[0])
        elif "silence_end:" in line and start is not None:
            end = float(line.rsplit("silence_end:", 1)[1].split("|")[0])
            silences.append((start, end))
            start = None
    if start is not None:  # silence runs to end of file
        silences.append((start, duration))

    regions, cursor = [], 0.0
    for silence_start, silence_end in silences:
        if silence_start - cursor > 0.3:
            regions.append((cursor, silence_start))
        cursor = silence_end
    if duration - cursor > 0.3:
        regions.append((cursor, duration))

    # Pad the cuts slightly so word edges are not clipped.
    padded = [(max(0.0, a - 0.25), min(duration, b + 0.25)) for a, b in regions]
    return padded if padded else ([(0.0, duration)] if duration > 0.3 else [])


def transcribe_channel(wav_path: Path) -> list[dict]:
    import mlx_whisper  # heavy import; keep it lazy

    # COACH_LANGUAGE: unset/auto = per-channel auto-detect (right default for
    # Hinglish and mixed panels); or a Whisper code like "hi" / "en" to force one.
    language = os.environ.get("COACH_LANGUAGE", "auto").lower()
    kwargs = {} if language in ("", "auto") else {"language": language}

    segments = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (region_start, region_end) in enumerate(speech_regions(wav_path)):
            clip = Path(tmp) / f"clip{i}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "quiet", "-i", str(wav_path),
                 "-ss", f"{region_start:.3f}", "-to", f"{region_end:.3f}",
                 "-c:a", "pcm_f32le", str(clip)],
                check=True,
            )
            result = mlx_whisper.transcribe(
                str(clip),
                path_or_hf_repo=whisper_model(),
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
                **kwargs,
            )
            for segment in result.get("segments", []):
                text = segment.get("text", "").strip()
                if not text:
                    continue
                # Drop likely silence hallucinations.
                if (segment.get("no_speech_prob", 0.0) > 0.6
                        and segment.get("avg_logprob", 0.0) < -1.0):
                    continue
                segments.append({
                    "start": region_start + float(segment["start"]),
                    "end": region_start + float(segment["end"]),
                    "text": text,
                })
    return segments


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9ऀ-ॿ ]", "", text.lower()).strip()


def suppress_crosstalk(segments: list[dict]) -> list[dict]:
    """Drop mic-channel copies of interviewer speech (speaker bleed).

    When the candidate listens on speakers, the mic also hears the interviewer, so
    the CANDIDATE channel duplicates INTERVIEWER lines a beat later. The system
    channel is digitally clean and therefore authoritative for interviewer speech:
    any CANDIDATE segment whose text is mostly found in nearby INTERVIEWER text is
    treated as bleed and removed.
    """
    interviewer = [s for s in segments if s["speaker"] == "INTERVIEWER"]
    kept, dropped = [], 0
    for segment in segments:
        if segment["speaker"] == "CANDIDATE":
            text = _normalize(segment["text"])
            if len(text) >= 8:
                window = " ".join(
                    _normalize(other["text"])
                    for other in interviewer
                    if other["start"] < segment["end"] + 6 and other["end"] > segment["start"] - 6
                )
                if window:
                    matcher = difflib.SequenceMatcher(None, text, window)
                    matched = sum(block.size for block in matcher.get_matching_blocks())
                    if matched / len(text) > 0.7:
                        dropped += 1
                        continue
        kept.append(segment)
    if dropped:
        print(f"  removed {dropped} mic segments that were speaker bleed from the interviewer")
    return kept


def format_timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def transcribe_session(session_dir: Path) -> Path:
    """Transcribe both channels and write the merged, speaker-labeled transcript."""
    channels = {
        "CANDIDATE": session_dir / "mic.wav",
        "INTERVIEWER": session_dir / "system.wav",
    }
    merged: list[dict] = []
    for speaker, wav in channels.items():
        if not wav.exists() or wav.stat().st_size <= 44:  # empty WAV = header only
            print(f"  note: {wav.name} is missing or empty, skipping {speaker} channel")
            continue
        print(f"  transcribing {wav.name} ({speaker}) …")
        for segment in transcribe_channel(wav):
            merged.append({"speaker": speaker, **segment})

    merged.sort(key=lambda s: s["start"])
    merged = suppress_crosstalk(merged)

    try:
        from voices import refine_speakers
        merged = refine_speakers(session_dir, merged)
    except Exception as error:  # voice ID is best-effort; channel labels still stand
        print(f"  note: voice identification skipped ({error})")

    transcript_json = session_dir / "transcript.json"
    transcript_json.write_text(json.dumps({"segments": merged}, indent=2, ensure_ascii=False))

    lines = [
        f"[{format_timestamp(s['start'])}] {s['speaker']}: {s['text']}" for s in merged
    ]
    (session_dir / "transcript.txt").write_text("\n".join(lines) + "\n")
    return transcript_json
