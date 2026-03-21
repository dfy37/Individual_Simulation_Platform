# SimulationView (urban_sim) — 设计方案

> **职责**：Step 2 — 配置仿真参数、启动仿真、在地图上展示 Agent 行为、查看历史仿真结果
> **页面路径**：`/simulation`

---

## 页面定位

```
Step 1: SetupView         Step 2: SimulationView              Step 3: OnlineSimView
  选 Agent → Next Step ──▶  配置参数 → Start Simulation          线上环境仿真
                             地图展示 + 历史列表
                             完成后 [Next Step →] ─────────────▶
```

---

## 已实现功能

### 页面布局

```
┌──────────────────────────────────────────────────────────────────┐
│  NavBar  (step 1 done ✓ · step 2 active · step 3 pending)  [←Back]│
├──────────────────┬───────────────────────────────────────────────┤
│  左侧栏 (280px)   │  地图主区 (flex: 1, position: relative)        │
│                  │                                               │
│  ┌─ Config ────┐ │  [CartoDB Light 底图 · 复旦校区]               │
│  │ Steps        │ │                                               │
│  │ Time/Step    │ │  每个 Agent: L.circleMarker (r=10)            │
│  │ Start Time   │ │    颜色: wellness-based (红/橙/色板)           │
│  │ Concurrency  │ │    hover → tooltip (名字 + 当前意图)           │
│  │ [▶ Start]   │ │    click → 右侧详情抽屉                        │
│  └─────────────┘ │                                               │
│                  │  左上角覆盖层：Step X/N · 时间                  │
│  ┌─ History ───┐ │                                               │
│  │ ● sim-003   │ │  底部回放栏：◀ ▶ ────●──── X/N  [Auto ✓]       │
│  │   Completed │ │                                               │
│  │ ● sim-001   │ │  右侧消息面板（280px，可折叠）                   │
│  └─────────────┘ │                                               │
│                  │                                               │
│  [Next Step →]   │                                               │
│  (sticky bottom) │                                               │
└──────────────────┴───────────────────────────────────────────────┘
```

### 三步仿真机制（backend）

每个仿真步骤包含三个阶段：

| 阶段 | 类型 | 内容 |
|------|------|------|
| Phase 0（待实现） | 事件检查 | 概率触发特殊事件，注入记忆/需求 |
| Phase 1: move_phase | LLM ReAct | 工具调用循环，决策移动目标并执行 |
| Phase 2: send_phase | LLM | 决策是否发消息、发给谁、发什么 |
| Phase 3: receive_phase | 规则 | 处理收到的消息，更新社交需求 |

### 消息 Channel 机制（已实现）

- `target=int`：私信（只有目标可见），类似微信
- `target="nearby"`：附近广播（100m 半径内的人可见）
- `target="all"`：全体广播

SSE 的 `channel_messages` 仅包含 `nearby` + `all` 消息（私信不在公共面板显示）。

### Gravity Model（已实现）

POI/AOI 候选列表用 gravity model 采样，而非随机选取：

```python
weight = (ring_density) / distance²
# 分 1km 环计算密度，按权重概率采样
```

### 前端状态模型

```javascript
// 仿真参数
const cfgSteps       = ref(12)
const cfgTick        = ref(3600)
const cfgStartTime   = ref('2024-09-02T08:00')
const cfgConcurrency = ref(5)

// 运行状态
const activeSimId  = ref(null)     // 当前 SSE 连接的仿真
const simRunning   = ref(false)

// 地图展示（可能是历史记录）
const viewingSimId    = ref(null)
const viewingSteps    = ref([])
const displayIdx      = ref(-1)
const autoFollow      = ref(true)

// 历史列表
const historyList = ref([])
```

---

## 待实现功能一：消息 Channel 完整展示

> **状态**：部分实现（消息机制已有，前端展示不完整）

### 后端改动（`simulator.py`）

agent snapshot 新增 `sent` / `received` 字段：

```python
result.append({
    ...
    "sent":     rec.get("sent"),          # {target, content} | null
    "received": rec.get("received", []),  # [{sender_id, sender_name, content, target_type}]
})
```

step event 新增 `channel_messages`：

