"""
backend/marketing/online_sim.py

Flask Blueprint — /api/online-sim/*

借鉴 mcp_server.py 的代码结构，将 OASIS 营销模拟封装为 REST API。

Endpoints
---------
POST /api/online-sim/start         启动仿真，返回 online_sim_id
GET  /api/online-sim/<id>/stream   SSE 实时推送日志 / step_done / complete
GET  /api/online-sim/<id>/posts    读取生成的帖子
GET  /api/online-sim/<id>/attitude 读取态度轨迹数据
"""

import asyncio
import csv
import json
import logging
import os
import queue
import re
import sqlite3
import sys
import threading
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, abort, jsonify, request, stream_with_context

# ── 路径常量（与 mcp_server.py 同结构）────────────────────────
APP_ROOT    = Path(__file__).resolve().parent          # backend/marketing/
SIM_SCRIPT  = APP_ROOT / "simulation" / "oasis_test_grouping.py"
DATA_ROOT   = APP_ROOT / "data"
LOG_DIR     = APP_ROOT / "simulation" / "log"
TMP_BASE    = Path("/tmp") / "online_sim"
RESULTS_DIR = APP_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

bp     = Blueprint("online_sim", __name__, url_prefix="/api/online-sim")
logger = logging.getLogger(__name__)

# 将 marketing/ 加入 sys.path，使 simulation.oasis_sim 可被 import
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── 直接复用 mcp_server.py 的 _read_env_file ─────────────────
def _read_env_file(path: Path) -> dict[str, str]:
    env_data: dict[str, str] = {}
    if not path.exists():
        return env_data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            env_data[key] = value
    return env_data


def _load_api_config() -> dict[str, str]:
    """依次尝试 data/.env → backend/.env，返回第一个有 API Key 的配置。"""
    for p in [DATA_ROOT / ".env", APP_ROOT.parent / ".env"]:
        cfg = _read_env_file(p)
        if cfg.get("MARS_MODEL_BASE_URL") or cfg.get("MARS_MODEL_API_KEY"):
            return cfg
    return {}


# ── 仿真状态 ──────────────────────────────────────────────────
class OnlineSimState:
    def __init__(self, sim_id: str, db_path: str, total_steps: int,
                 agent_map: dict[int, dict], topic: str = "", metric_key: str = ""):
        self.sim_id      = sim_id
        self.db_path     = db_path
        self.status      = "running"
        self.progress    = 0
        self.total_steps = total_steps
        self.log_queue: queue.Queue = queue.Queue()
        self.error_msg   = ""
        self.agent_map   = agent_map   # int_id → {name, username, group}
        self.topic       = topic
        self.metric_key  = metric_key


_active: dict[str, OnlineSimState] = {}


# ── 历史记录持久化 ─────────────────────────────────────────────

def _meta_path(sim_id: str) -> Path:
    return RESULTS_DIR / sim_id / "meta.json"


def _save_meta(state: OnlineSimState, end_time: str | None = None) -> None:
    """将仿真元数据写入 RESULTS_DIR/<sim_id>/meta.json。"""
    try:
        d = RESULTS_DIR / state.sim_id
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "sim_id":      state.sim_id,
            "topic":       state.topic,
            "metric_key":  state.metric_key,
            "total_steps": state.total_steps,
            "num_agents":  len(state.agent_map),
            "status":      state.status,
            "start_time":  getattr(state, "start_time", datetime.now().isoformat()),
            "end_time":    end_time,
            "agent_map":   {str(k): v for k, v in state.agent_map.items()},
            "db_path":     state.db_path,
        }
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"_save_meta failed for {state.sim_id}: {e}")


