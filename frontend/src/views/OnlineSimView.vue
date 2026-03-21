<template>
  <div class="online-layout">

    <!-- NavBar — Step 3 active -->
    <NavBar>
      <template #right>
        <button class="nav-back-btn" @click="router.push('/simulation')">← Back</button>
        <div class="step-indicator">
          <div class="step-pip done">1</div>
          <div class="step-line done"></div>
          <div class="step-pip done">2</div>
          <div class="step-line done"></div>
          <div class="step-pip active">3</div>
          <span class="step-label">Online Sim</span>
        </div>
      </template>
    </NavBar>

    <div class="workspace">

      <!-- ── Left Sidebar ──────────────────────────────────── -->
      <aside class="sidebar">
        <div class="sidebar-scroll">

          <!-- A: Agents -->
          <div class="cfg-section">
            <div class="cfg-section-title">
              Agents
              <span v-if="oasisAgents.length" class="count-pill">{{ oasisAgents.length }}</span>
            </div>
            <div v-if="!simResult" class="empty-hint">
              No result found — complete Step 2 first.
            </div>
            <div v-else class="agent-list">
              <div
                v-for="a in oasisAgents" :key="a.agent_id"
                class="agent-card"
                :class="{ expanded: expandedAgent === a.agent_id }"
                @click="expandedAgent = expandedAgent === a.agent_id ? null : a.agent_id"
              >
                <div class="agent-card-row">
                  <div class="agent-avatar" :style="`background:${wellnessColor(a._needs)}`">
                    {{ a.name[0] }}
                  </div>
                  <div class="agent-card-info">
                    <div class="agent-name">{{ a.name }}</div>
                    <div class="agent-occ">{{ a._occupation || '—' }}</div>
                  </div>
                  <div class="group-badge" :style="groupBadgeStyle(a.group)">
                    {{ groupShort(a.group) }}
                  </div>
                </div>
                <div v-if="expandedAgent === a.agent_id" class="agent-detail">
                  <div class="detail-label">Role Instruction</div>
                  <div class="detail-text">{{ a.user_char }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- B: Campaign -->
          <div class="cfg-section">
            <div class="cfg-section-title">Campaign</div>
            <div class="form-row">
              <label class="form-label">Topic / 营销主题</label>
              <input class="form-control" v-model="cfgTopic"
                     placeholder="e.g. TNT演唱会, 新品发布" />
            </div>
            <div class="form-row">
              <div class="slider-header">
                <span class="form-label" style="margin:0">Simulation Steps</span>
                <span class="slider-val">{{ cfgSteps }}</span>
              </div>
              <input type="range" min="1" max="12" step="1" v-model.number="cfgSteps"
                     :style="sliderStyle(cfgSteps, 1, 12)" />
            </div>
            <div class="form-row">
              <div class="slider-header">
                <span class="form-label" style="margin:0">Concurrency</span>
                <span class="slider-val">{{ cfgConcurrency }}</span>
              </div>
              <input type="range" min="1" max="10" step="1" v-model.number="cfgConcurrency"
                     :style="sliderStyle(cfgConcurrency, 1, 10)" />
            </div>
          </div>

          <!-- C: Interventions -->
          <div class="cfg-section">
            <div class="cfg-section-title">
              Interventions
              <button class="btn-tiny" @click="addIntervention">+ Add</button>
            </div>
            <div v-if="!interventions.length" class="empty-hint">
              No interventions — click + Add to design one.
            </div>
            <div v-for="(iv, idx) in interventions" :key="idx" class="iv-card">
              <div class="iv-card-header">
                <span class="iv-idx">#{{ idx + 1 }}</span>
                <select class="form-control iv-type" v-model="iv.type">
                  <option value="broadcast">📢 Broadcast</option>
                  <option value="bribery">💰 Bribery</option>
                  <option value="register_user">🤖 Bot Inject</option>
                </select>
                <div class="iv-step-wrap">
                  <span class="iv-step-label">Step</span>
                  <input class="form-control iv-step" type="number"
                         min="1" :max="cfgSteps" v-model.number="iv.step" />
                </div>
                <button class="btn-del" @click="interventions.splice(idx, 1)">×</button>
              </div>
              <textarea class="form-control iv-body" v-model="iv.content"
                        :placeholder="ivPlaceholder(iv.type)" rows="2" />
              <input v-if="iv.type === 'bribery'" class="form-control"
                     style="margin-top:6px"
                     v-model="iv.target_group"
                     placeholder="Target group (e.g. 活跃KOL)" />
            </div>
          </div>

          <!-- D: Simulation History -->
          <div class="cfg-section history-section">
            <div class="cfg-section-title">
              History
              <span v-if="historyList.length" class="count-pill">{{ historyList.length }}</span>
            </div>
            <div v-if="!historyList.length" class="empty-hint">No past simulations yet.</div>
            <div v-else class="hist-list">
              <div
                v-for="rec in historyList" :key="rec.sim_id"
                class="hist-card"
                :class="{ 'hist-active': rec.sim_id === selectedHistoryId }"
                @click="loadHistorySim(rec.sim_id)"
              >
                <div class="hist-card-row">
                  <span class="hist-topic">{{ rec.topic || '—' }}</span>
                  <span class="hist-status" :class="historyStatusClass(rec.status)">
                    {{ historyStatusLabel(rec.status) }}
                  </span>
                </div>
                <div class="hist-meta">
                  {{ formatHistoryTime(rec.start_time) }}
                  · {{ rec.num_agents }} agents
                  · {{ rec.total_steps }} steps
                </div>
              </div>
            </div>
          </div>

        </div><!-- sidebar-scroll -->

        <!-- Sticky footer -->
        <div class="sidebar-footer">
          <div v-if="logs.length" class="log-box" ref="logBox">
            <div v-for="(l, i) in logs" :key="i" v-html="l"></div>
          </div>
          <button class="btn-start"
                  :disabled="simRunning || !simResult || !cfgTopic.trim()"
                  @click="startSim">
            <template v-if="simRunning">
              <span class="dot-pulse"></span> Running {{ simProgress }}/{{ cfgSteps }}
            </template>
            <template v-else-if="simDone">
              ✓ Completed
            </template>
            <template v-else>
              ▶ Start Simulation
            </template>
          </button>
          <p v-if="!cfgTopic.trim() && simResult" class="hint-text">
            Enter a campaign topic to start
          </p>
        </div>
      </aside>

      <!-- ── Posts Feed Column ──────────────────────────────── -->
      <div class="posts-col">
        <div class="col-header">
          <span class="col-title">Posts</span>
          <span v-if="posts.length" class="count-pill">{{ posts.length }}</span>
          <span v-if="simRunning || simDone" class="step-badge">
            Step {{ simProgress }} / {{ cfgSteps }}
          </span>
        </div>

        <div v-if="!postsReversed.length" class="col-empty">
          <div class="empty-ico">💬</div>
          <div class="empty-ttl">No posts yet</div>
          <div class="empty-dsc">Start the simulation to see agents posting in real time</div>
        </div>

        <div v-else class="posts-feed">
          <div v-for="(p, i) in postsReversed" :key="i"
               class="post-card" :class="attitudeClass(p.attitude_score)">
            <div class="post-header">
              <div class="post-avatar" :style="`background:${groupColor(p.group)}`">
                {{ (p.name || p.username || '?')[0] }}
              </div>
              <div class="post-meta">
                <div class="post-uname">{{ p.name || p.username }}</div>
                <div class="post-sub-row">
                  <span class="group-badge-sm" :style="groupBadgeStyle(p.group)">
                    {{ groupShort(p.group) }}
                  </span>
                  <span class="post-step">Step {{ p.step }}</span>
                </div>
              </div>
              <div v-if="p.attitude_score != null"
                   class="attitude-chip" :class="attitudeClass(p.attitude_score)">
                {{ p.attitude_score > 0 ? '+' : '' }}{{ Number(p.attitude_score).toFixed(2) }}
              </div>
            </div>
            <div v-if="p.quote_content" class="post-quote">
              <span class="post-quote-label">转发自</span>
              {{ truncate(p.quote_content, 100) }}
            </div>
            <div class="post-body">{{ p.content }}</div>
            <div class="post-engage">
              <span class="engage-item"><span class="engage-ico">👍</span>{{ p.num_likes || 0 }}</span>
              <span class="engage-item"><span class="engage-ico">👎</span>{{ p.num_dislikes || 0 }}</span>
              <span class="engage-item"><span class="engage-ico">🔁</span>{{ p.num_shares || 0 }}</span>
              <span class="engage-item"><span class="engage-ico">💬</span>{{ p.comment_count || 0 }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Right Column: Attitude + Stats ────────────────── -->
      <div class="right-col">

        <!-- Attitude Panel -->
        <div class="right-panel attitude-panel">
          <div class="panel-header">
            <span class="col-title">Attitude</span>
            <span v-if="cfgTopic" class="panel-topic">"{{ cfgTopic }}"</span>
            <button
              v-if="simDone && attitudeData"
              class="btn-interpret"
              :disabled="interpreting"
              @click="interpretCurve"
            >
              {{ interpreting ? '解读中…' : '✦ 解读曲线' }}
            </button>
          </div>

          <div v-if="!attitudeData" class="panel-empty">
            <div class="empty-ico sm">📈</div>
            <div class="empty-dsc">Attitude trajectories will appear after simulation runs</div>
          </div>

          <div v-else>
            <svg class="attitude-svg" viewBox="0 0 560 220"
                 preserveAspectRatio="xMidYMid meet">
              <!-- Y grid + labels -->
              <g v-for="tick in Y_TICKS" :key="tick">
                <line
                  :x1="CP.l" :y1="chartY(tick)"
                  :x2="560 - CP.r" :y2="chartY(tick)"
                  :stroke="tick === 0 ? '#7c3aed' : '#e2e8f0'"
                  :stroke-width="tick === 0 ? 1 : 0.5"
                  :stroke-dasharray="tick === 0 ? '4,3' : ''"
                />
                <text :x="CP.l - 8" :y="chartY(tick) + 4"
                      text-anchor="end" font-size="10" fill="#94a3b8">
                  {{ tick }}
                </text>
              </g>
              <!-- X axis labels -->
              <text
                v-for="s in attitudeData.steps" :key="'x'+s"
                :x="chartX(attitudeData.steps.indexOf(s), attitudeData.steps.length)"
                :y="220 - CP.b + 14"
                text-anchor="middle" font-size="10" fill="#94a3b8"
              >{{ s }}</text>
              <!-- Lines + dots per group -->
              <g v-for="(vals, gname) in attitudeData.groups" :key="gname">
                <path :d="buildPath(gname)" fill="none"
                      :stroke="GROUP_COLORS[gname] || '#94a3b8'"
                      stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                <circle
                  v-for="(val, i) in vals" :key="i"
                  :cx="chartX(i, attitudeData.steps.length)"
                  :cy="chartY(val ?? 0)"
                  r="3.5"
                  :fill="GROUP_COLORS[gname] || '#94a3b8'"
                  stroke="#fff" stroke-width="1.5"
                />
              </g>
            </svg>
            <div class="chart-legend">
              <div v-for="(color, gname) in usedGroupColors" :key="gname" class="legend-item">
                <div class="legend-dot" :style="`background:${color}`"></div>
                <span>{{ gname }}</span>
              </div>
            </div>
            <!-- AI 解读卡片 -->
            <div v-if="interpretation" class="interpret-card">
              <div class="interpret-card-title">✦ AI 解读</div>
              <p class="interpret-card-text">{{ interpretation }}</p>
            </div>
          </div>
        </div>

        <!-- Stats Panel -->
        <div class="right-panel stats-panel">
          <div class="panel-header">
            <span class="col-title">Stats</span>
          </div>

          <div v-if="!statsData" class="panel-empty">
            <div class="empty-ico sm">📊</div>
            <div class="empty-dsc">Engagement analytics will appear after simulation runs</div>
          </div>

          <div v-else>
            <!-- Summary Cards -->
            <div class="stat-cards">
              <div class="stat-card">
                <div class="stat-value">{{ statsData.total_posts }}</div>
                <div class="stat-label">Posts</div>
              </div>
              <div class="stat-card">
                <div class="stat-value">{{ statsData.total_likes }}</div>
                <div class="stat-label">Likes</div>
              </div>
              <div class="stat-card">
                <div class="stat-value">{{ statsData.total_shares }}</div>
                <div class="stat-label">Reposts</div>
              </div>
              <div class="stat-card">
                <div class="stat-value">{{ statsData.total_comments }}</div>
                <div class="stat-label">Comments</div>
              </div>
              <div class="stat-card">
                <div class="stat-value">{{ statsData.total_actions }}</div>
                <div class="stat-label">Actions</div>
              </div>
            </div>

            <div class="stats-body">
              <!-- Group Activity -->
              <div class="stats-block">
                <div class="stats-block-title">Activity by Group</div>
                <div v-for="g in statsData.by_group" :key="g.group" class="gbar-row">
                  <div class="gbar-label">
                    <span class="group-dot" :style="`background:${groupColor(g.group)}`"></span>
                    {{ g.group }}
                  </div>
                  <div class="gbar-track">
                    <div class="gbar-fill"
                         :style="`width:${groupBarPct(g.posts)}%; background:${groupColor(g.group)}`">
                    </div>
                  </div>
                  <div class="gbar-nums">{{ g.posts }} posts · {{ g.likes }} 👍</div>
                </div>
              </div>

              <!-- Action Breakdown -->
              <div v-if="Object.keys(statsData.action_types).length" class="stats-block">
                <div class="stats-block-title">Action Breakdown</div>
                <div v-for="(cnt, act) in statsData.action_types" :key="act" class="action-row">
                  <span class="action-name">{{ formatAction(act) }}</span>
                  <div class="action-bar-track">
                    <div class="action-bar-fill" :style="`width:${actionBarPct(cnt)}%`"></div>
                  </div>
                  <span class="action-cnt">{{ cnt }}</span>
                </div>
              </div>
            </div>

            <!-- Top Posts -->
            <div v-if="statsData.top_posts.length" class="top-posts-block">
              <div class="stats-block-title">Top Engaged Posts</div>
              <div v-for="(p, i) in statsData.top_posts" :key="p.post_id" class="top-post-row">
                <div class="top-rank">#{{ i + 1 }}</div>
                <div class="top-post-body">
                  <div class="top-post-header">
                    <span class="top-post-name">{{ p.name }}</span>
                    <span class="group-badge-sm" :style="groupBadgeStyle(p.group)">
                      {{ groupShort(p.group) }}
                    </span>
                  </div>
                  <div class="top-post-content">{{ truncate(p.content, 120) }}</div>
                  <div class="top-post-engage">
                    <span>👍 {{ p.num_likes }}</span>
                    <span>👎 {{ p.num_dislikes }}</span>
                    <span>🔁 {{ p.num_shares }}</span>
                    <span>💬 {{ p.comment_count }}</span>
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>

      </div><!-- right-col -->
    </div><!-- workspace -->
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import NavBar from '../components/NavBar.vue'
import { getProfiles, getRelationships, startOnlineSim, getOnlineSimPosts, getOnlineSimAttitude, getOnlineSimStats, interpretAttitude, getOnlineSimHistory } from '../api/index.js'

// ── Constants ────────────────────────────────────────────────

const GROUP_COLORS = {
  '权威媒体/大V': '#3b82f6',
  '活跃KOL':     '#f97316',
  '活跃创作者':  '#22c55e',
  '普通用户':    '#64748b',
  '潜水用户':    '#94a3b8',
}

// Chart geometry (matches SVG viewBox "0 0 560 220")
const CP = { l: 46, r: 16, t: 16, b: 26 }
const PLOT_W = 560 - CP.l - CP.r   // 498
const PLOT_H = 220 - CP.t - CP.b   // 178
const Y_TICKS = [-1, -0.5, 0, 0.5, 1]

// ── State ────────────────────────────────────────────────────

const simResult      = ref(null)
const profilesMap    = ref({})
const followingMap   = ref({})   // user_id → [user_id, ...]
const router = useRouter()

const oasisAgents    = ref([])
const expandedAgent = ref(null)

const cfgTopic       = ref('')
const cfgSteps       = ref(4)
const cfgConcurrency = ref(3)
const interventions  = ref([])

const simRunning   = ref(false)
const simDone      = ref(false)
const simProgress  = ref(0)
const onlineSimId  = ref(null)
const logs         = ref([])
const logBox       = ref(null)

const posts           = ref([])
const attitudeData    = ref(null)
const statsData       = ref(null)
const interpreting    = ref(false)
const interpretation  = ref('')

// ── History ───────────────────────────────────────────────────
const historyList       = ref([])
const selectedHistoryId = ref(null)

// ── Computed ─────────────────────────────────────────────────

const postsReversed = computed(() => [...posts.value].reverse())

const usedGroupColors = computed(() => {
  if (!attitudeData.value) return GROUP_COLORS
  const used = {}
  for (const g of Object.keys(attitudeData.value.groups)) {
    used[g] = GROUP_COLORS[g] || '#94a3b8'
  }
  return used
})

// ── Display helpers ───────────────────────────────────────────

function wellnessColor(needs) {
  if (!needs) return '#94a3b8'
  const w = ((needs.satiety ?? 0.5) + (needs.energy ?? 0.5) +
             (needs.safety ?? 0.5) + (needs.social ?? 0.5)) / 4
  if (w < 0.3)  return '#ef4444'
  if (w < 0.55) return '#f97316'
  return '#7c3aed'
}

function groupColor(group) {
  return GROUP_COLORS[group] || '#94a3b8'
}

function groupBadgeStyle(group) {
  const c = groupColor(group)
  return `background:${c}1a; color:${c}; border:1px solid ${c}44`
}

function groupShort(group) {
  const MAP = {
    '权威媒体/大V': 'Media/V',
    '活跃KOL':     'KOL',
    '活跃创作者':  'Creator',
    '普通用户':    'Regular',
    '潜水用户':    'Lurker',
  }
  return MAP[group] || group
}

function attitudeClass(score) {
  if (score == null) return ''
  if (score >  0.3) return 'pos'
  if (score < -0.3) return 'neg'
  return 'neu'
}

function groupBarPct(n) {
  if (!statsData.value?.by_group?.length) return 0
  const max = Math.max(...statsData.value.by_group.map(g => g.posts))
  return max ? Math.round(n / max * 100) : 0
}

function actionBarPct(cnt) {
  if (!statsData.value?.action_types) return 0
  const max = Math.max(...Object.values(statsData.value.action_types))
  return max ? Math.round(cnt / max * 100) : 0
}

const ACTION_LABELS = {
  'create_post':  '发帖',
  'like_post':    '点赞',
  'dislike_post': '踩',
  'follow':       '关注',
  'repost':       '转发',
  'quote_post':   '引用',
  'comment':      '评论',
  'mute':         '屏蔽',
  'do_nothing':   '无操作',
}
function formatAction(act) {
  return ACTION_LABELS[act?.toLowerCase()] || act
}

function truncate(str, n) {
  if (!str) return ''
  return str.length > n ? str.slice(0, n) + '…' : str
}

function sliderStyle(val, min, max) {
  const pct = ((val - min) / (max - min) * 100).toFixed(1) + '%'
  return `background: linear-gradient(to right, var(--purple) ${pct}, var(--border) ${pct})`
}

// ── Chart helpers ─────────────────────────────────────────────

function chartX(idx, total) {
  return CP.l + (idx / Math.max(total - 1, 1)) * PLOT_W
}

function chartY(val) {
  return CP.t + ((1 - val) / 2) * PLOT_H
}

function buildPath(groupName) {
  const d = attitudeData.value
  if (!d) return ''
  const vals = d.groups[groupName]
  if (!vals?.length) return ''
  return vals.map((v, i) =>
    `${i === 0 ? 'M' : 'L'} ${chartX(i, d.steps.length).toFixed(1)} ${chartY(v ?? 0).toFixed(1)}`
  ).join(' ')
}

// ── Profile → OASIS mapping ───────────────────────────────────

function classifyGroup(agent, profile) {
  const occ = (profile?.occupation || agent.occupation || '').toLowerCase()
  const interests = profile?.interests || []
  const social = agent.needs?.social ?? 0.5

  if (occ.includes('博士') || occ.includes('研究生') || occ.includes('硕士'))
    return '权威媒体/大V'

  const creativeKw = ['写作', '摄影', '视频', '创作', '艺术', '绘画', '音乐', '设计']
  if (interests.some(i => creativeKw.includes(i))) return '活跃创作者'

  if (social > 0.7) return '活跃KOL'
  if (social < 0.25) return '潜水用户'
  return '普通用户'
}

function buildUserChar(agent, profile) {
  const name     = agent.name || profile?.name || '用户'
  const occ      = profile?.occupation || agent.occupation || '学生'
  const gender   = profile?.gender === 'male' ? '男生' : '女生'
  const interests = (profile?.interests || []).slice(0, 3).join('、')
  const intention = agent.intention || ''

  let char = `你是${name}，${gender}，${occ}。`
  if (interests) char += `兴趣爱好包括${interests}。`
  if (intention) char += `你最近的活动是"${truncate(intention, 30)}"。`
  if (cfgTopic.value) char += `你最近在关注「${cfgTopic.value}」这个话题，对此有自己的看法和态度。`
  char += `在社交平台上，你会以自然的方式表达自己对话题的真实看法。`
  return char
}

function buildOasisAgent(agent) {
  const profile  = profilesMap.value[agent.id] || {}
  const group    = classifyGroup(agent, profile)
  const username = (agent.name || profile.name || 'user')
    .replace(/[\s\u4e00-\u9fa5]/g, '').toLowerCase() || `user_${agent.id}`
  const occ = profile.occupation || agent.occupation || ''
  const bio = [occ, profile.gender === 'male' ? '男' : profile.gender === 'female' ? '女' : '',
               profile.major].filter(Boolean).join(' · ')

  return {
    agent_id:    agent.id,
    user_id:     agent.id,
    username,
    name:        agent.name || profile.name || agent.id,
    bio,
    description: bio,
    user_char:   buildUserChar(agent, profile),
    group,
    initial_attitude: 0.0,
    following_agentid_list: followingMap.value[agent.id] || [],
    _needs:      agent.needs,
    _intention:  agent.intention,
    _occupation: occ,
  }
}

// ── Interventions ─────────────────────────────────────────────

function addIntervention() {
  interventions.value.push({ step: 1, type: 'broadcast', content: '', target_group: '' })
}

function ivPlaceholder(type) {
  if (type === 'broadcast')     return '广播消息内容（如：官宣演唱会时间为9月15日）'
  if (type === 'bribery')       return '激励文本（如：您收到主办方合作邀请，请为演唱会发帖推广）'
  if (type === 'register_user') return 'Bot画像 JSON（如：{"persona": "pro-brand insider", "group": "活跃KOL"}）'
  return ''
}

// ── Interpret ─────────────────────────────────────────────────

async function interpretCurve() {
  if (!onlineSimId.value || interpreting.value) return
  interpreting.value   = true
  interpretation.value = ''
  try {
    const res = await interpretAttitude(onlineSimId.value)
    interpretation.value = res.interpretation || ''
  } catch (err) {
    interpretation.value = `解读失败：${err.message}`
  } finally {
    interpreting.value = false
  }
}

// ── Logging ───────────────────────────────────────────────────

function log(html) {
  const t = new Date().toTimeString().slice(0, 8)
  logs.value.push(`<span class="log-time">[${t}]</span> ${html}`)
  nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
}

// ── History ────────────────────────────────────────────────────

async function loadHistory() {
  try {
    historyList.value = await getOnlineSimHistory()
  } catch (_) {}
}

async function loadHistorySim(simId) {
  selectedHistoryId.value = simId
  onlineSimId.value = simId
  simDone.value = true
  simRunning.value = false
  interpretation.value = ''
  try {
    const [postsData, attData, stData] = await Promise.all([
      getOnlineSimPosts(simId),
      getOnlineSimAttitude(simId),
      getOnlineSimStats(simId),
    ])
    posts.value        = postsData.posts || []
    attitudeData.value = attData
    statsData.value    = stData
    // 从历史记录里恢复 topic 显示
    const rec = historyList.value.find(r => r.sim_id === simId)
    if (rec) cfgTopic.value = rec.topic
  } catch (err) {
    log(`<span class="log-err">✗ 加载历史记录失败: ${err.message}</span>`)
  }
}

function formatHistoryTime(isoStr) {
  if (!isoStr) return '—'
  const d = new Date(isoStr)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

function historyStatusLabel(status) {
  return { running: '运行中', completed: '完成', error: '出错', unknown: '未知' }[status] || status
}

function historyStatusClass(status) {
  return { running: 'hist-running', completed: 'hist-done', error: 'hist-error' }[status] || ''
}

// ── Simulation ────────────────────────────────────────────────

let sseConn = null

async function startSim() {
  if (!simResult.value || !cfgTopic.value.trim()) return

  simRunning.value     = true
  simDone.value        = false
  simProgress.value    = 0
  posts.value          = []
  attitudeData.value   = null
  statsData.value      = null
  interpretation.value = ''
  logs.value           = []

  log('Preparing agent profiles…')

  try {
    const payload = {
      agents:        oasisAgents.value,
      topic:         cfgTopic.value.trim(),
      total_steps:   cfgSteps.value,
      concurrency:   cfgConcurrency.value,
      interventions: interventions.value,
    }
    const { online_sim_id } = await startOnlineSim(payload)
    onlineSimId.value = online_sim_id
    log(`<span class="log-ok">✓</span> Started — <span class="log-info">${online_sim_id}</span>`)
    connectSSE(online_sim_id)
  } catch (err) {
    log(`<span class="log-err">✗ ${err.message}</span>`)
    simRunning.value = false
  }
}

function connectSSE(simId) {
  if (sseConn) { sseConn.close(); sseConn = null }
  sseConn = new EventSource(`/api/online-sim/${simId}/stream`)

  sseConn.onmessage = async (e) => {
    const ev = JSON.parse(e.data)
    if (ev.type === 'heartbeat') return

    if (ev.type === 'log') {
      log(ev.message)
    }

    if (ev.type === 'step_done') {
      simProgress.value = ev.step
      try {
        const [postsData, attData] = await Promise.all([
          getOnlineSimPosts(simId),
          getOnlineSimAttitude(simId),
        ])
        posts.value        = postsData.posts || []
        attitudeData.value = attData
      } catch (_) {}
    }

    if (ev.type === 'complete') {
      simRunning.value = false
      simDone.value    = true
      sseConn.close(); sseConn = null
      log('<span class="log-ok">✓</span> Simulation complete')
      try {
        const [postsData, attData, stData] = await Promise.all([
          getOnlineSimPosts(simId),
          getOnlineSimAttitude(simId),
          getOnlineSimStats(simId),
        ])
        posts.value        = postsData.posts || []
        attitudeData.value = attData
        statsData.value    = stData
      } catch (_) {}
      loadHistory()  // 刷新历史列表
    }

    if (ev.type === 'error') {
      simRunning.value = false
      sseConn.close(); sseConn = null
      log(`<span class="log-err">✗ ${ev.message}</span>`)
    }
  }

  sseConn.onerror = () => {
    if (sseConn) { sseConn.close(); sseConn = null }
    if (simRunning.value) setTimeout(() => connectSSE(simId), 3000)
  }
}

// ── Lifecycle ─────────────────────────────────────────────────

onMounted(async () => {
  loadHistory()

  const raw = localStorage.getItem('simResult')
  if (raw) {
    try { simResult.value = JSON.parse(raw) } catch (_) {}
  }

  if (simResult.value?.agents?.length) {
    try {
      const [profiles, relData] = await Promise.all([getProfiles(), getRelationships()])
      profiles.forEach(p => { profilesMap.value[p.user_id] = p })

      // 构建 followingMap：user_id → 关注列表（原始 string ID）
      const fm = {}
      for (const r of (relData.relationships || [])) {
        if (!r.agent1 || !r.agent2) continue
        if (!fm[r.agent1]) fm[r.agent1] = []
        fm[r.agent1].push(r.agent2)
        if (!r.directed) {           // 无向关系（朋友/室友等）双向关注
          if (!fm[r.agent2]) fm[r.agent2] = []
          fm[r.agent2].push(r.agent1)
        }
      }
      followingMap.value = fm
    } catch (_) {}
    oasisAgents.value = simResult.value.agents.map(buildOasisAgent)
  }
})

onBeforeUnmount(() => {
  if (sseConn) { sseConn.close(); sseConn = null }
})
</script>

<style scoped>
.online-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

/* ── Nav back button ── */
.nav-back-btn {
  margin-right: 12px;
  padding: 5px 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-dim);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.nav-back-btn:hover { color: var(--text); background: rgba(0,0,0,0.04); }

/* ── Step indicator ── */
.step-indicator { display: flex; align-items: center; gap: 6px; }
.step-pip {
  width: 22px; height: 22px; border-radius: 50%;
  border: 2px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  color: var(--text-muted); background: var(--bg);
}
.step-pip.active { border-color: var(--purple); color: var(--purple); background: rgba(124,58,237,.08); }
.step-pip.done   { border-color: #22c55e; background: rgba(34,197,94,.1); color: #22c55e; }
.step-line       { width: 20px; height: 2px; background: var(--border); border-radius: 1px; }
.step-line.done  { background: #22c55e; }
.step-label      { font-size: 11px; font-weight: 600; color: var(--text-dim); margin-left: 4px; }

/* ── Workspace — 4-column flex ── */
.workspace {
  display: flex;
  flex: 1;
  overflow: hidden;
  min-width: 900px;
}

/* ── Sidebar (unchanged) ── */
.sidebar {
  width: 300px; flex-shrink: 0;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--surface); border-right: 1px solid var(--border);
}
.sidebar-scroll { flex: 1; overflow-y: auto; padding: 20px 20px 0; }
.sidebar-scroll::-webkit-scrollbar { width: 4px; }
.sidebar-scroll::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: 2px; }

/* ── Posts column ── */
.posts-col {
  width: 400px; flex-shrink: 0;
  display: flex; flex-direction: column; overflow: hidden;
  border-right: 1px solid var(--border);
  background: var(--bg);
}

.col-header {
  display: flex; align-items: center; gap: 8px;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--surface); flex-shrink: 0;
}
.col-title {
  font-size: 13px; font-weight: 700; color: var(--text);
}
.step-badge {
  margin-left: auto;
  font-size: 11px; font-weight: 600; color: var(--purple);
  background: rgba(124,58,237,.08); border-radius: 6px;
  padding: 2px 8px;
}

.col-empty {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: var(--text-muted); text-align: center; padding: 32px;
}

.posts-feed {
  flex: 1; overflow-y: auto;
  padding: 12px;
  display: flex; flex-direction: column; gap: 10px;
}
.posts-feed::-webkit-scrollbar { width: 4px; }
.posts-feed::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: 2px; }

/* ── Post cards ── */
.post-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px;
}
.post-card.pos { background: #f0fdf4; border-color: #bbf7d0; }
.post-card.neg { background: #fff1f2; border-color: #fecdd3; }

.post-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.post-avatar {
  width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 700; font-size: 13px;
}
.post-meta { flex: 1; min-width: 0; }
.post-uname { font-size: 13px; font-weight: 600; color: var(--text); }
.post-sub-row { display: flex; align-items: center; gap: 5px; margin-top: 2px; }
.post-step  { font-size: 11px; color: var(--text-muted); }

.attitude-chip {
  font-size: 11px; font-weight: 700; border-radius: 5px; padding: 2px 7px; flex-shrink: 0;
}
.attitude-chip.pos { background: #dcfce7; color: #16a34a; }
.attitude-chip.neg { background: #fee2e2; color: #dc2626; }
.attitude-chip.neu { background: var(--border); color: var(--text-muted); }

.post-quote {
  font-size: 12px; color: var(--text-muted);
  background: var(--bg); border-left: 3px solid var(--border);
  border-radius: 0 5px 5px 0; padding: 5px 8px; margin-bottom: 6px; line-height: 1.5;
}
.post-quote-label {
  font-size: 10px; font-weight: 600; color: var(--purple);
  text-transform: uppercase; letter-spacing: .4px; margin-right: 5px;
}
.post-body { font-size: 13px; color: var(--text); line-height: 1.65; }
.post-engage {
  display: flex; gap: 14px; margin-top: 8px;
  padding-top: 7px; border-top: 1px solid var(--border);
}
.engage-item { display: flex; align-items: center; gap: 3px; font-size: 11px; color: var(--text-muted); }
.engage-ico  { font-size: 12px; }

/* ── Right column ── */
.right-col {
  flex: 1; overflow-y: auto; min-width: 0;
  display: flex; flex-direction: column;
  background: var(--bg);
}
.right-col::-webkit-scrollbar { width: 4px; }
.right-col::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: 2px; }

.right-panel {
  padding: 16px 20px 20px;
  border-bottom: 1px solid var(--border);
}
.right-panel:last-child { border-bottom: none; }

.panel-header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 14px;
}
.panel-topic {
  font-size: 12px; color: var(--text-muted);
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 5px; padding: 1px 8px;
}

.panel-empty {
  display: flex; flex-direction: column; align-items: center;
  padding: 24px 0; color: var(--text-muted); text-align: center; gap: 8px;
}
.empty-ico    { font-size: 36px; }
.empty-ico.sm { font-size: 24px; }
.empty-ttl    { font-size: 15px; font-weight: 600; color: var(--text-dim); }
.empty-dsc    { font-size: 12px; color: var(--text-muted); max-width: 280px; line-height: 1.6; }

/* ── Attitude SVG ── */
.attitude-svg { width: 100%; display: block; max-height: 200px; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 12px; }
.legend-item  { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-dim); }
.legend-dot   { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }

/* ── Stats ── */
.stat-cards {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 16px;
}
.stat-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 8px; text-align: center;
}
.stat-value {
  font-size: 24px; font-weight: 800;
  background: var(--grad); -webkit-background-clip: text;
  -webkit-text-fill-color: transparent; background-clip: text; line-height: 1.1; margin-bottom: 3px;
}
.stat-label { font-size: 10px; color: var(--text-muted); font-weight: 500; }

