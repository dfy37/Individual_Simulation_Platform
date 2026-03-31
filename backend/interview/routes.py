"""
Interview Blueprint — 7 个 Flask 路由
"""

import json
import logging
import queue
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

from .engine    import InterviewEngine
from .llm       import make_llm_client
from .persona   import extract_online_summary, extract_urban_summary
from .responder import VirtualAgentResponder
from .state     import BackendState

logger = logging.getLogger(__name__)
bp = Blueprint("interview", __name__)

_sessions: Dict[str, Dict] = {}
QUESTION_STAGE_PLANS = {
    10: {"basic": 2, "core": 3, "attitude": 2, "reflection": 2, "closing": 1},
    15: {"basic": 3, "core": 4, "attitude": 3, "reflection": 3, "closing": 2},
    20: {"basic": 3, "core": 6, "attitude": 4, "reflection": 5, "closing": 2},
}
STAGE_ORDER = ["basic", "core", "attitude", "reflection", "closing"]
STAGE_ALIASES = {
    "basic": "basic",
    "background": "basic",
    "intro": "basic",
    "opening": "basic",
    "core": "core",
    "experience": "core",
    "usage": "core",
    "attitude": "attitude",
    "evaluation": "attitude",
    "rating": "attitude",
    "reflection": "reflection",
    "insight": "reflection",
    "closing": "closing",
    "end": "closing",
    "wrapup": "closing",
}


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


def _get_stage_plan(num_questions: int) -> Dict[str, int]:
    return QUESTION_STAGE_PLANS.get(int(num_questions), QUESTION_STAGE_PLANS[15])


def _build_questionnaire_prompt(
    product_name: str,
    background: str,
    goal: str,
    num_questions: int,
    stage_plan: Dict[str, int],
) -> str:
    return f"""你是一位消费者研究专家，需要为访谈虚拟受访者设计一份关于「{product_name}」的问卷。

商品背景：{background or '无额外背景'}
访谈目标：{goal}
总题数：{num_questions} 题

请严格按以下阶段与题数生成问卷，题目总数必须严格等于 {num_questions}：
- basic：{stage_plan['basic']} 题，受访者与产品的关系（是否了解/用过、了解渠道）
- core：{stage_plan['core']} 题，使用体验与功能评价（第一印象、具体功能、亮点/痛点）
- attitude：{stage_plan['attitude']} 题，量化态度（满意度 Likert、购买意愿 Likert）
- reflection：{stage_plan['reflection']} 题，深层反思（竞品对比、改进建议、购买障碍）
- closing：{stage_plan['closing']} 题，收尾（是否推荐、补充说明）

要求：
1. 语言口语化、自然，适合访谈对话
2. 所有题目必须带 stage 字段，且只能是 basic/core/attitude/reflection/closing
3. Likert 题必须含 scale 字段，如 ["非常满意","满意","一般","不满意","非常不满意"]
4. single_choice 题必须含 options 字段
5. 不要输出多余解释，不要多于 {num_questions} 题，也不要少于 {num_questions} 题

只输出 JSON 数组：
[
  {{"id":1,"stage":"basic","question":"...","type":"single_choice","options":["...","..."]}},
  {{"id":2,"stage":"core","question":"...","type":"text_input"}},
  {{"id":3,"stage":"attitude","question":"...","type":"Likert","scale":["非常满意","满意","一般","不满意","非常不满意"]}}
]"""


def _build_questionnaire_repair_prompt(
    product_name: str,
    background: str,
    goal: str,
    num_questions: int,
    stage_plan: Dict[str, int],
    previous_output: Any,
    error_message: str,
) -> str:
    prev_text = json.dumps(previous_output, ensure_ascii=False, indent=2) if previous_output is not None else "[]"
    return f"""你上一版为「{product_name}」生成的问卷不合格，需要严格修复。

商品背景：{background or '无额外背景'}
访谈目标：{goal}
必须满足的总题数：{num_questions}
阶段配额：
- basic：{stage_plan['basic']}
- core：{stage_plan['core']}
- attitude：{stage_plan['attitude']}
- reflection：{stage_plan['reflection']}
- closing：{stage_plan['closing']}

当前问题：
{error_message}

上一版输出：
{prev_text}

请重新输出一个严格合格的 JSON 数组。不要解释，不要输出 Markdown。"""


