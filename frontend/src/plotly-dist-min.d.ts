declare module 'plotly.js-dist-min' {
  // 该发行版不提供类型声明，此处按 react-plotly.js factory 的入参要求放宽
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Plotly: any
  export default Plotly
}
