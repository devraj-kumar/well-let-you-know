"""Render analysis.json into a self-contained HTML report."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

CSS = """
:root { --bg:#fbfaf7; --card:#ffffff; --ink:#1e2430; --muted:#6b7280; --line:#e5e1d8;
        --strong:#15803d; --partial:#b45309; --missed:#b91c1c; --accent:#1d4ed8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161b; --card:#1d2027; --ink:#e8e6e1; --muted:#9aa0ab; --line:#2c3038;
          --strong:#4ade80; --partial:#fbbf24; --missed:#f87171; --accent:#93c5fd; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem 4rem; background:var(--bg); color:var(--ink);
       font:16px/1.55 -apple-system, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; }
h1 { font-size:1.6rem; margin:0 0 .25rem; }
h2 { font-size:1.15rem; margin:2.2rem 0 .8rem; border-bottom:1px solid var(--line); padding-bottom:.4rem; }
.sub { color:var(--muted); margin-bottom:1.5rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:1rem 1.2rem; margin-bottom:1rem; }
.badge { display:inline-block; font-size:.75rem; font-weight:700; letter-spacing:.04em;
         text-transform:uppercase; padding:.15rem .55rem; border-radius:999px;
         border:1.5px solid currentColor; }
.badge.strong { color:var(--strong); } .badge.partial { color:var(--partial); }
.badge.missed { color:var(--missed); }
.qhead { display:flex; gap:.75rem; align-items:baseline; flex-wrap:wrap; }
.qhead .t { color:var(--muted); font-size:.85rem; }
.qtitle { font-weight:650; }
.intent { color:var(--muted); font-size:.9rem; margin:.15rem 0 .6rem; }
.section-label { font-size:.78rem; font-weight:700; text-transform:uppercase;
                 letter-spacing:.05em; color:var(--muted); margin:.9rem 0 .3rem; }
blockquote { margin:.3rem 0 .6rem; padding:.4rem .8rem; border-left:3px solid var(--line);
             color:var(--muted); font-style:italic; }
.hint-taken::before { content:"✓ hint taken — "; color:var(--strong); font-weight:700; }
.hint-missed::before { content:"✗ hint missed — "; color:var(--missed); font-weight:700; }
ul { margin:.3rem 0 .6rem; padding-left:1.3rem; }
.stats { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:.7rem 1.1rem; }
.stat b { display:block; font-size:1.3rem; }
.stat span { color:var(--muted); font-size:.82rem; }
.improve { border-left:4px solid var(--accent); }
footer { color:var(--muted); font-size:.8rem; margin-top:3rem; text-align:center; }
"""


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def render_question(index: int, question: dict) -> str:
    verdict = question.get("verdict", "partial")
    parts = [
        '<div class="card">',
        '<div class="qhead">',
        f'<span class="badge {esc(verdict)}">{esc(verdict)}</span>',
        f'<span class="qtitle">Q{index}. {esc(question.get("question"))}</span>',
        f'<span class="t">{esc(question.get("asked_at", ""))}</span>',
        "</div>",
        f'<div class="intent">Probing: {esc(question.get("intent"))}</div>',
        f"<div>{esc(question.get('answer_summary'))}</div>",
    ]
    hints = question.get("hints") or []
    if hints:
        parts.append('<div class="section-label">Hints from the interviewer</div>')
        for hint in hints:
            cls = "hint-taken" if hint.get("taken") else "hint-missed"
            parts.append(f'<div class="{cls}">{esc(hint.get("hint"))}</div>')
            parts.append(f"<blockquote>“{esc(hint.get('quote'))}”</blockquote>")
            if hint.get("commentary"):
                parts.append(f"<div>{esc(hint['commentary'])}</div>")
    deflections = question.get("deflections") or []
    if deflections:
        parts.append('<div class="section-label">Where you deflected</div>')
        for d in deflections:
            parts.append(f"<blockquote>“{esc(d.get('quote'))}”</blockquote>")
            parts.append(f"<div>Avoided: {esc(d.get('what_was_avoided'))}</div>")
    parts.append('<div class="section-label">What a strong answer covers</div>')
    parts.append(f"<div>{esc(question.get('strong_answer_sketch'))}</div>")
    parts.append("</div>")
    return "\n".join(parts)


def render_report(session_dir: Path) -> Path:
    analysis = json.loads((session_dir / "analysis.json").read_text())
    questions = analysis.get("questions", [])
    counts = {"strong": 0, "partial": 0, "missed": 0}
    for q in questions:
        counts[q.get("verdict", "partial")] = counts.get(q.get("verdict", "partial"), 0) + 1
    communication = analysis.get("communication", {})
    talk_ratio = communication.get("talk_ratio_candidate", 0)

    body = [f"<style>{CSS}</style>", "<main>"]
    body.append("<h1>Interview gap report</h1>")
    body.append(f'<div class="sub">Session {esc(session_dir.name)} · generated '
                f"{datetime.now().strftime('%Y-%m-%d %H:%M')}</div>")

    body.append('<div class="stats">')
    body.append(f'<div class="stat"><b>{len(questions)}</b><span>questions</span></div>')
    for verdict in ("strong", "partial", "missed"):
        body.append(f'<div class="stat"><b>{counts.get(verdict, 0)}</b><span>{verdict}</span></div>')
    body.append(f'<div class="stat"><b>{round(talk_ratio * 100)}%</b><span>your talk time</span></div>')
    body.append("</div>")

    body.append(f'<div class="card">{esc(analysis.get("session_summary"))}</div>')

    body.append("<h2>Top 3 improvements for next time</h2>")
    for improvement in analysis.get("top_improvements", []):
        body.append(f'<div class="card improve">{esc(improvement)}</div>')

    body.append("<h2>Question by question</h2>")
    for i, question in enumerate(questions, 1):
        body.append(render_question(i, question))

    body.append("<h2>Weak concepts to study</h2>")
    for concept in analysis.get("weak_concepts", []):
        body.append('<div class="card">')
        body.append(f"<div class=\"qtitle\">{esc(concept.get('concept'))}</div>")
        body.append(f"<blockquote>“{esc(concept.get('evidence'))}”</blockquote>")
        pointers = concept.get("study_pointers") or []
        if pointers:
            body.append("<ul>" + "".join(f"<li>{esc(p)}</li>" for p in pointers) + "</ul>")
        body.append("</div>")

    body.append("<h2>Communication</h2>")
    body.append('<div class="card">')
    body.append(f"<div><b>Clarifying questions asked:</b> "
                f"{esc(communication.get('clarifying_questions_asked', 0))}</div>")
    body.append(f"<div><b>Filler / hedging:</b> {esc(communication.get('filler_notes'))}</div>")
    body.append(f"<div><b>Responsiveness to signals:</b> "
                f"{esc(communication.get('responsiveness_notes'))}</div>")
    body.append("</div>")

    body.append("<footer>Recorded and analyzed locally by interview-coach. "
                "Transcript and audio never left this Mac; analysis text was sent to Claude.</footer>")
    body.append("</main>")

    report_path = session_dir / "report.html"
    report_path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Interview gap report — {esc(session_dir.name)}</title></head><body>"
        + "\n".join(body)
        + "</body></html>"
    )
    return report_path
