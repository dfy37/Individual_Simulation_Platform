"""
画像特征工程：将原始 profile 字段映射为 IPF 可处理的离散化分类特征。

输出的特征 bucket 字段：
  gender          male / female / unknown
  age_bucket      le20 / 21to23 / 24to26 / 27plus / unknown
  education       undergrad / master / phd / unknown
  major_bucket    math_cs / econ_mgmt / humanities_social / medicine_life / arts_media / other
  activity        high / medium / low
  interest_bucket fashion / sports / academics / campus / entertainment / food_travel / other
"""

import re
from typing import Any


# ── 分类规则 ──────────────────────────────────────────────────────────────────

def _gender(p: dict) -> str:
    g = (p.get("gender") or "").lower()
    if g in ("male", "m", "男"):
        return "male"
    if g in ("female", "f", "女"):
        return "female"
    return "unknown"


def _age_bucket(p: dict) -> str:
    age = p.get("age")
    try:
        age = int(age)
    except (TypeError, ValueError):
        return "unknown"
    if age <= 20:
        return "le20"
    if age <= 23:
        return "21to23"
    if age <= 26:
        return "24to26"
    return "27plus"


def _education(p: dict) -> str:
    occ = (p.get("occupation") or "").lower()
    bio = (p.get("bio") or "").lower()
    text = occ + " " + bio
    if "博士" in text or "phd" in text:
        return "phd"
    if "硕士" in text or "研究生" in text or "master" in text:
        return "master"
    if "本科" in text or "undergraduate" in text or "学生" in text:
        return "undergrad"
    return "unknown"


_MAJOR_PATTERNS: list[tuple[str, list[str]]] = [
    ("math_cs",           ["数学", "计算机", "软件", "人工智能", "信息", "统计", "physics", "数据"]),
    ("econ_mgmt",         ["经济", "管理", "金融", "会计", "商", "finance", "business"]),
    ("humanities_social", ["历史", "哲学", "文学", "社会", "政治", "法律", "新闻", "传播"]),
    ("medicine_life",     ["医学", "生命", "生物", "药学", "临床", "护理"]),
    ("arts_media",        ["艺术", "设计", "音乐", "影视", "媒体", "传媒", "美术"]),
]

def _major_bucket(p: dict) -> str:
    major = (p.get("major") or "").lower()
    occ   = (p.get("occupation") or "").lower()
    text  = major + " " + occ
    for bucket, keywords in _MAJOR_PATTERNS:
        if any(kw in text for kw in keywords):
            return bucket
    return "other"


def _activity(p: dict) -> str:
    """基于 sample_posts 数量、bio 长度、interests 数量估计活跃度。"""
    posts    = len(p.get("sample_posts") or [])
    bio_len  = len(p.get("bio") or "")
    interests = len(p.get("interests") or [])
    score = posts * 2 + (bio_len // 30) + interests
    if score >= 10:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


_INTEREST_PATTERNS: list[tuple[str, list[str]]] = [
    ("fashion",       ["穿搭", "时尚", "美妆", "护肤", "搭配"]),
    ("sports",        ["运动", "健身", "篮球", "足球", "跑步", "游泳", "户外"]),
    ("academics",     ["学习", "科研", "读书", "文献", "考研", "论文"]),
    ("campus",        ["校园", "宿舍", "食堂", "社团", "班级", "志愿"]),
    ("entertainment", ["音乐", "追剧", "游戏", "动漫", "电影", "演唱会"]),
    ("food_travel",   ["美食", "旅行", "旅游", "探店", "打卡"]),
]

def _interest_bucket(p: dict) -> str:
    interests = " ".join(p.get("interests") or []).lower()
    bio       = (p.get("bio") or "").lower()
    text      = interests + " " + bio
    for bucket, keywords in _INTEREST_PATTERNS:
        if any(kw in text for kw in keywords):
            return bucket
    return "other"


# ── 公开入口 ─────────────────────────────────────────────────────────────────

FEATURE_NAMES = ["gender", "age_bucket", "education", "major_bucket", "activity", "interest_bucket"]


def extract_features(profile: dict) -> dict[str, str]:
    """为单个 profile 提取所有分类特征，返回 {feature_name: bucket_value}。"""
    return {
        "gender":         _gender(profile),
        "age_bucket":     _age_bucket(profile),
        "education":      _education(profile),
        "major_bucket":   _major_bucket(profile),
        "activity":       _activity(profile),
        "interest_bucket": _interest_bucket(profile),
    }


def extract_features_batch(profiles: list[dict]) -> list[dict[str, str]]:
    """批量提取特征，与 profiles 列表一一对应。"""
    return [extract_features(p) for p in profiles]
