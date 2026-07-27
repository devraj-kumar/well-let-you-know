"""Voice identity for sessions.

Two jobs, both fully local (resemblyzer speaker embeddings):

1. Split multiple interviewer voices on the system channel into
   INTERVIEWER_A / INTERVIEWER_B / … so a panel round is attributed correctly.
2. Learn the candidate's voiceprint across sessions (stored in profile/) and use
   it to catch speech on the mic channel that is not the candidate's voice.

Disable with COACH_VOICE_ID=off. Any failure degrades to the plain channel labels.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = REPO_ROOT / "profile"
VOICEPRINT_PATH = PROFILE_DIR / "voiceprint.npy"
VOICEPRINT_META = PROFILE_DIR / "voiceprint.json"

SAMPLE_RATE = 16000
MIN_SEGMENT_SECONDS = 0.8
CLUSTER_THRESHOLD = 0.70   # resemblyzer cosine: same speaker ≳0.75, different ≲0.65
MERGE_THRESHOLD = 0.75     # collapse clusters unless voices separate clearly:
                           # a false "two interviewers" hurts more than a false one
NOT_CANDIDATE_SIM = 0.55   # below this vs the stored voiceprint = suspicious


def _load_wav16k(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path), "-f", "f32le",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _embed_segments(encoder, wav: np.ndarray, segments: list[dict]) -> dict[int, np.ndarray]:
    """Embedding per segment index; segments too short/quiet are skipped."""
    from resemblyzer import preprocess_wav

    embeddings: dict[int, np.ndarray] = {}
    for index, segment in segments:
        if segment["end"] - segment["start"] < MIN_SEGMENT_SECONDS:
            continue
        clip = wav[int(segment["start"] * SAMPLE_RATE): int(segment["end"] * SAMPLE_RATE)]
        if len(clip) < int(0.5 * SAMPLE_RATE):
            continue
        try:
            processed = preprocess_wav(clip, source_sr=SAMPLE_RATE)
            if len(processed) < int(0.4 * SAMPLE_RATE):
                continue
            embeddings[index] = encoder.embed_utterance(processed)
        except Exception:
            continue
    return embeddings


def _cluster(embeddings: dict[int, np.ndarray]) -> list[list[int]]:
    """Greedy centroid clustering by cosine similarity (embeddings are unit norm)."""
    clusters: list[dict] = []
    for index in sorted(embeddings):
        emb = embeddings[index]
        best, best_sim = None, CLUSTER_THRESHOLD
        for cluster in clusters:
            sim = float(np.dot(emb, cluster["centroid"] / np.linalg.norm(cluster["centroid"])))
            if sim > best_sim:
                best, best_sim = cluster, sim
        if best is None:
            clusters.append({"members": [index], "centroid": emb.copy()})
        else:
            best["members"].append(index)
            best["centroid"] += emb

    # Greedy assignment is order-dependent and can split one speaker in two;
    # merge clusters whose centroids are clearly the same voice.
    def unit(v: np.ndarray) -> np.ndarray:
        return v / np.linalg.norm(v)

    merged = True
    while merged and len(clusters) > 1:
        merged = False
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                sim = float(np.dot(unit(clusters[a]["centroid"]), unit(clusters[b]["centroid"])))
                if sim > MERGE_THRESHOLD:
                    clusters[a]["members"] += clusters[b]["members"]
                    clusters[a]["centroid"] += clusters[b]["centroid"]
                    del clusters[b]
                    merged = True
                    break
            if merged:
                break
    return [c["members"] for c in clusters]


def _speech_time(segments: list[dict], indexes: list[int]) -> float:
    return sum(segments[i]["end"] - segments[i]["start"] for i in indexes)


def load_voiceprint() -> tuple[np.ndarray | None, int]:
    if VOICEPRINT_PATH.exists() and VOICEPRINT_META.exists():
        meta = json.loads(VOICEPRINT_META.read_text())
        return np.load(VOICEPRINT_PATH), int(meta.get("sessions", 0))
    return None, 0


def _save_voiceprint(voiceprint: np.ndarray, sessions: int) -> None:
    PROFILE_DIR.mkdir(exist_ok=True)
    np.save(VOICEPRINT_PATH, voiceprint)
    VOICEPRINT_META.write_text(json.dumps(
        {"sessions": sessions, "updated_at": datetime.now().isoformat(timespec="seconds")}
    ))


def refine_speakers(session_dir: Path, segments: list[dict]) -> list[dict]:
    """Voice pass over the merged transcript segments. Returns updated segments."""
    if os.environ.get("COACH_VOICE_ID", "on").lower() in ("off", "0", "no"):
        return segments

    from resemblyzer import VoiceEncoder

    encoder = VoiceEncoder("cpu", verbose=False)

    # --- 1. Split interviewer voices (system channel is digitally clean) ---
    interviewer_indexed = [(i, s) for i, s in enumerate(segments) if s["speaker"] == "INTERVIEWER"]
    interviewer_centroids: list[np.ndarray] = []
    centroid_labels: list[str] = []
    if interviewer_indexed:
        system_wav = _load_wav16k(session_dir / "system.wav")
        interviewer_embeddings = _embed_segments(encoder, system_wav, interviewer_indexed)
        clusters = _cluster(interviewer_embeddings)
        # Keep clusters that carry real speech; tiny ones are noise/artifacts.
        total_time = _speech_time(segments, list(interviewer_embeddings)) or 1.0
        major = [c for c in clusters
                 if len(c) >= 3 and _speech_time(segments, c) / total_time >= 0.12]
        major.sort(key=lambda c: min(segments[i]["start"] for i in c))
        multi = len(major) >= 2
        labels = ([f"INTERVIEWER_{chr(ord('A') + n)}" for n in range(len(major))]
                  if multi else ["INTERVIEWER"] * len(clusters))
        for n, cluster in enumerate(major if multi else clusters):
            centroid = np.mean([interviewer_embeddings[i] for i in cluster], axis=0)
            interviewer_centroids.append(centroid / np.linalg.norm(centroid))
            centroid_labels.append(labels[n])
        if multi:
            assignment: dict[int, str] = {}
            for label, cluster in zip(labels, major):
                for i in cluster:
                    assignment[i] = label
            # Segments whose embedding was skipped: inherit the label of the
            # nearest labeled interviewer segment in time.
            labeled = sorted(assignment)
            for i, _ in interviewer_indexed:
                if i not in assignment and labeled:
                    nearest = min(labeled, key=lambda j: abs(segments[j]["start"] - segments[i]["start"]))
                    assignment[i] = assignment[nearest]
            for i, label in assignment.items():
                segments[i]["speaker"] = label
            print(f"  detected {len(major)} interviewer voices → labels {', '.join(labels)}")

    # --- 2. Candidate voiceprint: verify mic channel, then learn from it ---
    candidate_indexed = [(i, s) for i, s in enumerate(segments) if s["speaker"] == "CANDIDATE"]
    if not candidate_indexed:
        return segments
    mic_wav = _load_wav16k(session_dir / "mic.wav")
    candidate_embeddings = _embed_segments(encoder, mic_wav, candidate_indexed)
    if not candidate_embeddings:
        return segments

    voiceprint, prior_sessions = load_voiceprint()
    reattributed = 0
    if voiceprint is not None and prior_sessions >= 1 and interviewer_centroids:
        for i, emb in candidate_embeddings.items():
            candidate_sim = float(np.dot(emb, voiceprint))
            sims = [float(np.dot(emb, c)) for c in interviewer_centroids]
            best = int(np.argmax(sims))
            if candidate_sim < NOT_CANDIDATE_SIM and sims[best] > candidate_sim + 0.10:
                segments[i]["speaker"] = centroid_labels[best]
                reattributed += 1
        if reattributed:
            print(f"  voiceprint: moved {reattributed} mic segments that were not your voice")

    # Update the rolling voiceprint from what is (still) attributed to the candidate.
    kept = [emb for i, emb in candidate_embeddings.items() if segments[i]["speaker"] == "CANDIDATE"]
    if len(kept) >= 5:
        session_print = np.mean(kept, axis=0)
        session_print /= np.linalg.norm(session_print)
        if voiceprint is None:
            blended = session_print
        else:
            blended = 0.7 * voiceprint + 0.3 * session_print
            blended /= np.linalg.norm(blended)
        _save_voiceprint(blended, prior_sessions + 1)
        print(f"  voiceprint updated (session {prior_sessions + 1})")

    return segments