def _normalize_question_item(question: Dict[str, Any], idx: int) -> Dict[str, Any]:
    q = dict(question)
    q["id"] = idx + 1
    raw_stage = str(q.get("stage", "")).strip().lower()
    q["stage"] = STAGE_ALIASES.get(raw_stage, raw_stage)
    q["question"] = str(q.get("question", "")).strip()
    qtype = str(q.get("type", "text_input")).strip()
    qtype_lower = qtype.lower()
    q["type"] = "text_input"
    if qtype_lower in {"single_choice", "single-choice", "choice"}:
        q["type"] = "single_choice"
    elif qtype_lower in {"likert", "rating"}:
        q["type"] = "Likert"
    elif qtype_lower in {"text_input", "text", "open", "open_text"}:
        q["type"] = "text_input"
    if "options" in q and isinstance(q.get("options"), list):
        q["options"] = [str(x).strip() for x in q["options"] if str(x).strip()]
    if "scale" in q and isinstance(q.get("scale"), list):
        q["scale"] = [str(x).strip() for x in q["scale"] if str(x).strip()]
    return q


def _fallback_stage_questions(stage: str, count: int, product_name: str, goal: str) -> List[Dict[str, Any]]:
    templates = {
        "basic": [
            {
                "stage": "basic",
                "question": f"在今天之前，您对「{product_name}」的了解程度更接近哪一种？",
                "type": "single_choice",
                "options": ["已经看过/体验过", "听说过但不了解细节", "几乎不了解"],
            },
            {
                "stage": "basic",
                "question": f"您最早是通过哪些渠道知道「{product_name}」的？可以简单说说。",
                "type": "text_input",
            },
            {
                "stage": "basic",
                "question": f"您一般会在什么情况下主动关注像「{product_name}」这样的信息？",
                "type": "text_input",
            },
        ],
        "core": [
            {"stage": "core", "question": f"提到「{product_name}」，您第一反应最强的是哪一点？", "type": "text_input"},
            {"stage": "core", "question": f"如果只说一个亮点，您觉得「{product_name}」最吸引您的地方是什么？", "type": "text_input"},
            {"stage": "core", "question": f"从您的角度看，「{product_name}」有没有让您觉得不够满意或还可以提升的地方？", "type": "text_input"},
            {"stage": "core", "question": f"如果真要使用或接触「{product_name}」，您最在意的体验点会是什么？", "type": "text_input"},
            {"stage": "core", "question": f"和您平时更喜欢的同类产品相比，「{product_name}」的风格更像哪一类？", "type": "text_input"},
            {"stage": "core", "question": f"如果要向朋友快速描述「{product_name}」，您会怎么概括它？", "type": "text_input"},
        ],
        "attitude": [
            {
                "stage": "attitude",
                "question": f"整体来看，您对「{product_name}」的满意程度如何？",
                "type": "Likert",
                "scale": ["非常满意", "满意", "一般", "不满意", "非常不满意"],
            },
            {
                "stage": "attitude",
                "question": f"如果未来要做相关选择，您考虑「{product_name}」的意愿有多大？",
                "type": "Likert",
                "scale": ["非常可能", "可能", "不确定", "不太可能", "非常不可能"],
            },
            {
                "stage": "attitude",
                "question": f"如果让您现在立刻表态，您对「{product_name}」整体是更偏正面还是更保留？",
                "type": "Likert",
                "scale": ["非常正面", "比较正面", "一般", "比较保留", "非常保留"],
            },
            {
                "stage": "attitude",
                "question": f"就这次访谈目标“{goal}”来说，您目前对「{product_name}」的态度强度如何？",
                "type": "Likert",
                "scale": ["非常强", "比较强", "一般", "比较弱", "非常弱"],
            },
        ],
        "reflection": [
            {"stage": "reflection", "question": f"如果把「{product_name}」放到同类选择里比较，您觉得它最大的优势和不足分别是什么？", "type": "text_input"},
            {"stage": "reflection", "question": f"什么情况下会让您更愿意接受「{product_name}」？什么情况下反而会犹豫？", "type": "text_input"},
            {"stage": "reflection", "question": f"如果您能给「{product_name}」提一个最值得优先改进的建议，您会提什么？", "type": "text_input"},
            {"stage": "reflection", "question": f"从您的消费或内容偏好来看，「{product_name}」和您本人之间最大的匹配点或不匹配点是什么？", "type": "text_input"},
            {"stage": "reflection", "question": f"如果您身边的人来问您怎么看「{product_name}」，您通常会先提醒他们注意什么？", "type": "text_input"},
        ],
        "closing": [
            {
                "stage": "closing",
                "question": f"您会向朋友推荐「{product_name}」吗？",
                "type": "single_choice",
                "options": ["会", "看情况", "不会"],
            },
            {"stage": "closing", "question": f"关于「{product_name}」，您还有什么想补充但前面还没说到的吗？", "type": "text_input"},
        ],
    }
    base = templates.get(stage, [])
    return [dict(base[i % len(base)]) for i in range(count)] if base else []


