<template>
  <div class="sim-layout">

    <!-- NavBar — Step 2 active -->
    <NavBar>
      <template #right>
        <button class="nav-back-btn" @click="router.push('/setup')">← Back</button>
        <div class="step-indicator">
          <div class="step-pip done">1</div>
          <div class="step-line done"></div>
          <div class="step-pip active">2</div>
          <div class="step-line"></div>
          <div class="step-pip">3</div>
          <span class="step-label">Simulate</span>
        </div>
      </template>
    </NavBar>

    <div class="workspace">

      <!-- ── Left Sidebar ── -->
      <aside class="sim-sidebar">
        <div class="sidebar-scroll">

          <!-- A: Simulation Config -->
          <div class="cfg-section">
            <div class="cfg-section-title">Simulation Config</div>

            <div class="form-row">
              <label class="form-label">Simulation Steps</label>
              <input type="range" min="1" max="24" step="1"
                     v-model.number="cfgSteps" :style="sliderStyle(cfgSteps, 1, 24)">
              <div class="slider-ticks">
                <span>1</span><span class="tick-val">{{ cfgSteps }}</span><span>24</span>
              </div>
            </div>

            <div class="form-row">
              <label class="form-label">Time per Step</label>
              <select class="form-control" v-model.number="cfgTick">
                <option :value="1800">30 minutes</option>
                <option :value="3600">1 hour</option>
                <option :value="7200">2 hours</option>
                <option :value="10800">3 hours</option>
              </select>
            </div>

            <div class="form-row">
              <label class="form-label">Start Time</label>
              <input type="datetime-local" class="form-control"
                     v-model="cfgStartTime" style="color-scheme:light">
            </div>

            <div class="form-row">
              <label class="form-label">LLM Concurrency</label>
              <input type="range" min="1" max="20" step="1"
                     v-model.number="cfgConcurrency" :style="sliderStyle(cfgConcurrency, 1, 20)">
              <div class="slider-ticks">
                <span>1</span><span class="tick-val">{{ cfgConcurrency }}</span><span>20</span>
              </div>
            </div>

            <button class="btn-start-sim" :disabled="simRunning" @click="startSim">
              <span v-if="simRunning" class="spinner-sm"></span>
              <svg v-else width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
                <polygon points="5,3 13,8 5,13"/>
              </svg>
              <span v-if="simRunning">Running {{ viewingSteps.length }} / {{ viewingTotal }}</span>
              <span v-else>Start Simulation</span>
            </button>
          </div>

          <!-- B: History -->
          <div class="cfg-section">
            <div class="cfg-section-title">Simulation History</div>
            <div class="history-list">
              <div
                v-for="s in historyList" :key="s.sim_id"
                class="history-card"
                :class="{ active: s.sim_id === viewingSimId }"
                @click="loadSimulation(s.sim_id)"
              >
                <div class="hcard-row">
                  <span class="hcard-title">{{ formatSimTitle(s) }}</span>
                  <span class="hcard-badge" :class="s.status">{{ statusText(s.status) }}</span>
                </div>
                <div class="hcard-meta">
                  {{ s.num_agents }} agents · {{ s.total_steps }} steps
                  <span v-if="s.status === 'running'"> · {{ s.current_step }}/{{ s.total_steps }}</span>
                </div>
              </div>
              <div v-if="!historyList.length" class="empty-history">
                No simulations yet — start one above
              </div>
            </div>
          </div>

        </div>

        <!-- C: Next Step (sticky bottom) -->
        <div class="next-step-wrap">
          <button class="btn-next-step" :disabled="!canProceed" @click="handleNextStep">
            Next Step
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 8h10M9 4l4 4-4 4"/>
            </svg>
          </button>
          <div class="next-step-hint" v-if="!canProceed && viewingSimId">
            {{ nextStepHint }}
          </div>
        </div>
      </aside>

      <!-- ── Map Area ── -->
      <main class="map-area">
        <div ref="mapEl" class="map-canvas"></div>

        <!-- Empty state -->
        <div v-if="!viewingSimId" class="map-empty">
          <div class="map-empty-icon">🗺️</div>
          <div class="map-empty-title">No simulation selected</div>
          <div class="map-empty-sub">Configure parameters and click Start Simulation</div>
        </div>

        <!-- Step overlay (top-left) -->
        <div class="step-overlay" v-if="currentStepData">
          <div class="ov-row">
            <span class="ov-label">Step</span>
            <span class="ov-val">{{ currentStepData.step }} / {{ viewingTotal }}</span>
          </div>
          <div class="ov-row">
            <span class="ov-label">Time</span>
            <span class="ov-val">{{ currentStepData.sim_time }}</span>
          </div>
          <div class="ov-row">
            <span class="ov-label">Date</span>
            <span class="ov-val">{{ currentStepData.sim_date }}</span>
          </div>
        </div>

        <!-- Agent list overlay (top-right, small) -->
        <div class="agent-chips" v-if="agentList.length">
          <div
            v-for="a in agentList" :key="a.id"
            class="agent-chip"
            :class="{ selected: selectedId === a.id }"
            @click="openDetail(a.id)"
          >
            <span class="chip-dot" :style="{ background: agentColor(a) }"></span>
            <span class="chip-name">{{ a.name }}</span>
            <!-- 消息收发小图标 -->
            <span v-if="a.sent" class="chip-msg-badge sent" title="发送了消息">↑</span>
            <span v-if="a.received?.length" class="chip-msg-badge recv" title="收到消息">↓{{ a.received.length }}</span>
          </div>
        </div>

        <!-- Replay bar (bottom) -->
        <div class="replay-bar" v-if="viewingSteps.length">
          <button class="replay-btn" :disabled="displayIdx <= 0"
                  @click="gotoIdx(displayIdx - 1)">◀</button>
          <button class="replay-btn" :disabled="displayIdx >= viewingSteps.length - 1"
                  @click="gotoIdx(displayIdx + 1)">▶</button>
          <input type="range" class="replay-slider"
            min="0" :max="Math.max(0, viewingSteps.length - 1)"
            :value="Math.max(0, displayIdx)"
            @input="gotoIdx(parseInt($event.target.value))"
          />
          <span class="replay-count">
            {{ displayIdx >= 0 ? viewingSteps[displayIdx]?.step : '—' }} / {{ viewingTotal || '—' }}
          </span>
          <label class="auto-label">
            <input type="checkbox" v-model="autoFollow" />
            <span>Auto</span>
          </label>
        </div>

        <!-- Agent detail drawer -->
        <div class="agent-drawer" :class="{ open: !!selectedId }">
          <template v-if="detailAgent">
            <div class="drawer-header">
              <div class="drawer-avatar">
                <img
                  :src="`/avatars/${detailMeta?.gender ?? 'male'}_${((selectedId - 1) % 5) + 1}.svg`"
                  style="width:100%;height:100%;border-radius:50%;object-fit:cover"
                />
              </div>
              <div class="drawer-titles">
                <div class="drawer-name">{{ detailMeta?.name || detailAgent.name }}</div>
                <div class="drawer-meta">
                  {{ [detailMeta?.occupation, detailMeta?.gender === 'male' ? '♂' : detailMeta?.gender === 'female' ? '♀' : ''].filter(Boolean).join(' · ') }}
                </div>
              </div>
              <button class="drawer-close" @click="closeDetail">×</button>
            </div>

            <div class="drawer-body">
              <div class="drawer-section-title">Current Needs</div>
              <div class="needs-list">
                <div v-for="[key, lbl] in NEEDS_ENTRIES" :key="key" class="need-row">
                  <span class="need-lbl">{{ lbl }}</span>
                  <div class="need-track">
                    <div class="need-fill"
                      :style="{ width: pct(detailAgent.needs?.[key]) + '%', background: NEEDS_COLORS[key] }">
                    </div>
                  </div>
                  <span class="need-pct" :style="{ color: NEEDS_COLORS[key] }">
                    {{ pct(detailAgent.needs?.[key]) }}%
                  </span>
                </div>
              </div>

              <div class="drawer-section-title">Current Intention</div>
              <div class="intention-card">
                <div class="intention-main">{{ detailAgent.intention || '—' }}</div>
                <div class="intention-sub" v-if="detailAgent.reasoning">
                  <span class="sub-label">Reasoning</span>{{ detailAgent.reasoning }}
                </div>
                <div class="intention-sub" v-if="detailAgent.act_result">
                  <span class="sub-label">Result</span>{{ detailAgent.act_result }}
                </div>
              </div>

              <!-- ── Messages（本步收发） ── -->
              <template v-if="detailAgent.sent || detailAgent.received?.length">
                <div class="drawer-section-title">Messages This Step</div>
                <div class="msg-list">
                  <div v-if="detailAgent.sent" class="msg-row sent-row">
                    <div class="msg-header">
                      <span class="msg-dir sent-dir">↑ 发送</span>
                      <span class="msg-target-badge" :class="targetBadgeClass(detailAgent.sent.target)">
                        {{ formatTarget(detailAgent.sent.target) }}
                      </span>
                    </div>
                    <div class="msg-content">{{ detailAgent.sent.content }}</div>
                  </div>
                  <div v-for="(r, i) in detailAgent.received" :key="i" class="msg-row recv-row">
                    <div class="msg-header">
                      <span class="msg-dir recv-dir">↓ 收到</span>
                      <span class="msg-target-badge" :class="targetBadgeClass(r.target_type)">
                        {{ r.target_type }}
                      </span>
                      <span class="msg-sender">{{ r.sender_name }}</span>
                    </div>
                    <div class="msg-content">{{ r.content }}</div>
                  </div>
                </div>
              </template>

              <div class="drawer-section-title">Event History</div>
              <div class="event-list">
                <div v-for="entry in agentHistory" :key="entry.step" class="event-item">
                  <div class="event-meta">
                    Step {{ entry.step }} · {{ entry.sim_time }}
                    <span v-if="entry.sent" class="ev-badge ev-sent" title="发送了消息">↑</span>
                    <span v-if="entry.received?.length" class="ev-badge ev-recv" title="收到消息">
                      ↓{{ entry.received.length }}
                    </span>
                  </div>
                  <div class="event-intention">{{ entry.intention }}</div>
                  <div class="event-result" v-if="entry.act_result">▸ {{ entry.act_result }}</div>
                </div>
                <div v-if="!agentHistory.length" class="event-empty">No records yet</div>
              </div>
            </div>
          </template>
        </div>

      </main>

      <!-- ── Message Panel ── -->
      <aside class="msg-panel" :class="{ collapsed: !msgPanelOpen }">
        <!-- 折叠/展开 tab -->
        <div class="msg-panel-tab" @click="msgPanelOpen = !msgPanelOpen">
          <span class="tab-icon">💬</span>
          <span v-if="!msgPanelOpen" class="tab-count-vert">
            {{ stepMessages.length || '' }}
          </span>
        </div>

        <div v-if="msgPanelOpen" class="msg-panel-inner">
          <div class="msg-panel-header">
            <span class="msg-panel-title">Messages</span>
            <span v-if="currentStepData" class="msg-panel-step">Step {{ currentStepData.step }}</span>
            <span v-if="stepMessages.length" class="msg-panel-count">{{ stepMessages.length }}</span>
          </div>

          <div class="msg-panel-body">
            <div v-if="!stepMessages.length" class="msg-panel-empty">
              <div class="mp-empty-ico">💬</div>
              <div class="mp-empty-txt">No messages this step</div>
            </div>

            <div v-for="(m, i) in stepMessages" :key="i" class="mp-card"
                 :style="{ borderLeftColor: agentColorById(m.sender_id) }">
              <div class="mp-card-header">
                <span class="mp-sender" :style="{ color: agentColorById(m.sender_id) }">
                  {{ m.sender_name }}
                </span>
                <span class="mp-target-badge" :class="targetBadgeClass(m.target)">
                  {{ formatTarget(m.target) }}
                </span>
              </div>
              <div class="mp-content">{{ m.content }}</div>
            </div>
          </div>
        </div>
      </aside>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import NavBar from '../components/NavBar.vue'
