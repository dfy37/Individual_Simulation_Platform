"""
SimulationLoop：多 Agent 三阶段仿真循环

每步（step）按顺序执行三个子阶段，所有 agent 在各阶段内并行：

  Phase 1: Move     → 所有 agent 并行：Observe + Decide移动 + Act移动
  Phase 2: Send     → 所有 agent 并行：LLM 决策发消息 → 写入 MessageChannel
  Phase 3: Receive  → 所有 agent 并行：从 Channel 读取消息 → 更新记忆（无 LLM）

Send 和 Receive 在同一步内完成，消息无需等到下一步才能被读取。
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import tqdm as tqdm_module

from .agent import AgentBase, PersonAgent
from .channel import MessageChannel
from .router import ReActRouter

__all__ = ["SimulationLoop"]

logger = logging.getLogger(__name__)


class SimulationLoop:
    """
    管理多个 Agent 的三阶段并行仿真循环。

    用法：
        channel = MessageChannel()
        async with SimulationLoop(agents, router, start_t, channel, concurrency=5) as sim:
            await sim.run(num_steps=48, tick=3600)
    """

    def __init__(
        self,
        agents:      list[AgentBase],
        router:      ReActRouter,
        start_t:     datetime,
        channel:     Optional[MessageChannel] = None,
        concurrency: int = 5,
        output_dir:  Optional[str] = None,
    ):
        """
        Args:
            agents:      Agent 列表。
            router:      环境路由器。
            start_t:     仿真起始时间。
            channel:     消息总线实例，不传则自动创建。
            concurrency: 同时运行的 Agent 数量上限（LLM 并发保护）。
            output_dir:  结果输出目录，None 则不保存文件。
        """
        self._agents      = agents
        self._router      = router
        self._t           = start_t
        self._channel     = channel or MessageChannel()
        self._concurrency = concurrency
        self._step_count  = 0
        self._semaphore   = asyncio.Semaphore(concurrency)
        self._output_dir  = output_dir
        self._all_records: list[dict] = []

    # ── 属性 ──────────────────────────────────────────────────

    @property
    def current_time(self) -> datetime:
        return self._t

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def agents(self) -> list[AgentBase]:
        return self._agents

    @property
    def router(self) -> ReActRouter:
        return self._router

    @property
    def channel(self) -> MessageChannel:
        return self._channel

    # ── 生命周期 ──────────────────────────────────────────────

    async def init(self) -> None:
        await self._router.init(self._t)
        for agent in self._agents:
            await agent.init(self._router)
        logger.info(
            f"SimulationLoop 初始化完成。"
            f"Agent 数: {len(self._agents)}，并发数: {self._concurrency}"
        )

    async def close(self) -> None:
        for agent in self._agents:
            await agent.close()
        await self._router.close()
        logger.info("SimulationLoop 已关闭。")

    async def __aenter__(self) -> "SimulationLoop":
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ── 三阶段步进 ────────────────────────────────────────────

    async def step(self, tick: int) -> list[str]:
        """
        推进一步，按序执行三个子阶段。

        Args:
            tick: 本步时长（秒）。

        Returns:
            每个 Agent 本步 move_phase 的行动摘要列表。
        """
        self._t          += timedelta(seconds=tick)
        self._step_count += 1
        self._channel.flush(self._step_count)

        time_str = self._t.strftime("%H:%M")
        logger.info(
            f"━━ Step {self._step_count} | {time_str} | "
            f"{len(self._agents)} agents ━━"
        )

        # ── Phase 1: Move（并行，LLM 密集，受 Semaphore 限制）─
        move_bar = tqdm_module.tqdm(
            total=len(self._agents),
            desc=f"  Step {self._step_count} [1/3 Move]",
            leave=False, ncols=80,
        )
        move_results: list[str] = list(
            await asyncio.gather(*[
                self._run_move(a, tick, move_bar) for a in self._agents
            ])
        )
        move_bar.close()

        # ── Phase 2: Send（并行，一次轻量 LLM，受 Semaphore 限制）─
        send_bar = tqdm_module.tqdm(
            total=len(self._agents),
            desc=f"  Step {self._step_count} [2/3 Send]",
            leave=False, ncols=80,
        )
        await asyncio.gather(*[
            self._run_send(a, send_bar) for a in self._agents
        ])
        send_bar.close()

        logger.info(
            f"  Channel: {self._channel.message_count} 条消息"
            + (
                f" → " + ", ".join(
                    f"{m.sender_name}→{m.target}"
                    for m in self._channel.all_messages
                )
                if self._channel.message_count else ""
            )
        )

        # ── Phase 3: Receive（并行，纯规则，无 LLM，不受 Semaphore 限制）─
        recv_bar = tqdm_module.tqdm(
            total=len(self._agents),
            desc=f"  Step {self._step_count} [3/3 Recv]",
            leave=False, ncols=80,
        )
        await asyncio.gather(*[
            self._run_receive(a, recv_bar) for a in self._agents
        ])
        recv_bar.close()

        # ── 环境物理更新 ──────────────────────────────────────
        await self._router.step(tick, self._t)

        return move_results

    # ── 各阶段的 wrapper（含错误处理）───────────────────────

    async def _run_move(
        self, agent: AgentBase, tick: int, bar: tqdm_module.tqdm
    ) -> str:
        async with self._semaphore:
            try:
                result = await agent.move_phase(tick, self._t)
            except Exception as e:
                logger.error(f"Agent {agent.name} move_phase 出错: {e}")
                result = f"[{agent.name}] ERROR: {e}"
        bar.update(1)
        return result

    async def _run_send(
        self, agent: AgentBase, bar: tqdm_module.tqdm
    ) -> None:
        async with self._semaphore:
            try:
                await agent.send_phase(self._t, self._channel)
            except Exception as e:
                logger.error(f"Agent {agent.name} send_phase 出错: {e}")
        bar.update(1)

    async def _run_receive(
        self, agent: AgentBase, bar: tqdm_module.tqdm
    ) -> None:
        # Receive 无 LLM，不占 Semaphore
        try:
            await agent.receive_phase(self._t, self._channel)
        except Exception as e:
            logger.error(f"Agent {agent.name} receive_phase 出错: {e}")
        bar.update(1)

    # ── 运行 ──────────────────────────────────────────────────

    async def run(
        self,
        num_steps:   int,
        tick:        int,
        on_step_end: Optional[callable] = None,
    ) -> None:
        """
        运行指定步数。

        Args:
            num_steps:   总步数。
            tick:        每步时长（秒）。
            on_step_end: 可选回调，签名 async def on_step_end(sim, step, results)。
        """
        step_bar = tqdm_module.tqdm(
            total=num_steps, desc="Simulation", ncols=80, unit="step"
        )

        for _ in range(num_steps):
            results  = await self.step(tick)
            time_str = self._t.strftime("%H:%M")

            step_bar.set_postfix({"time": time_str})
            step_bar.update(1)
            logger.info(
                f"Step {self._step_count:4d} | {time_str} | "
                f"{len(results)} agents done"
            )

            # 收集本步所有 agent 的记录
            for agent in self._agents:
                if isinstance(agent, PersonAgent) and agent.last_step_record:
                    record = dict(agent.last_step_record)
                    record["step"]     = self._step_count
                    record["sim_time"] = time_str
                    lng, lat = await self._router.get_agent_position(agent.id)
                    record["position"] = {"lng": lng, "lat": lat}
                    self._all_records.append(record)

            if on_step_end is not None:
                await on_step_end(self, self._step_count, results)

        step_bar.close()
        self._save_results()

    # ── 结果持久化 ────────────────────────────────────────────

    def _save_results(self) -> None:
        if not self._output_dir or not self._all_records:
            return

        os.makedirs(self._output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 详细记录（JSONL）
        detail_path = os.path.join(
            self._output_dir, f"simulation_{timestamp}.jsonl"
        )
        with open(detail_path, "w", encoding="utf-8") as f:
            for record in self._all_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 汇总（per-agent 轨迹）
        summary: dict = {}
        for record in self._all_records:
            aid = record["agent_id"]
            if aid not in summary:
                summary[aid] = {
                    "agent_id":   aid,
                    "agent_name": record["agent_name"],
                    "steps": [],
                }
            summary[aid]["steps"].append({
                "step":        record["step"],
                "sim_time":    record["sim_time"],
                "intention":   record["intention"],
                "act_result":  record["act_result"],
                "needs":       record["needs"],
                "position":    record["position"],
                "sent":        record.get("sent"),
                "received":    record.get("received", []),
            })

        summary_path = os.path.join(
            self._output_dir, f"simulation_{timestamp}_summary.json"
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(list(summary.values()), f, ensure_ascii=False, indent=2)

        logger.info(
            f"仿真结果已保存：\n"
            f"  详细记录 → {detail_path}\n"
            f"  汇总     → {summary_path}"
        )
