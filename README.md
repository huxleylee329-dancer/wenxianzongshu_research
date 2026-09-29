# Research Workbench / 文献综述研究工作台

一个面向学术检索、证据筛选、文献综述与结构化报告写作的本地研究工作台。

仓库地址：<https://github.com/huxleylee329-dancer/wenxianzongshu_research>

## 核心能力

- 同时支持网页检索、本地文档和 MCP 数据源。
- 集成 arXiv 与 Semantic Scholar 学术检索。
- 支持 Semantic Scholar API Key、排序和期刊白名单。
- 将论文映射为严格的内部候选模型，并在单次研究运行内统一收集。
- 支持 DOI/标题去重、年份和论文类型的确定性筛选。
- 可选的主题相关性判断与逐次运行审计快照。
- 支持报告审计附录、引用证据检查和参考文献生成。
- 提供经典静态界面、Next.js 界面与移动端布局。
- 提供面向学术写作的 LangGraph 工作流和人工确认节点。

## 快速启动

### Windows 一键启动

1. 安装 Python 3.11 或更高版本。
2. 在仓库目录创建虚拟环境并安装依赖。
3. 将 `.env.example` 复制为 `.env`，填写所需服务的 API Key。
4. 双击 `start-research-workbench.cmd`。
5. 浏览器访问 <http://127.0.0.1:8000/>。

### PowerShell

```powershell
Set-Location 'E:\AI-Projects\gpt-researcher-official'
py -3.11 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
Copy-Item .env.example .env
& '.\.venv\Scripts\python.exe' -m uvicorn backend.server.app:app --host 127.0.0.1 --port 8000 --env-file .env
```

不要提交 `.env`，也不要把 API Key 写入 README、日志、测试快照或前端代码。

## 界面

- 静态界面：由 FastAPI 在 `/` 提供，源码位于 `frontend/`。
- Next.js 界面：源码位于 `frontend/nextjs/`。
- 本地启动器：`start-research-workbench.cmd`。

## 学术检索配置

常用环境变量示例：

```dotenv
RETRIEVER=arxiv,semantic_scholar
SEMANTIC_SCHOLAR_API_KEY=
SEMANTIC_SCHOLAR_JOURNALS=
```

`SEMANTIC_SCHOLAR_JOURNALS` 使用稳定 token，例如：

```dotenv
SEMANTIC_SCHOLAR_JOURNALS=tgars,jstars,taes,remote_sensing,journal_of_radars
```

## 论文筛选流水线

论文筛选能力按里程碑逐步构建：

1. 学术 Retriever 生成结构化 `PaperCandidate`。
2. 单次研究运行收集所有候选 occurrence。
3. 确定性引擎完成去重、年份与论文类型规则。
4. Basic/Web 两阶段流水线在压缩上下文前建立全局 barrier。
5. 可选主题相关性判断只处理 canonical paper。
6. 审计快照和报告附录保留筛选证据与路由结果。

冻结设计与验收边界位于 `specs/`。

## 测试

```powershell
& '.\.venv\Scripts\python.exe' -B -m pytest -p no:cacheprovider
```

学术 Retriever、筛选和 LLM 相关自动化测试应使用 mock/fake，不应访问真实外部服务。

## 项目结构

```text
backend/                 API、WebSocket 与报告服务
frontend/                静态界面
frontend/nextjs/         Next.js 界面
gpt_researcher/          核心研究、检索、筛选与写作模块
specs/                   已冻结的功能设计规范
tests/                   自动化测试
```

内部模块名 `gpt_researcher`、公共类名 `GPTResearcher` 以及部分兼容配置名暂时保留，以避免破坏 Python 导入、外部调用和历史数据兼容性。它们不是当前界面品牌。

## 来源与许可

本仓库在开源研究代理代码基础上持续重构，并新增了学术检索、论文筛选、审计快照、学术写作工作流和定制界面。适用的版权、许可证及第三方依赖条款应继续按仓库中的 `LICENSE` 和各依赖许可证执行。界面品牌和仓库说明的更新不改变既有代码的许可证或历史来源。
