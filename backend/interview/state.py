"""
BackendState + LiveStagePlanner
移植自 intelligent_interview/demo/backend_service.py，去除 Streamlit 依赖。
"""

from dataclasses import dataclass, field
from math import ceil
from typing import Any, Dict, List, Optional


@dataclass
class BackendState:
    topic: str
    questions: List[Dict[str, Any]]
    idx: int = 0
    finished: bool = False
    qa_pairs: List[Dict[str, Any]] = field(default_factory=list)
    answered_ids: List[int] = field(default_factory=list)
    asked_question_ids: List[int] = field(default_factory=list)
    skipped_ids: List[int] = field(default_factory=list)
    deferred_ids: List[int] = field(default_factory=list)
    current_qid: Optional[int] = None
    stage_name: str = "basic"
    stage_goal: str = ""
    used_turns: int = 0
    main_turns_used: int = 0
    followup_turns_used: int = 0
    max_turns: int = 0
    followup_budget: int = 0
    question_followup_done: Dict[int, bool] = field(default_factory=dict)
    followup_history: List[Dict[str, Any]] = field(default_factory=list)
    question_map: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    question_sequence: List[int] = field(default_factory=list)
    awaiting_followup_answer: bool = False
    pending_followup_qid: Optional[int] = None
    interview_transcript: List[Dict[str, Any]] = field(default_factory=list)
    in_recovery_phase: bool = False

    def __post_init__(self):
        if not self.question_map:
            for q in self.questions:
                try:
                    qid = int(q.get("id"))
                except Exception:
                    continue
                self.question_map[qid] = q
                self.question_sequence.append(qid)
        if self.followup_budget <= 0:
            q_cnt = len(self.question_sequence)
            self.followup_budget = min(5, max(2, ceil(q_cnt * 0.25))) if q_cnt else 2
        if self.max_turns <= 0:
            self.max_turns = len(self.question_sequence) + self.followup_budget

    def remaining_primary_ids(self) -> List[int]:
        return [
            qid for qid in self.question_sequence
            if qid not in self.asked_question_ids and qid not in self.skipped_ids
        ]

    def recovery_question_ids(self) -> List[int]:
        return [
            qid for qid in self.question_sequence
            if qid in self.deferred_ids
            and qid not in self.answered_ids
            and qid not in self.skipped_ids
            and not self.question_followup_done.get(qid, False)
        ]

    def has_followup_budget(self) -> bool:
        return self.followup_turns_used < self.followup_budget

    def mark_question_asked(self, qid: int) -> None:
        if qid not in self.asked_question_ids:
            self.asked_question_ids.append(qid)

    def add_transcript_turn(
        self,
        role: str,
        content: str,
        qid: int,
        is_followup: bool,
        stage_name: str = "",
        event_type: str = "",
    ) -> None:
        self.interview_transcript.append({
            "turn":        len(self.interview_transcript) + 1,
            "role":        role,
            "content":     content,
            "event_type":  event_type,
            "current_qid": qid,
            "is_followup": is_followup,
            "stage":       stage_name,
        })


class LiveStagePlanner:
    stage_order = ["basic", "core", "attitude", "reflection", "closing"]
    stage_goals = {
        "basic":      "了解受访者与产品的基本关系",
        "core":       "收集核心使用体验与功能评价",
        "attitude":   "量化态度、满意度与购买意愿",
        "reflection": "引导对比、反思与改进建议",
        "closing":    "收尾补充，获取推荐意愿",
    }

    def classify(self, q: Dict[str, Any]) -> str:
        text  = str(q.get("question", ""))
        qtype = str(q.get("type", ""))
        stage = str(q.get("stage", ""))
        if stage in self.stage_order:
            return stage
        if qtype in ("Likert", "likert", "rating"):
            return "attitude"
        if any(k in text for k in ["原因", "为什么", "如何看待", "影响", "反思", "对比", "竞品", "改进", "建议"]):
            return "reflection"
        if any(k in text for k in ["推荐", "补充", "其他", "还有", "最后"]):
            return "closing"
        if any(k in text for k in ["了解", "使用过", "接触", "听说", "渠道", "多久"]):
            return "basic"
        return "core"

    def plan(self, state: BackendState) -> Dict[str, Any]:
        remaining = state.remaining_primary_ids()
        recovery_mode = False
        if not remaining and state.has_followup_budget():
            remaining = state.recovery_question_ids()
            recovery_mode = bool(remaining)

        stage_candidates: Dict[str, List[int]] = {s: [] for s in self.stage_order}
        for qid in remaining:
            q = state.question_map.get(qid, {})
            stage_candidates[self.classify(q)].append(qid)

        stage_name = "closing"
        for s in self.stage_order:
            if stage_candidates[s]:
                stage_name = s
                break

        return {
            "stage_name":       stage_name,
            "stage_goal":       self.stage_goals.get(stage_name, ""),
            "stage_candidates": stage_candidates.get(stage_name, []),
            "recovery_mode":    recovery_mode,
        }
