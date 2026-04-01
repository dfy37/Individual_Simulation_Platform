"""
自然语言采样需求解析器。

策略：规则优先 + LLM 补充。
规则层处理确定性表达（人数、性别、学历、年龄、专业）；
LLM 层补充语义模糊的软偏好（活跃度、多样性、校园气息等）。
"""

import json
import logging
import os
import re
from typing import Optional

from .schema import HardFilters, SamplingSpec

logger = logging.getLogger(__name__)


# ── 规则解析 ──────────────────────────────────────────────────────────────────

def _parse_target_size(text: str, explicit_size: Optional[int]) -> int:
    if explicit_size and explicit_size > 0:
        return explicit_size
    m = re.search(r'(\d+)\s*[个名人位]', text)
    if m:
        return int(m.group(1))
    return 10


def _parse_gender(text: str) -> tuple[Optional[str], dict]:
    """返回 (hard_gender_filter, gender_marginals)。"""
    if re.search(r'只要男|全部男|男性为主', text):
        return None, {"male": 0.8, "female": 0.2}
    if re.search(r'只要女|全部女|女性为主', text):
        return None, {"male": 0.2, "female": 0.8}
    if re.search(r'男女均衡|均衡|各半|性别平衡|男女各', text):
        return None, {"male": 0.5, "female": 0.5}
    if re.search(r'只.*男|仅.*男', text):
        return "male", {}
    if re.search(r'只.*女|仅.*女', text):
        return "female", {}
    return None, {}


def _parse_education(text: str) -> dict:
    """返回 education_bucket 的目标边际。"""
    marginals: dict[str, float] = {}

    has_undergrad = bool(re.search(r'本科|undergraduate', text, re.I))
    has_master    = bool(re.search(r'硕士|研究生|master', text, re.I))
    has_phd       = bool(re.search(r'博士|phd|doctorate', text, re.I))

    # "本科为主，保留少量研究生"
    if has_undergrad and (has_master or has_phd):
        if re.search(r'为主|主要|大多|多数|偏本科', text):
            marginals = {"undergrad": 0.70, "master": 0.20, "phd": 0.10}
        else:
            marginals = {"undergrad": 0.50, "master": 0.30, "phd": 0.20}
    elif has_undergrad:
        marginals = {"undergrad": 0.85, "master": 0.10, "phd": 0.05}
    elif has_master and has_phd:
        marginals = {"undergrad": 0.10, "master": 0.50, "phd": 0.40}
    elif has_master:
        marginals = {"undergrad": 0.20, "master": 0.65, "phd": 0.15}
    elif has_phd:
        marginals = {"undergrad": 0.10, "master": 0.20, "phd": 0.70}

    return marginals


def _parse_major(text: str) -> dict:
    """返回 major_bucket 的目标边际。"""
    buckets: dict[str, float] = {}

    kw_map = {
        "math_cs":           ["数学", "计算机", "软件", "人工智能", "信息", "统计", "理工", "cs", "it"],
        "econ_mgmt":         ["经济", "管理", "金融", "商科", "会计"],
        "humanities_social": ["人文", "社科", "历史", "哲学", "文学", "社会", "政治", "法律"],
        "medicine_life":     ["医学", "生命", "生物", "药学"],
        "arts_media":        ["艺术", "设计", "传媒", "新闻", "影视"],
    }
    for bucket, keywords in kw_map.items():
        if any(kw in text for kw in keywords):
            buckets[bucket] = 0.0  # 先标记有哪些

    if not buckets:
        return {}

    # 检测"多一些"修饰词
    prominent_buckets = set()
    for bucket, keywords in kw_map.items():
        if bucket not in buckets:
            continue
        for kw in keywords:
            if re.search(rf'{kw}.{{0,6}}(多|为主|偏|主要)', text):
                prominent_buckets.add(bucket)

    total_buckets = len(buckets)
    for b in buckets:
        if b in prominent_buckets:
            buckets[b] = 0.4 / max(len(prominent_buckets), 1)
        else:
            buckets[b] = 0.6 / total_buckets

    # 归一化，其余分给 other
    total = sum(buckets.values())
    if total < 0.95:
        buckets["other"] = 1.0 - total
    return {k: round(v, 3) for k, v in buckets.items()}


def _parse_activity(text: str) -> dict:
    """返回 activity_bucket 的目标边际。"""
    if re.search(r'活跃|发帖多|社交媒体活跃|爱发帖|高活跃', text):
        return {"high": 0.45, "medium": 0.40, "low": 0.15}
    if re.search(r'潜水|不活跃|低活跃|安静', text):
        return {"high": 0.10, "medium": 0.30, "low": 0.60}
    return {}


