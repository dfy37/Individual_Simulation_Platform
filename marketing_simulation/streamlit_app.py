import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path

import pandas as pd
import streamlit as st

APP_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = APP_ROOT.parent
DATA_ROOT = APP_ROOT / "data"


def resolve_sim_data_dir() -> Path:
    env_override = os.getenv("MARS_SIMULATION_DIR") or os.getenv("MARS_SIM_DATA_DIR")
    if env_override:
        override_path = Path(env_override).expanduser()
        if override_path.exists():
            return override_path.resolve()

    default_sim_dir = APP_ROOT / "simulation"
    if (default_sim_dir / "oasis_test_grouping.py").exists():
        return default_sim_dir.resolve()

    # Legacy layout: marketing assets under APP_ROOT/marketing
    legacy_marketing_dir = APP_ROOT / "marketing"
    if (legacy_marketing_dir / "oasis_test_grouping.py").exists():
        return legacy_marketing_dir.resolve()

    # Legacy layout fallback: APP_ROOT/code/{marketing,simulation_process}
    code_dir = APP_ROOT / "code"
    preferred = ["marketing", "simulation_process"]
    for folder in preferred:
        candidate = code_dir / folder
        if (candidate / "oasis_test_grouping.py").exists():
            return candidate.resolve()

    script_match = next((path.parent for path in code_dir.glob("*/oasis_test_grouping.py")), None)
    if script_match is not None:
        return script_match.resolve()

    # Fall back to the simulation directory even if the script is missing so the UI can prompt the user.
    return default_sim_dir.resolve()


def resolve_path_from_env(env_key: str, fallback: Path) -> Path:
    override = os.getenv(env_key)
    if override:
        return Path(override).expanduser()
    return fallback


def prefer_primary_file(file_name: str, primary_dir: Path, secondary_dir: Path) -> Path:
    primary_candidate = primary_dir / file_name
    if primary_candidate.exists() or primary_dir.exists():
        return primary_candidate
    return secondary_dir / file_name
SIM_DATA_DIR = resolve_sim_data_dir()
SIM_SCRIPT = SIM_DATA_DIR / "oasis_test_grouping.py"

DEFAULT_PROFILE_PATH = resolve_path_from_env(
    "MARS_PROFILE_PATH",
    prefer_primary_file("oasis_agent_init.csv", DATA_ROOT, SIM_DATA_DIR),
)
DEFAULT_DB_PATH = resolve_path_from_env(
    "MARS_DB_PATH",
    prefer_primary_file("oasis_database.db", DATA_ROOT, SIM_DATA_DIR),
)
DEFAULT_INTERVENTION_PATH = resolve_path_from_env(
    "MARS_INTERVENTION_PATH",
    prefer_primary_file("intervention_messages.csv", SIM_DATA_DIR, DATA_ROOT),
)
DEFAULT_ATTITUDE_CONFIG = {
    "attitude_TNT": "Evaluate the user's sentiment towards TNT."
}
DEFAULT_ATTITUDE_JSON = json.dumps(DEFAULT_ATTITUDE_CONFIG, indent=2)
INTERVENTION_STORAGE_DIR = SIM_DATA_DIR / "interventions"
LOG_OUTPUT_DIR = SIM_DATA_DIR / "log"
SIM_ENV_FILE = resolve_path_from_env(
    "MARS_ENV_FILE",
    prefer_primary_file(".env", DATA_ROOT, SIM_DATA_DIR),
)


def read_env_file(path: Path) -> dict[str, str]:
    env_data: dict[str, str] = {}
    if not path.exists():
        return env_data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            env_data[key] = value
    return env_data


def persist_env_file(path: Path, updates: dict[str, str]) -> None:
    existing = read_env_file(path)
    mutated = False
    for key, raw_value in updates.items():
        value = raw_value.strip()
        if value:
            if existing.get(key) != value:
                existing[key] = value
                mutated = True
        else:
            if key in existing:
                existing.pop(key)
                mutated = True
    if not mutated:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fout:
        for key in sorted(existing.keys()):
            fout.write(f"{key}={existing[key]}\n")


