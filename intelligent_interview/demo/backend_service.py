import json
import importlib.util
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

DEMO_DIR = os.path.abspath(os.path.dirname(__file__))
LOCAL_CONFIG_PATH = os.path.join(DEMO_DIR, "config.json")


@dataclass
class ModelConfig:
    name: str
    api_key: str
    base_url: str


def _load_model_cfg() -> ModelConfig:
    env_key = os.environ.get("DEMO_QWEN3_API_KEY", "").strip()
    env_url = os.environ.get("DEMO_QWEN3_BASE_URL", "").strip()
    env_model = os.environ.get("DEMO_INTERVIEWER_MODEL", "").strip() or "qwen3-max"
    if env_key and env_url:
        return ModelConfig(name=env_model, api_key=env_key, base_url=env_url)

    if not os.path.exists(LOCAL_CONFIG_PATH):
        raise FileNotFoundError(
            "Missing demo config: set DEMO_QWEN3_API_KEY/DEMO_QWEN3_BASE_URL or create demo_web_streamlit/config.json"
        )
    with open(LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    models = raw.get("models", [])
    selected = None
    for m in models:
        name = str(m.get("name", ""))
        if name == "qwen3-max":
            selected = m
            break
        if selected is None and "qwen3" in name.lower():
            selected = m
    if not selected:
        raise ValueError("No qwen3 model found in demo_web_streamlit/config.json")
    return ModelConfig(name=selected["name"], api_key=selected["api_key"], base_url=selected["base_url"])


def _load_runner_v2_module():
    """按文件路径加载 run_benchmark_v2，保证 demo 可在同仓库内复用其访谈员决策逻辑。"""
    module_path = os.path.abspath(os.path.join(DEMO_DIR, "..", "run_benchmark_v2.py"))
    if not os.path.exists(module_path):
        return None
    spec = importlib.util.spec_from_file_location("run_benchmark_v2_local", module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class BackendState:
    topic: str
    questions: List[Dict[str, Any]]
    idx: int = 0
    retries: int = 0
    finished: bool = False
    qa_pairs: Optional[List[Dict[str, Any]]] = None
    answered_ids: Optional[List[int]] = None
    skipped_ids: Optional[List[int]] = None
    deferred_ids: Optional[List[int]] = None
    current_qid: Optional[int] = None
    stage_name: str = "basic"
    stage_goal: str = ""
    used_turns: int = 0
    max_turns: int = 0
    question_followup_done: Optional[Dict[int, bool]] = None
    followup_history: Optional[List[Dict[str, Any]]] = None
    state_tracker: Optional[Dict[int, str]] = None
    policy_tracker: Optional[Dict[int, str]] = None
    awaiting_followup_answer: bool = False
    pending_followup_qid: Optional[int] = None
    pending_followup_decision: Optional[Dict[str, Any]] = None
    rolling_summary: str = ""
    question_map: Optional[Dict[int, Dict[str, Any]]] = None
    plan_used: int = 0
    plan_limit: int = 2
    intro_summary: Optional[Dict[str, Any]] = None
    interview_transcript: Optional[List[Dict[str, Any]]] = None

    def __post_init__(self):
        if self.qa_pairs is None:
            self.qa_pairs = []
        if self.answered_ids is None:
            self.answered_ids = []
        if self.skipped_ids is None:
            self.skipped_ids = []
        if self.deferred_ids is None:
            self.deferred_ids = []
        if self.question_followup_done is None:
            self.question_followup_done = {}
        if self.followup_history is None:
            self.followup_history = []
        if self.state_tracker is None:
            self.state_tracker = {}
        if self.policy_tracker is None:
            self.policy_tracker = {}
        if self.question_map is None:
            self.question_map = {}
            for q in self.questions:
                try:
                    self.question_map[int(q.get("id"))] = q
                except Exception:
                    continue
        if self.max_turns <= 0:
            self.max_turns = max(6, len(self.questions) * 3)
        if self.intro_summary is None:
            self.intro_summary = {}
        if self.interview_transcript is None:
            self.interview_transcript = []


class JSONSchemaGuard:
    @staticmethod
    def extract_json(text: str) -> Any:
        cleaned = re.sub(r"```json|```", "", text or "").strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise ValueError("No JSON found")
        return json.loads(match.group())

    @staticmethod
    def validate_followup_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if "followup_text" not in payload:
            return False
        return isinstance(payload.get("followup_text"), str) and bool(payload.get("followup_text").strip())

    @staticmethod
    def validate_intro_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("intro_text"), str):
            return False
        if not isinstance(payload.get("summary"), dict):
            return False
        return True

    @staticmethod
    def validate_persona_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("persona_profile"), str):
            return False
        if not isinstance(payload.get("persona_facts"), list):
            return False
        if not isinstance(payload.get("persona_traits"), dict):
            return False
        if not isinstance(payload.get("answer_memory"), dict):
            return False
        return True


