"""
仿真核心：SimulationState + 后台线程驱动的异步仿真循环

职责：
  1. 接收参数，加载学生画像，构建 Agent / MobilitySpace / SocialSpace
  2. 驱动 SimulationLoop 逐步推进
  3. 每步结束后：
       - 通过 event_queue 向 SSE 端点推送快照
       - 将快照追加写入 steps.jsonl
  4. 仿真完成 / 出错后更新 meta.json 状态
"""

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config  import (BACKEND_DIR, PROFILES_PATH, RESULTS_DIR,
                     FUDAN_AOI_IDS, DEFAULT_PARAMS,
                     FALLBACK_LNG, FALLBACK_LAT)
from storage import save_meta, open_steps_writer

# urban_sim 与本文件同级，直接可 import（无需 sys.path 操作）

logger = logging.getLogger(__name__)


# ── SimulationState ────────────────────────────────────────

class SimulationState:
    """
    单次仿真的运行时状态，全程存活于内存。

    Attributes:
        sim_id:        唯一 ID（时间戳 + uuid）
        params:        仿真参数字典
        status:        pending | initializing | running | completed | error
        event_queue:   线程安全队列，供 SSE 端点消费
        all_steps:     已完成步骤的快照列表（用于断线重连回放）
        current_step:  已完成的步数
        total_steps:   计划总步数
        error_msg:     出错时的错误信息
        start_time:    ISO 格式启动时间
        end_time:      ISO 格式结束时间（完成后填写）
    """

    def __init__(self, sim_id: str, params: dict):
        self.sim_id       = sim_id
        self.params       = params
        self.status       = "pending"
        self.event_queue: queue.Queue = queue.Queue()
        self.all_steps:   list[dict]  = []
        self.current_step = 0
        self.total_steps  = params.get("num_steps", DEFAULT_PARAMS["num_steps"])
        self.error_msg:   Optional[str] = None
        self.start_time   = datetime.now().isoformat()
        self.end_time:    Optional[str] = None

    def to_summary(self) -> dict:
        return {
            "sim_id":       self.sim_id,
            "status":       self.status,
            "start_time":   self.start_time,
            "end_time":     self.end_time,
            "total_steps":  self.total_steps,
            "current_step": self.current_step,
            "num_agents":   self.params.get("num_agents", 0),
            "tick_seconds": self.params.get("tick_seconds", 3600),
        }


# ── 公开入口 ───────────────────────────────────────────────

def launch_simulation(params: dict) -> SimulationState:
    """
    创建 SimulationState，在后台线程中启动仿真，立即返回 state。

    Args:
        params: 仿真参数，缺省值由 DEFAULT_PARAMS 补全。

    Returns:
        SimulationState 对象（status 初始为 "pending"）
    """
    # 补全默认值
    full_params = {**DEFAULT_PARAMS, **params}
    sim_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    state  = SimulationState(sim_id, full_params)

    t = threading.Thread(target=_run_thread, args=(state,), daemon=True)
    t.start()
    logger.info(f"[simulator] 仿真已启动 sim_id={sim_id}")
    return state


# ── 内部实现 ───────────────────────────────────────────────

def _run_thread(state: SimulationState) -> None:
    """在独立线程中运行 asyncio 事件循环。"""
    asyncio.run(_async_sim(state))


