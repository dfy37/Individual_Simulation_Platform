<template>
  <div class="setup-layout">
    <NavBar>
      <template #right>
        <div class="step-indicator">
          <div class="step-pip active">1</div>
          <div class="step-line"></div>
          <div class="step-pip">2</div>
          <div class="step-line"></div>
          <div class="step-pip">3</div>
          <span class="step-label">Configure</span>
        </div>
      </template>
    </NavBar>

    <div class="workspace">
      <!-- Left: relationship graph -->
      <GraphPanel
        :agents="selectedAgents"
        :relationships="relationships"
        :loading="loading"
      />

      <!-- Right: config panel -->
      <div class="config-pane">
        <div class="config-scroll">

          <!-- ── Research Sampling ── -->
          <div class="cfg-section">
            <div class="cfg-section-title">Research Sampling</div>

            <!-- NL input -->
            <div class="nl-input-wrap">
              <textarea
                v-model="samplingQuery"
                class="nl-textarea"
                placeholder="描述你想要的样本群体，例如：帮我抽 20 个复旦学生，男女均衡，本科为主，计算机和数学专业多一些，社交媒体活跃一点"
                rows="3"
              />
              <div class="nl-row">
                <div class="nl-size-wrap">
                  <label class="nl-size-label">Target Size</label>
                  <input type="number" class="nl-size-input" v-model.number="targetSize" min="2" max="37" />
                </div>
                <button class="btn-sample" :disabled="samplingLoading || !samplingQuery.trim()" @click="generateSample">
                  <span v-if="samplingLoading" class="spinner-sm"></span>
                  <span v-else>Generate Sample</span>
                </button>
              </div>
            </div>

            <!-- Sampling results (only shown after first sample) -->
            <template v-if="samplingPreview">

              <!-- Parsed constraints -->
              <div class="preview-block">
                <div class="preview-block-title">
                  Parsed Constraints
                  <span class="preview-badge badge-ok" v-if="diagnosticsOverall === 'good'">Good fit</span>
                  <span class="preview-badge badge-warn" v-else-if="diagnosticsOverall === 'fair'">Fair fit</span>
                  <span class="preview-badge badge-err" v-else-if="diagnosticsOverall === 'poor'">Poor fit</span>
                </div>
                <div class="rationale-text">{{ samplingSpec?.rationale }}</div>

                <!-- Marginals comparison -->
                <div v-for="(diag, feat) in filteredDiagnostics" :key="feat" class="diag-feat">
                  <div class="diag-feat-name">{{ FEAT_LABELS[feat] || feat }}</div>
                  <div class="diag-bars">
                    <div v-for="(tgt, bucket) in diag.target" :key="bucket" class="diag-bar-row">
                      <span class="diag-bucket">{{ bucket }}</span>
                      <div class="diag-bar-track">
                        <div class="diag-bar-target" :style="`width:${(tgt*100).toFixed(0)}%`"></div>
                        <div class="diag-bar-actual"
                             :style="`width:${((diag.actual[bucket]||0)*100).toFixed(0)}%;opacity:0.6`"></div>
                      </div>
                      <span class="diag-pct">{{ (tgt*100).toFixed(0) }}% → {{ ((diag.actual[bucket]||0)*100).toFixed(0) }}%</span>
                    </div>
                  </div>
                </div>

                <!-- Soft preferences -->
                <div v-if="samplingSpec?.soft_preferences?.length" class="soft-prefs">
                  <span class="soft-prefs-label">Soft preferences</span>
                  <span v-for="p in samplingSpec.soft_preferences" :key="p" class="soft-tag">{{ p }}</span>
                </div>
              </div>

              <!-- Summary -->
              <div class="preview-block">
                <div class="preview-block-title">
                  Sample Overview
                  <span class="summary-count">{{ samplingPreview.summary?.count }} agents from {{ samplingPreview.candidate_count }} candidates</span>
                </div>
                <div class="dist-grid">
                  <div class="dist-item" v-for="(dist, label) in summaryDistItems" :key="label">
                    <div class="dist-item-label">{{ label }}</div>
                    <div v-for="(pct, val) in dist" :key="val" class="dist-mini-row">
                      <span class="dist-mini-val">{{ val }}</span>
                      <div class="dist-mini-track">
                        <div class="dist-mini-fill" :style="`width:${(pct*100).toFixed(0)}%`"></div>
                      </div>
                      <span class="dist-mini-pct">{{ (pct*100).toFixed(0) }}%</span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Resample button -->
              <button class="btn-resample" @click="generateSample" :disabled="samplingLoading">
                <span v-if="samplingLoading" class="spinner-sm"></span>
                <span v-else>↻ Resample</span>
              </button>
            </template>

            <!-- Fallback: manual slider (shown when no NL sample yet) -->
            <template v-else>
              <div class="agent-slider-wrap">
                <div class="slider-header">
                  <div>
                    <div class="slider-label">Number of Agents</div>
                    <div class="slider-sub">Selected from {{ allProfiles.length }} profiles</div>
                  </div>
                  <div class="slider-val">{{ agentCount }}</div>
                </div>
                <input type="range" min="2" :max="Math.min(allProfiles.length || 37, 37)" step="1"
                       v-model.number="agentCount" :style="sliderStyle(agentCount, 2, Math.min(allProfiles.length || 37, 37))">
              </div>
            </template>
          </div>

          <!-- ── Network Stats ── -->
          <div class="cfg-section">
            <div class="cfg-section-title">Network Stats</div>
            <div class="stats-row">
              <div class="stat-box"><div class="stat-box-val">{{ selectedAgents.length }}</div><div class="stat-box-label">Agents</div></div>
              <div class="stat-box"><div class="stat-box-val">{{ activeEdgeCount }}</div><div class="stat-box-label">Connections</div></div>
              <div class="stat-box"><div class="stat-box-val">{{ avgDegree }}</div><div class="stat-box-label">Avg Degree</div></div>
            </div>
          </div>

          <!-- ── Distribution (when NL sampling active) ── -->
          <div class="cfg-section" v-if="samplingPreview">
            <div class="cfg-section-title">Agent Selection</div>
            <div class="dist-grid">
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.male }}</div>
                <div class="dist-item-label">Male</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#3b82f6;width:${dist.male / selectedAgents.length * 100}%`"></div></div>
              </div>
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.female }}</div>
                <div class="dist-item-label">Female</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#ec4899;width:${dist.female / selectedAgents.length * 100}%`"></div></div>
              </div>
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.ugrad }}</div>
                <div class="dist-item-label">Undergrad</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#7c3aed;width:${dist.ugrad / selectedAgents.length * 100}%`"></div></div>
              </div>
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.grad }}</div>
                <div class="dist-item-label">Grad / PhD</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#2563eb;width:${dist.grad / selectedAgents.length * 100}%`"></div></div>
              </div>
            </div>
          </div>
          <div class="cfg-section" v-else>
            <div class="cfg-section-title">Agent Selection</div>
            <div class="dist-grid">
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.male }}</div>
                <div class="dist-item-label">Male</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#3b82f6;width:${dist.male / agentCount * 100}%`"></div></div>
              </div>
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.female }}</div>
                <div class="dist-item-label">Female</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#ec4899;width:${dist.female / agentCount * 100}%`"></div></div>
              </div>
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.ugrad }}</div>
                <div class="dist-item-label">Undergrad</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#7c3aed;width:${dist.ugrad / agentCount * 100}%`"></div></div>
              </div>
              <div class="dist-item">
                <div class="dist-item-val">{{ dist.grad }}</div>
                <div class="dist-item-label">Grad / PhD</div>
                <div class="dist-bar-wrap"><div class="dist-bar-fill" :style="`background:#2563eb;width:${dist.grad / agentCount * 100}%`"></div></div>
              </div>
            </div>
          </div>

          <!-- ── System Log ── -->
          <div class="cfg-section">
            <div class="cfg-section-title">System Log</div>
            <div class="log-box" ref="logBox">
              <div v-for="(entry, i) in logs" :key="i" v-html="entry"></div>
            </div>
          </div>

        </div>

        <div class="config-footer">
          <button class="btn-start-sim" :disabled="loading || !selectedAgents.length" @click="nextStep">
            Next Step
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 8h10M9 4l4 4-4 4"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import NavBar from '../components/NavBar.vue'
import GraphPanel from '../components/GraphPanel.vue'
import { getProfiles, getRelationships, sampleProfilesPreview } from '../api/index.js'

