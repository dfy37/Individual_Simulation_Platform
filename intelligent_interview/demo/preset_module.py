import json
from datetime import datetime
from typing import Any, Dict, List

import streamlit as st

from data_loader import build_preset_index, load_benchmark_assets
from db import bulk_insert_messages, create_session, upsert_report

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


def _build_report(record: Dict[str, Any], ground_truth: Dict[str, Any]) -> Dict[str, Any]:
    questionnaire = (record.get("questionnaire") or {}).get("questionnaire", {})
    questions = questionnaire.get("questions", [])
    answer_count = sum(1 for q in questions if q.get("answer") not in (None, "", "null"))
    gt = ground_truth or {}
    total_questions = len(questions)
    correct_answers = 0
    for q in questions:
        qid = str(q.get("id"))
        if str(q.get("answer")) == str(gt.get(qid)):
            correct_answers += 1
    accuracy = round(correct_answers / max(1, total_questions), 4)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "survey_name": record.get("survey_name"),
        "model": record.get("interviewer_model"),
        "turns": record.get("used_turns"),
        "event_total": record.get("event_total", 0),
        "answer_filled": answer_count,
        "correct_answers": correct_answers,
        "total_questions": total_questions,
        "accuracy": accuracy,
        # "insight": "异常高发时，访谈员更依赖追问/改问。建议展示可视化事件时间线用于说明鲁棒性。",
    }


def _save_to_db(conn, record: Dict[str, Any], show_thought: bool) -> int:
    session_id = create_session(
        conn,
        mode="preset_replay",
        questionnaire=record.get("survey_name"),
        interviewee=record.get("interviewee_model"),
        metadata={
            "benchmark_dir": record.get("benchmark_dir"),
            "interviewer_model": record.get("interviewer_model"),
            "handling_mode": record.get("handling_mode"),
            "ablation_mode": record.get("ablation_mode"),
            "show_thought": show_thought,
        },
    )

    history = (record.get("conversation") or {}).get("history", [])
    decisions = {(d.get("turn")): d for d in (record.get("conversation") or {}).get("decisions", [])}

    rows: List[tuple] = []
    turn = 0
    for i in range(0, len(history), 2):
        turn += 1
        if i + 1 >= len(history):
            break
        d = decisions.get(turn, {})
        event = (d.get("event") or {}).get("event_type")
        note = None
        if show_thought:
            note = json.dumps(
                {
                    "action": d.get("action"),
                    "reason_tag": d.get("reason_tag"),
                    "stage": d.get("stage"),
                },
                ensure_ascii=False,
            )

        rows.append((session_id, turn, "interviewer", history[i].get("content", ""), event, note, datetime.utcnow().isoformat()))
        rows.append((session_id, turn, "interviewee", history[i + 1].get("content", ""), event, None, datetime.utcnow().isoformat()))

    if rows:
        bulk_insert_messages(conn, rows)

    assets = load_benchmark_assets(record.get("benchmark_dir"))
    upsert_report(conn, session_id, _build_report(record, assets.get("ground_truth") or {}))
    return session_id


