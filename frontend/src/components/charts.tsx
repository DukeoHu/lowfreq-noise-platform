import { useEffect, useMemo, useRef } from 'react'
import createPlotlyComponent from 'react-plotly.js/factory'
import Plotly from 'plotly.js-dist-min'
import type { BandLevel, Peak } from '../types'

const Plot = createPlotlyComponent(Plotly)

const FONT_FAMILY = '"Segoe UI", "Microsoft YaHei", system-ui, sans-serif'

const BASE_LAYOUT = {
  margin: { l: 56, r: 16, t: 24, b: 44 },
  font: { family: FONT_FAMILY, size: 12, color: '#5e6560' },
  paper_bgcolor: 'transparent',
  plot_bgcolor: 'transparent',
  hovermode: 'x unified' as const,
  hoverlabel: {
    bgcolor: '#0f1c1a',
    bordercolor: 'rgba(255, 255, 255, 0.2)',
    font: { family: FONT_FAMILY, size: 12, color: '#ffffff' },
  },
}

const AXIS_STYLE = {
  gridcolor: 'rgba(25, 28, 27, 0.09)',
  linecolor: 'rgba(25, 28, 27, 0.18)',
  zeroline: false,
}

const ACCENT = '#0f6b5f'
const SIGNAL = '#b45309'
const PEAK = '#c2410c'

export function TrendChart({ t, laeq, lceq }: { t: number[]; laeq: (number | null)[]; lceq: (number | null)[] }) {
  const data = useMemo(
    () => [
      { x: t, y: laeq, type: 'scatter' as const, mode: 'lines' as const, name: 'LAeq', line: { color: ACCENT, width: 2 } },
      { x: t, y: lceq, type: 'scatter' as const, mode: 'lines' as const, name: 'LCeq', line: { color: SIGNAL, width: 1.5, dash: 'dot' as const } },
    ],
    [t, laeq, lceq],
  )
  return (
    <Plot
      data={data}
      layout={{
        ...BASE_LAYOUT,
        height: 280,
        xaxis: { ...AXIS_STYLE, title: { text: '时间 (s)' } },
        yaxis: { ...AXIS_STYLE, title: { text: '声级 (dB)' } },
        legend: { orientation: 'h', y: 1.12 },
      }}
      config={{ displaylogo: false, responsive: true }}
      style={{ width: '100%' }}
    />
  )
}

export function BandChart({ bands, peaks }: { bands: BandLevel[]; peaks: Peak[] }) {
  const peakMap = new Map(peaks.map((peak) => [peak.fc, peak]))
  const valid = bands.filter((band) => band.level !== null)
  const data = useMemo(
    () => [
      {
        x: valid.map((band) => band.fc),
        y: valid.map((band) => band.level),
        text: valid.map((band) => (peakMap.has(band.fc) ? '低频峰' : '')),
        type: 'scatter' as const,
        mode: 'lines+markers' as const,
        name: 'Z 计权 1/3 倍频程',
        line: { color: ACCENT, width: 2 },
        marker: {
          size: valid.map((band) => (peakMap.has(band.fc) ? 9 : 5)),
          color: valid.map((band) => (peakMap.has(band.fc) ? PEAK : ACCENT)),
        },
        hovertemplate: '%{x} Hz<br>%{y:.1f} dB<extra></extra>',
      },
    ],
    [valid, peakMap],
  )
  return (
    <Plot
      data={data}
      layout={{
        ...BASE_LAYOUT,
        height: 320,
        xaxis: { ...AXIS_STYLE, title: { text: '1/3 倍频程中心频率 (Hz)' }, type: 'log', dtick: 'D2' },
        yaxis: { ...AXIS_STYLE, title: { text: '声级 (dB)' } },
      }}
      config={{ displaylogo: false, responsive: true }}
      style={{ width: '100%' }}
    />
  )
}

export function FftChart({
  f,
  level,
  peakHold,
}: {
  f: number[]
  level: number[]
  peakHold?: { f: number[]; level: number[] }
}) {
  const data = useMemo(() => {
    const traces: Record<string, unknown>[] = [
      {
        x: f,
        y: level,
        type: 'scatter',
        mode: 'lines',
        name: 'Z 计权窄带谱',
        line: { color: ACCENT, width: 1.5 },
        hovertemplate: '%{x:.1f} Hz<br>%{y:.1f} dB<extra></extra>',
      },
    ]
    if (peakHold && peakHold.f.length > 0) {
      traces.push({
        x: peakHold.f,
        y: peakHold.level,
        type: 'scatter',
        mode: 'lines',
        name: 'Peak Hold',
        line: { color: PEAK, width: 1.5, dash: 'dot' },
        hovertemplate: '%{x:.1f} Hz<br>%{y:.1f} dB<extra></extra>',
      })
    }
    return traces
  }, [f, level, peakHold])
  return (
    <Plot
      data={data as never}
      layout={{
        ...BASE_LAYOUT,
        height: 320,
        xaxis: { ...AXIS_STYLE, title: { text: '频率 (Hz)' }, type: 'log', dtick: 'D2' },
        yaxis: { ...AXIS_STYLE, title: { text: '窄带声级 (dB)' } },
        legend: { orientation: 'h', y: 1.12 },
      }}
      config={{ displaylogo: false, responsive: true }}
      style={{ width: '100%' }}
    />
  )
}

