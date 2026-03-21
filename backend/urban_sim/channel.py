"""
MessageChannel — 每步的公共消息总线

工作流程：
  Phase 2 (Send)   → agent 调用 channel.post() 写入消息
  Phase 3 (Receive) → agent 调用 channel.get_for() 读取与自己相关的消息
  每步开始           → SimulationLoop 调用 channel.flush() 清空上一步消息

target 类型：
  int       → 私信指定 agent（按 agent_id 匹配）
  "all"     → 全体广播
  "nearby"  → 空间广播，接收方在 NEARBY_RADIUS_M 米内才能收到
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Union

__all__ = ["ChannelMessage", "MessageChannel"]


@dataclass
class ChannelMessage:
    msg_id:      int
    step:        int
    sender_id:   int
    sender_name: str
    content:     str
    target:      Union[int, Literal["all", "nearby"]]
    timestamp:   datetime = field(default_factory=datetime.now)
    # 仅 target="nearby" 时有效，用于距离过滤
    sender_lng:  float | None = None
    sender_lat:  float | None = None


class MessageChannel:
    """每步公共消息总线。"""

    NEARBY_RADIUS_M: float = 100.0   # "nearby" 广播的半径（米）

    def __init__(self) -> None:
        self._messages: list[ChannelMessage] = []
        self._msg_counter: int = 0
        self._current_step: int = 0

    # ── 生命周期 ──────────────────────────────────────────────

    def flush(self, step: int) -> None:
        """每步开始时清空上一步消息，更新步骤号。"""
        self._messages.clear()
        self._current_step = step

    # ── 写入 ──────────────────────────────────────────────────

    def post(
        self,
        sender_id:   int,
        sender_name: str,
        content:     str,
        target:      Union[int, Literal["all", "nearby"]],
        timestamp:   datetime | None = None,
        sender_lng:  float | None = None,
        sender_lat:  float | None = None,
    ) -> ChannelMessage:
        """
        向 channel 写入一条消息。

        Args:
            sender_id:   发送方 agent_id
            sender_name: 发送方姓名（显示用）
            content:     消息内容
            target:      接收目标（agent_id / "all" / "nearby"）
            timestamp:   消息时间戳，默认当前时间
            sender_lng:  发送方经度（target="nearby" 时必须）
            sender_lat:  发送方纬度（target="nearby" 时必须）
        """
        self._msg_counter += 1
        msg = ChannelMessage(
            msg_id      = self._msg_counter,
            step        = self._current_step,
            sender_id   = sender_id,
            sender_name = sender_name,
            content     = content,
            target      = target,
            timestamp   = timestamp or datetime.now(),
            sender_lng  = sender_lng,
            sender_lat  = sender_lat,
        )
        self._messages.append(msg)
        return msg

    # ── 读取 ──────────────────────────────────────────────────

    def get_for(
        self,
        agent_id:  int,
        agent_lng: float | None = None,
        agent_lat: float | None = None,
    ) -> list[ChannelMessage]:
        """
        返回本步中与指定 agent 相关的消息列表（不含自己发的）。

        匹配规则：
          - target == agent_id          → 私信
          - target == "all"             → 全体广播
          - target == "nearby"
            且 distance(sender, agent) < NEARBY_RADIUS_M → 附近广播
        """
        result = []
        for m in self._messages:
            if m.sender_id == agent_id:
                continue
            if m.target == agent_id:
                result.append(m)
            elif m.target == "all":
                result.append(m)
            elif m.target == "nearby":
                if self._within_radius(m, agent_lng, agent_lat):
                    result.append(m)
        return result

    # ── 调试 / 统计 ───────────────────────────────────────────

    @property
    def all_messages(self) -> list[ChannelMessage]:
        """返回本步全部消息（只读快照）。"""
        return list(self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    # ── 内部 ──────────────────────────────────────────────────

    def _within_radius(
        self,
        msg:       ChannelMessage,
        agent_lng: float | None,
        agent_lat: float | None,
    ) -> bool:
        """判断发送方与接收方的距离是否在 NEARBY_RADIUS_M 米内。"""
        if (
            msg.sender_lng is None or msg.sender_lat is None
            or agent_lng is None or agent_lat is None
        ):
            return False
        # 小范围内用墨卡托近似，精度足够
        mid_lat = math.radians((msg.sender_lat + agent_lat) / 2)
        dx = (msg.sender_lng - agent_lng) * 111_320 * math.cos(mid_lat)
        dy = (msg.sender_lat - agent_lat) * 111_320
        return math.sqrt(dx * dx + dy * dy) < self.NEARBY_RADIUS_M
