import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api, realtimeWsUrl } from '../api/client'
import { BandChart, FftChart, LiveSpectrogram } from '../components/charts'
import { MetricLabel } from '../components/metric-label'
import {
  clearCalibration,
  loadCalibration,
  loadPrivacy,
  saveCalibration,
  type CalibrationProfile,
} from '../settings'
import type { AnalysisStage, RealtimeFrame, RealtimeSegment } from '../types'

const BLOCK_SIZE = 4096
// 4096 样本 @48 kHz ≈ 85 ms/块，600 块约 51 s，覆盖文档要求的 30–60 s 断网缓冲。
const MAX_BUFFER_CHUNKS = 600
const MAX_SPECTROGRAM_COLUMNS = 240
const MAX_RECONNECT_ATTEMPTS = 5

type Status =
  | 'idle'
  | 'starting'
  | 'running'
  | 'reconnecting'
  | 'saving'
  | 'saved'
  | 'stopped'
  | 'error'

interface BufferedChunk {
  seq: number
  dropped: number
  t: number
  buffer: ArrayBuffer
}

const STATUS_TEXT: Record<Status, string> = {
  idle: '未开始',
  starting: '正在启动麦克风…',
  running: '测量中',
  reconnecting: '连接中断，正在重连…',
  saving: '正在保存并分析…',
  saved: '已保存，分析已提交',
  stopped: '已停止（未保存）',
  error: '出错',
}

const STAGE_TEXT: Record<AnalysisStage, string> = {
  before: '整改前 ON',
  off: '关机 OFF',
  after: '整改后 ON',
}

function formatLevel(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} dB`
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(total / 60)
  return `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function mergeSegments(previous: RealtimeSegment[], incoming: RealtimeSegment[]): RealtimeSegment[] {
  const merged = new Map(previous.map((item) => [item.run_id, item]))
  for (const item of incoming) merged.set(item.run_id, item)
  return Array.from(merged.values()).sort((a, b) => a.segment - b.segment)
}

function describeMediaError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return '麦克风权限被拒绝。请点击地址栏左侧的麦克风图标允许本页使用麦克风，然后点击「重新开始测量」。'
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return '未找到可用麦克风，或选择的设备已断开，请刷新设备列表后重试。'
  }
  if (name === 'NotReadableError') {
    return '麦克风被其他程序占用，请关闭占用程序后重试。'
  }
  return error instanceof Error ? error.message : '无法启动麦克风，请检查浏览器权限。'
}

