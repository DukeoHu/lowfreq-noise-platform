import { Fragment, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { InsightCard } from '../components/insight-card'
import { MetricLabel } from '../components/metric-label'
import { loadPrivacy } from '../settings'
import type {
  AnalysisStage,
  EventAnalysisSummary,
  EventSummary,
  Insight,
  InsightFeedbackRating,
  InsightVersion,
  NoiseEvent,
} from '../types'

function formatLevel(value: number | null | undefined, unit = ' dB') {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)}${unit}`
}

function averageLevel(items: EventAnalysisSummary[], key: 'laeq' | 'lceq'): number | null {
  const values = items.map((item) => item[key]).filter((value): value is number => value !== null)
  if (values.length === 0) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function parseTags(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[,，;；\s]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ).slice(0, 20)
}

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '计算中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const EVENT_STATUS_LABELS: Record<string, string> = {
  open: '待处理',
  investigating: '排查中',
  resolved: '已解决',
}

interface DetailDraft {
  measurementPoint: string
  equipment: string
  notes: string
  tags: string
}

export default function EventsPage() {
  const [events, setEvents] = useState<NoiseEvent[]>([])
  const [title, setTitle] = useState('')
  const [location, setLocation] = useState('')
  const [description, setDescription] = useState('')
  const [measurementPoint, setMeasurementPoint] = useState('')
  const [equipment, setEquipment] = useState('')
  const [notes, setNotes] = useState('')
  const [tagsInput, setTagsInput] = useState('')
  const [coords, setCoords] = useState<{ latitude: number; longitude: number } | null>(null)
  const [geoBusy, setGeoBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [summaries, setSummaries] = useState<Record<string, EventSummary>>({})
  const [insights, setInsights] = useState<Record<string, Insight>>({})
  const [insightHistories, setInsightHistories] = useState<Record<string, InsightVersion[]>>({})
  const [insightBusyId, setInsightBusyId] = useState<string | null>(null)
  const [insightError, setInsightError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, DetailDraft>>({})
  const [savingDetailId, setSavingDetailId] = useState<string | null>(null)
  const [photoBusyId, setPhotoBusyId] = useState<string | null>(null)
  const [privacy] = useState(() => loadPrivacy())

  async function refresh() {
    try {
      setEvents(await api.listEvents())
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载事件失败')
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function loadSummary(eventId: string) {
    setDetailError(null)
    try {
      const summary = await api.getEventSummary(eventId)
      setSummaries((prev) => ({ ...prev, [eventId]: summary }))
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '加载事件分析失败')
    }
    try {
      const insight = await api.getEventInsight(eventId)
      if (insight) {
        setInsights((prev) => ({ ...prev, [eventId]: insight }))
        if (insight.status === 'succeeded') void loadInsightHistory(eventId)
      }
    } catch {
      // 未生成过解读属于正常情况，不提示错误
    }
  }

  async function loadInsightHistory(eventId: string) {
    try {
      const history = await api.getEventInsightHistory(eventId)
      setInsightHistories((prev) => ({ ...prev, [eventId]: history }))
    } catch {
      // 历史版本不是关键路径，失败时静默处理
    }
  }

  async function fetchEventInsight(eventId: string): Promise<Insight | null> {
    try {
      const data = await api.getEventInsight(eventId)
      if (data) setInsights((prev) => ({ ...prev, [eventId]: data }))
      return data
    } catch {
      return null
    }
  }

  async function pollEventInsight(eventId: string) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1500))
      const data = await fetchEventInsight(eventId)
      if (!data || data.status === 'succeeded' || data.status === 'failed' || data.status === 'cancelled') {
        if (data?.status === 'succeeded') void loadInsightHistory(eventId)
        return
      }
    }
  }

  async function handleGenerateEventInsight(eventId: string, force = false) {
    setInsightBusyId(eventId)
    setInsightError(null)
    try {
      const created = await api.generateEventInsight(eventId, force)
      setInsights((prev) => ({ ...prev, [eventId]: created }))
      void pollEventInsight(eventId)
    } catch (err) {
      setInsightError(err instanceof Error ? err.message : '生成 AI 解读失败')
    } finally {
      setInsightBusyId(null)
    }
  }

  async function handleCancelEventInsight(eventId: string) {
    setInsightBusyId(eventId)
    setInsightError(null)
    try {
      const cancelled = await api.cancelEventInsight(eventId)
      setInsights((prev) => ({ ...prev, [eventId]: cancelled }))
    } catch (err) {
      setInsightError(err instanceof Error ? err.message : '取消 AI 解读失败')
    } finally {
      setInsightBusyId(null)
    }
  }

  async function handleEventInsightFeedback(eventId: string, rating: InsightFeedbackRating) {
    setInsightBusyId(eventId)
    setInsightError(null)
    try {
      const updated = await api.submitEventInsightFeedback(eventId, rating)
      setInsights((prev) => ({ ...prev, [eventId]: updated }))
    } catch (err) {
      setInsightError(err instanceof Error ? err.message : '提交反馈失败')
    } finally {
      setInsightBusyId(null)
    }
  }

  async function toggleDetail(event: NoiseEvent) {
    if (expandedId === event.id) {
      setExpandedId(null)
      return
    }
    setExpandedId(event.id)
    setInsightError(null)
    setDrafts((prev) => ({
      ...prev,
      [event.id]: {
        measurementPoint: event.measurement_point,
        equipment: event.equipment,
        notes: event.notes,
        tags: event.tags.join('，'),
      },
    }))
    await loadSummary(event.id)
  }

  function patchEvent(event: NoiseEvent) {
    setEvents((prev) => prev.map((item) => (item.id === event.id ? event : item)))
    setSummaries((prev) => (prev[event.id] ? { ...prev, [event.id]: { ...prev[event.id], event } } : prev))
  }

  async function saveDetail(eventId: string) {
    const draft = drafts[eventId]
    if (!draft) return
    setSavingDetailId(eventId)
    setDetailError(null)
    try {
      const updated = await api.updateEvent(eventId, {
        measurement_point: draft.measurementPoint,
        equipment: draft.equipment,
        notes: draft.notes,
        tags: parseTags(draft.tags),
      })
      patchEvent(updated)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '保存事件补充信息失败')
    } finally {
      setSavingDetailId(null)
    }
  }

  async function handleDeleteEvent(event: NoiseEvent) {
    const confirmed = window.confirm(
      `确定删除事件「${event.title}」？关联分析会解除关联，已上传照片将一并删除，且不可撤销。`,
    )
    if (!confirmed) return
    setDetailError(null)
    try {
      await api.deleteEvent(event.id)
      setEvents((prev) => prev.filter((item) => item.id !== event.id))
      setSummaries((prev) => {
        const next = { ...prev }
        delete next[event.id]
        return next
      })
      setInsights((prev) => {
        const next = { ...prev }
        delete next[event.id]
        return next
      })
      if (expandedId === event.id) setExpandedId(null)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '删除事件失败')
    }
  }

  async function handleStageChange(eventId: string, runId: string, value: string) {
    setDetailError(null)
    try {
      await api.setAnalysisStage(runId, value === '' ? null : (value as AnalysisStage))
      await loadSummary(eventId)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '更新整改阶段失败')
    }
  }

  async function handleUnlink(eventId: string, runId: string) {
    setDetailError(null)
    try {
      const updated = await api.unlinkAnalysis(eventId, runId)
      patchEvent(updated)
      await loadSummary(eventId)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '解除关联失败')
    }
  }

  async function handlePhotoUpload(eventId: string, file: File | null) {
    if (!file) return
    setPhotoBusyId(eventId)
    setDetailError(null)
    try {
      await api.uploadEventPhoto(eventId, file)
      const updated = await api.getEvent(eventId)
      patchEvent(updated)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '上传照片失败')
    } finally {
      setPhotoBusyId(null)
    }
  }

  async function handlePhotoDelete(eventId: string, photoId: string) {
    setPhotoBusyId(eventId)
    setDetailError(null)
    try {
      await api.deleteEventPhoto(eventId, photoId)
      const updated = await api.getEvent(eventId)
      patchEvent(updated)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '删除照片失败')
    } finally {
      setPhotoBusyId(null)
    }
  }

  function captureLocation() {
    if (!privacy.allowLocation) {
      setError('未授权位置采集，请先到「隐私与校准」页开启位置授权')
      return
    }
    if (!navigator.geolocation) {
      setError('当前浏览器不支持定位')
      return
    }
    setGeoBusy(true)
    setError(null)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setCoords({
          latitude: Number(position.coords.latitude.toFixed(6)),
          longitude: Number(position.coords.longitude.toFixed(6)),
        })
        setGeoBusy(false)
      },
      (geoError) => {
        setError(geoError.message || '定位失败，请检查浏览器权限')
        setGeoBusy(false)
      },
      { enableHighAccuracy: false, timeout: 8000 },
    )
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault()
    if (!title.trim()) {
      setError('请填写事件标题')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.createEvent({
        title: title.trim(),
        location,
        description,
        measurement_point: measurementPoint,
        equipment,
        notes,
        tags: parseTags(tagsInput),
        latitude: coords?.latitude ?? null,
        longitude: coords?.longitude ?? null,
      })
      setTitle('')
      setLocation('')
      setDescription('')
      setMeasurementPoint('')
      setEquipment('')
      setNotes('')
      setTagsInput('')
      setCoords(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建事件失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>噪音事件</h1>
          <p className="subtitle">把一次排查记录成事件，补充测点、设备、照片与标签，并用 ON/OFF/ON 向导对比整改前后。</p>
        </div>
      </header>

      <form className="card form" onSubmit={handleCreate}>
        <div className="grid-2">
          <label className="field">
            <span>标题</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="如：3F 机房夜间嗡声" />
          </label>
          <label className="field">
            <span>位置</span>
            <input value={location} onChange={(event) => setLocation(event.target.value)} placeholder="如：3F 机房旁" />
          </label>
        </div>
        <div className="grid-2">
          <label className="field">
            <span>测点</span>
            <input
              value={measurementPoint}
              onChange={(event) => setMeasurementPoint(event.target.value)}
              placeholder="如：机房门外 1 m / 住户卧室床头"
            />
          </label>
          <label className="field">
            <span>设备</span>
            <input
              value={equipment}
              onChange={(event) => setEquipment(event.target.value)}
              placeholder="如：校准麦克风 + 手机 / 录音笔"
            />
          </label>
        </div>
        <div className="grid-2">
          <label className="field">
            <span>标签</span>
            <input
              value={tagsInput}
              onChange={(event) => setTagsInput(event.target.value)}
              placeholder="逗号分隔，如：夜间，低频，机房"
            />
          </label>
          <div className="field">
            <span>位置坐标（可选）</span>
            <div className="row-actions">
              <button type="button" className="ghost" onClick={captureLocation} disabled={geoBusy || !privacy.allowLocation}>
                {geoBusy ? '定位中…' : '获取当前位置'}
              </button>
              {coords ? (
                <>
                  <span className="hint">
                    {coords.latitude}, {coords.longitude}
                  </span>
                  <button type="button" className="link-button" onClick={() => setCoords(null)}>
                    清除
                  </button>
                </>
              ) : privacy.allowLocation ? (
                <span className="hint">未采集</span>
              ) : (
                <span className="hint">
                  未授权，请到<Link to="/settings">隐私与校准</Link>开启
                </span>
              )}
            </div>
          </div>
        </div>
        <label className="field">
          <span>描述</span>
          <textarea
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="现象、时间段、初步判断…"
          />
        </label>
        <label className="field">
          <span>备注</span>
          <textarea
            rows={2}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="排查过程、沟通记录、待办事项…"
          />
        </label>
        {error && <p className="alert" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? '创建中…' : '创建事件'}
        </button>
      </form>

      <div className="card">
        <h2>事件列表</h2>
        {events.length === 0 ? (
          <p className="empty">暂无事件</p>
        ) : (
          <table className="table">
            <caption className="sr-only">噪音事件列表</caption>
            <thead>
              <tr>
                <th scope="col">标题</th>
                <th scope="col">测点 / 位置</th>
                <th scope="col">设备</th>
                <th scope="col">标签</th>
                <th scope="col">状态</th>
                <th scope="col">关联分析</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {events.map((item) => {
                const summary = summaries[item.id]
                const beforeItems = summary?.analyses.filter((entry) => entry.stage === 'before') ?? []
                const offItems = summary?.analyses.filter((entry) => entry.stage === 'off') ?? []
                const afterItems = summary?.analyses.filter((entry) => entry.stage === 'after') ?? []
                const beforeLaeq = averageLevel(beforeItems, 'laeq')
                const offLaeq = averageLevel(offItems, 'laeq')
                const afterLaeq = averageLevel(afterItems, 'laeq')
                const beforeToAfter =
                  beforeLaeq !== null && afterLaeq !== null ? afterLaeq - beforeLaeq : null
                const beforeToOff = beforeLaeq !== null && offLaeq !== null ? offLaeq - beforeLaeq : null
                const offToAfter = offLaeq !== null && afterLaeq !== null ? afterLaeq - offLaeq : null
                const draft = drafts[item.id]
                return (
                  <Fragment key={item.id}>
                    <tr>
                      <td>{item.title}</td>
                      <td>
                        {item.measurement_point || '—'}
                        <br />
                        <small className="hint">{item.location || '未填写位置'}</small>
                      </td>
                      <td>{item.equipment || '—'}</td>
                      <td>
                        {item.tags.length === 0 ? (
                          '—'
                        ) : (
                          <span className="tag-list">
                            {item.tags.map((tag) => (
                              <span className="tag" key={tag}>
                                {tag}
                              </span>
                            ))}
                          </span>
                        )}
                      </td>
                      <td>{EVENT_STATUS_LABELS[item.status] ?? item.status}</td>
                      <td>{item.analysis_run_ids.length}</td>
                      <td>
                        <button
                          type="button"
                          className="link-button"
                          aria-expanded={expandedId === item.id}
                          onClick={() => void toggleDetail(item)}
                        >
                          {expandedId === item.id ? '收起' : '详情与对比'}
                        </button>
                      </td>
                    </tr>
                    {expandedId === item.id && (
                      <tr className="detail-row">
                        <td colSpan={7}>
                          <div className="detail-grid">
                            <label className="field">
                              <span>测点</span>
                              <input
                                value={draft?.measurementPoint ?? ''}
                                onChange={(event) =>
                                  setDrafts((prev) => ({
                                    ...prev,
                                    [item.id]: { ...prev[item.id], measurementPoint: event.target.value },
                                  }))
                                }
                              />
                            </label>
                            <label className="field">
                              <span>设备</span>
                              <input
                                value={draft?.equipment ?? ''}
                                onChange={(event) =>
                                  setDrafts((prev) => ({
                                    ...prev,
                                    [item.id]: { ...prev[item.id], equipment: event.target.value },
                                  }))
                                }
                              />
                            </label>
                            <label className="field">
                              <span>标签（逗号分隔）</span>
                              <input
                                value={draft?.tags ?? ''}
                                onChange={(event) =>
                                  setDrafts((prev) => ({
                                    ...prev,
                                    [item.id]: { ...prev[item.id], tags: event.target.value },
                                  }))
                                }
                              />
                            </label>
                          </div>
                          <label className="field" style={{ marginTop: 12 }}>
                            <span>备注</span>
                            <textarea
                              rows={2}
                              value={draft?.notes ?? ''}
                              onChange={(event) =>
                                setDrafts((prev) => ({
                                  ...prev,
                                  [item.id]: { ...prev[item.id], notes: event.target.value },
                                }))
                              }
                            />
                          </label>
                          <div className="actions" style={{ marginTop: 10 }}>
                            <button
                              type="button"
                              className="ghost"
                              onClick={() => void saveDetail(item.id)}
                              disabled={savingDetailId === item.id || !draft}
                            >
                              {savingDetailId === item.id ? '保存中…' : '保存补充信息'}
                            </button>
                            <button
                              type="button"
                              className="ghost danger"
                              onClick={() => void handleDeleteEvent(item)}
                            >
                              删除事件
                            </button>
                            {item.latitude !== null && item.longitude !== null ? (
                              <span className="hint">
                                坐标 {item.latitude}, {item.longitude}
                              </span>
                            ) : null}
                          </div>

                          <h3 style={{ marginTop: 20 }}>ON/OFF/ON 向导</h3>
                          <p className="hint">
                            整改前设备运行（ON）→ 关闭疑似设备（OFF）→ 整改后复测（ON），用阶段平均 LAeq 判断噪声来源与改善量。
                          </p>
                          <div className="wizard">
                            <div className={`wizard-step ${beforeItems.length > 0 ? 'active' : ''}`}>
                              <h3>1. 整改前 ON</h3>
                              <p className="hint">
                                {beforeItems.length} 条分析 · 平均 {formatLevel(beforeLaeq)}
                              </p>
                              <Link className="ghost" to={`/realtime?event=${item.id}&stage=before`}>
                                去测量
                              </Link>
                            </div>
                            <div className={`wizard-step ${offItems.length > 0 ? 'active' : ''}`}>
                              <h3>2. 关机 OFF</h3>
                              <p className="hint">
                                {offItems.length} 条分析 · 平均 {formatLevel(offLaeq)}
                              </p>
                              {beforeToOff !== null ? (
                                <p className="hint">较整改前 {beforeToOff >= 0 ? '+' : ''}{beforeToOff.toFixed(1)} dB</p>
                              ) : null}
                              <Link className="ghost" to={`/realtime?event=${item.id}&stage=off`}>
                                去测量
                              </Link>
                            </div>
                            <div className={`wizard-step ${afterItems.length > 0 ? 'active' : ''}`}>
                              <h3>3. 整改后 ON</h3>
                              <p className="hint">
                                {afterItems.length} 条分析 · 平均 {formatLevel(afterLaeq)}
                              </p>
                              {offToAfter !== null ? (
                                <p className="hint">较关机 {offToAfter >= 0 ? '+' : ''}{offToAfter.toFixed(1)} dB</p>
                              ) : null}
                              <Link className="ghost" to={`/realtime?event=${item.id}&stage=after`}>
                                去测量
                              </Link>
                            </div>
                          </div>
                          {beforeToAfter !== null ? (
                            <p className="hint" role="status">
                              整改前 → 整改后差值 {beforeToAfter >= 0 ? '+' : ''}
                              {beforeToAfter.toFixed(1)} dB
                              {beforeToAfter <= -10 ? '（达到 ≥10 dB 改善判定）' : '（未达 10 dB）'}
                            </p>
                          ) : null}

                          {summary?.band_comparison && (
                            <details style={{ marginTop: 8 }}>
                              <summary className="hint">
                                阶段 1/3 倍频程对比（基准 {summary.band_comparison['基准阶段']}，
                                阈值 {summary.band_comparison['判定阈值_db']} dB）
                              </summary>
                              {summary.band_comparison['与基准比较'].map((stage) => (
                                <div key={stage['阶段']} style={{ marginTop: 8 }}>
                                  <p className="hint">
                                    <strong>{stage['阶段']}</strong> 明显改善频段：
                                    {stage['明显改善频段_hz'].length > 0
                                      ? stage['明显改善频段_hz'].map((hz) => `${hz} Hz`).join('、')
                                      : '无'}
                                  </p>
                                  <table className="table">
                                    <thead>
                                      <tr>
                                        <th scope="col">中心频率</th>
                                        <th scope="col">基准</th>
                                        <th scope="col">当前</th>
                                        <th scope="col">降低</th>
                                        <th scope="col">判定</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {stage['频段'].map((row) => (
                                        <tr key={`${stage['阶段']}-${row['中心频率_hz']}`}>
                                          <td>{row['中心频率_hz']} Hz</td>
                                          <td>{formatLevel(row['基准_db'])}</td>
                                          <td>{formatLevel(row['当前_db'])}</td>
                                          <td>{row['降低_db'].toFixed(1)} dB</td>
                                          <td>{row['达到10db'] ? '≥10 dB' : '未达'}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              ))}
                            </details>
                          )}

                          <InsightCard
                            title="AI 对比解读"
                            insight={insights[item.id] ?? null}
                            busy={insightBusyId === item.id}
                            error={insightError}
                            history={insightHistories[item.id] ?? []}
                            disabled={!summary?.analyses.some((analysis) => analysis.status === 'succeeded')}
                            onGenerate={(force) => void handleGenerateEventInsight(item.id, force)}
                            onCancel={() => void handleCancelEventInsight(item.id)}
                            onFeedback={(rating) => void handleEventInsightFeedback(item.id, rating)}
                          />

                          {!summary ? (
                            <p className="empty">加载中…</p>
                          ) : summary.analyses.length === 0 ? (
                            <p className="empty">暂无关联分析，可在分析结果页的「归档到排查事件」中关联。</p>
                          ) : (
                            <table className="table">
                              <caption className="sr-only">关联分析指标对比</caption>
                              <thead>
                                <tr>
                                  <th scope="col">音频 / 时间</th>
                                  <th scope="col">阶段</th>
                                  <th scope="col">状态</th>
                                  <th scope="col">
                                    <MetricLabel term="laeq" />
                                  </th>
                                  <th scope="col">
                                    <MetricLabel term="lceq" />
                                  </th>
                                  <th scope="col">
                                    <MetricLabel term="ca" />
                                  </th>
                                  <th scope="col">
                                    <MetricLabel term="dominantPeak" />
                                  </th>
                                  <th scope="col">操作</th>
                                </tr>
                              </thead>
                              <tbody>
                                {summary.analyses.map((analysis) => (
                                  <tr key={analysis.run_id}>
                                    <td>
                                      {analysis.audio_filename}
                                      <br />
                                      <small className="hint">
                                        {new Date(analysis.created_at).toLocaleString('zh-CN')}
                                      </small>
                                    </td>
                                    <td>
                                      <select
                                        value={analysis.stage ?? ''}
                                        aria-label={`${analysis.audio_filename} 整改阶段`}
                                        onChange={(event) => handleStageChange(item.id, analysis.run_id, event.target.value)}
                                      >
                                        <option value="">未标记</option>
                                        <option value="before">整改前 ON</option>
                                        <option value="off">关机 OFF</option>
                                        <option value="after">整改后 ON</option>
                                      </select>
                                    </td>
                                    <td>{STATUS_LABELS[analysis.status] ?? analysis.status}</td>
                                    <td>{formatLevel(analysis.laeq)}</td>
                                    <td>{formatLevel(analysis.lceq)}</td>
                                    <td>{formatLevel(analysis.c_minus_a)}</td>
                                    <td>
                                      {analysis.dominant_peak_hz === null
                                        ? '—'
                                        : `${analysis.dominant_peak_hz} Hz / ${formatLevel(analysis.dominant_peak_level)}`}
                                    </td>
                                    <td className="row-actions">
                                      <Link className="link-button" to={`/analyses/${analysis.run_id}`}>
                                        结果
                                      </Link>
                                      {analysis.status === 'succeeded' && (
                                        <a className="link-button" href={api.reportPdfUrl(analysis.run_id)}>
                                          PDF
                                        </a>
                                      )}
                                      <button
                                        type="button"
                                        className="link-button danger"
                                        onClick={() => handleUnlink(item.id, analysis.run_id)}
                                      >
                                        解除关联
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}

                          <h3 style={{ marginTop: 20 }}>现场照片</h3>
                          <div className="row-actions">
                            <input
                              type="file"
                              accept=".jpg,.jpeg,.png,.webp,image/*"
                              aria-label="上传现场照片"
                              disabled={photoBusyId === item.id}
                              onChange={(event) => {
                                const file = event.target.files?.[0] ?? null
                                event.target.value = ''
                                void handlePhotoUpload(item.id, file)
                              }}
                            />
                            {photoBusyId === item.id ? <span className="hint">处理中…</span> : null}
                          </div>
                          {item.photos.length === 0 ? (
                            <p className="hint">暂无照片，支持 JPG/PNG/WebP，单张不超过 10 MB。</p>
                          ) : (
                            <div className="photo-grid">
                              {item.photos.map((photo) => (
                                <figure className="photo-card" key={photo.id}>
                                  <a href={api.eventPhotoUrl(item.id, photo.id)} target="_blank" rel="noreferrer">
                                    <img src={api.eventPhotoUrl(item.id, photo.id)} alt={photo.original_filename} />
                                  </a>
                                  <figcaption>
                                    {photo.original_filename}
                                    <br />
                                    <button
                                      type="button"
                                      className="link-button danger"
                                      onClick={() => void handlePhotoDelete(item.id, photo.id)}
                                      disabled={photoBusyId === item.id}
                                    >
                                      删除
                                    </button>
                                  </figcaption>
                                </figure>
                              ))}
                            </div>
                          )}

                          {detailError && <p className="alert" role="alert">{detailError}</p>}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