def _aggregate_events_by_qid(decisions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for d in decisions:
        qid = d.get("target_question_id")
        if qid is None:
            continue
        qid_str = str(qid)
        event = d.get("event") or {}
        et = event.get("event_type")
        if not et or et == "none":
            continue
        turn = int(d.get("turn", 0))
        if qid_str not in out:
            out[qid_str] = {"event_types": set(), "turns": []}
        out[qid_str]["event_types"].add(et)
        out[qid_str]["turns"].append(turn)

    for qid in list(out.keys()):
        types = sorted(out[qid]["event_types"])
        out[qid]["event_types"] = types
        out[qid]["turns"] = sorted(set(out[qid]["turns"]))
        if any(t in types for t in ["topic_refusal", "sensitive_topic_resistance"]):
            hint = "拒答后回收"
        elif "contradictory_information" in types:
            hint = "矛盾待澄清"
        elif "passive_noncooperation" in types:
            hint = "低配合度"
        else:
            hint = "存在异常事件"
        out[qid]["hint"] = hint
    return out


def _render_message(role: str, content: str, event_type: str = "", thought: str = "") -> None:
    with st.chat_message("assistant" if role == "interviewer" else "user"):
        cls = "interviewer-bubble" if role == "interviewer" else "interviewee-bubble"
        badge = _render_event_badge(event_type) if event_type and event_type != "none" else ""
        thought_html = ""
        if thought:
            thought_html = f"<div class='thought-mini'>{thought}</div>"
        st.markdown(
            f"<div class='chat-bubble {cls}'>{content}<div class='event-wrap'>{badge}</div>{thought_html}</div>",
            unsafe_allow_html=True,
        )


def render_preset_module(conn) -> None:
    st.subheader("模块1：预设问卷与访谈对象回放")
    st.caption("固定样例回放：展示对话过程、内部决策摘要、异常事件标记与回填对照。")

    records = build_preset_index()
    if not records:
        st.warning("未找到预设回放数据：请确认 examples/preset/manifest.json 与 outputs_v2（或配置的输出目录）中存在对应记录。")
        return

    choices = []
    for i, r in enumerate(records):
        label = f"{i+1}. {r.get('example_meta', {}).get('display_name', r.get('survey_name'))} | {r.get('interviewer_model')} | turns={r.get('used_turns')} | events={r.get('event_total')}"
        choices.append((label, r))
    selected_label = st.selectbox("选择预设样例", [x[0] for x in choices], index=0)
    record = next(x[1] for x in choices if x[0] == selected_label)

    col1, col2, col3 = st.columns(3)
    col1.metric("轮次", record.get("used_turns", 0))
    col2.metric("事件数", record.get("event_total", 0))
    q_obj = (record.get("questionnaire") or {}).get("questionnaire", {})
    col3.metric("问题数", len(q_obj.get("questions", [])))

    show_thought = st.toggle("展示内部思考摘要（非完整思维链）", value=True)

    assets = load_benchmark_assets(record.get("benchmark_dir"))
    with st.expander("查看访谈对象画像（persona）", expanded=False):
        st.json(assets.get("persona") or {})

    st.markdown("#### 回填答案 vs 原始答案")
    gt = assets.get("ground_truth") or {}
    q_rec = (record.get("questionnaire") or {}).get("questionnaire", {})
    decisions = (record.get("conversation") or {}).get("decisions", [])
    event_map = _aggregate_events_by_qid(decisions)

    rows = []
    for q in q_rec.get("questions", []):
        qid = str(q.get("id"))
        ev = event_map.get(qid, {})
        rows.append(
            {
                "id": qid,
                "question": q.get("question"),
                "filled_answer": q.get("answer"),
                "ground_truth": gt.get(qid),
                "match": str(q.get("answer")) == str(gt.get(qid)),
                "had_event": bool(ev),
                "event_types": ",".join(ev.get("event_types", [])),
                "event_turns": ",".join(str(x) for x in ev.get("turns", [])),
                "event_impact_hint": ev.get("hint", ""),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("#### 对话过程")
    history = (record.get("conversation") or {}).get("history", [])
    decision_map = {(d.get("turn")): d for d in decisions}
    max_turn = max(1, len(history) // 2)
    focus_turn = st.slider("跳转轮次", 1, max_turn, 1)

    start_turn = max(1, focus_turn - 3)
    end_turn = min(max_turn, focus_turn + 3)
    turn = 0
    for i in range(0, len(history), 2):
        turn += 1
        if i + 1 >= len(history):
            break
        if turn < start_turn or turn > end_turn:
            continue
        d = decision_map.get(turn, {})
        event_type = (d.get("event") or {}).get("event_type")
        thought = ""
        if show_thought:
            thought = f"T{turn} | action={d.get('action')} | stage={d.get('stage')} | reason={d.get('reason_tag')}"
        _render_message("interviewer", history[i].get("content", ""), event_type, thought)
        _render_message("interviewee", history[i + 1].get("content", ""), event_type, "")

    st.markdown("#### 自动总结")
    report = _build_report(record, gt)
    st.json(report)

    st.markdown("#### 个体建模（基于受访者回答）")
    final_profile = (record.get("conversation") or {}).get("final_profile") or {}
    if final_profile:
        st.json(final_profile)
    else:
        st.info("当前样例缺少 final_profile，可重跑对应 benchmark 生成。")

    if st.button("保存本次回放到数据库", type="primary"):
        session_id = _save_to_db(conn, record, show_thought)
        st.success(f"已保存，会话ID={session_id}")

    st.caption("预设回放仅做过程展示，不做现场打分。")
