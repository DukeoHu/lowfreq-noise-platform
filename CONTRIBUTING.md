# 贡献指南

感谢你对本项目的关注。提交代码前请先阅读以下约定。

## 开发环境

后端（Python 3.11+）：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

前端（Node.js 18+）：

```powershell
cd frontend
npm install
npm run dev
```

## 提交前检查

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest

cd ..\frontend
npm run typecheck
npm run build
```

- 新增 DSP 口径请同时补充合成信号或基准用例，说明计算依据。
- 修改接口时同步更新 README 的接口表与前端类型定义。
- 保持中文注释与现有代码风格一致。

## 不要提交的内容

- `backend/.env`、真实 API Key、访问口令
- `backend/data/`（数据库、音频、结果、现场照片）
- 真实测量录音、事件照片、包含个人或内网信息的截图
- `frontend/node_modules/`、`frontend/dist/`、虚拟环境与缓存目录

## 提交与 Pull Request

1. 从 `main` 切出功能分支，提交信息使用祈使句，例如 `fix: 修正 63 Hz 频段插值`。
2. 一个 PR 只做一件事，说明背景、改动与验证方式。
3. 涉及行为变化时更新 `CHANGELOG.md`。
4. 提交前确认没有把本机数据或密钥带入版本库。

## 安全与隐私

发现安全问题时请不要公开提交 Issue，按 [SECURITY.md](SECURITY.md) 的方式私下报告。涉及录音、位置与照片的功能，默认应关闭采集，并保留用户显式授权入口。

## 许可

提交即表示你同意以 [MIT License](LICENSE) 授权你的贡献。
