"""
IPF (Iterative Proportional Fitting) 采样实现。

流程：
  1. 对候选池构建特征矩阵（one-hot 形式）
  2. 依据 sampling_spec.marginals 构造目标边际分布
  3. 运行 IPF，得到每个 profile 的权重
  4. 按权重无放回抽样 target_size 个样本
  5. 评估实际分布与目标分布的偏差
"""

import logging
import random
from typing import Optional

from .schema import SamplingSpec

logger = logging.getLogger(__name__)

MAX_IPF_ITER  = 50
CONV_TOL      = 1e-4
MAX_RESAMPLE  = 5


# ── IPF 核心 ──────────────────────────────────────────────────────────────────

def _normalize(d: dict) -> dict:
    """将 dict 的 values 归一化为和为 1。"""
    total = sum(d.values())
    if total == 0:
        return {k: 1.0 / len(d) for k in d}
    return {k: v / total for k, v in d.items()}


def _run_ipf(
    feature_matrix: list[dict[str, str]],
    marginals: dict[str, dict[str, float]],
) -> list[float]:
    """
    对候选池执行 IPF，返回每个样本的归一化权重列表。

    Args:
        feature_matrix: 每个元素是 {feature_name: bucket_value}
        marginals:       目标边际分布 {feature_name: {bucket: proportion}}

    Returns:
        weights: 与 feature_matrix 等长，每个值为非负浮点数
    """
    n = len(feature_matrix)
    if n == 0:
        return []

    # 初始化均等权重
    weights = [1.0] * n

    # 归一化目标边际
    target = {feat: _normalize(dist) for feat, dist in marginals.items()}

    for iteration in range(MAX_IPF_ITER):
        max_delta = 0.0
        for feat, dist in target.items():
            for bucket, target_prop in dist.items():
                # 当前该 bucket 的权重之和
                bucket_weight = sum(
                    weights[i] for i, fm in enumerate(feature_matrix)
                    if fm.get(feat) == bucket
                )
                total_weight = sum(weights)
                if total_weight == 0:
                    break
                current_prop = bucket_weight / total_weight

                if current_prop == 0:
                    # 候选池中完全没有该 bucket，跳过（避免除零）
                    continue

                ratio = target_prop / current_prop
                delta = abs(ratio - 1.0)
                max_delta = max(max_delta, delta)

                # 对该 bucket 内的样本按比例缩放
                for i, fm in enumerate(feature_matrix):
                    if fm.get(feat) == bucket:
                        weights[i] *= ratio

        if max_delta < CONV_TOL:
            logger.debug(f"[ipf] 收敛于第 {iteration+1} 轮，max_delta={max_delta:.6f}")
            break
    else:
        logger.warning(f"[ipf] 达到最大迭代次数 {MAX_IPF_ITER}，可能未完全收敛")

    # 归一化为概率
    total = sum(weights)
    if total == 0:
        return [1.0 / n] * n
    return [w / total for w in weights]


# ── 加权无放回抽样 ────────────────────────────────────────────────────────────

def _weighted_sample_without_replacement(
    weights: list[float],
    k: int,
    rng: random.Random,
) -> list[int]:
    """
    按权重无放回抽样 k 个索引。
    使用 reservoir sampling（A-Chao 变体）。
    """
    n = len(weights)
    k = min(k, n)
    if k == 0:
        return []

    # 简单实现：用权重生成 key，取 top-k
    keys = [(rng.random() ** (1.0 / max(w, 1e-12)), i) for i, w in enumerate(weights)]
    keys.sort(reverse=True)
    return [i for _, i in keys[:k]]


# ── 偏差评估 ─────────────────────────────────────────────────────────────────

def _compute_deviation(
    selected_features: list[dict[str, str]],
    marginals: dict[str, dict[str, float]],
) -> dict[str, dict]:
    """
    计算已抽取样本的实际边际分布，以及与目标的偏差。

    Returns:
        {feature_name: {
            "target":   {bucket: proportion},
            "actual":   {bucket: proportion},
            "max_diff": float,
        }}
    """
    n = len(selected_features)
    if n == 0:
        return {}

    report = {}
    for feat, target_dist in marginals.items():
        target_norm = _normalize(target_dist)
        # 统计实际分布
        counts: dict[str, int] = {}
        for fm in selected_features:
            b = fm.get(feat, "unknown")
            counts[b] = counts.get(b, 0) + 1
        actual = {b: c / n for b, c in counts.items()}

        max_diff = max(
            abs(actual.get(b, 0.0) - target_norm.get(b, 0.0))
            for b in set(list(actual.keys()) + list(target_norm.keys()))
        )
        report[feat] = {
            "target":   target_norm,
            "actual":   actual,
            "max_diff": round(max_diff, 4),
        }
    return report


# ── 公开入口 ──────────────────────────────────────────────────────────────────

def ipf_sample(
    profiles: list[dict],
    feature_matrix: list[dict[str, str]],
    spec: SamplingSpec,
    seed: Optional[int] = None,
) -> dict:
    """
    对候选池执行 IPF 采样。

    Args:
        profiles:       已经过 hard_filter 的候选 profile 列表
        feature_matrix: 与 profiles 等长，每个元素是特征字典
        spec:           采样规格
        seed:           随机种子（可重现）

    Returns:
        {
            "selected_profiles": [...],
            "selected_features": [...],
            "weights":           [...],
            "deviation":         {...},
        }
    """
    rng = random.Random(seed)
    n = len(profiles)
    k = spec.target_size

    if n == 0:
        return {
            "selected_profiles": [],
            "selected_features": [],
            "weights": [],
            "deviation": {},
        }

    # 无 marginals 时退为均匀采样
    if not spec.marginals:
        indices = list(range(n))
        rng.shuffle(indices)
        sel = indices[:k]
        return {
            "selected_profiles": [profiles[i] for i in sel],
            "selected_features": [feature_matrix[i] for i in sel],
            "weights":           [1.0 / n] * len(sel),
            "deviation":         {},
        }

    # IPF + 重采样（取偏差最小的结果）
    best_result = None
    best_score  = float("inf")

    for attempt in range(MAX_RESAMPLE):
        weights  = _run_ipf(feature_matrix, spec.marginals)
        indices  = _weighted_sample_without_replacement(weights, k, rng)
        sel_feat = [feature_matrix[i] for i in indices]
        deviation = _compute_deviation(sel_feat, spec.marginals)
        score = sum(d["max_diff"] for d in deviation.values())

        if best_result is None or score < best_score:
            best_score  = score
            best_result = {
                "selected_profiles": [profiles[i] for i in indices],
                "selected_features": sel_feat,
                "weights":           [weights[i] for i in indices],
                "deviation":         deviation,
            }

        if best_score < 0.05:
            break

    logger.info(f"[ipf] 最终偏差得分={best_score:.4f}，重试 {attempt+1} 次")
    return best_result