class LiveStagePlanner:
    stage_order = ["basic", "core", "attitude", "reflection", "closing"]
    stage_goals = {
        "basic": "快速建立背景与基础事实",
        "core": "覆盖核心经历与关键事实",
        "attitude": "补齐态度、倾向与评价",
        "reflection": "引导解释、反思和原因",
        "closing": "收尾与补充未覆盖信息",
    }

    def classify(self, q: Dict[str, Any]) -> str:
        text = str(q.get("question", ""))
        qtype = q.get("type")
        if qtype == "Likert":
            return "attitude"
        if any(k in text for k in ["原因", "为什么", "如何看待", "影响", "反思"]):
            return "reflection"
        if any(k in text for k in ["意见", "补充", "其他"]):
            return "closing"
        if any(k in text for k in ["年龄", "性别", "学校", "专业", "背景", "国籍", "身份"]):
            return "basic"
        return "core"

    def plan(self, state: BackendState) -> Dict[str, Any]:
        remaining = []
        for q in state.questions:
            qid = int(q.get("id"))
            if qid in state.answered_ids or qid in state.skipped_ids:
                continue
            remaining.append(qid)

        stage_candidates = {s: [] for s in self.stage_order}
        for qid in remaining:
            q = state.question_map.get(qid, {})
            st = self.classify(q)
            stage_candidates[st].append(qid)

        stage_name = "closing"
        for s in self.stage_order:
            if stage_candidates[s]:
                stage_name = s
                break
        return {
            "stage_name": stage_name,
            "stage_goal": self.stage_goals.get(stage_name, ""),
            "stage_candidates": stage_candidates.get(stage_name, []),
        }


class BaseInterviewBackend:
    name = "base"

    def start(self, topic: str, questions: List[Dict[str, Any]]) -> BackendState:
        return BackendState(topic=topic, questions=questions)

    def opening_prompt(self, state: BackendState) -> str:
        if state.finished or state.idx >= len(state.questions):
            return "访谈已结束。"
        q = state.questions[state.idx]
        state.current_qid = int(q.get("id"))
        return q.get("question", "")

    def opening_messages(self, state: BackendState) -> List[str]:
        first = self.opening_prompt(state)
        return [first] if first else []

    def step(self, state: BackendState, user_text: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError

    def build_report(self, state: BackendState) -> Dict[str, Any]:
        answers = [x.get("answer", "") for x in state.qa_pairs if x.get("answer")]
        joined = "\n".join(answers)
        big5 = {
            "开放性": "高" if any(k in joined for k in ["尝试", "探索", "创意", "新"]) else "中",
            "尽责性": "高" if any(k in joined for k in ["计划", "执行", "复盘", "坚持"]) else "中",
            "外向性": "高" if any(k in joined for k in ["社交", "表达", "合作"]) else "中",
            "宜人性": "高" if any(k in joined for k in ["理解", "支持", "共情"]) else "中",
            "神经质": "高" if any(k in joined for k in ["焦虑", "紧张", "压力"]) else "中",
        }
        question_count = max(1, len(state.questions))
        answered_count = len(set(state.answered_ids))
        followup_count = len([x for x in state.followup_history if x.get("do_followup")])
        deferred_recovery = len(
            [x for x in state.followup_history if x.get("followup_type") == "deferred_recovery" and x.get("recovered")]
        )
        info_gain = 0
        for x in state.followup_history:
            ub = x.get("utility_breakdown", {})
            info_gain += float(ub.get("coverage", 0)) + float(ub.get("discovery", 0)) + float(ub.get("recovery", 0))

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "topic": state.topic,
            "qa_pairs": state.qa_pairs,
            "predicted_options": {
                "访谈主题": state.topic,
                "回答完整度": "高" if answered_count >= max(1, question_count - 1) else "中",
                "风险信号": "有" if any(x.get("event_type") not in (None, "none") for x in state.qa_pairs) else "无",
            },
            "persona_model": {
                "big_five": big5,
                "behavior_habits": ["可结合更多轮对话增强稳定性"],
            },
            "process_metrics": {
                "followup_rate_per_question": round(followup_count / question_count, 4),
                "deferred_recovery_yield": round(deferred_recovery / max(1, followup_count), 4),
                "followup_info_gain_proxy": round(info_gain, 4),
            },
        }


