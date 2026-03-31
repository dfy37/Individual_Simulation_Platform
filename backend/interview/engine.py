"""
InterviewEngine — 主问题推进、interviewer 话术改写、回答判定与追问策略
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .llm import make_llm_client
from .state import BackendState, LiveStagePlanner

logger = logging.getLogger(__name__)

STYLE_LIBRARY = {
    "理性高效": {
        "description": "问题简洁、聚焦重点、少铺垫。",
        "main_prefixes": {
            "basic": ["我先快速确认一下，", "先直接确认一下，"],
            "core": ["我想聚焦体验本身，", "直接说核心感受的话，"],
            "attitude": ["我想量化确认一下，", "我直接确认一个判断，"],
            "reflection": ["我们往深一点聊，", "换个分析视角看，"],
            "closing": ["最后快速收个尾，", "最后确认一下，"],
        },
        "followup_prefixes": ["那我继续追问一个细节，", "我想再确认一个关键点，"],
    },
    "温和耐心": {
        "description": "语气温和，给对方留表达空间。",
        "main_prefixes": {
            "basic": ["想先轻松了解一下，", "我们先从简单的开始，"],
            "core": ["想听你慢慢说说，", "如果按你的真实感受来说，"],
            "attitude": ["想听你直觉上怎么判断，", "我想再确认一下你的感受，"],
            "reflection": ["如果方便的话，也想听听你更深一点的想法，", "再往下聊一点的话，"],
            "closing": ["最后补一个收尾问题，", "最后再请你补充一下，"],
        },
        "followup_prefixes": ["如果方便的话，再补充一点，", "这个点我还想温和追问一句，"],
    },
    "活泼共情": {
        "description": "更口语化，更贴近日常聊天。",
        "main_prefixes": {
            "basic": ["先聊个轻松的，", "我们先从最直观的开始，"],
            "core": ["说到这个你第一反应会是，", "如果按你平时聊天的说法，"],
            "attitude": ["那按你的直觉打分的话，", "如果让你立刻表个态，"],
            "reflection": ["那我们再展开一点聊，", "换个更有意思的角度说，"],
            "closing": ["最后来个收尾，", "最后补一句就行，"],
        },
        "followup_prefixes": ["这个点挺有意思，我再接着问一句，", "顺着你刚才那句再往下聊，"],
    },
    "直接追问": {
        "description": "切入直接，追问明显，少寒暄。",
        "main_prefixes": {
            "basic": ["我先直接问一个基础信息，", "先直接确认一点，"],
            "core": ["我就直接问核心体验，", "那我直接切重点，"],
            "attitude": ["我直接确认你的判断，", "那我就直接问态度，"],
            "reflection": ["再往下我直接问深一点，", "那我继续追一个关键问题，"],
            "closing": ["最后我直接问收尾问题，", "收尾我只确认一点，"],
        },
        "followup_prefixes": ["那我直接追问一句，", "我继续追一个关键细节，"],
    },
}

RATIONAL_HINTS = ["研究", "理工", "工程", "数据", "分析", "计算机", "博士", "硕士", "实验"]
WARM_HINTS = ["耐心", "温和", "细腻", "稳重", "安静", "敏感", "慢热"]
DIRECT_HINTS = ["直接", "果断", "干脆", "效率", "锋利", "强势"]
LIVELY_HINTS = ["外向", "活泼", "表达", "热情", "社交", "创作", "音乐", "摄影", "追星"]
REASON_MARKERS = ["因为", "主要是", "更多是", "比如", "例如", "像", "场景", "细节", "一方面", "另一方面"]
FOLLOWUP_PLAIN_PREFIXES = (
    "你刚", "刚才", "前面", "关于", "具体", "展开", "如果只", "背后", "能结合", "我想确认",
)
FOCUS_STRIP_PREFIXES = [
    "我觉得", "我感觉", "我认为", "我会觉得", "其实", "就是", "可能", "大概", "应该",
    "主要是", "更多是", "比较", "有点", "感觉", "觉得", "如果说", "如果真要说", "对我来说",
]


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"```json|```", "", text or "").strip()
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if not m:
        raise ValueError("No JSON found")
    return json.loads(m.group())


class InterviewEngine:
    refusal_patterns = ["不想", "不回答", "跳过", "不方便", "拒绝", "不说"]
    anxious_patterns = ["焦虑", "紧张", "烦", "累", "压力", "崩"]
    vague_patterns = ["还行", "一般般", "差不多", "还好", "看情况", "不一定", "说不好", "不知道"]
    opinion_keywords = [
        "喜欢", "不喜欢", "满意", "不满意", "推荐", "不推荐", "会买", "不会买",
        "值得", "不值", "太贵", "性价比", "亮点", "痛点", "顾虑", "担心",
    ]
    contrast_markers = ["但是", "不过", "反而", "其实", "却", "没想到", "相比", "竞品"]
    specific_scene_markers = [
        "比如", "例如", "场景", "通勤", "宿舍", "办公室", "旅行", "周末",
        "价格", "预算", "续航", "外观", "质感", "功能", "售后", "发热", "卡顿",
    ]
    unique_opinion_markers = ["最", "特别", "非常", "唯一", "最大", "关键", "不能接受", "惊喜"]
    contradiction_markers = ["前面说", "刚才说", "一开始说", "后来又说", "前后不一致"]
    structured_types = {"single_choice", "Likert", "likert", "rating"}

    def __init__(self):
        self.planner = LiveStagePlanner()

    # ── interviewer style ────────────────────────────────────

    def build_interviewer_style(self, agent_context: Dict[str, Any]) -> Dict[str, str]:
        profile = agent_context.get("profile") or {}
        urban = agent_context.get("urban_summary") or {}
        online = agent_context.get("online_summary") or {}
        mbti = str(profile.get("mbti", "")).upper()

        text_bits = [
            mbti,
            str(profile.get("occupation", "")),
            str(profile.get("major", "")),
            str(profile.get("personality", "")),
            " ".join(str(x) for x in profile.get("interests", [])[:6]),
            str(online.get("role", "")),
            str(urban.get("behavior_pattern", "")),
        ]
        blob = " ".join(text_bits)

        scores = {key: 0 for key in STYLE_LIBRARY}
        if any(k in blob for k in RATIONAL_HINTS) or "T" in mbti:
            scores["理性高效"] += 3
        if any(k in blob for k in WARM_HINTS) or ("I" in mbti and "F" in mbti):
            scores["温和耐心"] += 3
        if any(k in blob for k in LIVELY_HINTS) or "E" in mbti or online.get("role") in {"KOL", "普通用户"}:
            scores["活泼共情"] += 2
        if any(k in blob for k in DIRECT_HINTS) or ("J" in mbti and "T" in mbti):
            scores["直接追问"] += 2

        social_need = urban.get("avg_social_need")
        if isinstance(social_need, (float, int)):
            if social_need < 0.45:
                scores["温和耐心"] += 1
            elif social_need > 0.7:
                scores["活泼共情"] += 1

        role = online.get("role")
        if role == "潜水用户":
            scores["温和耐心"] += 1
        elif role == "KOL":
            scores["直接追问"] += 1

        style_name = max(scores, key=scores.get)
        style_conf = STYLE_LIBRARY[style_name]
        return {
            "name": style_name,
            "description": style_conf["description"],
        }

    def render_asked_question(
        self,
        question: Dict[str, Any],
        style_profile: Dict[str, str],
        asked_index: int = 0,
        is_followup: bool = False,
        followup_text: str = "",
    ) -> str:
        style_name = style_profile.get("name", "温和耐心")
        style_conf = STYLE_LIBRARY.get(style_name, STYLE_LIBRARY["温和耐心"])
        stage_name = self.planner.classify(question)

        base_text = (followup_text if is_followup else question.get("question", "")).strip()
        if not base_text:
            return ""
        if self._is_already_stylized(base_text):
            return base_text
        if is_followup and self._should_keep_followup_plain(base_text):
            return base_text

        prefix_pool = style_conf["followup_prefixes"] if is_followup else style_conf["main_prefixes"].get(stage_name, [])
        prefix = prefix_pool[asked_index % len(prefix_pool)] if prefix_pool else ""
        styled = f"{prefix}{base_text}".strip()
        styled = re.sub(r"\s+", "", styled)
        return styled

    def _is_already_stylized(self, text: str) -> bool:
        return any(
            text.startswith(prefix)
            for conf in STYLE_LIBRARY.values()
            for group in [conf["followup_prefixes"], *conf["main_prefixes"].values()]
            for prefix in group
        )

    def _should_keep_followup_plain(self, text: str) -> bool:
        return text.startswith(FOLLOWUP_PLAIN_PREFIXES) or "“" in text or "\"" in text

    # ── 事件检测 ─────────────────────────────────────────────

    def detect_event(self, text: str) -> str:
        t = (text or "").strip()
        if any(k in t for k in self.refusal_patterns):
            return "topic_refusal"
        if any(k in t for k in self.anxious_patterns):
            return "emotional_breakdown"
        if len(re.sub(r"[，。！？、…\s]", "", t)) <= 3:
            return "passive_noncooperation"
        if any(k in t for k in self.contradiction_markers):
            return "contradictory_information"
        return "none"

    def _normalize_options(self, options: Optional[List[str]]) -> List[str]:
        return [str(o).strip() for o in (options or []) if str(o).strip()]

    def _is_structured_question(self, qtype: str, options: List[str]) -> bool:
        return str(qtype or "") in self.structured_types or bool(options)

    def _extract_exact_option(self, text: str, options: List[str]) -> Optional[str]:
        if not text or not options:
            return None
        best: tuple[int, int, str] | None = None
        for opt in sorted(options, key=len, reverse=True):
            m = re.search(re.escape(opt), text, flags=re.IGNORECASE)
            if not m:
                continue
            candidate = (m.start(), -len(opt), opt)
            if best is None or candidate < best:
                best = candidate
        return best[2] if best else None

    def _is_pure_vague(self, text: str) -> bool:
        bare = re.sub(r"[，。！？、…\s]", "", (text or "").strip())
        return bare in {re.sub(r"[，。！？、…\s]", "", x) for x in self.vague_patterns}

    def assess_answer(
        self,
        question: Dict[str, Any],
        answer: str,
        event: str,
    ) -> Dict[str, Any]:
        qtype = str(question.get("type", "text_input"))
        options = self._normalize_options(question.get("options") or question.get("scale"))
        structured = self._is_structured_question(qtype, options)
        matched_option = self._extract_exact_option(answer, options) if structured else None
        text = (answer or "").strip()
        compact_len = len(re.sub(r"\s+", "", text))
        pure_vague = self._is_pure_vague(text)

        if event in ("topic_refusal", "passive_noncooperation"):
            sufficient = False
        elif structured:
            sufficient = matched_option is not None
        elif compact_len >= 12:
            sufficient = True
        elif compact_len >= 6 and not pure_vague:
            sufficient = True
        elif compact_len >= 4 and any(k in text for k in self.opinion_keywords):
            sufficient = True
        else:
            sufficient = False

        return {
            "structured": structured,
            "matched_option": matched_option,
            "sufficient": sufficient,
            "compact_len": compact_len,
            "pure_vague": pure_vague,
        }

    # ── Follow-up Gate ────────────────────────────────────────

    def followup_gate(
        self,
        state: BackendState,
        current_qid: int,
        answer: str,
        event: str,
        question: Dict[str, Any],
        stage_name: str,
        style_profile: Optional[Dict[str, str]] = None,
        recovery_mode: bool = False,
    ) -> Dict[str, Any]:
        if state.question_followup_done.get(current_qid, False):
            return self._no_followup("question already followed up")
        if not state.has_followup_budget():
            return self._no_followup("followup budget exhausted")

        assessment = self.assess_answer(question, answer, event)
        if recovery_mode:
            if current_qid not in state.deferred_ids:
                return self._no_followup("no deferred question to recover")
            probe_kind = "repair_choice" if assessment["structured"] else "recovery"
            base_followup = self._gen_followup_text(question, answer, probe_kind, style_profile)
            return self._followup_result(
                selected_qid=current_qid,
                followup_type="recovery_followup",
                probe_kind=probe_kind,
                reason="回收之前未说清的问题",
                followup_text=base_followup,
                utility_breakdown=self._utility_breakdown("recovery"),
            )

        repair_needed = self._needs_repair(assessment, event)
        if repair_needed:
            probe_kind = self._repair_probe_kind(event, assessment)
            base_followup = self._gen_followup_text(question, answer, probe_kind, style_profile)
            return self._followup_result(
                selected_qid=current_qid,
                followup_type="repair_followup",
                probe_kind=probe_kind,
                reason="回答不足或需要明确确认",
                followup_text=base_followup,
                utility_breakdown=self._utility_breakdown("repair"),
            )

        if stage_name in ("core", "attitude", "reflection") and self._needs_exploration(
            question,
            answer,
            assessment,
            stage_name,
        ):
            probe_kind = "explore_reason" if assessment["structured"] else "explore_specific"
            base_followup = self._gen_followup_text(question, answer, probe_kind, style_profile)
            return self._followup_result(
                selected_qid=current_qid,
                followup_type="explore_followup",
                probe_kind=probe_kind,
                reason="观点明确但还缺少支撑细节",
                followup_text=base_followup,
                utility_breakdown=self._utility_breakdown("explore"),
            )

        return self._no_followup("no repair or exploration condition met")

    def _no_followup(self, reason: str) -> Dict[str, Any]:
        return {
            "do_followup": False,
            "followup_type": "none",
            "probe_kind": "",
            "selected_qid": None,
            "utility_breakdown": {k: 0.0 for k in ("coverage", "discovery", "recovery", "cost", "risk", "utility")},
            "reason": reason,
            "followup_text": "",
        }

    def _followup_result(
        self,
        selected_qid: int,
        followup_type: str,
        probe_kind: str,
        reason: str,
        followup_text: str,
        utility_breakdown: Dict[str, float],
    ) -> Dict[str, Any]:
        return {
            "do_followup": True,
            "followup_type": followup_type,
            "probe_kind": probe_kind,
            "selected_qid": selected_qid,
            "utility_breakdown": utility_breakdown,
            "reason": reason,
            "followup_text": followup_text,
        }

    def _needs_repair(self, assessment: Dict[str, Any], event: str) -> bool:
        if event in ("topic_refusal", "passive_noncooperation", "emotional_breakdown", "contradictory_information"):
            return True
        if assessment["structured"] and not assessment["matched_option"]:
            return True
        if assessment["pure_vague"]:
            return True
        return not assessment["sufficient"]

    def _repair_probe_kind(self, event: str, assessment: Dict[str, Any]) -> str:
        if assessment["structured"] and not assessment["matched_option"]:
            return "repair_choice"
        if event == "contradictory_information":
            return "consistency_check"
        if event == "emotional_breakdown":
            return "gentle_repair"
        return "repair_clarify"

    def _needs_exploration(
        self,
        question: Dict[str, Any],
        answer: str,
        assessment: Dict[str, Any],
        stage_name: str,
    ) -> bool:
        if not assessment["sufficient"]:
            return False

        text = (answer or "").strip()
        if assessment["structured"]:
            matched = assessment.get("matched_option") or ""
            remainder = text.replace(matched, "", 1).strip(" ，。；;:：")
            if stage_name == "attitude" and len(remainder) <= 10:
                return True
            return False

        if assessment["compact_len"] < 8:
            return True

        if any(marker in text for marker in self.specific_scene_markers):
            return False
        if any(marker in text for marker in REASON_MARKERS):
            return False
        if any(marker in text for marker in self.contrast_markers):
            return False
        if re.search(r"\d", text):
            return False

        return assessment["compact_len"] <= 24

    def _utility_breakdown(self, mode: str) -> Dict[str, float]:
        presets = {
            "repair": {"coverage": 0.95, "discovery": 0.35, "recovery": 0.40, "cost": 0.20, "risk": 0.06, "utility": 0.78},
            "explore": {"coverage": 0.58, "discovery": 0.76, "recovery": 0.00, "cost": 0.16, "risk": 0.05, "utility": 0.69},
            "recovery": {"coverage": 0.65, "discovery": 0.40, "recovery": 0.95, "cost": 0.22, "risk": 0.05, "utility": 0.72},
        }
        return presets.get(mode, {"coverage": 0.0, "discovery": 0.0, "recovery": 0.0, "cost": 0.0, "risk": 0.0, "utility": 0.0})

    def _gen_followup_text(
        self,
        question: Dict[str, Any],
        answer: str,
        probe_kind: str,
        style_profile: Optional[Dict[str, str]] = None,
    ) -> str:
        options = self._normalize_options(question.get("options") or question.get("scale"))
        style_name = (style_profile or {}).get("name", "温和耐心")
        focused_fallback = self._build_targeted_followup(question, answer, probe_kind, options)

        if probe_kind == "repair_choice":
            return self.render_asked_question(question, {"name": style_name}, is_followup=True, followup_text=focused_fallback)

        prompt = (
            "你是访谈员助手，根据受访者回答生成一个简短自然的追问（1句话，口语化，不施压）。\n"
            f"当前问题：{question.get('question', '')}\n"
            f"受访者回答：{answer}\n"
            f"追问目标：{probe_kind}\n"
            f"访谈语气：{style_name}\n"
            "要求：优先提取受访者回答中的具体信息点来追问，例如理由、对象、场景、顾虑、亮点。\n"
            "不要使用空泛套话，不要只说“能具体一点吗”，不要以“没关系”“那我继续追问一个细节”开头。\n"
            "只输出 JSON：{\"followup_text\": \"...\"}"
        )
        try:
            client, model = make_llm_client(timeout=60)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=120,
            )
            raw = resp.choices[0].message.content.strip()
            obj = _extract_json(raw)
            text = (obj.get("followup_text") or "").strip()
            if text:
                return self.render_asked_question(question, {"name": style_name}, is_followup=True, followup_text=text)
        except Exception as e:
            logger.warning(f"追问生成失败: {e}")

        return self.render_asked_question(question, {"name": style_name}, is_followup=True, followup_text=focused_fallback)

    def _build_targeted_followup(
        self,
        question: Dict[str, Any],
        answer: str,
        probe_kind: str,
        options: List[str],
    ) -> str:
        focus = self._extract_focus_phrase(answer)
        quoted = f"“{focus}”" if focus else ""

        if probe_kind == "repair_choice":
            if options:
                return f"如果让你直接选一个，哪一项最符合你的想法：{' / '.join(options)}？"
            return "如果让你直接明确选一个答案，会更接近哪一项？"

        if probe_kind == "gentle_repair":
            if quoted:
                return f"你刚才提到{quoted}，这个点对你来说更接近什么感受？"
            return "如果只说一个最真实的感受，你现在会怎么回答这个问题？"

        if probe_kind == "repair_clarify":
            if quoted:
                return f"你刚才提到{quoted}，能再展开一点说说它具体体现在哪吗？"
            return "你刚才的意思里，最关键的那个点能再说具体一点吗？"

        if probe_kind == "consistency_check":
            if quoted:
                return f"我想确认一下，你刚才提到的{quoted}，更接近你的主要判断，还是只是一个补充点？"
            return "我想确认一下，你刚才这段回答里，最主要的判断到底更接近哪一种？"

        if probe_kind == "recovery":
            if quoted:
                return f"前面你提到{quoted}，如果只补充一个最关键的细节，你会补哪一点？"
            return f"前面关于“{question.get('question', '')}”这个问题，我们还差一个更明确的点，你会怎么补充？"

        if probe_kind == "explore_reason":
            if quoted:
                return f"你刚才提到{quoted}，背后最主要的原因是什么？"
            return "你这个判断背后最主要的原因是什么？"

        if probe_kind == "explore_specific":
            if quoted:
                return f"你刚提到{quoted}，能结合一个更具体的例子、场景或细节展开吗？"
            return "能结合一个更具体的例子或场景说说吗？"

        if quoted:
            return f"你刚才提到{quoted}，这个点能再具体展开一下吗？"
        return "你刚才最想表达的那个点，能再具体展开一下吗？"

    def _extract_focus_phrase(self, answer: str) -> str:
        text = re.sub(r"[（(].*?[)）]|\[.*?]|\{.*?}|【.*?】", "", (answer or "")).strip()
        if not text:
            return ""

        clauses = [
            seg.strip(" ，。！？；;：:、")
            for seg in re.split(r"[，。！？；;：:\n]", text)
            if seg.strip(" ，。！？；;：:、")
        ]
        scored: List[tuple[int, str]] = []
        for clause in clauses:
            compact = clause
            for prefix in FOCUS_STRIP_PREFIXES:
                if compact.startswith(prefix):
                    compact = compact[len(prefix):].strip()
            compact = compact.strip(" ，。！？；;：:、")
            if len(compact) < 3:
                continue
            score = min(len(compact), 18)
            if any(marker in compact for marker in self.specific_scene_markers):
                score += 4
            if any(marker in compact for marker in REASON_MARKERS):
                score += 3
            if any(marker in compact for marker in self.contrast_markers):
                score += 2
            if re.search(r"\d", compact):
                score += 2
            scored.append((score, compact[:24]))

        if not scored:
            cleaned = text[:24].strip(" ，。！？；;：:、")
            return cleaned
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    # ── 问题推进 ─────────────────────────────────────────────

    def update_stage(self, state: BackendState) -> Dict[str, Any]:
        stage = self.planner.plan(state)
        state.stage_name = stage["stage_name"]
        state.stage_goal = stage["stage_goal"]
        state.in_recovery_phase = bool(stage.get("recovery_mode"))
        return stage

    def select_next_qid(self, state: BackendState, stage_candidates: List[int], event: str) -> Optional[int]:
        remaining_primary = state.remaining_primary_ids()
        if remaining_primary:
            return remaining_primary[0]
        if state.has_followup_budget():
            recovery_ids = state.recovery_question_ids()
            if recovery_ids:
                return recovery_ids[0]
        return None

    def build_context(self, state: BackendState, limit: int = 8) -> List[Dict[str, str]]:
        return [
            {"role": t["role"], "content": t.get("content", "")}
            for t in state.interview_transcript[-limit:]
        ]

    def record_answer(
        self,
        state: BackendState,
        qid: int,
        asked_question_text: str,
        source_question_text: str,
        question_style: str,
        answer: str,
        event: str,
        stage_name: str,
        is_followup: bool = False,
    ) -> Dict[str, Any]:
        q = state.question_map.get(qid, {})
        already_answered = qid in state.answered_ids
        assessment = self.assess_answer(q, answer, event)

        state.qa_pairs.append({
            "id": qid,
            "question": asked_question_text,
            "source_question": source_question_text,
            "question_style": question_style,
            "answer": answer,
            "event_type": event,
            "is_followup": is_followup,
            "resolved": assessment["sufficient"],
            "matched_option": assessment["matched_option"],
            "stage": stage_name,
        })

        state.add_transcript_turn(
            role="interviewer",
            content=asked_question_text,
            qid=qid,
            is_followup=is_followup,
            stage_name=stage_name,
        )
        state.add_transcript_turn(
            role="agent",
            content=answer,
            qid=qid,
            is_followup=is_followup,
            stage_name=stage_name,
            event_type=event,
        )

        if not is_followup:
            state.mark_question_asked(qid)

        if assessment["sufficient"]:
            if qid not in state.answered_ids:
                state.answered_ids.append(qid)
            if qid in state.deferred_ids:
                state.deferred_ids.remove(qid)
        elif not already_answered and qid not in state.skipped_ids and qid not in state.deferred_ids:
            state.deferred_ids.append(qid)

        return assessment

    def build_report(self, state: BackendState, agent_meta: Dict[str, Any]) -> Dict[str, Any]:
        answered_cnt = len(set(state.answered_ids))
        total_cnt = max(1, len(state.questions))
        followup_cnt = sum(1 for x in state.followup_history if x.get("do_followup"))
        attitude_score = _infer_attitude_score(state.qa_pairs)
        qa_summary = [
            {
                "question": p.get("source_question") or p["question"],
                "asked_question": p["question"],
                "answer": p["answer"],
            }
            for p in state.qa_pairs if not p.get("is_followup") and p.get("answer")
        ]
        return {
            "agent_id": agent_meta.get("id"),
            "agent_name": agent_meta.get("name", ""),
            "product": state.topic,
            "attitude_score": attitude_score,
            "attitude_label": _attitude_label(attitude_score),
            "answered_count": answered_cnt,
            "total_questions": total_cnt,
            "followup_count": followup_cnt,
            "qa_pairs": state.qa_pairs,
            "qa_summary": qa_summary,
            "key_opinions": _extract_key_opinions(state.qa_pairs),
            "process_metrics": {
                "followup_rate": round(followup_cnt / total_cnt, 3),
                "answer_coverage": round(answered_cnt / total_cnt, 3),
                "turns_used": state.used_turns,
            },
        }


def _infer_attitude_score(qa_pairs: List[Dict[str, Any]]) -> float:
    positive = ["喜欢", "好", "满意", "推荐", "购买", "值得", "不错", "棒", "优秀", "会买"]
    negative = ["不好", "贵", "差", "不满意", "不推荐", "不买", "失望", "糟", "不值"]
    pos, neg = 0, 0
    for qa in qa_pairs:
        ans = qa.get("answer", "")
        pos += sum(1 for k in positive if k in ans)
        neg += sum(1 for k in negative if k in ans)
        for opt, score in [
            ("非常满意", 5), ("满意", 4), ("一般", 3), ("不满意", 2), ("非常不满意", 1),
            ("非常可能", 5), ("可能", 4), ("不确定", 3), ("不可能", 2), ("非常不可能", 1),
        ]:
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


def _extract_key_opinions(qa_pairs: List[Dict[str, Any]]) -> List[str]:
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