def _coerce_generated_questions(
    questions: Any,
    num_questions: int,
    stage_plan: Dict[str, int],
    product_name: str,
    goal: str,
) -> List[Dict[str, Any]]:
    normalized = [_normalize_question_item(q, idx) for idx, q in enumerate(questions or []) if isinstance(q, dict)]
    kept_by_stage: Dict[str, List[Dict[str, Any]]] = {stage: [] for stage in stage_plan}

    for question in normalized:
        stage = question.get("stage")
        if stage not in stage_plan or not question.get("question"):
            continue
        if len(kept_by_stage[stage]) >= stage_plan[stage]:
            continue
        if question.get("type") == "single_choice" and not question.get("options"):
            continue
        if question.get("type") == "Likert" and not question.get("scale"):
            continue
        kept_by_stage[stage].append(question)

    for stage in STAGE_ORDER:
        if stage not in stage_plan:
            continue
        missing = stage_plan[stage] - len(kept_by_stage[stage])
        if missing <= 0:
            continue
        fillers = _fallback_stage_questions(stage, missing, product_name, goal)
        for filler in fillers:
            if len(kept_by_stage[stage]) < stage_plan[stage]:
                kept_by_stage[stage].append(filler)

    final_questions: List[Dict[str, Any]] = []
    for stage in STAGE_ORDER:
        if stage not in stage_plan:
            continue
        final_questions.extend(kept_by_stage[stage][:stage_plan[stage]])

    for idx, question in enumerate(final_questions):
        question["id"] = idx + 1

    return final_questions[:num_questions]


def _validate_generated_questions(
    questions: Any,
    num_questions: int,
    stage_plan: Dict[str, int],
) -> List[Dict[str, Any]]:
    if not isinstance(questions, list):
        raise ValueError("Expected JSON array")

    normalized = [_normalize_question_item(q, idx) for idx, q in enumerate(questions) if isinstance(q, dict)]
    if len(normalized) != num_questions:
        raise ValueError(f"题目总数不正确，期望 {num_questions}，实际 {len(normalized)}")

    stage_counts = {stage: 0 for stage in stage_plan}
    for question in normalized:
        stage = question.get("stage")
        if stage not in stage_plan:
            raise ValueError(f"存在非法 stage：{stage}")
        if not question.get("question"):
            raise ValueError("存在空题目文本")
        qtype = question.get("type", "text_input")
        if qtype == "single_choice" and not question.get("options"):
            raise ValueError(f"single_choice 缺少 options：{question.get('question')}")
        if qtype == "Likert" and not question.get("scale"):
            raise ValueError(f"Likert 缺少 scale：{question.get('question')}")
        stage_counts[stage] += 1

    mismatched = [f"{stage}={stage_counts[stage]}(期望{stage_plan[stage]})" for stage in stage_plan if stage_counts[stage] != stage_plan[stage]]
    if mismatched:
        raise ValueError("阶段题数不符合要求：" + "，".join(mismatched))

    return normalized


