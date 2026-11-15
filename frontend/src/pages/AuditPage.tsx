import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { AuditLogEntry } from '../types'

const PAGE_SIZE = 50

const ACTION_LABELS: Record<string, string> = {
  'audio.upload': '上传音频',
  'audio.delete': '删除音频',
  'analysis.create': '创建分析',
  'analysis.stage': '标记整改阶段',
  'analysis.export': '导出数据',
  'analysis.retry': '重试分析',
  'analysis.cancel': '取消分析',
  'event.create': '新建事件',
  'event.update': '修改事件',
  'event.link_analysis': '关联分析',
  'event.unlink_analysis': '解除关联',
  'event.photo_upload': '上传照片',
  'event.photo_delete': '删除照片',
  'realtime.save': '实时录音归档',
}

function formatDetail(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail).filter(([, value]) => value !== null && value !== '')
  if (entries.length === 0) return '—'
  return entries
    .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join('，')
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [action, setAction] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setBusy(true)
    setError(null)
    try {
      const data = await api.listAuditLogs({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        action: action || undefined,
        since: since || undefined,
        until: until || undefined,
      })
      setEntries(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载审计日志失败')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load()
  }, [page, action, since, until])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>审计日志</h1>
          <p className="subtitle">
            记录采集归档、分析创建/重试/取消、整改阶段变更、事件与照片变更以及所有导出动作，便于追溯结果来源。
          </p>
        </div>
        <div className="actions">
          <button type="button" className="ghost" onClick={() => void load()} disabled={busy}>
            {busy ? '刷新中…' : '刷新'}
          </button>
        </div>
      </header>

      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}

      <div className="card">
        <div className="inline-form" style={{ marginBottom: 12 }}>
          <label className="field">
            <span>动作</span>
            <select
              value={action}
              onChange={(event) => {
                setAction(event.target.value)
                setPage(0)
              }}
            >
              <option value="">全部动作</option>
              {Object.entries(ACTION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>开始时间</span>
            <input
              type="datetime-local"
              value={since}
              onChange={(event) => {
                setSince(event.target.value)
                setPage(0)
              }}
            />
          </label>
          <label className="field">
            <span>结束时间</span>
            <input
              type="datetime-local"
              value={until}
              onChange={(event) => {
                setUntil(event.target.value)
                setPage(0)
              }}
            />
          </label>
        </div>

        {entries.length === 0 ? (
          <p className="empty">{busy ? '正在加载…' : '暂无审计记录'}</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>时间</th>
                <th>操作人</th>
                <th>动作</th>
                <th>对象</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>{new Date(entry.created_at).toLocaleString()}</td>
                  <td>{entry.actor || 'local'}</td>
                  <td>{ACTION_LABELS[entry.action] ?? entry.action}</td>
                  <td>
                    {entry.entity_type || '—'}
                    {entry.entity_id ? <span className="hint"> / {entry.entity_id.slice(0, 8)}</span> : null}
                  </td>
                  <td className="hint">{formatDetail(entry.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="row-actions" style={{ marginTop: 12, justifyContent: 'space-between' }}>
          <span className="hint">
            第 {page + 1} / {pageCount} 页 · 共 {total} 条
          </span>
          <div className="row-actions">
            <button type="button" className="ghost" disabled={busy || page === 0} onClick={() => setPage((value) => value - 1)}>
              上一页
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy || (page + 1) * PAGE_SIZE >= total}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
