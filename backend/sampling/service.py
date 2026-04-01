"""
采样服务层：统一入口，串联 filter → parse_spec → IPF → sample → diagnostics。

公开函数：sample_preview(query, target_size) -> dict
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .schema import SamplingSpec
from .features import extract_features_batch
from .nl_parser import parse_query
from .ipf import ipf_sample

logger = logging.getLogger(__name__)


def _load_all_profiles() -> list[dict]:
    """加载全量画像（懒加载，每次调用重新读文件以保持最新）。"""
    from config import PROFILES_PATH
    if not PROFILES_PATH.exists():
        return []
    with open(PROFILES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _hard_filter(profiles: list[dict], spec: SamplingSpec) -> list[dict]:
    """按 hard_filters 过滤候选池。"""
    hf = spec.hard_filters
    result = []
    for p in profiles:
        # 性别硬过滤
        if hf.gender:
            g = (p.get("gender") or "").lower()
            if hf.gender == "male"   and g not in ("male",   "m", "男"):
                continue
            if hf.gender == "female" and g not in ("female", "f", "女"):
                continue
        # 年龄范围
        try:
            age = int(p.get("age") or 0)
        except (TypeError, ValueError):
            age = 0
        if hf.age_min is not None and age < hf.age_min:
            continue
        if hf.age_max is not None and age > hf.age_max:
            continue
        # occupation 关键词（OR 逻辑）
        if hf.occupation_keywords:
            occ = p.get("occupation") or ""
            if not any(kw in occ for kw in hf.occupation_keywords):
                continue
        result.append(p)
    return result


def _compute_summary(selected: list[dict], features: list[dict]) -> dict:
    """生成已选样本的人口统计摘要。"""
    n = len(selected)
    if n == 0:
        return {}

    def _count(feat_name):
        counts: dict[str, int] = {}
        for fm in features:
            b = fm.get(feat_name, "unknown")
            counts[b] = counts.get(b, 0) + 1
        return {k: round(v / n, 3) for k, v in counts.items()}

    return {
        "count":       n,
        "gender":      _count("gender"),
        "education":   _count("education"),
        "major_bucket": _count("major_bucket"),
        "activity":    _count("activity"),
        "age_bucket":  _count("age_bucket"),
    }


def sample_preview(query: str, target_size: Optional[int] = None) -> dict:
    """
    完整采样预览流程。

    Args:
        query:       自然语言采样需求
        target_size: 目标样本量（若 None 则从 query 中解析）

    Returns:
        {
            "sampling_spec":     dict,         # 结构化采样规格
            "selected_profiles": list[dict],   # 选出的 profile 列表（轻量版）
            "summary":           dict,         # 实际分布摘要
            "diagnostics":       dict,         # 偏差诊断报告
            "candidate_count":   int,          # hard filter 后候选池大小
            "total_profiles":    int,          # 全量画像数
        }
    """
    # 1. 解析自然语言 → sampling_spec
    spec = parse_query(query, target_size)
    logger.info(f"[service] 解析完成: target_size={spec.target_size}, "
                f"marginals={list(spec.marginals.keys())}")

    # 2. 加载全量画像
    all_profiles = _load_all_profiles()
    total = len(all_profiles)

    # 3. Hard filter
    candidates = _hard_filter(all_profiles, spec)
    candidate_count = len(candidates)
    logger.info(f"[service] hard filter 后候选池: {candidate_count}/{total}")

    if candidate_count == 0:
        return {
            "sampling_spec":     spec.to_dict(),
            "selected_profiles": [],
            "summary":           {},
            "diagnostics":       {"error": "候选池为空，约束过严，请放宽条件"},
            "candidate_count":   0,
            "total_profiles":    total,
        }

    if candidate_count < spec.target_size:
        logger.warning(f"[service] 候选池({candidate_count})小于目标量({spec.target_size})，"
                       "将全部返回")
        spec.target_size = candidate_count

    # 4. 特征工程
    feature_matrix = extract_features_batch(candidates)

    # 5. IPF 采样
    result = ipf_sample(candidates, feature_matrix, spec)

    selected   = result["selected_profiles"]
    sel_feats  = result["selected_features"]
    deviation  = result["deviation"]

    # 6. 生成摘要（轻量版 profile 供前端展示）
    slim_profiles = [
        {
            "user_id":    p.get("user_id"),
            "name":       p.get("name"),
            "gender":     p.get("gender"),
            "age":        p.get("age"),
            "occupation": p.get("occupation"),
            "major":      p.get("major"),
            "mbti":       p.get("mbti"),
            "interests":  p.get("interests", [])[:4],
            "bio":        (p.get("bio") or "")[:80],
        }
        for p in selected
    ]

    summary = _compute_summary(selected, sel_feats)

    # 7. 诊断报告（格式化偏差）
    diagnostics: dict = {}
    overall_quality = "good"
    for feat, dev in deviation.items():
        md = dev["max_diff"]
        quality = "good" if md < 0.08 else ("fair" if md < 0.15 else "poor")
        if quality == "poor":
            overall_quality = "poor"
        elif quality == "fair" and overall_quality == "good":
            overall_quality = "fair"
        diagnostics[feat] = {
            "target":   dev["target"],
            "actual":   dev["actual"],
            "max_diff": md,
            "quality":  quality,
        }
    diagnostics["_overall"] = overall_quality

    return {
        "sampling_spec":     spec.to_dict(),
        "selected_profiles": slim_profiles,
        "summary":           summary,
        "diagnostics":       diagnostics,
        "candidate_count":   candidate_count,
        "total_profiles":    total,
    }