```python
ev = {
    "type":             "step",
    ...
    "channel_messages": [
        {
            "sender_id":   m.sender_id,
            "sender_name": m.sender_name,
            "content":     m.content,
            "target":      m.target,   # int | "nearby" | "all"
        }
        for m in sim.channel.all_messages
        if m.target in ("nearby", "all")  # 私信不进公共面板
    ],
}
```

### 前端改动（`SimulationView.vue`）

**消息面板（右侧，280px，可折叠）**

```
┌─ Messages  Step N  [折叠按钮] ──────────────┐
│                                             │
│  ● Alice → 附近                             │
│    "有人一起去图书馆吗？"                    │
│                                             │
│  ● Bob → 所有人                             │
│    "紧急：宿舍停电了"                        │
│                                             │
│  [空状态] No messages this step             │
└─────────────────────────────────────────────┘
```

消息卡片：左侧彩色竖线（sender 颜色）+ target badge（附近/广播）+ 内容

**Agent 详情抽屉新增 Messages 区块**（在 Event History 之前）：

```
── MESSAGES ──────────────────
↑ 发送  →附近: "有人一起去图书馆吗？"
↓ 收到  Bob: "好的，我在南门等你"（私信）
```

消息行样式（两行布局，已实现）：
- 第一行：发送方向图标 + 发送方/接收方名字 + target badge
- 第二行：消息内容

### 实施顺序

```
1. simulator.py 后端 sent/received/channel_messages 字段
2. SimulationView.vue — 消息面板
3. SimulationView.vue — 抽屉 Messages 区块
4. SimulationView.vue — event history 行 ↑↓ badge（可选）
5. 地图消息气泡（可选，视觉亮点）
```

---

## 待实现功能二：出行经验库（Travel Experience Library）

> **状态**：待实现
> **目标**：给每个 Agent 加上出行经验库，用活动模板 + 熟悉地点两层结构约束出行，减少完全随机探索

### 核心思路

真实人的出行有两个层次：
- **时间层（ActivityTemplate）**：什么时间段做什么事，来自 Profile 静态生成
- **空间层（FamiliarPlaces）**：习惯去哪里做这件事，在仿真过程中动态积累

两者叠加在 gravity model 和 move_phase prompt 上，形成"软约束"：LLM 仍可自主决策，但有偏向。

### 数据结构设计

```python
@dataclass
class ActivitySlot:
    start_hour:     int          # 开始小时，如 8
    end_hour:       int          # 结束小时，如 10
    activity:       str          # 活动名，如 "学习"
    poi_categories: list[str]    # 优先 POI 类别，如 ["education_institution"]
    location_hints: list[str]    # 给 LLM 的地点提示，如 ["图书馆", "教学楼"]

class TravelProfile:
    slots: list[ActivitySlot]    # 按 start_hour 排序

    def current_slot(self, hour: int) -> ActivitySlot | None:
        for slot in self.slots:
            if slot.start_hour <= hour < slot.end_hour:
                return slot
        return None

# PersonAgent 新增字段
self._travel_profile:   TravelProfile
self._familiar_places:  dict[str, list[int]]
# key = activity 名，value = 访问过的 aoi_id 列表（最多保留 10 个）
```

### TravelProfile 生成规则（规则驱动，无 LLM）

基础模板（所有学生共用）：

| 时间段 | 活动 | POI 类别 | 地点提示 |
|--------|------|----------|----------|
| 07:00–08:00 | 早餐 | restaurant | 食堂、早餐店 |
| 08:00–12:00 | 学习/上课 | education_institution | 图书馆、教学楼 |
| 12:00–13:00 | 午饭 | restaurant | 食堂、餐厅 |
| 13:00–14:00 | 午休 | — | 宿舍、休息区 |
| 14:00–17:30 | 学习/上课 | education_institution | 图书馆、教学楼 |
| 17:30–19:00 | 晚饭 | restaurant | 食堂、餐厅 |
| 19:00–21:30 | 自由时间 | 由兴趣决定 | 见个性化规则 |
| 21:30–23:00 | 休息/回宿舍 | — | 宿舍 |

个性化规则（覆盖"自由时间"槽）：
- `interests` 含 "运动/健身/篮球" → 晚间槽加 `sports_facility`，提示"操场、健身房"
- `interests` 含 "阅读/学习" → 晚间槽加 `education_institution`，提示"图书馆"
- `mbti` 以 E 开头（外向）→ 晚间加社交槽，提示"咖啡厅、广场"
- `mbti` 以 I 开头（内向）→ 自由时间默认保留独处/学习

