#!/usr/bin/env python3
"""Convert filtered Weibo user JSONL records into platform profile schema."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


TOPIC_RULES = [
    ("影视娱乐", ["剧", "演员", "热播", "角色", "追剧", "综艺", "电影", "剧集"]),
    ("游戏电竞", ["游戏", "电竞", "回合制", "开黑", "上分", "比赛", "战队"]),
    ("摄影修图", ["摄影", "拍照", "修图", "镜头", "相机", "照片"]),
    ("时尚穿搭", ["穿搭", "ootd", "时尚", "妆", "口红", "衣服", "美甲"]),
    ("音乐演出", ["音乐", "演唱会", "歌", "乐队", "专辑", "live"]),
    ("社会热点", ["民调", "总统", "经济", "社会", "新闻", "时事", "关税"]),
    ("体育赛事", ["篮球", "足球", "羽毛球", "比赛", "冠军", "体育"]),
    ("美食生活", ["好吃", "火锅", "咖啡", "奶茶", "吃", "餐厅", "做饭"]),
    ("动漫二次元", ["动漫", "动画", "二次元", "漫画", "番", "角色"]),
    ("旅行出行", ["旅行", "旅游", "出发", "周末", "城市", "海边", "机场"]),
    ("宠物动物", ["猫", "狗", "宠物", "小猫", "小狗"]),
    ("校园学习", ["考研", "上课", "学校", "老师", "论文", "学习", "实验"]),
]

OCCUPATION_RULES = [
    ("摄影相关从业者", ["摄影", "修图", "拍照"]),
    ("电竞相关从业者", ["电竞", "赛事"]),
    ("学生", ["学生", "大学", "研究生", "本科", "考研", "保研"]),
    ("内容创作者", ["博主", "分享", "日常记录", "vlog"]),
    ("媒体评论用户", ["时评", "评论", "观察"]),
]

MALE_VALUES = {"male", "m", "男"}
FEMALE_VALUES = {"female", "f", "女"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert filtered Weibo users into platform profile schema."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "/Users/duanfeiyu/Documents/Fudan-DISC/Individual_Simulation_Platform_old/users/"
            "human_like_users_with_posts.filtered.post_count_2_5.final.jsonl"
        ),
        help="Input JSONL file.",
    )
    parser.add_argument(
        "--student-profiles",
        type=Path,
        default=Path(
            "/Users/duanfeiyu/Documents/Fudan-DISC/Individual_Simulation_Platform/backend/users/"
            "student_profiles.json"
        ),
        help="Existing platform profiles JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/Users/duanfeiyu/Documents/Fudan-DISC/Individual_Simulation_Platform/backend/users/"
            "weibo_profiles.json"
        ),
        help="Output converted profile list.",
    )
    parser.add_argument(
        "--merged-output",
        type=Path,
        default=Path(
            "/Users/duanfeiyu/Documents/Fudan-DISC/Individual_Simulation_Platform/backend/users/"
            "student_profiles_expanded.json"
        ),
        help="Output merged profile list.",
    )
    return parser.parse_args()


def normalize_gender(value: str | None) -> str:
    if not value:
        return "unknown"
    norm = str(value).strip().lower()
    if norm in FEMALE_VALUES:
        return "female"
    if norm in MALE_VALUES:
        return "male"
    return "unknown"


def compose_location(profile: dict) -> str:
    for key in ("location",):
        value = profile.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != "其他":
            return value.strip()

    province = str(profile.get("province", "")).strip()
    city = str(profile.get("city", "")).strip()
    combined = " ".join(part for part in (province, city) if part and part != "其他")
    if combined:
        return combined

    ip_location = str(profile.get("ip_location", "")).strip()
    return ip_location if ip_location and ip_location != "其他" else "未知"


def detect_interests(bio: str, posts: list[dict]) -> list[str]:
    corpus = " ".join(
        [bio]
        + [post.get("content", "") for post in posts if isinstance(post.get("content"), str)]
    )
    scored: list[tuple[str, int]] = []
    for label, keywords in TOPIC_RULES:
        score = sum(1 for keyword in keywords if keyword.lower() in corpus.lower())
        if score:
            scored.append((label, score))

    scored.sort(key=lambda item: (-item[1], item[0]))
    interests = [label for label, _ in scored[:5]]
    return interests or ["社交平台内容", "日常表达"]


def infer_occupation(raw_profile: dict, bio: str, interests: list[str]) -> str:
    lowered = bio.lower()
    for label, keywords in OCCUPATION_RULES:
        if any(keyword.lower() in lowered for keyword in keywords):
            return label

    user_type = str(raw_profile.get("user_type", "")).strip()
    if user_type == "content_author":
        return "内容创作者"
    if user_type == "comment_author":
        return "评论互动用户"
    if user_type == "forward_author":
        return "信息转发用户"
    if "校园学习" in interests:
        return "学生"
    return "普通用户"


def infer_personality(raw_profile: dict, bio: str, interests: list[str], posts: list[dict]) -> str:
    parts: list[str] = []

    if bio:
        parts.append(f"简介体现出较强的个人表达意愿，关注{ '、'.join(interests[:2]) }。")
    else:
        parts.append(f"发言主题集中在{ '、'.join(interests[:2]) }，有稳定的表达偏好。")

    content_lengths = [
        len(post.get("content", ""))
        for post in posts
        if isinstance(post.get("content"), str)
    ]
    avg_length = sum(content_lengths) / len(content_lengths) if content_lengths else 0
    if avg_length >= 30:
        parts.append("表达相对完整，倾向于直接陈述观点或情绪。")
    else:
        parts.append("表达偏简洁，更接近日常即时分享。")

    user_type = str(raw_profile.get("user_type", "")).strip()
    if user_type == "content_author":
        parts.append("主动输出内容，具备一定分享欲和话题组织能力。")
    elif user_type == "comment_author":
        parts.append("更偏向互动式表达，重视参与感和即时反馈。")
    elif user_type == "forward_author":
        parts.append("倾向借助平台内容参与讨论，关注社交场域中的信息流动。")

    return "".join(parts)


def infer_initial_needs(raw_profile: dict) -> dict[str, float]:
    user_type = str(raw_profile.get("user_type", "")).strip()
    social = 0.60 if user_type in {"content_author", "forward_author"} else 0.52
    return {
        "satiety": 0.70,
        "energy": 0.78,
        "safety": 0.88,
        "social": social,
    }


def build_sample_posts(posts: list[dict], location: str) -> list[dict]:
    sample_posts: list[dict] = []
    for post in posts[:5]:
        content = (post.get("content") or "").strip()
        if not content:
            continue
        sample_posts.append(
            {
                "title": content[:30],
                "content": content,
                "created_at": post.get("created_at", ""),
                "location": location,
            }
        )
    return sample_posts


def extract_age(bio: str) -> int:
    match = re.search(r"([1-9]\d)岁", bio)
    if match:
        age = int(match.group(1))
        if 10 <= age <= 80:
            return age
    return 0


def convert_record(record: dict) -> dict:
    raw_profile = record.get("user_profile", {})
    posts = record.get("posts", [])
    bio = str(raw_profile.get("bio", "") or "").strip()
    location = compose_location(raw_profile)
    interests = detect_interests(bio, posts)
    converted = {
        "user_id": str(raw_profile.get("user_id", "")),
        "name": raw_profile.get("display_name") or raw_profile.get("username") or "未知用户",
        "gender": normalize_gender(raw_profile.get("gender")),
        "mbti": "",
        "age": extract_age(bio),
        "occupation": infer_occupation(raw_profile, bio, interests),
        "major": "未知",
        "personality": infer_personality(raw_profile, bio, interests, posts),
        "interests": interests,
        "location": location,
        "bio": bio,
        "initial_needs": infer_initial_needs(raw_profile),
        "sample_posts": build_sample_posts(posts, location),
        "source_platform": "weibo",
        "metadata": {
            "verified": raw_profile.get("verified", ""),
            "verified_type": raw_profile.get("verified_type", ""),
            "followers_count": int(raw_profile.get("followers_count", 0) or 0),
            "following_count": int(raw_profile.get("following_count", 0) or 0),
            "posts_count": int(raw_profile.get("posts_count", 0) or 0),
            "favorites_count": int(raw_profile.get("favorites_count", 0) or 0),
            "user_type": raw_profile.get("user_type", ""),
            "source_mobile": raw_profile.get("source_mobile", ""),
            "registration_time": raw_profile.get("registration_time", ""),
            "last_published": raw_profile.get("last_published", ""),
            "influence_score": float(raw_profile.get("influence_score", 0) or 0),
            "post_count": record.get("post_count", 0),
            "ground_truth_post_count": record.get("ground_truth_post_count", 0),
            "total_post_count": record.get("total_post_count", 0),
        },
        "source_profile": raw_profile,
    }
    return converted


def summarize_profiles(profiles: list[dict]) -> dict[str, object]:
    genders = Counter(profile.get("gender", "unknown") for profile in profiles)
    occupations = Counter(profile.get("occupation", "") for profile in profiles)
    return {
        "count": len(profiles),
        "genders": dict(genders),
        "top_occupations": occupations.most_common(10),
    }


def main() -> None:
    args = parse_args()

    converted_profiles: list[dict] = []
    with args.input.open("r", encoding="utf-8") as src:
        for line in src:
            if not line.strip():
                continue
            converted_profiles.append(convert_record(json.loads(line)))

    args.output.write_text(
        json.dumps(converted_profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    existing_profiles = json.loads(args.student_profiles.read_text("utf-8"))
    merged_profiles = existing_profiles + converted_profiles
    args.merged_output.write_text(
        json.dumps(merged_profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"input: {args.input}")
    print(f"converted_output: {args.output}")
    print(f"merged_output: {args.merged_output}")
    print(json.dumps(summarize_profiles(converted_profiles), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