const router = useRouter()

// ── Feature label map ────────────────────────────────────────
const FEAT_LABELS = {
  gender:         'Gender',
  age_bucket:     'Age',
  education:      'Education',
  major_bucket:   'Major',
  activity:       'Activity',
  interest_bucket: 'Interests',
}

// ── Data ────────────────────────────────────────────────────
const loading       = ref(true)
const allProfiles   = ref([])
const relationships = ref([])
const logs          = ref([])
const logBox        = ref(null)

// ── Manual fallback slider ───────────────────────────────────
const agentCount = ref(10)

// ── NL Sampling state ────────────────────────────────────────
const samplingQuery    = ref('')
const targetSize       = ref(20)
const samplingLoading  = ref(false)
const samplingPreview  = ref(null)   // full response from /api/profiles/sample-preview
const samplingSpec     = ref(null)   // parsed sampling_spec
const samplingProfiles = ref([])     // selected profiles from NL sampling

// ── Selected agents (NL sampling or fallback slider) ─────────
const selectedAgents = computed(() => {
  if (samplingProfiles.value.length > 0) {
    return samplingProfiles.value
  }
  // Fallback: deterministic spread
  const sorted = [...allProfiles.value].sort((a, b) => a.user_id.localeCompare(b.user_id))
  const n = agentCount.value
  if (!sorted.length || !n) return []
  const step = sorted.length / n
  return Array.from({ length: n }, (_, i) =>
    sorted[Math.min(Math.floor(i * step), sorted.length - 1)]
  )
})

