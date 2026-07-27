"""Gap analysis: transcript -> structured JSON via Claude.

Primary path: the Claude Code CLI (`claude -p`) — zero extra setup for anyone who
already uses Claude Code. Fallback: the Anthropic SDK with ANTHROPIC_API_KEY.
Set COACH_CLAUDE_MODEL to override the model on either path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from jsonschema import validate, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
RUBRIC_PATH = REPO_ROOT / "prompts" / "analysis.md"
VERIFY_PATH = REPO_ROOT / "prompts" / "verify.md"
PROFILE_PATH = REPO_ROOT / "profile" / "profile.json"

# Quality-first: pin the analysis to the most capable Opus tier on both paths.
DEFAULT_MODEL = "claude-opus-4-8"

SCHEMA = {
    "type": "object",
    "required": ["session_summary", "questions", "competencies", "weak_concepts",
                 "communication", "top_improvements"],
    "properties": {
        "session_summary": {"type": "string"},
        "speaker_names": {"type": "object", "additionalProperties": {"type": "string"}},
        "competencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["area", "rating", "assessment"],
                "properties": {
                    "area": {"type": "string"},
                    "rating": {"enum": ["strong", "adequate", "concern", "not_observed"]},
                    "assessment": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["question", "intent", "answer_summary", "verdict",
                             "hints", "deflections", "strong_answer_sketch"],
                "properties": {
                    "question": {"type": "string"},
                    "intent": {"type": "string"},
                    "asked_at": {"type": "string"},
                    "asked_by": {"type": "string"},
                    "answer_summary": {"type": "string"},
                    "verdict": {"enum": ["strong", "partial", "missed"]},
                    "hints": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["hint", "quote", "taken"],
                            "properties": {
                                "hint": {"type": "string"},
                                "quote": {"type": "string"},
                                "taken": {"type": "boolean"},
                                "commentary": {"type": "string"},
                            },
                        },
                    },
                    "deflections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["quote", "what_was_avoided"],
                            "properties": {
                                "quote": {"type": "string"},
                                "what_was_avoided": {"type": "string"},
                            },
                        },
                    },
                    "strong_answer_sketch": {"type": "string"},
                },
            },
        },
        "weak_concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["concept", "evidence", "study_pointers"],
                "properties": {
                    "concept": {"type": "string"},
                    "evidence": {"type": "string"},
                    "study_pointers": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "communication": {
            "type": "object",
            "required": ["talk_ratio_candidate", "clarifying_questions_asked",
                         "filler_notes", "responsiveness_notes"],
            "properties": {
                "talk_ratio_candidate": {"type": "number"},
                "clarifying_questions_asked": {"type": "integer"},
                "filler_notes": {"type": "string"},
                "responsiveness_notes": {"type": "string"},
            },
        },
        "top_improvements": {"type": "array", "items": {"type": "string"}},
    },
}


def measured_stats(session_dir: Path) -> str:
    """Deterministic stats from channel timings, so the model never estimates them."""
    segments = json.loads((session_dir / "transcript.json").read_text())["segments"]
    talk = {"CANDIDATE": 0.0, "INTERVIEWER": 0.0}
    for segment in segments:
        side = "CANDIDATE" if segment["speaker"] == "CANDIDATE" else "INTERVIEWER"
        talk[side] += segment["end"] - segment["start"]
    total = talk["CANDIDATE"] + talk["INTERVIEWER"] or 1.0
    ratio = talk["CANDIDATE"] / total

    ordered = sorted(segments, key=lambda s: s["start"])
    gaps = []
    longest_monologue = 0.0
    run = 0.0
    for previous, current in zip(ordered, ordered[1:]):
        if previous["speaker"] != "CANDIDATE" and current["speaker"] == "CANDIDATE":
            gap = current["start"] - previous["end"]
            if 0 <= gap <= 30:
                gaps.append(gap)
        if current["speaker"] == "CANDIDATE" == previous["speaker"]:
            run += current["end"] - previous["start"]
            longest_monologue = max(longest_monologue, run)
        else:
            run = 0.0

    lines = [
        "MEASURED AUDIO STATS (computed from the audio channels — use these exact "
        "numbers; do not estimate your own):",
        f"- candidate speaking time: {talk['CANDIDATE']:.0f}s; interviewer: {talk['INTERVIEWER']:.0f}s",
        f"- talk_ratio_candidate = {ratio:.2f}  (use this exact value in communication.talk_ratio_candidate)",
    ]
    if gaps:
        lines.append(
            f"- pause before candidate responds after interviewer stops: "
            f"avg {sum(gaps) / len(gaps):.1f}s, max {max(gaps):.1f}s"
        )
    if longest_monologue:
        lines.append(f"- longest uninterrupted candidate stretch: {longest_monologue:.0f}s")
    return "\n".join(lines)


def profile_block() -> str:
    if not PROFILE_PATH.exists():
        return ""
    profile = json.loads(PROFILE_PATH.read_text())
    profile.pop("updated_at", None)
    if not profile:
        return ""
    lines = [f"- {key.replace('_', ' ')}: {value}" for key, value in profile.items()]
    return (
        "CANDIDATE PROFILE (calibrate every judgment to this persona):\n"
        + "\n".join(lines)
    )


def build_prompt(transcript_text: str, stats: str = "") -> str:
    rubric = RUBRIC_PATH.read_text()
    # COACH_REPORT_LANGUAGE: auto (default) follows the candidate's language;
    # or force e.g. "English" / "Hindi" / "Hinglish" for the narrative fields.
    report_language = os.environ.get("COACH_REPORT_LANGUAGE", "auto")
    if report_language.lower() not in ("", "auto"):
        rubric += (
            f"\n- Override: write all narrative fields in {report_language}, "
            "regardless of what the candidate spoke. Quotes still stay verbatim."
        )
    persona = profile_block()
    persona_block = f"\n\n---\n\n{persona}" if persona else ""
    stats_block = f"\n\n---\n\n{stats}" if stats else ""
    return f"{rubric}{persona_block}{stats_block}\n\n---\n\nTRANSCRIPT:\n\n{transcript_text}\n"


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply (tolerates fences/preamble)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


def run_claude_cli(prompt: str) -> str:
    command = ["claude", "-p", "--output-format", "json"]
    command += ["--model", os.environ.get("COACH_CLAUDE_MODEL", DEFAULT_MODEL)]
    result = subprocess.run(
        command, input=prompt, capture_output=True, text=True, timeout=900
    )
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[:500]
        raise RuntimeError(f"claude CLI failed (exit {result.returncode}): {detail}")
    envelope = json.loads(result.stdout)
    return envelope.get("result", "")


def run_anthropic_sdk(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("COACH_CLAUDE_MODEL", DEFAULT_MODEL)
    with client.messages.stream(
        model=model, max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("the model declined to analyze this transcript")
    return next((b.text for b in message.content if b.type == "text"), "")


def run_model(prompt: str) -> str:
    if shutil.which("claude"):
        try:
            return run_claude_cli(prompt)
        except (RuntimeError, json.JSONDecodeError) as error:
            print(f"  claude CLI hiccup ({error}); retrying once …")
            time.sleep(5)
            return run_claude_cli(prompt)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return run_anthropic_sdk(prompt)
    raise RuntimeError(
        "no way to reach Claude: install Claude Code (https://claude.com/claude-code) "
        "or set ANTHROPIC_API_KEY"
    )


def run_validated(prompt: str) -> dict:
    output = run_model(prompt)
    try:
        data = extract_json(output)
        validate(data, SCHEMA)
        return data
    except (ValueError, json.JSONDecodeError, ValidationError) as first_error:
        print(f"  output was malformed ({str(first_error)[:120]}); retrying once …")
        retry_prompt = (
            prompt
            + "\n\n---\n\nYour previous attempt is included below. It was rejected "
            + f"because: {str(first_error)[:400]}\n"
            + "Repair it — keep the analysis content, fix the structure, and fill in "
            + "any missing required fields from the transcript. "
            + "Return ONLY the corrected JSON object.\n\nPREVIOUS ATTEMPT:\n"
            + output[:80000]
        )
        data = extract_json(run_model(retry_prompt))
        validate(data, SCHEMA)
        return data


def verify_analysis(transcript: str, stats: str, draft: dict) -> dict:
    """Second pass: audit quotes, coverage, and verdicts against the transcript."""
    persona = profile_block()
    prompt = (
        VERIFY_PATH.read_text()
        + (("\n\n---\n\n" + persona) if persona else "")
        + ("\n\n---\n\n" + stats if stats else "")
        + "\n\n---\n\nTRANSCRIPT:\n\n" + transcript
        + "\n\n---\n\nDRAFT ANALYSIS JSON:\n\n"
        + json.dumps(draft, indent=2, ensure_ascii=False)
    )
    try:
        return run_validated(prompt)
    except (ValueError, json.JSONDecodeError, ValidationError, RuntimeError) as error:
        print(f"  verification pass failed ({str(error)[:120]}); keeping the draft")
        return draft


def analyze_session(session_dir: Path) -> Path:
    transcript = (session_dir / "transcript.txt").read_text()
    if len(transcript.strip()) < 50:
        raise RuntimeError("transcript is empty or too short to analyze")

    stats = measured_stats(session_dir)
    print("  pass 1/2: deep analysis …")
    draft = run_validated(build_prompt(transcript, stats))
    print("  pass 2/2: verifying quotes, coverage, and verdicts …")
    data = verify_analysis(transcript, stats, draft)

    analysis_path = session_dir / "analysis.json"
    analysis_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    (session_dir / "analysis.draft.json").write_text(
        json.dumps(draft, indent=2, ensure_ascii=False)
    )
    return analysis_path
