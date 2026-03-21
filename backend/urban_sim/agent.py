"""
AgentBase + PersonAgent

每步执行三个独立阶段（由 SimulationLoop 顺序调度）：

  Phase 1  move_phase(tick, t)
           → Observe 位置 / Decide 移动 / Act 移动
           → 更新 needs（被动衰减 + LLM 调整）

  Phase 2  send_phase(t, channel)
           → LLM 决策是否发消息、发给谁、发什么
           → 写入 MessageChannel

  Phase 3  receive_phase(t, channel)
           → 从 channel 读取本步收到的消息
           → 规则驱动更新短期记忆和 social need（无 LLM 调用）

三个阶段结束后 last_step_record 自动更新，供 SimulationLoop 收集。
"""

import json
import logging
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Union

from pydantic import BaseModel, Field

from .channel import MessageChannel
from .config import extract_json, get_llm_router

if TYPE_CHECKING:
    pass

__all__ = ["AgentBase", "PersonAgent", "Needs", "MoveDecision"]

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────

class Needs(BaseModel):
    """需求满意度（0~1），值越低越紧迫，驱动 Agent 决策。"""
    satiety: float = Field(default=0.7, ge=0.0, le=1.0, description="饱腹感")
    energy:  float = Field(default=0.8, ge=0.0, le=1.0, description="精力")
    safety:  float = Field(default=0.9, ge=0.0, le=1.0, description="安全感")
    social:  float = Field(default=0.6, ge=0.0, le=1.0, description="社交满足感")


# 被动衰减速率（每小时）
PASSIVE_DECAY_PER_HOUR: dict[str, float] = {
    "satiety": 0.03,
    "energy":  0.02,
    "social":  0.02,
    "safety":  0.005,
}


class MoveDecision(BaseModel):
    """Phase 1 LLM 输出的移动决策。"""
    intention:    str = Field(description="当前意图（一句话）")
    instruction:  str = Field(
        default="",
        description="发给环境 router 的移动指令（自然语言）。无需移动时留空。",
    )
    need_updates: dict[str, float] = Field(
        default_factory=dict,
        description="需求满意度调整量，如 {\"satiety\": -0.1}",
    )
    reasoning: str = Field(default="", description="决策简短理由")


class SendDecision(BaseModel):
    """Phase 2 LLM 输出的发消息决策。"""
    should_send: bool = Field(description="是否发送消息")
    target:      Union[int, str] = Field(
        default="nearby",
        description="接收目标：agent_id（整数）/ 'nearby'（附近） / 'all'（全体广播）",
    )
    content:     str = Field(default="", description="消息内容")
    reasoning:   str = Field(default="", description="决策简短理由")


# ─────────────────────────────────────────────────────────────
# AgentBase
# ─────────────────────────────────────────────────────────────

class AgentBase(ABC):
    """Agent 基类，定义三阶段接口。"""

    def __init__(self, id: int, profile: dict, name: Optional[str] = None):
        self._id = id
        self._profile = profile
        self._name = name or profile.get("name", f"Agent_{id}")
        self._router = None
        self._llm_router, self._model_name = get_llm_router()

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def profile(self) -> dict:
        return self._profile

    async def init(self, router) -> None:
        self._router = router

    @abstractmethod
    async def move_phase(self, tick: int, t: datetime) -> str:
        """Phase 1：处理移动逻辑，返回本步行动摘要。"""
        ...

    @abstractmethod
    async def send_phase(self, t: datetime, channel: MessageChannel) -> None:
        """Phase 2：决定是否发消息，写入 channel。"""
        ...

    @abstractmethod
    async def receive_phase(self, t: datetime, channel: MessageChannel) -> None:
        """Phase 3：从 channel 读取消息，更新记忆。"""
        ...

    async def close(self) -> None:
        pass

    async def _llm_call(self, messages: list[dict]) -> str:
        response = await self._llm_router.acompletion(
            model=self._model_name,
            messages=messages,
        )
        return response.choices[0].message.content or ""


