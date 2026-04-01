# Interview Module

`backend/interview` 负责 Step 4 的 AI 访谈模块。它不是独立服务，而是由 [backend/app.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/app.py) 注册为 Flask blueprint，对外提供 `/api/interview/*` 路由。

这个模块当前的职责有 4 类：

1. 根据商品信息生成 5 阶段问卷草稿。
2. 基于 Step 1/2/3 产出的 agent 信息创建 interview session。
3. 对单个 agent 启动访谈线程，并通过 SSE 推送问答过程。
4. 在访谈结束后生成单人报告、多人 summary 和 AI analyze 结果。

## Files

- [routes.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/routes.py): Flask 路由、session 内存态、访谈线程入口。
- [engine.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/engine.py): 问题推进、回答判定、追问 gate、context 构建、report 生成。
- [state.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/state.py): `BackendState` 与 stage planner。
- [responder.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/responder.py): 虚拟受访者 prompt 和回答后处理。
- [persona.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/persona.py): 从 Step 2 / Step 3 提取 persona 摘要。
- [llm.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/llm.py): interview 专用 LLM 配置和多 `API key` 轮转。

## End-to-End Flow

### 1. 前端进入 Interview 页

前端页是 [frontend/src/views/InterviewView.vue](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/frontend/src/views/InterviewView.vue)。

- 页面挂载时读取 `localStorage.simResult`。
  - 这里带来 Step 2 的 `sim_id` 和最后一步的 agent 列表。
- 页面同时读取 `localStorage.latestOnlineSimId`。
  - 这里带来 Step 3 的 `online_sim_id`。

也就是说，Interview 页本身不重新算 agent，而是消费前两步已经落地的数据。

### 2. 生成问卷

前端调用 `POST /api/interview/generate-questionnaire`。

- 路由在 [routes.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/routes.py)。
- 输入是 `product_name / background / goal / num_questions`。
- 输出是按 `basic / core / attitude / reflection / closing` 分阶段的 JSON 问卷。

前端允许直接编辑题目文本和类型，再调用 session 创建接口。

### 3. 创建 session

前端调用 `POST /api/interview/sessions`，携带：

- 编辑后的 `questions`
- `product_name`
- 当前页面上的 agent 列表
- `sim_id_urban`
- `sim_id_online`

后端会做几件事：

1. 读取 [backend/users/student_profiles.json](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/users/student_profiles.json)。
2. 如果有 `sim_id_urban`，从 [backend/storage.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/storage.py) 读取 Step 2 的 steps。
3. 如果有 `sim_id_online`，从 [backend/marketing/online_sim.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/marketing/online_sim.py) 的 `get_session_agents()` 读取 Step 3 的帖子和最终态度摘要。
4. 为每个 agent 组装 `agent_contexts`。

`agent_contexts[agent_id]` 当前结构：

```json
{
  "meta": { "...": "前端传来的基础信息" },
  "profile": { "...": "student_profiles.json 里的完整画像" },
  "urban_summary": { "...": "Step 2 摘要，可为空" },
  "online_summary": { "...": "Step 3 摘要，可为空" }
}
```

session 目前保存在内存 `_sessions` 里，不会自动持久化。后端重启后 session 会丢失。

### 4. 启动单个 agent 的访谈

前端现在有两种启动方式：

- 点击单个 agent 行，仍然沿用原来的手动启动模式。
- 勾选多个 agent 或全选后，点击“运行所选”，前端会并行连接多个现有 SSE 端点。

这里没有新增后端路由。批量运行本质上还是多次连接：

`GET /api/interview/sessions/<session_id>/agents/<agent_id>/stream`

这个端点会：

1. 检查该 agent 是否已完成。
2. 如果已完成，则回放已有 `qa_pairs`。
3. 如果还没运行，则创建一个后台线程 `_run_interview_thread(...)`。
4. 用 `queue.Queue()` 持续往 SSE 推送 `qa / followup / done / error` 事件。

## Interview Thread Flow

后台线程入口在 [routes.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/routes.py) 的 `_run_interview_thread(...)`。

线程内部顺序如下：

1. 创建 `VirtualAgentResponder`
2. 创建 `InterviewEngine`
3. 创建 `BackendState`
4. `while not state.finished` 循环推进

每轮主流程：