import { createSimulation, getSimulation, getSimulations, getSimulationSteps, sseUrl } from '../api/index.js'

const router = useRouter()

// ── Constants ────────────────────────────────────────────────
const FUDAN_CENTER  = [31.2980, 121.5015]
const NEEDS_COLORS  = { satiety: '#f59e0b', energy: '#22c55e', safety: '#3b82f6', social: '#a855f7' }
const NEEDS_ENTRIES = Object.entries({ satiety: 'Satiety', energy: 'Energy', safety: 'Safety', social: 'Social' })
const AGENT_COLORS  = ['#FF6B35','#004E89','#7B2D8E','#1A936F','#C5283D','#E9724C','#3498db','#9b59b6','#27ae60','#f39c12']

// ── Template refs ────────────────────────────────────────────
const mapEl = ref(null)

// ── Message Panel ─────────────────────────────────────────────
const msgPanelOpen = ref(true)

// ── Non-reactive state ────────────────────────────────────────
let mapInstance    = null
let markers        = {}
let agentDataCache = {}   // { [id]: latest agent snapshot } for icon rebuilds
let sseConn        = null
let pollTimer      = null

// ── Config params ─────────────────────────────────────────────
const cfgSteps       = ref(12)
const cfgTick        = ref(3600)
const cfgStartTime   = ref('2024-09-02T08:00')
const cfgConcurrency = ref(5)