export function SpectrogramChart({ t, f, z, vmin, vmax }: { t: number[]; f: number[]; z: number[][]; vmin: number; vmax: number }) {
  const data = useMemo(
    () => [
      {
        x: t,
        y: f,
        z,
        type: 'heatmap' as const,
        colorscale: 'Viridis',
        zmin: vmin,
        zmax: vmax,
        colorbar: { title: { text: 'dB' }, thickness: 12 },
        hovertemplate: '时间 %{x:.1f}s<br>频率 %{y:.0f} Hz<br>%{z:.1f} dB<extra></extra>',
      },
    ],
    [t, f, z, vmin, vmax],
  )
  return (
    <Plot
      data={data}
      layout={{
        ...BASE_LAYOUT,
        height: 360,
        xaxis: { ...AXIS_STYLE, title: { text: '时间 (s)' } },
        yaxis: { ...AXIS_STYLE, title: { text: '频率 (Hz)' } },
      }}
      config={{ displaylogo: false, responsive: true }}
      style={{ width: '100%' }}
    />
  )
}

const VIRIDIS_STOPS: Array<[number, number, number]> = [
  [68, 1, 84],
  [72, 40, 120],
  [62, 74, 137],
  [49, 104, 142],
  [38, 130, 142],
  [31, 158, 137],
  [53, 183, 121],
  [109, 205, 89],
  [180, 222, 44],
  [253, 231, 37],
]

function viridis(value: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, value))
  const scaled = clamped * (VIRIDIS_STOPS.length - 1)
  const index = Math.min(VIRIDIS_STOPS.length - 2, Math.floor(scaled))
  const ratio = scaled - index
  const start = VIRIDIS_STOPS[index]
  const end = VIRIDIS_STOPS[index + 1]
  return [
    Math.round(start[0] + (end[0] - start[0]) * ratio),
    Math.round(start[1] + (end[1] - start[1]) * ratio),
    Math.round(start[2] + (end[2] - start[2]) * ratio),
  ]
}

/** 实时声谱图：按时间追加列，用 canvas 增量绘制，避免 Plotly 高频重绘。 */
export function LiveSpectrogram({
  f,
  t,
  columns,
}: {
  f: number[]
  t: number[]
  columns: number[][]
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const width = 900
    const height = 240
    if (canvas.width !== width) canvas.width = width
    if (canvas.height !== height) canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.fillStyle = '#0d1b19'
    ctx.fillRect(0, 0, width, height)
    if (columns.length === 0 || f.length === 0) {
      ctx.fillStyle = '#ffffff'
      ctx.font = '13px sans-serif'
      ctx.fillText('等待频谱窗口…', 16, height / 2)
      return
    }

    let vmin = Infinity
    let vmax = -Infinity
    for (const column of columns) {
      for (const value of column) {
        if (value < vmin) vmin = value
        if (value > vmax) vmax = value
      }
    }
    const span = Math.max(1, vmax - vmin)

    const offscreen = document.createElement('canvas')
    offscreen.width = columns.length
    offscreen.height = f.length
    const offCtx = offscreen.getContext('2d')
    if (!offCtx) return
    const image = offCtx.createImageData(columns.length, f.length)
    for (let x = 0; x < columns.length; x += 1) {
      const column = columns[x]
      for (let y = 0; y < f.length; y += 1) {
        const [r, g, b] = viridis(((column[y] ?? vmin) - vmin) / span)
        const index = ((f.length - 1 - y) * columns.length + x) * 4
        image.data[index] = r
        image.data[index + 1] = g
        image.data[index + 2] = b
        image.data[index + 3] = 255
      }
    }
    offCtx.putImageData(image, 0, 0)

    ctx.imageSmoothingEnabled = false
    ctx.drawImage(offscreen, 0, 0, width, height)
    ctx.fillStyle = '#ffffff'
    ctx.shadowColor = 'rgba(0, 0, 0, 0.75)'
    ctx.shadowBlur = 4
    ctx.font = '12px sans-serif'
    ctx.fillText(`${Math.round(f[0])} Hz`, 8, height - 8)
    ctx.fillText(`${Math.round(f[f.length - 1])} Hz`, 8, 14)
    if (t.length > 0) {
      ctx.fillText(`${t[0].toFixed(0)}s`, width - 40, height - 8)
      ctx.fillText(`${t[t.length - 1].toFixed(0)}s`, width - 46, 14)
    }
    ctx.shadowBlur = 0
    ctx.shadowColor = 'transparent'
  }, [columns, f, t])

  return <canvas ref={canvasRef} className="spectrogram-canvas" aria-label="实时声谱图" />
}
