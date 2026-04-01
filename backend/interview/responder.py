"""
VirtualAgentResponder — 以 agent profile 为人格基底，由 LLM 扮演虚拟受访者回答问题。
"""

import logging
import re
from typing import Any, Dict, List, Optional

from .llm import make_llm_client

logger = logging.getLogger(__name__)

_STATUS_MARKERS = [
    "想了想", "沉默", "停顿", "犹豫", "笑了笑", "叹气", "思考", "认真想", "皱眉",
    "无奈", "小声", "轻声", "顿了顿", "停了一下", "受访者", "状态", "心理活动",
]


def _llm_call(messages: List[Dict], temperature: float = 0.85, max_tokens: int = 200) -> str:
    client, model = make_llm_client(timeout=60)
    resp     = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


class VirtualAgentResponder:
    """
    Args:
        agent_profile:  来自 student_profiles.json 的完整画像 dict
        product_name:   本次访谈的产品名称
        urban_summary:  Step 2 城市仿真行为摘要（可为 None）
        online_summary: Step 3 线上仿真舆论摘要（可为 None）
    """

    def __init__(self, agent_profile: Dict[str, Any], product_name: str,
                 urban_summary: Optional[Dict] = None, online_summary: Optional[Dict] = None):
        self._profile = agent_profile
        self._product = product_name
        self._urban   = urban_summary
        self._online  = online_summary
        self._system_prompt = self._build_system_prompt()

    def reply(self, question_text: str, question_type: str,
              options: Optional[List[str]], context: List[Dict]) -> str:
        user_msg = self._build_user_message(question_text, question_type, options)
        messages = [{"role": "system", "content": self._system_prompt}]
        for turn in context[-6:]:
            role = "assistant" if turn.get("role") == "agent" else "user"
            messages.append({"role": role, "content": turn.get("content", "")})
        messages.append({"role": "user", "content": user_msg})
        try:
            raw = _llm_call(messages) or "我需要想想……"
            return self._postprocess_response(raw, question_type, options)
        except Exception as e:
            logger.error(f"VirtualAgentResponder 调用失败: {e}")
            return "嗯……这个问题我需要想一下。"

    def _build_system_prompt(self) -> str:
        p           = self._profile
        name        = p.get("name", "受访者")
        occupation  = p.get("occupation", "学生")
        mbti        = p.get("mbti", "")
        gender      = "男" if p.get("gender") == "male" else "女" if p.get("gender") == "female" else ""
        age         = p.get("age", "")
        major       = p.get("major", "")
        interests   = "、".join(p.get("interests", [])[:5])
        personality = (p.get("personality") or "")[:200]

        posts = p.get("sample_posts", [])[:2]
        posts_str = ""
        if posts:
            lines = [f"  「{post.get('content','')[:60]}」" for post in posts if post.get("content")]
            if lines:
                posts_str = "\n\n【你的社媒发言风格】\n" + "\n".join(lines)

        urban_str = ""
        if self._urban:
            u = self._urban
            urban_str = (
                "\n\n【最近的生活状态（来自行为仿真）】\n"
                f"  行为规律：{u.get('behavior_pattern', '正常作息')}\n"
                f"  常见活动：{u.get('frequent_intentions', '')}\n"
                f"  社交活跃度：{'偏低，倾向独处' if u.get('avg_social_need', 0.6) < 0.5 else '较正常'}"
            )

        online_str = ""
        if self._online:
            o = self._online
            role_map = {"KOL": "意见领袖（爱发帖、影响力强）",
                        "普通用户": "普通网友（偶尔发帖）",
                        "潜水用户": "潜水党（很少发帖）"}
            role_desc = role_map.get(o.get("role", ""), "普通网友")
            online_str = f"\n\n【最近的线上表达（来自舆论仿真）】\n  社交媒体角色：{role_desc}\n"
            for post in o.get("sample_posts", [])[:2]:
                online_str += f"  你最近发过：「{str(post)[:60]}」\n"

        profile_section = (
            f"你是 {name}，{f'{age}岁，' if age else ''}{gender}{occupation}。\n"
            + (f"专业：{major}\n" if major else "")
            + (f"MBTI：{mbti}\n" if mbti else "")
            + (f"兴趣：{interests}\n" if interests else "")
            + f"性格特点：{personality if personality else '真实自然'}"
        )

        return f"""{profile_section}{posts_str}{urban_str}{online_str}

【访谈背景】
你正在参与一场关于「{self._product}」的消费者访谈。请以你真实的个性和生活状态来回答。

【回答规则】
1. 用口语化的自然中文，1~3 句话，不要过于正式
2. 结合自身实际情况，有时可以提及具体细节（比如价格感受、使用场景）
3. 保持人物一致性，前后回答不要矛盾
4. 如果是单选题或量表题，必须先直接说出给定选项里的一个原样选项，再补一句很短的理由
5. 不要输出括号状态、动作描写、旁白、舞台说明或心理活动，例如“（想了想）”“[沉默]”“【停顿】”
6. 不要假装热情，也不要过于负面，保持真实感"""

    def _build_user_message(self, question: str, qtype: str, options: Optional[List[str]]) -> str:
        msg = f"访谈员问：{question}"
        if options and qtype in ("single_choice", "Likert", "likert"):
            msg += f"\n选项：{' / '.join(str(o) for o in options)}"
        return msg

    def _postprocess_response(self, text: str, qtype: str, options: Optional[List[str]]) -> str:
        cleaned = self._strip_status_text(text or "")
        if options and qtype in ("single_choice", "Likert", "likert", "rating"):
            cleaned = self._normalize_structured_answer(cleaned, options)
        return cleaned or "我需要想想……"

    def _strip_status_text(self, text: str) -> str:
        cleaned = text.strip()
        bracket_patterns = [
            r"（[^）]{0,12}）",
            r"\[[^\]]{0,12}\]",
            r"【[^】]{0,12}】",
            r"\([^)]{0,12}\)",
        ]

        def _maybe_remove(match: re.Match[str]) -> str:
            inner = match.group(0)[1:-1]
            return "" if any(k in inner for k in _STATUS_MARKERS) else match.group(0)

        for pattern in bracket_patterns:
            cleaned = re.sub(pattern, _maybe_remove, cleaned)

        cleaned = re.sub(r"^(受访者|回答|回复)[:：]\s*", "", cleaned)
        cleaned = re.sub(r"^[，。！？、:：\-\s]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _extract_exact_option(self, text: str, options: List[str]) -> Optional[str]:
        best: tuple[int, int, str] | None = None
        for opt in sorted([str(o).strip() for o in options if str(o).strip()], key=len, reverse=True):
            m = re.search(re.escape(opt), text, flags=re.IGNORECASE)
            if not m:
                continue
            candidate = (m.start(), -len(opt), opt)
            if best is None or candidate < best:
                best = candidate
        return best[2] if best else None

    def _normalize_structured_answer(self, text: str, options: List[str]) -> str:
        match = self._extract_exact_option(text, options)
        if not match:
            return text.strip()

        m = re.search(re.escape(match), text, flags=re.IGNORECASE)
        if not m:
            return text.strip()

        prefix = text[:m.start()].strip(" ，。；;:：")
        suffix = text[m.end():].strip(" ，。；;:：")
        remainder = " ".join(part for part in [prefix, suffix] if part).strip()
        remainder = re.sub(r"^(我会选|我选的是|我选|选|会选|更像|更接近|应该是|就是|我觉得是|我觉得)\s*", "", remainder)
        if remainder:
            return f"{match}，{remainder}".strip()
        return match
