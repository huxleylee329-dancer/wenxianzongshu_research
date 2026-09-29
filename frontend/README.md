# Research Workbench Frontend

当前项目提供两套界面：

- `frontend/`：由 FastAPI 直接提供的静态 HTML/CSS/JavaScript 界面。
- `frontend/nextjs/`：Next.js 响应式界面和可嵌入组件。

## 静态界面

在仓库根目录启动：

```powershell
& '.\.venv\Scripts\python.exe' -m uvicorn backend.server.app:app --host 127.0.0.1 --port 8000 --env-file .env
```

访问 <http://127.0.0.1:8000/>。

## Next.js 界面

```powershell
Set-Location frontend\nextjs
npm install
npm run dev
```

默认访问 <http://localhost:3000/>，并连接运行在 `http://localhost:8000` 的后端。

## 主要功能

- 研究任务创建与实时进度展示。
- 学术检索与论文筛选工作流。
- 报告、来源、图片和研究历史展示。
- 桌面端与移动端响应式布局。
- 模型、Retriever 和 MCP 配置。

不要在前端源码中写入 API Key 或其他秘密。
