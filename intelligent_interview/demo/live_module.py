import json
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from backend_service import get_backend
from data_loader import load_live_questionnaires
from db import create_session, insert_feedback, insert_message, insert_message_trace, upsert_report

EVENT_COLOR = {
    "topic_refusal": "#f59e0b",
    "sensitive_topic_resistance": "#f97316",
    "contradictory_information": "#ef4444",
    "passive_noncooperation": "#facc15",
    "early_termination_request": "#fb7185",
    "questionnaire_critique": "#a78bfa",
    "emotional_breakdown": "#ec4899",
}

SCAFFOLD_REPLIES = {
    "收到，我们继续。",
    "收到，我们继续",
    "收到，我记下了。",
    "收到，我记下了",
    "我先补问一个小问题。",
    "我先补问一个小问题",
}


# =========================
# UI helpers
# =========================
def _render_event_badge(event_type: str) -> str:
    color = EVENT_COLOR.get(event_type, "#22c55e")
    EVENT_CN = {
        "topic_refusal": "拒答",
        "sensitive_topic_resistance": "敏感抵触",
        "contradictory_information": "信息矛盾",
        "passive_noncooperation": "低配合",
        "early_termination_request": "提前结束",
        "questionnaire_critique": "质疑流程",
        "emotional_breakdown": "情绪波动",
    }
    label = EVENT_CN.get(event_type, "异常事件")
    return f"<span class='event-pill' style='background:{color};'>{label}</span>"

def _questionnaire_to_live_questions(questionnaire: Dict[str, Any], max_questions: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    将问卷 questions 字段转换为 backend.start 所需的简化结构
    max_questions=None 表示不截断（推荐）
    """
    out: List[Dict[str, Any]] = []
    for q in questionnaire.get("questions", []):
        out.append(
            {
                "id": q.get("id"),
                "question": q.get("question"),
                "type": q.get("type", "text_input"),
            }
        )
    if max_questions is None:
        return out
    return out[:max_questions]

def _ensure_state() -> None:
    """
    初始化 streamlit session_state
    """
    defaults = {
        "live_session_id": None,
        "live_topic": None,
        "live_messages": [],
        "live_finished": False,
        "live_backend_state": None,
        "live_backend": None,
        "live_example_id": None,
        "live_msg_idx": 0,
        "live_report_cached": None,
        "live_last_summary": {},
        "live_last_followup_decision": {},
        # ===== 新增：用于“防止一轮出现两个问题/重复问题” =====
        "live_last_question_text": "",     # 最近一次 interviewer 发出的“问题文本”
        "live_asked_question_count": 0,    # 问题计数（仅用于展示/调试）
        # ===== 新增：用于记录开场简介结构化摘要 =====
        "live_intro_summary": {},
        "live_awaiting_consent": False,
        "live_pending_first_question": "",
        "live_pending_user_text": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _append_and_store_message(
    conn,
    role: str,
    content: str,
    event_type: str = "none",
    internal_note: Optional[Dict[str, Any]] = None,
) -> int:
    """
    同步写入 session_state 和 DB
    """
    st.session_state.live_messages.append({"role": role, "content": content, "event_type": event_type})
    st.session_state.live_msg_idx += 1
    idx = int(st.session_state.live_msg_idx)
    insert_message(
        conn,
        st.session_state.live_session_id,
        idx,
        role,
        content,
        event_type=event_type,
        internal_note=json.dumps(internal_note or {}, ensure_ascii=False) if internal_note is not None else None,
    )
    return idx


def _render_message(role: str, content: str, event_type: str = "", show_event_badge: bool = False) -> None:
    """
    渲染 chat bubble（interviewer=assistant, user=human）
    默认不展示事件标签（事件标签属于系统侧标注，适合研究者视图/调试面板）
    """
    with st.chat_message("assistant" if role == "interviewer" else "user"):
        cls = "interviewer-bubble" if role == "interviewer" else "interviewee-bubble"
        badge = ""
        if show_event_badge and role == "interviewer" and event_type and event_type != "none":
            badge = _render_event_badge(event_type)
        st.markdown(
            f"<div class='chat-bubble {cls}'>{badge}{content}</div>",
            unsafe_allow_html=True,
        )

def _render_summary_bar() -> None:
    """
    顶部摘要条：尽量贴近流程图的“阶段/动作/Follow-up/Deferred/事件”等
    """
    s = st.session_state.live_last_summary or {}
    f = st.session_state.live_last_followup_decision or {}

    stage_name = s.get("stage_name", "-")
    event_type = s.get("event_type", s.get("last_event_type", "none"))
    followup = "是" if f.get("do_followup") else "否"
    deferred_count = s.get("deferred_count", 0)

    current_qid = s.get("current_qid", s.get("qid", "-"))
    answered = s.get("answered_count", s.get("answered", "-"))
    total = s.get("total_questions", s.get("total", "-"))

    # evt_badge = _render_event_badge(event_type) if event_type and event_type != "none" else ""
    evt_badge = ""

    st.markdown(
        (
            "<div class='chat-summary-bar'>"
            f"<span><b>阶段</b>：{stage_name}</span>"
            f"<span><b>题号</b>：{current_qid}</span>"
            f"<span><b>进度</b>：{answered}/{total}</span>"
            f"<span><b>Follow-up</b>：{followup}</span>"
            f"<span><b>Deferred</b>：{deferred_count}</span>"
            f"{evt_badge}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


# =========================
# 关键：开场简介生成（用模型生成目的/模块/敏感性/时间）
# =========================
def _build_intro_prompt_from_questionnaire(questionnaire: Dict[str, Any]) -> str:
    """
    生成“访谈开始前导语”提示词：强调这是访谈而非问卷，包含耗时、退出、匿名、用途、主题概览
    """
    data = json.dumps(questionnaire, ensure_ascii=False, indent=2)

    query = f"""
你是一个研究型访谈的“开场导语”生成助手。
你的任务是根据输入的访谈脚本（来源于问卷 JSON，但在本系统中视为访谈提纲），生成一段适合对受访者朗读的开场介绍。

你需要从脚本中提取并组织：
1) 访谈目的（用一句话说明）
2) 大致主题范围/模块（用 3-6 个短语概括，不要逐题念题干）
3) 是否可能涉及敏感信息（如收入、健康、家庭、政治立场等），如涉及要给出温和提醒
4) 预计耗时（按题目数量估计，给区间，比如 8–12 分钟）
5) 参与者权利：可跳过、可暂停、可随时结束，无需解释
6) 数据处理：仅用于研究/系统改进，匿名化记录，不对外泄露