async def _async_sim(state: SimulationState) -> None:
    """
    完整的异步仿真流程：
      初始化 → 构建环境 → 运行步骤 → 保存结果 → 推送完成事件
    """
    sim_dir = RESULTS_DIR / state.sim_id
    sim_dir.mkdir(exist_ok=True)
    steps_file = None

    try:
        # ── 阶段1：参数解析 ──────────────────────────────
        state.status = "initializing"
        p = state.params

        num_agents    = min(int(p["num_agents"]),   37)
        num_steps     = int(p["num_steps"])
        tick_seconds  = int(p["tick_seconds"])
        concurrency   = int(p["concurrency"])
        start_time_s  = p["start_time"]
        start_t       = datetime.strptime(start_time_s, "%Y-%m-%d %H:%M:%S")
        map_file      = os.getenv("MAP_FILE_PATH", "")
        map_home      = os.getenv("MAP_HOME_DIR",  "")

        logger.info(f"[simulator] {state.sim_id}: {num_agents} agents, {num_steps} steps, "
                    f"tick={tick_seconds}s, concurrency={concurrency}")

        # ── 阶段2：加载画像 ──────────────────────────────
        if not PROFILES_PATH.exists():
            raise FileNotFoundError(
                f"学生画像文件不存在: {PROFILES_PATH}\n"
                "请先运行 offline_simulation/examples/build_student_profiles.py"
            )
        with open(PROFILES_PATH, encoding="utf-8") as f:
            all_profiles = json.load(f)

        # 优先按 agent_ids 精确选取，否则退回取前 num_agents 条
        agent_ids = p.get("agent_ids")
        if agent_ids:
            id_set = set(str(aid) for aid in agent_ids)
            id_order = {str(aid): i for i, aid in enumerate(agent_ids)}
            matched = [prof for prof in all_profiles if str(prof.get("user_id", "")) in id_set]
            matched.sort(key=lambda prof: id_order.get(str(prof.get("user_id", "")), 9999))
            profiles = matched[:num_agents]
        else:
            profiles = all_profiles[:num_agents]
        logger.info(f"[simulator] 加载画像 {len(profiles)} 个")

        # ── 阶段3：构建 Agent ────────────────────────────
        # 延迟导入：确保在后台线程中才拉入 urban_sim（避免 Flask 启动时提前加载）
        from urban_sim import PersonAgent, ReActRouter, SimulationLoop
        from urban_sim.agent import Needs
        from urban_sim.mobility_space import MobilitySpace, MobilityPersonInit
        from urban_sim.social_space import SimpleSocialSpace

        # 构建 id→name 名单，传给 PersonAgent 的 send_phase prompt
        agent_roster = {i + 1: prof["name"] for i, prof in enumerate(profiles[:num_agents])}
        agents, mobility_persons, agents_meta = _build_agents(profiles, num_agents, agent_roster)

        # 持久化 meta（running 前）
        meta = {
            "sim_id":          state.sim_id,
            "params":          p,
            "agents":          agents_meta,
            "start_time":      state.start_time,
            "status":          "initializing",
            "total_steps":     num_steps,
            "sampling_query":  p.get("sampling_query"),
            "sampling_spec":   p.get("sampling_spec"),
        }
        save_meta(state.sim_id, meta)

        # ── 阶段4：初始化环境 ────────────────────────────
        state.status = "running"
        meta["status"] = "running"
        save_meta(state.sim_id, meta)

        mobility = MobilitySpace(
            file_path=map_file, home_dir=map_home, persons=mobility_persons
        )
        social = SimpleSocialSpace(
            agent_id_name_pairs=[(a.id, a.name) for a in agents]
        )
        router = ReActRouter(env_modules=[mobility, social], max_steps=6)
        logger.info(f"[simulator] 环境初始化完成")

        # ── 阶段5：仿真主循环 ────────────────────────────
        steps_file = open_steps_writer(state.sim_id)

        async def on_step_end(sim: SimulationLoop, step: int, results: list[str]):
            """每步结束回调：采集快照 → 推送 SSE → 落盘"""
            step_agents = await _collect_step_agents(sim)
            ev = {
                "type":     "step",
                "step":     step,
                "sim_time": sim.current_time.strftime("%H:%M"),
                "sim_date": sim.current_time.strftime("%Y-%m-%d"),
                "agents":   step_agents,
                # 只保留公开消息（nearby / all），私信不出现在公共面板
                "channel_messages": [
                    {
                        "sender_id":   m.sender_id,
                        "sender_name": m.sender_name,
                        "content":     m.content,
                        "target":      m.target,
                    }
                    for m in sim.channel.all_messages
                    if m.target in ("nearby", "all")
                ],
            }
            state.all_steps.append(ev)
            state.current_step = step
            state.event_queue.put(ev)

            steps_file.write(json.dumps(ev, ensure_ascii=False) + "\n")
            steps_file.flush()
            logger.info(f"[simulator] step {step}/{num_steps} 完成，{len(step_agents)} agents")

        async with SimulationLoop(
            agents=agents,
            router=router,
            start_t=start_t,
            concurrency=concurrency,
            output_dir=str(sim_dir),
        ) as sim:
            await sim.run(
                num_steps=num_steps,
                tick=tick_seconds,
                on_step_end=on_step_end,
            )

        # ── 阶段6：完成 ──────────────────────────────────
        state.status   = "completed"
        state.end_time = datetime.now().isoformat()
        meta.update({"status": "completed", "end_time": state.end_time})
        save_meta(state.sim_id, meta)

        state.event_queue.put({"type": "complete", "total_steps": num_steps})
        logger.info(f"[simulator] {state.sim_id} 仿真完成")

    except Exception as exc:
        import traceback
        traceback.print_exc()
        state.status    = "error"
        state.error_msg = str(exc)
        logger.error(f"[simulator] {state.sim_id} 出错: {exc}")

        state.event_queue.put({"type": "error", "message": str(exc)})

        # 更新 meta
        try:
            from .storage import get_simulation_meta
            m = get_simulation_meta(state.sim_id) or {"sim_id": state.sim_id, "params": state.params}
            m.update({"status": "error", "error": str(exc)})
            save_meta(state.sim_id, m)
        except Exception:
            pass

    finally:
        if steps_file and not steps_file.closed:
            steps_file.close()
        state.event_queue.put(None)   # 哨兵：告知 SSE 流可以结束


