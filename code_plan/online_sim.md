# OnlineSimView (online_sim) — 设计方案

> **职责**：Step 3 — 线上社交环境仿真（OASIS 框架）
> **页面路径**：`/online-sim`
> 将个体离线行为轨迹融入画像，模拟 Agent 在社交平台上的发帖/互动行为，并支持营销干预实验

---

## 页面定位

```
Step 1: SetupView    Step 2: SimulationView    Step 3: OnlineSimView
  Agent 配置           个体行为仿真（地图）          线上社交平台仿真（当前页）
                       Next Step ────────────▶
```

---

## 数据流

### 输入（来自 Step 2）

```javascript
// localStorage('simResult')
{
  sim_id:      "sim_xxx",
  total_steps: 12,
  agents: [
    {
      id:          "user_001",
      name:        "张明",
      occupation:  "本科生",
      gender:      "male",
      needs:       { satiety, energy, safety, social },
      position:    { lat, lng },
      intention:   "去图书馆学习",
      event_history: [...]
    }
  ]
}
```

### 画像融合逻辑（前端 → 后端 POST）

将 lifesim agent 映射为 OASIS CSV 格式：

| lifesim 字段 | OASIS 字段 | 映射规则 |
|---|---|---|
| `id` | `agent_id` / `user_id` | 直接使用 |
| `name` | `name` | 直接使用 |
| name → 小写去空格 | `username` | `zhangming` |
| occupation + interests | `bio` | `${occupation}，兴趣：${interests.join('、')}` |
| bio + event_history 摘要 | `description` | 拼接离线行为摘要（最多3条意图） |
| event_history + occupation | `user_char` | 生成角色扮演指令（含离线行为特征） |
| occupation → 分层规则 | `group` | 见下表 |
| relationships | `following_agentid_list` | 从 /api/relationships 筛选选中 agents 的关系 |
| 0.0 | `initial_attitude_[topic]` | 初始中立，由仿真动态演化 |

**群体分层规则：**

| occupation 关键词 | OASIS group |
|---|---|
| 博士 / 研究生 / 硕士 | 权威媒体/大V |
| 学生会 / 社团干部 | 活跃KOL |
| 活跃创作（多项兴趣含创作类） | 活跃创作者 |
| 本科生（默认） | 普通用户 |
| needs.social < 0.3 / 低活跃 | 潜水用户 |

---

## 页面布局（三区单页结构）

```
┌────────────────────────────────────────────────────────────────────────────┐
│  NavBar  (step 1 ✓ · step 2 ✓ · step 3 active)  [← Back]                  │
├──────────────────────┬─────────────────────────────────────────────────────┤
│  左侧栏 (320px)       │  右侧内容区 (flex:1, 分上下两块)                     │
│                      │                                                     │
│  [A] Agents          │  ┌─────────────────────────────────────────────┐   │
│      agent cards     │  │ 右上：Attitude 折线图（flex: 0 0 320px）      │   │
│      (群体badge)     │  │   · 各群体平均 attitude 随步骤折线           │   │
│                      │  │   · Y轴 -1~1，中线虚线，图例                 │   │
│  [B] Campaign        │  │   · [✦ 解读曲线] 按钮（待实现）              │   │
│      Topic: ______   │  └─────────────────────────────────────────────┘   │
│      Steps: [4]      │  ┌─────────────────────────────────────────────┐   │
│      Concurrency:[3] │  │ 右下：Stats 数据面板                          │   │
│                      │  │   · 5张摘要卡片（Posts/Likes/Reposts/        │   │
│  [C] Interventions   │  │     Comments/Actions）                       │   │
│      + Add row       │  │   · Group Activity 水平条形图                │   │
│      step|type|      │  │   · Top 5 最高互动帖子                       │   │
│      content|target  │  └─────────────────────────────────────────────┘   │
│                      │                                                     │
│  [▶ Start Sim]       │  左侧 Posts Feed（flex: 1, 可滚动）                  │
│  [● Running 2/4]     │  （与右侧上下区并列，各自独立滚动）                   │
└──────────────────────┴─────────────────────────────────────────────────────┘
```

精确网格结构：

```
workspace
├── sidebar (320px, flex-shrink:0)
└── main-area (flex:1, overflow:hidden)
    ├── posts-col (width:420px, overflow-y:auto)     ← Posts Feed
    └── right-col (flex:1, overflow-y:auto)
        ├── attitude-panel (border-bottom)            ← 折线图
        └── stats-panel                              ← 数据汇总
```

---

## 左侧栏三区块

### 区块 A — Agents

- 从 `simResult.agents` 渲染 Agent 卡片
- 每张卡片：头像圆圈（wellness色）+ 姓名 + 群体badge
- 群体badge颜色：权威媒体→蓝，活跃KOL→橙，活跃创作者→绿，普通用户→灰，潜水用户→浅灰
- 卡片可展开查看 `user_char`（已融合离线行为的角色指令）