// ── Active simulation (SSE running) ──────────────────────────
const activeSimId = ref(null)   // sim_id currently streaming via SSE
const simRunning  = ref(false)

// ── Viewing simulation (shown on map, may differ from active) ─
const viewingSimId  = ref(null)
const viewingSteps  = ref([])   // all steps for the viewed sim
const viewingTotal  = ref(0)
const agentsMeta    = ref({})   // { [id]: { name, occupation, gender, ... } }

// ── Display cursor ────────────────────────────────────────────
const displayIdx = ref(-1)
const autoFollow = ref(true)

const currentStepData = computed(() => viewingSteps.value[displayIdx.value] ?? null)
const agentList       = computed(() => currentStepData.value?.agents ?? [])
const stepMessages    = computed(() => currentStepData.value?.channel_messages ?? [])

// ── History list ─────────────────────────────────────────────
const historyList = ref([])

// ── Workflow: Next Step ───────────────────────────────────────
const viewingStatus = computed(() =>
  historyList.value.find(s => s.sim_id === viewingSimId.value)?.status ?? null
)
const canProceed = computed(() => viewingStatus.value === 'completed')
const nextStepHint = computed(() => {
  if (!viewingSimId.value) return 'Select a completed simulation'
  if (viewingStatus.value === 'running')     return 'Waiting for simulation to complete…'
  if (viewingStatus.value === 'error')       return 'Simulation encountered an error'
  if (viewingStatus.value === 'initializing') return 'Simulation is initializing…'
  return 'Simulation not completed yet'
})

function handleNextStep() {
  localStorage.setItem('simResult', JSON.stringify({
    sim_id:      viewingSimId.value,
    total_steps: viewingSteps.value.length,
    agents:      viewingSteps.value.at(-1)?.agents ?? [],
  }))
  router.push('/online-sim')
}

