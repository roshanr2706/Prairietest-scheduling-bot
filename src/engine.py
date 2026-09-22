from __future__ import annotations
from dataclasses import dataclass
from src.models import Session
from src.parser import parse_sessions
from src.ranker import choose_session

@dataclass
class Decision:
    target_id: int
    exam_id: str
    exam_name: str
    session: Session

def plan_decisions(targets, discovered, page_html_by_id, already_booked, dry_run_by_target) -> list[Decision]:
    decisions: list[Decision] = []
    for target_id, exam in targets:
        for exam_id, exam_name in discovered.get(target_id, []):
            if already_booked(target_id, exam_id):
                continue
            html = page_html_by_id.get(exam_id)
            if not html:
                continue
            sessions = parse_sessions(html)
            chosen = choose_session(sessions, exam.preferences, exam.tiebreak, exam.min_seats)
            if chosen:
                decisions.append(Decision(target_id, exam_id, exam_name, chosen))
    return decisions
