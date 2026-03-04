# Streamlit Demo: 智能访谈工作流展示

本目录用于将当前自动化访谈工作流以网站 Demo 形式展示，先基于 Streamlit 实现，后续可迁移 Flask + HTML。

## 功能
1. 项目介绍
- 结构化介绍项目目标、流程与使用方式。
- 内置流程图（Graphviz）。

2. 预设回放
- 选择预设问卷与访谈对象。
- 展示完整对话、异常事件高亮、访谈者决策摘要（开关控制）。
- 自动生成访谈总结，并展示“回填答案 vs 原始答案”对照（不做回放打分）。

3. 实时访谈
- 用户选择主题与访谈者对话。
- 记录异常事件与策略应对（每题必经 Follow-up Gate 决策）。
- 结束后生成个体建模报告（含大五粗分类、行为习惯推断、选项倾向）。
- 支持满意度和正确性评分。

4. 数据库持久化
- SQLite 存储会话、消息、报告、反馈。
- 看板展示会话数、消息数、反馈数和事件分布。

## 目录
- `app.py`: Streamlit 入口
- `db.py`: SQLite 数据层
- `data_loader.py`: 读取已有 benchmark/outputs 数据
- `preset_module.py`: 预设回放模块
- `live_module.py`: 实时访谈模块
- `backend_service.py`: 实时访谈后端适配层（易替换）
- `styles.css`: 样式
- `demo_data/demo.db`: 数据库文件（运行后生成）
- `web_data/questionnaires/`: 网页目录下单独保存的问卷格式数据

## 启动（端口 8501）
```bash
cd "智能访谈项目代码/自动化问答/demo_web_streamlit"
pip install -r requirements.txt
# 可选：若你把 demo 单独迁移到别处，先准备本地模型配置
# cp config.sample.json config.json
python scripts/prepare_demo_examples.py
streamlit run app.py --server.port 8501
```

默认隐藏“数据看板”页面；仅本地调试需要时开启：
```bash
DEMO_SHOW_DASHBOARD=true streamlit run app.py --server.port 8501
```

## 实时访谈后端切换（便于后台更改）
默认会优先用 `run_benchmark` 逻辑，失败时自动回退规则后端。

- 使用 run_benchmark 后端：
```bash
DEMO_BACKEND=run_benchmark DEMO_INTERVIEWER_MODEL=qwen3-max streamlit run app.py --server.port 8501
```

- 使用规则后端（不依赖模型调用）：
```bash
DEMO_BACKEND=rule streamlit run app.py --server.port 8501
```

可选参数：
- `DEMO_INTERVIEWER_MODEL`：固定建议 `qwen3-max`（后端会优先强制回退到 qwen3 系列）
- `DEMO_HANDLING_MODE`：`default|alt`
- `DEMO_RUNNER_VERSION`：`v1|v2`（`v2` 使用新版流程逻辑）
- `DEMO_SHOW_DASHBOARD`：`true|false`（默认 false）
- `DEMO_BENCH_DIR`：示例抽取源 benchmark 目录（默认 `../benchmarks`）
- `DEMO_OUTPUT_DIRS`：预设回放输出目录，逗号分隔（默认自动探测 demo 内/上级 outputs）
- `DEMO_QWEN3_API_KEY` / `DEMO_QWEN3_BASE_URL`：可覆盖本地 `config.json`

## Human-in-the-loop 说明
- 用户回答由 `st.chat_input` 提供，不再使用 IntervieweeAgent 模拟受访者。
- 实时访谈问卷只来自 `web_data/questionnaires/*.questionnaire.json`（由 `web_data/questionnaires/manifest.json` 管理）。
- 预设回放样例来自 `examples/preset/`。
- 新增 `message_trace` 表用于记录每轮状态与 follow-up 决策：
  - `state/action/policy_applied/event_type/followup_decision/turn_index`

## 独立迁移运行说明
- 示例数据、问卷数据、manifest 都在 `demo_web_streamlit` 目录内。
- 路径为软编码：manifest 使用相对路径，可整体拷贝后运行。
- 如果迁移后不在原仓库目录，设置以下环境变量即可：
  - `DEMO_BENCH_DIR`（用于重新生成 examples）
  - `DEMO_OUTPUT_DIRS`（用于加载预设回放结果）
  - `DEMO_QWEN3_API_KEY`、`DEMO_QWEN3_BASE_URL`（或本地 `config.json`）

## 后续 Flask + HTML 迁移建议
1. 复用 `db.py` 和 `data_loader.py`。
2. 将 `backend_service.py` 保持为 service 层。
3. Flask 仅做路由与模板渲染，前端用 Jinja + HTMX/Alpine.js。
