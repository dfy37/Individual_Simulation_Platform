# Setup 自然语言采样与 IPF 改造计划

> 目标：改造 Step 1 `SetupView`，将当前“前端等间距抽样”升级为“自然语言需求 -> 结构化采样约束 -> IPF 采样 -> 预览确认 -> 仿真启动”的完整链路。

---

## 1. 当前架构理解

### 前端

- `frontend/src/views/SetupView.vue`
  - 当前只负责加载画像、显示关系图、滑块选择人数
  - `selectedAgents` 逻辑是按 `user_id` 排序后做等间距抽样
  - 点击 Next 后，仅把 `num_agents` 和 `agent_ids` 写入 `localStorage`

- `frontend/src/views/SimulationView.vue`
  - 启动仿真时从 `localStorage('agentParams')` 读取参数
  - 当前只向后端发送 `num_agents / num_steps / tick_seconds / concurrency / start_time`
  - 没有把 `agent_ids` 发给后端

### 后端

- `backend/app.py`
  - 提供 `/api/profiles`、`/api/relationships`、`/api/simulations`
  - 当前没有“按条件采样”的 API

- `backend/simulator.py`
  - 启动仿真时读取 `PROFILES_PATH`
  - 当前直接取前 `num_agents` 条画像进入仿真
  - 没有使用前端在 setup 阶段选出的 `agent_ids`

- `backend/config.py`
  - 当前 `PROFILES_PATH` 指向 `backend/users/student_profiles.json`

### 数据现状

- 目标切换数据源：
  - `/Users/duanfeiyu/Documents/Fudan-DISC/Individual_Simulation_Platform/backend/users/student_profiles_expanded.json`
- 已确认字段：
  - `user_id`
  - `name`
  - `gender`
  - `age`
  - `occupation`
  - `major`
  - `interests`
  - `mbti`
  - `personality`
  - `bio`
  - `sample_posts`
  - `initial_needs`
- 数据量：
  - 9824 条

---

## 2. 当前存在的核心问题

### 问题 1：Setup 选人和 Simulation 跑人不一致

当前 setup 页面虽然生成了 `agent_ids`，但后端仿真并没有按这些 `agent_ids` 加载画像，而是简单取前 `num_agents` 条。因此：

- setup 页面看到的是一批人
- simulation 实际运行的是另一批人

这个问题必须优先修复，否则后续自然语言采样只是界面层假动作。

### 问题 2：采样逻辑完全在前端，无法支撑复杂约束

当前抽样逻辑只是前端 computed：

- 无法支持自然语言需求
- 无法支持硬约束与软约束
- 无法支持可解释的采样诊断
- 无法保证仿真阶段复现同一批样本

### 问题 3：IPF 不能直接对原始文本字段使用

IPF 适合处理离散类别的边际分布约束，因此必须先把画像特征做 bucket 化，例如：

- 性别
- 年龄段
- 教育层次
- 专业类别
- 活跃度类别

不能直接拿原始 `major / interests / bio / sample_posts` 文本做 IPF。

### 问题 4：关系图可能与扩展画像库不一致

当前 `relationships.json` 是预生成关系网。扩展画像库有 9824 人，但现有关系图未必覆盖全部用户。若 setup 从 9824 人中任意采样：

- 关系图可能缺边
- 图结构可能稀疏
- 后续可能需要动态建图或降级展示

---

## 3. 改造目标

将 setup 阶段改造为：

1. 用户输入自然语言采样需求
2. 系统将其解析为结构化采样规格 `sampling_spec`
3. 基于候选画像库做 hard filter
4. 基于目标边际分布执行 IPF
5. 按权重无放回抽样得到目标人群
6. 前端展示采样结果、实际分布、与目标偏差
7. 用户确认后进入 simulation
8. simulation 按 setup 已确认的 `agent_ids` 精确加载同一批人

---

## 4. 总体设计

### 4.1 新的数据流

