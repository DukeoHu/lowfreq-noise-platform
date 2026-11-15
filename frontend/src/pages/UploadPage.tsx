import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { loadCalibration } from '../settings'
import type { AnalysisConfigInput } from '../types'

export default function UploadPage() {
  const navigate = useNavigate()
  const savedCalibration = loadCalibration()
  const [file, setFile] = useState<File | null>(null)
  const [frameS, setFrameS] = useState(1)
  const [hopS, setHopS] = useState(0.5)
  const [calibrated, setCalibrated] = useState(Boolean(savedCalibration))
  const [offsetDb, setOffsetDb] = useState(savedCalibration?.offsetDb ?? 0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!file) {
      setError('请先选择音频文件')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const audio = await api.uploadAudio(file)
      const config: AnalysisConfigInput = {
        frame_s: frameS,
        hop_s: hopS,
        calibration: {
          mode: calibrated ? 'calibrated' : 'uncalibrated',
          offset_db: Number(offsetDb),
          full_scale_pa: 1.0,
        },
      }
      const run = await api.createAnalysis(audio.id, config)
      navigate(`/analyses/${run.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>测量分析</h1>
          <p className="subtitle">上传历史录音，由 Python 服务执行可复现 DSP 计算，输出权威指标与可下载数据。</p>
        </div>
      </header>

      <form className="card form" onSubmit={handleSubmit}>
        <label className="field">
          <span>音频文件（WAV / FLAC / OGG / MP3）</span>
          <input
            type="file"
            accept=".wav,.flac,.ogg,.mp3,audio/*"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          {file && (
            <small className="hint">
              {file.name} · {(file.size / 1024 / 1024).toFixed(2)} MB
            </small>
          )}
        </label>

        <div className="grid-2">
          <label className="field">
            <span>时间序列帧长（秒）</span>
            <input
              type="number"
              min={0.1}
              max={60}
              step={0.1}
              value={frameS}
              onChange={(event) => setFrameS(Number(event.target.value))}
            />
          </label>
          <label className="field">
            <span>步长（秒）</span>
            <input
              type="number"
              min={0.05}
              max={60}
              step={0.05}
              value={hopS}
              onChange={(event) => setHopS(Number(event.target.value))}
            />
          </label>
        </div>

        <fieldset className="fieldset">
          <legend>校准</legend>
          <label className="checkbox">
            <input type="checkbox" checked={calibrated} onChange={(event) => setCalibrated(event.target.checked)} />
            <span>使用已校准麦克风</span>
          </label>
          <label className="field">
            <span>校准修正量（dB）</span>
            <input
              type="number"
              step={0.1}
              value={offsetDb}
              disabled={!calibrated}
              onChange={(event) => setOffsetDb(Number(event.target.value))}
            />
          </label>
          {savedCalibration ? (
            <p className="hint">
              已读取本机校准档案：{savedCalibration.offsetDb >= 0 ? '+' : ''}
              {savedCalibration.offsetDb.toFixed(2)} dB（{savedCalibration.level} dB @ 1 kHz，
              {new Date(savedCalibration.savedAt).toLocaleDateString()}）。可在
              <Link to="/realtime">实时测量</Link>页重新校准。
            </p>
          ) : null}
          {!calibrated && (
            <p className="hint warn">未校准时结果为相对值，报告中会标注 <code>uncalibrated</code> 质量标志。</p>
          )}
        </fieldset>

        {error && <p className="alert" role="alert">{error}</p>}

        <button type="submit" className="primary" disabled={busy}>
          {busy ? '上传并提交分析…' : '上传并开始分析'}
        </button>
      </form>
    </section>
  )
}
