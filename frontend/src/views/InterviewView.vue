<template>
  <div class="interview-layout">

    <!-- NavBar — Step 4 active -->
    <NavBar>
      <template #right>
        <button class="nav-back-btn" @click="router.push('/online-sim')">← Back</button>
        <div class="step-indicator">
          <div class="step-pip done">1</div>
          <div class="step-line done"></div>
          <div class="step-pip done">2</div>
          <div class="step-line done"></div>
          <div class="step-pip done">3</div>
          <div class="step-line done"></div>
          <div class="step-pip active">4</div>
          <span class="step-label">Interview</span>
        </div>
      </template>
    </NavBar>

    <div class="workspace">

      <!-- ══════════════════════════════════════════
           LEFT SIDEBAR
      ══════════════════════════════════════════ -->
      <aside class="sidebar">
        <div class="sidebar-scroll">

          <!-- Phase A: Config -->
          <template v-if="phase === 'questionnaire'">
            <div class="cfg-section">
              <div class="cfg-section-title">商品信息</div>

              <div class="form-row">
                <label class="form-label">商品名称 <span class="req">*</span></label>
                <input class="form-control" v-model="productName" placeholder="例：小米 SU7 Ultra" />
              </div>

              <div class="form-row">
                <label class="form-label">背景信息</label>
                <textarea class="form-control form-textarea" v-model="background"
                          placeholder="价格定位、主要功能、目标人群等" rows="3" />
              </div>

              <div class="form-row">
                <label class="form-label">访谈目标</label>
                <select class="form-control" v-model="goal">
                  <option value="购买意愿">购买意愿</option>
                  <option value="使用体验">使用体验</option>
                  <option value="改进建议">改进建议</option>
                  <option value="竞品对比">竞品对比</option>
                </select>
              </div>

              <div class="form-row">
                <label class="form-label">问题数量</label>
                <select class="form-control" v-model.number="numQuestions">
                  <option :value="10">10 题</option>
                  <option :value="15">15 题</option>
                  <option :value="20">20 题</option>
                </select>
              </div>

              <button class="btn-generate" :disabled="!productName.trim() || generating" @click="doGenerateQuestionnaire">
                <span v-if="generating" class="spinner-sm"></span>
                <span v-else>✨</span>
                {{ generating ? '生成中…' : '生成问卷' }}
              </button>
            </div>

            <!-- Agent list preview -->
            <div class="cfg-section" v-if="selectedAgents.length">
              <div class="cfg-section-title">
                待访谈 Agent
                <span class="count-pill">{{ selectedAgents.length }}</span>
              </div>
              <div class="agent-mini-list">
                <div v-for="a in selectedAgents" :key="a.id" class="agent-mini-row">
                  <div class="agent-mini-avatar">{{ (a.name || '?')[0] }}</div>
                  <div class="agent-mini-info">
                    <div class="agent-mini-name">{{ a.name }}</div>
                    <div class="agent-mini-occ">{{ a.occupation || '—' }}</div>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- Phase B/C: Agent list -->
          <template v-else>
            <div class="cfg-section">
              <div class="cfg-section-title">
                Agent 列表
                <span class="count-pill">{{ doneCount }}/{{ selectedAgents.length }}</span>
              </div>
              <div class="agent-interview-list">
                <div
                  v-for="a in selectedAgents" :key="a.id"
                  class="agent-iv-row"
                  :class="{ selected: selectedAgentId === a.id }"
                  @click="selectAgent(a.id)"
                >
                  <div class="agent-iv-avatar" :style="{ background: avatarColor(a.id) }">
                    {{ (a.name || '?')[0] }}
                  </div>
                  <div class="agent-iv-info">
                    <div class="agent-iv-name">{{ a.name }}</div>
                    <div class="agent-iv-occ">{{ a.occupation || '—' }}</div>
                  </div>
                  <div class="agent-iv-status" :class="agentStatus(a.id)">
                    <span v-if="agentStatus(a.id) === 'pending'">待</span>
                    <span v-else-if="agentStatus(a.id) === 'running'" class="spinner-xs"></span>
                    <span v-else-if="agentStatus(a.id) === 'done'">✓</span>
                    <span v-else>!</span>
                  </div>
                </div>
              </div>
            </div>
          </template>

        </div><!-- sidebar-scroll -->

        <!-- Sticky footer -->
        <div class="sidebar-footer">
          <template v-if="phase === 'questionnaire'">
            <!-- nothing extra -->
          </template>
          <template v-else>
            <div class="progress-summary">
              已完成 <strong>{{ doneCount }}</strong> / {{ selectedAgents.length }} 人
            </div>
            <button v-if="doneCount >= 1" class="btn-summary" @click="goToSummary">
              查看汇总 →
            </button>
          </template>
        </div>
      </aside>

      <!-- ══════════════════════════════════════════
           MAIN AREA
      ══════════════════════════════════════════ -->
      <main class="main-area">

        <!-- ── Phase A: Questionnaire Design ── -->
        <template v-if="phase === 'questionnaire'">
          <div v-if="!questions.length" class="empty-state">
            <div class="empty-icon">📋</div>
            <div class="empty-title">设计访谈问卷</div>
            <div class="empty-sub">填写商品信息，点击"生成问卷"自动生成 5 阶段访谈题目</div>
          </div>

          <div v-else class="questionnaire-editor">
            <div class="qe-header">
              <div class="qe-title">问卷草稿 · {{ questions.length }} 题</div>
              <div class="qe-hint">点击题目文字可直接编辑，可调整顺序与类型</div>
            </div>

            <div class="qe-table">
              <div class="qe-thead">
                <span class="col-idx">#</span>
                <span class="col-stage">阶段</span>
                <span class="col-question">题目</span>
                <span class="col-type">类型</span>
                <span class="col-action"></span>
              </div>
              <div v-for="(q, idx) in questions" :key="q.id" class="qe-row">
                <span class="col-idx">{{ idx + 1 }}</span>
                <span class="col-stage">
                  <span class="stage-badge" :class="`stage-${q.stage}`">{{ q.stage }}</span>
                </span>
                <span class="col-question">
                  <input class="q-text-input" v-model="q.question" />
                </span>
                <span class="col-type">
                  <select class="q-type-select" v-model="q.type">
                    <option value="text_input">开放</option>
                    <option value="single_choice">单选</option>
                    <option value="Likert">Likert</option>
                  </select>
                </span>
                <span class="col-action">
                  <button class="btn-del-q" @click="questions.splice(idx, 1)">×</button>
                </span>
              </div>
            </div>

            <div class="qe-add-row">
              <button class="btn-add-q" @click="addQuestion">+ 添加问题</button>
            </div>

            <div class="qe-footer">
              <button class="btn-regen" @click="doGenerateQuestionnaire">← 重新生成</button>
              <button class="btn-confirm" :disabled="!questions.length" @click="confirmQuestionnaire">
                确认问卷，开始访谈 →
              </button>
            </div>
          </div>
        </template>

        <!-- ── Phase B: Interview Chat ── -->
        <template v-else-if="phase === 'interview'">
          <div v-if="!selectedAgentId" class="empty-state">
            <div class="empty-icon">👤</div>
            <div class="empty-title">选择一个 Agent 开始访谈</div>
            <div class="empty-sub">点击左侧列表中的任意 Agent</div>
          </div>

          <div v-else class="chat-area">
            <!-- chat header -->
            <div class="chat-header">
              <div class="chat-agent-info">
                <div class="chat-avatar" :style="{ background: avatarColor(selectedAgentId) }">
                  {{ (currentAgentName)[0] }}
                </div>
                <div>
                  <div class="chat-agent-name">{{ currentAgentName }}</div>
                  <div class="chat-stage-info" v-if="currentMessages.length">
                    {{ currentStageLabel }} · Q{{ answeredCount }}/{{ questions.length }}
                  </div>
                </div>
              </div>
              <div class="chat-status-badge" :class="agentStatus(selectedAgentId)">
                {{ statusLabel(agentStatus(selectedAgentId)) }}
              </div>
            </div>

            <!-- messages -->
            <div class="chat-messages" ref="chatEl">
              <template v-for="(msg, i) in currentMessages" :key="i">
                <!-- interviewer question -->
                <div v-if="msg.type === 'question'" class="msg-interviewer"
                     :class="{ 'msg-followup': msg.isFollowup }">
                  <span class="msg-icon">🎙</span>
                  <div class="msg-bubble interviewer-bubble">
                    <span v-if="msg.isFollowup" class="followup-prefix">↪ </span>{{ msg.text }}
                  </div>
                </div>
                <!-- agent answer -->
                <div v-else-if="msg.type === 'answer'" class="msg-agent">
                  <div class="msg-bubble agent-bubble">{{ msg.text }}</div>
                  <div class="agent-msg-avatar" :style="{ background: avatarColor(selectedAgentId) }">
                    {{ (currentAgentName)[0] }}
                  </div>
                </div>
              </template>

              <!-- generating indicator -->
              <div v-if="agentStatus(selectedAgentId) === 'running'" class="msg-generating">
                <span class="spinner-sm"></span> 生成中…
              </div>

              <!-- report card after done -->
              <div v-if="agentStatus(selectedAgentId) === 'done' && currentReport" class="report-card">
                <div class="report-card-header">
                  <span class="report-done-icon">✓</span> 访谈完成
                </div>
                <div class="report-attitude">
                  <span class="attitude-label" :class="`attitude-${currentReport.attitude_label}`">
                    {{ currentReport.attitude_label }}
                  </span>
                  <span class="attitude-score">{{ currentReport.attitude_score }} / 5</span>
                </div>
                <div class="report-opinions" v-if="currentReport.key_opinions?.length">
                  <div class="report-opinions-title">关键意见</div>
                  <ul>
                    <li v-for="(op, i) in currentReport.key_opinions" :key="i">{{ op }}</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- ── Phase C: Summary ── -->
        <template v-else-if="phase === 'summary'">
          <div class="summary-area">
            <div class="summary-header">
              <div class="summary-title">访谈汇总 · {{ productName }}</div>
              <button class="btn-back-interview" @click="phase = 'interview'">← 返回访谈</button>
            </div>

            <!-- stats row -->
            <div class="stats-row" v-if="summaryData">
              <div class="stat-card">
                <div class="stat-val">{{ summaryData.total_completed }}</div>
                <div class="stat-lbl">已完成访谈</div>
              </div>
              <div class="stat-card">
                <div class="stat-val">{{ summaryData.avg_attitude_score }}</div>
                <div class="stat-lbl">平均态度分 (1~5)</div>
              </div>
              <div class="stat-card">
                <div class="stat-val" style="color:#10b981">{{ summaryData.attitude_distribution?.正面 || 0 }}</div>
                <div class="stat-lbl">正面</div>
              </div>
              <div class="stat-card">
                <div class="stat-val" style="color:#f97316">{{ summaryData.attitude_distribution?.中立 || 0 }}</div>
                <div class="stat-lbl">中立</div>
              </div>
              <div class="stat-card">
                <div class="stat-val" style="color:#ef4444">{{ summaryData.attitude_distribution?.负面 || 0 }}</div>
                <div class="stat-lbl">负面</div>
              </div>
            </div>

            <!-- attitude distribution bar -->
            <div class="dist-bar-wrap" v-if="summaryData">
              <div class="dist-bar">
                <div class="dist-seg pos"
                     :style="{ flex: summaryData.attitude_distribution?.正面 || 0 }"
                     v-if="summaryData.attitude_distribution?.正面">
                  正面 {{ summaryData.attitude_distribution.正面 }}
                </div>
                <div class="dist-seg neu"
                     :style="{ flex: summaryData.attitude_distribution?.中立 || 0 }"
                     v-if="summaryData.attitude_distribution?.中立">
                  中立 {{ summaryData.attitude_distribution.中立 }}
                </div>
                <div class="dist-seg neg"
                     :style="{ flex: summaryData.attitude_distribution?.负面 || 0 }"
                     v-if="summaryData.attitude_distribution?.负面">
                  负面 {{ summaryData.attitude_distribution.负面 }}
                </div>
              </div>
            </div>

            <!-- per-agent table -->
            <div class="agent-table-wrap" v-if="summaryData?.agents?.length">
              <div class="section-title">各 Agent 态度</div>
              <table class="agent-table">
                <thead>
                  <tr>
                    <th>姓名</th><th>态度</th><th>分数</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="a in summaryData.agents" :key="a.agent_id">
                    <td>{{ a.name }}</td>
                    <td>
                      <span class="attitude-label" :class="`attitude-${a.attitude_label}`">
                        {{ a.attitude_label }}
                      </span>
                    </td>
                    <td>{{ a.attitude_score }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- key opinions -->
            <div class="opinions-wrap" v-if="summaryData?.key_opinions_sample?.length">
              <div class="section-title">关键意见样本</div>
              <ul class="opinions-list">
                <li v-for="(op, i) in summaryData.key_opinions_sample" :key="i">{{ op }}</li>
              </ul>
            </div>

            <!-- AI analysis -->
            <div class="analyze-section">
              <button class="btn-analyze" :disabled="analyzing" @click="doAnalyze">
                <span v-if="analyzing" class="spinner-sm"></span>
                <span v-else>🤖</span>
                {{ analyzing ? 'AI 分析中…' : 'AI 深度解读' }}
              </button>

              <div v-if="analysisResult" class="analysis-result">
                <div class="analysis-block">
                  <div class="analysis-title">整体概述</div>
                  <p>{{ analysisResult.overview }}</p>
                </div>
                <div class="analysis-block" v-if="analysisResult.consensus?.length">
                  <div class="analysis-title">主要共识</div>
                  <ul><li v-for="(c, i) in analysisResult.consensus" :key="i">{{ c }}</li></ul>
                </div>
                <div class="analysis-block" v-if="analysisResult.divergence?.length">
                  <div class="analysis-title">主要分歧</div>
                  <ul><li v-for="(d, i) in analysisResult.divergence" :key="i">{{ d }}</li></ul>
                </div>
                <div class="analysis-block" v-if="analysisResult.insights?.length">
                  <div class="analysis-title">关键洞察与建议</div>
                  <ul><li v-for="(ins, i) in analysisResult.insights" :key="i">{{ ins }}</li></ul>
                </div>
              </div>
            </div>

          </div>
        </template>

      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import NavBar from '../components/NavBar.vue'
import {
  generateQuestionnaire,
  createInterviewSession,
  getInterviewSummary,
  analyzeInterview,
  interviewStreamUrl,
} from '../api/index.js'

const router = useRouter()

// ── 从 localStorage 读取上下文 ─────────────────────────────
const selectedAgents = ref([])
const urbanSimId     = ref(null)
const onlineSimId    = ref(null)

onMounted(() => {
  // agents and urbanSimId come from simResult saved by SimulationView
  try {
    const raw = localStorage.getItem('simResult')
    if (raw) {
      const simResult = JSON.parse(raw)
      if (simResult.agents?.length) {
        selectedAgents.value = simResult.agents
      }
      if (simResult.sim_id) {
        urbanSimId.value = simResult.sim_id
      }
    }
  } catch {}
  // onlineSimId saved by OnlineSimView when clicking Next Step
  onlineSimId.value = localStorage.getItem('latestOnlineSimId') || null
})

// ── Phase A state ──────────────────────────────────────────
const phase        = ref('questionnaire')
const productName  = ref('')
const background   = ref('')
const goal         = ref('购买意愿')
const numQuestions = ref(15)
const questions    = ref([])
const generating   = ref(false)

// ── Phase B state ──────────────────────────────────────────
const sessionId       = ref(null)
const selectedAgentId = ref(null)
const chatEl          = ref(null)

// agentData: { [agent_id]: { status, messages, report } }
const agentData = ref({})

// ── Phase C state ──────────────────────────────────────────
const summaryData    = ref(null)
const analyzing      = ref(false)
const analysisResult = ref(null)

// ── Computed ───────────────────────────────────────────────
const doneCount = computed(() =>
  Object.values(agentData.value).filter(d => d.status === 'done').length
)

const currentAgentName = computed(() => {
  if (!selectedAgentId.value) return ''
  const a = selectedAgents.value.find(a => a.id === selectedAgentId.value)
  return a?.name || String(selectedAgentId.value)
})

const currentMessages = computed(() =>
  agentData.value[selectedAgentId.value]?.messages || []
)

const currentReport = computed(() =>
  agentData.value[selectedAgentId.value]?.report || null
)

const answeredCount = computed(() => {
  const msgs = currentMessages.value
  return msgs.filter(m => m.type === 'answer' && !m.isFollowup).length
})

const currentStageLabel = computed(() => {
  const msgs = currentMessages.value
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].stage) return stageLabel(msgs[i].stage)
  }
  return ''
})

