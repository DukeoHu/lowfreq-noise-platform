import type {
  AnalysisConfigInput,
  AnalysisResult,
  AnalysisRun,
  AnalysisStage,
  AudioFile,
  AuditLogEntry,
  DashboardData,
  EventPhoto,
  EventSummary,
  MeasurementDetail,
  MeasurementSummary,
  Insight,
  InsightFeedbackRating,
  InsightVersion,
  LlmConfig,
  LlmConfigInput,
  LlmTestResult,
  LlmUsage,
  NoiseEvent,
  Page,
  SystemStatus,
} from '../types'
import { loadAccess } from '../settings'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

/** 鉴权头：口令与操作人保存在浏览器，由设置页维护。 */
function authHeaders(): Record<string, string> {
  const { token, actor } = loadAccess()
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  // 请求头只允许 ASCII，中文操作人先做百分号编码，服务端会解码
  if (actor) headers['X-Actor'] = encodeURIComponent(actor)
  return headers
}

/** 下载类链接无法带请求头，改为查询参数传递口令。 */
function withToken(url: string): string {
  const { token } = loadAccess()
  if (!token) return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}

export function realtimeWsUrl(): string {
  let base: string
  if (/^https?:\/\//.test(BASE_URL)) {
    const url = new URL(BASE_URL)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    url.pathname = `${url.pathname.replace(/\/$/, '')}/realtime/ws`
    base = url.toString()
  } else {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    base = `${scheme}://${window.location.host}${BASE_URL}/realtime/ws`
  }
  const { token, actor } = loadAccess()
  if (!token && !actor) return base
  const url = new URL(base)
  if (token) url.searchParams.set('token', token)
  if (actor) url.searchParams.set('actor', actor)
  return url.toString()
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = {
    ...authHeaders(),
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  }
  const response = await fetch(`${BASE_URL}${path}`, { ...init, headers })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
    } catch {
      /* 保留状态码信息 */
    }
    throw new Error(detail)
  }
  if (response.status === 204) {
    return undefined as unknown as T
  }
  return (await response.json()) as T
}

export interface EventInput {
  title: string
  description?: string
  location?: string
  measurement_point?: string
  equipment?: string
  notes?: string
  tags?: string[]
  latitude?: number | null
  longitude?: number | null
  status?: string
}

export interface AuditQuery {
  limit?: number
  offset?: number
  action?: string
  since?: string
  until?: string
}

function toQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value))
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