```text
SetupView
  -> 输入自然语言 query + 目标人数 target_size
  -> POST /api/profiles/sample-preview
  -> 后端解析 query -> sampling_spec
  -> 后端 hard filter + feature engineering + IPF + weighted sample
  -> 返回 selected_profiles + sampling_spec + diagnostics
  -> 前端预览并确认
  -> localStorage 保存 selected_agent_ids

SimulationView
  -> POST /api/simulations
  -> body 包含 agent_ids
  -> backend/simulator.py 按 user_id 精确加载 profile
  -> 仿真运行
```

### 4.2 设计原则

- 采样逻辑放在后端，不放前端
- 自然语言解析输出必须是受限 JSON schema
- IPF 只处理离散化后的特征维度
- setup 必须展示“系统理解成了什么”，不能黑盒抽样
- simulation 必须严格复用 setup 已确认样本

---

## 5. 分阶段实施计划

## Phase 1：修正真实选人链路

### 目标

先保证“setup 选谁，simulation 就跑谁”。

### 改动内容

- `backend/config.py`
  - 将 `PROFILES_PATH` 从 `student_profiles.json` 切换为 `student_profiles_expanded.json`

- `frontend/src/views/SimulationView.vue`
  - `startSim()` 时把 `agent_ids` 一起传给后端

- `backend/app.py`
  - `/api/simulations` 接收 `agent_ids`

- `backend/simulator.py`
  - 根据 `agent_ids` 从全量画像中按 `user_id` 精确选取 profile
  - 若未传 `agent_ids`，再退回旧逻辑

### 验收标准

- setup 页面确认的 agent 列表与 simulation 中 `meta.agents` 一致
- 仿真日志和结果文件中出现的是 setup 已选中的用户

---

## Phase 2：建立采样模块骨架

### 目标

把采样逻辑从 `SetupView` 拆到后端服务层。

### 建议新增模块

- `backend/sampling/schema.py`
  - 定义 `sampling_spec` 结构

- `backend/sampling/features.py`
  - 把 profile 原始字段映射成可采样分类特征

- `backend/sampling/ipf.py`
  - 实现 IPF 权重拟合

- `backend/sampling/service.py`
  - 提供统一服务入口：filter -> parse spec -> IPF -> sample -> diagnostics

### 建议新增 API

`POST /api/profiles/sample-preview`

请求示例：

```json
{
  "query": "帮我抽30个复旦学生，男女尽量均衡，本科生为主，数学和计算机相关专业多一些，也保留少量博士生，最好社交媒体活跃一些",
  "target_size": 30
}
```

返回示例：

```json
{
  "sampling_spec": {},
  "selected_profiles": [],
  "summary": {},
  "diagnostics": {}
}
```

### 验收标准

- 后端可独立完成采样预览
- 前端不再自行决定最终样本集合

---

## Phase 3：自然语言需求解析

### 目标

将自然语言需求稳定转换为结构化采样规格 `sampling_spec`。

### 推荐策略

采用“规则优先 + LLM 补充”的混合方案。

#### 规则优先处理的内容

- 人数
- 性别均衡
- 本科/硕博比例
- 年龄范围
- 专业倾向
- 明确的硬过滤条件

#### LLM 补充解析的内容

- “社交媒体活跃”
- “尽量多样化”
- “偏理工科”
- “校园生活气息浓一些”

### 建议新增模块

- `backend/sampling/nl_parser.py`

### `sampling_spec` 建议结构

```json
{
  "target_size": 30,
  "hard_filters": {
    "occupation_contains": ["复旦大学"],
    "age_range": [18, 30]
  },
  "marginals": {
    "gender": {
      "male": 0.5,
      "female": 0.5
    },
    "education_bucket": {
      "undergrad": 0.7,
      "grad_phd": 0.3
    },
    "major_bucket": {
      "math_cs": 0.4,
      "other": 0.6
    },
    "activity_bucket": {
      "high_activity": 0.4,
      "normal_activity": 0.6
    }
  },
  "soft_preferences": [
    "interests 包含校园生活或社交媒体相关主题"
  ],
  "rationale": "系统对自然语言的结构化解释"
}
```