def _get_state(sim_id: str) -> OnlineSimState | None:
    """先查内存 _active，再尝试从磁盘 meta.json 恢复（用于历史记录查询）。"""
    if sim_id in _active:
        return _active[sim_id]
    p = _meta_path(sim_id)
    if not p.exists():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        agent_map = {int(k): v for k, v in meta.get("agent_map", {}).items()}
        # 优先用当前 RESULTS_DIR 推导路径，避免项目移动后绝对路径失效
        derived_db = str(RESULTS_DIR / meta["sim_id"] / "oasis.db")
        db_path = derived_db if os.path.exists(derived_db) else meta.get("db_path", derived_db)
        state = OnlineSimState(
            sim_id      = meta["sim_id"],
            db_path     = db_path,
            total_steps = meta.get("total_steps", 1),
            agent_map   = agent_map,
            topic       = meta.get("topic", ""),
            metric_key  = meta.get("metric_key", ""),
        )
        # 从磁盘恢复时，若状态仍为 running 说明进程中断，修正为 completed
        stored_status = meta.get("status", "completed")
        state.status = "completed" if stored_status == "running" else stored_status
        state.start_time = meta.get("start_time", "")  # type: ignore[attr-defined]
        return state
    except Exception as e:
        logger.warning(f"_get_state from disk failed for {sim_id}: {e}")
        return None


def get_session_agents(sim_id: str) -> list[dict[str, Any]]:
    """
    Return per-agent online simulation summaries mapped back to the original
    interview agent ids, so Step 4 can enrich persona prompts.
    """
    state = _get_state(sim_id)
    if not state:
        return []

    def _normalize_agent_id(value: Any) -> Any:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    summaries: dict[Any, dict[str, Any]] = {}
    for sim_agent_id, meta in state.agent_map.items():
        orig_id = _normalize_agent_id(meta.get("orig_id", sim_agent_id))
        summaries[orig_id] = {
            "agent_id": orig_id,
            "name": meta.get("name", ""),
            "group": meta.get("group", ""),
            "posts": [],
            "final_attitude": None,
        }

    if not os.path.exists(state.db_path):
        return list(summaries.values())

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(state.db_path)

        def _tbl_exists(name: str) -> bool:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone() is not None

        if _tbl_exists("post"):
            for user_id, content in conn.execute(
                "SELECT user_id, content FROM post WHERE content != '' ORDER BY post_id ASC LIMIT 500"
            ).fetchall():
                try:
                    sim_uid = int(user_id)
                except (TypeError, ValueError):
                    continue
                meta = state.agent_map.get(sim_uid, {})
                orig_id = _normalize_agent_id(meta.get("orig_id", sim_uid))
                item = summaries.setdefault(orig_id, {
                    "agent_id": orig_id,
                    "name": meta.get("name", ""),
                    "group": meta.get("group", ""),
                    "posts": [],
                    "final_attitude": None,
                })
                if content:
                    item["posts"].append({"content": str(content)})

        attitude_tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if row[0] == "log_attitude_average"
            or row[0].startswith("attitude_")
            or row[0].startswith("log_attitude_")
        ]
        for table in attitude_tables:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not {"agent_id", "attitude_score", "time_step"}.issubset(cols):
                continue
            rows = conn.execute(f"""
                SELECT agent_id, attitude_score
                FROM {table}
                WHERE time_step = (
                    SELECT MAX(time_step) FROM {table} inner_t
                    WHERE inner_t.agent_id = {table}.agent_id
                )
            """).fetchall()
            for agent_id, score in rows:
                try:
                    sim_uid = int(agent_id)
                except (TypeError, ValueError):
                    continue
                meta = state.agent_map.get(sim_uid, {})
                orig_id = _normalize_agent_id(meta.get("orig_id", sim_uid))
                item = summaries.setdefault(orig_id, {
                    "agent_id": orig_id,
                    "name": meta.get("name", ""),
                    "group": meta.get("group", ""),
                    "posts": [],
                    "final_attitude": None,
                })
                item["final_attitude"] = float(score) if score is not None else None
            break

    except Exception as exc:
        logger.warning(f"get_session_agents failed for {sim_id}: {exc}")
    finally:
        if conn:
            conn.close()

    return list(summaries.values())