export const api = {
  uploadAudio(file: File): Promise<AudioFile> {
    const form = new FormData()
    form.append('file', file)
    return request<AudioFile>('/audio-files', { method: 'POST', body: form })
  },

  listAudioFiles(limit = 50, offset = 0, filename?: string): Promise<Page<AudioFile>> {
    return request<Page<AudioFile>>(`/audio-files${toQuery({ limit, offset, filename })}`)
  },

  createAnalysis(audioFileId: string, config?: AnalysisConfigInput): Promise<AnalysisRun> {
    return request<AnalysisRun>('/analyses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_file_id: audioFileId, config }),
    })
  },

  getAnalysis(runId: string): Promise<AnalysisRun & { result?: AnalysisResult | null }> {
    return request<AnalysisRun & { result?: AnalysisResult | null }>(`/analyses/${runId}`)
  },

  listAnalyses(audioFileId?: string, limit = 50, offset = 0): Promise<Page<AnalysisRun>> {
    return request<Page<AnalysisRun>>(
      `/analyses${toQuery({ audio_file_id: audioFileId, limit, offset })}`,
    )
  },

  retryAnalysis(runId: string): Promise<AnalysisRun> {
    return request<AnalysisRun>(`/analyses/${runId}/retry`, { method: 'POST' })
  },

  cancelAnalysis(runId: string): Promise<AnalysisRun> {
    return request<AnalysisRun>(`/analyses/${runId}/cancel`, { method: 'POST' })
  },

  getAnalysisInsight(runId: string): Promise<Insight | null> {
    return request<Insight | null>(`/analyses/${runId}/insight`)
  },

  generateAnalysisInsight(runId: string, force = false): Promise<Insight> {
    return request<Insight>(`/analyses/${runId}/insight${force ? '?force=true' : ''}`, {
      method: 'POST',
    })
  },

  cancelAnalysisInsight(runId: string): Promise<Insight> {
    return request<Insight>(`/analyses/${runId}/insight/cancel`, { method: 'POST' })
  },

  getAnalysisInsightHistory(runId: string, limit = 20): Promise<InsightVersion[]> {
    return request<InsightVersion[]>(`/analyses/${runId}/insight/history?limit=${limit}`)
  },

  submitAnalysisInsightFeedback(
    runId: string,
    rating: InsightFeedbackRating,
    note = '',
  ): Promise<Insight> {
    return request<Insight>(`/analyses/${runId}/insight/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, note }),
    })
  },

  bandsCsvUrl(runId: string): string {
    return withToken(`${BASE_URL}/analyses/${runId}/bands.csv`)
  },

  reportPdfUrl(runId: string): string {
    return withToken(`${BASE_URL}/analyses/${runId}/report.pdf`)
  },

  exportZipUrl(runId: string, includeAudio = false): string {
    const suffix = includeAudio ? '?include_audio=true' : ''
    return withToken(`${BASE_URL}/analyses/${runId}/export.zip${suffix}`)
  },

  listEvents(): Promise<NoiseEvent[]> {
    return request<NoiseEvent[]>('/events')
  },

  getEvent(eventId: string): Promise<NoiseEvent> {
    return request<NoiseEvent>(`/events/${eventId}`)
  },

  createEvent(payload: EventInput): Promise<NoiseEvent> {
    return request<NoiseEvent>('/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  updateEvent(eventId: string, payload: Partial<EventInput>): Promise<NoiseEvent> {
    return request<NoiseEvent>(`/events/${eventId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  deleteEvent(eventId: string): Promise<void> {
    return request<void>(`/events/${eventId}`, { method: 'DELETE' })
  },

  getSystemStatus(): Promise<SystemStatus> {
    return request<SystemStatus>('/system/status')
  },

  getLlmConfig(): Promise<LlmConfig> {
    return request<LlmConfig>('/system/llm')
  },

  saveLlmConfig(payload: LlmConfigInput): Promise<LlmConfig> {
    return request<LlmConfig>('/system/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  testLlm(): Promise<LlmTestResult> {
    return request<LlmTestResult>('/system/llm/test', { method: 'POST' })
  },

  getLlmUsage(): Promise<LlmUsage> {
    return request<LlmUsage>('/system/llm/usage')
  },

  getDashboard(): Promise<DashboardData> {
    return request<DashboardData>('/dashboard')
  },

  listMeasurements(limit = 50): Promise<MeasurementSummary[]> {
    return request<MeasurementSummary[]>(`/measurements?limit=${limit}`)
  },

  getMeasurement(measurementId: string, pointLimit = 7200): Promise<MeasurementDetail> {
    return request<MeasurementDetail>(`/measurements/${measurementId}?point_limit=${pointLimit}`)
  },

  linkAnalysis(eventId: string, runId: string, stage?: AnalysisStage): Promise<NoiseEvent> {
    const query = stage ? `?stage=${stage}` : ''
    return request<NoiseEvent>(`/events/${eventId}/analyses/${runId}${query}`, { method: 'POST' })
  },

  uploadEventPhoto(eventId: string, file: File): Promise<EventPhoto> {
    const form = new FormData()
    form.append('file', file)
    return request<EventPhoto>(`/events/${eventId}/photos`, { method: 'POST', body: form })
  },

  deleteEventPhoto(eventId: string, photoId: string): Promise<void> {
    return request<void>(`/events/${eventId}/photos/${photoId}`, { method: 'DELETE' })
  },

  getEventInsight(eventId: string): Promise<Insight | null> {
    return request<Insight | null>(`/events/${eventId}/insight`)
  },

  generateEventInsight(eventId: string, force = false): Promise<Insight> {
    return request<Insight>(`/events/${eventId}/insight${force ? '?force=true' : ''}`, {
      method: 'POST',
    })
  },

  cancelEventInsight(eventId: string): Promise<Insight> {
    return request<Insight>(`/events/${eventId}/insight/cancel`, { method: 'POST' })
  },

  getEventInsightHistory(eventId: string, limit = 20): Promise<InsightVersion[]> {
    return request<InsightVersion[]>(`/events/${eventId}/insight/history?limit=${limit}`)
  },

  submitEventInsightFeedback(
    eventId: string,
    rating: InsightFeedbackRating,
    note = '',
  ): Promise<Insight> {
    return request<Insight>(`/events/${eventId}/insight/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, note }),
    })
  },

  eventPhotoUrl(eventId: string, photoId: string): string {
    return withToken(`${BASE_URL}/events/${eventId}/photos/${photoId}`)
  },

  listAuditLogs(params: AuditQuery = {}): Promise<Page<AuditLogEntry>> {
    return request<Page<AuditLogEntry>>(`/audit${toQuery({ ...params })}`)
  },

  setAnalysisStage(runId: string, stage: AnalysisStage | null): Promise<AnalysisRun> {
    return request<AnalysisRun>(`/analyses/${runId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage }),
    })
  },

  unlinkAnalysis(eventId: string, runId: string): Promise<NoiseEvent> {
    return request<NoiseEvent>(`/events/${eventId}/analyses/${runId}`, { method: 'DELETE' })
  },

  getEventSummary(eventId: string): Promise<EventSummary> {
    return request<EventSummary>(`/events/${eventId}/summary`)
  },
}
