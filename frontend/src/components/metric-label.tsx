const HELP: Record<string, { text: string; help: string }> = {
  laeq: {
    text: 'LAeq',
    help: 'A 计权等效连续声级：模拟人耳对低频不敏感的特性，是评价环境噪声的主要指标。',
  },
  lceq: {
    text: 'LCeq',
    help: 'C 计权等效连续声级：对低频衰减较少，常用于判断低频成分的强弱。',
  },
  lzeq: {
    text: 'LZeq',
    help: 'Z 计权（不计权）等效声级：反映原始声压能量，便于与仪器原始读数对比。',
  },
  laf: {
    text: 'LAF',
    help: 'A 计权、125 ms 时间计权的快速声级，随声音变化实时起伏。',
  },
  lcf: {
    text: 'LCF',
    help: 'C 计权、125 ms 时间计权的快速声级，用于观察低频的瞬时波动。',
  },
  lafmax: {
    text: 'LAFmax',
    help: '测量时段内 A 计权快速声级的最大值，反映最吵闹的瞬间。',
  },
  lcfmax: {
    text: 'LCFmax',
    help: '测量时段内 C 计权快速声级的最大值，适合评估低频峰值。',
  },
  lcpeak: {
    text: 'LCpeak',
    help: 'C 计权峰值声级（不做时间计权），用于捕捉撞击等瞬态峰值。',
  },
  ca: {
    text: 'C − A',
    help: 'C 计权与 A 计权声级之差；差值越大，说明低频成分越突出，是低频噪音的重要判据。',
  },
  lowRatio: {
    text: '低频占比',
    help: '20–200 Hz 频段能量占全频带能量的比例，占比越高说明能量越集中在低频。',
  },
  duration: {
    text: '时长 / 采样率',
    help: '参与本次分析的音频时长与采样率。',
  },
  dominantPeak: {
    text: '主峰',
    help: '10–500 Hz 范围内最突出的窄带峰值，给出频率与声级。',
  },
}

export function MetricLabel({
  term,
  prefix,
  suffix,
  className = 'metric-label',
}: {
  term: keyof typeof HELP
  prefix?: string
  suffix?: string
  className?: string
}) {
  const item = HELP[term]
  return (
    <span className={`has-tooltip ${className}`} tabIndex={0} data-tooltip={item.help}>
      {prefix ? `${prefix} ` : ''}
      {item.text}
      {suffix ?? ''}
      <span className="sr-only">：{item.help}</span>
    </span>
  )
}
