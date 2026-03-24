"""
Interview Blueprint — 7 个 Flask 路由
"""

import json
import logging
import os
import queue
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context
from openai import OpenAI

from .engine    import InterviewEngine
from .persona   import extract_online_summary, extract_urban_summary
from .responder import VirtualAgentResponder
from .state     import BackendState

logger = logging.getLogger(__name__)
bp = Blueprint("interview", __name__)

_sessions: Dict[str, Dict] = {}


# ── 工具函数 ──────────────────────────────────────────────────

def _llm_client():
    api_key  = os.environ.get("LLM_API_KEY", "")
    api_base = os.environ.get("LLM_API_BASE", "https://api.deepseek.com")
    model    = os.environ.get("LLM_MODEL", "deepseek-chat").split("/")[-1]
    return OpenAI(api_key=api_key, base_url=api_base, timeout=90), model


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"```json|```", "", text or "").strip()
    m = re.search(r"[\[{][\s\S]*[\]}]", cleaned)
    if not m:
        raise ValueError("No JSON found")
    return json.loads(m.group())


def _profiles_path() -> Path:
    return Path(__file__).parent.parent / "users" / "student_profiles.json"


def _load_profiles() -> list:
    p = _profiles_path()
    return json.loads(p.read_text("utf-8")) if p.exists() else []


# ══════════════════════════════════════════════════════════════
#  1. 生成问卷草稿
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/generate-questionnaire", methods=["POST"])
def generate_questionnaire():
    body          = request.get_json() or {}
    product_name  = body.get("product_name", "").strip()
    background    = body.get("background", "").strip()
    goal          = body.get("goal", "购买意愿").strip()
    num_questions = int(body.get("num_questions", 15))

    if not product_name:
        return jsonify({"error": "product_name is required"}), 400

    prompt = f"""你是一位消费者研究专家，需要为访谈虚拟受访者设计一份关于「{product_name}」的问卷。

商品背景：{background or '无额外背景'}
访谈目标：{goal}
题目数量：{num_questions} 题

请按以下 5 个阶段生成问卷：
- basic（2~3题）：受访者与产品的关系（是否了解/用过、了解渠道）
- core（4~5题）：使用体验与功能评价（第一印象、具体功能、亮点/痛点）
- attitude（2~3题）：量化态度（满意度 Likert、购买意愿 Likert）
- reflection（3~4题）：深层反思（竞品对比、改进建议、购买障碍）
- closing（1~2题）：收尾（是否推荐、补充说明）

要求：
1. 语言口语化、自然，适合访谈对话
2. Likert 题含 scale 字段（如 ["非常满意","满意","一般","不满意","非常不满意"]）
3. 单选题含 options 字段
4. 每题带 stage 字段

只输出 JSON 数组：
[
  {{"id":1,"stage":"basic","question":"...","type":"single_choice","options":["...","..."]}},
  {{"id":2,"stage":"core","question":"...","type":"text_input"}},
  {{"id":3,"stage":"attitude","question":"...","type":"Likert","scale":["非常满意","满意","一般","不满意","非常不满意"]}},
  ...
]"""

    try:
        client, model = _llm_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=2000,
        )
        raw       = resp.choices[0].message.content.strip()
        questions = _extract_json(raw)
        if not isinstance(questions, list):
            raise ValueError("Expected JSON array")
        for i, q in enumerate(questions):
            if "id" not in q:
                q["id"] = i + 1
        return jsonify({"questions": questions})
    except Exception as e:
        logger.error(f"问卷生成失败: {e}")
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  2. 创建访谈会话
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/sessions", methods=["POST"])
def create_session():
    body          = request.get_json() or {}
    questions     = body.get("questions", [])
    product_name  = body.get("product_name", "未知商品").strip()
    agent_ids     = body.get("agent_ids", [])      # [{id, name, ...}, ...]
    sim_id_urban  = body.get("sim_id_urban")
    sim_id_online = body.get("sim_id_online")

    if not questions:
        return jsonify({"error": "questions is required"}), 400
    if not agent_ids:
        return jsonify({"error": "agent_ids is required"}), 400

    session_id   = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    all_profiles = _load_profiles()
    profile_map  = {p.get("name"): p for p in all_profiles}

    # 可选：加载仿真摘要
    urban_steps   = None
    online_agents = None

    if sim_id_urban:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            import storage
            urban_steps = storage.get_simulation_steps(sim_id_urban)
        except Exception as e:
            logger.warning(f"无法加载城市仿真 {sim_id_urban}: {e}")

    if sim_id_online:
        try:
            from marketing.online_sim import get_session_agents
            online_agents = get_session_agents(sim_id_online)
        except Exception as e:
            logger.warning(f"无法加载线上仿真 {sim_id_online}: {e}")

    agent_contexts: Dict[int, Dict] = {}
    for meta_agent in agent_ids:
        aid  = meta_agent.get("id")
        name = meta_agent.get("name", "")
        full_profile = profile_map.get(name, meta_agent)

        agent_contexts[aid] = {
            "meta":           meta_agent,
            "profile":        full_profile,
            "urban_summary":  extract_urban_summary(urban_steps, aid) if urban_steps else None,
            "online_summary": extract_online_summary(online_agents, aid) if online_agents else None,
        }

    _sessions[session_id] = {
        "session_id":     session_id,
        "product_name":   product_name,
        "questions":      questions,
        "agent_ids":      [a["id"] for a in agent_ids],
        "agent_contexts": agent_contexts,
        "agent_states":   {
            aid: {"status": "pending", "state": None, "report": None, "queue": None}
            for aid in agent_contexts
        },
        "created_at": datetime.now().isoformat(),
    }
    return jsonify({"session_id": session_id})


