"""
IndividualSim — Backend API 服务器

纯 JSON API，不服务任何 HTML。
前端独立部署，通过 CORS 跨域请求此服务。

启动：
    cd backend
    python app.py
    # 或生产环境：gunicorn app:app -b 0.0.0.0:5050 -w 1 --threads 4
"""

import json
import os
import queue
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request, stream_with_context
from flask_cors import CORS

# ── 加载环境变量（LLM Key、地图路径等）────────────────────
load_dotenv(Path(__file__).parent / ".env")

# ── 创建应用 ─────────────────────────────────────────────
app = Flask(__name__)
app.json.ensure_ascii = False
CORS(app)   # 允许所有来源跨域（前端本地 http://localhost:3000 → 后端 :5050）

# ── 导入后端业务模块 ──────────────────────────────────────
from simulator import SimulationState, launch_simulation
from storage   import (list_simulations, get_simulation_meta,
                       get_simulation_steps)
from config    import PROFILES_PATH, RELATIONSHIPS_PATH
from marketing.online_sim import bp as online_sim_bp
from interview import bp as interview_bp

app.register_blueprint(online_sim_bp)
app.register_blueprint(interview_bp)

# 活跃仿真（内存，进程重启后清空）
_active: dict[str, SimulationState] = {}


# ══════════════════════════════════════════════════════════
#  API — 学生画像
# ══════════════════════════════════════════════════════════

@app.route("/api/profiles")
def api_profiles():
    """返回可用学生画像列表。"""
    if not PROFILES_PATH.exists():
        return jsonify([])
    profiles = json.loads(PROFILES_PATH.read_text("utf-8"))
    return jsonify([
        {
            "user_id":    p["user_id"],
            "name":       p["name"],
            "mbti":       p.get("mbti",       ""),
            "gender":     p.get("gender",     "unknown"),
            "age":        p.get("age",        0),
            "occupation": p.get("occupation", ""),
            "major":      p.get("major",      ""),
            "interests":  p.get("interests",  []),
        }
        for p in profiles
    ])


@app.route("/api/relationships")
def api_relationships():
    """返回预生成的智能体关系网络。"""
    if not RELATIONSHIPS_PATH.exists():
        return jsonify({"relationships": []})
    return jsonify(json.loads(RELATIONSHIPS_PATH.read_text("utf-8")))


# ══════════════════════════════════════════════════════════
#  API — 仿真管理
# ══════════════════════════════════════════════════════════

@app.route("/api/simulations", methods=["POST"])
def api_create_simulation():
    """
    启动新仿真。

    请求体（JSON，均有默认值）：
        num_agents    int   10
        num_steps     int   12
        tick_seconds  int   3600
        concurrency   int   5
        start_time    str   "2024-09-02 08:00:00"

    返回：
        {"sim_id": "...", "status": "pending"}
    """
    params = request.get_json() or {}
    state  = launch_simulation(params)
    _active[state.sim_id] = state
    return jsonify({"sim_id": state.sim_id, "status": state.status})


@app.route("/api/simulations", methods=["GET"])
def api_list_simulations():
    """返回所有历史仿真的摘要列表（倒序）。"""
    rows = list_simulations()
    for row in rows:
        live = _active.get(row["sim_id"])
        if live:
            row["status"]       = live.status
            row["current_step"] = live.current_step
    return jsonify(rows)


@app.route("/api/simulations/<sim_id>", methods=["GET"])
def api_get_simulation(sim_id):
    """返回单次仿真的完整元信息（含 agents 列表）。"""
    meta = get_simulation_meta(sim_id)
    if meta is None:
        abort(404)
    live = _active.get(sim_id)
    if live:
        meta["status"]       = live.status
        meta["current_step"] = live.current_step
    return jsonify(meta)


@app.route("/api/simulations/<sim_id>/steps", methods=["GET"])
def api_get_steps(sim_id):
    """
    返回某次仿真的所有步骤快照。
    - 仿真进行中：从内存读取（最新）
    - 仿真完成后：从 steps.jsonl 读取
    """
    live = _active.get(sim_id)
    if live:
        return jsonify(live.all_steps)
    steps = get_simulation_steps(sim_id)
    if steps is None:
        abort(404)
    return jsonify(steps)


# ══════════════════════════════════════════════════════════
#  API — SSE 实时流
# ══════════════════════════════════════════════════════════

@app.route("/api/simulations/<sim_id>/stream")
def api_stream(sim_id):
    """
    Server-Sent Events 端点，向前端推送仿真实时进度。

    事件类型：
      step       {"type":"step", "step":N, "sim_time":"HH:MM", "agents":[...]}
      complete   {"type":"complete", "total_steps":N}
      error      {"type":"error", "message":"..."}
      heartbeat  {"type":"heartbeat"}   （每 25s 一次保活）

    断线重连时会先回放已完成步骤，再监听新事件。
    """
    state = _active.get(sim_id)
    if state is None:
        abort(404, "仿真不存在或进程已重启（内存状态丢失）")

    @stream_with_context
    def generate():
        sent: set[int] = set()

        # 1. 回放已累积步骤（断线重连场景）
        for ev in list(state.all_steps):
            n = ev.get("step")
            if n not in sent:
                sent.add(n)
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

        # 2. 若已结束，推送最终事件后退出
        if state.status in ("completed", "error"):
            final = {
                "type":        "complete" if state.status == "completed" else "error",
                "total_steps": state.current_step,
                "message":     state.error_msg or "",
            }
            yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
            return

        # 3. 持续监听新事件
        while True:
            try:
                ev = state.event_queue.get(timeout=25)
            except queue.Empty:
                yield 'data: {"type":"heartbeat"}\n\n'
                continue

            if ev is None:      # 哨兵：仿真线程结束
                break

            if ev.get("type") == "step":
                n = ev.get("step")
                if n in sent:
                    continue
                sent.add(n)

            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


# ══════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