// ── Helpers ───────────────────────────────────────────────────
function wellness(needs) {
  if (!needs) return 0.6
  const vals = Object.values(needs)
  return vals.reduce((a, b) => a + b, 0) / (vals.length || 1)
}
function agentColor(agent) {
  const w = wellness(agent.needs)
  if (w < 0.30) return '#ef4444'
  if (w < 0.55) return '#f97316'
  return AGENT_COLORS[(agent.id ?? 0) % AGENT_COLORS.length]
}
function pct(v) { return ((v ?? 0) * 100).toFixed(0) }
function nameInitials(name) {
  if (!name) return '?'
  return name.replace(/[^\w\u4e00-\u9fa5]/g, '').slice(0, 2)
}
function statusText(s) {
  return { pending: 'Pending', initializing: 'Init', running: 'Running', completed: 'Done', error: 'Error' }[s] ?? s
}
function formatSimTitle(s) {
  if (!s.start_time) return s.sim_id?.slice(0, 12) ?? '—'
  return new Date(s.start_time).toLocaleString('en-US', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}
function sliderStyle(val, min, max) {
  const pct = ((val - min) / (max - min) * 100).toFixed(1) + '%'
  return `background: linear-gradient(to right, var(--purple) ${pct}, var(--border) ${pct})`
}

// ── Map ───────────────────────────────────────────────────────
function initMap() {
  mapInstance = L.map(mapEl.value, { zoomControl: false }).setView(FUDAN_CENTER, 15)
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: 'abcd', maxZoom: 19,
  }).addTo(mapInstance)
  L.control.zoom({ position: 'bottomright' }).addTo(mapInstance)
}

// Build/update fallback position from previous step
const lastPos = {}

function buildAvatarIcon(agent, selected = false) {
  const gender  = agentsMeta.value[agent.id]?.gender ?? 'male'
  const variant = ((agent.id - 1) % 5) + 1
  const color   = agentColor(agent)
  const border  = selected ? '#E91E63' : color
  const shadow  = selected
    ? '0 0 0 2px #E91E63, 0 2px 8px rgba(0,0,0,0.4)'
    : '0 2px 6px rgba(0,0,0,0.35)'
  return L.divIcon({
    html: `<img src="/avatars/${gender}_${variant}.svg"
               style="width:38px;height:38px;border-radius:50%;border:3px solid ${border};
                      background:#fff;box-shadow:${shadow};display:block;cursor:pointer">`,
    iconSize: [38, 38],
    iconAnchor: [19, 19],
    className: '',
  })
}

function updateMarkers(stepData) {
  if (!mapInstance || !stepData) return
  stepData.agents.forEach(agent => {
    let { lat, lng } = agent.position ?? {}
    if (!lat || !lng) {
      // Fallback to last known position or Fudan center
      const fb = lastPos[agent.id]
      if (fb) { lat = fb.lat; lng = fb.lng }
      else     { lat = FUDAN_CENTER[0]; lng = FUDAN_CENTER[1] }
    } else {
      lastPos[agent.id] = { lat, lng }
    }
    agentDataCache[agent.id] = agent
    const isSelected = agent.id === selectedId.value
    const tooltip = `<b>${agent.name}</b><br>${agent.intention ?? ''}`
    if (markers[agent.id]) {
      markers[agent.id].setLatLng([lat, lng])
      markers[agent.id].setIcon(buildAvatarIcon(agent, isSelected))
      markers[agent.id].setTooltipContent(tooltip)
    } else {
      markers[agent.id] = L.marker([lat, lng], { icon: buildAvatarIcon(agent, isSelected) })
        .bindTooltip(tooltip, { sticky: true, className: 'agent-tooltip' })
        .on('click', () => openDetail(agent.id))
        .addTo(mapInstance)
    }
  })
}

function clearMarkers() {
  Object.values(markers).forEach(m => { if (mapInstance) mapInstance.removeLayer(m) })
  markers = {}
}

// ── Replay ────────────────────────────────────────────────────
function gotoIdx(idx) {
  const clamped = Math.max(0, Math.min(idx, viewingSteps.value.length - 1))
  if (clamped === displayIdx.value && displayIdx.value >= 0) return
  displayIdx.value = clamped
  updateMarkers(viewingSteps.value[clamped])
  if (selectedId.value) updateMarkerHighlight()
}

// ── Detail drawer ─────────────────────────────────────────────
const selectedId  = ref(null)
const detailMeta  = computed(() => agentsMeta.value[selectedId.value] ?? null)
const detailAgent = computed(() =>
  currentStepData.value?.agents.find(a => a.id === selectedId.value) ?? null
)
const agentHistory = computed(() => {
  if (!selectedId.value) return []
  const limit = displayIdx.value < 0 ? 0 : displayIdx.value + 1
  return viewingSteps.value
    .slice(0, limit)
    .map(s => ({
      step:     s.step,
      sim_time: s.sim_time,
      ...s.agents.find(a => a.id === selectedId.value),
    }))
    .filter(r => r.intention !== undefined)
    .reverse()
})

// 按 agent_id 查颜色（消息面板中发送方着色）
function agentColorById(agentId) {
  const agent = agentDataCache[agentId]
  return agent ? agentColor(agent) : AGENT_COLORS[agentId % AGENT_COLORS.length]
}

// 格式化 target 字段为可读标签
function formatTarget(target) {
  if (target === 'nearby') return '附近'
  if (target === 'all')    return '广播'
  // 数字 agent_id：尝试找名字
  const name = agentDataCache[target]?.name
  return name ? `→${name}` : `私信`
}

// target badge 的 CSS class
function targetBadgeClass(target) {
  if (target === 'nearby' || target === '附近') return 'badge-nearby'
  if (target === 'all'    || target === '广播') return 'badge-all'
  return 'badge-private'
}

