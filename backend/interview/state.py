"""
BackendState + LiveStagePlanner
移植自 intelligent_interview/demo/backend_service.py，去除 Streamlit 依赖。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BackendState:
    topic: str
    questions: List[Dict[str, Any]]
    idx: int = 0
    finished: bool = False
    qa_pairs: List[Dict[str, Any]] = field(default_factory=list)
    answered_ids: List[int] = field(default_factory=list)
    skipped_ids: List[int] = field(default_factory=list)
    deferred_ids: List[int] = field(default_factory=list)
    current_qid: Optional[int] = None
    stage_name: str = "basic"
    stage_goal: str = ""
    used_turns: int = 0
    max_turns: int = 0
    question_followup_done: Dict[int, bool] = field(default_factory=dict)
    followup_history: List[Dict[str, Any]] = field(default_factory=list)
    question_map: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    awaiting_followup_answer: bool = False
    pending_followup_qid: Optional[int] = None
    interview_transcript: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.question_map:
            for q in self.questions:
                try:
                    self.question_map[int(q.get("id"))] = q
                except Exception:
                    continue
        if self.max_turns <= 0:
            self.max_turns = max(6, len(self.questions) * 3)


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
        remaining = [
            int(q.get("id"))
            for q in state.questions
            if int(q.get("id")) not in state.answered_ids
            and int(q.get("id")) not in state.skipped_ids
        ]
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
        }
