/**
 * 实时采集 AudioWorklet：多声道求平均后按固定块长切分。
 *
 * 主线程每处理完一块会回 ack；待确认队列超过容量时丢弃新块并累加 dropped，
 * 用于在渲染/网络繁忙时量化丢帧，而不是无限堆积内存。
 */
class RealtimeCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const configured = options?.processorOptions?.bufferSize
    this._size = Number.isFinite(configured) && configured > 0 ? Math.floor(configured) : 4096
    this._buffer = new Float32Array(this._size)
    this._offset = 0
    this._queue = []
    this._maxQueue = 8
    this._seq = 0
    this._dropped = 0

    this.port.onmessage = (event) => {
      const data = event.data
      if (data && data.type === 'ack') {
        const index = this._queue.indexOf(data.seq)
        if (index >= 0) this._queue.splice(index, 1)
      }
    }
  }

  process(inputs) {
    const channels = inputs[0]
    if (!channels || channels.length === 0 || !channels[0] || channels[0].length === 0) {
      return true
    }

    const frames = channels[0].length
    const count = channels.length

    for (let i = 0; i < frames; i += 1) {
      let sum = 0
      for (let channel = 0; channel < count; channel += 1) {
        sum += channels[channel][i] || 0
      }
      this._buffer[this._offset] = sum / count
      this._offset += 1
      if (this._offset >= this._size) {
        this._offset = 0
        this._emit()
      }
    }
    return true
  }

  _emit() {
    this._seq += 1
    if (this._queue.length >= this._maxQueue) {
      this._dropped += 1
      return
    }
    const chunk = this._buffer.slice()
    this._queue.push(this._seq)
    this.port.postMessage(
      {
        seq: this._seq,
        dropped: this._dropped,
        t: currentTime,
        buffer: chunk,
      },
      [chunk.buffer],
    )
  }
}

registerProcessor('realtime-capture', RealtimeCaptureProcessor)
