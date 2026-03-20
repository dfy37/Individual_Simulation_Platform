"""
simulation/oasis_sim.py

OASIS 仿真核心逻辑，从 oasis_test_grouping.py 重构而来。
被 online_sim.py 直接 import 并在独立线程中通过 asyncio.run() 调用。
"""
import asyncio
import logging
import os
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

# deepseek 是国内 API，不需要走代理；httpx trust_env=True 会自动读取系统代理，
# 导致通过 ClashX 等代理转发时长连接断开、请求挂死。
os.environ.setdefault("NO_PROXY", "api.deepseek.com")
os.environ.setdefault("no_proxy", "api.deepseek.com")

# 将本模块所在目录加入 sys.path，以便 import 同级文件
_SIM_DIR = Path(__file__).resolve().parent
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))

from camel.models import OpenAICompatibleModel
from attitude_annotator import _VLLMAttitudeAnnotator as APIAttitudeAnnotator
import oasis
from oasis import ActionType, LLMAction, HeuristicAction
from oasis import generate_and_register_agents
from oasis.social_agent import BaseAgent
from oasis.social_platform.typing import ActionType
from db_manager import reset_simulation_tables
from intervention_processor import InterventionProcessor

logger = logging.getLogger(__name__)

# ── 群体分层配置 ──────────────────────────────────────────────────
TIER_1_LLM_GROUPS = {"活跃KOL", "普通用户"}
TIER_2_HEURISTIC_GROUPS = {"潜水用户"}

# 默认激活率（可通过 run_simulation(activation_rates=...) 覆盖）
DEFAULT_ACTIVATION_RATES = {
    "活跃KOL": 0.7,
    "普通用户": 0.3,
    "潜水用户": 0.1,
}

CALIBRATION_END    = "2025-06-02T16:30:00"
TIME_STEP_MINUTES  = 5
MAX_LLM_PER_STEP   = 8   # 每步最多激活 N 个 LLM agent
ENV_STEP_TIMEOUT   = 300  # env.step() 超时秒数


