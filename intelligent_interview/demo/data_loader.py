import json
import os
from typing import Any, Dict, List, Optional

DEMO_DIR = os.path.abspath(os.path.dirname(__file__))
EXAMPLES_DIR = os.path.join(DEMO_DIR, "examples")
LIVE_MANIFEST_PATH = os.path.join(EXAMPLES_DIR, "live", "manifest.json")
PRESET_MANIFEST_PATH = os.path.join(EXAMPLES_DIR, "preset", "manifest.json")
WEB_QUESTIONNAIRE_DIR = os.path.join(DEMO_DIR, "web_data", "questionnaires")
WEB_QUESTIONNAIRE_MANIFEST = os.path.join(WEB_QUESTIONNAIRE_DIR, "manifest.json")


def _resolve_output_dirs() -> List[str]:
    # 软编码：优先环境变量，其次常见相对目录
    env_val = os.environ.get("DEMO_OUTPUT_DIRS", "").strip()
    if env_val:
        return [os.path.abspath(p.strip()) for p in env_val.split(",") if p.strip()]
    candidates = [
        os.path.join(DEMO_DIR, "outputs_v2"),
        os.path.join(DEMO_DIR, "outputs_02"),
        os.path.join(DEMO_DIR, "outputs_01"),
        os.path.join(DEMO_DIR, "outputs"),
        os.path.abspath(os.path.join(DEMO_DIR, "..", "outputs_v2")),
        os.path.abspath(os.path.join(DEMO_DIR, "..", "outputs_02")),
        os.path.abspath(os.path.join(DEMO_DIR, "..", "outputs_01")),
        os.path.abspath(os.path.join(DEMO_DIR, "..", "outputs")),
    ]
    # 保持顺序并去重
    seen = set()
    out: List[str] = []
    for c in candidates:
        ac = os.path.abspath(c)
        if ac in seen:
            continue
        seen.add(ac)
        out.append(ac)
    return out


