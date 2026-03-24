# InterviewView (interview) — 设计方案

> **职责**：Step 4 — 基于商品/话题，对虚拟 Agent 进行 LLM 驱动的智能访谈，获取结构化意见
> **页面路径**：`/interview`

---

## 页面定位

```
Step 1: SetupView    Step 2: SimulationView    Step 3: OnlineSimView    Step 4: InterviewView
  选 Agent → Next ──▶  城市仿真 → Next ─────▶  线上仿真 → Next ───▶  问卷设计 → 逐个访谈 → 汇总
                           ↓                        ↓
                       行动轨迹                  发帖/态度
                       需求变化                  群体角色
                           └────────────────────────┘
                                   共同注入访谈人设
```

Step 4 依赖 Step 2 / Step 3 的仿真结果（有其一即可，两者均有则人设更丰富）。如果两个仿真均未运行，则仅使用 Step 1 的静态 profile。

---

## 页面布局（三阶段流转）

```
┌──────────────────────────────────────────────────────────────┐
│  NavBar  (1 ✓ · 2 ✓ · 3 ✓ · 4 active)              [← Back]  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  阶段 A：问卷设计   →   阶段 B：逐个访谈   →   阶段 C：汇总   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 阶段 A：问卷设计

### 输入区

```
商品名称：  [_________________]
背景信息：  [_________________]（品类、价格定位、功能特点等）
访谈目标：  [购买意愿 ▼]（购买意愿 / 使用体验 / 改进建议 / 竞品对比）
问题数量：  [15 题 ▼]（10 / 15 / 20）

[✨ 生成问卷]
```

### 问卷草稿（可编辑）

LLM 按 5 个阶段生成问卷，生成后呈现为可编辑列表：

```
┌──────────────────────────────────────────────────────────────┐
│ #  │ 阶段       │ 题目                            │ 类型    │  │
├──────────────────────────────────────────────────────────────┤
│  1 │ basic      │ 您之前是否了解或使用过这款产品？ │ 单选    │ ✎ × │
│  2 │ basic      │ 您通常通过哪些渠道了解新产品？   │ 单选    │ ✎ × │
│  3 │ core       │ 您对该产品的第一印象是什么？     │ 开放    │ ✎ × │
│  4 │ core       │ 您最看重/不满意的功能是什么？    │ 开放    │ ✎ × │
│  5 │ attitude   │ 您对该产品的整体满意度           │ Likert  │ ✎ × │
│  6 │ attitude   │ 您的购买意愿有多强？             │ Likert  │ ✎ × │
│  7 │ reflection │ 与竞品相比，您认为差距在哪里？   │ 开放    │ ✎ × │
│  8 │ closing    │ 您会向朋友推荐这款产品吗？       │ 单选    │ ✎ × │
│ ...│            │                                 │         │     │
│ + 添加问题                                                   │
└──────────────────────────────────────────────────────────────┘

