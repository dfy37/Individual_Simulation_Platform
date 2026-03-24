"""
从 Step 2 / Step 3 仿真结果中提取行为摘要，丰富访谈人设。
"""

import logging
from statistics import mean
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def extract_urban_summary(steps: List[Dict], agent_id: int) -> Optional[Dict[str, Any]]:
    """从城市仿真 steps 列表中提取指定 agent 的行为摘要。"""
    agent_steps = [
        a for step in steps
        for a in step.get("agents", [])
        if a.get("id") == agent_id
    ]
    if not agent_steps:
        return None

    intentions = [a.get("intention", "") for a in agent_steps if a.get("intention")]
    intention_counts: Dict[str, int] = {}
    for it in intentions:
        k = it[:20]
        intention_counts[k] = intention_counts.get(k, 0) + 1
    top_intentions = sorted(intention_counts, key=lambda k: intention_counts[k], reverse=True)[:3]

    social_vals = [a["needs"].get("social", 0.6) for a in agent_steps if a.get("needs")]
    energy_vals = [a["needs"].get("energy", 0.7) for a in agent_steps if a.get("needs")]
    avg_social  = mean(social_vals) if social_vals else 0.6
    avg_energy  = mean(energy_vals) if energy_vals else 0.7
    sent_count  = sum(1 for a in agent_steps if a.get("sent"))

    return {
        "behavior_pattern":    _describe_behavior(avg_social, avg_energy, sent_count, len(agent_steps)),
        "frequent_intentions": "、".join(top_intentions) if top_intentions else "日常活动",
        "avg_social_need":     round(avg_social, 2),
        "avg_energy_need":     round(avg_energy, 2),
        "message_activity":    sent_count,
    }


def extract_online_summary(agents_data: List[Dict], agent_id: int) -> Optional[Dict[str, Any]]:
    """从线上仿真 agents 数据中提取指定 agent 的发帖/态度摘要。"""
    agent = next(
        (a for a in agents_data if a.get("agent_id") == agent_id or a.get("id") == agent_id),
        None,
    )
    if not agent:
        return None

    group  = agent.get("group", "普通用户")
    posts  = agent.get("posts", [])
    sample = []
    for p in posts[:3]:
        if isinstance(p, str):
            sample.append(p[:60])
        elif isinstance(p, dict):
            content = p.get("content") or p.get("text") or ""
            if content:
                sample.append(content[:60])

    final_attitude = agent.get("final_attitude") or agent.get("attitude_score")
    return {
        "role":           group,
        "sample_posts":   sample,
        "final_attitude": float(final_attitude) if final_attitude is not None else None,
        "interaction_style": _describe_online_style(group, len(posts)),
    }


def _describe_behavior(social, energy, sent, total_steps) -> str:
    parts = []
    if energy < 0.4:
        parts.append("状态较疲惫")
    elif energy > 0.7:
        parts.append("精力充沛")
    if social < 0.45:
        parts.append("社交需求偏低，倾向独处")
    elif social > 0.7:
        parts.append("社交活跃")
    if sent == 0:
        parts.append("不主动发消息")
    elif total_steps > 0 and sent >= total_steps // 2:
        parts.append("主动发消息，表达欲强")
    return "，".join(parts) if parts else "作息正常"


def _describe_online_style(group, post_count) -> str:
    if group == "KOL":
        return "意见领袖，发帖积极，影响力较强"
    if group == "潜水用户":
        return "倾向浏览而非发帖，很少主动表达"
    return "普通用户，偶尔发帖分享"
