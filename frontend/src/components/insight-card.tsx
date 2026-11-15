import { useEffect, useState } from 'react'

import type { Insight, InsightFeedbackRating, InsightVersion } from '../types'

const CONFIDENCE_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

const FEEDBACK_OPTIONS: { value: InsightFeedbackRating; label: string }[] = [
  { value: 'useful', label: '有用' },
  { value: 'not_useful', label: '无用' },
  { value: 'adopted', label: '已采纳' },
]

interface InsightCardProps {
  title?: string
  insight: Insight | null
  busy: boolean
  error: string | null
  disabled?: boolean
  history?: InsightVersion[]
  onGenerate: (force?: boolean) => void
  onCancel?: () => void
  onFeedback?: (rating: InsightFeedbackRating) => void
}

export function InsightCard({
  title = 'AI 辅助解读',
  insight,
  busy,
  error,
  disabled = false,
  history = [],
  onGenerate,
  onCancel,
  onFeedback,
}: InsightCardProps) {
  const running = insight?.status === 'queued' || insight?.status === 'running'
  const content = insight?.content ?? null
  const cancelled = insight?.status === 'cancelled'

  // 等待计时：不依赖服务端时间戳，避免时区差异导致显示异常
  const [waitStart, setWaitStart] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) {
      setWaitStart(null)
      return
    }
    setWaitStart((prev) => prev ?? Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  const waitedSeconds = running && waitStart ? Math.max(0, Math.floor((now - waitStart) / 1000)) : 0

  const warnings = content?.validation_warnings ?? []
  const feedback = insight?.feedback ?? ''

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row-actions" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <p className="hint" style={{ margin: '4px 0 0' }}>
            由大模型基于上述指标生成，仅用于排查参考，不构成检测、执法或验收结论。
          </p>
        </div>
        <div className="row-actions">
          {running && onCancel && (
            <button type="button" className="ghost" onClick={onCancel}>
              取消
            </button>
          )}
          <button
            type="button"
            className="ghost"
            disabled={disabled || busy || running}
            onClick={() => onGenerate(Boolean(content))}
          >
            {running ? `生成中… ${waitedSeconds}s` : content ? '重新生成' : '生成解读'}
          </button>
        </div>
      </div>

      {error && (
        <p className="alert" role="alert">{error}</p>
      )}

      {insight?.status === 'failed' && (
        <p className="alert" role="alert">生成失败：{insight.error ?? '未知错误'}</p>
      )}

      {cancelled && (
        <p className="hint">本次生成已取消，可随时重新生成。</p>
      )}

      {running && (
        <p className="empty">
          正在请求大模型，已等待 {waitedSeconds} 秒，通常需要 10–40 秒…
        </p>
      )}

      {content && (
        <div>
          <p>{content.summary}</p>
          {content.findings.length > 0 && (
            <ul>
              {content.findings.map((finding, index) => (
                <li key={`${finding.title}-${index}`}>
                  <strong>{finding.title}</strong>
                  {finding.detail ? `：${finding.detail}` : ''}
                  {finding.evidence && (
                    <div className="hint">依据：{finding.evidence}</div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {content.recommendations.length > 0 && (
            <>
              <p><strong>排查与整改建议</strong></p>
              <ol>
                {content.recommendations.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ol>
            </>
          )}
          {content.caveats.length > 0 && (
            <ul className="hint">
              {content.caveats.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          )}
          {warnings.length > 0 && (
            <div className="alert" role="alert">
              <strong>数值校验提示</strong>
              <ul style={{ margin: '4px 0 0' }}>
                {warnings.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="hint">
            模型 {insight?.model} · 置信度 {CONFIDENCE_LABELS[content.confidence] ?? content.confidence}
            {content.confidence_basis ? `（${content.confidence_basis}）` : ''} · 提示词 {insight?.prompt_version}
          </p>
          <p className="hint">
            {typeof insight?.total_tokens === 'number' && insight.total_tokens > 0
              ? `本次用量 ${insight.total_tokens} tokens`
              : '模型未返回用量'}
            {typeof insight?.elapsed_ms === 'number' && insight.elapsed_ms > 0
              ? ` · 耗时 ${(insight.elapsed_ms / 1000).toFixed(1)}s`
              : ''}
            {insight?.cache_hit ? ' · 命中缓存，未重复调用模型' : ''}
          </p>

          {onFeedback && (
            <div className="row-actions" style={{ alignItems: 'center' }}>
              <span className="hint">这条解读：</span>
              {FEEDBACK_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={feedback === option.value ? 'primary' : 'ghost'}
                  disabled={busy}
                  onClick={() => onFeedback(option.value)}
                >
                  {option.label}
                </button>
              ))}
              {feedback && <span className="hint">已记录</span>}
            </div>
          )}

          {history.length > 1 && (
            <details style={{ marginTop: 8 }}>
              <summary className="hint">历史版本（{history.length}）</summary>
              <ul className="hint">
                {history.map((version) => (
                  <li key={version.id}>
                    {new Date(version.created_at).toLocaleString()} · {version.model} ·{' '}
                    {version.content
                      ? `置信度 ${CONFIDENCE_LABELS[version.content.confidence] ?? version.content.confidence}`
                      : '无内容'}
                    {version.total_tokens > 0 ? ` · ${version.total_tokens} tokens` : ''}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {!insight && !error && !disabled && (
        <p className="hint">
          尚未生成。点击“生成解读”后，AI 会结合低频峰值、C−A 差值与数据质量给出排查建议。
        </p>
      )}

      {!insight && disabled && (
        <p className="hint">需要至少一条已完成的分析结果后才能生成。</p>
      )}
    </div>
  )
}