### 区块 B — Campaign

| 参数 | 控件 | 说明 |
|---|---|---|
| Campaign Topic | text input | 如 "TNT演唱会"，决定 attitude metric 名称 |
| Simulation Steps | range 1–12 | 默认 4 |
| Concurrency | range 1–10 | 默认 3 |

> LLM Model 由 `backend/marketing/data/.env` 中的 `MARS_MODEL_NAME` 决定，不暴露给用户。

### 区块 C — Interventions

可编辑表格，每行一条干预策略：

| 字段 | 说明 |
|---|---|
| Step | 触发步骤（1~N）|
| Type | broadcast / bribery / register_user |
| Content | 干预内容 |
| Target Group | 目标群体（空=全体）|
| Ratio | 目标比例 0~1（仅bribery）|

---

## 各区内容规格

### Posts Feed（左侧主体）

- 标题栏：`Posts [N条]` + 进度 `Step X / N`
- 帖子卡片（新帖插入顶部）：
  - 头像（群体色）+ 姓名 + 群体badge + Step标签
  - attitude chip（正/负/中立，带数值）
  - 正文内容
  - 若为转发/引用：显示 quote_content 引用块
  - 底部互动栏：👍 N · 👎 N · 🔁 N · 💬 N
- 背景微色：attitude > 0.3 → 浅绿；< -0.3 → 浅红；否则白

### Attitude 折线图（右上）

- 标题：`Attitude towards "<topic>" by group`
- SVG 折线图，viewBox="0 0 560 220"
- X轴 = 步骤，Y轴 = -1~1，中线0用紫色虚线
- 每个群体一条线（5色），鼠标悬停显示数值
- 仿真进行中每步更新；完成后最终数据

### Stats 数据面板（右下）

**摘要卡片行（5格）**：Posts / Likes / Reposts / Comments / Actions

**Group Activity 水平条形图**：
每行：群体色点 + 群体名 + 进度条（按最大帖数比例）+ `N posts · N 👍`

**Top 5 最高互动帖子**：
按 (num_likes + num_shares) 降序，显示排名 + 作者 + 内容摘要 + 互动数

---

## 后端 API（现状）

```
POST /api/online-sim/start         启动仿真，返回 online_sim_id
GET  /api/online-sim/<id>/stream   SSE 日志流 / step_done / complete
GET  /api/online-sim/<id>/posts    帖子列表（含 num_likes/shares/comments/step）
GET  /api/online-sim/<id>/attitude 态度轨迹（按群体聚合）
GET  /api/online-sim/<id>/stats    统计摘要（帖数/点赞/群体分布/Top5）
```

模型配置从 `data/.env` 读取，默认 `deepseek-chat`，不由前端传入。

---

## 前端状态模型

```javascript
// 输入数据
const simResult    = ref(null)       // 从 localStorage 读取
const oasisAgents  = ref([])         // 前端映射的 OASIS 画像

// Campaign 配置
const cfgTopic       = ref('')
const cfgSteps       = ref(4)
const cfgConcurrency = ref(3)

// 干预策略
const interventions  = ref([])

// 仿真运行
const onlineSimId   = ref(null)
const simRunning    = ref(false)
const simProgress   = ref(0)
const logs          = ref([])

// 结果
const posts         = ref([])
const attitudeData  = ref(null)
const statsData     = ref(null)
```

---

## UX 细节

**启动按钮状态**：
- 空闲：`▶ Start Simulation`（紫色渐变）
- 运行中：`● Running 2/4`（灰色禁用 + pulse 动画）
- 完成：`✓ Completed`（绿色）

**实时更新节奏**：
- `step_done` 事件：刷新 posts（实时插入新帖）+ 刷新 attitude（折线更新）
- `complete` 事件：刷新 posts + attitude + stats（完整数据）

**历史记录**：
- 进入页面时加载历史仿真列表（从 `RESULTS_DIR` 读取 meta.json）
- 历史中 status=running 的记录自动修正为 completed（已实现）
- db_path 从当前 RESULTS_DIR 动态派生，不依赖存储的绝对路径（已实现）

---

## 待实现功能一：OnlineSim 重构

> **状态**：待实现
> **目标**：消除独立子进程脚本，内联仿真逻辑；开启 Attitude 标注；新增曲线解读功能

### 改进一：内联仿真逻辑（消除独立子进程脚本）

**当前问题**：`online_sim.py` 通过 `subprocess.Popen(oasis_test_grouping.py)` 启动仿真，跨进程通信脆弱。

**目标**：新建 `simulation/oasis_sim.py`，将逻辑提取为可调用的 async 函数：

```python
async def run_simulation(
    profile_path, db_path, intervention_path,
    total_steps, model_name, model_base_url, model_api_key,
    attitude_config,
    progress_callback=None,    # callable(step, total)
    log_callback=None,         # callable(message: str)
) -> None: ...
```

