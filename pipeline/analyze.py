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

SDK_DEFAULT_MODEL = "claude-opus-4-8"

SCHEMA = {
    "type": "object",
    "required": ["session_summary", "questions", "weak_concepts", "communication", "top_improvements"],
    "properties": {
        "session_summary": {"type": "string"},
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


def build_prompt(transcript_text: str) -> str:
    rubric = RUBRIC_PATH.read_text()
    # COACH_REPORT_LANGUAGE: auto (default) follows the candidate's language;
    # or force e.g. "English" / "Hindi" / "Hinglish" for the narrative fields.
    report_language = os.environ.get("COACH_REPORT_LANGUAGE", "auto")
    if report_language.lower() not in ("", "auto"):
        rubric += (
            f"\n- Override: write all narrative fields in {report_language}, "
            "regardless of what the candidate spoke. Quotes still stay verbatim."
        )
    return f"{rubric}\n\n---\n\nTRANSCRIPT:\n\n{transcript_text}\n"


def extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply (tolerates fences/preamble)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


def run_claude_cli(prompt: str) -> str:
    command = ["claude", "-p", "--output-format", "json"]
    model = os.environ.get("COACH_CLAUDE_MODEL")
    if model:
        command += ["--model", model]
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
    model = os.environ.get("COACH_CLAUDE_MODEL", SDK_DEFAULT_MODEL)
    with client.messages.stream(
        model=model, max_tokens=32000,
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


def analyze_session(session_dir: Path) -> Path:
    transcript = (session_dir / "transcript.txt").read_text()
    if len(transcript.strip()) < 50:
        raise RuntimeError("transcript is empty or too short to analyze")

    prompt = build_prompt(transcript)
    output = run_model(prompt)

    try:
        data = extract_json(output)
        validate(data, SCHEMA)
    except (ValueError, json.JSONDecodeError, ValidationError) as first_error:
        print(f"  first analysis pass was malformed ({first_error}); retrying once …")
        retry_prompt = (
            prompt
            + "\n\nYour previous output was invalid JSON or did not match the required "
            + f"schema ({first_error}). Return ONLY the corrected JSON object."
        )
        data = extract_json(run_model(retry_prompt))
        validate(data, SCHEMA)

    analysis_path = session_dir / "analysis.json"
    analysis_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return analysis_path
