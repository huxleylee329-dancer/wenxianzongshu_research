# Research Workbench MCP

本目录保留 Research Workbench 的 MCP 接入说明。MCP 用于把外部工具和数据源接入研究流程。

## 能力

- 启动深度研究或快速搜索。
- 读取研究上下文和来源。
- 基于已收集证据生成报告。
- 连接本地或远程 MCP 服务。

## 配置原则

- MCP 服务配置应通过运行参数或本地环境提供。
- Token、API Key 和私有 Header 不得写入仓库。
- 自动化测试必须使用 fake/mock MCP 服务，不能依赖真实外部进程。

当前项目仓库：<https://github.com/huxleylee329-dancer/wenxianzongshu_research>

核心实现和配置示例位于 `gpt_researcher/mcp/`、`.mcp.json` 与根目录 README。