// ── Helpers ────────────────────────────────────────────────
const STAGE_LABELS = { basic:'背景', core:'体验', attitude:'态度', reflection:'反思', closing:'收尾' }
const COLORS = ['#7c3aed','#2563eb','#0891b2','#059669','#d97706','#dc2626','#7c3aed','#4f46e5']

function stageLabel(s) { return STAGE_LABELS[s] || s }
function agentStatus(id) { return agentData.value[id]?.status || 'pending' }
function avatarColor(id) { return COLORS[(id - 1) % COLORS.length] }
function statusLabel(s) {
  return { pending:'待访谈', running:'访谈中…', done:'已完成', error:'出错' }[s] || s
}

// ── Phase A: Generate & Confirm ────────────────────────────
async function doGenerateQuestionnaire() {
  if (!productName.value.trim()) return
  generating.value = true
  try {
    const res = await generateQuestionnaire({
      product_name:  productName.value,
      background:    background.value,
      goal:          goal.value,
      num_questions: numQuestions.value,
    })
    questions.value = res.questions || []
  } catch (e) {
    alert('问卷生成失败：' + (e.response?.data?.error || e.message))
  } finally {
    generating.value = false
  }
}

function addQuestion() {
  const id = (questions.value[questions.value.length - 1]?.id || 0) + 1
  questions.value.push({ id, stage: 'core', question: '', type: 'text_input' })
}