export function RealtimePage() {
  const [searchParams] = useSearchParams()
  const linkedEventId = searchParams.get('event') ?? ''
  const stageParam = searchParams.get('stage')
  const linkedStage: AnalysisStage | null =
    stageParam === 'before' || stageParam === 'off' || stageParam === 'after' ? stageParam : null
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [frame, setFrame] = useState<RealtimeFrame | null>(null)
  const [savedRuns, setSavedRuns] = useState<RealtimeSegment[]>([])
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [deviceId, setDeviceId] = useState('')
  const [saveAudio, setSaveAudio] = useState(() => loadPrivacy().allowRecording)
  const [dropped, setDropped] = useState(0)
  const [bufferedCount, setBufferedCount] = useState(0)
  const [reconnectCount, setReconnectCount] = useState(0)
  const [sampleRate, setSampleRate] = useState<number | null>(null)
  const [profile, setProfile] = useState<CalibrationProfile | null>(() => loadCalibration())
  const [calibratorLevel, setCalibratorLevel] = useState(() => loadCalibration()?.level ?? 94)
  const [spectro, setSpectro] = useState<{ f: number[]; t: number[]; columns: number[][] }>({
    f: [],
    t: [],
    columns: [],
  })

  const socketRef = useRef<WebSocket | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const contextRef = useRef<AudioContext | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const wantRunningRef = useRef(false)
  const manualStopRef = useRef(false)
  const sessionRef = useRef('')
  const segmentRef = useRef(0)
  const bufferRef = useRef<BufferedChunk[]>([])
  const reconnectTimerRef = useRef<number | null>(null)
  const attemptRef = useRef(0)
  const saveAudioRef = useRef(saveAudio)
  const profileRef = useRef(profile)
  const sampleRateRef = useRef(48000)
  const deviceLabelRef = useRef('')
  const constraintsRef = useRef<MediaTrackConstraints | null>(null)
  const spectroRef = useRef<{ f: number[]; t: number[]; columns: number[][] }>({ f: [], t: [], columns: [] })

  const clearReconnectTimer = () => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
  }

  const closeSocket = useCallback(() => {
    const socket = socketRef.current
    if (!socket) return
    socketRef.current = null
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    try {
      socket.close()
    } catch {
      /* 已关闭 */
    }
  }, [])

  const releaseCapture = useCallback(() => {
    const node = nodeRef.current
    if (node) {
      node.port.onmessage = null
      node.port.close()
      node.disconnect()
      nodeRef.current = null
    }
    sourceRef.current?.disconnect()
    sourceRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    const context = contextRef.current
    if (context) {
      void context.close()
      contextRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      wantRunningRef.current = false
      manualStopRef.current = true
      clearReconnectTimer()
      closeSocket()
      releaseCapture()
    }
  }, [closeSocket, releaseCapture])

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return
    try {
      const all = await navigator.mediaDevices.enumerateDevices()
      const inputs = all.filter((device) => device.kind === 'audioinput')
      setDevices(inputs)
      setDeviceId((current) => current || inputs[0]?.deviceId || '')
    } catch {
      /* 设备枚举失败时保留手动输入 */
    }
  }, [])

  useEffect(() => {
    void refreshDevices()
  }, [refreshDevices])

  const rememberSpectrogram = (payload: RealtimeFrame) => {
    if (!payload.spectrum || payload.spectrum.f.length === 0) return
    const store = spectroRef.current
    if (store.f.length !== payload.spectrum.f.length) {
      store.f = payload.spectrum.f
      store.t = []
      store.columns = []
    }
    store.t.push(payload.t)
    store.columns.push(payload.spectrum.level)
    if (store.columns.length > MAX_SPECTROGRAM_COLUMNS) {
      store.columns.shift()
      store.t.shift()
    }
    setSpectro({ f: store.f, t: store.t.slice(), columns: store.columns.slice() })
  }

  const openSocket = useCallback(() => {
    const socket = new WebSocket(realtimeWsUrl())
    socket.binaryType = 'arraybuffer'
    socketRef.current = socket

    socket.onopen = () => {
      attemptRef.current = 0
      socket.send(
        JSON.stringify({
          type: 'hello',
          sample_rate: sampleRateRef.current,
          save_audio: saveAudioRef.current,
          offset_db: profileRef.current?.offsetDb ?? 0,
          session_id: sessionRef.current,
          segment: segmentRef.current,
          device_label: deviceLabelRef.current,
          constraints: constraintsRef.current ?? {},
        }),
      )
    }

    socket.onmessage = (event) => {
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (payload.type === 'ready') {
        setSampleRate(Number(payload.sample_rate ?? sampleRateRef.current))
        setStatus('running')
        const segments = Array.isArray(payload.segments) ? (payload.segments as RealtimeSegment[]) : []
        if (segments.length > 0) setSavedRuns((previous) => mergeSegments(previous, segments))
        const pending = bufferRef.current
        bufferRef.current = []
        setBufferedCount(0)
        for (const item of pending) {
          if (socket.readyState !== WebSocket.OPEN) break
          socket.send(JSON.stringify({ type: 'chunk', seq: item.seq, dropped: item.dropped, t: item.t }))
          socket.send(item.buffer)
        }
        return
      }
      if (payload.type === 'frame') {
        const next = payload as unknown as RealtimeFrame
        setFrame(next)
        rememberSpectrogram(next)
        return
      }
      if (payload.type === 'saved') {
        const saved = payload as unknown as RealtimeSegment
        setSavedRuns((previous) => mergeSegments(previous, [saved]))
        wantRunningRef.current = false
        manualStopRef.current = true
        closeSocket()
        releaseCapture()
        setStatus('saved')
        if (linkedEventId && linkedStage) {
          void api.linkAnalysis(linkedEventId, saved.run_id, linkedStage).catch((linkError: unknown) => {
            setError(linkError instanceof Error ? linkError.message : '自动关联事件失败，请到事件页手动关联')
          })
        }
        return
      }
      if (payload.type === 'stopped') {
        wantRunningRef.current = false
        closeSocket()
        setStatus('stopped')
        return
      }
      if (payload.type === 'discarded') {
        setError(String(payload.detail ?? '本次录音未保存'))
        setStatus('stopped')
        return
      }
      if (payload.type === 'error') {
        wantRunningRef.current = false
        manualStopRef.current = true
        clearReconnectTimer()
        closeSocket()
        releaseCapture()
        setError(String(payload.detail ?? '实时连接出错'))
        setStatus('error')
      }
    }

    socket.onerror = () => {
      /* onclose 统一处理重连 */
    }

    socket.onclose = () => {
      if (socketRef.current !== socket) return
      socketRef.current = null
      if (!wantRunningRef.current || manualStopRef.current) return

      attemptRef.current += 1
      if (attemptRef.current > MAX_RECONNECT_ATTEMPTS) {
        wantRunningRef.current = false
        releaseCapture()
        setError('实时连接多次中断，已停止采集。请检查网络或后端服务后重新开始。')
        setStatus('error')
        return
      }
      setReconnectCount((count) => count + 1)
      setStatus('reconnecting')
      const delay = Math.min(4000, 1000 * 2 ** (attemptRef.current - 1))
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null
        if (!wantRunningRef.current) return
        segmentRef.current += 1
        openSocket()
      }, delay)
    }
  }, [closeSocket, linkedEventId, linkedStage, releaseCapture])

  const handleStart = async () => {
    setError(null)
    setFrame(null)
    setSavedRuns([])
    setDropped(0)
    setBufferedCount(0)
    setReconnectCount(0)
    setSpectro({ f: [], t: [], columns: [] })
    spectroRef.current = { f: [], t: [], columns: [] }
    bufferRef.current = []
    attemptRef.current = 0
    segmentRef.current = 0
    sessionRef.current = crypto.randomUUID()
    wantRunningRef.current = true
    manualStopRef.current = false
    saveAudioRef.current = saveAudio
    profileRef.current = profile
    setStatus('starting')

    try {
      const constraints: MediaTrackConstraints = {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
      }
      if (deviceId) constraints.deviceId = { exact: deviceId }
      constraintsRef.current = constraints

      const stream = await navigator.mediaDevices.getUserMedia({ audio: constraints, video: false })
      streamRef.current = stream
      deviceLabelRef.current = stream.getAudioTracks()[0]?.label ?? ''
      void refreshDevices()

      const context = new AudioContext()
      contextRef.current = context
      await context.audioWorklet.addModule('/realtime-worklet.js')
      if (context.state === 'suspended') await context.resume()
      sampleRateRef.current = context.sampleRate
      setSampleRate(context.sampleRate)

      const node = new AudioWorkletNode(context, 'realtime-capture', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { bufferSize: BLOCK_SIZE },
      })
      nodeRef.current = node
      node.port.onmessage = (event) => {
        const data = event.data as { seq: number; dropped: number; t: number; buffer: ArrayBuffer }
        setDropped(data.dropped ?? 0)
        const socket = socketRef.current
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'chunk', seq: data.seq, dropped: data.dropped, t: data.t }))
          socket.send(data.buffer)
        } else if (wantRunningRef.current && bufferRef.current.length < MAX_BUFFER_CHUNKS) {
          bufferRef.current.push({ seq: data.seq, dropped: data.dropped, t: data.t, buffer: data.buffer })
          setBufferedCount(bufferRef.current.length)
        }
        node.port.postMessage({ type: 'ack', seq: data.seq })
      }

      const source = context.createMediaStreamSource(stream)
      sourceRef.current = source
      const gain = context.createGain()
      gain.gain.value = 0
      source.connect(node)
      node.connect(gain)
      gain.connect(context.destination)

      openSocket()
    } catch (mediaError) {
      wantRunningRef.current = false
      releaseCapture()
      closeSocket()
      setError(describeMediaError(mediaError))
      setStatus('error')
    }
  }

  const handleStop = () => {
    wantRunningRef.current = false
    manualStopRef.current = true
    clearReconnectTimer()
    const socket = socketRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      setStatus('saving')
      socket.send(JSON.stringify({ type: 'stop' }))
      releaseCapture()
      return
    }
    closeSocket()
    releaseCapture()
    setStatus('stopped')
    if (saveAudioRef.current) {
      setError('连接已断开，当前分段可能已在服务端自动归档，可在「测量分析」页查看记录。')
    }
  }

  const applyCalibration = () => {
    const measured = frame?.cumulative.laeq
    if (measured === null || measured === undefined) return
    const offsetDb = Math.round((calibratorLevel - measured) * 100) / 100
    const next: CalibrationProfile = {
      offsetDb,
      level: calibratorLevel,
      measuredLaeq: measured,
      savedAt: new Date().toISOString(),
    }
    saveCalibration(next)
    setProfile(next)
    profileRef.current = next
  }

  const removeCalibration = () => {
    clearCalibration()
    setProfile(null)
    profileRef.current = null
  }

  const busy = status === 'starting' || status === 'saving'
  const capturing = status === 'running' || status === 'reconnecting' || status === 'starting' || status === 'saving'
  const canStop = status === 'running' || status === 'reconnecting'
  const levels = frame?.levels
  const cumulative = frame?.cumulative

  return (
    <section>
      <header className="page-header">
        <div>
          <h1>实时测量</h1>
          <p className="subtitle">
            浏览器采集麦克风音频，服务端逐帧给出 LAeq/LCeq/LZeq、快速计权、峰值与频谱。停止后可归档并自动生成离线分析。
          </p>
        </div>
      </header>

      <div className="card">
        <div className="realtime-bar">
          <span className={`status-dot ${status}`} aria-hidden="true" />
          <strong>{STATUS_TEXT[status]}</strong>
          <span className="hint">时长 {formatDuration(cumulative?.duration_s ?? 0)}</span>
          {sampleRate ? <span className="hint">采样率 {sampleRate} Hz</span> : null}
          {frame?.measurement_id ? (
            <span className="hint">
              测量 {frame.measurement_id.slice(0, 8)} · 已记录 {frame.recorded_s ?? 0} 个 1 Hz 指标
            </span>
          ) : null}
          {dropped > 0 ? <span className="hint warn">丢帧 {dropped}</span> : null}
          {reconnectCount > 0 ? <span className="hint warn">重连 {reconnectCount} 次</span> : null}
          {bufferedCount > 0 ? <span className="hint warn">缓存 {bufferedCount} 帧</span> : null}
        </div>

        <div className="grid-2">
          <label className="field">
            <span>输入设备</span>
            <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)} disabled={capturing}>
              {devices.length === 0 ? <option value="">默认麦克风</option> : null}
              {devices.map((device, index) => (
                <option key={device.deviceId || `device-${index}`} value={device.deviceId}>
                  {device.label || `麦克风 ${index + 1}`}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span>设备操作</span>
            <div className="row-actions">
              <button type="button" className="ghost" onClick={() => void refreshDevices()} disabled={capturing}>
                刷新设备
              </button>
              <span className="hint">首次授权后才能显示设备名称</span>
            </div>
          </div>
        </div>

        <label className="checkbox" style={{ marginTop: 12 }}>
          <input
            type="checkbox"
            checked={saveAudio}
            onChange={(event) => {
              setSaveAudio(event.target.checked)
              saveAudioRef.current = event.target.checked
            }}
            disabled={capturing}
          />
          <span>保存本次录音（默认关闭，勾选后才写入 WAV 并自动提交离线分析）</span>
        </label>

        {linkedEventId && linkedStage ? (
          <p className="hint" style={{ marginTop: 6 }}>
            本次测量将自动关联事件阶段「{STAGE_TEXT[linkedStage]}」，需勾选上方保存录音才会产生可关联的分析。
          </p>
        ) : null}

        <p className="hint" style={{ marginTop: 6 }}>
          {profile
            ? `已应用单点校准修正 ${profile.offsetDb >= 0 ? '+' : ''}${profile.offsetDb.toFixed(2)} dB（${new Date(profile.savedAt).toLocaleDateString()}，校准在下次开始时生效）`
            : '未校准：结果按 1 Pa 全量程换算，仅作相对排查参考。'}
        </p>

        <div className="actions" style={{ marginTop: 16 }}>
          <button type="button" className="primary" onClick={handleStart} disabled={capturing || busy}>
            {status === 'error' || status === 'stopped' || status === 'saved' ? '重新开始测量' : '开始测量'}
          </button>
          <button type="button" className="ghost" onClick={handleStop} disabled={!canStop}>
            {saveAudio ? '停止并保存' : '停止测量'}
          </button>
        </div>

        {error ? (
          <p className="alert" role="alert" style={{ marginTop: 12 }}>
            {error}
          </p>
        ) : null}
      </div>

      {savedRuns.length > 0 ? (
        <div className="card">
          <h2>已归档录音</h2>
          <table className="table">
            <thead>
              <tr>
                <th>分段</th>
                <th>时长</th>
                <th>状态</th>
                <th>分析</th>
              </tr>
            </thead>
            <tbody>
              {savedRuns.map((item) => (
                <tr key={item.run_id}>
                  <td>第 {item.segment + 1} 段</td>
                  <td>{item.duration_s.toFixed(1)} s</td>
                  <td>
                    {item.interrupted ? <span className="warn">连接中断自动保存</span> : '正常停止'}
                    {item.truncated ? ' / 已达容量上限截断' : ''}
                  </td>
                  <td>
                    <Link to={`/analyses/${item.run_id}`}>查看分析结果</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="card">
        <div className="metric-group">
          <p className="metric-group-title">累计</p>
          <div className="metric-grid">
            <div className="metric">
              <MetricLabel term="laeq" prefix="累计" />
              <strong className="metric-value">{formatLevel(cumulative?.laeq)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lceq" prefix="累计" />
              <strong className="metric-value">{formatLevel(cumulative?.lceq)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lzeq" prefix="累计" />
              <strong className="metric-value">{formatLevel(cumulative?.lzeq)}</strong>
            </div>
          </div>
        </div>

        <div className="metric-group">
          <p className="metric-group-title">快速（125 ms）与峰值</p>
          <div className="metric-grid">
            <div className="metric">
              <MetricLabel term="laf" prefix="快速" />
              <strong className="metric-value">{formatLevel(levels?.laf)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lcf" prefix="快速" />
              <strong className="metric-value">{formatLevel(levels?.lcf)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lafmax" />
              <strong className="metric-value">{formatLevel(levels?.lafmax)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lcfmax" />
              <strong className="metric-value">{formatLevel(levels?.lcfmax)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lcpeak" />
              <strong className="metric-value">{formatLevel(levels?.lcpeak)}</strong>
            </div>
          </div>
        </div>

        <div className="metric-group">
          <p className="metric-group-title">当前窗口</p>
          <div className="metric-grid">
            <div className="metric">
              <MetricLabel term="laeq" prefix="窗口" />
              <strong className="metric-value">{formatLevel(levels?.laeq)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lceq" prefix="窗口" />
              <strong className="metric-value">{formatLevel(levels?.lceq)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="lzeq" prefix="窗口" />
              <strong className="metric-value">{formatLevel(levels?.lzeq)}</strong>
            </div>
            <div className="metric">
              <MetricLabel term="ca" prefix="窗口" />
              <strong className="metric-value">{formatLevel(levels?.c_minus_a)}</strong>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>窄带 FFT 与 Peak Hold</h2>
        <FftChart f={frame?.fft.f ?? []} level={frame?.fft.level ?? []} peakHold={frame?.peak_hold} />
      </div>

      <div className="card">
        <h2>1/3 倍频程（20–200 Hz）</h2>
        <BandChart bands={frame?.bands ?? []} peaks={[]} />
      </div>

      <div className="card">
        <h2>实时声谱图</h2>
        <LiveSpectrogram f={spectro.f} t={spectro.t} columns={spectro.columns} />
        <p className="hint" style={{ marginTop: 8 }}>
          10–500 Hz，最近 {MAX_SPECTROGRAM_COLUMNS} 个窗口；颜色越亮表示该频率能量越高。
        </p>
      </div>

      <div className="card">
        <h2>单点校准</h2>
        <p className="hint">
          将声校准器（94 dB 或 114 dB @ 1 kHz）套在麦克风上，待累计 LAeq 稳定后选择校准器声级并点击计算。
          修正量保存在本机，下次开始测量时应用；也可在测量分析页手动填写。
        </p>
        <div className="inline-form">
          <label className="field">
            <span>校准器声级</span>
            <select value={calibratorLevel} onChange={(event) => setCalibratorLevel(Number(event.target.value))}>
              <option value={94}>94 dB @ 1 kHz</option>
              <option value={114}>114 dB @ 1 kHz</option>
            </select>
          </label>
          <button type="button" className="primary" onClick={applyCalibration} disabled={cumulative?.laeq == null}>
            以当前累计 LAeq 校准
          </button>
          {profile ? (
            <button type="button" className="ghost" onClick={removeCalibration}>
              清除校准
            </button>
          ) : null}
        </div>
        <p className="hint" style={{ marginTop: 8 }}>
          当前读数：{formatLevel(cumulative?.laeq)}
          {profile ? `；测量值 ${profile.measuredLaeq.toFixed(1)} dB → 修正 ${profile.offsetDb >= 0 ? '+' : ''}${profile.offsetDb.toFixed(2)} dB` : ''}
        </p>
      </div>

      <p className="hint">
        实时指标按 1 Hz 落库，可在<Link to="/">工作台</Link>查看最近测量与逐秒趋势；录音仍默认不落盘，
        勾选保存后，停止或断线时会归档 WAV 并自动执行一次离线分析，可在
        <Link to="/upload">测量分析</Link>页查看全部记录。
      </p>
    </section>
  )
}

export default RealtimePage
