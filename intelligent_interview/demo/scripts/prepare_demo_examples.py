import json
import os
import shutil
from typing import Dict, List

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
DEMO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DEFAULT_BENCH_DIR = os.path.abspath(os.path.join(DEMO_DIR, "..", "benchmarks"))
BENCH_DIR = os.path.abspath(os.environ.get("DEMO_BENCH_DIR", DEFAULT_BENCH_DIR))
EXAMPLES_DIR = os.path.join(DEMO_DIR, "examples")
WEB_DATA_DIR = os.path.join(DEMO_DIR, "web_data", "questionnaires")

SOURCE_SPECS = [
    {
        "id": "qualitative_001_clean",
        "display_name": "定性开放认知访谈（001_clean）",
        "topic": "定性访谈",
        "benchmark_name": "定性数据开放认知访谈-14_survey_0bf697f6_001_clean",
        "default": True,
    },
    {
        "id": "time_management_001_clean",
        "display_name": "时间管理访谈（001_clean）",
        "topic": "时间管理",
        "benchmark_name": "international_students_international_students_time_management_questionnaire_001_clean",
        "default": False,
    },
]


def _copy_benchmark(dst_root: str, spec: Dict[str, str]) -> Dict[str, str]:
    src_dir = os.path.join(BENCH_DIR, spec["benchmark_name"])
    if not os.path.exists(src_dir):
        raise FileNotFoundError(f"Benchmark not found: {src_dir}")
    dst_dir = os.path.join(dst_root, spec["id"])
    os.makedirs(dst_root, exist_ok=True)
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
    questionnaire_src = os.path.join(src_dir, "questionnaire.json")
    questionnaire_dst = os.path.join(dst_dir, "questionnaire_only.json")
    if os.path.exists(questionnaire_src):
        shutil.copy2(questionnaire_src, questionnaire_dst)
        os.makedirs(WEB_DATA_DIR, exist_ok=True)
        shutil.copy2(questionnaire_src, os.path.join(WEB_DATA_DIR, f"{spec['id']}.questionnaire.json"))
        # 额外保存“方向级”问卷文件，供 live 模块直接使用
        if spec["id"].startswith("qualitative"):
            shutil.copy2(questionnaire_src, os.path.join(WEB_DATA_DIR, "qualitative.questionnaire.json"))
        if spec["id"].startswith("time_management"):
            shutil.copy2(questionnaire_src, os.path.join(WEB_DATA_DIR, "time_management.questionnaire.json"))
    return {
        "id": spec["id"],
        "display_name": spec["display_name"],
        "topic": spec["topic"],
        "dir": spec["id"],
        "default": bool(spec.get("default", False)),
        # 软编码：优先相对路径，便于整体迁移
        "benchmark_dir": os.path.join("benchmarks", spec["benchmark_name"]),
        "source_type": "questionnaire_json",
        "questionnaire_path": os.path.join("examples", os.path.basename(dst_root), spec["id"], "questionnaire.json"),
        "questionnaire_only_path": os.path.join("examples", os.path.basename(dst_root), spec["id"], "questionnaire_only.json"),
    }


def _write_manifest(path: str, examples: List[Dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"examples": examples}, f, ensure_ascii=False, indent=2)


def _write_questionnaire_manifest() -> None:
    os.makedirs(WEB_DATA_DIR, exist_ok=True)
    payload = {
        "questionnaires": [
            {
                "id": "qualitative",
                "direction": "定性开放获取态度与实践",
                "path": "web_data/questionnaires/qualitative.questionnaire.json",
                "default": True,
            },
            {
                "id": "time_management",
                "direction": "国际学生时间管理",
                "path": "web_data/questionnaires/time_management.questionnaire.json",
                "default": False,
            },
        ]
    }
    with open(os.path.join(WEB_DATA_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    live_root = os.path.join(EXAMPLES_DIR, "live")
    preset_root = os.path.join(EXAMPLES_DIR, "preset")
    live_manifest_path = os.path.join(live_root, "manifest.json")
    preset_manifest_path = os.path.join(preset_root, "manifest.json")

    live_items = []
    preset_items = []
    for spec in SOURCE_SPECS:
        live_items.append(_copy_benchmark(live_root, spec))
        preset_items.append(_copy_benchmark(preset_root, spec))

    _write_manifest(live_manifest_path, live_items)
    _write_manifest(preset_manifest_path, preset_items)
    _write_questionnaire_manifest()
    print(f"Prepared live examples: {live_root}")
    print(f"Prepared preset examples: {preset_root}")
    print(f"Prepared questionnaire-only data: {WEB_DATA_DIR}")


if __name__ == "__main__":
    main()