额外要求：
- 语气自然、简洁、可信，不要“问卷”“填写”这类措辞，统一说“访谈”“交流”
- 不要出现具体题目文本（例如“请选择您的职位/职称”这种）
- 200-260 字左右
- 输出必须是 JSON，不要输出多余文本

输出格式：
{{
  "intro_text": "<开场导语文本>",
  "summary": {{
    "purpose": "...",
    "topics": ["...","..."],
    "contains_sensitive": true/false,
    "estimated_time": "X–X 分钟"
  }}
}}

输入脚本：
{data}
""".strip()
    return query


def _try_generate_intro_with_backend(backend, questionnaire: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    优先走 backend 的 LLM 能力；如果 backend 没暴露接口，则回退到一个稳定模板。
    返回：(intro_text, intro_summary)
    """
    prompt = _build_intro_prompt_from_questionnaire(questionnaire)

    # 1) 尝试 backend.generate_text(prompt) 之类
    for fn_name in ("generate_text", "ask_llm", "call_llm", "complete"):
        fn = getattr(backend, fn_name, None)
        if callable(fn):
            try:
                raw = fn(prompt)
                # 有些 backend 会返回 dict，有些返回 str
                if isinstance(raw, dict):
                    intro_text = str(raw.get("intro_text") or "").strip()
                    summary = raw.get("summary") or {}
                    if intro_text:
                        return intro_text, summary
                if isinstance(raw, str) and raw.strip():
                    # 尝试解析 JSON
                    txt = raw.strip()
                    try:
                        obj = json.loads(txt)
                        intro_text = str(obj.get("intro_text") or "").strip()
                        summary = obj.get("summary") or {}
                        if intro_text:
                            return intro_text, summary
                    except Exception:
                        # 不是 JSON，就当作纯文本
                        return txt, {}
            except Exception:
                # 如果某个接口调用失败，继续试下一个
                continue

    # 2) 回退：生成一个“可信但不依赖模型”的介绍
    qn = questionnaire.get("survey_name") or "本次访谈"
    n_q = len(questionnaire.get("questions", []) or [])
    # 估时：可按 22 题 ≈ 8-12 分钟做一个更稳定的区间
    est_min = max(6, int(round(n_q * 0.35)))
    est_max = max(est_min + 3, int(round(n_q * 0.55)))

    fallback_text = (
        f"在开始之前我先简单说明一下：本次是研究型访谈（{qn}），主要想了解你的相关经历与看法，"
        f"用于研究分析或系统改进。过程中你可以随时说“跳过”“暂停”或“结束”，无需说明理由。"
        f"你的回答会被匿名化记录，仅用于分析，不会对外泄露。"
        f"整个访谈大约会涉及 {n_q} 个话题点，预计用时 {est_min}–{est_max} 分钟。"
    )
    fallback_summary = {
        "purpose": "了解受访者相关经历与看法，用于研究分析或系统改进",
        "topics": [],
        "contains_sensitive": False,
        "estimated_time": f"{est_min}–{est_max} 分钟",
    }
    return fallback_text, fallback_summary