[← 重新生成]                            [确认问卷，开始访谈 →]
```

**可编辑操作：**
- 点击题目文字 → 内联编辑
- 点击类型 → 下拉切换（单选 / 多选 / 开放 / Likert / 数字）
- 拖拽调整顺序
- × 删除，+ 添加

**5 个阶段含义：**

| 阶段 | 内容定位 | 示例 |
|------|---------|------|
| basic | 受访者与产品的关系 | 是否了解/用过、了解渠道 |
| core | 使用体验与功能评价 | 第一印象、具体功能评价 |
| attitude | 态度与意愿（量化） | 满意度 Likert、购买意愿 |
| reflection | 深层反思与对比 | 改进建议、竞品对比 |
| closing | 收尾 | 是否推荐、补充说明 |

---

## 阶段 B：逐个访谈

### 布局

```
┌─────────────────┬────────────────────────────────────────────┐
│  Agent 列表     │  访谈对话区                                 │
│                 │                                             │
│  ○ 林晓雨  待   │  ── 林晓雨 · core 阶段 · Q4/15 ──          │
│  ✓ 陈浩宇  完   │                                             │
│  ⟳ 张伟   中   │  🎙 您之前是否了解或使用过这款产品？        │
│  ○ ...          │                                             │
│                 │  💬 之前在小红书上刷到过，但没深入看，      │
│  点击任意       │     主要是觉得价格有点贵。                  │
│  Agent 开始     │                                             │
│  或继续访谈     │  🎙 ↪ 当时是什么让您没有继续了解呢？       │
│                 │                                             │
│                 │  💬 消费预算不够，而且当时没有迫切需求，    │
│                 │     就划过去了。                            │
│                 │                                             │
│                 │  🎙 您对该产品的第一印象是什么？            │
│                 │  ⏳ 生成中...                               │
│                 │                                             │
│                 │  ────────────────── 访谈完成 ──────────── │
│                 │  [📄 查看完整报告]                          │
└─────────────────┴────────────────────────────────────────────┘
```

**Agent 列表状态：**
- `待`（灰）：未开始访谈
- `中`（蓝）：访谈正在进行
- `完`（绿）：访谈已完成（可重新查看）

**对话区规则：**
- 🎙 = 访谈员提问（含追问，追问前缀 `↪`）
- 💬 = 虚拟 Agent 回答
- 追问显示为内联缩进，不另起一轮

### 单个 Agent 访谈完成后的摘要

```
┌─ 林晓雨 访谈摘要 ──────────────────────────────────┐
│  整体态度：略负面（价格敏感，功能认可）              │
│  购买意愿：3 / 5                                    │
│  关键意见：                                         │
│    · 价格是最大障碍，期待大促活动                   │
│    · 外观设计符合个人审美                           │
│    · 功能上与预期差距不大，但没有惊喜               │
│  [📄 完整 QA 记录]                                 │
└────────────────────────────────────────────────────┘
```

---

## 阶段 C：汇总分析

完成 ≥ 2 个 Agent 访谈后可进入。

```
┌──────────────────────────────────────────────────────────────┐
│  已访谈：6 人  ·  总问答轮次：84  ·  平均追问率：38%          │
│                                                              │
│  态度分布                                                    │
│  正面 ████████ 3人    中立 ████ 2人    负面 ██ 1人           │
│                                                              │
│  购买意愿均值：3.2 / 5                                       │
│                                                              │
│  关键意见聚合（LLM 提炼）                                    │
│  · 价格敏感是最普遍的阻力（5/6 人提及）                      │
│  · 外观设计获得较高认可（4/6 人正面评价）                    │
│  · 功能与竞品差异化不明显（3/6 人提及）                      │
│                                                              │
│  [🤖 AI 深度解读]  ← 触发 LLM 生成完整分析报告              │
└──────────────────────────────────────────────────────────────┘
```

---

## 核心技术：仿真结果注入人设

### 从 Step 2（城市仿真）提取行为摘要

```python
def extract_urban_summary(sim_id: str, agent_id: int) -> dict:
    steps = get_simulation_steps(sim_id)
    agent_steps = [
        a for s in steps
        for a in s["agents"] if a["id"] == agent_id
    ]
    return {
        "behavior_pattern": _infer_pattern(agent_steps),  # 如"早起型，偏好独处学习"
        "frequent_intentions": _top_intentions(agent_steps, n=3),
        "avg_social_need": mean(a["needs"]["social"] for a in agent_steps),
        "message_activity": sum(1 for a in agent_steps if a.get("sent")),
    }
```

### 从 Step 3（线上仿真）提取发帖摘要

```python
def extract_online_summary(sim_id: str, agent_id: int) -> dict:
    # 从 oasis.db 或 agents.csv 读取
    return {
        "role":            agent_row["group"],          # KOL / 普通用户 / 潜水用户
        "sample_posts":    agent_row["posts"][:3],      # 最近3条发帖
        "final_attitude":  agent_row["final_attitude"],
        "interaction_style": _describe_style(agent_row),
    }
```

### 注入 Virtual Agent Responder Prompt

```
你是 {name}，{occupation}，{mbti}，兴趣爱好：{interests}。
性格：{personality}

【最近的生活状态（行为仿真数据）】
- 行为规律：{behavior_pattern}
- 常见意图：{frequent_intentions}
- 社交活跃度：{"偏低，倾向独处" if avg_social < 0.5 else "较高，喜欢互动"}

【最近的线上表达（舆论仿真数据）】
- 社交媒体角色：{role}
- 你最近发过：{sample_posts}
- 当前态度倾向：{attitude_description}

现在你参与一场关于「{product_name}」的访谈。
请根据你的个性、生活状态和线上习惯，以自然口语回答问题（1~3句话）。
不要刻意表现，保持真实。
```

---

## 访谈引擎：Follow-up Gate

直接移植 `intelligent_interview/demo/backend_service.py` 中的核心逻辑，去除 Streamlit 依赖。

### 移植的组件

| 原组件 | 新文件 | 适配改动 |
|--------|--------|---------|
| `BackendState` dataclass | `backend/interview/state.py` | 无改动，直接复制 |
| `LiveStagePlanner` | `backend/interview/planner.py` | 无改动 |
| Follow-up Gate 效用评分 | `backend/interview/followup.py` | 权重微调（产品访谈场景） |
| 事件检测（拒绝/消极/矛盾） | `backend/interview/events.py` | 无改动 |
| `build_report` | `backend/interview/report.py` | 改为产品意见维度 |

### 效用函数（产品访谈微调版）

```python
# 产品访谈中更看重意见深度和态度确认，调低风险权重
utility = (
    0.35 * coverage    # 问题覆盖推进
  + 0.30 * discovery   # 新信息发现（比原系统更高）
  + 0.20 * recovery    # 回收不充分的答案
  - 0.08 * cost        # turn 消耗
  - 0.07 * risk        # 追问风险（产品访谈风险低，权重降低）
)
threshold = 0.30       # 比原系统（0.35）更激进，因为虚拟 Agent 不会真的拒绝
```

### Interview Runner 主流程

```python
async def run_interview(agent_profile: dict, questions: list, product_name: str,
                         urban_summary: dict, online_summary: dict) -> AsyncGenerator:
    state = BackendState(topic=product_name, questions=questions)
    persona_prompt = build_persona_prompt(agent_profile, urban_summary, online_summary)

    while not state.finished:
        question = planner.next_question(state)
        state.current_qid = question["id"]

        # 虚拟 Agent 回答
        answer = await agent_responder.reply(persona_prompt, question, state)
        yield {"type": "qa", "question": question["question"], "answer": answer}

        # 事件检测
        event = detect_event(answer)

        # Follow-up Gate
        followup = followup_gate.decide(state, answer, event)
        if followup:
            fu_answer = await agent_responder.reply(persona_prompt, followup, state)
            yield {"type": "followup", "question": followup["text"], "answer": fu_answer}
            state.record_followup(question["id"], followup, fu_answer)

        state.record_answer(question["id"], answer, event)

    report = build_report(state, agent_profile)
    yield {"type": "done", "report": report}