.stats-body { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 16px; }

.stats-block {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 14px;
}
.stats-block-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .6px; color: var(--text-muted); margin-bottom: 12px;
}

.gbar-row    { margin-bottom: 10px; }
.gbar-label  { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; }
.group-dot   { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.gbar-track  { height: 5px; background: var(--border); border-radius: 3px; overflow: hidden; margin-bottom: 3px; }
.gbar-fill   { height: 100%; border-radius: 3px; transition: width .4s ease; }
.gbar-nums   { font-size: 11px; color: var(--text-muted); }

.action-row        { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.action-name       { font-size: 11px; color: var(--text-dim); min-width: 60px; flex-shrink: 0; }
.action-bar-track  { flex: 1; height: 5px; background: var(--border); border-radius: 3px; overflow: hidden; }
.action-bar-fill   { height: 100%; background: var(--purple); border-radius: 3px; transition: width .4s ease; }
.action-cnt        { font-size: 11px; color: var(--text-muted); min-width: 24px; text-align: right; }

.top-posts-block { border-top: 1px solid var(--border); padding-top: 14px; }
.top-post-row    { display: flex; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.top-post-row:last-child { border-bottom: none; }
.top-rank        { font-size: 15px; font-weight: 800; color: var(--purple); min-width: 24px; padding-top: 2px; flex-shrink: 0; }
.top-post-body   { flex: 1; min-width: 0; }
.top-post-header { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.top-post-name   { font-size: 13px; font-weight: 600; color: var(--text); }
.top-post-content { font-size: 12px; color: var(--text-dim); line-height: 1.55; margin-bottom: 5px; }
.top-post-engage  { display: flex; gap: 10px; font-size: 11px; color: var(--text-muted); }

/* ── Shared section ── */
.cfg-section       { margin-bottom: 24px; }
.cfg-section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .8px; color: var(--text-muted); margin-bottom: 12px;
  display: flex; align-items: center; gap: 8px;
}
.cfg-section-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }

.count-pill {
  background: var(--purple); color: #fff;
  border-radius: 10px; padding: 1px 7px; font-size: 10px; font-weight: 700; flex-shrink: 0;
}

/* ── Agent cards ── */
.agent-list { display: flex; flex-direction: column; gap: 6px; }
.agent-card {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: border-color .15s;
}
.agent-card:hover { border-color: var(--purple); }
.agent-card.expanded { border-color: var(--purple); }
.agent-card-row { display: flex; align-items: center; gap: 10px; }
.agent-avatar {
  width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 700; font-size: 13px;
}
.agent-card-info { flex: 1; min-width: 0; }
.agent-name { font-size: 13px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.agent-occ  { font-size: 11px; color: var(--text-muted); }
.group-badge {
  font-size: 10px; font-weight: 600; border-radius: 6px; padding: 2px 7px; white-space: nowrap; flex-shrink: 0;
}
.agent-detail { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px; }
.detail-label { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 5px; }
.detail-text  { font-size: 11px; color: var(--text-dim); line-height: 1.6; }

.group-badge-sm {
  font-size: 10px; font-weight: 600; border-radius: 5px; padding: 1px 6px;
}

/* ── Form controls ── */
.form-row    { margin-bottom: 12px; }
.form-label  { display: block; font-size: 12px; font-weight: 500; color: var(--text-dim); margin-bottom: 5px; }
.form-control {
  width: 100%; background: var(--bg); border: 1px solid var(--border);
  border-radius: 7px; padding: 8px 11px; font-size: 13px;
  color: var(--text); outline: none; transition: border-color .15s; font-family: inherit;
  box-sizing: border-box;
}
.form-control:focus { border-color: var(--purple); }

.slider-header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.slider-val {
  font-size: 22px; font-weight: 800;
  background: var(--grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
input[type=range] {
  -webkit-appearance: none; appearance: none;
  width: 100%; height: 4px; border-radius: 2px; outline: none; cursor: pointer;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 15px; height: 15px; border-radius: 50%;
  background: var(--purple); box-shadow: 0 0 0 3px rgba(124,58,237,.15);
}

/* ── Intervention cards ── */
.btn-tiny {
  margin-left: auto; font-size: 10px; font-weight: 600;
  background: var(--purple); color: #fff;
  border: none; border-radius: 5px; padding: 2px 8px; cursor: pointer;
}
.iv-card         { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 8px; }
.iv-card-header  { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.iv-idx          { font-size: 10px; font-weight: 700; color: var(--text-muted); flex-shrink: 0; }
.iv-type         { flex: 1; padding: 5px 8px; font-size: 12px; }
.iv-step-wrap    { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.iv-step-label   { font-size: 10px; color: var(--text-muted); }
.iv-step         { width: 46px; padding: 5px 6px; font-size: 12px; text-align: center; }
.iv-body         { font-size: 12px; resize: vertical; min-height: 48px; }
.btn-del         { background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 0 2px; line-height: 1; flex-shrink: 0; }
.btn-del:hover   { color: #ef4444; }

/* ── Sidebar footer ── */
.sidebar-footer { padding: 14px 20px 20px; border-top: 1px solid var(--border); flex-shrink: 0; }
.log-box {
  background: #f1f3f8; border: 1px solid var(--border); border-radius: 7px;
  padding: 8px 10px; font-family: 'SF Mono','Fira Code',monospace; font-size: 10.5px;
  color: var(--text-dim); line-height: 1.9; max-height: 80px; overflow-y: auto; margin-bottom: 10px;
}
.btn-start {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  background: var(--grad); color: #fff; font-weight: 600; font-size: 14px;
  padding: 13px; border-radius: 10px; border: none; cursor: pointer;
  width: 100%; transition: opacity .2s, transform .15s, box-shadow .2s; font-family: inherit;
}
.btn-start:hover:not(:disabled) { opacity: .9; transform: translateY(-1px); box-shadow: 0 6px 24px rgba(124,58,237,.3); }
.btn-start:disabled { opacity: .4; cursor: not-allowed; }
.hint-text { font-size: 11px; color: var(--text-muted); text-align: center; margin-top: 6px; }
.dot-pulse { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #fff; animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .3; } }

.empty-hint { font-size: 12px; color: var(--text-muted); padding: 10px 0; text-align: center; line-height: 1.6; }

/* ── Interpret ── */
.btn-interpret {
  margin-left: auto;
  font-size: 11px; font-weight: 600;
  background: rgba(124,58,237,.1); color: var(--purple);
  border: 1px solid rgba(124,58,237,.3); border-radius: 6px;
  padding: 3px 10px; cursor: pointer; transition: background .15s;
  white-space: nowrap;
}
.btn-interpret:hover:not(:disabled) { background: rgba(124,58,237,.18); }
.btn-interpret:disabled { opacity: .5; cursor: not-allowed; }

.interpret-card {
  margin-top: 14px;
  background: linear-gradient(135deg, rgba(124,58,237,.06), rgba(99,102,241,.06));
  border: 1px solid rgba(124,58,237,.2); border-radius: 10px;
  padding: 14px 16px;
}
.interpret-card-title {
  font-size: 11px; font-weight: 700; color: var(--purple);
  text-transform: uppercase; letter-spacing: .6px; margin-bottom: 8px;
}
.interpret-card-text {
  font-size: 13px; color: var(--text-dim); line-height: 1.8;
  margin: 0; white-space: pre-wrap;
}

/* ── History ── */
.history-section { border-top: 1px solid var(--border); padding-top: 14px; }

.hist-list { display: flex; flex-direction: column; gap: 6px; }

.hist-card {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 9px 12px; cursor: pointer; transition: border-color .15s, background .15s;
}
.hist-card:hover { border-color: var(--purple); background: rgba(124,58,237,.04); }
.hist-card.hist-active { border-color: var(--purple); background: rgba(124,58,237,.07); }

.hist-card-row { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 3px; }
.hist-topic { font-size: 13px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hist-meta { font-size: 11px; color: var(--text-muted); }

.hist-status { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 10px; flex-shrink: 0; }
.hist-done    { background: rgba(22,163,74,.12); color: #16a34a; }
.hist-running { background: rgba(234,179,8,.12); color: #a16207; }
.hist-error   { background: rgba(239,68,68,.12); color: #dc2626; }
</style>

<style>
.log-time { color: var(--text-muted); }
.log-ok   { color: #16a34a; }
.log-info { color: var(--purple); }
.log-err  { color: #dc2626; }
</style>