function openDetail(agentId) {
  selectedId.value = agentId
  updateMarkerHighlight()
}
function closeDetail() {
  selectedId.value = null
  Object.entries(markers).forEach(([id, m]) => {
    const agent = agentDataCache[parseInt(id)]
    if (agent) m.setIcon(buildAvatarIcon(agent, false))
  })
}
function updateMarkerHighlight() {
  Object.entries(markers).forEach(([id, m]) => {
    const agent = agentDataCache[parseInt(id)]
    if (agent) m.setIcon(buildAvatarIcon(agent, parseInt(id) === selectedId.value))
  })
}

// ── SSE ───────────────────────────────────────────────────────
function connectSSE(simId) {
  if (sseConn) { sseConn.close(); sseConn = null }
  sseConn = new EventSource(sseUrl(simId))

  sseConn.onmessage = e => {
    let ev
    try { ev = JSON.parse(e.data) } catch { return }
    if (ev.type === 'heartbeat') return

    if (ev.type === 'step') {
      // Only push into viewingSteps if we're watching this simulation
      if (viewingSimId.value === simId) {
        if (!viewingSteps.value.find(s => s.step === ev.step)) {
          viewingSteps.value.push(ev)
          viewingSteps.value.sort((a, b) => a.step - b.step)
        }
        if (autoFollow.value) {
          displayIdx.value = viewingSteps.value.length - 1
          updateMarkers(ev)
          if (selectedId.value) updateMarkerHighlight()
        }
      }
    }

    if (ev.type === 'complete') {
      simRunning.value = false
      if (sseConn) { sseConn.close(); sseConn = null }
      stopPoll()
      loadHistory()
    }

    if (ev.type === 'error') {
      simRunning.value = false
      if (sseConn) { sseConn.close(); sseConn = null }
      stopPoll()
      loadHistory()
    }
  }

  sseConn.onerror = () => {
    if (sseConn) { sseConn.close(); sseConn = null }
    if (simRunning.value) setTimeout(() => connectSSE(simId), 3000)
  }
}

// ── Start simulation ──────────────────────────────────────────
async function startSim() {
  const agentParams = JSON.parse(localStorage.getItem('agentParams') || '{}')
  const params = {
    num_agents:   agentParams.num_agents ?? 10,
    num_steps:    cfgSteps.value,
    tick_seconds: cfgTick.value,
    concurrency:  cfgConcurrency.value,
    start_time:   cfgStartTime.value.replace('T', ' ') + ':00',
  }

  try {
    const { sim_id } = await createSimulation(params)
    activeSimId.value  = sim_id
    simRunning.value   = true

    // Switch map view to this new simulation
    viewingSimId.value   = sim_id
    viewingSteps.value   = []
    viewingTotal.value   = params.num_steps
    displayIdx.value     = -1
    agentsMeta.value     = {}
    selectedId.value     = null
    clearMarkers()
    Object.keys(lastPos).forEach(k => delete lastPos[k])

    // Poll agent metadata until ready
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 500))
      try {
        const meta = await getSimulation(sim_id)
        if (meta.agents?.length) {
          meta.agents.forEach(a => { agentsMeta.value[a.id] = a })
          break
        }
      } catch { /* keep trying */ }
    }

    await loadHistory()
    connectSSE(sim_id)
    startPoll()
  } catch (err) {
    simRunning.value = false
    console.error('Failed to start simulation:', err)
  }
}

// ── Load historical simulation onto map ───────────────────────
async function loadSimulation(simId) {
  if (simId === viewingSimId.value) return
  viewingSimId.value   = simId
  viewingSteps.value   = []
  displayIdx.value     = -1
  selectedId.value     = null
  clearMarkers()
  Object.keys(lastPos).forEach(k => delete lastPos[k])

  try {
    const [meta, steps] = await Promise.all([
      getSimulation(simId),
      getSimulationSteps(simId),
    ])
    agentsMeta.value = {}
    ;(meta.agents || []).forEach(a => { agentsMeta.value[a.id] = a })
    viewingTotal.value = meta.total_steps || 0

    viewingSteps.value = steps.sort((a, b) => a.step - b.step)
    if (viewingSteps.value.length) {
      displayIdx.value = viewingSteps.value.length - 1
      updateMarkers(viewingSteps.value[displayIdx.value])
    }

    // If this sim is still running, connect SSE and start following
    if (meta.status === 'running') {
      activeSimId.value = simId
      simRunning.value  = true
      autoFollow.value  = true
      connectSSE(simId)
      startPoll()
    }
  } catch (err) {
    console.error('Failed to load simulation:', err)
  }
}

// ── History polling ───────────────────────────────────────────
async function loadHistory() {
  try {
    const list = await getSimulations()
    // Sort newest first
    historyList.value = list.sort((a, b) =>
      new Date(b.start_time || 0) - new Date(a.start_time || 0)
    )
  } catch { /* ignore */ }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(() => {
    if (simRunning.value) loadHistory()
    else stopPoll()
  }, 3000)
}
function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// ── Lifecycle ─────────────────────────────────────────────────
onMounted(async () => {
  initMap()
  await loadHistory()
  // Auto-load the latest simulation if any
  if (historyList.value.length) {
    await loadSimulation(historyList.value[0].sim_id)
  }
})

onBeforeUnmount(() => {
  if (sseConn) { sseConn.close(); sseConn = null }
  stopPoll()
  if (mapInstance) { mapInstance.remove(); mapInstance = null }
  markers = {}
})
</script>

