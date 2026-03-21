# SetupView — 设计方案

> **职责**：Step 1 — Agent 配置 + 关系图谱预览，配置完成后跳转到 SimulationView
> **页面路径**：`/setup`

---

## 页面定位

```
Step 1: SetupView         Step 2: SimulationView     Step 3: OnlineSimView
  配置 Agent 数量           仿真参数 + 启动 + 历史        线上环境仿真
  选择研究主题
  预览关系图谱
  [Next Step →]  ────────▶
```

SetupView 负责：
- 选择 Agent 数量（或通过主题自动选择）
- 查看 Agent 关系图谱
- 确认后跳转 SimulationView（携带 Agent 参数）

**不负责**仿真参数配置（Steps / Concurrency 等），这些交给 SimulationView。

---

## 已实现功能

### 当前页面结构

```
SetupView
├── NavBar (step 1 active)
└── .workspace
    ├── GraphPanel (左侧)         ← 关系图，已实现
    └── .config-pane (右侧)
        ├── Agent Selection       ← 数量滑块 + 分布统计，已实现
        ├── Network Stats         ← Agents / Connections / Avg Degree，已实现
        ├── System Log            ← 日志区，已实现
        └── [Next Step →]         ← 跳转按钮，已实现
```

### Next Step 按钮逻辑

```javascript
function nextStep() {
  const params = {
    num_agents: agentCount.value,
    agent_ids:  selectedAgents.value.map(a => a.user_id),
  }
  localStorage.setItem('agentParams', JSON.stringify(params))
  setTimeout(() => router.push('/simulation'), 300)
}
```

SimulationView 从 `localStorage('agentParams')` 读取，传给后端启动仿真。

---

## 待实现：主题驱动 Agent 选择

> **状态**：待实现
> **目标**：用户输入研究主题，系统从 Agent 库中筛选最相关的 N 个 Agent

### 功能描述

用户在 Setup 阶段输入研究主题（如"大学生消费行为"、"社交媒体焦虑"），系统从 Agent 库中筛选出最相关的 N 个 Agent，替代当前的等间距采样逻辑。

---

### 后端设计

#### 新增 API Endpoint

```
POST /api/profiles/select
Body: { topic: str, n: int }
Response: {
  selected: [
    { user_id, name, relevance_score, relevance_reason, ...profile }
  ],
  topic: str
}
```

#### 选择算法（两阶段）

**阶段一：规则粗筛（无 LLM，快速）**

从每个 profile 提取关键词（interests + personality + occupation），与 topic 做关键词匹配，得到初步相关性分数，过滤掉明显无关的 Agent，减少后续 LLM token 消耗。

**阶段二：LLM 精排（批量调用，低成本）**

将通过粗筛的 Agent 画像批量送入 LLM，一次请求返回所有 Agent 的相关性评分：

```
System: 你是一个研究设计助手。
User:
  研究主题：{topic}
  请对以下 Agent 画像与该主题的相关性打分（0-10），并给出一句理由。
  [{ name, mbti, interests, personality_brief }]
  输出 JSON 数组：[{ user_id, score, reason }]
```

选取 score 最高的 N 个 Agent 返回。

#### 后端改动文件

| 文件 | 改动 |
|------|------|
| `backend/app.py` | 新增 `POST /api/profiles/select` 路由 |
| `backend/simulator.py` | `POST /api/simulations` 接收并存储 `research_topic` |

---

### 前端设计

#### SetupView 新增区块（插入 Agent Selection 上方）

```
┌─────────────────────────────────────────┐
│  Research Topic                         │
│  ┌───────────────────────────────────┐  │
│  │ 输入研究主题（如：大学生消费行为）  │  │
│  └───────────────────────────────────┘  │
│  [Auto-select Agents]  ← 按钮           │
│  状态提示：Selecting 10 relevant agents... │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Agent Selection                        │
│  Number of Agents: [滑块 10]            │
│  （主题选择生效时，滑块变为只读）         │
└─────────────────────────────────────────┘
```

#### 交互逻辑

1. 用户输入 topic → 点击 "Auto-select" 按钮
2. 前端调用 `POST /api/profiles/select`，显示 loading 状态
3. 返回后：
   - `selectedAgents` 切换为 topic-selected 列表（替代等间距采样）
   - 关系图节点上显示相关性分数标记（颜色深浅 / 小 badge）
   - 每个 Agent 卡片可展开查看 `relevance_reason`
4. 用户仍可手动调整滑块覆盖（清空 topic 选择，恢复等间距采样）

#### 状态管理新增字段

```javascript
const researchTopic        = ref('')          // 用户输入的主题
const topicSelectedAgents  = ref([])          // 主题选出的 agent 列表（含 score）
const topicSelectLoading   = ref(false)
const useTopicSelection    = ref(false)       // true 时 selectedAgents 用 topic 结果

// selectedAgents computed 改为：
const selectedAgents = computed(() => {
  if (useTopicSelection.value && topicSelectedAgents.value.length)
    return topicSelectedAgents.value
  // 否则走原有等间距逻辑
  ...
})
```

#### 关系图联动

- topic 选中的 Agent 节点加高亮边框
- 节点 tooltip 中加一行 `Relevance: 8.5 — "该用户有明显的消费记录和购物兴趣"`

#### 主题传递到仿真

`researchTopic` 随 `POST /api/simulations` 一起传入后端，存入 `meta.json`，供后续 Agent prompt 使用（如 move_phase 中加入"本次模拟研究主题"背景）。

#### 前端改动文件

| 文件 | 改动 |
|------|------|
| `frontend/src/views/SetupView.vue` | 新增 topic 输入区 + Auto-select 按钮 + `topicSelectedAgents` 状态 |
| `frontend/src/api/index.js` | 新增 `selectAgentsByTopic(topic, n)` |

---

## 实现优先级

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P1 | 后端 `/api/profiles/select` + LLM 评分 | 高 |
| P2 | SetupView 主题输入 + Auto-select UI | 高 |
| P3 | research_topic 传入仿真 prompt | 中 |
| P4 | 关系图节点相关性高亮 | 低 |

---

## 待讨论问题

1. **LLM 评分成本**：37 个 Agent 批量评分约消耗一次 LLM 调用，可接受。但如果 Agent 库扩展到 100+ 需考虑分批。
2. **相关性 vs 多样性**：是否需要在相关性最高的 Agent 中保证人口多样性（性别/MBTI 均衡）？
3. **topic 对 prompt 的影响**：research_topic 传入仿真后，应该在哪个 prompt 中体现？move_phase 还是 send_phase？

---

## localStorage 传递规范

SetupView 写入，SimulationView 读取：

```javascript
// SetupView 写入
localStorage.setItem('agentParams', JSON.stringify({
  num_agents:     10,
  agent_ids:      ['uuid-001', 'uuid-002', ...],
  research_topic: 'string or null',
}))

// SimulationView 读取（start 时使用）
const agentParams = JSON.parse(localStorage.getItem('agentParams') || '{}')
```