### 验收标准

- 相同输入能够稳定输出同结构 JSON
- 输出字段可直接驱动后续 IPF 过程
- setup 页面可展示解析结果给用户确认

---

## Phase 4：画像特征工程

### 目标

把原始 profile 映射成适合 IPF 的离散化特征。

### 建议特征

- `gender`
  - `male / female / unknown`

- `age_bucket`
  - `<=20 / 21-23 / 24-26 / 27+`

- `education_bucket`
  - `undergrad / master / phd / grad_phd / unknown`
  - 可从 `occupation` 中推断

- `major_bucket`
  - `math_cs / economics_management / humanities_social / medicine_life / arts_media / other`

- `activity_bucket`
  - `high / medium / low`
  - 可依据 `sample_posts` 数量、bio 丰富度、interests 丰富度构造

- `interest_bucket`
  - `fashion / sports / academics / campus_life / entertainment / pets / food / travel / social_media / other`

### 验收标准

- 9824 条 profile 可批量转换为结构化 sampling features
- 各 bucket 覆盖率足够高，不出现大面积 `unknown`

---

## Phase 5：IPF 采样实现

### 目标

对候选池执行边际约束拟合，并抽取合理样本。

### 推荐流程

1. 对全量画像应用 hard filters
2. 若候选池不足，返回“约束过严”
3. 为候选池建立分类特征矩阵
4. 依据 `sampling_spec.marginals` 构造目标边际分布
5. 运行 IPF，得到每个 profile 的权重
6. 采用 weighted sampling without replacement 抽取 `target_size`
7. 对抽样结果进行偏差评估
8. 若偏差较大，重试若干次并选偏差最小结果

### 采样输出

- `selected_profiles`
- `weights`
- `target_marginals`
- `actual_marginals`
- `deviation_report`
- `explanations`

### 注意事项

- IPF 是拟合边际分布，不保证高维联合分布完美匹配
- 候选池过小、维度过多或目标过严时，IPF 可能不稳定
- 对 soft preference 不能强行做 hard constraint

### 验收标准

- 对常见 query 可稳定生成接近目标分布的人群样本
- 结果中附带偏差解释，便于前端展示

---

## Phase 6：改造 Setup 界面

### 目标

把 `SetupView` 从 slider-only 页面升级为“自然语言采样控制台”。

### UI 建议

在当前右侧 config panel 增加新的采样区块，建议放在 `Agent Selection` 上方：

```text
Research Sampling
- Natural language input
- Target size slider / input
- Generate sample
- Resample
- Parsed constraints
- Sampling diagnostics
```

### 新增交互

1. 用户输入自然语言需求
2. 设置目标人数
3. 点击“Generate Sample”
4. 前端调用 `/api/profiles/sample-preview`
5. 展示：
   - 结构化 `sampling_spec`
   - 目标分布
   - 实际分布
   - 偏差诊断
   - 当前抽到的用户群体
6. 用户可点击“Resample”
7. 用户确认后点击 Next Step

### 状态建议

- `samplingQuery`
- `targetSize`
- `samplingPreview`
- `samplingSpec`
- `selectedProfiles`
- `samplingDiagnostics`
- `samplingLoading`
- `samplingMode`

### 本阶段不建议一开始做的内容

- 不先做过于复杂的图上可视编码
- 不先做 profile 卡的复杂交互
- 不先把所有软偏好都映射成高级约束

### 验收标准

- 用户可以通过一句自然语言完成样本选择
- 用户能看见系统如何理解其要求
- Next Step 传递的是已确认的 `selected_agent_ids`

---

## Phase 7：仿真阶段接入研究上下文

### 目标