async function confirmQuestionnaire() {
  if (!questions.value.length) return

  // 创建访谈会话
  try {
    const agentIds = selectedAgents.value.map(a => ({
      id:         a.id,
      name:       a.name,
      occupation: a.occupation || '',
      mbti:       a.mbti || '',
      gender:     a.gender || '',
      interests:  a.interests || [],
    }))
    const res = await createInterviewSession({
      questions:     questions.value,
      product_name:  productName.value,
      agent_ids:     agentIds,
      sim_id_urban:  urbanSimId.value,
      sim_id_online: onlineSimId.value,
    })
    sessionId.value = res.session_id

    // 初始化每个 agent 的状态
    const data = {}
    for (const a of selectedAgents.value) {
      data[a.id] = { status: 'pending', messages: [], report: null }
    }
    agentData.value = data
    phase.value = 'interview'
  } catch (e) {
    alert('创建访谈会话失败：' + (e.response?.data?.error || e.message))
  }
}

// ── Phase B: Per-agent Interview ───────────────────────────
function selectAgent(agentId) {
  selectedAgentId.value = agentId
  const ad = agentData.value[agentId]
  if (!ad) return

  // 若已完成或正在进行，无需重新连接
  if (ad.status === 'done' || ad.status === 'running') return

  // 开始新的 SSE 访谈
  startAgentInterview(agentId)
}

