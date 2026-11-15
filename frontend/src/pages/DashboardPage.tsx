import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { DashboardData } from '../types'

// 图表依赖 plotly，体积较大；延后到工作台主体渲染后再按需加载
const TrendChart = lazy(() => import('../components/charts').then((module) => ({ default: module.TrendChart })))

const MEASUREMENT_STATUS: Record<string, string> = {
  running: '测量中',
  completed: '已完成',
  interrupted: '中断归档',
}

const EVENT_STATUS: Record<string, string> = {
  open: '待处理',
  investigating: '排查中',
  resolved: '已解决',
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const value = bytes / 1024 ** index
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  if (minutes > 0) return `${minutes} 分 ${rest} 秒`
  return `${rest} 秒`
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.getDashboard())
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载工作台失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const kpi = data?.kpi
  const latest = data?.latest_measurement
  const points = latest?.points ?? []
  const trendT = points.map((point) => point.t_offset_s)
  const trendA = points.map((point) => point.laeq)
  const trendC = points.map((point) => point.lceq)

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>工作台</h1>
          <p className="subtitle">
            实时测量按 1 Hz 自动落库，这里汇总最近的声级趋势、待处理事件、测量记录与系统状态。
          </p>
        </div>
        <div className="actions">
          <Link className="ghost" to="/realtime">
            开始实时测量
          </Link>
          <button type="button" className="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </header>

      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}

      {!data ? (
        <p className="empty">{loading ? '正在加载工作台…' : '暂无数据'}</p>
      ) : (
        <>
          <div className="metric-grid dashboard-kpis">
            <div className="metric">
              <span className="metric-label">最近 24 小时测量</span>
              <strong className="metric-value">{kpi?.measurements_24h ?? 0}</strong>
              <small className="hint">累计 {kpi?.measurements ?? 0} 条测量记录</small>
            </div>
            <div className="metric">
              <span className="metric-label">待处理事件</span>
              <strong className="metric-value">{kpi?.events_open ?? 0}</strong>
              <small className="hint">共 {kpi?.events_total ?? 0} 个噪音事件</small>
            </div>
            <div className="metric">
              <span className="metric-label">已记录时长</span>
              <strong className="metric-value">{formatDuration(kpi?.recorded_seconds ?? 0)}</strong>
              <small className="hint">逐秒指标点自动保存</small>
            </div>
            <div className="metric">
              <span className="metric-label">分析任务</span>
              <strong className="metric-value">{kpi?.analyses_total ?? 0}</strong>
              <small className="hint">
                排队 {kpi?.analyses_pending ?? 0} · 失败 {kpi?.analyses_failed ?? 0}
              </small>
            </div>
            <div className="metric">
              <span className="metric-label">音频存储</span>
              <strong className="metric-value">{formatBytes(kpi?.audio_bytes ?? 0)}</strong>
              <small className={data.system.disk.warning ? 'hint warn' : 'hint'}>
                磁盘占用 {data.system.disk.used_percent.toFixed(1)}%
              </small>
            </div>
          </div>

          <div className="card">
            <h2>最近一次测量 · 1 Hz 趋势</h2>
            {latest && points.length > 0 ? (
              <>
                <p className="hint dashboard-meta">
                  {latest.device_label || '未命名设备'} · {latest.sample_rate} Hz ·{' '}
                  {formatDateTime(latest.started_at)} · {formatDuration(latest.duration_s)} ·{' '}
                  {latest.point_count} 个指标点 ·{' '}
                  {MEASUREMENT_STATUS[latest.status] ?? latest.status}
                </p>
                <Suspense fallback={<p className="empty">图表加载中…</p>}>
                  <TrendChart t={trendT} laeq={trendA} lceq={trendC} />
                </Suspense>
              </>
            ) : (
              <p className="empty">
                还没有逐秒指标。完成一次实时测量后，这里会显示 LAeq / LCeq 的 1 Hz 曲线。
              </p>
            )}
          </div>

          <div className="dashboard-columns">
            <div className="card">
              <h2>待处理事件</h2>
              {data.pending_events.length === 0 ? (
                <p className="empty">暂无待处理事件</p>
              ) : (
                <table className="table">
                  <caption className="sr-only">待处理噪音事件</caption>
                  <thead>
                    <tr>
                      <th scope="col">事件</th>
                      <th scope="col">测点 / 位置</th>
                      <th scope="col">创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.pending_events.map((event) => (
                      <tr key={event.id}>
                        <td>
                          <Link to="/events">{event.title}</Link>
                          {event.analysis_count > 0 ? (
                            <small className="hint"> · {event.analysis_count} 条分析</small>
                          ) : null}
                        </td>
                        <td>{event.measurement_point || event.location || '—'}</td>
                        <td>{formatDateTime(event.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="card">
              <h2>最近测量</h2>
              {data.recent_measurements.length === 0 ? (
                <p className="empty">暂无测量记录</p>
              ) : (
                <table className="table">
                  <caption className="sr-only">最近实时测量记录</caption>
                  <thead>
                    <tr>
                      <th scope="col">开始时间</th>
                      <th scope="col">设备</th>
                      <th scope="col">时长</th>
                      <th scope="col">指标点</th>
                      <th scope="col">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_measurements.map((item) => (
                      <tr key={item.id}>
                        <td>{formatDateTime(item.started_at)}</td>
                        <td title={item.device_label}>{item.device_label || '—'}</td>
                        <td>{formatDuration(item.duration_s)}</td>
                        <td>{item.point_count}</td>
                        <td>{MEASUREMENT_STATUS[item.status] ?? item.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="dashboard-columns">
            <div className="card">
              <h2>最近事件</h2>
              {data.recent_events.length === 0 ? (
                <p className="empty">暂无事件</p>
              ) : (
                <table className="table">
                  <caption className="sr-only">最近噪音事件</caption>
                  <thead>
                    <tr>
                      <th scope="col">事件</th>
                      <th scope="col">状态</th>
                      <th scope="col">分析</th>
                      <th scope="col">创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_events.map((event) => (
                      <tr key={event.id}>
                        <td>{event.title}</td>
                        <td>{EVENT_STATUS[event.status] ?? event.status}</td>
                        <td>{event.analysis_count}</td>
                        <td>{formatDateTime(event.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="card">
              <h2>系统摘要</h2>
              <dl className="kv-grid">
                <div>
                  <dt>数据目录</dt>
                  <dd title={data.system.data_dir}>{data.system.data_dir}</dd>
                </div>
                <div>
                  <dt>磁盘可用</dt>
                  <dd>{formatBytes(data.system.disk.free_bytes)}</dd>
                </div>
                <div>
                  <dt>音频文件</dt>
                  <dd>
                    {data.system.audio.count} 个 · {formatBytes(data.system.audio.bytes)}
                  </dd>
                </div>
                <div>
                  <dt>分析队列</dt>
                  <dd>
                    排队 {data.system.pending_runs} · 失败 {data.system.failed_runs}
                  </dd>
                </div>
                <div>
                  <dt>音频保留期</dt>
                  <dd>{data.system.retention_days > 0 ? `${data.system.retention_days} 天` : '未启用自动清理'}</dd>
                </div>
                <div>
                  <dt>分析线程</dt>
                  <dd>{data.system.analysis_workers}</dd>
                </div>
              </dl>
              <div className="actions" style={{ marginTop: 14 }}>
                <Link className="ghost" to="/upload">
                  上传音频分析
                </Link>
                <Link className="ghost" to="/events">
                  新建事件
                </Link>
                <Link className="ghost" to="/settings">
                  校准与存储设置
                </Link>
              </div>
            </div>
          </div>

          <p className="hint">数据生成于 {formatDateTime(data.generated_at)}。</p>
        </>
      )}
    </section>
  )
}