每个时间边界加 ±30 分钟随机偏移，避免所有 Agent 完全同步。

### 集成点

**1. move_phase prompt 注入**（`_build_move_system_prompt`）

```
## 今日出行习惯
当前时间段（08:00–10:00）建议活动：学习
优先前往：图书馆、教学楼
你常去的学习地点：文科图书馆（根据以往记录）
```

**2. familiar_places 积累**（move_phase Act 阶段结束后）

```python
if current_slot and person.position.aoi_id:
    activity = current_slot.activity
    if activity not in self._familiar_places:
        self._familiar_places[activity] = []
    aoi_id = person.position.aoi_id
    if aoi_id not in self._familiar_places[activity]:
        self._familiar_places[activity].append(aoi_id)
        self._familiar_places[activity] = self._familiar_places[activity][-10:]
```

**3. gravity model 熟悉度加权**（`_gravity_sample`）

```python
FAMILIARITY_BOOST = 2.0

def _gravity_sample(candidates, sample_size, familiar_aoi_ids=frozenset()):
    ...
    for item in candidates:
        aoi_id = item[0].get("id")
        if aoi_id in familiar_aoi_ids:
            w *= FAMILIARITY_BOOST
```

### 改动文件

| 文件 | 改动 |
|------|------|
| `urban_sim/agent.py` | 新增 `ActivitySlot`、`TravelProfile` 数据类；`__init__` 生成 travel_profile；prompt 注入；Act 后更新 familiar_places |
| `urban_sim/mobility_space/environment.py` | `_gravity_sample` 新增 `familiar_aoi_ids` 参数 |
| `urban_sim/router.py` | 传递 familiar_aoi_ids 给环境 |

### 实现优先级

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P1 | ActivitySlot + TravelProfile 数据类 + 规则生成 | 高 |
| P2 | move_phase prompt 注入当前活动槽 | 高 |
| P3 | familiar_places 积累（Act 阶段后） | 中 |
| P4 | gravity model 熟悉度加权 | 中 |
| P5 | find_nearby_pois category 按槽过滤 | 低 |

---

## 待实现功能三：特殊事件触发系统

> **状态**：待实现
> **目标**：在仿真每步开始前，依概率为 Agent 触发特殊事件，从事件库中匹配最合适的事件注入 Agent 状态

### 核心概念

```
事件库（EventLibrary）
  │
  ├── EventTemplate（事件模板，静态定义）
  │     ├── 触发条件（时间/地点/需求/概率）
  │     ├── 匹配权重（与 Agent 画像的相关性）
  │     └── 效果（记忆注入 / 需求变化 / 强制消息 / 位置改变）
  │
  └── EventInstance（触发后的具体实例）

触发流程（每步，每 Agent）：
  Phase 0（新增，在 move_phase 之前）
  1. 概率检查：base_prob × 时间修正 × Agent状态修正
  2. 候选筛选：过滤满足触发条件的模板
  3. 最优匹配：规则评分（可选 LLM）
  4. 实例化：将模板结合 Agent 画像生成具体事件描述
  5. 效果注入：更新记忆 / 需求 / 标记强制发消息
```

### 数据结构

```python
@dataclass
class EventTemplate:
    event_id:    str
    name:        str           # 如 "偶遇朋友"
    category:    str           # social / academic / physical / environmental
    description: str           # 支持 {name} {location} 占位符

    # 触发条件
    time_range:       tuple[int, int] | None   # None = 全天
    location_types:   list[str] | None         # None = 任意
    min_step:         int
    cooldown_steps:   int

    # 概率
    base_prob:     float       # 如 0.08（12步期望触发约1次）
    profile_boost: dict        # {"mbti_E": 1.4, "occupation_student": 1.5}

    # 效果
    memory_injection:  str | None
    need_updates:      dict    # {"social": 0.15, "energy": -0.1}
    force_send:        bool    # 是否强制 Phase 2 发消息
    force_send_hint:   str | None
    duration_steps:    int     # 持续步数
```

### 预置事件库（`urban_sim/event_library.json`）