# ── Agent CSV 生成 ────────────────────────────────────────────
def _make_agent_csv(agents: list[dict], path: Path, metric_key: str) -> dict[int, dict]:
    """
    将前端 oasisAgents 列表写成 OASIS 所需 CSV。
    返回 agent_map: {int_id → {name, username, group}}。

    OASIS agents_generator 要求：
      - 第一列为 agent_id（整数，作为 DataFrame index_col=0）
      - user_id 必须是整数（pd 会 astype(int)）
      - following_agentid_list 格式为 "[1001, 1002]"
    """
    att_col    = f"initial_{metric_key}"   # e.g. "initial_attitude_TNT"
    fieldnames = [
        "agent_id", "user_id", "username", "name", "bio",
        "description", "user_char", "group",
        "following_agentid_list", att_col,
        "initial_attitude_avg", "attitude_avg",
    ]

    # str-id → int-id 映射，用于 following 列表转换
    id_map = {
        a.get("agent_id", a.get("user_id", str(i))): 1001 + i
        for i, a in enumerate(agents)
    }
    agent_map: dict[int, dict] = {}

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, a in enumerate(agents):
            int_id  = 1001 + i
            orig_id = a.get("agent_id", a.get("user_id", str(i)))

            # 解析并重映射 following 列表
            raw_following = a.get("following_agentid_list", "[]")
            try:
                following_raw = json.loads(raw_following) if isinstance(raw_following, str) else raw_following
            except Exception:
                following_raw = []
            int_following = [id_map[fid] for fid in (following_raw or []) if fid in id_map]

            # 保留 ASCII username
            raw_uname = a.get("username", f"user{int_id}")
            username  = re.sub(r"[^a-zA-Z0-9_]", "", raw_uname) or f"user{int_id}"

            writer.writerow({
                "agent_id":               int_id,
                "user_id":                int_id,
                "username":               username,
                "name":                   a.get("name", f"User{int_id}"),
                "bio":                    a.get("bio", ""),
                "description":            a.get("description", a.get("bio", "")),
                "user_char":              a.get("user_char", ""),
                "group":                  a.get("group", "普通用户"),
                "following_agentid_list": str(int_following),
                att_col:                  0.0,
                "initial_attitude_avg":   0.0,
                "attitude_avg":           0.0,
            })
            agent_map[int_id] = {
                "name":     a.get("name", ""),
                "username": username,
                "group":    a.get("group", "普通用户"),
                "orig_id":  orig_id,
            }

    return agent_map


