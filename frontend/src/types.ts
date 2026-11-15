export type AnalysisStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type InsightStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface InsightFinding {
  title: string
  detail: string
  evidence?: string
}

export interface InsightContent {
  summary: string
  findings: InsightFinding[]
  recommendations: string[]
  caveats: string[]
  confidence: 'low' | 'medium' | 'high'
  confidence_basis?: string
  validation_warnings?: string[]
}

export type InsightFeedbackRating = 'useful' | 'not_useful' | 'adopted'

export interface Insight {
  id: string
  run_id?: string | null
  event_id?: string | null
  status: InsightStatus
  model: string
  prompt_version: string
  content: InsightContent | null
  error: string | null
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at: string | null
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  elapsed_ms?: number
  cache_hit?: boolean
  feedback?: InsightFeedbackRating | ''
  feedback_note?: string
  feedback_at?: string | null
}

export interface InsightVersion {
  id: string
  run_id?: string | null
  event_id?: string | null
  model: string
  prompt_version: string
  content: InsightContent | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  elapsed_ms: number
  created_at: string
}

export interface LlmConfig {
  enabled: boolean
  base_url: string
  model: string
  timeout_s: number
  max_tokens: number
  auto_insight: boolean
  has_api_key: boolean
  redact_context: boolean
  api_key_env_only: boolean
  monthly_token_budget: number
  source: 'db' | 'env' | 'none' | string
  updated_at: string | null
  updated_by: string | null
}

export interface LlmConfigInput {
  enabled: boolean
  base_url: string
  model: string
  api_key?: string
  clear_api_key?: boolean
  timeout_s: number
  max_tokens: number
  auto_insight: boolean
  redact_context: boolean
  api_key_env_only: boolean
  monthly_token_budget: number
}

export interface LlmUsage {
  month: string
  requests: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  budget: number
  remaining_tokens: number | null
  used_percent: number | null
  exceeded: boolean
}

export interface LlmTestResult {
  ok: boolean
  model: string
  reply: string
}

export interface AudioFile {
  id: string
  original_filename: string
  size_bytes: number
  sample_rate: number | null
  channels: number | null
  duration_s: number | null
  sha256: string
  created_at: string
}

export interface BandLevel {
  fc: number
  level: number | null
}

export interface Peak {
  fc: number
  level: number | null
  prominence: number | null
}

export interface QualityReport {
  duration_s: number
  sample_rate: number
  channels: number
  calibrated: boolean
  clip_ratio: number
  dc_offset: number
  rms_dbfs: number
  silence_ratio: number
  flags: string[]
}

export interface HarmonicCandidate {
  fundamental_hz: number
  fundamental_level_db: number
  rpm: number
  matched_orders: number[]
  score: number
  harmonics: { order: number; target_hz: number; matched_hz: number | null }[]
}

export interface LowFrequencyShare {
  low_hz: number
  high_hz: number
  ratio: number | null
  low_level_db: number | null
  total_level_db: number | null
  difference_db: number | null
}

export interface FftSpectrum {
  f: number[]
  level: number[]
  df: number
  min_hz: number
  max_hz: number
  window: string
}

export interface AnalysisResult {
  algorithm_version: string
  generated_at: string
  audio: { sample_rate: number; channels: number; samples: number; duration_s: number }
  calibration: { mode: string; offset_db: number; full_scale_pa: number }
  overall: {
    laeq: number | null
    lceq: number | null
    lzeq: number | null
    c_minus_a: number | null
    lafmax?: number | null
    lcfmax?: number | null
    lcpeak?: number | null
  }
  low_frequency?: LowFrequencyShare
  fft?: FftSpectrum
  harmonics?: HarmonicCandidate[]
  bands: { z_weighted: BandLevel[]; a_weighted: BandLevel[] }
  time_series: { t: number[]; laeq: (number | null)[]; lceq: (number | null)[] }
  peaks: Peak[]
  spectrogram: { t: number[]; f: number[]; z: number[][]; vmin: number; vmax: number } | null
  quality: QualityReport
}

export type AnalysisStage = 'before' | 'off' | 'after'

export interface AnalysisRun {
  id: string
  audio_file_id: string
  status: AnalysisStatus
  stage?: AnalysisStage | null
  algorithm_version: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  config?: Record<string, unknown> | null
  quality?: QualityReport | null
  result?: AnalysisResult | null
}

export interface EventPhoto {
  id: string
  original_filename: string
  size_bytes: number
  created_at: string
}

export interface NoiseEvent {
  id: string
  title: string
  description: string
  location: string
  measurement_point: string
  equipment: string
  notes: string
  tags: string[]
  latitude: number | null
  longitude: number | null
  status: string
  created_at: string
  updated_at: string
  analysis_run_ids: string[]
  photos: EventPhoto[]
}

export interface AuditLogEntry {
  id: string
  action: string
  actor: string
  entity_type: string
  entity_id: string | null
  detail: Record<string, unknown>
  created_at: string
}