function startAgentInterview(agentId) {
  if (!sessionId.value) return
  const ad = agentData.value[agentId]
  if (!ad) return

  ad.status   = 'running'
  ad.messages = []

  const url = interviewStreamUrl(sessionId.value, agentId)
  const es  = new EventSource(url)

  es.onmessage = async (e) => {
    let ev
    try { ev = JSON.parse(e.data) } catch { return }

    if (ev.type === 'qa') {
      ad.messages.push({ type: 'question', text: ev.question, stage: ev.stage, isFollowup: false })
      ad.messages.push({ type: 'answer',   text: ev.answer,   stage: ev.stage, isFollowup: false })
    } else if (ev.type === 'followup') {
      ad.messages.push({ type: 'question', text: ev.question, isFollowup: true })
      ad.messages.push({ type: 'answer',   text: ev.answer,   isFollowup: true })
    } else if (ev.type === 'done') {
      ad.status = 'done'
      ad.report = ev.report
      es.close()
    } else if (ev.type === 'error') {
      ad.status = 'error'
      es.close()
    }

    // 滚动到底部
    await nextTick()
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  }

  es.onerror = () => {
    if (ad.status !== 'done') ad.status = 'error'
    es.close()
  }
}

// ── Phase C: Summary ───────────────────────────────────────
async function goToSummary() {
  if (!sessionId.value) return
  try {
    summaryData.value = await getInterviewSummary(sessionId.value)
    phase.value = 'summary'
  } catch (e) {
    alert('获取汇总失败：' + (e.response?.data?.error || e.message))
  }
}

