import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { BandChart, FftChart, SpectrogramChart, TrendChart } from '../components/charts'
import { InsightCard } from '../components/insight-card'
import { MetricLabel } from '../components/metric-label'
import { loadPrivacy } from '../settings'
import type {
  AnalysisResult,
  AnalysisRun,
  AnalysisStage,
  Insight,
  InsightFeedbackRating,
  InsightVersion,
  NoiseEvent,
} from '../types'

const FLAG_LABELS: Record<string, string> = {
  uncalibrated: '未校准，读数为相对值',
  clipping_suspected: '疑似削波',
  dc_offset_detected: '存在直流偏移',
  digital_silence: '数字静音',
  mostly_silent: '大部分时段静音',
  short_duration: '录音过短',
  multichannel_downmixed: '多声道已混为单声道',
}

function formatLevel(value: number | null | undefined, unit = 'dB') {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} ${unit}`
}

export default function AnalysisPage() {
  const { runId } = useParams<{ runId: string }>()
  const [run, setRun] = useState<(AnalysisRun & { result?: AnalysisResult | null }) | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [events, setEvents] = useState<NoiseEvent[]>([])
  const [selectedEventId, setSelectedEventId] = useState('')
  const [newEventTitle, setNewEventTitle] = useState('')
  const [linkBusy, setLinkBusy] = useState(false)
  const [linkNotice, setLinkNotice] = useState<string | null>(null)
  const [linkError, setLinkError] = useState<string | null>(null)
  const [stage, setStage] = useState<AnalysisStage | ''>('')
  const [includeAudio, setIncludeAudio] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [insight, setInsight] = useState<Insight | null>(null)
  const [insightError, setInsightError] = useState<string | null>(null)
  const [insightBusy, setInsightBusy] = useState(false)
  const [insightHistory, setInsightHistory] = useState<InsightVersion[]>([])
  const [insightToken, setInsightToken] = useState(0)
  const [pollToken, setPollToken] = useState(0)
  const [privacy] = useState(() => loadPrivacy())

  useEffect(() => {
    api
      .listEvents()
      .then(setEvents)
      .catch(() => undefined)
  }, [])

  function upsertEvent(updated: NoiseEvent) {
    setEvents((prev) => [updated, ...prev.filter((item) => item.id !== updated.id)])
  }

  async function handleStageChange(value: AnalysisStage | '') {
    setStage(value)
    if (!run) return
    setLinkError(null)
    try {
      const updated = await api.setAnalysisStage(run.id, value === '' ? null : value)
      setRun((prev) => (prev ? { ...prev, stage: updated.stage ?? null } : prev))
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : '更新整改阶段失败')
    }
  }

  async function linkToEvent(eventId: string) {
    if (!run) return
    setLinkBusy(true)
    setLinkError(null)
    setLinkNotice(null)
    try {
      const updated = await api.linkAnalysis(eventId, run.id)
      upsertEvent(updated)
      setLinkNotice(`已关联到事件「${updated.title}」`)
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : '关联失败')
    } finally {
      setLinkBusy(false)
    }
  }

  async function handleCreateAndLink(event: FormEvent) {
    event.preventDefault()
    if (!run || !newEventTitle.trim()) return
    setLinkBusy(true)
    setLinkError(null)
    setLinkNotice(null)
    try {
      const created = await api.createEvent({ title: newEventTitle.trim() })
      const updated = await api.linkAnalysis(created.id, run.id)
      upsertEvent(updated)
      setNewEventTitle('')
      setLinkNotice(`已新建并关联事件「${updated.title}」`)
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : '新建事件失败')
    } finally {
      setLinkBusy(false)
    }
  }

  useEffect(() => {
    if (!runId) return
    let cancelled = false
    let timer: number | undefined

    async function poll() {
      try {
        const detail = await api.getAnalysis(runId as string)
        if (cancelled) return
        setRun(detail)
        setStage(detail.stage ?? '')
        if (detail.status === 'queued' || detail.status === 'running') {
          timer = window.setTimeout(poll, 1500)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载失败')
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [runId, pollToken])

  useEffect(() => {
    if (!runId || run?.status !== 'succeeded') return
    let cancelled = false
    let timer: number | undefined

    async function pollInsight() {
      try {
        const data = await api.getAnalysisInsight(runId as string)
        if (cancelled) return
        setInsight(data)
        if (data && (data.status === 'queued' || data.status === 'running')) {
          timer = window.setTimeout(pollInsight, 1500)
        }
      } catch (err) {
        if (!cancelled) setInsightError(err instanceof Error ? err.message : '加载 AI 解读失败')
      }
    }

    void pollInsight()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [runId, run?.status, insightToken])

  async function handleGenerateInsight(force = false) {
    if (!run) return
    setInsightBusy(true)
    setInsightError(null)
    try {
      const created = await api.generateAnalysisInsight(run.id, force)
      setInsight(created)
      setInsightToken((value) => value + 1)
    } catch (err) {
      setInsightError(err instanceof Error ? err.message : '生成失败')
    } finally {
      setInsightBusy(false)
    }
  }

  async function handleCancelInsight() {
    if (!run) return
    setInsightBusy(true)
    setInsightError(null)
    try {
      setInsight(await api.cancelAnalysisInsight(run.id))
    } catch (err) {
      setInsightError(err instanceof Error ? err.message : '取消失败')
    } finally {
      setInsightBusy(false)
    }
  }

  async function handleInsightFeedback(rating: InsightFeedbackRating) {
    if (!run) return
    setInsightBusy(true)
    setInsightError(null)
    try {
      setInsight(await api.submitAnalysisInsightFeedback(run.id, rating))
    } catch (err) {
      setInsightError(err instanceof Error ? err.message : '提交反馈失败')
    } finally {
      setInsightBusy(false)
    }
  }

  useEffect(() => {
    if (!runId || insight?.status !== 'succeeded') return
    api
      .getAnalysisInsightHistory(runId)
      .then(setInsightHistory)
      .catch(() => undefined)
  }, [runId, insight?.id, insight?.status, insight?.updated_at])

  async function handleRetry() {
    if (!run) return
    setActionBusy(true)
    setActionError(null)
    try {
      const updated = await api.retryAnalysis(run.id)
      setRun((prev) => (prev ? { ...prev, ...updated, result: null } : prev))
      setPollToken((value) => value + 1)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '重试失败')
    } finally {
      setActionBusy(false)
    }
  }

  async function handleCancel() {
    if (!run) return
    setActionBusy(true)
    setActionError(null)
    try {
      const updated = await api.cancelAnalysis(run.id)
      setRun((prev) => (prev ? { ...prev, ...updated } : prev))
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '取消失败')
    } finally {
      setActionBusy(false)
    }
  }

  if (error) {
    return (
      <section>
        <p className="alert" role="alert">{error}</p>
        <Link to="/upload">返回测量分析</Link>
      </section>
    )
  }

  if (!run) {
    return <p className="empty">加载中…</p>
  }

  const result = run.result ?? null
  const lowRatio = result?.low_frequency?.ratio ?? null
  const statusText =
    run.status === 'queued'
      ? '排队中'
      : run.status === 'running'
        ? '计算中'
        : run.status === 'failed'
          ? '失败'
          : run.status === 'cancelled'
            ? '已取消'
            : '已完成'

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>分析结果</h1>
          <p className="subtitle">
            任务 {run.id.slice(0, 8)} · {statusText} · 算法 {run.algorithm_version}
          </p>
        </div>
        <div className="actions">
          {run.status === 'succeeded' && (
            <>
              <a className="ghost" href={api.reportPdfUrl(run.id)}>
                下载 PDF 报告
              </a>
              <a className="ghost" href={api.bandsCsvUrl(run.id)}>
                下载 1/3 倍频程 CSV
              </a>
              <a className="ghost" href={api.exportZipUrl(run.id, includeAudio)}>
                下载数据包 ZIP
              </a>
            </>
          )}
          {run.status === 'failed' && (
            <button type="button" className="ghost" disabled={actionBusy} onClick={() => void handleRetry()}>
              {actionBusy ? '提交中…' : '重试分析'}
            </button>
          )}
          {(run.status === 'queued' || run.status === 'running') && (
            <button type="button" className="ghost danger" disabled={actionBusy} onClick={() => void handleCancel()}>
              {actionBusy ? '取消中…' : '取消分析'}
            </button>
          )}
          <Link className="ghost" to="/upload">新建分析</Link>
        </div>
      </header>

      {run.status === 'succeeded' && (
        <label className="checkbox" style={{ marginBottom: 12 }}>
          <input
            type="checkbox"
            checked={includeAudio}
            disabled={!privacy.allowSharing}
            onChange={(event) => setIncludeAudio(event.target.checked)}
          />
          <span>
            数据包 ZIP 包含原始音频
            {!privacy.allowSharing ? '（未开启分享授权，当前仅导出结果、CSV 与 PDF）' : ''}
          </span>
        </label>
      )}

      {run.status === 'failed' && (
        <p className="alert" role="alert">分析失败：{run.error ?? '未知错误'}</p>
      )}

      {run.status === 'cancelled' && (
        <p className="alert" role="status">任务已取消，未生成结果。如需重新计算，可在上传页重新提交。</p>
      )}

      {actionError && (
        <p className="alert" role="alert">{actionError}</p>
      )}

      {run.status === 'succeeded' && (
        <InsightCard
          insight={insight}
          busy={insightBusy}
          error={insightError}
          history={insightHistory}
          onGenerate={(force) => void handleGenerateInsight(force)}
          onCancel={() => void handleCancelInsight()}
          onFeedback={(rating) => void handleInsightFeedback(rating)}
        />
      )}

      {(run.status === 'queued' || run.status === 'running') && (
        <p className="empty" role="status">正在计算频谱与声级指标，请稍候…</p>
      )}

      {result && (
        <>
          {result.quality.flags.length > 0 && (
            <ul className="flag-list" aria-live="polite">
              {result.quality.flags.map((flag) => (
                <li key={flag} className="flag">
                  <span aria-hidden="true">⚠</span> {FLAG_LABELS[flag] ?? flag}
                </li>
              ))}
            </ul>
          )}

          <div className="metric-grid">
            <div className="card metric">
              <MetricLabel term="laeq" />
              <strong className="metric-value">{formatLevel(result.overall.laeq)}</strong>
            </div>
            <div className="card metric">
              <MetricLabel term="lceq" />
              <strong className="metric-value">{formatLevel(result.overall.lceq)}</strong>
            </div>
            <div className="card metric">
              <MetricLabel term="ca" />
              <strong className="metric-value">{formatLevel(result.overall.c_minus_a)}</strong>
            </div>
            <div className="card metric">
              <MetricLabel term="lafmax" />
              <strong className="metric-value">{formatLevel(result.overall.lafmax)}</strong>
            </div>
            <div className="card metric">
              <MetricLabel term="lcpeak" />
              <strong className="metric-value">{formatLevel(result.overall.lcpeak)}</strong>
            </div>
            <div className="card metric">
              <MetricLabel
                term="lowRatio"
                suffix={
                  result.low_frequency
                    ? `（${result.low_frequency.low_hz}–${result.low_frequency.high_hz} Hz）`
                    : ''
                }
              />
              <strong className="metric-value">{lowRatio === null ? '—' : `${(lowRatio * 100).toFixed(1)}%`}</strong>
            </div>
            <div className="card metric">
              <MetricLabel term="duration" />
              <strong className="metric-value">
                {result.audio.duration_s.toFixed(1)} s / {(result.audio.sample_rate / 1000).toFixed(1)} kHz
              </strong>
            </div>
          </div>

          <div className="card">
            <h2>归档到排查事件</h2>
            <p className="hint">标记整改阶段并关联事件后，可在事件页对比整改前后的 LAeq / LCeq 与主峰频率。</p>
            <div className="inline-form">
              <label className="field">
                <span>整改阶段</span>
                <select
                  value={stage}
                  onChange={(event) => handleStageChange(event.target.value as AnalysisStage | '')}
                >
                  <option value="">未标记</option>
                  <option value="before">整改前 ON</option>
                  <option value="off">关机 OFF</option>
                  <option value="after">整改后 ON</option>
                </select>
              </label>
            </div>
            {events.length > 0 && (
              <div className="inline-form">
                <label className="field">
                  <span>已有事件</span>
                  <select value={selectedEventId} onChange={(event) => setSelectedEventId(event.target.value)}>
                    <option value="">请选择事件</option>
                    {events.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                        {item.analysis_run_ids.includes(run.id) ? '（已关联）' : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="ghost"
                  disabled={!selectedEventId || linkBusy}
                  onClick={() => linkToEvent(selectedEventId)}
                >
                  关联
                </button>
              </div>
            )}
            <form className="inline-form" onSubmit={handleCreateAndLink}>
              <label className="field">
                <span>新建事件</span>
                <input
                  value={newEventTitle}
                  onChange={(event) => setNewEventTitle(event.target.value)}
                  placeholder="如：整改后复测（夜间）"
                />
              </label>
              <button type="submit" className="primary" disabled={!newEventTitle.trim() || linkBusy}>
                新建并关联
              </button>
            </form>
            {linkNotice && <p className="hint" role="status">{linkNotice}</p>}
            {linkError && <p className="alert" role="alert">{linkError}</p>}
          </div>

          <div className="card">
            <h2>低频峰值</h2>
            {result.peaks.length === 0 ? (
              <p className="empty">未检测到显著的低频峰值。</p>
            ) : (
              <table className="table">
                <caption className="sr-only">低频峰值列表</caption>
                <thead>
                  <tr>
                    <th scope="col">中心频率</th>
                    <th scope="col">声级</th>
                    <th scope="col">突出度</th>
                  </tr>
                </thead>
                <tbody>
                  {result.peaks.map((peak) => (
                    <tr key={peak.fc}>
                      <td>{peak.fc} Hz</td>
                      <td>{formatLevel(peak.level)}</td>
                      <td>{formatLevel(peak.prominence)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            <h2>时间趋势</h2>
            <TrendChart t={result.time_series.t} laeq={result.time_series.laeq} lceq={result.time_series.lceq} />
          </div>

          <div className="card">
            <h2>1/3 倍频程频谱</h2>
            <BandChart bands={result.bands.z_weighted} peaks={result.peaks} />
            <details className="details">
              <summary>数据表（可访问性替代视图）</summary>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">中心频率 (Hz)</th>
                    <th scope="col">Z 计权 (dB)</th>
                    <th scope="col">A 计权 (dB)</th>
                  </tr>
                </thead>
                <tbody>
                  {result.bands.z_weighted.map((band, index) => (
                    <tr key={band.fc}>
                      <td>{band.fc}</td>
                      <td>{formatLevel(band.level)}</td>
                      <td>{formatLevel(result.bands.a_weighted[index]?.level)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </div>

          {result.fft && result.fft.f.length > 0 && (
            <div className="card">
              <h2>窄带 FFT 谱（{result.fft.min_hz}–{result.fft.max_hz} Hz）</h2>
              <FftChart f={result.fft.f} level={result.fft.level} />
              <p className="hint">
                Z 计权、{result.fft.window} 窗；频率分辨率 {result.fft.df.toFixed(2)} Hz/点。
              </p>
            </div>
          )}

          {result.harmonics && result.harmonics.length > 0 && (
            <div className="card">
              <h2>谐波与转速候选</h2>
              <p className="hint">把窄带谱显著峰当作基频，检查 2X–10X 谐波；仅作排查线索，请结合现场设备转速核对。</p>
              <table className="table">
                <caption className="sr-only">谐波与转速候选</caption>
                <thead>
                  <tr>
                    <th scope="col">基频</th>
                    <th scope="col">推算转速</th>
                    <th scope="col">命中谐波</th>
                    <th scope="col">匹配分数</th>
                  </tr>
                </thead>
                <tbody>
                  {result.harmonics.map((item) => (
                    <tr key={item.fundamental_hz}>
                      <td>{item.fundamental_hz} Hz</td>
                      <td>{Math.round(item.rpm)} rpm</td>
                      <td>{item.matched_orders.length > 0 ? item.matched_orders.map((order) => `${order}X`).join('、') : '—'}</td>
                      <td>{(item.score * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.spectrogram && (
            <div className="card">
              <h2>声谱图</h2>
              <SpectrogramChart {...result.spectrogram} />
            </div>
          )}
        </>
      )}
    </section>
  )
}