# ─────────────────────────────────────────────────────────────
# PersonAgent
# ─────────────────────────────────────────────────────────────

class PersonAgent(AgentBase):
    """
    LLM 驱动的人物 Agent，三阶段执行。

    Args:
        id:           唯一整数 ID
        profile:      人物档案 dict（name / occupation / mbti / interests 等）
        name:         显示名称，默认取 profile["name"]
        memory_size:  短期记忆最大条数
        needs:        初始需求状态
        agent_roster: 所有 agent 的 {id: name} 映射，用于 send_phase 提示 LLM
    """

    def __init__(
        self,
        id:            int,
        profile:       dict,
        name:          Optional[str] = None,
        memory_size:   int = 10,
        needs:         Optional[Needs] = None,
        agent_roster:  Optional[dict[int, str]] = None,
    ):
        super().__init__(id, profile, name)
        self._needs       = needs or Needs()
        self._short_memory: deque[str] = deque(maxlen=memory_size)
        self._current_intention: str = "刚开始一天的活动"
        self._step_count:  int = 0
        self._agent_roster: dict[int, str] = agent_roster or {}

        # 每步各阶段的中间状态（供 last_step_record 汇总）
        self._phase_obs:          str = ""
        self._phase_instruction:  str = ""
        self._phase_act_result:   str = ""
        self._phase_need_updates: dict = {}
        self._phase_sent:         dict | None = None
        self._phase_received:     list[dict] = []
        self._last_step_record:   dict = {}

    # ── Phase 1: Move ─────────────────────────────────────────

    async def move_phase(self, tick: int, t: datetime) -> str:
        """
        Observe 当前位置/状态 → LLM 决策移动意图和指令 → 执行移动。
        更新 needs（被动衰减 + LLM 调整）并记录到短期记忆。
        """
        assert self._router is not None, "Agent 未初始化，请先调用 init()"

        self._step_count += 1
        ctx      = {"agent_id": self._id, "name": self._name}
        time_str = t.strftime("%H:%M")
        prefix   = f"[{time_str}] {self._name:<10}"

        # Observe
        obs = await self._router.ask(
            ctx=ctx,
            instruction=(
                "Please observe and report my current state: "
                "location, movement status, and any unread messages."
            ),
            readonly=True,
        )
        self._phase_obs = obs
        logger.info(f"{prefix} [观察] {obs[:120].replace(chr(10), ' ')}")

        # Decide
        decision = await self._decide_movement(obs, t, tick)
        self._current_intention  = decision.intention
        self._phase_instruction  = decision.instruction
        self._phase_need_updates = decision.need_updates

        logger.info(
            f"{prefix} [决策] {decision.intention}"
            + (f" | {decision.reasoning}" if decision.reasoning else "")
        )

        # Act
        act_result = ""
        if decision.instruction.strip():
            logger.info(f"{prefix} [执行] {decision.instruction}")
            act_result = await self._router.ask(
                ctx=ctx,
                instruction=decision.instruction,
                readonly=False,
                system_prompt=f"你在帮 {self._name} 执行环境操作。请使用可用工具完成指令，并用一句话报告执行结果。",
            )
            logger.info(f"{prefix} [结果] {act_result[:120].replace(chr(10), ' ')}")
        self._phase_act_result = act_result

        # 更新 needs
        self._apply_passive_decay(tick)
        self._apply_need_updates(decision.need_updates)

        needs = self._needs
        logger.info(
            f"{prefix} [需求] 饱腹:{needs.satiety:.2f} "
            f"精力:{needs.energy:.2f} "
            f"安全:{needs.safety:.2f} "
            f"社交:{needs.social:.2f}"
        )

        # 更新记忆
        self._update_short_memory(t, decision.intention, act_result)

        summary = f"[{self._name}] {decision.intention}"
        if act_result:
            summary += f" → {act_result[:80]}"
        return summary

    # ── Phase 2: Send ─────────────────────────────────────────

    async def send_phase(self, t: datetime, channel: MessageChannel) -> None:
        """
        LLM 决策是否发消息、发给谁、发什么 → 写入 channel。
        LLM 调用轻量（无工具调用），只输出结构化 JSON。
        """
        self._phase_sent = None

        decision = await self._decide_send(t)
        if not decision.should_send or not decision.content.strip():
            return

        # 解析 target：字符串数字转 int
        target = decision.target
        if isinstance(target, str) and target.lstrip("-").isdigit():
            target = int(target)

        if target not in ("all", "nearby") and not isinstance(target, int):
            logger.warning(f"[{self._name}] send_phase: 无效 target={target!r}，跳过发送")
            return

        # 获取当前坐标（仅 nearby 需要，但统一获取以备用）
        lng, lat = await self._router.get_agent_position(self._id)

        channel.post(
            sender_id   = self._id,
            sender_name = self._name,
            content     = decision.content,
            target      = target,
            timestamp   = t,
            sender_lng  = lng,
            sender_lat  = lat,
        )

        self._phase_sent = {
            "target":  target,
            "content": decision.content,
        }
        target_label = (
            f"agent_{target}" if isinstance(target, int) else target
        )
        logger.info(f"[{t.strftime('%H:%M')}] {self._name:<10} [发送→{target_label}] {decision.content[:80]}")

    # ── Phase 3: Receive ──────────────────────────────────────

    async def receive_phase(self, t: datetime, channel: MessageChannel) -> None:
        """
        从 channel 读取本步收到的消息 → 写入短期记忆 → 更新 social need。
        规则驱动，无 LLM 调用。
        """
        self._phase_received = []

        lng, lat = await self._router.get_agent_position(self._id)
        messages = channel.get_for(self._id, agent_lng=lng, agent_lat=lat)

        if not messages:
            self._build_step_record(t)
            return

        time_str = t.strftime("%H:%M")
        for m in messages:
            label = (
                "私信" if isinstance(m.target, int)
                else ("附近" if m.target == "nearby" else "广播")
            )
            entry = f"[{time_str}][{label}] {m.sender_name}: {m.content}"
            self._short_memory.append(entry)

            self._phase_received.append({
                "sender_id":   m.sender_id,
                "sender_name": m.sender_name,
                "content":     m.content,
                "target_type": label,
            })
            logger.info(f"[{time_str}] {self._name:<10} [收到/{label}] {m.sender_name}: {m.content[:80]}")

        # social need 小幅提升（收到消息 = 有社交互动）
        boost = min(0.05 * len(messages), 0.15)
        self._needs.social = min(1.0, self._needs.social + boost)

        self._build_step_record(t)

    # ── 记忆注入（外部调用，如 GatheringSpace）────────────────

    def inject_memory(self, entry: str) -> None:
        """直接向短期记忆追加一条记录（不经 LLM）。"""
        self._short_memory.append(entry)

    # ── 内部：LLM 决策 ────────────────────────────────────────

    async def _decide_movement(
        self, observation: str, t: datetime, tick: int
    ) -> MoveDecision:
        """Phase 1 LLM 决策：移动意图 + 环境执行指令。"""
        messages = [
            {"role": "system", "content": self._build_move_system_prompt(t, tick)},
            {"role": "user",   "content": f"当前环境观察：\n{observation}\n\n请决策并输出 JSON："},
        ]
        raw = await self._llm_call(messages)

        json_str = extract_json(raw)
        if json_str:
            try:
                return MoveDecision.model_validate_json(json_str)
            except Exception as e:
                logger.warning(f"[{self._name}] MoveDecision 解析失败：{e}")

        return MoveDecision(
            intention  = raw.strip()[:100] or "思考中",
            instruction= "",
            reasoning  = "JSON 解析失败，跳过本步行动",
        )

    async def _decide_send(self, t: datetime) -> SendDecision:
        """Phase 2 LLM 决策：是否发消息、发给谁、发什么。"""
        messages = [
            {"role": "system", "content": self._build_send_system_prompt(t)},
            {"role": "user",   "content": "请决策并输出 JSON："},
        ]
        raw = await self._llm_call(messages)

        json_str = extract_json(raw)
        if json_str:
            try:
                return SendDecision.model_validate_json(json_str)
            except Exception as e:
                logger.warning(f"[{self._name}] SendDecision 解析失败：{e}")

        return SendDecision(should_send=False)

    # ── 内部：Prompt 构建 ─────────────────────────────────────

    def _build_move_system_prompt(self, t: datetime, tick: int) -> str:
        skip_keys   = {"sample_posts", "initial_needs", "bio", "user_id"}
        profile_str = "\n".join(
            f"  {k}: {v}" for k, v in self._profile.items() if k not in skip_keys
        )
        sample_posts = self._profile.get("sample_posts", [])
        posts_section = ""
        if sample_posts:
            lines = []
            for p in sample_posts[:3]:
                meta = f"[{p.get('created_at','')[:10]} | {p.get('location','')}]"
                lines.append(f"  {meta} {p.get('title','')}")
                if p.get("content"):
                    lines.append(f"    {p['content'][:60]}")
            posts_section = "## 你的历史发帖\n" + "\n".join(lines) + "\n\n"

        needs = self._needs
        needs_str = (
            f"  饱腹感:{needs.satiety:.2f}  精力:{needs.energy:.2f}  "
            f"安全感:{needs.safety:.2f}  社交:{needs.social:.2f}"
        )
        memory_str = (
            "\n".join(f"  - {m}" for m in self._short_memory)
            if self._short_memory else "  （暂无记录）"
        )
        time_str  = t.strftime("%Y-%m-%d %H:%M")
        tick_min  = tick // 60

        return f"""你是一个生活在城市中的真实人物，当前时间是 {time_str}，本步时长 {tick_min} 分钟。

## 你的个人信息
{profile_str}

{posts_section}## 当前需求状态（0=极度匮乏，1=完全满足）
{needs_str}

## 最近行动记忆
{memory_str}

## 决策规则
- 根据当前时间、需求状态和观察，决定本步最合理的移动行动
- 需求值越低越紧迫，优先满足最迫切的需求
- instruction 是发给地图环境的执行指令，如："移动到最近的餐厅"、"前往工作地点"
- 如果暂时不需要移动（如刚到达目的地、正在休息），instruction 留空

## 输出格式（JSON）
{{
  "intention": "本步意图（一句话）",
  "instruction": "移动指令（自然语言，或留空字符串）",
  "need_updates": {{"satiety": -0.05}},
  "reasoning": "决策理由（简短）"
}}"""

    def _build_send_system_prompt(self, t: datetime) -> str:
        time_str   = t.strftime("%H:%M")
        memory_str = (
            "\n".join(f"  - {m}" for m in self._short_memory)
            if self._short_memory else "  （暂无记录）"
        )

        # 构建 agent 名单（帮助 LLM 知道可以联系谁）
        if self._agent_roster:
            roster_lines = "\n".join(
                f"  id={aid}: {aname}"
                for aid, aname in self._agent_roster.items()
                if aid != self._id
            )
            roster_section = (
                f"## 同学列表（都是复旦学生，可以主动联系）\n{roster_lines}\n\n"
            )
        else:
            roster_section = ""

        needs      = self._needs
        social_val = needs.social
        social_hint = (
            "【社交需求很低（<0.4），强烈建议主动联系他人】"
            if social_val < 0.4 else
            "【社交需求偏低（<0.6），可以考虑主动沟通】"
            if social_val < 0.6 else
            ""
        )

        # 简要人物信息
        skip_keys = {"sample_posts", "initial_needs", "bio", "user_id"}
        profile_brief = "、".join(
            f"{k}={v}" for k, v in self._profile.items()
            if k not in skip_keys and isinstance(v, (str, int, float)) and v
        )

        return f"""你是 {self._name}（{profile_brief}），当前时间 {time_str}，本步意图：{self._current_intention}。

## 当前需求（0=极度匮乏，1=完全满足）
  社交满足感：{social_val:.2f} {social_hint}
  精力：{needs.energy:.2f}  饱腹感：{needs.satiety:.2f}

## 最近记忆（含已收到的消息）
{memory_str}

{roster_section}## 消息发送指引
你生活在一个有真实社交的世界。以下情形**应该**发消息：
- 社交需求低（<0.6）：主动找人聊天、约饭、约活动等
- 当前意图与他人相关（如想去某地但想找人同行）
- 看到记忆中有人曾联系过你，想回应
- 附近有活动，想向周围的人打招呼或搭话（target: "nearby"）
- 有紧急或公告性信息（target: "all"）

target 选择与可见性规则（非常重要）：
  整数 agent_id → **私信**：只有对方能看到，其他人看不到。适合：
      - 回复某人发给你的消息
      - 约某个同学一起吃饭/学习/活动
      - 分享只想告诉某人的事
  "nearby"      → **附近广播**：只有空间上靠近你的人能收到。适合：
      - 当面打招呼、搭话
      - 向周围人发起活动邀请
  "all"         → **全体广播**：所有人都能看到，慎用。适合紧急通知或公告。

私信是最常见、最自然的选择——就像发微信一样，优先考虑发私信给特定的人。

只有在**完全没有任何社交需求**且当前意图纯粹独立（如专注学习/睡觉）时才不发消息。

## 输出格式（JSON）
发送时：
{{"should_send": true, "target": <agent_id 或 "nearby" 或 "all">, "content": "消息内容（自然口语，1-2句）", "reasoning": "理由"}}
不发送时：
{{"should_send": false, "reasoning": "不发的理由"}}"""

    # ── 内部：需求和记忆更新 ──────────────────────────────────

    def _apply_need_updates(self, updates: dict[str, float]) -> None:
        for key, delta in updates.items():
            if hasattr(self._needs, key):
                current = getattr(self._needs, key)
                setattr(self._needs, key, max(0.0, min(1.0, current + delta)))

    def _apply_passive_decay(self, tick: int) -> None:
        hours = tick / 3600.0
        for key, rate in PASSIVE_DECAY_PER_HOUR.items():
            current = getattr(self._needs, key)
            setattr(self._needs, key, max(0.0, current - rate * hours))

    def _update_short_memory(self, t: datetime, intention: str, result: str) -> None:
        entry = f"[{t.strftime('%H:%M')}] {intention}"
        if result:
            entry += f"（{result[:60]}）"
        self._short_memory.append(entry)

    def _build_step_record(self, t: datetime) -> None:
        """汇总三个阶段的中间状态，写入 last_step_record。"""
        self._last_step_record = {
            "agent_id":    self._id,
            "agent_name":  self._name,
            "observation": self._phase_obs,
            "intention":   self._current_intention,
            "instruction": self._phase_instruction,
            "act_result":  self._phase_act_result,
            "need_updates":self._phase_need_updates,
            "needs":       self._needs.model_dump(),
            "sent":        self._phase_sent,
            "received":    self._phase_received,
        }

    # ── 属性 ──────────────────────────────────────────────────

    @property
    def needs(self) -> Needs:
        return self._needs

    @property
    def current_intention(self) -> str:
        return self._current_intention

    @property
    def short_memory(self) -> list[str]:
        return list(self._short_memory)

    @property
    def last_step_record(self) -> dict:
        return self._last_step_record