将 `sampling_spec` 或用户原始 query 作为研究上下文写入仿真 metadata，供后续阶段使用。

### 建议改动

- `frontend/src/views/SetupView.vue`
  - 将 `sampling_query / sampling_spec / selected_agent_ids` 一并保存到 `localStorage`

- `frontend/src/views/SimulationView.vue`
  - 启动仿真时一并传给后端

- `backend/app.py`
  - `/api/simulations` 接收并存储 `sampling_query / sampling_spec`

- `backend/simulator.py`
  - 将这些字段写入 `meta.json`

### 用途

- 为后续 online sim 和 interview 阶段保留研究背景
- 后续如需 prompt 注入，也有结构化上下文可用

### 验收标准

- `meta.json` 中可追溯本次样本是如何被选出的

---

## 6. 建议文件改动清单

### 后端

- `backend/config.py`
  - 切换 `PROFILES_PATH`

- `backend/app.py`
  - 新增 `POST /api/profiles/sample-preview`
  - 更新 `POST /api/simulations` 参数接收

- `backend/simulator.py`
  - 按 `agent_ids` 精确选取 profile
  - 持久化 `sampling_query / sampling_spec`

- `backend/sampling/schema.py`
- `backend/sampling/features.py`
- `backend/sampling/nl_parser.py`
- `backend/sampling/ipf.py`
- `backend/sampling/service.py`

### 前端

- `frontend/src/views/SetupView.vue`
  - 增加自然语言输入、采样按钮、采样预览、诊断展示

- `frontend/src/views/SimulationView.vue`
  - 启动仿真时传递 `agent_ids`

- `frontend/src/api/index.js`
  - 新增 `sampleProfilesPreview()`

---

## 7. 风险与限制

### 限制 1：当前仿真 agent 数量上限仍然较低

`backend/simulator.py` 当前存在：

- `num_agents = min(int(p["num_agents"]), 37)`

因此即使画像库扩展到 9824，单次仿真依然受当前核心逻辑限制。需要确认是否继续保留该上限。

### 限制 2：关系图未必覆盖扩展画像库

从 9824 人中抽样时：

- setup 关系图可能出现无边
- 旧关系网可能无法准确反映新样本结构

后续可选方案：

- 接受稀疏图
- 仅展示已知关系子图
- 动态按兴趣/专业/属性生成相似性图

### 限制 3：自然语言包含软性、不确定表达

例如：

- “尽量均衡”
- “活跃一点”
- “稍微偏理工科”

这些都应当转成 soft preference 或目标边际，而不是硬过滤。

### 限制 4：IPF 不等于最优群体构造器

IPF 适合控制边际分布，但不能保证：

- 高阶联合分布完美
- 每个个体都“语义最相关”

必要时可在 IPF 前后叠加：

- 规则打分
- 语义相关性重排
- 多轮抽样后最优选择

---

## 8. 推荐开发顺序

### P1

- 修正 setup -> simulation 的真实选人链路
- 切换到 `student_profiles_expanded.json`

### P2

- 完成 feature engineering
- 完成后端 `sample-preview` API 骨架

### P3

- 完成自然语言解析
- 完成 IPF + weighted sample

### P4

- 改造 setup 页面交互
- 展示采样解释、偏差、已选群体

### P5

- 再考虑关系图增强、重采样策略、研究上下文注入

---

## 9. 最终预期效果

用户在 setup 页面输入一句自然语言，例如：

> “帮我抽 24 个复旦学生，男女尽量均衡，本科生为主，保留少量研究生，最好数学和计算机专业多一些，也要有比较活跃的社交媒体用户。”

系统将完成：

1. 自动解析为结构化采样需求
2. 在 9824 条画像中筛选候选池
3. 使用 IPF 拟合目标分布并抽样
4. 展示实际样本的分布与偏差
5. 用户确认后进入 simulation
6. simulation 精确运行这批已确认用户

这才是完整、可复现、可解释的 setup 采样方案。