export interface EventAnalysisSummary {
  run_id: string
  audio_filename: string
  status: string
  stage: AnalysisStage | null
  created_at: string
  duration_s: number | null
  laeq: number | null
  lceq: number | null
  lzeq: number | null
  c_minus_a: number | null
  dominant_peak_hz: number | null
  dominant_peak_level: number | null
  calibrated: boolean | null
  flags: string[]
}

export interface EventBandRow {
  '中心频率_hz': number
  '基准_db': number
  '当前_db': number
  '降低_db': number
  '达到10db': boolean
}

export interface EventBandComparison {
  '基准阶段': string
  '判定阈值_db': number
  '各阶段平均Z计权三分之一倍频程': {
    '阶段': string
    '频段': { '中心频率_hz': number; '声级_db': number }[]
  }[]
  '与基准比较': { '阶段': string; '频段': EventBandRow[]; '明显改善频段_hz': number[] }[]
  '明显改善': Record<string, number[]>
}

export interface EventSummary {
  event: NoiseEvent
  analyses: EventAnalysisSummary[]
  band_comparison: EventBandComparison | null
}

export interface AnalysisConfigInput {
  frame_s?: number
  hop_s?: number
  include_spectrogram?: boolean
  peak_top_n?: number
  calibration?: { mode: 'uncalibrated' | 'calibrated'; offset_db: number; full_scale_pa: number }
}

export interface SystemStatus {
  data_dir: string
  database: string
  retention_days: number
  analysis_workers: number
  audio: { count: number; bytes: number }
  photos: { count: number }
  results: { count: number; bytes: number }
  pending_runs: number
  failed_runs: number
  disk: {
    total_bytes: number
    used_bytes: number
    free_bytes: number
    used_percent: number
    warn_percent: number
    warning: boolean
  }
}

export interface RealtimeLevels {
  laeq: number | null
  lceq: number | null
  lzeq: number | null
  c_minus_a: number | null
  laf: number | null
  lcf: number | null
  lafmax: number | null
  lcfmax: number | null
  lcpeak: number | null
}

export interface RealtimeSpectrum {
  f: number[]
  level: number[]
}

export interface RealtimeFrame {
  type: 'frame'
  seq: number
  ready: boolean
  t: number
  sample_rate: number
  measurement_id?: string
  recorded_s?: number
  levels: RealtimeLevels
  cumulative: {
    laeq: number | null
    lceq: number | null
    lzeq: number | null
    duration_s: number
  }
  fft: RealtimeSpectrum
  spectrum: RealtimeSpectrum
  peak_hold: RealtimeSpectrum
  bands: BandLevel[]
}

export interface RealtimeSegment {
  run_id: string
  audio_file_id: string
  segment: number
  duration_s: number
  truncated: boolean
  interrupted: boolean
}

export interface RealtimeReady {
  type: 'ready'
  sample_rate: number
  fft_size: number
  channels: number
  dtype: string
  save_on_stop: boolean
  segment: number
  segments: RealtimeSegment[]
  measurement_id?: string
}

export interface RealtimeSaved extends RealtimeSegment {
  type: 'saved'
  measurement_id?: string
  metric_points?: number
}

export interface RealtimeStopped {
  type: 'stopped'
  duration_s: number
  saved: false
  measurement_id?: string
  metric_points?: number
}

export interface RealtimeDiscarded {
  type: 'discarded'
  detail: string
  measurement_id?: string
  metric_points?: number
}

export interface RealtimeError {
  type: 'error'
  detail: string
}

export interface MetricPoint {
  t_offset_s: number
  laeq: number | null
  lceq: number | null
  lzeq: number | null
  laf: number | null
  lcf: number | null
  lafmax: number | null
  lcfmax: number | null
  lcpeak: number | null
  c_minus_a: number | null
}

export interface MeasurementSegment {
  id: string
  segment_index: number
  run_id: string | null
  audio_file_id: string | null
  duration_s: number
  interrupted: boolean
  created_at: string
}

export interface MeasurementSummary {
  id: string
  session_id: string
  device_label: string
  sample_rate: number
  offset_db: number
  full_scale_pa: number
  status: string
  started_at: string
  ended_at: string | null
  duration_s: number
  point_count: number
  capture: Record<string, unknown>
}

export interface MeasurementDetail extends MeasurementSummary {
  points: MetricPoint[]
  segments: MeasurementSegment[]
}

export interface DashboardKpi {
  measurements: number
  measurements_24h: number
  recorded_seconds: number
  events_open: number
  events_total: number
  analyses_total: number
  analyses_pending: number
  analyses_failed: number
  audio_bytes: number
}

export interface DashboardEvent {
  id: string
  title: string
  status: string
  location: string
  measurement_point: string
  created_at: string
  analysis_count: number
}

export interface DashboardMeasurement extends MeasurementSummary {
  points: MetricPoint[]
}

export interface DashboardData {
  generated_at: string
  kpi: DashboardKpi
  latest_measurement: DashboardMeasurement | null
  recent_measurements: MeasurementSummary[]
  pending_events: DashboardEvent[]
  recent_events: DashboardEvent[]
  system: SystemStatus
}
