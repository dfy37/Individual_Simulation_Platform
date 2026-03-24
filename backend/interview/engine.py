"""
InterviewEngine — Follow-up Gate + 事件检测 + 问题推进
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .state import BackendState, LiveStagePlanner

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"```json|```", "", text or "").strip()
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if not m:
        raise ValueError("No JSON found")
    return json.loads(m.group())


def _llm_client() -> Tuple[OpenAI, str]:
    api_key  = os.environ.get("LLM_API_KEY", "")
    api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com")
    model    = os.environ.get("LLM_MODEL", "deepseek-chat").split("/")[-1]
    return OpenAI(api_key=api_key, base_url=api_base, timeout=60), model


class InterviewEngine:
    refusal_patterns      = ["不想", "不回答", "跳过", "不方便", "拒绝", "不说"]
    anxious_patterns      = ["焦虑", "紧张", "烦", "累", "压力", "崩"]
    vague_patterns        = ["还行", "一般", "差不多", "还好", "看情况", "不一定", "说不好"]
    contradiction_markers = ["但是", "不过", "其实", "可是", "前面说", "刚才说"]

    # 产品访谈效用权重（追问积极性更高，风险权重低）
    _W = {"coverage": 0.35, "discovery": 0.30, "recovery": 0.20, "cost": 0.08, "risk": 0.07}
    FOLLOWUP_THRESHOLD = 0.30

    def __init__(self):
        self.planner = LiveStagePlanner()

    # ── 事件检测 ─────────────────────────────────────────────

    def detect_event(self, text: str) -> str:
        t = (text or "").strip()
        if any(k in t for k in self.refusal_patterns):
            return "topic_refusal"
        if any(k in t for k in self.anxious_patterns):
            return "emotional_breakdown"
        if len(t) <= 4:
            return "passive_noncooperation"
        if any(k in t for k in self.contradiction_markers):
            return "contradictory_information"
        return "none"

    def is_sufficient(self, text: str, event: str) -> bool:
        if event in ("topic_refusal", "passive_noncooperation"):
            return False
        return len((text or "").strip()) >= 8

    # ── Follow-up Gate ────────────────────────────────────────

    def followup_gate(self, state: BackendState, current_qid: int,
                      answer: str, event: str, stage_candidates: List[int]) -> Dict[str, Any]:
        candidates = self._build_candidates(state, current_qid, answer, event)
        if not candidates:
            return self._no_followup("无候选")

        scored = sorted(
            [(c, self._score(c, state, current_qid, stage_candidates, event)) for c in candidates],
            key=lambda x: x[1]["utility"], reverse=True,
        )
        best, ub = scored[0]

        limit = 2 if event in ("topic_refusal", "passive_noncooperation") else 1
        used  = 1 if state.question_followup_done.get(current_qid, False) else 0
        do_followup = ub["utility"] >= self.FOLLOWUP_THRESHOLD and used < limit

        if not do_followup:
            return self._no_followup(f"utility={ub['utility']:.3f} < threshold")

        qid  = int(best.get("selected_qid") or current_qid)
        q    = state.question_map.get(qid, state.question_map.get(current_qid, {}))
        text = self._gen_followup_text(q, answer, best.get("candidate_type", "answer_probe"),
                                       best.get("probe_kind", "clarify"))
        return {
            "do_followup":       True,
            "followup_type":     best.get("candidate_type", "answer_probe"),
            "probe_kind":        best.get("probe_kind", "clarify"),
            "selected_qid":      qid,
            "utility_breakdown": ub,
            "reason":            best.get("hint", ""),
            "followup_text":     text,
        }

    def _no_followup(self, reason: str) -> Dict[str, Any]:
        return {
            "do_followup":       False,
            "followup_type":     "none",
            "probe_kind":        "",
            "selected_qid":      None,
            "utility_breakdown": {k: 0.0 for k in ("coverage","discovery","recovery","cost","risk","utility")},
            "reason":            reason,
            "followup_text":     "",
        }

    def _build_candidates(self, state, current_qid, answer, event):
        candidates = []
        for dqid in state.deferred_ids:
            if dqid != current_qid:
                candidates.append({"candidate_type": "deferred_recovery", "probe_kind": "recovery",
                                    "selected_qid": dqid, "hint": "回收未充分回答的问题"})
                break
        t = answer or ""
        if any(k in t for k in self.vague_patterns):
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "clarify",
                                "selected_qid": current_qid, "hint": "回答偏模糊，需澄清"})
        if len(t) >= 20:
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "expand",
                                "selected_qid": current_qid, "hint": "有细节可深挖"})
        if any(k in t for k in self.contradiction_markers):
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "consistency_check",
                                "selected_qid": current_qid, "hint": "前后表述可能不一致"})
        if not candidates:
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "fill_missing",
                                "selected_qid": current_qid, "hint": "补充信息"})
        return candidates[:3]

    def _score(self, candidate, state, current_qid, stage_candidates, event) -> Dict[str, float]:
        qid       = int(candidate.get("selected_qid") or current_qid)
        coverage  = 1.0 if qid in stage_candidates and qid not in state.answered_ids else 0.4
        discovery = {"expand": 0.7, "clarify": 0.5, "fill_missing": 0.5,
                     "consistency_check": 0.4, "recovery": 0.3}.get(candidate.get("probe_kind", ""), 0.3)
        recovery  = 1.0 if candidate.get("candidate_type") == "deferred_recovery" else 0.0
        cost      = min(1.0, state.used_turns / max(1, state.max_turns))
        risk      = 0.05
        if event in ("topic_refusal", "emotional_breakdown"):
            risk = 0.25
        w = self._W
        utility = (w["coverage"]*coverage + w["discovery"]*discovery +
                   w["recovery"]*recovery - w["cost"]*cost - w["risk"]*risk)
        return {"coverage": round(coverage,4), "discovery": round(discovery,4),
                "recovery": round(recovery,4), "cost": round(cost,4),
                "risk": round(risk,4), "utility": round(utility,4)}

    def _gen_followup_text(self, question, answer, ftype, probe_kind) -> str:
        prompt = (
            "你是访谈员助手，根据受访者的回答生成一个简短自然的追问（1句话，口语化，不施压）。\n"
            f"当前问题：{question.get('question','')}\n"
            f"受访者回答：{answer}\n"
            f"追问类型：{probe_kind}\n"
            "只输出JSON: {\"followup_text\": \"...\"}"
        )
        try:
            client, model = _llm_client()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7, max_tokens=120,
            )
            raw = resp.choices[0].message.content.strip()
            obj = _extract_json(raw)
            text = (obj.get("followup_text") or "").strip()
            if text:
                return text
        except Exception as e:
            logger.warning(f"追问生成失败: {e}")
        defaults = {
            "clarify":           "能具体说说是什么让你有这种感觉吗？",
            "expand":            "这个点能多聊一点吗？",
            "fill_missing":      "关于这一点，你觉得最重要的是什么？",
            "consistency_check": "我注意到你前后的说法有些不同，方便确认一下吗？",
            "recovery":          "之前这个问题我们没有深入，方便简单说说吗？",
        }
        return defaults.get(probe_kind, "能再补充一下吗？")

    # ── 问题推进 ─────────────────────────────────────────────

    def update_stage(self, state: BackendState) -> Dict[str, Any]:
        stage = self.planner.plan(state)
        state.stage_name = stage["stage_name"]
        state.stage_goal = stage["stage_goal"]
        return stage

    def select_next_qid(self, state: BackendState, stage_candidates: List[int],
                        event: str) -> Optional[int]:
        high_risk = event in ("topic_refusal", "emotional_breakdown")
        if state.deferred_ids and not high_risk:
            for qid in state.deferred_ids:
                if qid not in state.answered_ids and qid not in state.skipped_ids:
                    return qid
        for qid in stage_candidates:
            if qid not in state.answered_ids and qid not in state.skipped_ids:
                return qid
        for q in state.questions:
            qid = int(q.get("id"))
            if qid not in state.answered_ids and qid not in state.skipped_ids:
                return qid
        return None

    def record_answer(self, state: BackendState, qid: int, answer: str,
                      event: str, is_followup: bool = False) -> None:
        q = state.question_map.get(qid, {})
        state.qa_pairs.append({
            "id":          qid,
            "question":    q.get("question", ""),
            "answer":      answer,
            "event_type":  event,
            "is_followup": is_followup,
        })
        state.interview_transcript.append({
            "turn":        len(state.interview_transcript) + 1,
            "role":        "agent",
            "content":     answer,
            "event_type":  event,
            "current_qid": qid,
        })
        if self.is_sufficient(answer, event):
            if qid not in state.answered_ids:
                state.answered_ids.append(qid)
            if qid in state.deferred_ids:
                state.deferred_ids.remove(qid)
        else:
            if qid not in state.deferred_ids and qid not in state.skipped_ids:
                state.deferred_ids.append(qid)

    def build_report(self, state: BackendState, agent_meta: Dict[str, Any]) -> Dict[str, Any]:
        answered_cnt = len(set(state.answered_ids))
        total_cnt    = max(1, len(state.questions))
        followup_cnt = sum(1 for x in state.followup_history if x.get("do_followup"))
        attitude_score = _infer_attitude_score(state.qa_pairs)
        qa_summary = [
            {"question": p["question"], "answer": p["answer"]}
            for p in state.qa_pairs if not p.get("is_followup") and p.get("answer")
        ]
        return {
            "agent_id":        agent_meta.get("id"),
            "agent_name":      agent_meta.get("name", ""),
            "product":         state.topic,
            "attitude_score":  attitude_score,
            "attitude_label":  _attitude_label(attitude_score),
            "answered_count":  answered_cnt,
            "total_questions": total_cnt,
            "followup_count":  followup_cnt,
            "qa_pairs":        state.qa_pairs,
            "qa_summary":      qa_summary,
            "key_opinions":    _extract_key_opinions(state.qa_pairs),
            "process_metrics": {
                "followup_rate":   round(followup_cnt / total_cnt, 3),
                "answer_coverage": round(answered_cnt / total_cnt, 3),
                "turns_used":      state.used_turns,
            },
        }


def _infer_attitude_score(qa_pairs: List[Dict]) -> float:
    positive = ["喜欢", "好", "满意", "推荐", "购买", "值得", "不错", "棒", "优秀", "会买"]
    negative = ["不好", "贵", "差", "不满意", "不推荐", "不买", "失望", "糟", "不值"]
    pos, neg = 0, 0
    for qa in qa_pairs:
        ans = qa.get("answer", "")
        pos += sum(1 for k in positive if k in ans)
        neg += sum(1 for k in negative if k in ans)
        for opt, score in [("非常满意",5),("满意",4),("一般",3),("不满意",2),("非常不满意",1),
                           ("非常可能",5),("可能",4),("不确定",3),("不可能",2),("非常不可能",1)]:
            if opt in ans:
                return float(score)
    if pos + neg == 0:
        return 3.0
    return round(min(5.0, max(1.0, 3.0 + (pos - neg) * 0.5)), 1)


def _attitude_label(score: float) -> str:
    if score >= 4.0:
        return "正面"
    if score >= 2.5:
        return "中立"
    return "负面"


def _extract_key_opinions(qa_pairs: List[Dict]) -> List[str]:
    opinions = []
    for qa in qa_pairs:
        if qa.get("is_followup"):
            continue
        ans = (qa.get("answer") or "").strip()
        if len(ans) > 15:
            opinions.append(ans[:80] + ("…" if len(ans) > 80 else ""))
        if len(opinions) >= 5:
            break
    return opinions