def _make_intervention_csv(interventions: list[dict], path: Path) -> None:
    fieldnames = [
        "time_step", "intervention_type", "content",
        "target_group", "target_id", "ratio",
        "attitude_target", "user_profile",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for iv in interventions:
            if not (iv.get("content") or "").strip():
                continue
            writer.writerow({
                "time_step":         int(iv.get("step", 1)),
                "intervention_type": iv.get("type", "broadcast"),
                "content":           iv.get("content", ""),
                "target_group":      iv.get("target_group", ""),
                "target_id":         "",
                "ratio":             float(iv.get("ratio", 1.0)),
                "attitude_target":   "{}",
                "user_profile":      "{}",
            })


# ── 后台仿真线程（直接调用 oasis_sim.run_simulation，无子进程）───
def _run_simulation(state: OnlineSimState, tmp_dir: Path, body: dict) -> None:
    topic      = body.get("topic", "topic")
    topic_key  = re.sub(r"[^a-zA-Z0-9]", "_", topic)[:24].strip("_") or "topic"
    metric_key = f"attitude_{topic_key}"

    api_cfg = _load_api_config()
    q       = state.log_queue

    def _on_progress(step: int, total: int):
        state.progress = step
        q.put({"type": "step_done", "step": step})

    def _on_log(msg: str):
        q.put({"type": "log", "message": msg})

    try:
        from simulation.oasis_sim import run_simulation
        asyncio.run(run_simulation(
            profile_path      = str(tmp_dir / "agents.csv"),
            db_path           = str(tmp_dir / "oasis.db"),
            intervention_path = str(tmp_dir / "interventions.csv"),
            total_steps       = int(body.get("total_steps", 4)),
            model_name        = api_cfg.get("MARS_MODEL_NAME", "deepseek-chat"),
            model_base_url    = api_cfg.get("MARS_MODEL_BASE_URL", ""),
            model_api_key     = api_cfg.get("MARS_MODEL_API_KEY", ""),
            attitude_config   = {metric_key: f"Evaluate the user's sentiment towards {topic}."},
            agent_map         = state.agent_map,
            progress_callback = _on_progress,
            log_callback      = _on_log,
        ))
        state.status = "completed"
        _save_meta(state, end_time=datetime.now().isoformat())
        q.put({"type": "complete"})

    except Exception as exc:
        state.status    = "error"
        state.error_msg = str(exc)
        _save_meta(state, end_time=datetime.now().isoformat())
        q.put({"type": "error", "message": state.error_msg})
        logger.exception(f"Simulation {state.sim_id} crashed")

    finally:
        q.put(None)   # SSE sentinel


# ── Routes ────────────────────────────────────────────────────

@bp.route("/start", methods=["POST"])
def start():
    body   = request.get_json() or {}
    agents = body.get("agents", [])
    if not agents:
        return jsonify({"error": "No agents provided"}), 400

    topic      = body.get("topic", "topic")
    topic_key  = re.sub(r"[^a-zA-Z0-9]", "_", topic)[:24].strip("_") or "topic"
    metric_key = f"attitude_{topic_key}"

    sim_id   = f"osim_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    # 使用持久结果目录（替代 /tmp），保证历史记录不丢失
    res_dir  = RESULTS_DIR / sim_id
    res_dir.mkdir(parents=True, exist_ok=True)

    agent_map = _make_agent_csv(agents, res_dir / "agents.csv", metric_key)
    _make_intervention_csv(body.get("interventions", []), res_dir / "interventions.csv")

    total_steps = int(body.get("total_steps", 4))
    state = OnlineSimState(sim_id, str(res_dir / "oasis.db"), total_steps, agent_map,
                           topic=topic, metric_key=metric_key)
    state.start_time = datetime.now().isoformat()  # type: ignore[attr-defined]
    _active[sim_id] = state
    _save_meta(state)   # 立即落盘（status=running）

    threading.Thread(
        target=_run_simulation,
        args=(state, res_dir, body),
        daemon=True,
    ).start()

    logger.info(f"Online sim {sim_id} started — {len(agents)} agents, topic={topic!r}")
    return jsonify({"online_sim_id": sim_id, "status": "running"})


@bp.route("/<sim_id>/stream")
def stream(sim_id):
    state = _active.get(sim_id)
    if state is None:
        abort(404, "Simulation not found")

    @stream_with_context
    def generate():
        while True:
            ev = state.log_queue.get()
            if ev is None:
                break
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


@bp.route("/history")
def get_history():
    """返回所有历史仿真记录（按时间倒序）。"""
    records = []
    if RESULTS_DIR.exists():
        for d in sorted(RESULTS_DIR.iterdir(), reverse=True):
            mp = d / "meta.json"
            if not mp.exists():
                continue
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
                records.append({
                    "sim_id":      meta["sim_id"],
                    "topic":       meta.get("topic", ""),
                    "total_steps": meta.get("total_steps", 0),
                    "num_agents":  meta.get("num_agents", 0),
                    "status":      meta.get("status", "unknown"),
                    "start_time":  meta.get("start_time", ""),
                    "end_time":    meta.get("end_time", ""),
                })
            except Exception:
                pass
    return jsonify(records)


@bp.route("/<sim_id>/posts")
def get_posts(sim_id):
    state = _get_state(sim_id)
    if not state:
        abort(404, "Simulation not found")
    if not os.path.exists(state.db_path):
        return jsonify({"posts": []})

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(state.db_path)
        conn.row_factory = sqlite3.Row

        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='post'"
        ).fetchone():
            return jsonify({"posts": []})

        user_exists    = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user'").fetchone()
        comment_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='comment'").fetchone()

        cmt_col = "(SELECT COUNT(*) FROM comment c WHERE c.post_id = p.post_id) AS comment_count" \
                  if comment_exists else "0 AS comment_count"

        if user_exists:
            try:
                rows = conn.execute(f"""
                    SELECT p.post_id, p.user_id, p.content, p.created_at,
                           p.num_likes, p.num_dislikes, p.num_shares, p.num_reports,
                           p.quote_content, p.original_post_id,
                           u.name, u.user_name AS username,
                           {cmt_col}
                    FROM post p
                    LEFT JOIN user u ON CAST(p.user_id AS TEXT) = CAST(u.user_id AS TEXT)
                    WHERE p.content != ''
                    ORDER BY p.post_id ASC
                    LIMIT 300
                """).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute("""
                    SELECT post_id, user_id, content, created_at,
                           0,0,0,0, NULL, NULL, NULL, NULL, 0
                    FROM post WHERE content != '' ORDER BY post_id ASC LIMIT 300
                """).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT p.post_id, p.user_id, p.content, p.created_at,
                       p.num_likes, p.num_dislikes, p.num_shares, p.num_reports,
                       p.quote_content, p.original_post_id,
                       NULL AS name, NULL AS username,
                       {cmt_col}
                FROM post p WHERE p.content != '' ORDER BY p.post_id ASC LIMIT 300
            """).fetchall()

        total       = len(rows)
        total_steps = state.total_steps
        result      = []
        for i, r in enumerate(rows):
            item = dict(r)
            item["step"] = max(1, min(total_steps, int(i * total_steps / max(total, 1)) + 1))
            try:
                uid = int(item.get("user_id") or 0)
            except (ValueError, TypeError):
                uid = 0
            info = state.agent_map.get(uid, {})
            if not item.get("name"):
                item["name"] = info.get("name") or str(uid)
            if not item.get("username"):
                item["username"] = info.get("username") or str(uid)
            item["group"] = info.get("group", "")
            result.append(item)

        return jsonify({"posts": result})

    except Exception as exc:
        logger.error(f"get_posts error for {sim_id}: {exc}")
        return jsonify({"posts": [], "error": str(exc)})
    finally:
        if conn:
            conn.close()


@bp.route("/<sim_id>/stats")
def get_stats(sim_id):
    state = _get_state(sim_id)
    if not state or not os.path.exists(state.db_path):
        return jsonify(None)

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(state.db_path)

        def tbl_exists(name):
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone() is not None

        if not tbl_exists("post"):
            return jsonify(None)

        total_posts = conn.execute(
            "SELECT COUNT(*) FROM post WHERE content != ''"
        ).fetchone()[0]
        total_likes = int(conn.execute(
            "SELECT COALESCE(SUM(num_likes),0) FROM post WHERE content != ''"
        ).fetchone()[0])
        total_shares = int(conn.execute(
            "SELECT COALESCE(SUM(num_shares),0) FROM post WHERE content != ''"
        ).fetchone()[0])

        total_comments = 0
        if tbl_exists("comment"):
            total_comments = conn.execute("SELECT COUNT(*) FROM comment").fetchone()[0]

        action_types: dict[str, int] = {}
        if tbl_exists("trace"):
            for action, cnt in conn.execute(
                "SELECT action, COUNT(*) FROM trace GROUP BY action ORDER BY COUNT(*) DESC"
            ).fetchall():
                action_types[action] = cnt
        total_actions = sum(action_types.values())

        # Posts per group (via agent_map)
        group_agg: dict[str, dict] = defaultdict(lambda: {"posts": 0, "likes": 0, "shares": 0})
        for user_id, cnt, lk, sh in conn.execute(
            "SELECT user_id, COUNT(*), COALESCE(SUM(num_likes),0), COALESCE(SUM(num_shares),0)"
            " FROM post WHERE content != '' GROUP BY user_id"
        ).fetchall():
            try:
                uid = int(user_id)
            except (ValueError, TypeError):
                uid = 0
            grp = state.agent_map.get(uid, {}).get("group", "其他")
            group_agg[grp]["posts"]  += cnt
            group_agg[grp]["likes"]  += int(lk)
            group_agg[grp]["shares"] += int(sh)
        by_group = sorted(
            [{"group": g, **v} for g, v in group_agg.items()],
            key=lambda x: x["posts"], reverse=True,
        )

        # Top 5 posts by (likes + shares)
        top_posts = []
        if tbl_exists("user"):
            try:
                top_rows = conn.execute("""
                    SELECT p.post_id, p.user_id, p.content,
                           p.num_likes, p.num_dislikes, p.num_shares,
                           u.name, u.user_name
                    FROM post p
                    LEFT JOIN user u ON CAST(p.user_id AS TEXT) = CAST(u.user_id AS TEXT)
                    WHERE p.content != ''
                    ORDER BY (p.num_likes + p.num_shares) DESC LIMIT 5
                """).fetchall()
            except sqlite3.OperationalError:
                top_rows = conn.execute(
                    "SELECT post_id, user_id, content, num_likes, num_dislikes, num_shares, NULL, NULL"
                    " FROM post WHERE content != '' ORDER BY (num_likes+num_shares) DESC LIMIT 5"
                ).fetchall()
        else:
            top_rows = conn.execute(
                "SELECT post_id, user_id, content, num_likes, num_dislikes, num_shares, NULL, NULL"
                " FROM post WHERE content != '' ORDER BY (num_likes+num_shares) DESC LIMIT 5"
            ).fetchall()

        for r in top_rows:
            post_id, user_id, content, n_likes, n_dislikes, n_shares, name, uname = r
            try:
                uid = int(user_id)
            except (ValueError, TypeError):
                uid = 0
            info = state.agent_map.get(uid, {})
            cmt_count = 0
            if tbl_exists("comment"):
                try:
                    cmt_count = conn.execute(
                        "SELECT COUNT(*) FROM comment WHERE post_id=?", (post_id,)
                    ).fetchone()[0]
                except Exception:
                    pass
            top_posts.append({
                "post_id":       post_id,
                "content":       content,
                "name":          name or info.get("name") or str(user_id),
                "group":         info.get("group", ""),
                "num_likes":     int(n_likes or 0),
                "num_dislikes":  int(n_dislikes or 0),
                "num_shares":    int(n_shares or 0),
                "comment_count": cmt_count,
            })

        return jsonify({
            "total_posts":    total_posts,
            "total_likes":    total_likes,
            "total_shares":   total_shares,
            "total_comments": total_comments,
            "total_actions":  total_actions,
            "action_types":   action_types,
            "by_group":       by_group,
            "top_posts":      top_posts,
        })

    except Exception as exc:
        logger.error(f"get_stats error for {sim_id}: {exc}")
        return jsonify(None)
    finally:
        if conn:
            conn.close()


@bp.route("/<sim_id>/attitude")
def get_attitude(sim_id):
    state = _get_state(sim_id)
    if not state or not os.path.exists(state.db_path):
        return jsonify(None)

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(state.db_path)

        def _tbl_exists(name: str) -> bool:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone() is not None

        # 优先读取标注聚合表（改进二生成）
        if _tbl_exists("attitude_step_group"):
            rows = conn.execute(
                "SELECT time_step, group_name, avg_score FROM attitude_step_group ORDER BY time_step"
            ).fetchall()
            if rows:
                gd: dict[str, dict] = defaultdict(dict)
                ss: set[int] = set()
                for ts, grp, avg in rows:
                    gd[grp][int(ts)] = round(float(avg or 0), 3)
                    ss.add(int(ts))
                if ss:
                    steps = sorted(ss)
                    return jsonify({
                        "steps":  steps,
                        "groups": {g: [v.get(s) for s in steps] for g, v in gd.items()},
                    })

        # 发现原始态度日志表（fallback）
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if r[0] == "log_attitude_average"
            or r[0].startswith("attitude_")
            or r[0].startswith("log_attitude_")
        ]

        if not tables:
            return jsonify(None)

        tbl  = tables[0]
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}

        # 读取所有行，含 agent_id 以便从 agent_map 获取群体名
        if "agent_id" in cols and "attitude_score" in cols and "time_step" in cols:
            rows = conn.execute(f"""
                SELECT time_step, agent_id, attitude_score
                FROM {tbl}
                WHERE time_step > 0
                ORDER BY time_step
            """).fetchall()
        elif "group_name" in cols and "avg_attitude" in cols:
            rows_raw = conn.execute(f"""
                SELECT time_step, group_name AS grp, avg_attitude AS avg_att
                FROM {tbl} WHERE time_step > 0 ORDER BY time_step
            """).fetchall()
            if not rows_raw:
                return jsonify(None)
            group_data: dict[str, dict] = defaultdict(dict)
            steps_set: set[int] = set()
            for ts, grp, avg_att in rows_raw:
                group_data[grp][int(ts)] = round(float(avg_att or 0), 3)
                steps_set.add(int(ts))
            steps = sorted(steps_set)
            return jsonify({"steps": steps,
                            "groups": {g: [v.get(s) for s in steps] for g, v in group_data.items()}})
        else:
            return jsonify(None)

        if not rows:
            return jsonify(None)

        # 按 agent_map 群体汇总（每步骤每群体取平均）
        from collections import defaultdict as _dd
        step_group_scores: dict[int, dict[str, list]] = _dd(lambda: _dd(list))
        for time_step, agent_id, attitude_score in rows:
            grp = state.agent_map.get(int(agent_id) if agent_id else 0, {}).get("group", "其他")
            step_group_scores[int(time_step)][grp].append(float(attitude_score or 0))

        steps = sorted(step_group_scores.keys())
        all_groups: set[str] = set()
        for g_map in step_group_scores.values():
            all_groups.update(g_map.keys())

        groups: dict[str, list] = {}
        for grp in sorted(all_groups):
            groups[grp] = [
                round(sum(step_group_scores[s].get(grp, [0])) /
                      max(len(step_group_scores[s].get(grp, [1])), 1), 3)
                for s in steps
            ]

        return jsonify({"steps": steps, "groups": groups})

    except Exception as exc:
        logger.error(f"get_attitude error for {sim_id}: {exc}")
        return jsonify(None)
    finally:
        if conn:
            conn.close()


# ── Attitude 解读 ──────────────────────────────────────────────

def _load_attitude_data(state: OnlineSimState) -> dict | None:
    """从 DB 读取态度数据，优先 attitude_step_group 表。"""
    if not os.path.exists(state.db_path):
        return None
    conn = sqlite3.connect(state.db_path)
    try:
        def tbl(n):
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (n,)
            ).fetchone() is not None

        if tbl("attitude_step_group"):
            rows = conn.execute(
                "SELECT time_step, group_name, avg_score FROM attitude_step_group ORDER BY time_step"
            ).fetchall()
            if rows:
                gd: dict[str, dict] = defaultdict(dict)
                ss: set[int] = set()
                for ts, grp, avg in rows:
                    gd[grp][int(ts)] = round(float(avg or 0), 3)
                    ss.add(int(ts))
                if ss:
                    steps = sorted(ss)
                    return {"steps": steps,
                            "groups": {g: [v.get(s) for s in steps] for g, v in gd.items()}}
        return None
    finally:
        conn.close()


def _build_interpret_prompt(topic: str, attitude: dict, total_steps: int) -> str:
    steps  = attitude.get("steps", [])
    groups = attitude.get("groups", {})

    # 格式化为 Markdown 表格
    header = "步骤 | " + " | ".join(groups.keys())
    sep    = "--- | " + " | ".join("---" for _ in groups)
    body_rows = []
    for i, s in enumerate(steps):
        row = [str(s)]
        for vals in groups.values():
            v = vals[i] if i < len(vals) else None
            row.append(f"{v:+.2f}" if v is not None else "N/A")
        body_rows.append(" | ".join(row))
    table_str = "\n".join([header, sep] + body_rows)

    return f"""你是一名社会媒体研究员，正在分析一场针对主题「{topic}」的舆情仿真结果。