def _aggregate_attitude_to_table(
    db_path:   str,
    step:      int,
    agent_map: Dict[int, Dict],
    metric_col: str,
) -> None:
    """
    读取已标注的 post 表，按 step + group 聚合平均态度分，
    写入 attitude_step_group 表供前端折线图使用。
    """
    # timeout=30：等待最多30秒，解决 OASIS 持有写锁时的 "database is locked" 问题
    # WAL 模式：允许读写并发，从根本上避免锁冲突
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attitude_step_group (
                time_step  INTEGER,
                group_name TEXT,
                avg_score  REAL,
                post_count INTEGER,
                PRIMARY KEY (time_step, group_name)
            )
        """)

        # 检查 metric 列是否存在
        cols = {r[1] for r in cur.execute("PRAGMA table_info(post)").fetchall()}
        if metric_col not in cols:
            conn.commit()
            return

        # 读取当前步骤已标注帖子（sim 帖子 created_at 为整数步骤号）
        rows = cur.execute(f"""
            SELECT user_id, {metric_col}
            FROM post
            WHERE attitude_annotated = 1
              AND CAST(created_at AS TEXT) NOT LIKE '%-%'
              AND CAST(CAST(created_at AS TEXT) AS INTEGER) = ?
        """, (step,)).fetchall()

        if not rows:
            conn.commit()
            return

        group_scores: Dict[str, list] = defaultdict(list)
        for user_id, score in rows:
            try:
                uid = int(user_id)
            except (ValueError, TypeError):
                uid = 0
            grp = agent_map.get(uid, {}).get("group", "其他")
            group_scores[grp].append(float(score or 0.0))

        for grp, scores in group_scores.items():
            cur.execute(
                "INSERT OR REPLACE INTO attitude_step_group "
                "(time_step, group_name, avg_score, post_count) VALUES (?, ?, ?, ?)",
                (step, grp, round(sum(scores) / len(scores), 4), len(scores)),
            )
        conn.commit()
        logger.info(f"attitude_step_group: step={step}, groups={list(group_scores.keys())}")
    except Exception as e:
        logger.error(f"_aggregate_attitude_to_table error at step {step}: {e}")
    finally:
        conn.close()


async def run_simulation(
    profile_path:     str,
    db_path:          str,
    intervention_path: str,
    total_steps:      int,
    model_name:       str,
    model_base_url:   str,
    model_api_key:    str,
    attitude_config:  Dict[str, str],   # {metric_key: description}
    agent_map:        Dict[int, Dict],  # int_id → {name, username, group, orig_id}
    activation_rates: Optional[Dict[str, float]] = None,  # group → prob，覆盖默认值
    progress_callback: Optional[Callable] = None,  # (step, total) → None
    log_callback:     Optional[Callable] = None,   # (message) → None
) -> None:
    """
    完整的 OASIS 仿真循环，含每步 Attitude 标注。
    由 online_sim.py 在 daemon 线程中通过 asyncio.run() 调用。
    """
    def _log(msg: str):
        logger.info(msg)
        if log_callback:
            log_callback(msg)

    if not model_base_url or not model_api_key:
        raise RuntimeError("Missing MARS_MODEL_BASE_URL or MARS_MODEL_API_KEY")

    # 合并激活率：调用方传入的优先
    rates = {**DEFAULT_ACTIVATION_RATES, **(activation_rates or {})}
    _log(f"激活率配置: {rates}")

    attitude_metrics_list = list(attitude_config.keys())
    metric_key = attitude_metrics_list[0] if attitude_metrics_list else "attitude_topic"

    # --- 1. Agent 模型 ---
    _log(f"正在初始化 Agent 模型: {model_name}")
    # camel 的 token_limit 属性读取 model_config_dict["max_tokens"] 作为上下文窗口大小，
    # 但该值同时会作为 API 请求的 max_tokens 参数发送。为避免冲突：
    # 1. model_config_dict 中设正常的生成长度 (512)
    # 2. 创建后手动覆盖 token_limit，让 memory 系统使用正确的上下文窗口
    model = OpenAICompatibleModel(
        model_type=model_name,
        model_config_dict={"temperature": 0.5, "max_tokens": 512},
        api_key=model_api_key,
        url=model_base_url,
    )
    # 让 memory 系统使用 65536 作为上下文窗口大小，
    # 而 API 请求的 max_tokens 保持 512（由 model_config_dict 控制）。
    type(model).token_limit = property(lambda self: 65536)
    _log(f"Agent 模型初始化完毕。token_limit={model.token_limit}, API max_tokens={model.model_config_dict['max_tokens']}")

    # --- 2. Attitude 标注器 ---
    # 使用 _VLLMAttitudeAnnotator（OpenAI-compatible HTTP API，适用于 deepseek）
    # concurrency_limit=100 避免基类 _process_post 与子类内部双重 Semaphore 死锁
    _log("正在初始化 AttitudeAnnotator...")
    annotator = APIAttitudeAnnotator(
        model_name=model_name,
        attitude_config=attitude_config,
        base_url=model_base_url,
        api_key=model_api_key,
        concurrency_limit=100,
    )
    _log("AttitudeAnnotator 初始化完毕。")

    # --- 3. 数据库重置 ---
    _log("步骤 1: 正在重置数据库...")
    reset_simulation_tables(
        db_path=db_path,
        tables_to_keep=["post", "ground_truth_post", "sqlite_sequence"],
        logger=logger,
        calibration_cutoff=CALIBRATION_END,
    )

    # --- 4. 创建 OASIS 环境 ---
    _log("步骤 2: 正在创建 OASIS 环境...")
    env = oasis.make(
        agent_graph=None,
        platform=oasis.DefaultPlatformType.TWITTER,
        database_path=db_path,
        attitude_metrics=attitude_metrics_list,
    )
    _log("OASIS 环境创建完毕。")

    # --- 5. 生成并注册 Agents ---
    _log("步骤 3: 正在生成并注册 Agents...")
    available_actions = [
        ActionType.CREATE_POST,
        ActionType.LIKE_POST,
        ActionType.REPOST,
        ActionType.FOLLOW,
        ActionType.DO_NOTHING,
        ActionType.QUOTE_POST,
    ]
    agent_list: List[BaseAgent] = await generate_and_register_agents(
        profile_path=profile_path,
        db_path=db_path,
        platform=env.platform,
        model=model,
        available_actions=available_actions,
        CALIBRATION_END=CALIBRATION_END,
        TIME_STEP_MINUTES=TIME_STEP_MINUTES,
        attitude_metrics=attitude_metrics_list,
    )
    _log(f"Agent 注册完毕，共 {len(agent_list)} 个。")
    env.agent_graph = agent_list
    current_max_agent_id = max(
        (getattr(a, "agent_id_int", int(a.agent_id)) for a in agent_list), default=0
    )

    # --- 6. 干预预处理 ---
    int_processor = None
    if os.path.exists(intervention_path):
        _log("步骤 4: 处理干预文件...")
        int_processor = InterventionProcessor(db_path=db_path)
        int_processor.process_and_distribute(
            csv_path=intervention_path,
            agent_list=agent_list,
        )
        _log("干预指令已写入数据库。")
    else:
        _log("步骤 4: 无干预文件，跳过。")

    # --- 7. 环境重置 ---
    _log("步骤 5: 执行环境重置...")
    await env.reset()
    _log("环境重置完毕。")

    # --- 8. 仿真循环 ---
    for step in range(total_steps):
        current_step = step + 1
        _log(f"--- Simulation Step {current_step} / {total_steps} ---")

        llm_agents_to_run:       list = []
        heuristic_agents_to_run: list = []

        # 动态注册（干预中的 register_user 类型）
        if int_processor is not None:
            new_agents, current_max_agent_id = int_processor.execute_dynamic_registrations(
                env=env,
                current_step=current_step,
                current_max_agent_id=current_max_agent_id,
                model=model,
                available_actions=available_actions,
                attitude_metrics=attitude_metrics_list,
                agent_list=agent_list,
            )
            if new_agents:
                llm_agents_to_run.extend(new_agents)

        pool = (
            [(a.agent_id, a) for a in env.agent_graph]
            if isinstance(env.agent_graph, list)
            else env.agent_graph.get_agents()
        )

        for _, agent in pool:
            if agent in llm_agents_to_run:
                continue
            grp = agent.group
            if grp in TIER_1_LLM_GROUPS:
                if random.random() < rates.get(grp, 0.3):
                    llm_agents_to_run.append(agent)
            elif grp in TIER_2_HEURISTIC_GROUPS:
                if random.random() < rates.get(grp, 0.1):
                    heuristic_agents_to_run.append(agent)

        # 限制每步并发 LLM 请求数，防止 API 速率限制导致挂起
        if len(llm_agents_to_run) > MAX_LLM_PER_STEP:
            llm_agents_to_run = random.sample(llm_agents_to_run, MAX_LLM_PER_STEP)

        _log(f"激活: {len(llm_agents_to_run)} LLM + {len(heuristic_agents_to_run)} Heuristic")

        for agent in llm_agents_to_run + heuristic_agents_to_run:
            agent.current_time_step = current_step

        all_actions = {}
        all_actions.update({a: LLMAction()     for a in llm_agents_to_run})
        all_actions.update({a: HeuristicAction() for a in heuristic_agents_to_run})

        if all_actions:
            _log(f"执行 {len(all_actions)} 个 Agent 的 actions...")
            try:
                await asyncio.wait_for(env.step(all_actions), timeout=ENV_STEP_TIMEOUT)
            except asyncio.TimeoutError:
                _log(f"Step {current_step}: env.step() 超时 ({ENV_STEP_TIMEOUT}s)，跳过本步")
        else:
            _log("本轮无 Agent 激活，跳过 step。")

        # Attitude 标注 + 聚合（非致命，失败不中断仿真）
        try:
            _log(f"Step {current_step}: 开始 Attitude 标注...")
            await asyncio.wait_for(annotator.annotate_table(
                db_path=db_path,
                table_name="post",
                only_sim_posts=True,
                batch_size=50,
            ), timeout=ENV_STEP_TIMEOUT)
            _aggregate_attitude_to_table(db_path, current_step, agent_map, metric_key)
            _log(f"Step {current_step}: Attitude 标注聚合完成")
        except Exception as e:
            _log(f"Step {current_step}: Attitude 标注失败（非致命）: {e}")

        # 通知外层进度
        if progress_callback:
            progress_callback(current_step, total_steps)

    await env.close()
    _log("--- Simulation Finished ---")

    # env.close() 之后 OASIS 释放所有 DB 锁，做全量兜底聚合
    # 从 step=0 开始（含 env.reset() 期间生成的帖子）确保折线图有足够数据点
    _log("正在执行全量 Attitude 聚合（兜底）...")
    try:
        for step in range(0, total_steps + 1):
            _aggregate_attitude_to_table(db_path, step, agent_map, metric_key)
        _log("全量 Attitude 聚合完成")
    except Exception as e:
        _log(f"全量 Attitude 聚合失败: {e}")