def path_to_display(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def dual(en: str, zh: str) -> str:
    """Render bilingual copy."""
    return zh


INTERVENTION_TIPS_MD = """
**干预设计**
在这个表格里，你可以设计三种类型的市场干预，每一行代表一个具体操作，系统会在模拟过程中按计划触发：
- **广播攻势**：用脚本化话术抢占时间线，率先定调。
- **影响力赞助**：向创作者或 KOL 发放激励，让他们用熟悉语气改写剧情。
- **机器人注水**：随时孵化机器人账号，在需要声量时迅速撑场。
每个标签页里的行都会被拼接成 OASIS 可读取的 CSV，随填随用。
"""


ATTITUDE_TIPS_MD = """
**情绪雷达**
一次定义多个态度指标，系统会自动生成对应的 SQL 查询、提示词工具和报表挂钩点，帮助你在模拟过程中实时捕捉舆论风向。
"""

st.set_page_config(
    page_title=dual("MARS Social Marketing Simulation Console", "MARS 社交营销模拟控制台"),
    page_icon="🪐",
    layout="wide",
)

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

:root {
    --bg: #f4f5fb;
    --card: #ffffff;
    --accent: #0f766e;
    --accent-soft: #c7f5e8;
    --accent-dark: #0c4a3b;
    --muted: #5f6b7c;
    --border: #e4e7ec;
    --warning: #f0a04b;
    --gradient: linear-gradient(135deg, #0f766e 0%, #1f8b80 45%, #5fd1c5 100%);
    --shadow: 0 25px 55px rgba(15, 118, 110, 0.15);
}

html, body, [class*="css"], [data-testid="stAppViewContainer"], [data-testid="stMarkdownContainer"] {
    font-family: 'Space Grotesk', 'IBM Plex Sans', 'Segoe UI', sans-serif;
}

[data-testid="stAppViewContainer"] {
    background: radial-gradient(circle at 10% 20%, rgba(95, 209, 197, 0.25), transparent 55%),
                radial-gradient(circle at 80% 0%, rgba(15, 118, 110, 0.18), transparent 60%),
                var(--bg);
}

.hero-card {
    background: var(--card);
    border-radius: 28px;
    padding: 36px;
    box-shadow: var(--shadow);
    border: 1px solid rgba(255, 255, 255, 0.7);
    position: relative;
    overflow: hidden;
}

.hero-card::after {
    content: "";
    position: absolute;
    inset: 0;
    background: radial-gradient(circle at 70% 0%, rgba(255, 255, 255, 0.4), transparent 50%);
    pointer-events: none;
}

.hero-title {
    font-size: 46px;
    font-weight: 600;
    line-height: 1.1;
    background: var(--gradient);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
}

.hero-subtitle {
    font-size: 18px;
    color: var(--muted);
    max-width: 720px;
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    border-radius: 999px;
    background: rgba(15, 118, 110, 0.1);
    color: var(--accent-dark);
    font-size: 13px;
    font-weight: 500;
}

.section-card {
    background: rgba(255, 255, 255, 0.92);
    border-radius: 22px;
    padding: 26px;
    border: 1px solid rgba(15, 118, 110, 0.08);
    box-shadow: 0 10px 30px rgba(15, 118, 110, 0.08);
}

.section-title {
    font-size: 24px;
    font-weight: 600;
    color: var(--accent-dark);
    margin-bottom: 6px;
}

.section-subtitle {
    color: var(--muted);
    margin-bottom: 22px;
}

.feature-chip {
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(12, 74, 59, 0.06);
    border: 1px solid rgba(12, 74, 59, 0.15);
    margin-bottom: 12px;
}

.metric-card {
    border-radius: 16px;
    padding: 18px;
    border: 1px solid rgba(15, 118, 110, 0.15);
    background: rgba(255, 255, 255, 0.95);
}

.metric-label {
    color: var(--muted);
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}

.metric-value {
    font-size: 30px;
    font-weight: 600;
    color: var(--accent-dark);
}

.small-text {
    font-size: 13px;
    color: var(--muted);
    margin-top: 6px;
}

.log-box {
    border-radius: 16px;
    padding: 16px;
    background: #050505;
    color: #e4e7ec;
    font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
    font-size: 13px;
}

.highlight-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: rgba(240, 160, 75, 0.12);
    border-radius: 999px;
    color: var(--warning);
    font-weight: 500;
}

.footer-note {
    color: var(--muted);
    font-size: 13px;
    text-align: center;
    margin-top: 12px;
}
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


INTERVENTION_COLUMNS = ["strategy_id", "target_scope", "action_type", "payload", "step"]
INTERVENTION_DESIGN_COLUMNS = ["strategy_id", "target_scope", "payload", "step"]
INTERVENTION_EXPORT_COLUMNS = [
    "time_step",
    "intervention_type",
    "content",
    "target_group",
    "target_id",
    "ratio",
    "attitude_target",
    "user_profile",
    "strategy_id",
]
INTERVENTION_TYPE_CONFIGS = [
    {
        "key": "broadcast",
        "label": dual("Broadcast blasts", "广播攻势"),
        "story": dual(
            "Warm up the timeline with scripted headlines or campaign talking points so everyone sees the same spark.",
            "用脚本化标题或话术抢占时间线，先声夺人。",
        ),
        "defaults": [
            {
                "strategy_id": "launch_kol",
                "target_scope": "group:KOL",
                "payload": "{\"message\": \"Push TNT positive narrative\"}",
                "step": 0,
            }
        ],
    },
    {
        "key": "bribe",
        "label": dual("Branded payouts", "影响力赞助"),
        "story": dual(
            "Privately reward select cells so they remix your storyline with their own credibility.",
            "向核心圈层发放激励，让他们用可信口吻演绎剧情。",
        ),
        "defaults": [
            {
                "strategy_id": "reward_creators",
                "target_scope": "group:creator",
                "payload": "{\"value\": \"credits\", \"message\": \"Sponsor pro-brand takes\"}",
                "step": 0,
            }
        ],
    },
    {
        "key": "register",
        "label": dual("Bot registration", "机器人注水"),
        "story": dual(
            "Spin up sleeper accounts that can echo specific sentiments whenever you need extra volume.",
            "孵化能随时应声的机器人账号，随需撑起声量。",
        ),
        "defaults": [
            {
                "strategy_id": "ignite_supporters",
                "target_scope": "ratio:0.15",
                "payload": "{\"persona\": \"pro-brand insider\"}",
                "step": 1,
            }
        ],
    },
]

ACTION_TYPE_MAP = {
    "broadcast": "broadcast",
    "bribe": "bribery",
    "bribery": "bribery",
    "register": "register_user",
    "register_user": "register_user",
}


def normalize_intervention_type(raw_type: str) -> str:
    key = (raw_type or "").strip().lower()
    return ACTION_TYPE_MAP.get(key, key or "broadcast")


def parse_target_scope(scope: str) -> tuple[str, str, str]:
    scope = (scope or "").strip()
    if not scope:
        return "", "", ""
    tokens = re.split(r"[|;,]", scope)
    target_group = ""
    target_id = ""
    ratio = ""
    for token in tokens:
        item = token.strip()
        if not item:
            continue
        if ":" in item:
            key, value = item.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key in {"group", "target_group"}:
                target_group = value
            elif key in {"agent", "target", "target_id", "id"}:
                target_id = value.lstrip("@")
            elif key == "ratio":
                ratio = value
        else:
            if item.startswith("@") or item.isdigit():
                target_id = item.lstrip("@")
            else:
                target_group = item
    return target_group, target_id, ratio


def build_intervention_export(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=INTERVENTION_EXPORT_COLUMNS)

    rows: list[dict[str, str]] = []
    for record in df.to_dict("records"):
        target_group, target_id, ratio = parse_target_scope(str(record.get("target_scope", "") or ""))
        payload = str(record.get("payload", "") or "").strip()
        normalized_type = normalize_intervention_type(str(record.get("action_type", "") or ""))
        try:
            time_step = int(record.get("time_step") or record.get("step", 0))
        except (TypeError, ValueError):
            time_step = 0

        rows.append(
            {
                "time_step": time_step,
                "intervention_type": normalized_type,
                "content": payload,
                "target_group": target_group,
                "target_id": target_id,
                "ratio": ratio,
                "attitude_target": "",
                "user_profile": payload if normalized_type == "register_user" else "",
                "strategy_id": record.get("strategy_id", ""),
            }
        )

    return pd.DataFrame(rows, columns=INTERVENTION_EXPORT_COLUMNS)


def initial_attitude_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metric": key, "description": desc}
            for key, desc in DEFAULT_ATTITUDE_CONFIG.items()
        ],
        columns=["metric", "description"],
    )


