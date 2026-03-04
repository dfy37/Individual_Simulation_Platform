import json
from typing import Any, Dict, List

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


def _render_event_badge(event_type: str) -> str:
    color = EVENT_COLOR.get(event_type, "#22c55e")
    return f"<span class='event-pill' style='background:{color};'>{event_type}</span>"


def _questionnaire_to_live_questions(questionnaire: Dict[str, Any], max_questions: int = 8) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for q in questionnaire.get("questions", []):
        out.append(
            {
                "id": q.get("id"),
                "question": q.get("question"),
                "type": q.get("type", "text_input"),
            }
        )
    return out[:max_questions]


def _ensure_state() -> None:
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
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _append_and_store_message(
    conn,
    role: str,
    content: str,
    event_type: str = "none",
    internal_note: Dict[str, Any] = None,
) -> int:
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

    opening_msgs = backend.opening_messages(backend_state)
    for msg in opening_msgs:
        if msg:
            _append_and_store_message(conn, "interviewer", msg)


def _render_message(role: str, content: str, event_type: str = "") -> None:
    with st.chat_message("assistant" if role == "interviewer" else "user"):
        cls = "interviewer-bubble" if role == "interviewer" else "interviewee-bubble"
        st.markdown(
            f"<div class='chat-bubble {cls}'>{content}</div>",
            unsafe_allow_html=True,
        )


def _render_summary_bar() -> None:
    s = st.session_state.live_last_summary or {}
    f = st.session_state.live_last_followup_decision or {}
    stage_name = s.get("stage_name", "-")
    followup = "是" if f.get("do_followup") else "否"
    deferred_count = s.get("deferred_count", 0)
    st.markdown(
        (
            "<div class='chat-summary-bar'>"
            f"<span><b>阶段</b>：{stage_name}</span>"
            f"<span><b>Follow-up</b>：{followup}</span>"
            f"<span><b>Deferred</b>：{deferred_count}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


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

    user_text = st.chat_input("输入你的回答")
    if not user_text:
        return

    _append_and_store_message(conn, "user", user_text)
    # 先把用户输入即时显示出来，再执行模型推理
    _render_message("user", user_text, "")
    with st.spinner("正在生成访谈回复..."):
        result = backend.step(backend_state, user_text, st.session_state.live_messages)

    event_type = result.get("event_type", "none")

    reply = (result.get("reply") or "").strip()
    next_q = (result.get("next_question") or "").strip()
    if reply and next_q and not result.get("finished", False):
        merged = f"{reply}\n\n{next_q}"
        _append_and_store_message(
            conn,
            "interviewer",
            merged,
            event_type=event_type,
            internal_note=result.get("internal_note", {}),
        )
    elif reply:
        _append_and_store_message(
            conn,
            "interviewer",
            reply,
            event_type=event_type,
            internal_note=result.get("internal_note", {}),
        )
    elif next_q and not result.get("finished", False):
        _append_and_store_message(conn, "interviewer", next_q)

    summary = result.get("state_summary", {}) or {}
    followup_decision = result.get("followup_decision", {}) or {}
    trace_payload = result.get("trace_payload", {}) or {}
    turn_index_for_trace = int(trace_payload.get("turn_index", st.session_state.live_msg_idx))
    insert_message_trace(
        conn,
        st.session_state.live_session_id,
        turn_idx=turn_index_for_trace,
        state=trace_payload.get("state", {}),
        action=trace_payload.get("action", result.get("action", "")),
        policy_applied=trace_payload.get("policy_applied", result.get("policy_applied", "")),
        event_type=trace_payload.get("event_type", event_type),
        followup_decision=trace_payload.get("followup_decision", followup_decision),
    )

    st.session_state.live_last_summary = summary
    st.session_state.live_last_followup_decision = followup_decision
    if result.get("finished", False):
        st.session_state.live_finished = True

    st.session_state.live_backend_state = backend_state
    st.rerun()