`online_sim.py` 改为：

```python
from simulation.oasis_sim import run_simulation

def _run_simulation(state, tmp_dir, body):
    asyncio.run(run_simulation(
        ...,
        progress_callback=lambda step, total: state.log_queue.put({"type": "step_done", "step": step}),
        log_callback=lambda msg: state.log_queue.put({"type": "log", "message": msg}),
    ))
```

**注意**：Flask 必须在 oasis conda 环境下运行：`conda activate oasis && python backend/app.py`

改动文件：

| 文件 | 操作 |
|---|---|
| `simulation/oasis_sim.py` | **新建**：提取 main() 为可调用函数 |
| `online_sim.py` | 修改 `_run_simulation`：用 asyncio.run() 替换 subprocess |
| `oasis_test_grouping.py` | 精简为 CLI 薄包装 |

### 改进二：开启 Attitude 标注（让折线图有动态数据）

**当前问题**：`oasis_test_grouping.py` 中 Attitude 标注循环被注释，折线图无动态数据。

**目标**：取消注释，每步标注后聚合写入 `attitude_step_group` 表：

```python
# 每步 await env.step() 之后
await annotator.annotate_table(db_path, "post", only_sim_posts=True, batch_size=50)
_aggregate_attitude_to_table(db_path, current_step, agent_map, metric_key)
```

`attitude_step_group` 表结构：
```sql
CREATE TABLE attitude_step_group (
    time_step INTEGER,
    group_name TEXT,
    avg_score REAL,
    post_count INTEGER
)
```

`get_attitude` 端点优先读取此表，fallback 原有表。

改动文件：

| 文件 | 操作 |
|---|---|
| `simulation/oasis_sim.py` | 取消注释标注循环；初始化 annotator；添加 `_aggregate_attitude_to_table` |
| `online_sim.py` | `get_attitude` 优先读 `attitude_step_group` 表 |

### 改进三：态度曲线解读（LLM 分析）

**新增端点**：

```
POST /api/online-sim/<id>/attitude/interpret
Response: { interpretation, topic, generated_at }
```

**Prompt 模板**：

```
你是一名社会媒体研究员，正在分析一场针对主题「{topic}」的舆情仿真结果。
模拟共进行 {total_steps} 步，包含以下群体：{group_names}。
各群体的态度变化（-1=极度负面，0=中立，+1=极度正面）如下：
{attitude_table}

请从以下角度解读：
1. 整体趋势：舆论是向正面还是负面演化？
2. 群体差异：哪个群体最支持/最抗拒？
3. 转折点：哪一步出现明显变化？
4. 干预效果（若有）
5. 结论与建议

请用简洁、专业的中文回答，100-300字。
```

**前端展示**：

```html
<!-- attitude-panel 右上角 -->
<button @click="interpretCurve" :disabled="interpreting || !attitudeData">
  {{ interpreting ? '解读中...' : '✦ 解读曲线' }}
</button>

<!-- 折线图下方 -->
<div v-if="interpretation" class="interpret-card">
  <div class="interpret-title">AI 解读</div>
  <p>{{ interpretation }}</p>
</div>
```

改动文件：

| 文件 | 操作 |
|---|---|
| `online_sim.py` | 新增 `interpret_attitude` 路由 + `_build_interpret_prompt` + `_call_llm_sync` |
| `OnlineSimView.vue` | 新增 interpreting/interpretation 状态 + 按钮 + 解读卡片 |
| `frontend/src/api/index.js` | 新增 `interpretAttitude(id)` |

### 实施顺序

```
改进二（最有价值，立刻有折线图数据）
  ↓
改进三（依赖折线图数据才能解读）
  ↓
改进一（架构优化，最后做，避免引入风险）
```

---

## 风险与处理

| 风险 | 处理方式 |
|---|---|
| 改进一要求 Flask 在 oasis conda env 下运行 | 启动命令改为 `conda activate oasis && python app.py` |
| 标注调 LLM 每步会增加仿真时间 | 可设 `MARS_ANNOTATE=false` 环境变量跳过标注 |
| interpret endpoint 超时（deepseek 慢） | 设 timeout=30s，前端 loading 状态 |
| attitude 数据 agent_type='LLM' | 后端用 agent_map 将 agent_id 映射到群体名 |
| 三区布局在小屏幕变形 | main-area min-width:900px，小屏水平滚动 |
| OASIS DB 并发写入冲突 | 每次仿真用独立 DB 文件（`osim_{id}.db`）|

---

## 样式规范

与 SetupView / SimulationView 保持一致：
- CSS 变量：`var(--bg)` / `var(--surface)` / `var(--border)` / `var(--purple)` / `var(--grad)`
- 左侧栏宽度 320px，Posts Feed 列宽度 420px（固定），右侧列 flex:1
- 区块标题：`cfg-section-title` 样式