| 事件 | 类别 | 概率 | 效果 |
|------|------|------|------|
| 偶遇熟人 | social | 0.10 | social+0.12，强制发消息 |
| 突然下雨 | environmental | 0.05 | safety-0.1，energy-0.05 |
| 考试压力 | academic | 0.08 | energy-0.08，social-0.05 |
| 发现有趣活动 | social | 0.07 | social+0.1，强制发消息 |
| 设备故障 | physical | 0.04 | energy-0.1，safety-0.05 |

### 触发逻辑（`EventEngine`）

```python
class EventEngine:
    def try_trigger(
        self,
        agent_id:      int,
        profile:       dict,
        needs:         Needs,
        step:          int,
        hour:          int,
        location_type: str | None,
    ) -> EventTemplate | None:
        # 1. 过滤：step/冷却/time_range/location_types
        # 2. 计算 prob = base_prob × profile_boost_factor
        # 3. 独立随机抽签，返回第一个触发的事件
```

### 集成点（`simulation.py`）

Phase 0（在 move_phase 之前）：

```python
async def _run_event_check(agent, step, t):
    event = event_engine.try_trigger(...)
    if event:
        agent.inject_event(event, t)
```

`PersonAgent.inject_event()`：
1. 注入短期记忆：`[HH:MM][事件] {desc}`
2. 更新需求
3. 若 `force_send`：设置 `_pending_event_send_hint`（Phase 2 发消息时读取，然后清空）
4. 记录 `_phase_event`（供 SSE snapshot 使用）

### SSE 数据扩展

agent snapshot 新增 `event` 字段：

```json
{
  "id": 1,
  "event": {
    "name": "偶遇熟人",
    "description": "iamkiki在图书馆偶遇了一位熟人，简短地寒暄了几句"
  }
}
```

### 前端展示

Agent 详情抽屉中，在"Messages"区块上方展示事件卡片（黄色/橙色标记）。

### 改动文件

| 文件 | 改动 |
|------|------|
| `urban_sim/event_engine.py` | **新建**：EventTemplate + EventEngine 类 |
| `urban_sim/event_library.json` | **新建**：预置事件库 |
| `urban_sim/agent.py` | 新增 `inject_event()` + `_pending_event_send_hint` + `_phase_event` |
| `urban_sim/simulation.py` | 新增 Phase 0 事件检查 |
| `simulator.py` | 初始化 `EventEngine`，传入 `SimulationLoop` |
| `urban_sim/__init__.py` | 导出 `EventEngine`、`EventTemplate` |
| `frontend/src/views/SimulationView.vue` | 抽屉中新增事件卡片 |

### 实现优先级

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| P1 | EventTemplate 数据类 + event_library.json 初版 | 高 |
| P2 | EventEngine.try_trigger() 规则触发 | 高 |
| P3 | inject_event() 记忆/需求注入 | 高 |
| P4 | 仿真 Phase 0 集成 + SSE 字段扩展 | 高 |
| P5 | 前端事件卡片展示 | 中 |
| P6 | LLM 辅助最优事件匹配（替代规则评分） | 可选 |

---

## 待讨论问题

### 出行经验库
1. familiar_places 应该跨步骤持久化吗？目前设计为内存中积累，重启仿真即重置。
2. FAMILIARITY_BOOST=2.0 是否合适？太高则 Agent 永远只去同一个地方。
3. 非学生角色如何处理？当前模板面向复旦学生。

### 特殊事件系统
1. 事件频率：base_prob=0.08、12步仿真中每 Agent 期望触发约 1 次，是否合适？
2. 多 Agent 联动事件：如两个 Agent 同时在同一 AOI → 触发"相遇"事件（当前设计是单 Agent 事件）。
3. 持续事件处理：`duration_steps > 1` 的事件如何跨步骤持续影响 Agent（需 active_events 列表）。

---

## 地图与样式规范

```javascript
// 底图：CartoDB Light
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png')

// Agent marker 颜色：wellness-based
function agentFillColor(agent) {
  const w = wellness(agent.needs)
  if (w < 0.30)  return '#ef4444'  // 紧迫 → 红
  if (w < 0.55)  return '#f97316'  // 偏低 → 橙
  return AGENT_COLORS[agent.id % AGENT_COLORS.length]
}
```

样式变量：`var(--bg)` / `var(--surface)` / `var(--border)` / `var(--purple)` / `var(--grad)`