def _canonical_question_text(pair: Dict[str, Any]) -> str:
    return pair.get("source_question") or pair.get("question") or ""


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
    stage_plan    = _get_stage_plan(num_questions)

    if not product_name:
        return jsonify({"error": "product_name is required"}), 400

    try:
        client, model = make_llm_client(timeout=90)
        attempt_prompt = _build_questionnaire_prompt(product_name, background, goal, num_questions, stage_plan)
        previous_output: Any = None
        last_error = None

        for attempt_idx in range(2):
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": attempt_prompt}],
                temperature=0.35,
                max_tokens=2200,
            )
            raw = resp.choices[0].message.content.strip()
            parsed = _extract_json(raw)
            previous_output = parsed
            try:
                questions = _validate_generated_questions(parsed, num_questions, stage_plan)
                return jsonify({"questions": questions})
            except Exception as validation_error:
                logger.warning(f"问卷校验失败，attempt={attempt_idx + 1}, error={validation_error}")
                coerced = _coerce_generated_questions(
                    parsed,
                    num_questions,
                    stage_plan,
                    product_name,
                    goal,
                )
                try:
                    questions = _validate_generated_questions(coerced, num_questions, stage_plan)
                    return jsonify({"questions": questions})
                except Exception as coerce_error:
                    logger.warning(f"问卷服务端纠偏仍未通过，attempt={attempt_idx + 1}, error={coerce_error}")
                last_error = validation_error
                if attempt_idx == 1:
                    break
                attempt_prompt = _build_questionnaire_repair_prompt(
                    product_name,
                    background,
                    goal,
                    num_questions,
                    stage_plan,
                    previous_output,
                    str(validation_error),
                )

        final_questions = _coerce_generated_questions(
            previous_output,
            num_questions,
            stage_plan,
            product_name,
            goal,
        )
        questions = _validate_generated_questions(final_questions, num_questions, stage_plan)
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

    style_engine = InterviewEngine()
    agent_contexts: Dict[int, Dict] = {}
    for meta_agent in agent_ids:
        aid  = meta_agent.get("id")
        name = meta_agent.get("name", "")
        full_profile = profile_map.get(name, meta_agent)

        agent_ctx = {
            "meta":           meta_agent,
            "profile":        full_profile,
            "urban_summary":  extract_urban_summary(urban_steps, aid) if urban_steps else None,
            "online_summary": extract_online_summary(online_agents, aid) if online_agents else None,
        }
        agent_ctx["interviewer_style"] = style_engine.build_interviewer_style(agent_ctx)
        agent_contexts[aid] = agent_ctx

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
    return jsonify({
        "session_id": session_id,
        "agent_styles": {
            aid: (ctx.get("interviewer_style") or {}).get("name", "")
            for aid, ctx in agent_contexts.items()
        },
    })


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
            "interviewer_style": (ctx.get("interviewer_style") or {}).get("name", ""),
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
                ev = {
                    "type": "followup" if pair.get("is_followup") else "qa",
                    "question": pair["question"],
                    "source_question": pair.get("source_question"),
                    "question_style": pair.get("question_style"),
                    "stage": pair.get("stage"),
                    "answer": pair["answer"],
                }
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
        + "\n".join(f"  Q: {_canonical_question_text(p)}\n  A: {p['answer']}"
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
        client, model = make_llm_client(timeout=90)
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
        style_profile = ctx.get("interviewer_style") or engine.build_interviewer_style(ctx)

        def _last_answer_for_qid(qid: int) -> str:
            for pair in reversed(state.qa_pairs):
                if pair.get("id") == qid:
                    return pair.get("answer", "")
            return ""

        while not state.finished:
            stage    = engine.update_stage(state)
            next_qid = engine.select_next_qid(state, stage["stage_candidates"], "none")

            if next_qid is None:
                state.finished = True
                break

            state.current_qid = next_qid
            question = state.question_map[next_qid]
            source_q_text = question.get("question", "")
            q_type   = question.get("type", "text_input")
            q_opts   = question.get("options") or question.get("scale")

            if stage.get("recovery_mode"):
                recovery_seed = _last_answer_for_qid(next_qid)
                fu = engine.followup_gate(
                    state=state,
                    current_qid=next_qid,
                    answer=recovery_seed,
                    event="none",
                    question=question,
                    stage_name=stage["stage_name"],
                    style_profile=style_profile,
                    recovery_mode=True,
                )
                if not fu["do_followup"]:
                    if next_qid in state.deferred_ids:
                        state.deferred_ids.remove(next_qid)
                    continue

                fu_text = engine.render_asked_question(
                    question,
                    style_profile,
                    asked_index=state.used_turns,
                    is_followup=True,
                    followup_text=fu["followup_text"],
                )
                fu_context = engine.build_context(state)
                fu_answer  = responder.reply(fu_text, "text_input", None, fu_context)
                fu_event   = engine.detect_event(fu_answer)

                engine.record_answer(
                    state=state,
                    qid=fu["selected_qid"],
                    asked_question_text=fu_text,
                    source_question_text=source_q_text,
                    question_style=style_profile.get("name", ""),
                    answer=fu_answer,
                    event=fu_event,
                    stage_name=stage["stage_name"],
                    is_followup=True,
                )
                state.question_followup_done[next_qid] = True
                state.followup_turns_used += 1
                state.used_turns += 1
                state.followup_history.append({
                    **fu,
                    "answer": fu_answer,
                    "recovered": fu["selected_qid"] in state.answered_ids,
                })

                q.put({
                    "type": "followup",
                    "qid": fu["selected_qid"],
                    "stage": stage["stage_name"],
                    "question": fu_text,
                    "source_question": source_q_text,
                    "question_style": style_profile.get("name", ""),
                    "answer": fu_answer,
                })
                continue

            context = engine.build_context(state)
            asked_q_text = engine.render_asked_question(
                question,
                style_profile,
                asked_index=state.main_turns_used,
            )

            answer     = responder.reply(asked_q_text, q_type, q_opts, context)
            event_type = engine.detect_event(answer)
            engine.record_answer(
                state=state,
                qid=next_qid,
                asked_question_text=asked_q_text,
                source_question_text=source_q_text,
                question_style=style_profile.get("name", ""),
                answer=answer,
                event=event_type,
                stage_name=stage["stage_name"],
                is_followup=False,
            )
            state.main_turns_used += 1
            state.used_turns += 1

            q.put({
                "type": "qa",
                "qid": next_qid,
                "stage": stage["stage_name"],
                "question": asked_q_text,
                "source_question": source_q_text,
                "question_style": style_profile.get("name", ""),
                "answer": answer,
            })

            fu = engine.followup_gate(
                state=state,
                current_qid=next_qid,
                answer=answer,
                event=event_type,
                question=question,
                stage_name=stage["stage_name"],
                style_profile=style_profile,
                recovery_mode=False,
            )
            if fu["do_followup"]:
                fu_text = engine.render_asked_question(
                    question,
                    style_profile,
                    asked_index=state.used_turns,
                    is_followup=True,
                    followup_text=fu["followup_text"],
                )
                fu_context = engine.build_context(state)
                fu_answer  = responder.reply(fu_text, "text_input", None, fu_context)
                fu_event   = engine.detect_event(fu_answer)

                engine.record_answer(
                    state=state,
                    qid=fu["selected_qid"],
                    asked_question_text=fu_text,
                    source_question_text=source_q_text,
                    question_style=style_profile.get("name", ""),
                    answer=fu_answer,
                    event=fu_event,
                    stage_name=stage["stage_name"],
                    is_followup=True,
                )
                state.followup_history.append({
                    **fu,
                    "answer": fu_answer,
                    "recovered": fu["selected_qid"] in state.answered_ids,
                })
                state.question_followup_done[next_qid] = True
                state.followup_turns_used += 1
                state.used_turns += 1

                q.put({
                    "type": "followup",
                    "qid": fu["selected_qid"],
                    "stage": stage["stage_name"],
                    "question": fu_text,
                    "source_question": source_q_text,
                    "question_style": style_profile.get("name", ""),
                    "answer": fu_answer,
                })

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
