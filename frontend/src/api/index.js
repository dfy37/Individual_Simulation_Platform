import axios from 'axios'

// Vite dev server proxies /api → http://localhost:5050
const api = axios.create({ baseURL: '' })

export const getProfiles       = ()       => api.get('/api/profiles').then(r => r.data)
export const getRelationships  = ()       => api.get('/api/relationships').then(r => r.data)
export const createSimulation  = (params) => api.post('/api/simulations', params).then(r => r.data)
export const getSimulation     = (id)     => api.get(`/api/simulations/${id}`).then(r => r.data)
export const getSimulations    = ()       => api.get('/api/simulations').then(r => r.data)
export const getSimulationSteps = (id)   => api.get(`/api/simulations/${id}/steps`).then(r => r.data)

// SSE URL (relative, goes through Vite proxy)
export const sseUrl = (id) => `/api/simulations/${id}/stream`

// ── Online Simulation (Step 3) ─────────────────────────────
export const startOnlineSim       = (body) => api.post('/api/online-sim/start', body).then(r => r.data)
export const getOnlineSimPosts    = (id)   => api.get(`/api/online-sim/${id}/posts`).then(r => r.data)
export const getOnlineSimAttitude = (id)   => api.get(`/api/online-sim/${id}/attitude`).then(r => r.data)
export const getOnlineSimStats    = (id)   => api.get(`/api/online-sim/${id}/stats`).then(r => r.data)
export const interpretAttitude    = (id)   => api.post(`/api/online-sim/${id}/attitude/interpret`).then(r => r.data)
export const getOnlineSimHistory  = ()     => api.get('/api/online-sim/history').then(r => r.data)

// ── Interview (Step 4) ─────────────────────────────────────
export const generateQuestionnaire  = (body)               => api.post('/api/interview/generate-questionnaire', body).then(r => r.data)
export const createInterviewSession = (body)               => api.post('/api/interview/sessions', body).then(r => r.data)
export const getInterviewAgents     = (sessionId)          => api.get(`/api/interview/sessions/${sessionId}/agents`).then(r => r.data)
export const getAgentReport         = (sessionId, agentId) => api.get(`/api/interview/sessions/${sessionId}/agents/${agentId}/report`).then(r => r.data)
export const getInterviewSummary    = (sessionId)          => api.get(`/api/interview/sessions/${sessionId}/summary`).then(r => r.data)
export const analyzeInterview       = (sessionId)          => api.post(`/api/interview/sessions/${sessionId}/analyze`).then(r => r.data)
export const interviewStreamUrl     = (sessionId, agentId) => `/api/interview/sessions/${sessionId}/agents/${agentId}/stream`

export default api