# ── 辅助函数 ───────────────────────────────────────────────

def _build_agents(profiles: list[dict], num_agents: int,
                  agent_roster: dict[int, str] | None = None):
    """
    根据学生画像构建 PersonAgent 列表、MobilityPersonInit 列表和 agents_meta。

    Args:
        agent_roster: {agent_id: name} 映射，传给 PersonAgent 用于 send_phase prompt。

    Returns:
        (agents, mobility_persons, agents_meta)
    """
    from urban_sim import PersonAgent
    from urban_sim.agent import Needs
    from urban_sim.mobility_space import MobilityPersonInit

    agents, mobility_persons, agents_meta = [], [], []
    for i, prof in enumerate(profiles[:num_agents]):
        aid = i + 1
        aoi = FUDAN_AOI_IDS[i % len(FUDAN_AOI_IDS)]
        nd  = prof.get("initial_needs", {})

        agents.append(PersonAgent(
            id=aid, profile=prof, name=prof["name"],
            needs=Needs(
                satiety=nd.get("satiety", 0.70),
                energy =nd.get("energy",  0.80),
                safety =nd.get("safety",  0.90),
                social =nd.get("social",  0.55),
            ),
            agent_roster=agent_roster or {},
        ))
        mobility_persons.append(
            MobilityPersonInit(id=aid, position={"aoi_id": aoi})
        )
        agents_meta.append({
            "id":           aid,
            "name":         prof["name"],
            "mbti":         prof.get("mbti",       "?"),
            "gender":       prof.get("gender",     ""),
            "occupation":   prof.get("occupation", ""),
            "major":        prof.get("major",      ""),
            "interests":    prof.get("interests",  []),
            "initial_needs": nd,
            "start_aoi":    aoi,
        })

    return agents, mobility_persons, agents_meta


async def _collect_step_agents(sim) -> list[dict]:
    """
    从 SimulationLoop 中采集当步所有 PersonAgent 的快照。

    每条记录包含：
      id, name, position (lng/lat), needs, intention,
      reasoning, act_result, observation
    """
    from urban_sim import PersonAgent

    result = []
    for agent in sim.agents:
        if not isinstance(agent, PersonAgent):
            continue
        rec = agent.last_step_record
        if not rec:
            continue

        try:
            lng, lat = await sim.router.get_agent_position(agent.id)
        except Exception:
            lng, lat = FALLBACK_LNG, FALLBACK_LAT

        result.append({
            "id":          agent.id,
            "name":        agent.name,
            "position":    {"lng": lng, "lat": lat},
            "needs":       rec.get("needs",       {}),
            "intention":   rec.get("intention",   ""),
            "reasoning":   rec.get("reasoning",   ""),
            "act_result":  rec.get("act_result",  ""),
            "observation": rec.get("observation", ""),
            "sent":        rec.get("sent"),           # {target, content} | null
            "received":    rec.get("received", []),   # [{sender_id, sender_name, content, target_type}]
        })

    return result