1. `engine.update_stage(state)`
2. `engine.select_next_qid(...)`
3. `engine.build_context(state)`
4. `responder.reply(...)`
5. `engine.detect_event(answer)`
6. `engine.record_answer(...)`
7. `engine.followup_gate(...)`
8. 如需追问，再走一次 `reply -> detect_event -> record_answer`
9. 推送 SSE 事件到前端

访谈结束后：

1. `engine.build_report(...)`
2. 存到 `sess["agent_states"][aid]["report"]`
3. 推送 `done`

## Current State Machine

当前代码已经落了你之前计划里的大部分核心结构：

- 主问题优先：
  - [state.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/state.py) 里的 `question_sequence` 按问卷顺序保存。
  - [engine.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/engine.py) 的 `select_next_qid()` 先跑所有主问题，再进入 recovery。
- follow-up 预算独立：
  - `followup_budget = min(5, max(2, ceil(question_count * 0.25)))`
  - `max_turns = 主问题数 + followup_budget`
- transcript 保存完整对话轮次：
  - interviewer 问题和 agent 回答都写进 `interview_transcript`
- follow-up 上下文可见最新主回答：
  - `build_context()` 取 `interview_transcript` 的最近若干轮
- 结构化题做 exact option 匹配与归一化
- 回答后处理会清洗常见状态括号

## One Point To Watch

当前实现和你原计划仍有一个明显差异：

- [engine.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/engine.py) 里仍然保留了 `core/reflection` 的探索性追问。
- 触发条件不再是简单“回答长就追问”，但仍会在“信息密度较高”时触发 `explore_followup`。

如果你下一轮要把策略收紧成“每题只允许 repair，不做探索性深挖”或者“只有 `attitude/reflection` 可追问”，优先改这里：

- [engine.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/engine.py)

## LLM Config

### Current behavior

Interview 模块的 LLM 调用现在统一经过 [llm.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/llm.py)。

优先级如下：

1. `INTERVIEW_LLM_API_KEYS`
2. `INTERVIEW_LLM_API_KEY`
3. `LLM_API_KEYS`
4. `LLM_API_KEY`

`INTERVIEW_LLM_API_KEYS` 和 `LLM_API_KEYS` 支持逗号、分号或换行分隔，模块会轮转选 key。

### Recommended .env

在 `backend/.env` 里可以这样配：

```env
INTERVIEW_LLM_API_BASE=https://api.deepseek.com
INTERVIEW_LLM_MODEL=deepseek-chat

INTERVIEW_LLM_API_KEYS=sk-key-a
sk-key-b
sk-key-c
```

如果只想保留单 key，也可以继续用旧配置：

```env
LLM_API_BASE=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-single-key
```

### Which calls use this config

Interview 模块里这些调用都会走上面的配置：

- 问卷生成
- follow-up 文本生成
- 虚拟受访者回答
- session analyze

## Frontend Batch Run

Interview 页现在已经支持：

- 勾选多个 agent
- 全选 / 清空
- 一键启动所选 agent 的访谈

实现方式仍然保持前端手动控制：

- 每个被选中的 agent 会单独建立一个 `EventSource`
- 每个连接仍然只对应一个现有的 `/stream` 路由
- 单个 agent 点击启动仍然有效

也就是说，这次改动没有改变你们现有的 SSE 事件类型，也没有改 session API。

## Important Routes

- `POST /api/interview/generate-questionnaire`
- `POST /api/interview/sessions`
- `GET /api/interview/sessions/<session_id>/agents`
- `GET /api/interview/sessions/<session_id>/agents/<agent_id>/stream`
- `GET /api/interview/sessions/<session_id>/agents/<agent_id>/report`
- `GET /api/interview/sessions/<session_id>/summary`
- `POST /api/interview/sessions/<session_id>/analyze`

## Collaboration Notes

如果后面多人一起改 interview，建议按这条边界拆任务：

- 改 persona / 回答风格：只动 [responder.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/responder.py)
- 改推进策略 / 追问策略 / 停止条件：只动 [engine.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/engine.py) 和 [state.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/state.py)
- 改接口 / SSE / session 生命周期：只动 [routes.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/routes.py)
- 改前端交互：只动 [frontend/src/views/InterviewView.vue](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/frontend/src/views/InterviewView.vue)

## Run

项目根 README 目前只写了基础启动方式。Interview 模块本身没有额外独立命令，仍按整体项目启动：

```bash
cd backend
python app.py
```

```bash
cd frontend
npm run dev
```