# =========================
# 关键：避免“一轮出现两个问题 / 重复问题”
# =========================
def _looks_like_question(text: str) -> bool:
    """
    轻量问句判定：用于防止 reply 已经在问问题时又追加 next_question。
    """
    t = (text or "").strip()
    if not t:
        return False
    if "？" in t or "?" in t:
        return True
    # 常见问句开头（尽量保守）
    starters = ("请问", "你能", "你可以", "能否", "是否", "方便", "想了解", "我想问", "可以说说")
    return t.startswith(starters)


def _normalize_question_text(text: str) -> str:
    """
    归一化：用于 next_question 去重
    """
    t = (text or "").strip()
    # 简单归一：去掉多余空白
    t = " ".join(t.split())
    return t


def _should_append_next_question(reply: str, next_q: str) -> bool:
    """
    决策：是否要把 next_question 作为单独消息追加出来
    解决两类问题：
    1) reply 已经像问题，则不再追加 next_q（避免一轮两个问题）
    2) next_q 与上一次提问重复，则不追加（避免重复问）
    """
    if not next_q.strip():
        return False

    # reply 本身就是一个问题：就别再追加 next_q
    if _looks_like_question(reply):
        return False

    next_norm = _normalize_question_text(next_q)
    last_norm = _normalize_question_text(st.session_state.live_last_question_text or "")
    if next_norm and last_norm and next_norm == last_norm:
        return False

    return True


# =========================
# Session start
# =========================
def _start_new_session(conn, example: Dict[str, Any]) -> None:
    backend = st.session_state.live_backend
    if backend is None:
        backend = get_backend()
        st.session_state.live_backend = backend

    questionnaire = example.get("questionnaire") or {}
    topic = example.get("display_name", "实时访谈")
    questions = _questionnaire_to_live_questions(questionnaire)
    backend_state = backend.start(topic=topic, questions=questions)

    session_id = create_session(
        conn,
        mode="live_chat",
        questionnaire=questionnaire.get("survey_name", topic),
        topic=topic,
        metadata={
            "source": "streamlit_demo",
            "example_id": example.get("id"),
            "questionnaire_path": example.get("questionnaire_path"),
            "source_type": "questionnaire_json",
        },
    )

    st.session_state.live_session_id = session_id
    st.session_state.live_topic = topic
    st.session_state.live_messages = []
    st.session_state.live_finished = False
    st.session_state.live_backend_state = backend_state
    st.session_state.live_example_id = example.get("id")
    st.session_state.live_msg_idx = 0
    st.session_state.live_report_cached = None
    st.session_state.live_last_summary = {}
    st.session_state.live_last_followup_decision = {}

    # ===== 新增：清空“最近提问”状态，避免新会话串扰 =====
    st.session_state.live_last_question_text = ""
    st.session_state.live_asked_question_count = 0
    st.session_state.live_intro_summary = {}
    st.session_state.live_awaiting_consent = False
    st.session_state.live_pending_first_question = ""

    # ===== 新增：先生成“问卷开始前简介”（包含主要目的等） =====
    # intro_text, intro_summary = _try_generate_intro_with_backend(backend, questionnaire)
    # st.session_state.live_intro_summary = intro_summary or {}
    # if intro_text:
    #    _append_and_store_message(
    #        conn,
    #        "interviewer",
    #        intro_text,
    #        event_type="none",
    #        internal_note={"kind": "questionnaire_intro", "summary": intro_summary},
    #    )

    # opening messages（例如：开场白/规则/第一题等）
    opening_msgs = [m for m in backend.opening_messages(backend_state) if m]
    if not opening_msgs:
        return

    # 只先展示开场介绍；第一题延后到用户确认参与后再展示
    intro_msg = opening_msgs[0]
    _append_and_store_message(conn, "interviewer", intro_msg)

    pending_first = opening_msgs[1] if len(opening_msgs) > 1 else ""
    st.session_state.live_pending_first_question = pending_first
    st.session_state.live_awaiting_consent = True
    _append_and_store_message(conn, "interviewer", "如果您愿意参与，请回复“愿意参与”；如果暂不参与，请回复“暂不参与”。")