模拟共进行 {total_steps} 步，各群体的态度变化（-1=极度负面，0=中立，+1=极度正面）如下：

{table_str}

请从以下角度解读这条曲线：
1. **整体趋势**：舆论是向正面还是负面演化？
2. **群体差异**：哪个群体最支持 / 最抗拒？
3. **转折点**：哪一步出现明显变化？可能原因是什么？
4. **结论与建议**：此次仿真对真实营销活动有何启示？

请用简洁、专业的中文回答，150-300字。"""


def _call_llm_sync(prompt: str, model: str, api_key: str, base_url: str,
                   timeout: int = 60) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=800,
    )
    return resp.choices[0].message.content.strip()


@bp.route("/<sim_id>/attitude/interpret", methods=["POST"])
def interpret_attitude(sim_id):
    state = _get_state(sim_id)
    if not state:
        abort(404, "Simulation not found")

    attitude = _load_attitude_data(state)
    if not attitude:
        return jsonify({"error": "No attitude data available — run simulation first"}), 400

    api_cfg = _load_api_config()
    prompt  = _build_interpret_prompt(state.topic, attitude, state.total_steps)

    try:
        text = _call_llm_sync(
            prompt   = prompt,
            model    = api_cfg.get("MARS_MODEL_NAME", "deepseek-chat"),
            api_key  = api_cfg.get("MARS_MODEL_API_KEY", ""),
            base_url = api_cfg.get("MARS_MODEL_BASE_URL", ""),
        )
        return jsonify({"interpretation": text, "topic": state.topic})
    except Exception as exc:
        logger.error(f"interpret_attitude error for {sim_id}: {exc}")
        return jsonify({"error": str(exc)}), 500