async function doAnalyze() {
  if (!sessionId.value) return
  analyzing.value = true
  try {
    analysisResult.value = await analyzeInterview(sessionId.value)
  } catch (e) {
    alert('AI 解读失败：' + (e.response?.data?.error || e.message))
  } finally {
    analyzing.value = false
  }
}
</script>

<style scoped>
/* ── Layout ── */
.interview-layout { display: flex; flex-direction: column; height: 100vh; background: var(--bg); }
.workspace { display: flex; flex: 1; overflow: hidden; }

/* ── NavBar extras ── */
.nav-back-btn {
  background: none; border: none; color: var(--text-muted); cursor: pointer;
  font-size: 13px; padding: 4px 10px; border-radius: 6px; font-family: inherit;
}
.nav-back-btn:hover { background: var(--surface); color: var(--text); }
.step-indicator { display: flex; align-items: center; gap: 4px; }
.step-pip {
  width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 11px; font-weight: 700;
  background: var(--surface); color: var(--text-muted); border: 1.5px solid var(--border);
}
.step-pip.done   { background: var(--purple); color: #fff; border-color: var(--purple); }
.step-pip.active { background: var(--grad); color: #fff; border-color: transparent; }
.step-line { width: 18px; height: 2px; background: var(--border); border-radius: 1px; }
.step-line.done { background: var(--purple); }
.step-label { font-size: 11px; font-weight: 600; color: var(--purple); margin-left: 6px; }

/* ── Sidebar ── */
.sidebar {
  width: 270px; min-width: 270px; background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
.sidebar-scroll { flex: 1; overflow-y: auto; padding: 16px 12px; }
.sidebar-footer  { padding: 12px; border-top: 1px solid var(--border); }

/* ── Cfg sections ── */
.cfg-section { margin-bottom: 20px; }
.cfg-section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .06em; color: var(--text-muted); margin-bottom: 10px;
  display: flex; align-items: center; gap: 6px;
}
.count-pill {
  background: rgba(124,58,237,.12); color: var(--purple);
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 10px;
}
.form-row { margin-bottom: 10px; }
.form-label { display: block; font-size: 11px; color: var(--text-muted); margin-bottom: 4px; }
.req { color: #ef4444; }
.form-control {
  width: 100%; box-sizing: border-box;
  background: var(--bg); border: 1px solid var(--border); border-radius: 7px;
  color: var(--text); font-size: 13px; padding: 7px 10px; font-family: inherit;
}
.form-control:focus { outline: none; border-color: var(--purple); }
.form-textarea { resize: vertical; min-height: 60px; }

.btn-generate {
  width: 100%; padding: 10px; border: none; border-radius: 9px;
  background: var(--grad); color: #fff; font-size: 13px; font-weight: 600;
  cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;
  font-family: inherit; transition: opacity .2s;
}
.btn-generate:disabled { opacity: .45; cursor: not-allowed; }
.btn-generate:hover:not(:disabled) { opacity: .9; }

/* ── Agent mini list (Phase A preview) ── */
.agent-mini-list { display: flex; flex-direction: column; gap: 6px; }
.agent-mini-row  { display: flex; align-items: center; gap: 8px; }
.agent-mini-avatar {
  width: 28px; height: 28px; border-radius: 50%; background: var(--purple);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; color: #fff; flex-shrink: 0;
}
.agent-mini-name { font-size: 13px; font-weight: 600; color: var(--text); }
.agent-mini-occ  { font-size: 11px; color: var(--text-muted); }

/* ── Agent interview list (Phase B/C) ── */
.agent-interview-list { display: flex; flex-direction: column; gap: 4px; }
.agent-iv-row {
  display: flex; align-items: center; gap: 9px; padding: 8px 10px;
  border-radius: 9px; cursor: pointer; transition: background .15s;
}
.agent-iv-row:hover   { background: rgba(124,58,237,.07); }
.agent-iv-row.selected { background: rgba(124,58,237,.12); }
.agent-iv-avatar {
  width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700; color: #fff;
}
.agent-iv-info { flex: 1; min-width: 0; }
.agent-iv-name { font-size: 13px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.agent-iv-occ  { font-size: 11px; color: var(--text-muted); }
.agent-iv-status {
  width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}
.agent-iv-status.pending { background: var(--surface); color: var(--text-muted); border: 1px solid var(--border); }
.agent-iv-status.running { background: rgba(59,130,246,.15); color: #3b82f6; }
.agent-iv-status.done    { background: rgba(16,185,129,.15); color: #10b981; }
.agent-iv-status.error   { background: rgba(239,68,68,.15); color: #ef4444; }

/* ── Sidebar footer ── */
.progress-summary { font-size: 12px; color: var(--text-muted); text-align: center; margin-bottom: 8px; }
.progress-summary strong { color: var(--text); }
.btn-summary {
  width: 100%; padding: 10px; border-radius: 9px; cursor: pointer; font-family: inherit;
  background: transparent; color: var(--purple); font-weight: 600; font-size: 13px;
  border: 1.5px solid var(--purple); transition: background .15s;
}
.btn-summary:hover { background: rgba(124,58,237,.08); }

/* ── Main area ── */
.main-area { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }

/* ── Empty state ── */
.empty-state {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; color: var(--text-muted); padding: 40px;
}
.empty-icon  { font-size: 48px; }
.empty-title { font-size: 18px; font-weight: 700; color: var(--text); }
.empty-sub   { font-size: 13px; text-align: center; max-width: 320px; line-height: 1.6; }

/* ── Questionnaire editor ── */
.questionnaire-editor { padding: 24px; display: flex; flex-direction: column; gap: 16px; }
.qe-header { display: flex; align-items: baseline; gap: 12px; }
.qe-title   { font-size: 16px; font-weight: 700; color: var(--text); }
.qe-hint    { font-size: 12px; color: var(--text-muted); }

.qe-table {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; overflow: hidden;
}
.qe-thead {
  display: grid; grid-template-columns: 36px 80px 1fr 90px 36px;
  padding: 8px 12px; border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .05em; color: var(--text-muted);
}
.qe-row {
  display: grid; grid-template-columns: 36px 80px 1fr 90px 36px;
  align-items: center; padding: 7px 12px;
  border-bottom: 1px solid var(--border);
}
.qe-row:last-child { border-bottom: none; }
.col-idx   { font-size: 12px; color: var(--text-muted); }
.col-stage { }
.col-question { padding: 0 8px; }
.col-type  { }
.col-action { text-align: right; }

/* Stage badges */
.stage-badge {
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 8px; white-space: nowrap;
}
.stage-basic      { background: rgba(59,130,246,.15);  color: #3b82f6; }
.stage-core       { background: rgba(124,58,237,.15);  color: #7c3aed; }
.stage-attitude   { background: rgba(249,115,22,.15);  color: #f97316; }
.stage-reflection { background: rgba(16,185,129,.15);  color: #10b981; }
.stage-closing    { background: rgba(107,114,128,.15); color: #6b7280; }

.q-text-input {
  width: 100%; background: transparent; border: none; border-bottom: 1px solid transparent;
  color: var(--text); font-size: 13px; font-family: inherit; padding: 2px 0;
}
.q-text-input:focus { outline: none; border-bottom-color: var(--purple); }
.q-type-select {
  background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); font-size: 12px; padding: 3px 6px; font-family: inherit;
}
.btn-del-q {
  width: 22px; height: 22px; border-radius: 50%; background: none;
  border: 1px solid var(--border); color: var(--text-muted); cursor: pointer; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
.btn-del-q:hover { background: rgba(239,68,68,.12); border-color: #ef4444; color: #ef4444; }

.qe-add-row { padding: 0 4px; }
.btn-add-q {
  background: none; border: 1px dashed var(--border); border-radius: 8px;
  color: var(--text-muted); font-size: 12px; padding: 8px 16px; cursor: pointer;
  font-family: inherit; transition: border-color .15s, color .15s;
}
.btn-add-q:hover { border-color: var(--purple); color: var(--purple); }

.qe-footer { display: flex; gap: 10px; justify-content: space-between; }
.btn-regen {
  background: none; border: 1px solid var(--border); border-radius: 9px;
  color: var(--text-muted); font-size: 13px; padding: 10px 18px; cursor: pointer;
  font-family: inherit; transition: border-color .15s;
}
.btn-regen:hover { border-color: var(--purple); color: var(--purple); }
.btn-confirm {
  flex: 1; background: var(--grad); color: #fff; border: none; border-radius: 9px;
  font-size: 13px; font-weight: 600; padding: 10px 18px; cursor: pointer;
  font-family: inherit; transition: opacity .2s;
}
.btn-confirm:disabled { opacity: .4; cursor: not-allowed; }
.btn-confirm:hover:not(:disabled) { opacity: .9; }

/* ── Chat area ── */
.chat-area { display: flex; flex-direction: column; flex: 1; overflow: hidden; }
.chat-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--border);
  background: var(--surface); flex-shrink: 0;
}
.chat-agent-info  { display: flex; align-items: center; gap: 10px; }
.chat-avatar {
  width: 36px; height: 36px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 15px; font-weight: 700; color: #fff;
}
.chat-agent-name  { font-size: 14px; font-weight: 700; color: var(--text); }
.chat-stage-info  { font-size: 11px; color: var(--text-muted); }
.chat-status-badge {
  font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 10px;
}
.chat-status-badge.pending { background: var(--surface); color: var(--text-muted); border: 1px solid var(--border); }
.chat-status-badge.running { background: rgba(59,130,246,.12); color: #3b82f6; }
.chat-status-badge.done    { background: rgba(16,185,129,.12); color: #10b981; }
.chat-status-badge.error   { background: rgba(239,68,68,.12); color: #ef4444; }

.chat-messages {
  flex: 1; overflow-y: auto; padding: 20px;
  display: flex; flex-direction: column; gap: 14px;
}

/* Interviewer bubble (left) */
.msg-interviewer { display: flex; align-items: flex-start; gap: 8px; }
.msg-interviewer.msg-followup { padding-left: 24px; }
.msg-icon { font-size: 16px; flex-shrink: 0; margin-top: 2px; }
.interviewer-bubble {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px 12px 12px 2px; padding: 10px 14px;
  font-size: 13px; color: var(--text); max-width: 70%; line-height: 1.5;
}
.msg-followup .interviewer-bubble { font-size: 12px; color: var(--text-muted); }
.followup-prefix { color: var(--purple); font-weight: 700; }

/* Agent bubble (right) */
.msg-agent {
  display: flex; align-items: flex-start; gap: 8px; justify-content: flex-end;
}
.agent-bubble {
  background: var(--purple); color: #fff;
  border-radius: 12px 12px 2px 12px; padding: 10px 14px;
  font-size: 13px; max-width: 70%; line-height: 1.5;
}
.agent-msg-avatar {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; color: #fff; margin-top: 2px;
}

/* Generating */
.msg-generating {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--text-muted); padding: 4px 0;
}

/* Report card */
.report-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px; margin-top: 8px;
}
.report-card-header {
  font-size: 13px; font-weight: 700; color: var(--text);
  margin-bottom: 12px; display: flex; align-items: center; gap: 6px;
}
.report-done-icon { color: #10b981; font-size: 15px; }
.report-attitude { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.report-opinions-title { font-size: 11px; font-weight: 700; color: var(--text-muted); margin-bottom: 6px; }
.report-opinions ul { margin: 0; padding-left: 16px; }
.report-opinions li { font-size: 12px; color: var(--text); line-height: 1.6; }

/* Attitude labels */
.attitude-label {
  font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 8px;
}
.attitude-正面 { background: rgba(16,185,129,.15);  color: #10b981; }
.attitude-中立 { background: rgba(249,115,22,.15);  color: #f97316; }
.attitude-负面 { background: rgba(239,68,68,.15); color: #ef4444; }
.attitude-score { font-size: 18px; font-weight: 700; color: var(--text); }

/* ── Summary area ── */
.summary-area { padding: 24px; display: flex; flex-direction: column; gap: 20px; }
.summary-header { display: flex; align-items: center; justify-content: space-between; }
.summary-title  { font-size: 18px; font-weight: 700; color: var(--text); }
.btn-back-interview {
  background: none; border: 1px solid var(--border); border-radius: 8px;
  color: var(--text-muted); font-size: 12px; padding: 6px 12px; cursor: pointer;
  font-family: inherit;
}
.btn-back-interview:hover { border-color: var(--purple); color: var(--purple); }

.stats-row { display: flex; gap: 12px; flex-wrap: wrap; }
.stat-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 18px; min-width: 90px; text-align: center;
}
.stat-val { font-size: 24px; font-weight: 700; color: var(--text); }
.stat-lbl { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

.dist-bar-wrap { }
.dist-bar { display: flex; border-radius: 8px; overflow: hidden; height: 32px; }
.dist-seg {
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; color: #fff; min-width: 40px;
}
.dist-seg.pos { background: #10b981; }
.dist-seg.neu { background: #f97316; }
.dist-seg.neg { background: #ef4444; }

.section-title { font-size: 12px; font-weight: 700; color: var(--text-muted); margin-bottom: 10px; text-transform: uppercase; letter-spacing: .05em; }

.agent-table-wrap { }
.agent-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.agent-table th {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;
}
.agent-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--text); }
.agent-table tr:last-child td { border-bottom: none; }

.opinions-list { margin: 0; padding-left: 16px; }
.opinions-list li { font-size: 13px; color: var(--text); line-height: 1.7; }

/* AI analysis */
.analyze-section { }
.btn-analyze {
  display: flex; align-items: center; gap: 8px; padding: 11px 20px;
  background: var(--grad); color: #fff; border: none; border-radius: 10px;
  font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit;
  transition: opacity .2s;
}
.btn-analyze:disabled { opacity: .45; cursor: not-allowed; }
.btn-analyze:hover:not(:disabled) { opacity: .9; }

.analysis-result {
  margin-top: 16px; background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px; display: flex; flex-direction: column; gap: 14px;
}
.analysis-block { }
.analysis-title { font-size: 12px; font-weight: 700; color: var(--purple); margin-bottom: 6px; }
.analysis-block p { font-size: 13px; color: var(--text); margin: 0; line-height: 1.6; }
.analysis-block ul { margin: 0; padding-left: 16px; }
.analysis-block li { font-size: 13px; color: var(--text); line-height: 1.7; }

/* ── Spinners ── */
.spinner-sm {
  display: inline-block; width: 14px; height: 14px; border-radius: 50%;
  border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
  animation: spin .7s linear infinite; flex-shrink: 0;
}
.spinner-xs {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  border: 2px solid rgba(59,130,246,.3); border-top-color: #3b82f6;
  animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