def _consent_intent(text: str) -> str:
    t = (text or "").strip().lower()
    positive = ["愿意", "同意", "可以", "开始", "好", "好的", "行", "ok", "yes"]
    negative = ["不愿意", "不同意", "不可以", "不了", "暂不", "不参与", "结束", "no"]
    if any(x in t for x in negative):
        return "no"
    if any(x in t for x in positive):
        return "yes"
    return "unknown"


# =========================
# Main module
# =========================
def render_live_module(conn) -> None:
    _ensure_state()
    if st.session_state.live_backend is None:
        st.session_state.live_backend = get_backend()
    backend = st.session_state.live_backend
    examples = load_live_questionnaires()

    st.subheader("模块2：用户实时访谈")
    st.caption("真人输入驱动访谈")

    if not examples:
        st.warning("未找到实时访谈问卷，请检查 web_data/questionnaires")
        return

    labels = [x.get("direction", x.get("id")) for x in examples]
    default_idx = 0
    for i, e in enumerate(examples):
        if e.get("default"):
            default_idx = i
            break
    selected_label = st.selectbox("选择访谈方向", labels, index=default_idx)
    selected = examples[labels.index(selected_label)]

    if st.button("开始新访谈", type="primary"):
        _start_new_session(conn, selected)
        st.rerun()

    if not st.session_state.live_session_id:
        st.info("请先点击“开始新访谈”。")
        return

    _render_summary_bar()
    for m in st.session_state.live_messages:
        _render_message(m.get("role", ""), m.get("content", ""), m.get("event_type", ""))

    backend_state = st.session_state.live_backend_state

    # 结束态：展示 report + feedback
    if st.session_state.live_finished:
        if st.session_state.live_report_cached is None:
            report = backend.build_report(backend_state)
            st.session_state.live_report_cached = report
            upsert_report(conn, st.session_state.live_session_id, report)
        else:
            report = st.session_state.live_report_cached

        st.markdown("#### 访谈报告")
        st.json(report)

        enable_feedback = st.toggle("开启回访问卷", value=True)
        if enable_feedback:
            sat = st.slider("满意度", 1, 5, 4, key="live_sat")
            corr = st.slider("信息正确性", 1, 5, 4, key="live_corr")
            comments = st.text_area("反馈备注", key="live_comments")
            if st.button("提交反馈"):
                insert_feedback(conn, st.session_state.live_session_id, sat, corr, comments)
                st.success("反馈已保存")
        return

    # 已提交用户输入后，后端处理放在“下一次渲染”执行，避免输入框位置抖动
    pending_user_text = (st.session_state.live_pending_user_text or "").strip()
    if pending_user_text:
        user_text = pending_user_text
        st.session_state.live_pending_user_text = ""
    else:
        user_text = st.chat_input("输入你的回答")
        if not user_text:
            return
        _append_and_store_message(conn, "user", user_text)
        st.session_state.live_pending_user_text = user_text
        st.rerun()
        return

    # 处理参与确认，不进入主流程打分
    if st.session_state.live_awaiting_consent:
        intent = _consent_intent(user_text)
        if intent == "yes":
            st.session_state.live_awaiting_consent = False
            first_q = st.session_state.live_pending_first_question
            if first_q:
                _append_and_store_message(conn, "interviewer", first_q)
                st.session_state.live_last_question_text = first_q
                st.session_state.live_asked_question_count += 1
            else:
                _append_and_store_message(conn, "interviewer", "感谢确认，我们现在开始。")
        elif intent == "no":
            st.session_state.live_awaiting_consent = False
            st.session_state.live_finished = True
            _append_and_store_message(conn, "interviewer", "好的，已结束本次访谈。感谢您的时间。")
        else:
            _append_and_store_message(conn, "interviewer", "请确认是否参与：回复“愿意参与”或“暂不参与”。")
        st.rerun()
        return

    # 推理
    with st.spinner("正在生成访谈回复..."):
        result = backend.step(backend_state, user_text, st.session_state.live_messages)

    event_type = result.get("event_type", "none")
    reply = (result.get("reply") or "").strip()
    next_q = (result.get("next_question") or "").strip()
    finished = bool(result.get("finished", False))

    # 本轮 interviewer 写入的消息 idx（用于 trace 对齐）
    interviewer_msg_indices: List[int] = []

    # 与 run_benchmark_v2 展示方式保持一致：
    # 优先展示“实质提问(next_question)”，过滤流程性过渡话术（如“收到，我们继续”）
    should_show_reply = bool(reply) and (reply not in SCAFFOLD_REPLIES) and not (next_q and not finished)

    # 1) 写 reply（仅在非流程性话术时）
    if should_show_reply:
        idx = _append_and_store_message(
            conn,
            "interviewer",
            reply,
            event_type=event_type,
            internal_note=result.get("internal_note", {}),
        )
        interviewer_msg_indices.append(idx)

        # 如果 reply 本身像问句，把它当作“最新提问”
        if _looks_like_question(reply):
            st.session_state.live_last_question_text = reply
            st.session_state.live_asked_question_count += 1

    # 2) 只在合适时追加 next_question（解决“一轮两个问题”）
    if not finished and _should_append_next_question(reply if should_show_reply else "", next_q):
        idx = _append_and_store_message(
            conn,
            "interviewer",
            next_q,
            event_type="none",
            internal_note={"kind": "next_question"},
        )
        interviewer_msg_indices.append(idx)

        st.session_state.live_last_question_text = next_q
        st.session_state.live_asked_question_count += 1

    # State summary / followup decision / trace
    summary = result.get("state_summary", {}) or {}
    followup_decision = result.get("followup_decision", {}) or {}
    trace_payload = result.get("trace_payload", {}) or {}

    # ===== Trace 写入：与 message idx 对齐 =====
    trace_state = trace_payload.get("state", {})
    trace_action = trace_payload.get("action", result.get("action", ""))
    trace_policy = trace_payload.get("policy_applied", result.get("policy_applied", ""))
    trace_event = trace_payload.get("event_type", event_type)
    trace_follow = trace_payload.get("followup_decision", followup_decision)

    backend_turn_index = trace_payload.get("turn_index")
    if backend_turn_index is not None:
        insert_message_trace(
            conn,
            st.session_state.live_session_id,
            turn_idx=int(backend_turn_index),
            state=trace_state,
            action=trace_action,
            policy_applied=trace_policy,
            event_type=trace_event,
            followup_decision=trace_follow,
        )
    else:
        # 没有 turn_index：用本轮 interviewer message idx 逐条对齐
        if not interviewer_msg_indices:
            # 如果本轮没输出 interviewer（极少见），仍给一个可追溯的 idx
            interviewer_msg_indices = [int(st.session_state.live_msg_idx)]
        for turn_idx in interviewer_msg_indices:
            insert_message_trace(
                conn,
                st.session_state.live_session_id,
                turn_idx=int(turn_idx),
                state=trace_state,
                action=trace_action,
                policy_applied=trace_policy,
                event_type=trace_event,
                followup_decision=trace_follow,
            )

    # 更新顶部摘要栏数据
    st.session_state.live_last_summary = summary
    st.session_state.live_last_followup_decision = followup_decision

    if finished:
        st.session_state.live_finished = True
    # 后端偶发未显式 finished 时，基于动作和结束语兜底进入结算
    if result.get("action") == "conclude":
        st.session_state.live_finished = True
    if not next_q and any(x in (reply or "") for x in ["访谈就到这里", "感谢您的参与", "今天的访谈就到这里"]):
        st.session_state.live_finished = True

    st.session_state.live_backend_state = backend_state
    st.rerun()