def _parse_age(text: str) -> tuple[Optional[int], Optional[int]]:
    """返回 (age_min, age_max)。"""
    m = re.search(r'(\d{2})\s*[-~至到]\s*(\d{2})\s*岁', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'(\d{2})\s*岁以上', text)
    if m:
        return int(m.group(1)), None
    m = re.search(r'(\d{2})\s*岁以下', text)
    if m:
        return None, int(m.group(1))
    return None, None


# ── LLM 补充解析 ──────────────────────────────────────────────────────────────

_LLM_SYSTEM = """你是一个用户研究助手，负责将自然语言的采样需求解析为结构化 JSON。
只输出合法 JSON，不要输出任何解释文字。

JSON 结构如下：
{
  "soft_preferences": ["描述1", "描述2"],
  "additional_marginals": {
    "feature_name": {"bucket_value": proportion_float}
  },
  "rationale": "对用户需求的简洁解释（50字以内）"
}

feature_name 只能从以下选择：gender, age_bucket, education, major_bucket, activity, interest_bucket
bucket 值：
  gender:         male / female / unknown
  age_bucket:     le20 / 21to23 / 24to26 / 27plus / unknown
  education:      undergrad / master / phd / unknown
  major_bucket:   math_cs / econ_mgmt / humanities_social / medicine_life / arts_media / other
  activity:       high / medium / low
  interest_bucket: fashion / sports / academics / campus / entertainment / food_travel / other

不确定的内容放到 soft_preferences，不要强行映射到 additional_marginals。
"""

def _llm_supplement(query: str, rule_spec: SamplingSpec) -> tuple[list[str], dict, str]:
    """
    调用 LLM 补充规则层无法覆盖的软偏好和语义模糊部分。
    返回 (soft_preferences, additional_marginals, rationale)。
    """
    try:
        import litellm
        model = os.getenv("LLM_MODEL", "deepseek/deepseek-chat")

        user_msg = f"""用户采样需求："{query}"

规则层已解析的部分（无需重复）：
- target_size: {rule_spec.target_size}
- hard_filters: {rule_spec.hard_filters}
- 已有 marginals: {json.dumps(rule_spec.marginals, ensure_ascii=False)}

请只补充规则层未能覆盖的语义信息。"""

        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        # 提取 JSON 部分（防止 LLM 输出 markdown code block）
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            data = json.loads(m.group())
            return (
                data.get("soft_preferences", []),
                data.get("additional_marginals", {}),
                data.get("rationale", ""),
            )
    except Exception as e:
        logger.warning(f"[nl_parser] LLM 补充解析失败: {e}")
    return [], {}, ""


# ── 公开入口 ──────────────────────────────────────────────────────────────────

def parse_query(query: str, target_size: Optional[int] = None) -> SamplingSpec:
    """
    将自然语言采样需求解析为 SamplingSpec。

    Args:
        query:       自然语言描述，如"帮我抽30个复旦学生，男女均衡，本科为主"
        target_size: 明确指定的目标人数（优先于 query 中的数字）

    Returns:
        SamplingSpec 对象
    """
    spec = SamplingSpec()
    spec.target_size = _parse_target_size(query, target_size)

    # Hard filters
    hard_gender, gender_marginals = _parse_gender(query)
    age_min, age_max = _parse_age(query)
    spec.hard_filters = HardFilters(
        age_min=age_min,
        age_max=age_max,
        gender=hard_gender,
        occupation_keywords=[],
    )

    # Marginals
    marginals: dict[str, dict[str, float]] = {}
    if gender_marginals:
        marginals["gender"] = gender_marginals
    edu_m = _parse_education(query)
    if edu_m:
        marginals["education"] = edu_m
    major_m = _parse_major(query)
    if major_m:
        marginals["major_bucket"] = major_m
    act_m = _parse_activity(query)
    if act_m:
        marginals["activity"] = act_m

    spec.marginals = marginals

    # LLM 补充
    soft_prefs, extra_marginals, rationale = _llm_supplement(query, spec)
    spec.soft_preferences = soft_prefs
    # 合并 LLM 追加的 marginals（不覆盖规则层已有的）
    for feat, dist in extra_marginals.items():
        if feat not in spec.marginals and isinstance(dist, dict):
            spec.marginals[feat] = {k: float(v) for k, v in dist.items()}
    spec.rationale = rationale or f'从"{query}"解析的采样规格'

    return spec
