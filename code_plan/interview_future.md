# Interview 模块后续改进方向

## 当前状态

本轮已经完成的改造重点：

- 问卷生成改为固定阶段配额，并对 `10 / 15 / 20` 题做严格校验。
- 前端问卷编辑支持拖动排序。
- 访谈启动交互改为“点击用户仅查看，右侧显式执行访谈”。
- interviewer 增加基于画像的固定话术风格层。
- `qa_pairs` 已区分：
  - `question`: 实际问出的文本
  - `source_question`: canonical 原始问卷题目
  - `question_style`: 当前访谈话术风格
- 追问从“几乎没有”回调到“有限但可触发”，仍保持每题最多 1 次 follow-up。

## 已识别但本轮未实现的问题

### 1. 评分过于集中

现象：

- 多个受访者的最终态度分容易集中在 `4.0` 左右。
- 汇总页上经常出现明显偏同质化的打分结果。

当前原因：

- 现有实现位于 [backend/interview/engine.py](/Users/daishangzhe/实验室项目/个体建模服务构建/Individual_Simulation_platform/Individual_Simulation_Platform/backend/interview/engine.py) 的 `_infer_attitude_score(...)`。
- 这段逻辑优先命中第一个 Likert 关键词，例如 `"满意"`、`"可能"`，一旦命中会直接返回固定分值。
- 如果没有命中结构化词，就退回简单的正负关键词计数，区分度仍然偏弱。
- 评分没有结合：
  - 用户画像差异
  - 多题联合判断
  - follow-up 中的补充立场
  - 不同题型权重

后续建议：

- 方案 A：改为按 `attitude` 阶段题目加权求分，减少单条命中的支配性。
- 方案 B：将 `core / reflection` 的显著正负意见转成补充分项，而不是只看 Likert。
- 方案 C：输出 `score_breakdown`，把“命中哪道题、为什么得这个分”显式记录下来，便于调参。

### 2. 是否引入完整 interviewer agent

当前实现：

- 现在仍是“规则编排 + 话术改写 + follow-up 生成”，没有独立 interviewer agent。

优点：

- 可控、稳定、便于约束主问题顺序。

不足：

- 虽然已经比直接照读问卷自然，但复杂场景下仍不够像真人访谈。
- 访谈员无法动态调整更长的对话节奏，只能在单轮 question / follow-up 上做优化。

后续建议：

- 保持当前 canonical 问题与预算 gate，不要把问题推进权完全交给自由 agent。
- 如果需要更自然的风格，可考虑引入“受限 interviewer agent”：
  - 输入 canonical question、当前 stage、剩余预算、受访者画像
  - 输出实际问法与追问候选
  - 最终是否执行仍由现有 gate 决定

### 3. session 持久化

当前实现：

- `_sessions` 在内存中维护。
- 重启后端后，访谈 session 会丢失。

影响：

- 不适合长周期协作和多人同时调试。
- 也不利于回放、重分析和后续质检。

后续建议：

- 至少把以下内容落盘：
  - session metadata
  - questions
  - agent states
  - qa_pairs / report
- 优先建议使用文件持久化目录，而不是先上数据库，以降低改造成本。

### 4. 更细粒度的访谈指标和调参面板

当前实现：

- report 中只有基础 `process_metrics`，例如：
  - `followup_rate`
  - `answer_coverage`
  - `turns_used`

仍缺少的可观测性：

- 每题是否触发 follow-up 的原因
- repair 与 explore 的占比
- 各 stage 的完成率
- 结构化题 exact option 命中率
- 因“过于抽象”触发追问的次数

后续建议：

- 在 `followup_history` 上继续补字段，并把可视化留给前端 summary 页。
- 最终可以加一个 interview 调参面板，集中展示：
  - stage 配额
  - follow-up budget
  - 不同 stage 的 explore 开关
  - 追问触发阈值

## 协作建议

如果下一轮多人并行开发，建议按下面拆分：

- 前端方向：继续打磨问卷编辑器和访谈回放体验。
- 访谈编排方向：继续调 `engine.py` 的追问阈值与 style 规则。
- 评分方向：重写 `_infer_attitude_score(...)`，同时保留兼容旧 summary 输出。
- 持久化方向：给 session/report 增加稳定落盘与重载能力。
