"""
采样规格 (sampling_spec) 的数据结构定义。

sampling_spec 是自然语言 → IPF 采样的中间层，
由 nl_parser.py 生成，由 service.py 消费。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HardFilters:
    """硬过滤条件，不满足直接排除。"""
    age_min: Optional[int] = None       # 最小年龄（含）
    age_max: Optional[int] = None       # 最大年龄（含）
    gender: Optional[str] = None        # "male" / "female" / None 表示不限
    occupation_keywords: list[str] = field(default_factory=list)  # occupation 中必须包含的关键词（OR）


@dataclass
class SamplingSpec:
    """
    完整的采样规格。

    Attributes:
        target_size:      目标样本量
        hard_filters:     硬过滤条件
        marginals:        目标边际分布，格式：{ feature_name: { bucket_value: proportion } }
                          proportion 之和不必严格等于 1（会自动归一化）
        soft_preferences: 软偏好描述（自然语言，仅记录用于展示，不强制执行）
        rationale:        系统对自然语言的结构化解释
    """
    target_size: int = 10
    hard_filters: HardFilters = field(default_factory=HardFilters)
    marginals: dict[str, dict[str, float]] = field(default_factory=dict)
    soft_preferences: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "target_size": self.target_size,
            "hard_filters": {
                "age_min":              self.hard_filters.age_min,
                "age_max":              self.hard_filters.age_max,
                "gender":               self.hard_filters.gender,
                "occupation_keywords":  self.hard_filters.occupation_keywords,
            },
            "marginals":        self.marginals,
            "soft_preferences": self.soft_preferences,
            "rationale":        self.rationale,
        }