<style scoped>
/* ── Layout ── */
.sim-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}
.workspace {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Left Sidebar ── */
.sim-sidebar {
  width: 300px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  background: var(--surface);
  overflow: hidden;
}
.sidebar-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 0 8px;
}
.sidebar-scroll::-webkit-scrollbar { width: 4px; }
.sidebar-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

/* cfg-section mirrors SetupView */
.cfg-section { padding: 20px 20px 0; margin-bottom: 4px; }
.cfg-section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .8px; color: var(--text-muted); margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}
.cfg-section-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }

/* Form controls — identical to SetupView */
.form-row { margin-bottom: 12px; }
.form-label {
  display: block; font-size: 12px; font-weight: 500;
  color: var(--text-dim); margin-bottom: 5px;
}
.form-control {
  width: 100%; background: var(--bg); border: 1px solid var(--border);
  border-radius: 7px; padding: 8px 11px; font-size: 13px;
  color: var(--text); outline: none; font-family: inherit;
  transition: border-color .15s; box-sizing: border-box;
}
.form-control:focus { border-color: var(--purple); }
select.form-control { cursor: pointer; }

input[type=range] {
  -webkit-appearance: none; appearance: none;
  width: 100%; height: 4px; border-radius: 2px; outline: none; cursor: pointer;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 16px; height: 16px; border-radius: 50%;
  background: var(--purple); box-shadow: 0 0 0 3px rgba(124,58,237,.15);
}
.slider-ticks {
  display: flex; justify-content: space-between; margin-top: 4px;
  font-size: 11px; color: var(--text-muted);
}
.tick-val { font-size: 12px; font-weight: 600; color: var(--purple); }

/* Start Simulation button */
.btn-start-sim {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  background: var(--grad); color: #fff; font-weight: 600; font-size: 13px;
  padding: 11px; border-radius: 10px; border: none; cursor: pointer;
  width: 100%; margin-top: 14px; transition: opacity .2s, transform .15s, box-shadow .2s;
  font-family: inherit;
}
.btn-start-sim:hover:not(:disabled) {
  opacity: .9; transform: translateY(-1px); box-shadow: 0 6px 24px rgba(124,58,237,.3);
}
.btn-start-sim:disabled { background: var(--border); color: var(--text-muted); cursor: not-allowed; }

.spinner-sm {
  display: inline-block; width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,.35); border-top-color: #fff;
  border-radius: 50%; animation: spin .7s linear infinite; flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* History list */
.history-list { display: flex; flex-direction: column; gap: 6px; padding-bottom: 8px; }

.history-card {
  padding: 10px 12px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--border); background: var(--bg);
  transition: border-color .15s, background .15s;
}
.history-card:hover   { border-color: rgba(124,58,237,.3); background: rgba(124,58,237,.03); }
.history-card.active  { border-color: var(--purple); background: rgba(124,58,237,.06); }

.hcard-row {
  display: flex; align-items: center;
  justify-content: space-between; margin-bottom: 4px;
}
.hcard-title { font-size: 12px; font-weight: 600; color: var(--text); }
.hcard-meta  { font-size: 11px; color: var(--text-muted); }