# ══════════════════════════════════════════════════════════════
#  3. 获取 Agent 访谈状态列表
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/sessions/<session_id>/agents")
def get_session_agents_status(session_id):
    sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    result = []
    for aid, st in sess["agent_states"].items():
        ctx  = sess["agent_contexts"].get(aid, {})
        meta = ctx.get("meta", {})
        result.append({
            "agent_id": aid,
            "name":     meta.get("name", str(aid)),
            "status":   st["status"],
            "report":   st["report"],
        })
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  4. SSE 实时访谈流
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/sessions/<session_id>/agents/<int:agent_id>/stream")
def agent_interview_stream(session_id, agent_id):
    sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    agent_st = sess["agent_states"].get(agent_id)
    if not agent_st:
        return jsonify({"error": "agent not found in session"}), 404

    # 已完成：重放报告
    if agent_st["status"] == "done" and agent_st["report"]:
        def replay():
            for pair in agent_st["report"].get("qa_pairs", []):
                ev = {"type": "followup" if pair.get("is_followup") else "qa",
                      "question": pair["question"], "answer": pair["answer"]}
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'done','report':agent_st['report']}, ensure_ascii=False)}\n\n"
        return Response(stream_with_context(replay()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # 正在运行：接上已有队列
    if agent_st["status"] == "running" and agent_st["queue"] is not None:
        q = agent_st["queue"]
    else:
        q = queue.Queue()
        agent_st["queue"]  = q
        agent_st["status"] = "running"
        ctx = sess["agent_contexts"][agent_id]
        threading.Thread(
            target=_run_interview_thread,
            args=(sess, agent_id, ctx, q),
            daemon=True,
        ).start()

    def generate():
        while True:
            ev = q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════════════════════
#  5. 获取单 Agent 完整报告
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/sessions/<session_id>/agents/<int:agent_id>/report")
def get_agent_report(session_id, agent_id):
    sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    st = sess["agent_states"].get(agent_id)
    if not st or st["status"] != "done":
        return jsonify({"error": "interview not completed"}), 404
    return jsonify(st["report"])


# ══════════════════════════════════════════════════════════════
#  6. 汇总分析
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/sessions/<session_id>/summary")
def get_session_summary(session_id):
    sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404

    done = [st for st in sess["agent_states"].values()
            if st["status"] == "done" and st["report"]]
    if not done:
        return jsonify({"error": "no completed interviews"}), 400

    reports   = [st["report"] for st in done]
    scores    = [r["attitude_score"] for r in reports if r.get("attitude_score")]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 3.0
    dist      = {"正面": 0, "中立": 0, "负面": 0}
    for r in reports:
        label = r.get("attitude_label", "中立")
        dist[label] = dist.get(label, 0) + 1

    all_opinions = []
    for r in reports:
        all_opinions.extend(r.get("key_opinions", [])[:2])

    return jsonify({
        "total_completed":      len(done),
        "avg_attitude_score":   avg_score,
        "attitude_distribution": dist,
        "key_opinions_sample":  all_opinions[:8],
        "agents": [
            {"agent_id": r["agent_id"], "name": r["agent_name"],
             "attitude_score": r["attitude_score"], "attitude_label": r["attitude_label"]}
            for r in reports
        ],
    })


# ══════════════════════════════════════════════════════════════
#  7. AI 深度解读
# ══════════════════════════════════════════════════════════════

@bp.route("/api/interview/sessions/<session_id>/analyze", methods=["POST"])
def analyze_session(session_id):
    sess = _sessions.get(session_id)
    if not sess:
        return jsonify({"error": "session not found"}), 404

    done = [st["report"] for st in sess["agent_states"].values()
            if st["status"] == "done" and st["report"]]
    if len(done) < 2:
        return jsonify({"error": "需要至少 2 个完成的访谈"}), 400

    summary_text = "\n\n".join(
        f"受访者：{r['agent_name']}（态度：{r['attitude_label']}，分：{r['attitude_score']}）\n"
        + "\n".join(f"  Q: {p['question']}\n  A: {p['answer']}"
                    for p in r.get("qa_pairs", [])[:6] if not p.get("is_followup"))
        for r in done
    )

    prompt = f"""你是消费者研究分析师。以下是对「{sess['product_name']}」的 {len(done)} 份虚拟受访者访谈：

{summary_text}

请生成跨受访者分析报告，包含：
1. 整体态度概述（1~2句）
2. 主要共识点（最多3条）
3. 主要分歧点（最多2条）
4. 关键洞察与建议（最多3条）

只输出 JSON：
{{"overview":"...","consensus":["..."],"divergence":["..."],"insights":["..."]}}"""

    try:
        client, model = _llm_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=600,
        )
        raw    = resp.choices[0].message.content.strip()
        result = _extract_json(raw)
        return jsonify(result)
    except Exception as e:
        logger.error(f"AI 解读失败: {e}")
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════
#  访谈后台线程
# ══════════════════════════════════════════════════════════════

def _run_interview_thread(sess: Dict, agent_id: int, ctx: Dict, q: queue.Queue):
    agent_st = sess["agent_states"][agent_id]
    try:
        responder = VirtualAgentResponder(
            agent_profile  = ctx["profile"],
            product_name   = sess["product_name"],
            urban_summary  = ctx.get("urban_summary"),
            online_summary = ctx.get("online_summary"),
        )
        engine = InterviewEngine()
        state  = BackendState(topic=sess["product_name"], questions=sess["questions"])

        while not state.finished:
            stage    = engine.update_stage(state)
            next_qid = engine.select_next_qid(state, stage["stage_candidates"], "none")

            if next_qid is None or state.used_turns >= state.max_turns:
                state.finished = True
                break

            state.current_qid = next_qid
            question = state.question_map[next_qid]
            q_text   = question.get("question", "")
            q_type   = question.get("type", "text_input")
            q_opts   = question.get("options") or question.get("scale")

            # 构建对话历史
            context = [
                {"role": t["role"], "content": t.get("content", "")}
                for t in state.interview_transcript[-6:]
            ]

            # 虚拟 Agent 回答
            answer     = responder.reply(q_text, q_type, q_opts, context)
            event_type = engine.detect_event(answer)
            engine.record_answer(state, next_qid, answer, event_type, is_followup=False)
            state.used_turns += 1

            q.put({"type": "qa", "qid": next_qid, "stage": stage["stage_name"],
                   "question": q_text, "answer": answer})

            # Follow-up Gate
            fu = engine.followup_gate(state, next_qid, answer, event_type,
                                      stage["stage_candidates"])
            if fu["do_followup"]:
                fu_text   = fu["followup_text"]
                fu_answer = responder.reply(fu_text, "text_input", None, context)
                fu_event  = engine.detect_event(fu_answer)

                engine.record_answer(state, fu["selected_qid"], fu_answer, fu_event, is_followup=True)
                state.followup_history.append({**fu, "answer": fu_answer,
                                               "recovered": fu_event == "none"})
                state.question_followup_done[next_qid] = True
                state.used_turns += 1

                q.put({"type": "followup", "qid": fu["selected_qid"],
                       "question": fu_text, "answer": fu_answer})

        # 生成报告
        report = engine.build_report(state, ctx.get("meta", {}))
        agent_st["report"] = report
        agent_st["status"] = "done"
        q.put({"type": "done", "report": report})

    except Exception as e:
        logger.error(f"访谈线程异常 agent_id={agent_id}: {e}", exc_info=True)
        agent_st["status"] = "error"
        q.put({"type": "error", "message": str(e)})
    finally:
        q.put(None)