class RuleBackend(BaseInterviewBackend):
    name = "rule"
    refusal_patterns = ["不想", "不回答", "跳过", "不方便", "拒绝", "不聊", "不说"]
    anxious_patterns = ["焦虑", "紧张", "烦", "累", "压力", "崩"]

    def _detect_event(self, text: str) -> str:
        if any(k in text for k in self.refusal_patterns):
            return "topic_refusal"
        if any(k in text for k in self.anxious_patterns):
            return "emotional_breakdown"
        if len(text.strip()) <= 4:
            return "passive_noncooperation"
        return "none"

    def step(self, state: BackendState, user_text: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        event_type = self._detect_event(user_text)
        if state.idx >= len(state.questions):
            state.finished = True
            return {
                "event_type": event_type,
                "action": "conclude",
                "reply": "访谈已结束，感谢你的配合。",
                "next_question": None,
                "finished": True,
                "followup_decision": {
                    "do_followup": False,
                    "followup_type": "none",
                    "source": "none",
                    "selected_qid": None,
                    "utility_breakdown": {"coverage": 0, "discovery": 0, "recovery": 0, "cost": 0, "risk": 0, "utility": 0},
                    "reason": "已到终止状态",
                    "followup_text": "",
                },
                "state_summary": {
                    "stage_name": state.stage_name,
                    "event_type": event_type,
                    "deferred_count": len(state.deferred_ids),
                    "followup_triggered": False,
                },
                "trace_payload": {
                    "state": {"stage_name": state.stage_name, "deferred_ids": state.deferred_ids},
                    "action": "conclude",
                    "policy_applied": "none",
                    "event_type": event_type,
                    "followup_decision": {"do_followup": False, "reason": "finished"},
                    "turn_index": state.used_turns,
                },
                "internal_note": {"backend": self.name, "reason": "all done"},
            }
        q = state.questions[state.idx]
        qid = int(q.get("id"))
        state.current_qid = qid
        state.qa_pairs.append({"id": qid, "question": q.get("question"), "answer": user_text, "event_type": event_type, "is_followup": False})
        if event_type in ["topic_refusal", "passive_noncooperation"]:
            if qid not in state.deferred_ids:
                state.deferred_ids.append(qid)
        else:
            if qid not in state.answered_ids:
                state.answered_ids.append(qid)
            if qid in state.deferred_ids:
                state.deferred_ids.remove(qid)
        state.idx += 1
        state.used_turns += 1
        finished = state.idx >= len(state.questions) or state.used_turns >= state.max_turns
        state.finished = finished
        next_q = None if finished else state.questions[state.idx].get("question")
        decision = {
            "do_followup": False,
            "followup_type": "none",
            "source": "none",
            "selected_qid": None,
            "utility_breakdown": {"coverage": 0.0, "discovery": 0.0, "recovery": 0.0, "cost": 0.1, "risk": 0.2, "utility": -0.1},
            "reason": "rule 后端未启用追问",
            "followup_text": "",
        }
        return {
            "event_type": event_type,
            "action": "ask_question" if not finished else "conclude",
            "reply": "收到，我记下了。",
            "next_question": next_q,
            "finished": finished,
            "policy_applied": "rule_progress",
            "followup_decision": decision,
            "state_summary": {
                "stage_name": state.stage_name,
                "event_type": event_type,
                "deferred_count": len(state.deferred_ids),
                "followup_triggered": False,
            },
            "trace_payload": {
                "state": {
                    "stage_name": state.stage_name,
                    "answered_ids": state.answered_ids,
                    "deferred_ids": state.deferred_ids,
                    "current_qid": qid,
                },
                "action": "ask_question" if not finished else "conclude",
                "policy_applied": "rule_progress",
                "event_type": event_type,
                "followup_decision": decision,
                "turn_index": state.used_turns,
            },
            "internal_note": {"backend": self.name, "reason": "normal progress"},
        }


class RunBenchmarkBackend(BaseInterviewBackend):
    name = "run_benchmark"
    refusal_patterns = ["不想", "不回答", "跳过", "不方便", "拒绝", "不聊", "不说"]
    anxious_patterns = ["焦虑", "紧张", "烦", "累", "压力", "崩", "崩溃", "难受"]
    vague_patterns = ["还行", "一般", "差不多", "还好", "看情况", "不一定"]
    contradiction_markers = ["但是", "不过", "其实", "可是", "前面说"]

    def __init__(self):
        if OpenAI is None:
            raise ModuleNotFoundError("openai")
        self.runner_version = os.environ.get("DEMO_RUNNER_VERSION", "v2").strip().lower()
        self.model_cfg = _load_model_cfg()
        self.model_name = self.model_cfg.name
        self.stage_planner = LiveStagePlanner()
        self.utility_weights = {"a": 0.40, "b": 0.25, "d": 0.25, "g": 0.05, "l": 0.05}
        self.client = OpenAI(api_key=self.model_cfg.api_key, base_url=self.model_cfg.base_url)
        self.runner_agent = None
        self._runner_module = _load_runner_v2_module()
        if self._runner_module is not None:
            try:
                runner_models = self._runner_module.load_config(LOCAL_CONFIG_PATH)
                handling_mode = os.environ.get("DEMO_HANDLING_MODE", "default")
                if self.model_name in runner_models:
                    self.runner_agent = self._runner_module.InterviewerAgent(runner_models[self.model_name], handling_mode=handling_mode)
                elif runner_models:
                    first_model = next(iter(runner_models.keys()))
                    self.runner_agent = self._runner_module.InterviewerAgent(runner_models[first_model], handling_mode=handling_mode)
            except Exception:
                self.runner_agent = None

    def _llm_chat(self, messages: List[Dict[str, str]]) -> str:
        resp = self.client.chat.completions.create(model=self.model_name, messages=messages)
        if not resp or not getattr(resp, "choices", None):
            return ""
        content = resp.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for x in content:
                if isinstance(x, dict) and x.get("text"):
                    parts.append(str(x["text"]))
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _load_prompt_template(self, filename: str, fallback: str) -> str:
        path = os.path.join(DEMO_DIR, "prompts", filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return fallback

    def _generate_intro_text(self, state: BackendState) -> str:
        input_payload = {
            "topic": state.topic,
            "question_count": len(state.questions),
            "questions": [
                {
                    "id": q.get("id"),
                    "type": q.get("type", "text_input"),
                    "question": q.get("question", ""),
                    "options": q.get("options", []),
                }
                for q in state.questions
            ],
        }
        fallback_template = (
            "你是一个专业的问卷介绍生成助手。\n"
            "请根据输入内容输出严格JSON："
            "{\"intro_text\":\"...\",\"summary\":{\"purpose\":\"...\",\"modules\":[\"...\"],\"contains_sensitive\":false,\"estimated_time\":\"X-X分钟\"}}。\n"
            "要求intro_text必须包含研究目标、匿名说明、可中止说明、模块说明和预计时长。"
        )
        template = self._load_prompt_template("live_intro_prompt.txt", fallback_template)
        prompt = template.replace("{data}", json.dumps(input_payload, ensure_ascii=False))
        payload = {}
        for _ in range(2):
            raw = self._llm_chat(
                [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": prompt}]
            )
            try:
                obj = JSONSchemaGuard.extract_json(raw)
                if JSONSchemaGuard.validate_intro_payload(obj):
                    payload = obj
                    break
            except Exception:
                continue
        if payload:
            state.intro_summary = payload.get("summary", {})
            text = str(payload.get("intro_text", "")).strip()
            text = re.sub(r"\[[^\]]{1,30}\]", "", text)
            text = re.sub(r"【[^】]{1,30}】", "", text)
            text = re.sub(r"\s{2,}", " ", text).strip()
            if text:
                return text
        return "您好，我是本次访谈助手。本次交流仅用于研究分析并匿名处理，您可随时结束。若您愿意，我们现在开始。"

    def _build_persona_from_dialog(self, state: BackendState) -> Dict[str, Any]:
        answer_memory: Dict[str, str] = {}
        for item in state.qa_pairs:
            qid = item.get("id")
            ans = str(item.get("answer", "")).strip()
            if qid is None or not ans:
                continue
            answer_memory[str(qid)] = ans

        dialog_payload = state.interview_transcript or []
        fallback_template = (
            "你是访谈画像建模助手。请基于输入输出严格JSON："
            "{\"persona_profile\":\"...\",\"persona_facts\":[\"...\"],\"persona_traits\":{\"big_five\":{\"开放性\":\"中\",\"尽责性\":\"中\",\"外向性\":\"中\",\"宜人性\":\"中\",\"神经质\":\"中\"},"
            "\"behavior_habits\":[],\"language_habits\":[],\"memorable_recent_events\":[],\"deep_impression_points\":[]},\"answer_memory\":{}}"
        )
        template = self._load_prompt_template("live_persona_from_dialog_prompt.txt", fallback_template)
        prompt = (
            template.replace("{topic}", state.topic)
            .replace("{dialog_json}", json.dumps(dialog_payload, ensure_ascii=False))
            .replace("{answers_json}", json.dumps(answer_memory, ensure_ascii=False))
        )
        for _ in range(2):
            raw = self._llm_chat(
                [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": prompt}]
            )
            try:
                obj = JSONSchemaGuard.extract_json(raw)
                if JSONSchemaGuard.validate_persona_payload(obj):
                    obj["answer_memory"] = answer_memory
                    return obj
            except Exception:
                continue

        return {
            "persona_profile": "当前信息可支持基础画像，但部分维度证据不足。",
            "persona_facts": ["依据用户原始发言生成，未使用额外推断信息。"],
            "persona_traits": {
                "big_five": {"开放性": "中", "尽责性": "中", "外向性": "中", "宜人性": "中", "神经质": "中"},
                "behavior_habits": ["信息不足，需更多对话证据。"],
                "language_habits": ["表达较简洁。"],
                "memorable_recent_events": ["信息不足。"],
                "deep_impression_points": ["信息不足。"],
            },
            "answer_memory": answer_memory,
        }

    def opening_messages(self, state: BackendState) -> List[str]:
        intro = self._generate_intro_text(state)
        first_raw = self.opening_prompt(state)
        first = first_raw
        # 第一题也走“访谈化改写”，保持与后续问题一致
        if first_raw and state.current_qid is not None:
            q = state.question_map.get(int(state.current_qid), {})
            if self.runner_agent is not None:
                try:
                    first_gen = self.runner_agent.generate_response(
                        action="ask_question",
                        reason_tag="normal",
                        current_question=q,
                        history=[],
                    )
                    if isinstance(first_gen, str) and first_gen.strip():
                        first = first_gen.strip()
                except Exception:
                    pass
            else:
                try:
                    prompt = (
                        "请将下面问题改写为自然访谈提问，保持原意，不要改变信息点。"
                        f"\n原问题：{q.get('question', first_raw)}\n"
                        "只输出改写后的一个问题句。"
                    )
                    first_gen = self._llm_chat(
                        [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": prompt}]
                    )
                    if isinstance(first_gen, str) and first_gen.strip():
                        first = first_gen.strip()
                except Exception:
                    pass
        out = [intro] if intro else []
        if first:
            out.append(first)
        return out

    def _json_with_retry(self, messages: List[Dict[str, str]], retries: int = 2) -> Dict[str, Any]:
        last = {}
        msgs = list(messages)
        for _ in range(retries):
            raw = self._llm_chat(msgs)
            try:
                obj = JSONSchemaGuard.extract_json(raw)
                if JSONSchemaGuard.validate_followup_payload(obj):
                    return obj
                last = obj if isinstance(obj, dict) else {}
            except Exception:
                pass
            msgs.append({"role": "user", "content": "请只返回严格JSON: {\"followup_text\":\"...\"}，不要其他内容。"})
        return {"followup_text": last.get("followup_text", "") if isinstance(last, dict) else ""}

    def _detect_event(self, text: str) -> str:
        t = text.strip()
        if any(k in t for k in self.refusal_patterns):
            return "topic_refusal"
        if any(k in t for k in self.anxious_patterns):
            return "emotional_breakdown"
        if len(t) <= 4:
            return "passive_noncooperation"
        if any(k in t for k in self.contradiction_markers):
            return "contradictory_information"
        return "none"

    def _is_answer_sufficient(self, text: str, event_type: str) -> bool:
        t = (text or "").strip()
        if event_type in ["topic_refusal", "passive_noncooperation"]:
            return False
        return len(t) >= 4

    def _update_stage(self, state: BackendState) -> Dict[str, Any]:
        stage = self.stage_planner.plan(state)
        state.stage_name = stage["stage_name"]
        state.stage_goal = stage["stage_goal"]
        return stage

    def _select_next_main_qid(self, state: BackendState, stage_candidates: List[int], event_type: str) -> Optional[int]:
        # 先尝试回收 deferred（若当前风险不高）
        high_risk = event_type in ["topic_refusal", "emotional_breakdown"]
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

    def _extract_probe_candidates(self, user_text: str, current_qid: int) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        text = user_text or ""
        if any(k in text for k in self.vague_patterns):
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "clarify", "selected_qid": current_qid, "hint": "回答偏模糊"})
        if len(text) >= 30:
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "expand", "selected_qid": current_qid, "hint": "可补充细节"})
        if any(k in text for k in self.contradiction_markers):
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "consistency_check", "selected_qid": current_qid, "hint": "可能存在前后差异"})
        if any(k in text for k in ["焦虑", "难受", "不舒服", "紧张"]):
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "low_pressure", "selected_qid": current_qid, "hint": "情绪线索明显"})
        if not candidates:
            candidates.append({"candidate_type": "answer_probe", "probe_kind": "fill_missing", "selected_qid": current_qid, "hint": "尝试补齐信息"})
        return candidates[:3]

    def _candidate_scores(
        self,
        candidate: Dict[str, Any],
        state: BackendState,
        current_qid: int,
        stage_candidates: List[int],
        event_type: str,
    ) -> Dict[str, float]:
        qid = int(candidate.get("selected_qid") or current_qid)
        coverage = 1.0 if qid in stage_candidates and qid not in state.answered_ids else 0.4
        discovery = 0.2
        if candidate.get("probe_kind") in ["expand", "fill_missing", "clarify"]:
            discovery = 0.6
        elif candidate.get("probe_kind") == "consistency_check":
            discovery = 0.5
        recovery = 1.0 if candidate.get("candidate_type") == "deferred_recovery" else 0.0
        cost = min(1.0, state.used_turns / max(1, state.max_turns))
        risk = 0.1
        if event_type in ["topic_refusal", "emotional_breakdown"]:
            risk = 0.6
        if candidate.get("probe_kind") == "low_pressure":
            risk = max(0.05, risk - 0.25)
        a, b, d, g, l = (
            self.utility_weights["a"],
            self.utility_weights["b"],
            self.utility_weights["d"],
            self.utility_weights["g"],
            self.utility_weights["l"] + (0.15 if event_type in ["topic_refusal", "emotional_breakdown"] else 0),
        )
        utility = a * coverage + b * discovery + d * recovery - g * cost - l * risk
        return {
            "coverage": round(coverage, 4),
            "discovery": round(discovery, 4),
            "recovery": round(recovery, 4),
            "cost": round(cost, 4),
            "risk": round(risk, 4),
            "utility": round(utility, 4),
        }

    def _build_followup_candidates(
        self,
        state: BackendState,
        current_qid: int,
        user_text: str,
        event_type: str,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        if state.deferred_ids:
            for dqid in state.deferred_ids:
                if dqid == current_qid:
                    continue
                candidates.append(
                    {
                        "candidate_type": "deferred_recovery",
                        "probe_kind": "recovery",
                        "selected_qid": dqid,
                        "hint": "回收 deferred 议题",
                    }
                )
                break
        candidates.extend(self._extract_probe_candidates(user_text, current_qid))
        return candidates

    def _generate_followup_text(
        self,
        question: Dict[str, Any],
        user_text: str,
        followup_type: str,
        probe_kind: str,
    ) -> str:
        prompt = (
            "你是访谈员的文案润色器。"
            "策略已固定，不能改变策略目标。"
            "请基于给定约束，仅输出JSON: {\"followup_text\":\"...\"}。\n"
            f"followup_type={followup_type}\n"
            f"probe_kind={probe_kind}\n"
            f"目标问题={question.get('question')}\n"
            f"用户刚回答={user_text}\n"
            "要求：口吻自然、简短、不过度施压。"
        )
        payload = self._json_with_retry(
            [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": prompt}],
            retries=2,
        )
        text = (payload.get("followup_text") or "").strip()
        if text:
            return text
        if followup_type == "deferred_recovery":
            return "刚才那个点如果方便，我们只确认一个最关键细节就好。"
        return "这个点能再具体一点吗？比如一个例子就可以。"

    def _followup_gate(
        self,
        state: BackendState,
        current_qid: int,
        user_text: str,
        event_type: str,
        stage_candidates: List[int],
    ) -> Dict[str, Any]:
        candidates = self._build_followup_candidates(state, current_qid, user_text, event_type)
        scored: List[Tuple[Dict[str, Any], Dict[str, float]]] = []
        for c in candidates:
            breakdown = self._candidate_scores(c, state, current_qid, stage_candidates, event_type)
            scored.append((c, breakdown))
        scored.sort(key=lambda x: x[1]["utility"], reverse=True)
        if not scored:
            return {
                "do_followup": False,
                "followup_type": "none",
                "source": "none",
                "selected_qid": None,
                "utility_breakdown": {"coverage": 0, "discovery": 0, "recovery": 0, "cost": 0, "risk": 0, "utility": 0},
                "reason": "无候选",
                "followup_text": "",
            }
        best, ub = scored[0]
        threshold = 0.35
        qid = int(best.get("selected_qid") or current_qid)
        per_q_followup_limit = 1
        used_for_q = 1 if state.question_followup_done.get(current_qid, False) else 0
        if event_type in ["topic_refusal", "passive_noncooperation"]:
            per_q_followup_limit = 2
        do_followup = ub["utility"] >= threshold and used_for_q < per_q_followup_limit

        followup_type = "none"
        source = "none"
        reason = "低于阈值，继续主流程"
        text = ""
        if do_followup:
            followup_type = best.get("candidate_type")
            source = "deferred_ids" if followup_type == "deferred_recovery" else "answer_probe"
            reason = best.get("hint", "追问信息增量更高")
            q = state.question_map.get(qid, state.question_map.get(current_qid, {}))
            text = self._generate_followup_text(q, user_text, followup_type, best.get("probe_kind", "clarify"))
        return {
            "do_followup": do_followup,
            "followup_type": followup_type if do_followup else "none",
            "source": source if do_followup else "none",
            "selected_qid": qid if do_followup else None,
            "utility_breakdown": ub,
            "reason": reason,
            "followup_text": text,
            "probe_kind": best.get("probe_kind", ""),
        }

    def _history_to_rb(self, history: List[Dict[str, Any]], keep_last: int = 20) -> List[Dict[str, str]]:
        rb_history: List[Dict[str, str]] = []
        for m in history[-keep_last:]:
            if m.get("role") == "interviewer":
                rb_history.append({"role": "assistant", "content": m.get("content", "")})
            else:
                rb_history.append({"role": "user", "content": m.get("content", "")})
        return rb_history

    def _decide_next_question_with_agent(
        self,
        state: BackendState,
        history: List[Dict[str, Any]],
        stage: Dict[str, Any],
        default_qid: Optional[int],
    ) -> Tuple[Optional[int], str, str]:
        """
        返回: (selected_qid, action, interviewer_utterance)
        如果 runner_agent 不可用，则回退到规则选择 + 原题文本。
        """
        if default_qid is None:
            return None, "conclude", ""
        if self.runner_agent is None:
            q = state.question_map.get(default_qid, {})
            return default_qid, "ask_question", q.get("question", "")

        remaining_ids = []
        for q in state.questions:
            qid = int(q.get("id"))
            if qid in state.answered_ids or qid in state.skipped_ids:
                continue
            remaining_ids.append(qid)
        if not remaining_ids:
            return None, "conclude", ""

        try:
            action_obj = self.runner_agent.decide_action(
                answered_ids=state.answered_ids,
                skipped_ids=state.skipped_ids,
                remaining_ids=remaining_ids,
                stage_name=stage.get("stage_name", "core"),
                stage_goal=stage.get("stage_goal", ""),
                stage_candidates=stage.get("stage_candidates", []),
                plan_used=state.plan_used,
                plan_limit=state.plan_limit,
                max_turns=state.max_turns,
                used_turns=state.used_turns,
                history=[{"role": m.get("role", ""), "content": m.get("content", "")} for m in history[-20:]],
            )
            action = action_obj.get("action", "ask_question")
            if action == "plan" and state.plan_used < state.plan_limit:
                state.plan_used += 1
                action = "ask_question"
            if action == "conclude":
                utterance = self.runner_agent.generate_response(
                    action="conclude",
                    reason_tag=action_obj.get("reason_tag", "normal"),
                    current_question={},
                    history=[{"role": m.get("role", ""), "content": m.get("content", "")} for m in history[-20:]],
                )
                if not utterance:
                    utterance = "感谢你的配合，本次访谈就到这里。"
                return None, "conclude", utterance
            raw_target = action_obj.get("target_question_id")
            selected_qid = default_qid
            try:
                if raw_target is not None:
                    cand = int(raw_target)
                    if cand in remaining_ids:
                        selected_qid = cand
            except Exception:
                selected_qid = default_qid
            question = state.question_map.get(selected_qid, state.question_map.get(default_qid, {}))
            utterance = self.runner_agent.generate_response(
                action=action,
                reason_tag=action_obj.get("reason_tag", "normal"),
                current_question=question,
                history=[{"role": m.get("role", ""), "content": m.get("content", "")} for m in history[-20:]],
            )
            if not utterance:
                utterance = question.get("question", "")
            return selected_qid, action, utterance
        except Exception:
            q = state.question_map.get(default_qid, {})
            return default_qid, "ask_question", q.get("question", "")

    def step(self, state: BackendState, user_text: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        if state.finished:
            return {
                "event_type": "none",
                "action": "conclude",
                "reply": "访谈已经结束。",
                "next_question": None,
                "finished": True,
                "policy_applied": "finished",
                "followup_decision": {
                    "do_followup": False,
                    "followup_type": "none",
                    "source": "none",
                    "selected_qid": None,
                    "utility_breakdown": {"coverage": 0, "discovery": 0, "recovery": 0, "cost": 0, "risk": 0, "utility": 0},
                    "reason": "already finished",
                    "followup_text": "",
                },
                "state_summary": {
                    "stage_name": state.stage_name,
                    "event_type": "none",
                    "deferred_count": len(state.deferred_ids),
                    "followup_triggered": False,
                },
                "trace_payload": {
                    "state": {"stage_name": state.stage_name, "deferred_ids": state.deferred_ids},
                    "action": "conclude",
                    "policy_applied": "finished",
                    "event_type": "none",
                    "followup_decision": {"do_followup": False, "reason": "finished"},
                    "turn_index": state.used_turns,
                },
                "internal_note": {"backend": self.name, "model": self.model_name, "action": "conclude"},
            }

        event_type = self._detect_event(user_text)
        state.interview_transcript.append(
            {
                "turn": len(state.interview_transcript) + 1,
                "user_text": user_text,
                "event_type": event_type,
                "current_qid": state.current_qid,
            }
        )
        stage = self._update_stage(state)

        if state.awaiting_followup_answer:
            qid = int(state.pending_followup_qid or state.current_qid or 0)
            question = state.question_map.get(qid, {})
            sufficient = self._is_answer_sufficient(user_text, event_type)
            state.qa_pairs.append(
                {
                    "id": qid,
                    "question": question.get("question", ""),
                    "answer": user_text,
                    "event_type": event_type,
                    "is_followup": True,
                }
            )
            policy = "followup_recovery"
            recovered = False
            if sufficient:
                if qid not in state.answered_ids:
                    state.answered_ids.append(qid)
                if qid in state.deferred_ids:
                    state.deferred_ids.remove(qid)
                    recovered = True
            else:
                if qid not in state.deferred_ids:
                    state.deferred_ids.append(qid)
            state.awaiting_followup_answer = False
            state.pending_followup_qid = None
            state.question_followup_done[qid] = True
            state.used_turns += 1

            # 继续主问题推进
            stage = self._update_stage(state)
            default_qid = self._select_next_main_qid(state, stage["stage_candidates"], event_type)
            if default_qid is None or state.used_turns >= state.max_turns:
                state.finished = True
                action = "conclude"
                next_question = None
            else:
                next_qid, action, next_question = self._decide_next_question_with_agent(state, history, stage, default_qid)
                state.current_qid = next_qid
                if action == "conclude":
                    state.finished = True
                    next_question = None

            followup_decision = state.pending_followup_decision or {
                "do_followup": True,
                "followup_type": "answer_probe",
                "source": "answer_probe",
                "selected_qid": qid,
                "utility_breakdown": {"coverage": 0, "discovery": 0, "recovery": 0, "cost": 0, "risk": 0, "utility": 0},
                "reason": "followup answer processed",
                "followup_text": "",
            }
            followup_decision["recovered"] = recovered
            state.followup_history.append(followup_decision)
            state.pending_followup_decision = None

            trace = {
                "state": {
                    "stage_name": stage["stage_name"],
                    "answered_ids": state.answered_ids,
                    "deferred_ids": state.deferred_ids,
                    "current_qid": state.current_qid,
                },
                "action": action,
                "policy_applied": policy,
                "event_type": event_type,
                "followup_decision": followup_decision,
                "turn_index": state.used_turns,
            }
            return {
                "event_type": event_type,
                "action": action,
                "reply": "感谢你的配合，本次访谈就到这里。" if action == "conclude" else "收到，我们继续。",
                "next_question": next_question,
                "finished": state.finished,
                "policy_applied": policy,
                "followup_decision": followup_decision,
                "state_summary": {
                    "stage_name": stage["stage_name"],
                    "event_type": event_type,
                    "deferred_count": len(state.deferred_ids),
                    "followup_triggered": False,
                },
                "trace_payload": trace,
                "internal_note": {"backend": self.name, "runner_version": self.runner_version, "model": self.model_name, "action": action},
            }

        # 主问题回答路径
        if state.current_qid is None:
            stage = self._update_stage(state)
            state.current_qid = self._select_next_main_qid(state, stage["stage_candidates"], event_type)
        current_qid = int(state.current_qid or state.questions[min(state.idx, len(state.questions)-1)].get("id"))
        question = state.question_map.get(current_qid, {})
        state.qa_pairs.append(
            {
                "id": current_qid,
                "question": question.get("question", ""),
                "answer": user_text,
                "event_type": event_type,
                "is_followup": False,
            }
        )
        sufficient = self._is_answer_sufficient(user_text, event_type)
        policy_applied = "normal_progress"
        if sufficient:
            if current_qid not in state.answered_ids:
                state.answered_ids.append(current_qid)
            if current_qid in state.deferred_ids:
                state.deferred_ids.remove(current_qid)
        else:
            if current_qid not in state.deferred_ids:
                state.deferred_ids.append(current_qid)
            policy_applied = "defer_on_insufficient"

        # 每题必经 Follow-up Gate
        followup_decision = self._followup_gate(
            state=state,
            current_qid=current_qid,
            user_text=user_text,
            event_type=event_type,
            stage_candidates=stage["stage_candidates"],
        )
        state.pending_followup_decision = followup_decision
        state.used_turns += 1

        if followup_decision.get("do_followup"):
            selected_qid = int(followup_decision.get("selected_qid") or current_qid)
            state.awaiting_followup_answer = True
            state.pending_followup_qid = selected_qid
            state.current_qid = selected_qid
            action = "follow_up"
            reply = "我先补问一个小问题。"
            next_question = followup_decision.get("followup_text", "")
            if not next_question:
                q = state.question_map.get(selected_qid, question)
                next_question = q.get("question", "")
            followup_triggered = True
        else:
            stage = self._update_stage(state)
            next_qid = self._select_next_main_qid(state, stage["stage_candidates"], event_type)
            if next_qid is None or state.used_turns >= state.max_turns:
                state.finished = True
                action = "conclude"
                reply = "感谢你的配合，本次访谈就到这里。"
                next_question = None
            else:
                selected_qid, action, next_question = self._decide_next_question_with_agent(state, history, stage, next_qid)
                state.current_qid = selected_qid
                if action == "conclude":
                    state.finished = True
                    reply = next_question or "感谢你的配合，本次访谈就到这里。"
                    next_question = None
                else:
                    reply = "收到，我们继续。"
            followup_triggered = False
            state.followup_history.append(followup_decision)

        trace = {
            "state": {
                "stage_name": state.stage_name,
                "answered_ids": state.answered_ids,
                "deferred_ids": state.deferred_ids,
                "current_qid": state.current_qid,
                "used_turns": state.used_turns,
                "max_turns": state.max_turns,
            },
            "action": action,
            "policy_applied": policy_applied,
            "event_type": event_type,
            "followup_decision": followup_decision,
            "turn_index": state.used_turns,
        }
        return {
            "event_type": event_type,
            "action": action,
            "reply": reply,
            "next_question": next_question,
            "finished": state.finished,
            "policy_applied": policy_applied,
            "followup_decision": followup_decision,
            "state_summary": {
                "stage_name": state.stage_name,
                "event_type": event_type,
                "deferred_count": len(state.deferred_ids),
                "followup_triggered": followup_triggered,
            },
            "trace_payload": trace,
            "internal_note": {"backend": self.name, "runner_version": self.runner_version, "model": self.model_name, "action": action},
        }

    def build_report(self, state: BackendState) -> Dict[str, Any]:
        base = super().build_report(state)
        base["interview_transcript"] = state.interview_transcript or []
        base["intro_summary"] = state.intro_summary or {}
        base["persona_from_dialog"] = self._build_persona_from_dialog(state)
        return base


def get_backend() -> BaseInterviewBackend:
    backend_name = os.environ.get("DEMO_BACKEND", "run_benchmark").strip().lower()
    if backend_name == "rule":
        b = RuleBackend()
        b.status_label = "rule (manual)"
        return b
    try:
        b = RunBenchmarkBackend()
        b.status_label = f"run_benchmark ({getattr(b, 'model_name', 'unknown')})"
        return b
    except Exception as e:
        # 配置缺失或模型不可用时，自动回退，保证页面可用
        b = RuleBackend()
        b.status_label = f"rule_fallback ({str(e)[:80]})"
        return b