// ── Distribution stats ────────────────────────────────────────
const dist = computed(() => {
  const a = selectedAgents.value
  const n = a.length || 1
  const male   = a.filter(x => x.gender === 'male').length
  const female = a.filter(x => x.gender === 'female').length
  const occ    = (p) => { const o = (p.occupation||'').toLowerCase(); return o.includes('博士') || o.includes('研究生') || o.includes('硕士') }
  const grad   = a.filter(occ).length
  return { male, female, ugrad: n - grad, grad }
})

// ── Diagnostics ───────────────────────────────────────────────
const diagnosticsOverall = computed(() => samplingPreview.value?.diagnostics?._overall ?? null)
const filteredDiagnostics = computed(() => {
  const d = samplingPreview.value?.diagnostics
  if (!d) return {}
  return Object.fromEntries(Object.entries(d).filter(([k]) => k !== '_overall'))
})

// ── Summary distribution for display ─────────────────────────
const summaryDistItems = computed(() => {
  const s = samplingPreview.value?.summary
  if (!s) return {}
  return {
    Gender:    s.gender,
    Education: s.education,
    Activity:  s.activity,
  }
})

// ── Network stats ─────────────────────────────────────────────
const activeEdgeCount = computed(() => {
  const ids = new Set(selectedAgents.value.map(a => a.user_id))
  return relationships.value.filter(r => ids.has(r.agent1) && ids.has(r.agent2)).length
})

const avgDegree = computed(() => {
  const n = selectedAgents.value.length
  return n > 0 ? (activeEdgeCount.value * 2 / n).toFixed(1) : '—'
})

// ── Slider style helper ───────────────────────────────────────
function sliderStyle(val, min, max) {
  const pct = ((val - min) / (max - min) * 100).toFixed(1) + '%'
  return `background: linear-gradient(to right, var(--purple) ${pct}, var(--border) ${pct})`
}

// ── Logging ──────────────────────────────────────────────────
function log(html) {
  const t = new Date().toTimeString().slice(0, 8)
  logs.value.push(`<span class="log-time">[${t}]</span> ${html}`)
  nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
}

// ── Generate Sample (NL → IPF) ────────────────────────────────
async function generateSample() {
  if (!samplingQuery.value.trim()) return
  samplingLoading.value = true
  log(`Sampling: "<span class="log-info">${samplingQuery.value.slice(0, 40)}…</span>"`)
  try {
    const result = await sampleProfilesPreview({
      query:       samplingQuery.value,
      target_size: targetSize.value,
    })
    if (result.error) {
      log(`<span class="log-err">Sampling error: ${result.error}</span>`)
      return
    }
    samplingPreview.value  = result
    samplingSpec.value     = result.sampling_spec
    samplingProfiles.value = result.selected_profiles
    log(`<span class="log-ok">✓</span> Sampled <span class="log-info">${result.selected_profiles.length}</span> agents (from ${result.candidate_count} candidates)`)
    log(`Fit: <span class="log-info">${result.diagnostics?._overall ?? '—'}</span> — ${result.sampling_spec?.rationale?.slice(0, 60) ?? ''}`)
  } catch (err) {
    log(`<span class="log-err">Error: ${err.message}</span>`)
  } finally {
    samplingLoading.value = false
  }
}