def run_simulation(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    python_exec = env.get("OASIS_PYTHON_BIN", "/root/anaconda3/envs/oasis/bin/python")
    if not Path(python_exec).exists():
        python_exec = "python"
    return subprocess.run(
        [python_exec, str(SIM_SCRIPT)],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def hero_section():
    st.markdown(
        """
        <div class="hero-card">
            <span class="badge">MARS · OASIS 社交营销沙盒</span>
            <div class="hero-title">大规模社交营销干预压测面板</div>
            <div class="hero-subtitle">
                在投入真实预算前，用 MARS 引擎预演广告轰炸、达人买量与协同引导，实时观察情绪、触达与反制的演化路径。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def feature_section():
    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">能力亮点</div>
            <div class="section-subtitle">浓缩自 MARS 体系的五大支柱。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown(
            """
            <div class="feature-chip">
                <strong>混合代理体系</strong><br>
                一级 LLM 负责推理与叙事，二级启发式人群覆盖全国尺度的情绪水位。
            </div>
            <div class="feature-chip">
                <strong>分群与动态激活</strong><br>
                自动聚类受众并按概率触发，贴近真实活跃节奏。
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_b:
        st.markdown(
            """
            <div class="feature-chip">
                <strong>自定义评估指标</strong><br>
                一次定义态度指标，系统自动生成 SQL、提示词与报表挂钩点。
            </div>
            <div class="feature-chip">
                <strong>内心—行为闭环</strong><br>
                LLM 代理在发声前必须刷新态度工具，保证所想即所行。
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_c:
        st.markdown(
            """
            <div class="feature-chip">
                <strong>多模干预系统</strong><br>
                一张 CSV 统筹广播、赞助与机器人注水，可直接观察各杠杆如何折射舆论。
            </div>
            """,
            unsafe_allow_html=True,
        )


def intervention_designer() -> pd.DataFrame:
    st.markdown(f"#### {dual('Intervention Designer', '干预编排')}")
    st.markdown(INTERVENTION_TIPS_MD)

    if "intervention_maps" not in st.session_state:
        st.session_state["intervention_maps"] = {}
    maps: dict[str, pd.DataFrame] = st.session_state["intervention_maps"]

    for cfg in INTERVENTION_TYPE_CONFIGS:
        if cfg["key"] not in maps:
            maps[cfg["key"]] = pd.DataFrame(cfg["defaults"], columns=INTERVENTION_DESIGN_COLUMNS)

    column_config = {
        "strategy_id": st.column_config.TextColumn(
            label=dual("Strategy ID", "策略 ID"),
            help=dual("Name the play", "为操作命名，方便对照"),
        ),
        "target_scope": st.column_config.TextColumn(
            label=dual("Target Scope", "目标范围"),
            help=dual("agent_id, group:KOL, ratio:0.2", "支持 agent_id/group/ratio 等格式"),
        ),
        "payload": st.column_config.TextColumn(
            label=dual("Payload", "载荷"),
            help=dual("JSON or plain text instructions", "JSON 或纯文本指令"),
        ),
        "step": st.column_config.NumberColumn(
            label=dual("Step", "时间步"),
            min_value=0,
            format="%d",
            help=dual("Trigger time step (0-based)", "触发时间步 (从 0 开始)"),
        ),
    }

    tabs = st.tabs([cfg["label"] for cfg in INTERVENTION_TYPE_CONFIGS])
    for cfg, tab in zip(INTERVENTION_TYPE_CONFIGS, tabs):
        with tab:
            st.caption(cfg["story"])
            editor_df = st.data_editor(
                maps[cfg["key"]],
                column_config=column_config,
                num_rows="dynamic",
                use_container_width=True,
                key=f"intervention_editor_{cfg['key']}",
            )
            maps[cfg["key"]] = editor_df

    frames: list[pd.DataFrame] = []
    for cfg in INTERVENTION_TYPE_CONFIGS:
        df = maps.get(cfg["key"], pd.DataFrame(columns=INTERVENTION_DESIGN_COLUMNS))
        cleaned = df.dropna(how="all")
        if "strategy_id" in cleaned.columns:
            cleaned = cleaned[cleaned["strategy_id"].notnull()]
        if cleaned.empty:
            continue
        typed = cleaned.copy()
        if "step" in typed.columns:
            typed["step"] = typed["step"].fillna(0).astype(int)
        typed["action_type"] = cfg["key"]
        frames.append(typed[INTERVENTION_COLUMNS])

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=INTERVENTION_COLUMNS)
    st.session_state["intervention_df"] = combined
    return combined


def attitude_designer() -> pd.DataFrame:
    st.markdown(f"#### {dual('Attitude Metric Designer', '态度指标设计')}")
    st.markdown(ATTITUDE_TIPS_MD)
    if "attitude_df" not in st.session_state:
        st.session_state["attitude_df"] = initial_attitude_df()
    attitude_df = st.data_editor(
        st.session_state["attitude_df"],
        column_config={
            "metric": st.column_config.TextColumn(label=dual("Metric Key", "指标键"), help=dual("alpha_numeric key", "英文或下划线")),
            "description": st.column_config.TextColumn(label=dual("Description", "描述"), help=dual("Natural language guidance", "自然语言描述")),
        },
        num_rows="dynamic",
        use_container_width=True,
        key="attitude_editor",
    )
    st.session_state["attitude_df"] = attitude_df
    return attitude_df


def simulation_console():
    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">模拟控制室</div>
            <div class="section-subtitle">在这里配置路径、调整指标并一键发起 OASIS 模拟。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    env_defaults = read_env_file(SIM_ENV_FILE)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            """
            <div class="metric-card">
                <div class="metric-label">LLM 组别</div>
                <div class="metric-value">4</div>
                <div class="small-text">权威媒体、活跃 KOL、创作者、普通用户</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            """
            <div class="metric-card">
                <div class="metric-label">启发式簇</div>
                <div class="metric-value">1</div>
                <div class="small-text">大体量潜水群，负责背景情绪</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col3:
        st.markdown(
            """
            <div class="metric-card">
                <div class="metric-label">干预模式</div>
                <div class="metric-value">3</div>
                <div class="small-text">广播、赞助、机器人注册</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with st.form("simulation_form"):
        st.write(f"### {dual('Runtime Configuration', '运行配置')}")
        total_steps = st.slider(dual("Total simulation steps", "模拟总步数"), min_value=1, max_value=24, value=2)

        default_base_url = env_defaults.get("MARS_MODEL_BASE_URL") or os.getenv("MARS_MODEL_BASE_URL", "")
        default_api_key = env_defaults.get("MARS_MODEL_API_KEY") or os.getenv("MARS_MODEL_API_KEY", "")

        st.write("#### 模型服务入口")
        endpoint_col, key_col = st.columns(2)
        with endpoint_col:
            model_base_url = default_base_url
            st.text_input(
                "API 基础 URL",
                value=model_base_url,
                disabled=True,
                help="此地址由 run.sh 或环境变量 MARS_MODEL_BASE_URL 提供。",
            )
            if not model_base_url:
                st.caption("⚠️ 未检测到 API 地址，请先在 run.sh 中配置 MARS_MODEL_BASE_URL。")
        with key_col:
            model_api_key = default_api_key
            st.text_input(
                "API 密钥",
                value=model_api_key,
                type="password",
                disabled=True,
                help="该值由 run.sh 或环境变量 MARS_MODEL_API_KEY 提供。",
            )
            if not model_api_key:
                st.caption("⚠️ 未检测到 API 密钥，请先在 run.sh 中配置 MARS_MODEL_API_KEY。")
        st.caption(f"{dual('Stored at', '保存位置')} {path_to_display(SIM_ENV_FILE)}")

        intervention_df = intervention_designer()
        uploaded_intervention = st.file_uploader(
            dual("Upload intervention CSV (override)", "拖拽上传干预 CSV（覆盖）"),
            type="csv",
        )

        attitude_df = attitude_designer()
        metrics_upload = st.file_uploader(
            dual("Upload attitude JSON (override)", "拖拽上传态度 JSON（覆盖）"),
            type=["json", "txt"],
            key="metrics_uploader",
        )

        model_name = st.text_input(
            dual("OpenAI-compatible model name", "OpenAI 兼容模型名称"),
            value=os.getenv("MARS_MODEL_NAME", "gpt-4o-mini"),
            help=dual("Passed directly to OpenAICompatibleModel (e.g., gpt-4o-mini).", "直接传入 OpenAICompatibleModel，例如 gpt-4o-mini。"),
        )
        submitted = st.form_submit_button(dual("Launch Simulation", "启动模拟"), use_container_width=True)

    if not submitted:
        return

    if not model_base_url.strip() or not model_api_key.strip():
        st.error(dual("Please provide both API base URL and API key.", "请同时填写 API 基础 URL 和 API 密钥。"))
        return

    persist_env_file(
        SIM_ENV_FILE,
        {
            "MARS_MODEL_BASE_URL": model_base_url,
            "MARS_MODEL_API_KEY": model_api_key,
        },
    )

    try:
        if metrics_upload is not None:
            try:
                attitude_payload = metrics_upload.getvalue().decode("utf-8")
            except UnicodeDecodeError:
                st.error(dual("Uploaded attitude file must be UTF-8.", "上传的态度文件需为 UTF-8 编码。"))
                return
        else:
            attitude_records = attitude_df.dropna(subset=["metric"]).to_dict("records")
            if attitude_records:
                attitude_config = {
                    str(record["metric"]).strip(): str(record.get("description", "")).strip()
                    for record in attitude_records
                    if str(record["metric"]).strip()
                }
                attitude_payload = json.dumps(attitude_config, ensure_ascii=False)
            else:
                attitude_payload = DEFAULT_ATTITUDE_JSON

        attitude_config = json.loads(attitude_payload) if attitude_payload.strip() else DEFAULT_ATTITUDE_CONFIG
        if not isinstance(attitude_config, dict) or not attitude_config:
            raise ValueError("Attitude JSON must define an object with metric names.")
    except (json.JSONDecodeError, ValueError) as exc:
        st.error(f"{dual('Attitude config error', '态度配置错误')}: {exc}")
        return

    profile_path_resolved = DEFAULT_PROFILE_PATH
    db_path_resolved = DEFAULT_DB_PATH
    intervention_path_resolved = DEFAULT_INTERVENTION_PATH

    if uploaded_intervention is not None:
        INTERVENTION_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        upload_target = INTERVENTION_STORAGE_DIR / f"intervention_{int(time.time())}.csv"
        with open(upload_target, "wb") as fout:
            fout.write(uploaded_intervention.getbuffer())
        intervention_path_resolved = upload_target
        st.caption(f"{dual('Uploaded intervention saved to', '上传的干预文件已保存至')} {path_to_display(upload_target)}")
    else:
        cleaned_interventions = intervention_df.copy()
        cleaned_interventions = cleaned_interventions.dropna(how="all")
        cleaned_interventions = cleaned_interventions[
            cleaned_interventions["strategy_id"].notnull() & cleaned_interventions["action_type"].notnull()
        ]
        if not cleaned_interventions.empty:
            cleaned_interventions["step"] = cleaned_interventions["step"].fillna(0).astype(int)
            export_df = build_intervention_export(cleaned_interventions)
            INTERVENTION_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
            inline_target = INTERVENTION_STORAGE_DIR / f"intervention_designer_{int(time.time())}.csv"
            export_df.to_csv(inline_target, index=False)
            intervention_path_resolved = inline_target
            st.caption(f"{dual('Using designed interventions saved to', '使用设计的干预，已保存至')} {path_to_display(inline_target)}")

    model_name_clean = model_name.strip() or "gpt-4o-mini"

    overrides = {
        "MARS_PROFILE_PATH": str(profile_path_resolved),
        "MARS_DB_PATH": str(db_path_resolved),
        "MARS_INTERVENTION_PATH": str(intervention_path_resolved),
        "MARS_TOTAL_STEPS": str(total_steps),
        "MARS_ATTITUDE_CONFIG_JSON": json.dumps(attitude_config),
        "MARS_MODEL_NAME": model_name_clean,
        "MARS_MODEL_BASE_URL": model_base_url.strip(),
        "MARS_MODEL_API_KEY": model_api_key.strip(),
    }

    with st.spinner(dual("Simulating agents inside OASIS...", "正在 OASIS 中执行模拟...")):
        result = run_simulation(overrides)

    LOG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_target = LOG_OUTPUT_DIR / f"streamlit_run_{int(time.time())}.log"
    with open(log_target, "w", encoding="utf-8") as log_file:
        log_file.write("=== STDOUT ===\n")
        log_file.write(result.stdout or "")
        log_file.write("\n\n=== STDERR ===\n")
        log_file.write(result.stderr or "")

    db_display_path = path_to_display(db_path_resolved)
    log_display_path = path_to_display(log_target)

    if result.returncode == 0:
        st.success(f"{dual('Simulation completed. Inspect', '模拟完成，结果存放于')} {db_display_path}")
    else:
        st.error(dual("Simulation reported an error. Review the log below.", "模拟出现错误，请查看下方日志。"))
    st.caption(f"{dual('Run log saved to', '运行日志已保存到')} {log_display_path}")

    if result.stdout:
        st.markdown("#### 标准输出")
        st.code(result.stdout, language="text")
    if result.stderr:
        st.markdown("#### 标准错误")
        st.code(result.stderr, language="text")

    st.markdown(
        f"""
        <div class="highlight-pill">ℹ️ 可使用 `sqlitebrowser {db_display_path}` 快速浏览数据库。</div>
        """,
        unsafe_allow_html=True,
    )

    display_db_insights(db_path_resolved)


def how_it_works():
    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">流程速览</div>
            <div class="section-subtitle">快速回顾一次完整的市场干预推演节奏。</div>
            <ol>
                <li>明确活动目标、核心 KPI 与重点社交结构。</li>
                <li>配置态度指标与干预日程，定义实验杠杆。</li>
                <li>运行模拟，观察 LLM 与启发式群体的逐步反馈。</li>
                <li>回看轨迹、建议与态度曲线，评估增量与风险。</li>
                <li>调整杠杆再跑，形成投放前的对照基准。</li>
            </ol>
        </div>
        """,
        unsafe_allow_html=True,
    )


def display_db_insights(db_path: Path) -> None:
    st.markdown("### 数据库速览")
    if not db_path.exists():
        st.info(f"{dual('Database not found yet at', '数据库尚未生成，路径')} {path_to_display(db_path)}")
        return

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        st.error(f"{dual('Unable to open database', '无法打开数据库')}: {exc}")
        return

    try:
        tables_df = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", conn)
        table_names = tables_df['name'].tolist()

        with st.expander(dual("Latest simulated posts", "最新模拟帖子"), expanded=True):
            post_limit = st.number_input(
                dual("Rows to show", "展示条数"),
                min_value=5,
                max_value=200,
                value=20,
                step=5,
                key="post_rows_input",
            )
            if "post" in table_names:
                try:
                    post_cols_info = pd.read_sql_query("PRAGMA table_info(post)", conn)
                    post_cols = post_cols_info['name'].tolist()
                    preferred_cols = ["post_id", "agent_id", "user_id", "created_at", "content", "text"]
                    select_cols = [col for col in preferred_cols if col in post_cols]
                    select_clause = ", ".join(select_cols) if select_cols else "*"
                    order_clause = "ORDER BY datetime(created_at) DESC" if "created_at" in post_cols else "ORDER BY rowid DESC"
                    posts_df = pd.read_sql_query(
                        f"SELECT {select_clause} FROM post {order_clause} LIMIT ?",
                        conn,
                        params=(int(post_limit),),
                    )
                    st.dataframe(posts_df)
                except Exception as exc:
                    st.error(f"{dual('Failed to read post table', '读取 post 表失败')}: {exc}")
            else:
                st.info(dual("Table 'post' has not been populated yet.", "post 表尚未生成。"))

        with st.expander(dual("Attitude trajectories", "态度轨迹"), expanded=False):
            attitude_tables = [
                name
                for name in table_names
                if name.startswith("log_attitude_") or name.startswith("attitude_")
            ]
            if not attitude_tables:
                st.info(dual("No attitude tables detected yet. Run a simulation first.", "尚未检测到态度表，请先运行模拟。"))
            else:
                att_table = st.selectbox(dual("Choose log table", "选择态度日志表"), attitude_tables, key="att_table_select")
                att_limit = st.number_input(
                    dual("Rows to show (attitudes)", "态度展示条数"),
                    min_value=5,
                    max_value=200,
                    value=30,
                    step=5,
                    key="att_rows_input",
                )
                try:
                    att_df = pd.read_sql_query(
                        f"SELECT * FROM {att_table} ORDER BY time_step DESC LIMIT ?",
                        conn,
                        params=(int(att_limit),),
                    )
                    st.dataframe(att_df)
                except Exception as exc:
                    st.error(f"{dual('Failed to read table', '读取表失败')} {att_table}: {exc}")
    finally:
        conn.close()


def main():
    tab_welcome, tab_console = st.tabs([dual("Welcome", "欢迎"), dual("Simulation Console", "模拟面板")])
    with tab_welcome:
        hero_section()
        feature_section()
        how_it_works()
    with tab_console:
        simulation_console()
        st.markdown(
            """
            <p class="footer-note">若需复现，请保存上方的标准输出/错误日志，便于记录实验。</p>
            """,
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
