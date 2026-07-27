"""On-device transcription of the two capture channels via mlx-whisper.

mic.wav    -> CANDIDATE   (you)
system.wav -> INTERVIEWER (everyone on the call)

Produces transcript.json and transcript.txt in the session directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

# Nudges Whisper toward technical-interview vocabulary.
INITIAL_PROMPT = (
    "Technical software engineering interview. Vocabulary may include: Big-O, "
    "O(n log n), hash map, binary tree, SQL, REST API, Kubernetes, Docker, React, "
    "microservices, load balancer, TCP, DNS, CI/CD, unit tests, refactoring."
)


def whisper_model() -> str:
    return os.environ.get("COACH_WHISPER_MODEL", DEFAULT_MODEL)


def transcribe_channel(wav_path: Path) -> list[dict]:
    import mlx_whisper  # heavy import; keep it lazy

    result = mlx_whisper.transcribe(
        str(wav_path),
        path_or_hf_repo=whisper_model(),
        initial_prompt=INITIAL_PROMPT,
        condition_on_previous_text=False,
    )
    segments = []
    for segment in result.get("segments", []):
        text = segment.get("text", "").strip()
        if not text:
            continue
        # Drop likely silence hallucinations.
        if segment.get("no_speech_prob", 0.0) > 0.6 and segment.get("avg_logprob", 0.0) < -1.0:
            continue
        segments.append(
            {"start": float(segment["start"]), "end": float(segment["end"]), "text": text}
        )
    return segments


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

    transcript_json = session_dir / "transcript.json"
    transcript_json.write_text(json.dumps({"segments": merged}, indent=2, ensure_ascii=False))

    lines = [
        f"[{format_timestamp(s['start'])}] {s['speaker']}: {s['text']}" for s in merged
    ]
    (session_dir / "transcript.txt").write_text("\n".join(lines) + "\n")
    return transcript_json
