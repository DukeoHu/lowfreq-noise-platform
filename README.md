# 低频噪音 Web 平台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](backend/pyproject.toml)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](frontend/package.json)
[![Tests](https://img.shields.io/badge/tests-64%20passed-brightgreen.svg)](backend/tests)

浏览器麦克风实时测量 / 文件上传 → Python 服务端 DSP → 权威指标与可下载数据。

一个可自托管的低频噪声排查平台：实时测量、离线 1/3 倍频程分析、整改前后对比、报告导出与可选的 LLM 辅助解读。

> **English**: An open-source, self-hosted platform for low-frequency noise investigation. It combines browser-based realtime measurement, server-side DSP (A/C/Z weighting, 1/3-octave bands, low-frequency peaks), before/off/after comparison, PDF/CSV/ZIP export, and optional LLM-assisted explanations. See this README for setup; the codebase and comments are primarily in Chinese.

当前 MVP 包含：

- 离线分析：上传音频 → 异步分析 → 报告 / CSV / JSON / ZIP
- 实时测量：浏览器采集 → WebSocket 逐帧回传指标 → 停止时归档音频并自动跑一次离线分析，得到与离线一致的结果
- 默认不保存录音：实时录音需显式勾选授权；异常断线可自动保存分段并在重连后继续

## 目录

```
backend/            FastAPI + DSP 分析服务
  app/api/routes/   REST：health / audio-files / analyses / events / audit / measurements / dashboard；WebSocket：realtime
  app/services/dsp/ 计权、1/3 倍频程、频谱、声谱图、质量标志
  app/services/realtime.py           实时分析器（分块进、逐帧出）
  app/services/realtime_capture.py   实时录音落盘与归档（复用离线分析）
  app/services/measurement.py        测量会话与 1 Hz 指标落库
  app/services/audit.py              审计日志写入
  app/services/pipeline.py  一次分析 = 音频 + 配置 → 可复现结果
  app/workers/runner.py     任务执行器（后续替换为 Celery）
  tests/            计权基准 + 合成正弦 + 接口与实时链路测试
frontend/           React + TypeScript + Vite 前端（图表用 Plotly）
  public/realtime-worklet.js  采集 AudioWorklet（4096 分块、ack 队列、丢帧计数）
  src/pages/        工作台 / 测量分析 / 实时测量 / 噪音事件 / 审计日志 / 隐私与校准 / 分析详情
  src/settings.ts   本机隐私授权与校准档案
deploy/             内网 HTTPS 部署（Caddy）、备份脚本
docker-compose.yml  API + Caddy 一键部署
```

## 启动后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

接口文档：http://127.0.0.1:8000/docs

## 启动前端（需先安装 Node.js 18+）

```powershell
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 ，`/api`（含 WebSocket）已代理到后端 8000 端口。

页面：`/` 工作台（KPI、最近一次测量的 1 Hz 趋势、待处理事件、最近测量与系统摘要）、`/upload` 测量分析、`/realtime` 实时测量、`/events` 噪音事件、`/audit` 审计日志、`/settings` 隐私与校准、`/analyses/{id}` 分析详情。

构建产物已按需拆包：各页面通过 `React.lazy` 懒加载，Vite 将 plotly 单独成块，工作台趋势图也延后渲染，首屏入口 JS 从约 4.9 MB 降到数 kB，图表只在进入相关页面时下载。

## 内网部署（必须 HTTPS）

浏览器只在 **HTTPS 或 localhost** 下开放麦克风。其他电脑用 `http://内网IP` 打开会无法采集，因此内网部署必须启用 TLS 和 WSS。

### 方式一：Docker Compose（推荐）

```powershell
# 将 lowfreq.local 解析到服务器 IP，或改用实际域名（真实域名可自动签发证书）
$env:LOWFREQ_HOST = "lowfreq.local"
docker compose up -d --build
```

Caddy 使用内部 CA 签发证书。首次使用需把根证书装到每台客户端：

```powershell
docker compose cp web:/data/caddy/pki/authorities/local/root.crt ./deploy/root.crt
Import-Certificate -FilePath .\deploy\root.crt -CertStoreLocation Cert:\LocalMachine\Root
```

随后访问 `https://lowfreq.local`；`/api` 与 WebSocket 由 Caddy 反向代理到 `api:8000`。

### 方式二：本机 Caddy（不用 Docker）

```powershell
cd frontend; npm run build; cd ..
cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 另开终端，在仓库根目录运行（需先安装 Caddy）
caddy trust
caddy run --config deploy/Caddyfile.native
```

两者都用 `deploy/Caddyfile*` 中的 `tls internal`；换成真实域名时删除该行即可自动申请证书。

## 备份、保留与任务恢复

- 备份：`python deploy/backup.py --keep 14` 会把 SQLite 快照、音频、结果、照片打包到 `backups/`；建议用 Windows 任务计划程序每日执行。恢复时停止服务，把 ZIP 内容解压回 `backend/data`。
- 保留期：`LOWFREQ_AUDIO_RETENTION_DAYS` 控制音频自动清理（0=关闭，建议内网设 30）。已关联事件的音频视为证据，不参与清理；清理动作写入审计。
- 磁盘水位：`/api/system/status` 与设置页展示磁盘占用、音频/照片/结果数量、队列与失败任务数；超过 `LOWFREQ_DISK_WARN_PERCENT` 会在设置页告警。
- 任务恢复：分析任务由单机线程池执行；服务启动时会把上次残留的 `queued/running` 任务重新入队，避免进程中断后永久卡住。当前按单进程部署设计，不要开多个 uvicorn worker。
- 任务运维：失败任务可在分析页重试（`POST /api/analyses/{id}/retry`），排队中/计算中的任务可取消（`POST /api/analyses/{id}/cancel`）；执行器在落盘前会重新确认状态，取消后不会写入结果。
- 数据一致性：删除音频会级联删除分析记录，同时清理对应的 `results/{run.id}.json`，并把引用该音频/分析的测量分段 `audio_file_id`、`run_id` 置空；审计详情记录受影响的分析数、结果文件数与分段数。
- 数据库并发：SQLite 连接默认启用 `WAL`、`busy_timeout=30s` 与 `synchronous=NORMAL`，逐秒指标和审计写入带短重试；仍建议单进程部署，分析线程数控制在 1–2。
- 访问控制：设置 `LOWFREQ_ACCESS_TOKEN` 后，除 `/api/health`、`/docs`、`/redoc`、`/openapi.json` 与 `/` 外的接口都需要口令，支持 `Authorization: Bearer <token>`、`X-Access-Token` 或 `?token=`；WebSocket 用 `?token=` 校验。前端在「隐私与校准 → 访问口令与操作人」填写，仅保存在浏览器 localStorage；留空即关闭鉴权。
- 操作人审计：请求头 `X-Actor`（中文先做百分号编码；WebSocket 用 `?actor=`）写入审计日志 `actor` 列，缺省 `local`，审计页新增「操作人」列。
- 分析内存上限：音频按块解码到预分配的单声道缓冲，避免整段 float64 与 downmix 副本同时驻留；单文件分析时长上限由 `LOWFREQ_ANALYSIS_MAX_SECONDS` 控制（默认 600 s，0=不限制），超限时上传返回 413、创建分析返回 422，并在提示中说明裁剪或调整配置。
- 实时链路并发：WebSocket 上的落库与文件写入通过 `asyncio.to_thread` 卸载到线程池，避免阻塞事件循环；断线与任务取消时归档逻辑加屏蔽，确保录音与测量记录不被中断丢失。


## LLM 辅助解读（可选）

默认关闭，兼容 OpenAI Chat Completions 协议（DeepSeek、通义千问兼容模式、OpenAI、内网 vLLM/网关等）。

推荐在「隐私与校准 → AI 辅助解读」直接填写并保存：配置存入服务端数据库（`llm_config` 表），**优先于环境变量**，保存后立即生效、无需重启；API Key 仅服务端保存，读取接口只返回 `has_api_key`，页面上留空表示保持原值，勾选「清除已保存的密钥」才会删除。同一卡片可「测试连接」，用最小请求验证 Base URL / 模型 / 密钥是否可用。

也可以用环境变量做首次部署的默认值（数据库未写入配置时才生效）：

```powershell
$env:LOWFREQ_LLM_ENABLED="true"
$env:LOWFREQ_LLM_BASE_URL="https://api.deepseek.com/v1"
$env:LOWFREQ_LLM_API_KEY="sk-..."
$env:LOWFREQ_LLM_MODEL="deepseek-chat"
$env:LOWFREQ_LLM_TIMEOUT_S="60"
$env:LOWFREQ_LLM_MAX_TOKENS="1500"
# 分析成功后自动生成（默认 false，也可在页面手动生成）
$env:LOWFREQ_LLM_AUTO_INSIGHT="false"
```

- 分析页的「AI 辅助解读」与事件详情里的「AI 对比解读」可手动生成/重新生成，不自动改动 DSP 结果。
- 默认脱敏：不发送文件名、现场位置、备注与设备名称，可在设置页关闭。
- 结果、模型与提示词版本一致时命中缓存，不重复消耗 token；`?force=true` 可强制重新生成。
- 排队中/生成中可取消，每次成功生成都会保留历史版本，并支持「有用 / 无用 / 已采纳」人工反馈。
- 服务端计算事件各阶段 1/3 倍频程差值与 ≥10 dB 判定，模型只做解释；数值引用不一致会在页面与 PDF 中告警。
- 置信度按校准状态、削波/静音/直流偏移与时长规则计算，不采信模型自评。
- 可设置月度 token 预算，`GET /api/system/llm/usage` 与设置页展示本月用量；预算用尽后返回 429。
- 支持「仅环境变量」模式：勾选后数据库不再保存 API Key，运行时读取 `LOWFREQ_LLM_API_KEY`。
- 配置变更会写入审计（`system.llm_update`），详情只记录启停、地址、模型与是否已配置密钥，不记录密钥本身。
- 只发送指标摘要（总声级、C−A、≤200 Hz 的 1/3 倍频程、主要峰值、数据质量标志、事件各阶段均值），不发送音频与完整频谱。
- 输出统一为 `summary / findings / recommendations / caveats / confidence`，分别存于 `analysis_insights`、`event_insights` 表；报告 PDF 会附带「AI 辅助解读（仅供参考）」章节。
- 未配置模型返回 503；模型超时或返回非法 JSON 时解读记录标记失败，不影响分析主流程，也不写入正式结果。
- 未校准数据会在提示词中强制声明为相对值，AI 结论不构成检测、执法或验收依据。

## 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
```

## 接口一览

启用 `LOWFREQ_ACCESS_TOKEN` 后，下表接口（`/api/health` 除外）都需要口令；下载类链接无法带请求头，可用 `?token=` 传递。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/audio-files` | 上传音频（WAV/FLAC/OGG/MP3） |
| GET | `/api/audio-files` | 音频列表（`filename` 模糊搜索，返回 `{items,total,limit,offset}`） |
| GET | `/api/audio-files/{id}` | 音频元数据 |
| GET | `/api/audio-files/{id}/download` | 下载原始音频 |
| DELETE | `/api/audio-files/{id}` | 删除音频与结果文件，断开测量分段引用 |
| POST | `/api/analyses` | 创建分析任务（异步执行） |
| GET | `/api/analyses` | 分析任务列表（`status`/`filename`/`audio_file_id` 过滤，返回分页对象） |
| GET | `/api/analyses/{id}` | 任务状态 + 完整结果 |
| PATCH | `/api/analyses/{id}` | 标记整改阶段（before/off/after/null） |
| POST | `/api/analyses/{id}/retry` | 重试失败任务（非失败状态返回 409） |
| POST | `/api/analyses/{id}/cancel` | 取消排队中/计算中的任务 |
| GET/POST | `/api/analyses/{id}/insight` | 读取 / 生成单次分析 AI 解读（`?force=true` 跳过缓存，未配置模型返回 503） |
| POST | `/api/analyses/{id}/insight/cancel` | 取消排队中/生成中的解读 |
| GET | `/api/analyses/{id}/insight/history` | 解读历史版本 |
| POST | `/api/analyses/{id}/insight/feedback` | 提交人工反馈（useful / not_useful / adopted） |
| GET | `/api/analyses/{id}/bands.csv` | 1/3 倍频程数据导出 |
| GET | `/api/analyses/{id}/result.json` | 完整结果 JSON 导出 |
| GET | `/api/analyses/{id}/report.pdf` | 排查报告 PDF（未完成返回 409，含 AI 解读章节） |
| GET | `/api/analyses/{id}/export.zip` | 数据包 ZIP（结果/CSV/PDF，可选原始音频） |
| GET/POST | `/api/events` | 噪音事件列表 / 新建 |
| GET/PATCH | `/api/events/{id}` | 事件详情 / 修改测点、设备、标签、备注、坐标、状态 |
| DELETE | `/api/events/{id}` | 删除事件（同时删除现场照片，解除分析关联） |
| GET | `/api/events/{id}/summary` | 事件关联分析的关键指标对比 |
| GET/POST | `/api/events/{id}/insight` | 读取 / 生成事件阶段对比 AI 解读（`?force=true` 跳过缓存） |
| POST | `/api/events/{id}/insight/cancel` | 取消排队中/生成中的对比解读 |
| GET | `/api/events/{id}/insight/history` | 对比解读历史版本 |
| POST | `/api/events/{id}/insight/feedback` | 提交人工反馈 |
| POST | `/api/events/{id}/analyses/{run_id}` | 事件关联分析（可带 `stage=before/off/after`） |
| DELETE | `/api/events/{id}/analyses/{run_id}` | 解除事件与分析的关联 |
| POST | `/api/events/{id}/photos` | 上传现场照片（JPG/PNG/WebP，≤10 MB） |
| GET | `/api/events/{id}/photos/{photo_id}` | 读取现场照片 |
| DELETE | `/api/events/{id}/photos/{photo_id}` | 删除现场照片 |
| GET | `/api/audit` | 审计日志（action/entity_type/entity_id/actor/since/until 过滤，返回分页对象） |
| GET | `/api/system/status` | 磁盘占用、音频/照片/结果统计、队列与失败任务 |
| GET/PUT | `/api/system/llm` | 读取 / 保存 LLM 配置（DB 优先于环境变量，不返回 API Key） |
| GET | `/api/system/llm/usage` | 本月解读 token 用量与预算使用情况 |
| POST | `/api/system/llm/test` | 用已保存配置发起最小请求测试连通性 |
| GET | `/api/measurements` | 实时测量记录列表（按开始时间倒序） |
| GET | `/api/measurements/{id}` | 测量详情 + 真实 1 秒 Leq 指标点 + 录音分段（`point_limit`/`point_offset` 分页） |
| GET | `/api/dashboard` | 工作台汇总：KPI、最近一次测量趋势、待处理/最近事件、系统状态 |
| WS | `/api/realtime/ws` | 实时采集与逐帧指标（启用口令时追加 `?token=...&actor=...`） |

## 实时采集协议

1. 客户端连上后先发 hello：
   ```json
   {"type":"hello","sample_rate":48000,"save_audio":false,"offset_db":0,
    "session_id":"...","segment":0,"device_label":"...","constraints":{}}
   ```
   `save_audio` 默认 `false`；只有显式开启才会创建录音文件。`offset_db` 来自单点校准档案。
2. 服务端回 `ready`：`sample_rate`、`fft_size`、`save_on_stop`、`segment`、`segments`（同一 `session_id` 下已归档的分段）、`measurement_id`。
3. 客户端先发 `{"type":"chunk","seq":n,"dropped":d,"t":...}`，再发 float32 单声道二进制块（4096 样本）。每块服务端回 `frame`（含 `measurement_id` 与已记录指标点数 `recorded_s`）：
   - `levels`：窗口 LAeq/LCeq/LZeq/C−A、快速 LAF/LCF/LCpeak、累计 LAFmax/LCFmax
   - `cumulative`：累计 Leq 与时长
   - `fft`、`spectrum`、`peak_hold`：10–500 Hz 窄带谱、声谱图列、逐频点峰值保持
   - `bands`：20–200 Hz 的 1/3 倍频程
   - 每跨过一个整秒，服务端对该秒的 A/C/Z 计权波形做能量积分，写入真实 LAeq/LCeq/LZeq、秒内 LAFmax/LCFmax/LCpeak 与秒末 LAF/LCF（帧内 `seconds` 字段返回本次新增的整秒指标）
4. 停止时发 `{"type": "stop"}`：
   - 未开启录音：回 `{"type":"stopped","saved":false}`；
   - 时长不足 1 s：回 `{"type":"discarded"}`，不保存录音（1 Hz 指标仍保留）；
   - 否则写 `PCM_16` WAV，创建音频记录与 `queued` 分析任务，回 `{"type":"saved","run_id","audio_file_id","segment","duration_s","truncated","interrupted"}`。
5. 异常断开（关闭页面、网络中断）时，若已授权录音且时长 ≥1 s，会按分段自动归档并在审计中标记 `interrupted`；前端重连时携带同一 `session_id`，服务端在 `ready.segments` 返回已归档分段。
6. 录音超过 `max_upload_mb` 时截断并置 `truncated`。实时二进制帧本身不落库；按整秒能量积分得到的指标点写入 `metric_points`，停止或断线后测量记录标记为 `completed` / `interrupted`，同一 `session_id` 的重连会归并到同一条测量记录。

## 事件工作流

- 事件字段：标题、描述、位置、测点、设备、备注、标签、经纬度、状态。
- 现场照片：每个事件可上传多张 JPG/PNG/WebP，单张 ≤10 MB。
- 整改阶段：`before`（整改前 ON）、`off`（关机 OFF）、`after`（整改后 ON）。
- ON/OFF/ON 向导：事件详情按三个阶段汇总平均 LAeq，给出「整改前→整改后」「整改前→关机」「关机→整改后」差值，并判断是否达到 ≥10 dB 改善。

## 隐私、校准与审计

- 隐私授权分别控制：录音保存、位置采集、导出/分享原始音频，默认全部关闭，保存在浏览器 localStorage。
- 单点校准：在实时测量页用 94/114 dB @ 1 kHz 校准器测得累计 LAeq，自动计算修正量并保存；上传分析默认读取该修正量，可手动覆盖。
- 审计日志记录：音频上传/删除、分析创建/阶段变更/导出、事件创建/修改/关联/照片、实时录音归档，便于追溯结果来源。

## 已实现的计算口径

- 频率计权：A / C / Z，频域施加（IEC 61672 公式），1 kHz 归零
- 总级：LAeq、LCeq、LZeq、C−A
- 快速计权最大值：LAFmax / LCFmax（125 ms 指数计权，分块频域计权）与 LCpeak
- 低频占比：20–200 Hz 能量占全频带比例
- 窄带 FFT 谱：10–500 Hz，Z 计权、Hann 窗，按块取峰值降采样
- 谐波候选：显著峰作基频，匹配 2X–10X 谐波，给出 RPM 与匹配分数
- 1/3 倍频程：1 Hz–20 kHz 标称中心频率，Z 与 A 双口径
- 时间序列：按帧长/步长给出 LAeq、LCeq 趋势
- 低频峰值：300 Hz 以下局部峰值及突出度
- 声谱图：STFT 后按时间/频率降采样，避免浏览器内存失控
- 实时指标：8192 点 Hann 窗 + rfft，hop 4096；A/C 计权用带前后文的重叠保留法做线性卷积，避免分块边界产生宽带伪峰；LAF/LCF 为 125 ms 指数计权，LCpeak 为 C 计权瞬时峰值
- 实时 LAFmax/LCFmax：累计快速计权最大值；Peak Hold 为逐频点历史最大值
- 实时累计 Leq：Z 按原始采样平方和累计，A/C 按已处理 hop 的计权波形能量与样本数计算
- 质量标志：`uncalibrated`、`clipping_suspected`、`dc_offset_detected`、`digital_silence`、`mostly_silent`、`short_duration`、`multichannel_downmixed`

未校准麦克风的全量程默认映射为 1 Pa（≈94 dB SPL），结果仅作相对排查参考，不作为法定声级。

## 免责声明

- 本项目是低频噪声**排查与辅助分析工具**，不是计量认证仪器；未经过型式评价或校准链溯源的结果不能作为执法、验收或法律证据。
- 浏览器麦克风、设备频响与现场环境都会引入误差；正式检测请使用合规声级计与持证机构。
- 使用者需自行确保录音、位置与现场照片的采集、存储和分享符合当地隐私与个人信息保护法规。
- LLM 辅助解读由第三方模型生成，可能存在错误或遗漏，仅作参考；提交给模型的指标摘要默认脱敏，但仍请避免输入敏感信息。
- 软件按 [MIT License](LICENSE)「按原样」提供，不附带任何明示或暗示担保。

## 贡献与许可

- 开发环境、测试命令与提交约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要公开提交可利用细节。
- 变更记录见 [CHANGELOG.md](CHANGELOG.md)。
- 本项目采用 [MIT License](LICENSE)，欢迎 Issue 与 Pull Request。