```

---

## 后端 API（Flask Blueprint）

**文件：** `backend/interview/routes.py`

```python
# 生成问卷草稿
POST /api/interview/generate-questionnaire
  body:   { product_name, background, goal, num_questions }
  return: { questions: [{ id, stage, question, type, options? }] }

# 创建访谈会话（保存确认后的问卷）
POST /api/interview/sessions
  body:   { questions, product_name, sim_id_urban?, sim_id_online?, agent_ids }
  return: { session_id }

# 获取该会话下各 Agent 的访谈状态
GET  /api/interview/sessions/<session_id>/agents
  return: [{ agent_id, name, status: pending|running|done, report? }]

# SSE 实时访谈流（点击 Agent 后连接）
GET  /api/interview/sessions/<session_id>/agents/<agent_id>/stream
  events:
    { type: "qa",       question, answer }
    { type: "followup", question, answer }
    { type: "done",     report: { qa_pairs, summary, attitude_score } }

# 获取单个 Agent 完整报告
GET  /api/interview/sessions/<session_id>/agents/<agent_id>/report
  return: { qa_pairs, summary, attitude_score, key_opinions }

# 汇总分析报告（≥2 个 Agent 完成后可调用）
GET  /api/interview/sessions/<session_id>/summary
  return: { attitude_distribution, avg_purchase_intent, key_findings }

# AI 深度解读（触发 LLM 生成）
POST /api/interview/sessions/<session_id>/analyze
  return: { analysis_report }
```

---

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `backend/interview/__init__.py` | 新建 | Blueprint 注册 |
| `backend/interview/routes.py` | 新建 | 上述 6 个接口 |
| `backend/interview/state.py` | 新建（移植）| BackendState dataclass |
| `backend/interview/planner.py` | 新建（移植）| LiveStagePlanner |
| `backend/interview/followup.py` | 新建（移植）| Follow-up Gate 效用评分 |
| `backend/interview/events.py` | 新建（移植）| 事件检测 |
| `backend/interview/responder.py` | 新建 | Virtual Agent Responder（核心新组件）|
| `backend/interview/report.py` | 新建（移植）| 访谈报告生成 |
| `backend/interview/persona.py` | 新建 | 仿真结果提取 + 人设 prompt 构建 |
| `backend/app.py` | 修改 | 注册 interview blueprint |
| `frontend/src/views/InterviewView.vue` | 新建 | 三阶段页面 |
| `frontend/src/api/index.js` | 修改 | 新增 interview 相关 API 函数 |
| `frontend/src/router/index.js` | 修改 | 新增 `/interview` 路由 |
| `frontend/src/views/OnlineSimView.vue` | 修改 | Next Step 按钮指向 `/interview` |

---

## 实现优先级

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P1 | 移植 BackendState + LiveStagePlanner + 事件检测 | 高 |
| P1 | Virtual Agent Responder（persona prompt 设计）| 高 |
| P1 | 问卷生成 LLM prompt + `/generate-questionnaire` 接口 | 高 |
| P1 | Interview Runner 主流程 + SSE 流 | 高 |
| P1 | 前端阶段 A（问卷设计页）+ 阶段 B（对话区） | 高 |
| P2 | Follow-up Gate 集成 | 中 |
| P2 | 仿真结果提取（urban + online → persona） | 中 |
| P2 | 单 Agent 访谈摘要报告 | 中 |
| P3 | 前端阶段 C（汇总面板 + 态度分布图） | 中 |
| P3 | AI 深度解读（跨 Agent 分析）| 低 |

---

## 待讨论问题

1. **问卷持久化**：确认后的问卷是否需要存到磁盘（允许刷新后恢复）？
2. **并发访谈**：用户能否同时开启多个 Agent 的访谈流，还是严格一次一个？
3. **访谈轮次上限**：建议设为 `num_questions × 1.5`（含追问），防止无限循环。
4. **仿真结果可选性**：若 Step 2/3 未完成，访谈仍可进行（纯 profile 人设），是否在 UI 上有提示？
5. **虚拟 Agent 回答语言**：统一中文，还是跟随 profile 的 `language` 字段？