.hcard-badge {
  font-size: 10px; font-weight: 600; padding: 2px 7px;
  border-radius: 10px; flex-shrink: 0;
  background: rgba(148,163,184,.15); color: var(--text-muted);
}
.hcard-badge.completed { background: rgba(34,197,94,.12);  color: #16a34a; }
.hcard-badge.running   { background: rgba(59,130,246,.12); color: #3b82f6; }
.hcard-badge.error     { background: rgba(239,68,68,.12);  color: #dc2626; }
.hcard-badge.initializing { background: rgba(245,158,11,.12); color: #d97706; }

.empty-history {
  font-size: 12px; color: var(--text-muted);
  padding: 12px 0; text-align: center;
}

/* Next Step wrapper */
.next-step-wrap {
  padding: 14px 20px 18px;
  border-top: 1px solid var(--border);
  background: var(--surface);
  flex-shrink: 0;
}
.btn-next-step {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  width: 100%; padding: 13px; border-radius: 10px; border: none;
  font-size: 14px; font-weight: 600; cursor: pointer;
  font-family: inherit; transition: opacity .2s, transform .15s, box-shadow .2s;
  background: var(--grad); color: #fff;
}
.btn-next-step:disabled {
  background: var(--border); color: var(--text-muted); cursor: not-allowed;
}
.btn-next-step:not(:disabled):hover {
  opacity: .9; transform: translateY(-1px); box-shadow: 0 6px 24px rgba(167,139,250,.3);
}
.next-step-hint {
  font-size: 11px; color: var(--text-muted); text-align: center;
  margin-top: 7px; line-height: 1.5;
}

/* ── Map Area ── */
.map-area {
  flex: 1; position: relative; overflow: hidden;
  background: #e8e8e8; min-width: 0;
}
.map-canvas { position: absolute; inset: 0; }

/* Empty state */
.map-empty {
  position: absolute; inset: 0; z-index: 5;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 12px;
  background: var(--bg); pointer-events: none;
}
.map-empty-icon  { font-size: 48px; opacity: .3; }
.map-empty-title { font-size: 16px; font-weight: 600; color: var(--text-dim); }
.map-empty-sub   { font-size: 13px; color: var(--text-muted); }

/* Step overlay */
.step-overlay {
  position: absolute; top: 14px; left: 14px; z-index: 400;
  background: rgba(255,255,255,.93); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 14px;
  display: flex; flex-direction: column; gap: 4px;
  box-shadow: 0 2px 12px rgba(0,0,0,.08);
}
.ov-row   { display: flex; gap: 10px; align-items: center; }
.ov-label { font-size: 10px; color: var(--text-muted); width: 30px; font-weight: 500; }
.ov-val   { font-size: 12px; color: var(--text); font-weight: 600; }

/* Agent chips (top-right) */
.agent-chips {
  position: absolute; top: 14px; right: 14px; z-index: 400;
  display: flex; flex-direction: column; gap: 4px;
  max-height: calc(100% - 80px); overflow-y: auto;
}
.agent-chip {
  display: flex; align-items: center; gap: 7px;
  background: rgba(255,255,255,.92); border: 1px solid var(--border);
  border-radius: 20px; padding: 4px 10px 4px 6px;
  cursor: pointer; font-size: 11px; font-weight: 500;
  color: var(--text-dim); transition: border-color .12s, background .12s;
  box-shadow: 0 1px 4px rgba(0,0,0,.06);
}
.agent-chip:hover   { border-color: rgba(124,58,237,.3); background: rgba(255,255,255,.98); }
.agent-chip.selected { border-color: #E91E63; color: var(--text); }
.chip-dot  { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.chip-name { white-space: nowrap; }

/* Replay bar */
.replay-bar {
  position: absolute; bottom: 0; left: 0; right: 0; z-index: 400;
  height: 48px; background: rgba(255,255,255,.95);
  border-top: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
  padding: 0 16px; backdrop-filter: blur(8px);
}
.replay-btn {
  width: 28px; height: 28px; border: 1px solid var(--border);
  background: var(--surface); border-radius: 6px;
  font-size: 11px; cursor: pointer; color: var(--text-dim);
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; transition: background .12s;
}
.replay-btn:hover:not(:disabled) { background: var(--border); }
.replay-btn:disabled { opacity: .4; cursor: not-allowed; }
.replay-slider { flex: 1; accent-color: var(--purple); cursor: pointer; }
.replay-count  {
  font-size: 12px; color: var(--text-dim);
  white-space: nowrap; min-width: 48px; text-align: right;
}
.auto-label {
  display: flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--text-dim); cursor: pointer; white-space: nowrap;
}
.auto-label input { accent-color: var(--purple); cursor: pointer; }

/* ── Agent Drawer ── */
.agent-drawer {
  position: absolute; top: 0; right: 0; bottom: 48px;
  width: 300px; background: var(--surface);
  border-left: 1px solid var(--border); z-index: 401;
  display: flex; flex-direction: column;
  transform: translateX(100%); transition: transform .25s ease;
  box-shadow: -4px 0 20px rgba(0,0,0,.07);
}
.agent-drawer.open { transform: translateX(0); }

.drawer-header {
  display: flex; align-items: center; gap: 12px;
  padding: 16px; border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.drawer-avatar {
  width: 42px; height: 42px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; overflow: hidden;
}
.drawer-titles { min-width: 0; flex: 1; }
.drawer-name { font-size: 14px; font-weight: 600; color: var(--text); }
.drawer-meta { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.drawer-close {
  margin-left: auto; background: none; border: none;
  font-size: 20px; color: var(--text-muted); cursor: pointer;
  line-height: 1; padding: 2px 6px; border-radius: 4px; flex-shrink: 0;
}
.drawer-close:hover { color: var(--text); }

.drawer-body { flex: 1; overflow-y: auto; padding: 14px 16px 20px; }

.drawer-section-title {
  font-size: 10px; font-weight: 700; letter-spacing: 1px;
  text-transform: uppercase; color: var(--purple);
  border-bottom: 1px solid var(--border);
  padding-bottom: 6px; margin: 16px 0 12px;
}
.drawer-section-title:first-child { margin-top: 0; }

/* Needs */
.needs-list { display: flex; flex-direction: column; gap: 8px; }
.need-row   { display: flex; align-items: center; gap: 8px; }
.need-lbl   { font-size: 11px; color: var(--text-dim); width: 46px; flex-shrink: 0; }
.need-track { flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.need-fill  { height: 100%; border-radius: 3px; transition: width .3s ease; }
.need-pct   { font-size: 11px; font-weight: 600; width: 34px; text-align: right; flex-shrink: 0; }

/* Intention */
.intention-card {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px;
}
.intention-main { font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
.intention-sub  { font-size: 11px; color: var(--text-dim); line-height: 1.6; margin-top: 4px; }
.sub-label { font-weight: 600; color: var(--text-muted); margin-right: 5px; }

/* Event history */
.event-list  { display: flex; flex-direction: column; }
.event-item  { padding: 10px 0; border-bottom: 1px solid var(--border); }
.event-item:last-child { border-bottom: none; }
.event-meta  { font-size: 10px; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .5px; }
.event-intention { font-size: 12px; color: var(--text); font-weight: 500; margin-bottom: 3px; }
.event-result    { font-size: 11px; color: var(--text-dim); line-height: 1.5; }
.event-empty     { font-size: 12px; color: var(--text-muted); padding: 10px 0; }

/* ── Agent chip message badges ── */
.chip-msg-badge {
  font-size: 9px; font-weight: 700; padding: 1px 4px;
  border-radius: 8px; line-height: 1.4; flex-shrink: 0;
}
.chip-msg-badge.sent { background: rgba(124,58,237,.15); color: var(--purple); }
.chip-msg-badge.recv { background: rgba(34,197,94,.15);  color: #16a34a; }

/* ── Drawer: Messages section ── */
.msg-list { display: flex; flex-direction: column; gap: 6px; }
.msg-row {
  display: flex; flex-direction: column;
  gap: 4px; padding: 7px 10px; border-radius: 8px;
  background: var(--bg); border: 1px solid var(--border);
  font-size: 12px; line-height: 1.5;
}
.sent-row { border-left: 3px solid var(--purple); }
.recv-row { border-left: 3px solid #16a34a; }
.msg-header { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.msg-dir  { font-size: 11px; font-weight: 700; flex-shrink: 0; white-space: nowrap; }
.sent-dir { color: var(--purple); }
.recv-dir { color: #16a34a; }
.msg-sender { font-weight: 600; color: var(--text-dim); flex-shrink: 0; }
.msg-content { color: var(--text); word-break: break-all; }

/* target badge (shared by drawer + panel) */
.msg-target-badge {
  font-size: 10px; font-weight: 600; padding: 1px 6px;
  border-radius: 8px; flex-shrink: 0; white-space: nowrap;
}
.badge-nearby  { background: rgba(245,158,11,.15); color: #d97706; }
.badge-all     { background: rgba(59,130,246,.15);  color: #3b82f6; }
.badge-private { background: rgba(124,58,237,.12);  color: var(--purple); }

/* ── Event history badges ── */
.ev-badge {
  font-size: 9px; font-weight: 700; padding: 1px 4px;
  border-radius: 6px; margin-left: 4px;
}
.ev-sent { background: rgba(124,58,237,.12); color: var(--purple); }
.ev-recv { background: rgba(34,197,94,.12);  color: #16a34a; }

/* ── Message Panel ── */
.msg-panel {
  width: 280px; flex-shrink: 0;
  border-left: 1px solid var(--border);
  background: var(--surface);
  display: flex; flex-direction: row;
  overflow: hidden; transition: width .2s ease;
}
.msg-panel.collapsed { width: 32px; }

/* 展开后右侧内容区（纵向排列）*/
.msg-panel-inner {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  overflow: hidden;
}

/* 折叠 tab（左侧竖条，始终可见）*/
.msg-panel-tab {
  width: 32px; flex-shrink: 0;
  display: flex; flex-direction: column; align-items: center;
  justify-content: flex-start; padding-top: 16px; gap: 8px;
  cursor: pointer; border-right: 1px solid var(--border);
  background: var(--surface);
  transition: background .12s;
}
.msg-panel-tab:hover { background: rgba(124,58,237,.04); }
.tab-icon   { font-size: 14px; }
.tab-count-vert {
  font-size: 10px; font-weight: 700; color: var(--purple);
  writing-mode: vertical-rl; letter-spacing: 1px;
}

/* 面板主体（展开时可见）*/
.msg-panel-header {
  display: flex; align-items: center; gap: 8px;
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.msg-panel-title { font-size: 12px; font-weight: 700; color: var(--text); }
.msg-panel-step  { font-size: 11px; color: var(--text-muted); }
.msg-panel-count {
  margin-left: auto; font-size: 11px; font-weight: 700;
  background: rgba(124,58,237,.12); color: var(--purple);
  padding: 1px 7px; border-radius: 10px;
}

.msg-panel-body {
  flex: 1; overflow-y: auto; padding: 10px 12px 16px;
  display: flex; flex-direction: column; gap: 8px;
}
.msg-panel-body::-webkit-scrollbar { width: 3px; }
.msg-panel-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

/* 空状态 */
.msg-panel-empty {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 8px;
  color: var(--text-muted); padding: 20px 0;
}
.mp-empty-ico { font-size: 28px; opacity: .4; }
.mp-empty-txt { font-size: 12px; }

/* 消息卡片 */
.mp-card {
  border-left: 3px solid var(--border);
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px;
  display: flex; flex-direction: column; gap: 5px;
}
.mp-card-header { display: flex; align-items: center; gap: 6px; }
.mp-sender { font-size: 12px; font-weight: 600; }
.mp-content { font-size: 12px; color: var(--text); line-height: 1.5; word-break: break-all; }

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

/* ── Step indicator (mirrors SetupView) ── */
.step-indicator { display: flex; align-items: center; gap: 6px; }
.step-pip {
  display: flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  font-size: 11px; font-weight: 600;
  background: var(--border); color: var(--text-muted);
}
.step-pip.active { background: var(--grad); color: #fff; }
.step-pip.done   {
  background: rgba(34,197,94,.15); color: #16a34a;
  border: 1.5px solid #16a34a;
}
.step-line { width: 24px; height: 1px; background: var(--border); }
.step-line.done { background: #16a34a; }
.step-label { font-size: 11px; color: var(--text-dim); margin-left: 6px; }
</style>

<style>
.agent-tooltip {
  background: white !important;
  border: 1px solid #e4e7ef !important;
  border-radius: 8px !important;
  box-shadow: 0 4px 16px rgba(0,0,0,.10) !important;
  padding: 7px 11px !important;
  font-size: 12px !important;
  font-family: Inter, system-ui, sans-serif !important;
  color: #1e293b !important;
}
.agent-tooltip::before { display: none !important; }
</style>