// ── Next Step ─────────────────────────────────────────────────
function nextStep() {
  const agents = selectedAgents.value
  localStorage.setItem('agentParams', JSON.stringify({
    num_agents:     agents.length,
    agent_ids:      agents.map(a => a.user_id),
    sampling_query: samplingQuery.value || null,
    sampling_spec:  samplingSpec.value  || null,
  }))
  log(`Selected <span class="log-info">${agents.length}</span> agents — proceeding to simulation`)
  setTimeout(() => router.push('/simulation'), 300)
}

// ── Log when manual agent count changes ──────────────────────
watch(agentCount, (v) => {
  if (!samplingProfiles.value.length) {
    log(`Selected <span class="log-info">${v}</span> agents — rebuilding network…`)
  }
})

// ── Fetch data ────────────────────────────────────────────────
onMounted(async () => {
  log('Connecting to backend…')
  try {
    const [profiles, relData] = await Promise.all([getProfiles(), getRelationships()])
    allProfiles.value   = profiles
    relationships.value = relData.relationships || []
    log(`<span class="log-ok">✓</span> <span class="log-info">${profiles.length}</span> profiles loaded`)
    log(`<span class="log-ok">✓</span> <span class="log-info">${relationships.value.length}</span> relationships loaded`)
    log('Network ready — enter a query above or use the slider')
  } catch (err) {
    log(`<span class="log-err">Error: ${err.message} — start the Flask server first</span>`)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.setup-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

/* Step indicator */
.step-indicator { display: flex; align-items: center; gap: 6px; }
.step-pip {
  display: flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  font-size: 11px; font-weight: 600;
  background: var(--border); color: var(--text-muted);
}
.step-pip.active { background: var(--grad); color: #fff; }
.step-line { width: 24px; height: 1px; background: var(--border); }
.step-label { font-size: 11px; color: var(--text-dim); margin-left: 6px; }

/* Workspace */
.workspace { display: flex; flex: 1; overflow: hidden; }

/* Config pane */
.config-pane {
  width: 360px; flex-shrink: 0;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--surface); border-left: 1px solid var(--border);
}
.config-scroll { flex: 1; overflow-y: auto; padding: 20px 20px 0; }
.config-scroll::-webkit-scrollbar { width: 4px; }
.config-scroll::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: 2px; }

.cfg-section { margin-bottom: 24px; }
.cfg-section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .8px; color: var(--text-muted); margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}
.cfg-section-title::after { content: ''; flex: 1; height: 1px; background: var(--border); }

/* NL Input */
.nl-input-wrap {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px; margin-bottom: 12px;
}
.nl-textarea {
  width: 100%; background: transparent; border: none; outline: none;
  font-size: 12.5px; color: var(--text); resize: none; line-height: 1.6;
  font-family: inherit;
}
.nl-textarea::placeholder { color: var(--text-muted); }
.nl-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.nl-size-wrap { display: flex; align-items: center; gap: 6px; }
.nl-size-label { font-size: 11px; color: var(--text-dim); white-space: nowrap; }
.nl-size-input {
  width: 52px; background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 5px 7px; font-size: 13px; font-weight: 600;
  color: var(--text); text-align: center; outline: none;
}
.btn-sample {
  flex: 1; background: var(--grad); color: #fff; border: none; border-radius: 8px;
  padding: 8px 12px; font-size: 12px; font-weight: 600; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 6px;
  font-family: inherit; transition: opacity .15s;
}
.btn-sample:disabled { opacity: .45; cursor: not-allowed; }

/* Preview blocks */
.preview-block {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px; margin-bottom: 10px;
}
.preview-block-title {
  font-size: 11px; font-weight: 700; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .6px;
  display: flex; align-items: center; gap: 6px; margin-bottom: 8px;
}
.preview-badge {
  font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 99px;
}
.badge-ok   { background: #dcfce7; color: #166534; }
.badge-warn { background: #fef9c3; color: #854d0e; }
.badge-err  { background: #fee2e2; color: #991b1b; }
.summary-count {
  font-size: 10px; font-weight: 400; color: var(--text-muted);
  text-transform: none; letter-spacing: 0; margin-left: auto;
}

.rationale-text {
  font-size: 12px; color: var(--text-dim); margin-bottom: 10px; line-height: 1.5;
}

/* Diagnostics bars */
.diag-feat { margin-bottom: 8px; }
.diag-feat-name { font-size: 11px; font-weight: 600; color: var(--text-dim); margin-bottom: 4px; }
.diag-bars { display: flex; flex-direction: column; gap: 3px; }
.diag-bar-row { display: flex; align-items: center; gap: 6px; }
.diag-bucket  { font-size: 10px; color: var(--text-muted); width: 70px; flex-shrink: 0; }
.diag-bar-track {
  flex: 1; height: 6px; background: var(--border); border-radius: 3px; position: relative; overflow: hidden;
}
.diag-bar-target {
  position: absolute; top: 0; left: 0; height: 100%;
  background: var(--purple); border-radius: 3px; transition: width .3s;
}
.diag-bar-actual {
  position: absolute; top: 0; left: 0; height: 100%;
  background: #22c55e; border-radius: 3px; transition: width .3s;
}
.diag-pct { font-size: 10px; color: var(--text-muted); white-space: nowrap; width: 70px; text-align: right; }

.soft-prefs { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; align-items: center; }
.soft-prefs-label { font-size: 10px; color: var(--text-muted); }
.soft-tag {
  font-size: 10px; background: #ede9fe; color: #4c1d95;
  border-radius: 99px; padding: 2px 8px;
}

/* Summary dist */
.dist-mini-row { display: flex; align-items: center; gap: 5px; margin-bottom: 2px; }
.dist-mini-val { font-size: 10px; color: var(--text-muted); width: 52px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dist-mini-track { flex: 1; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
.dist-mini-fill  { height: 100%; background: var(--purple); border-radius: 2px; transition: width .3s; }
.dist-mini-pct   { font-size: 10px; color: var(--text-muted); width: 28px; text-align: right; }

.btn-resample {
  width: 100%; background: transparent; border: 1px solid var(--border);
  border-radius: 8px; padding: 8px; font-size: 12px; font-weight: 600;
  color: var(--text-dim); cursor: pointer; margin-bottom: 4px;
  display: flex; align-items: center; justify-content: center; gap: 6px;
  font-family: inherit; transition: border-color .15s, color .15s;
}
.btn-resample:hover { border-color: var(--purple); color: var(--purple); }
.btn-resample:disabled { opacity: .4; cursor: not-allowed; }

/* Manual slider (fallback) */
.agent-slider-wrap {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px; margin-bottom: 12px;
}
.slider-header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.slider-label { font-size: 13px; font-weight: 500; }
.slider-sub   { font-size: 11px; color: var(--text-muted); }
.slider-val {
  font-size: 26px; font-weight: 800;
  background: var(--grad);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}

input[type=range] {
  -webkit-appearance: none; appearance: none;
  width: 100%; height: 4px; border-radius: 2px; outline: none; cursor: pointer;
}
input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 16px; height: 16px; border-radius: 50%;
  background: var(--purple); box-shadow: 0 0 0 3px rgba(124,58,237,.15);
}

.dist-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 4px; }
.dist-item { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.dist-item-val   { font-size: 18px; font-weight: 700; color: var(--text); }
.dist-item-label { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.dist-bar-wrap   { height: 3px; background: var(--border); border-radius: 2px; margin-top: 8px; }
.dist-bar-fill   { height: 100%; border-radius: 2px; transition: width .3s; }

.stats-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; margin-bottom: 16px; }
.stat-box { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center; }
.stat-box-val   { font-size: 18px; font-weight: 700; color: var(--text); }
.stat-box-label { font-size: 10px; color: var(--text-muted); margin-top: 2px; font-weight: 500; }

.log-box {
  background: #f1f3f8; border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px;
  font-family: 'SF Mono','Fira Code',monospace; font-size: 11px;
  color: var(--text-dim); line-height: 1.9; max-height: 100px; overflow-y: auto;
}

.config-footer { padding: 16px 20px; border-top: 1px solid var(--border); flex-shrink: 0; }
.btn-start-sim {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  background: var(--grad); color: #fff; font-weight: 600; font-size: 14px;
  padding: 13px; border-radius: 10px; border: none; cursor: pointer;
  width: 100%; transition: opacity .2s, transform .15s, box-shadow .2s;
  font-family: inherit;
}
.btn-start-sim:hover { opacity: .9; transform: translateY(-1px); box-shadow: 0 6px 24px rgba(124,58,237,.3); }
.btn-start-sim:disabled { opacity: .4; cursor: not-allowed; transform: none; box-shadow: none; }

/* Spinner */
.spinner-sm {
  width: 12px; height: 12px; border: 2px solid rgba(255,255,255,.3);
  border-top-color: #fff; border-radius: 50%;
  animation: spin .6s linear infinite; display: inline-block;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>

<style>
/* Log colors (used in v-html) */
.log-time { color: var(--text-muted); }
.log-ok   { color: #16a34a; }
.log-info { color: var(--purple); }
.log-err  { color: #dc2626; }
</style>
