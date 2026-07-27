"""well-let-you-know CLI.

They said they'd let you know. They didn't. This does.

Commands:
  record   - record an interview (Ctrl-C to stop), then transcribe + analyze + report
  process  - (re)run transcribe/analyze/report on an existing session
  history  - list past sessions and aggregate weak concepts
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "sessions"
CAPTURE_BIN = REPO_ROOT / "capture" / ".build" / "release" / "capturecli"
PROFILE_PATH = REPO_ROOT / "profile" / "profile.json"

CONSENT_NOTICE = """\
┌─────────────────────────────────────────────────────────────────────┐
│  Recording notice                                                   │
│                                                                     │
│  This records BOTH sides of your call. Recording laws vary:         │
│  many places require the other party's consent, and some            │
│  companies prohibit recording interviews. You are responsible       │
│  for making sure this recording is lawful and allowed.              │
│                                                                     │
│  Everything is stored locally in sessions/. Only the transcript     │
│  text is sent to Claude for analysis after the interview ends.      │
└─────────────────────────────────────────────────────────────────────┘"""


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def create_profile_interactive() -> None:
    print("\nYour profile calibrates the feedback — an answer that's fine at 2 years of")
    print("experience can be a red flag at 5. Takes one minute; edit any time with")
    print("'./wlyk profile'. Press Enter to skip a question.\n")
    questions = [
        ("name", "Your name"),
        ("years_experience", "Total years of professional experience (number)"),
        ("current_role", "Current / most recent role (e.g. Backend Engineer at a fintech)"),
        ("expertise", "Main expertise / stack (e.g. Java, Spring, SQL, AWS)"),
        ("target_role", "Role you're interviewing for (e.g. Senior Backend Engineer)"),
        ("notes", "Anything else that should shape the feedback (career gap, switch, …)"),
    ]
    profile = {}
    for key, question in questions:
        answer = input(f"  {question}: ").strip()
        if answer:
            profile[key] = answer
    profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROFILE_PATH.parent.mkdir(exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    print(f"\nsaved → {PROFILE_PATH}\n")


def cmd_profile(_args: argparse.Namespace) -> None:
    if PROFILE_PATH.exists():
        print("current profile:")
        print(PROFILE_PATH.read_text())
        if not sys.stdin.isatty():
            return
        if input("edit it? [y/N] ").strip().lower() != "y":
            return
    create_profile_interactive()


def cmd_record(args: argparse.Namespace) -> None:
    if not CAPTURE_BIN.exists():
        die("capture binary not built — run ./setup.sh first")

    if not PROFILE_PATH.exists() and sys.stdin.isatty():
        if input("No profile yet — set one up for experience-calibrated feedback? [Y/n] ")\
                .strip().lower() not in ("n", "no"):
            create_profile_interactive()

    print(CONSENT_NOTICE)
    session_dir = SESSIONS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nsession: {session_dir}")

    command = [str(CAPTURE_BIN), str(session_dir)]
    if args.duration:
        command += ["--duration", str(args.duration)]

    process = subprocess.Popen(command)
    try:
        process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        process.wait()

    if process.returncode not in (0, None):
        die("recording failed — see the message above")

    mic = session_dir / "mic.wav"
    if not mic.exists() or mic.stat().st_size <= 44:
        die("nothing was recorded (mic.wav is empty)")

    process_session(session_dir, open_report=not args.no_open)


def cmd_process(args: argparse.Namespace) -> None:
    session_dir = Path(args.session).expanduser()
    if not session_dir.is_absolute():
        candidate = SESSIONS_DIR / args.session
        session_dir = candidate if candidate.exists() else session_dir.resolve()
    if not session_dir.is_dir():
        die(f"no such session directory: {session_dir}")
    process_session(session_dir, open_report=not args.no_open, force=args.force)


def process_session(session_dir: Path, open_report: bool = True, force: bool = False) -> None:
    from analyze import analyze_session
    from report import render_report
    from transcribe import transcribe_session

    transcript = session_dir / "transcript.json"
    if force or not transcript.exists():
        print("transcribing (on-device, first run downloads the Whisper model) …")
        transcribe_session(session_dir)
    else:
        print("transcript exists, skipping transcription (use --force to redo)")

    analysis = session_dir / "analysis.json"
    if force or not analysis.exists():
        print("analyzing with Claude …")
        analyze_session(session_dir)
    else:
        print("analysis exists, skipping (use --force to redo)")

    report = render_report(session_dir)
    print(f"report: {report}")
    if open_report:
        subprocess.run(["open", str(report)], check=False)


def cmd_history(_args: argparse.Namespace) -> None:
    sessions = sorted(SESSIONS_DIR.glob("*/analysis.json"))
    if not sessions:
        print("no analyzed sessions yet — run ./wlyk record")
        return

    weak_counts: dict[str, int] = {}
    print(f"{'session':<18} {'questions':>9} {'strong':>6} {'partial':>7} {'missed':>6}")
    for analysis_path in sessions:
        data = json.loads(analysis_path.read_text())
        questions = data.get("questions", [])
        counts = {"strong": 0, "partial": 0, "missed": 0}
        for q in questions:
            verdict = q.get("verdict", "partial")
            counts[verdict] = counts.get(verdict, 0) + 1
        print(
            f"{analysis_path.parent.name:<18} {len(questions):>9} "
            f"{counts['strong']:>6} {counts['partial']:>7} {counts['missed']:>6}"
        )
        for concept in data.get("weak_concepts", []):
            name = concept.get("concept", "").strip().lower()
            if name:
                weak_counts[name] = weak_counts.get(name, 0) + 1

    recurring = sorted(weak_counts.items(), key=lambda item: -item[1])
    if recurring:
        print("\nweak concepts across sessions:")
        for name, count in recurring[:10]:
            print(f"  {count}× {name}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wlyk", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="record an interview, then build the report")
    record.add_argument("--duration", type=float, help="auto-stop after N seconds (for testing)")
    record.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    record.set_defaults(func=cmd_record)

    process = subparsers.add_parser("process", help="(re)process an existing session directory")
    process.add_argument("session", help="session name (e.g. 20260727-141500) or path")
    process.add_argument("--force", action="store_true", help="redo transcription and analysis")
    process.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    process.set_defaults(func=cmd_process)

    history = subparsers.add_parser("history", help="list past sessions and recurring weak concepts")
    history.set_defaults(func=cmd_history)

    profile = subparsers.add_parser("profile", help="view or edit your candidate profile")
    profile.set_defaults(func=cmd_profile)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
