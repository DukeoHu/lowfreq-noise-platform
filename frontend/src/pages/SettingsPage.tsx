import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import {
  clearCalibration,
  loadAccess,
  loadCalibration,
  loadPrivacy,
  saveAccess,
  savePrivacy,
  type AccessSettings,
  type PrivacySettings,
} from '../settings'
import type { LlmConfig, LlmUsage, SystemStatus } from '../types'

interface LlmForm {
  enabled: boolean
  base_url: string
  model: string
  api_key: string
  clear_api_key: boolean
  timeout_s: number
  max_tokens: number
  auto_insight: boolean
  redact_context: boolean
  api_key_env_only: boolean
  monthly_token_budget: number
}

const EMPTY_LLM_FORM: LlmForm = {
  enabled: false,
  base_url: '',
  model: '',
  api_key: '',
  clear_api_key: false,
  timeout_s: 60,
  max_tokens: 1500,
  auto_insight: false,
  redact_context: true,
  api_key_env_only: false,
  monthly_token_budget: 0,
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

const ITEMS: Array<{ key: keyof PrivacySettings; title: string; description: string }> = [
  {
    key: 'allowRecording',
    title: '允许录音与保存音频',
    description: '关闭时实时测量默认不写 WAV；每次开始测量仍可单独勾选授权。',
  },
  {
    key: 'allowLocation',
    title: '允许采集位置坐标',
    description: '开启后可在噪音事件中点击「获取当前位置」，把经纬度写入事件。',
  },
  {
    key: 'allowSharing',
    title: '允许导出/分享原始音频',
    description: '关闭时数据包导出不勾选原始音频，仅提供结果、CSV 与 PDF。',
  },
]

export default function SettingsPage() {
  const [privacy, setPrivacy] = useState<PrivacySettings>(() => loadPrivacy())
  const [profile, setProfile] = useState(() => loadCalibration())
  const [access, setAccess] = useState<AccessSettings>(() => loadAccess())
  const [accessSaved, setAccessSaved] = useState(false)
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [llmConfig, setLlmConfig] = useState<LlmConfig | null>(null)
  const [llmForm, setLlmForm] = useState<LlmForm>(EMPTY_LLM_FORM)
  const [llmSaving, setLlmSaving] = useState(false)
  const [llmTesting, setLlmTesting] = useState(false)
  const [llmMessage, setLlmMessage] = useState<string | null>(null)
  const [llmError, setLlmError] = useState<string | null>(null)
  const [llmUsage, setLlmUsage] = useState<LlmUsage | null>(null)

  useEffect(() => {
    api
      .getSystemStatus()
      .then(setStatus)
      .catch((error: unknown) =>
        setStatusError(error instanceof Error ? error.message : '无法读取服务端状态，请确认后端服务已启动。'),
      )
    api
      .getLlmConfig()
      .then((config) => {
        setLlmConfig(config)
        setLlmForm({
          enabled: config.enabled,
          base_url: config.base_url,
          model: config.model,
          api_key: '',
          clear_api_key: false,
          timeout_s: config.timeout_s,
          max_tokens: config.max_tokens,
          auto_insight: config.auto_insight,
          redact_context: config.redact_context,
          api_key_env_only: config.api_key_env_only,
          monthly_token_budget: config.monthly_token_budget,
        })
      })
      .catch((error: unknown) =>
        setLlmError(error instanceof Error ? error.message : '无法读取 LLM 配置'),
      )
    api
      .getLlmUsage()
      .then(setLlmUsage)
      .catch(() => undefined)
  }, [])

  const toggle = (key: keyof PrivacySettings) => {
    const next = { ...privacy, [key]: !privacy[key] }
    setPrivacy(next)
    savePrivacy(next)
  }

  const persistAccess = () => {
    saveAccess(access)
    setAccessSaved(true)
  }

  const persistLlm = async () => {
    setLlmSaving(true)
    setLlmError(null)
    setLlmMessage(null)
    try {
      const saved = await api.saveLlmConfig({
        enabled: llmForm.enabled,
        base_url: llmForm.base_url.trim(),
        model: llmForm.model.trim(),
        api_key: llmForm.api_key.trim() || undefined,
        clear_api_key: llmForm.clear_api_key,
        timeout_s: llmForm.timeout_s,
        max_tokens: llmForm.max_tokens,
        auto_insight: llmForm.auto_insight,
        redact_context: llmForm.redact_context,
        api_key_env_only: llmForm.api_key_env_only,
        monthly_token_budget: llmForm.monthly_token_budget,
      })
      setLlmConfig(saved)
      setLlmForm((prev) => ({ ...prev, api_key: '', clear_api_key: false }))
      setLlmMessage('已保存并立即生效')
      api.getLlmUsage().then(setLlmUsage).catch(() => undefined)
    } catch (error) {
      setLlmError(error instanceof Error ? error.message : '保存 LLM 配置失败')
    } finally {
      setLlmSaving(false)
    }
  }

  const testLlm = async () => {
    setLlmTesting(true)
    setLlmError(null)
    setLlmMessage(null)
    try {
      const result = await api.testLlm()
      setLlmMessage(`连接成功（${result.model}）：${result.reply}`)
    } catch (error) {
      setLlmError(error instanceof Error ? error.message : '连接测试失败')
    } finally {
      setLlmTesting(false)
    }
  }

  const llmSourceLabel =
    llmConfig?.source === 'db'
      ? '设置页配置'
      : llmConfig?.source === 'env'
        ? '环境变量 LOWFREQ_LLM_*'
        : '未配置'

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>隐私与校准</h1>
          <p className="subtitle">
            访问口令为内网共享口令，操作人用于审计留痕；隐私授权与校准档案仅保存在当前浏览器，默认全部关闭。
          </p>
        </div>
      </header>

      <div className="card">
        <h2>访问口令与操作人</h2>
        <p className="hint">
          内网共享口令由服务端 <code>LOWFREQ_ACCESS_TOKEN</code> 配置；服务端未设置时留空即可。
          口令与操作人仅保存在当前浏览器，请求头与实时连接的查询参数会自动携带，操作人会写入审计日志。
        </p>
        <label className="field" style={{ marginTop: 12 }}>
          <span>访问口令</span>
          <input
            type="password"
            value={access.token}
            autoComplete="off"
            placeholder="服务端未开启鉴权时留空"
            onChange={(event) => {
              setAccess({ ...access, token: event.target.value })
              setAccessSaved(false)
            }}
          />
        </label>
        <label className="field" style={{ marginTop: 12 }}>
          <span>操作人</span>
          <input
            type="text"
            value={access.actor}
            maxLength={64}
            placeholder="例如：张三（留空记为 local）"
            onChange={(event) => {
              setAccess({ ...access, actor: event.target.value })
              setAccessSaved(false)
            }}
          />
        </label>
        <div className="actions" style={{ marginTop: 12 }}>
          <button type="button" onClick={persistAccess}>
            保存访问设置
          </button>
          {accessSaved && <span className="hint">已保存到当前浏览器</span>}
        </div>
      </div>

      <div className="card">
        <h2>AI 辅助解读</h2>
        <p className="hint">
          兼容 OpenAI Chat Completions 协议的云厂商或内网网关（如 DeepSeek、通义千问兼容模式）。
          配置保存在服务端数据库并优先于环境变量，保存后立即生效；API Key 不会回传浏览器。
        </p>
        <div className="inline-form" style={{ marginTop: 12 }}>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={llmForm.enabled}
              onChange={(event) => setLlmForm({ ...llmForm, enabled: event.target.checked })}
            />
            <span>启用 AI 解读</span>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={llmForm.auto_insight}
              onChange={(event) => setLlmForm({ ...llmForm, auto_insight: event.target.checked })}
            />
            <span>分析成功后自动生成</span>
          </label>
        </div>
        <div className="inline-form" style={{ marginTop: 8 }}>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={llmForm.redact_context}
              onChange={(event) => setLlmForm({ ...llmForm, redact_context: event.target.checked })}
            />
            <span>摘要脱敏（不发送文件名、位置、备注、设备）</span>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={llmForm.api_key_env_only}
              onChange={(event) =>
                setLlmForm({ ...llmForm, api_key_env_only: event.target.checked })
              }
            />
            <span>API Key 仅保存在环境变量</span>
          </label>
        </div>
        <p className="hint">
          脱敏默认开启；勾选“仅环境变量”后服务端不会把密钥写入数据库，需在启动前设置
          LOWFREQ_LLM_API_KEY。
        </p>
        <label className="field" style={{ marginTop: 12 }}>
          <span>Base URL</span>
          <input
            type="text"
            value={llmForm.base_url}
            placeholder="https://api.deepseek.com/v1"
            onChange={(event) => setLlmForm({ ...llmForm, base_url: event.target.value })}
          />
        </label>
        <label className="field" style={{ marginTop: 12 }}>
          <span>模型</span>
          <input
            type="text"
            value={llmForm.model}
            placeholder="deepseek-chat"
            onChange={(event) => setLlmForm({ ...llmForm, model: event.target.value })}
          />
        </label>
        <label className="field" style={{ marginTop: 12 }}>
          <span>API Key</span>
          <input
            type="password"
            autoComplete="off"
            disabled={llmForm.api_key_env_only}
            value={llmForm.api_key}
            placeholder={
              llmForm.api_key_env_only
                ? '已启用仅环境变量模式，请设置 LOWFREQ_LLM_API_KEY'
                : llmConfig?.has_api_key
                  ? '已配置，留空保持不变'
                  : '输入云厂商 API Key'
            }
            onChange={(event) => setLlmForm({ ...llmForm, api_key: event.target.value })}
          />
        </label>
        {llmConfig?.has_api_key && (
          <label className="checkbox settings-row">
            <input
              type="checkbox"
              checked={llmForm.clear_api_key}
              onChange={(event) => setLlmForm({ ...llmForm, clear_api_key: event.target.checked })}
            />
            <span>
              <strong>清除已保存的密钥</strong>
              <span className="hint" style={{ display: 'block' }}>
                勾选后保存会删除服务端保存的 API Key。
              </span>
            </span>
          </label>
        )}
        <div className="inline-form" style={{ marginTop: 12 }}>
          <label className="field">
            <span>超时（秒）</span>
            <input
              type="number"
              min={1}
              max={600}
              value={llmForm.timeout_s}
              onChange={(event) =>
                setLlmForm({ ...llmForm, timeout_s: Math.max(1, Number(event.target.value) || 1) })
              }
            />
          </label>
          <label className="field">
            <span>最大输出 tokens</span>
            <input
              type="number"
              min={64}
              max={8192}
              value={llmForm.max_tokens}
              onChange={(event) =>
                setLlmForm({ ...llmForm, max_tokens: Math.max(64, Number(event.target.value) || 64) })
              }
            />
          </label>
          <label className="field">
            <span>月度 token 预算（0 为不限额）</span>
            <input
              type="number"
              min={0}
              value={llmForm.monthly_token_budget}
              onChange={(event) =>
                setLlmForm({
                  ...llmForm,
                  monthly_token_budget: Math.max(0, Number(event.target.value) || 0),
                })
              }
            />
          </label>
        </div>
        {llmUsage && (
          <p className="hint">
            本月（{llmUsage.month}）已用 {llmUsage.total_tokens} tokens，{llmUsage.requests} 次解读
            {llmUsage.budget > 0
              ? ` · 预算 ${llmUsage.budget}（${llmUsage.used_percent ?? 0}%）· 剩余 ${llmUsage.remaining_tokens ?? 0}`
              : ' · 未设置预算'}
            {llmUsage.exceeded ? ' · 已达上限，新的解读会被拒绝' : ''}
          </p>
        )}
        <p className="hint">
          推理模型（如 deepseek-flash / reasoner）会把思考 tokens 计入该上限，建议不低于 3000；
          输出被截断时服务端会自动按 2 倍加预算重试（上限 8192），仍失败可在「分析页」查看错误详情。
        </p>
        <div className="actions" style={{ marginTop: 12 }}>
          <button type="button" onClick={() => void persistLlm()} disabled={llmSaving}>
            {llmSaving ? '保存中…' : '保存 LLM 配置'}
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() => void testLlm()}
            disabled={llmTesting || !llmConfig?.has_api_key}
          >
            {llmTesting ? '测试中…' : '测试连接'}
          </button>
          <span className="hint">当前生效来源：{llmSourceLabel}</span>
        </div>
        {llmError && <p className="alert" role="alert">{llmError}</p>}
        {llmMessage && <p className="hint">{llmMessage}</p>}
        <p className="hint">提示：先保存再测试，测试会向模型发送一次很小的请求。</p>
      </div>

      <div className="card">
        <h2>隐私授权</h2>
        {ITEMS.map((item) => (
          <label className="checkbox settings-row" key={item.key}>
            <input type="checkbox" checked={privacy[item.key]} onChange={() => toggle(item.key)} />
            <span>
              <strong>{item.title}</strong>
              <span className="hint" style={{ display: 'block' }}>
                {item.description}
              </span>
            </span>
          </label>
        ))}
      </div>

      <div className="card">
        <h2>单点校准档案</h2>
        {profile ? (
          <>
            <p>
              修正量 <strong>{profile.offsetDb >= 0 ? '+' : ''}{profile.offsetDb.toFixed(2)} dB</strong>，
              校准器 {profile.level} dB @ 1 kHz，测量读数 {profile.measuredLaeq.toFixed(1)} dB，
              记录于 {new Date(profile.savedAt).toLocaleString()}。
            </p>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                clearCalibration()
                setProfile(null)
              }}
            >
              清除校准档案
            </button>
          </>
        ) : (
          <p className="hint">尚未校准。请到<Link to="/realtime">实时测量</Link>页使用 94/114 dB @ 1 kHz 校准器完成单点校准。</p>
        )}
      </div>

      <div className="card">
        <h2>服务端存储与运行状态</h2>
        {statusError && <p className="hint">{statusError}</p>}
        {status && (
          <>
            {status.disk.warning && (
              <p className="alert" role="alert">
                磁盘使用已达 {status.disk.used_percent}%，建议清理旧音频或调整保留期。
              </p>
            )}
            <div className="detail-grid">
              <div>
                <span className="metric-label">磁盘使用</span>
                <p>
                  {status.disk.used_percent}% · 剩余 {formatBytes(status.disk.free_bytes)}
                </p>
              </div>
              <div>
                <span className="metric-label">音频文件</span>
                <p>
                  {status.audio.count} 个 · {formatBytes(status.audio.bytes)}
                </p>
              </div>
              <div>
                <span className="metric-label">现场照片</span>
                <p>{status.photos.count} 张</p>
              </div>
              <div>
                <span className="metric-label">分析结果</span>
                <p>
                  {status.results.count} 份 · {formatBytes(status.results.bytes)}
                </p>
              </div>
              <div>
                <span className="metric-label">任务队列</span>
                <p>
                  排队/运行 {status.pending_runs} · 失败 {status.failed_runs}
                </p>
              </div>
              <div>
                <span className="metric-label">音频保留期</span>
                <p>{status.retention_days > 0 ? `${status.retention_days} 天` : '不自动清理'}</p>
              </div>
            </div>
            <p className="hint" style={{ marginTop: 12 }}>
              数据目录 {status.data_dir}。保留期由服务端 <code>LOWFREQ_AUDIO_RETENTION_DAYS</code> 控制，
              已关联事件的音频不参与自动清理；导出与删除动作会写入<Link to="/audit">审计日志</Link>。
            </p>
          </>
        )}
      </div>
    </section>
  )
}
