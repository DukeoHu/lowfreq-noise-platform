/** 本地隐私授权与校准档案（MVP 无账号体系，保存在浏览器 localStorage）。 */

export interface PrivacySettings {
  /** 允许保存/上传录音；默认关闭 */
  allowRecording: boolean
  /** 允许采集位置坐标；默认关闭 */
  allowLocation: boolean
  /** 允许导出/分享包含原始音频的数据包；默认关闭 */
  allowSharing: boolean
}

export interface CalibrationProfile {
  offsetDb: number
  level: number
  measuredLaeq: number
  savedAt: string
}

/** 内网共享口令与操作人；仅保存在当前浏览器，随请求头/WebSocket 参数发送。 */
export interface AccessSettings {
  token: string
  actor: string
}

const PRIVACY_KEY = 'lowfreq.privacy'
const CALIBRATION_KEY = 'lowfreq.calibration'
const ACCESS_KEY = 'lowfreq.access'

export const DEFAULT_PRIVACY: PrivacySettings = {
  allowRecording: false,
  allowLocation: false,
  allowSharing: false,
}

export function loadPrivacy(): PrivacySettings {
  try {
    const raw = localStorage.getItem(PRIVACY_KEY)
    if (!raw) return { ...DEFAULT_PRIVACY }
    const parsed = JSON.parse(raw) as Partial<PrivacySettings>
    return {
      allowRecording: Boolean(parsed.allowRecording),
      allowLocation: Boolean(parsed.allowLocation),
      allowSharing: Boolean(parsed.allowSharing),
    }
  } catch {
    return { ...DEFAULT_PRIVACY }
  }
}

export function savePrivacy(settings: PrivacySettings): void {
  localStorage.setItem(PRIVACY_KEY, JSON.stringify(settings))
}

export function loadCalibration(): CalibrationProfile | null {
  try {
    const raw = localStorage.getItem(CALIBRATION_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<CalibrationProfile>
    if (typeof parsed.offsetDb !== 'number' || typeof parsed.level !== 'number') return null
    return {
      offsetDb: parsed.offsetDb,
      level: parsed.level,
      measuredLaeq: Number(parsed.measuredLaeq ?? 0),
      savedAt: String(parsed.savedAt ?? ''),
    }
  } catch {
    return null
  }
}

export function saveCalibration(profile: CalibrationProfile): void {
  localStorage.setItem(CALIBRATION_KEY, JSON.stringify(profile))
}

export function clearCalibration(): void {
  localStorage.removeItem(CALIBRATION_KEY)
}

export function loadAccess(): AccessSettings {
  try {
    const raw = localStorage.getItem(ACCESS_KEY)
    if (!raw) return { token: '', actor: '' }
    const parsed = JSON.parse(raw) as Partial<AccessSettings>
    return {
      token: typeof parsed.token === 'string' ? parsed.token : '',
      actor: typeof parsed.actor === 'string' ? parsed.actor : '',
    }
  } catch {
    return { token: '', actor: '' }
  }
}

export function saveAccess(settings: AccessSettings): void {
  localStorage.setItem(
    ACCESS_KEY,
    JSON.stringify({ token: settings.token.trim(), actor: settings.actor.trim() }),
  )
}