OUTPUT_DIRS = _resolve_output_dirs()


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_manifest(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_example_bundle(example_dir: str) -> Dict[str, Any]:
    return {
        "example_dir": example_dir,
        "questionnaire": read_json(os.path.join(example_dir, "questionnaire.json")),
        "ground_truth": read_json(os.path.join(example_dir, "ground_truth.json")),
        "persona": read_json(os.path.join(example_dir, "persona.json")),
        "scenario_plan": read_json(os.path.join(example_dir, "scenario_plan.json")),
        "meta": read_json(os.path.join(example_dir, "meta.json")),
    }


def _resolve_example_items(manifest: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    items = manifest.get("examples", [])
    base = os.path.join(EXAMPLES_DIR, kind)
    for item in items:
        rel = item.get("dir")
        if not rel:
            continue
        example_dir = os.path.abspath(os.path.join(base, rel))
        bundle = load_example_bundle(example_dir)
        questionnaire_path = item.get("questionnaire_path") or os.path.join(example_dir, "questionnaire.json")
        questionnaire_only_path = item.get("questionnaire_only_path") or os.path.join(example_dir, "questionnaire_only.json")
        benchmark_dir = item.get("benchmark_dir", "")
        if benchmark_dir and not os.path.isabs(benchmark_dir):
            benchmark_dir = os.path.abspath(os.path.join(DEMO_DIR, benchmark_dir))
        if questionnaire_path and not os.path.isabs(questionnaire_path):
            questionnaire_path = os.path.abspath(os.path.join(DEMO_DIR, questionnaire_path))
        if questionnaire_only_path and not os.path.isabs(questionnaire_only_path):
            questionnaire_only_path = os.path.abspath(os.path.join(DEMO_DIR, questionnaire_only_path))
        out.append(
            {
                "id": item.get("id", rel),
                "display_name": item.get("display_name", rel),
                "topic": item.get("topic"),
                "default": bool(item.get("default", False)),
                "example_dir": example_dir,
                "benchmark_dir": os.path.abspath(benchmark_dir) if benchmark_dir else "",
                "source_type": item.get("source_type", "questionnaire_json"),
                "questionnaire_path": questionnaire_path,
                "questionnaire_only_path": questionnaire_only_path,
                "questionnaire": bundle.get("questionnaire"),
                "ground_truth": bundle.get("ground_truth"),
                "persona": bundle.get("persona"),
                "scenario_plan": bundle.get("scenario_plan"),
                "meta": bundle.get("meta"),
            }
        )
    return out


def load_live_examples() -> List[Dict[str, Any]]:
    manifest = load_manifest(LIVE_MANIFEST_PATH)
    return _resolve_example_items(manifest, "live")


def load_live_questionnaires() -> List[Dict[str, Any]]:
    manifest = load_manifest(WEB_QUESTIONNAIRE_MANIFEST)
    items = manifest.get("questionnaires", [])
    out: List[Dict[str, Any]] = []
    for item in items:
        rel = item.get("path")
        if not rel:
            continue
        abs_path = rel if os.path.isabs(rel) else os.path.abspath(os.path.join(DEMO_DIR, rel))
        q = read_json(abs_path)
        if not q:
            continue
        out.append(
            {
                "id": item.get("id", os.path.splitext(os.path.basename(abs_path))[0]),
                "direction": item.get("direction") or q.get("survey_name", "访谈问卷"),
                "default": bool(item.get("default", False)),
                "questionnaire_path": abs_path,
                "questionnaire": q,
            }
        )
    if out:
        return out

    # manifest 缺失时，按目录自动兜底（仅 web_data/questionnaires）
    if not os.path.isdir(WEB_QUESTIONNAIRE_DIR):
        return []
    for fn in sorted(os.listdir(WEB_QUESTIONNAIRE_DIR)):
        if not fn.endswith(".json") or fn == "manifest.json":
            continue
        abs_path = os.path.join(WEB_QUESTIONNAIRE_DIR, fn)
        q = read_json(abs_path)
        if not q or "questions" not in q:
            continue
        out.append(
            {
                "id": os.path.splitext(fn)[0],
                "direction": q.get("survey_name", os.path.splitext(fn)[0]),
                "default": len(out) == 0,
                "questionnaire_path": abs_path,
                "questionnaire": q,
            }
        )
    return out


def load_preset_examples() -> List[Dict[str, Any]]:
    manifest = load_manifest(PRESET_MANIFEST_PATH)
    return _resolve_example_items(manifest, "preset")


def load_conversation_records() -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for d in OUTPUT_DIRS:
        merged.extend(_load_jsonl(os.path.join(d, "conversation_result.jsonl")))
    return merged


def load_questionnaire_records() -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for d in OUTPUT_DIRS:
        merged.extend(_load_jsonl(os.path.join(d, "questionnaire_result.jsonl")))
    return merged


def load_score_records() -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for d in OUTPUT_DIRS:
        merged.extend(_load_jsonl(os.path.join(d, "score_result.jsonl")))
    return merged


def build_preset_index() -> List[Dict[str, Any]]:
    example_items = load_preset_examples()
    if not example_items:
        return []
    example_bench_dirs = {os.path.abspath(x.get("benchmark_dir", "")) for x in example_items if x.get("benchmark_dir")}
    example_bench_names = {os.path.basename(x) for x in example_bench_dirs}

    convs = load_conversation_records()
    scores = load_score_records()
    qrs = load_questionnaire_records()

    def _in_examples(path: str) -> bool:
        ap = os.path.abspath(path or "")
        if ap in example_bench_dirs:
            return True
        return os.path.basename(ap) in example_bench_names

    convs = [c for c in convs if _in_examples(c.get("benchmark_dir", ""))]
    scores = [s for s in scores if _in_examples(s.get("benchmark_dir", ""))]
    qrs = [q for q in qrs if _in_examples(q.get("benchmark_dir", ""))]

    q_map: Dict[tuple, Dict[str, Any]] = {}
    for q in qrs:
        key = (
            q.get("benchmark_dir"),
            q.get("interviewer_model"),
            q.get("handling_mode", "default"),
            q.get("ablation_mode", "direct"),
        )
        q_map[key] = q

    s_map: Dict[tuple, Dict[str, Any]] = {}
    for s in scores:
        key = (
            s.get("benchmark_dir"),
            s.get("interviewer_model"),
            s.get("handling_mode", "default"),
            s.get("ablation_mode", "direct"),
        )
        s_map[key] = s

    out: List[Dict[str, Any]] = []
    item_by_bench = {os.path.abspath(x.get("benchmark_dir", "")): x for x in example_items}
    item_by_bench_name = {os.path.basename(k): v for k, v in item_by_bench.items()}
    for c in convs:
        bench_abs = os.path.abspath(c.get("benchmark_dir", ""))
        key = (
            c.get("benchmark_dir"),
            c.get("interviewer_model"),
            c.get("handling_mode", "default"),
            c.get("ablation_mode", "direct"),
        )
        out.append(
            {
                "survey_name": c.get("survey_name"),
                "benchmark_dir": c.get("benchmark_dir"),
                "interviewer_model": c.get("interviewer_model"),
                "interviewee_model": c.get("interviewee_model"),
                "handling_mode": c.get("handling_mode", "default"),
                "ablation_mode": c.get("ablation_mode", "direct"),
                "used_turns": c.get("used_turns"),
                "event_total": c.get("event_total", 0),
                "conversation": c,
                "questionnaire": q_map.get(key),
                "score": s_map.get(key),
                "example_meta": item_by_bench.get(bench_abs, item_by_bench_name.get(os.path.basename(bench_abs), {})),
            }
        )
    # 保持示例顺序稳定，优先按 manifest 的顺序
    if example_items:
        order = {os.path.abspath(x.get("benchmark_dir", "")): i for i, x in enumerate(example_items)}
        out.sort(key=lambda r: order.get(os.path.abspath(r.get("benchmark_dir", "")), 10**9))
    return out


def read_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_benchmark_assets(benchmark_dir: str) -> Dict[str, Any]:
    return {
        "questionnaire": read_json(os.path.join(benchmark_dir, "questionnaire.json")),
        "ground_truth": read_json(os.path.join(benchmark_dir, "ground_truth.json")),
        "persona": read_json(os.path.join(benchmark_dir, "persona.json")),
        "scenario_plan": read_json(os.path.join(benchmark_dir, "scenario_plan.json")),
        "meta": read_json(os.path.join(benchmark_dir, "meta.json")),
    }
