# Research Workbench WebSocket Client

用于连接本地 Research Workbench 后端的 WebSocket 客户端。

历史 npm 包名为 `gpt-researcher`，为避免破坏既有消费者暂时保留。

```javascript
const ResearchClient = require("gpt-researcher");

const client = new ResearchClient({
  host: "http://localhost:8000",
  logListener: (event) => console.log(event),
});

client.sendMessage({
  query: "分析一个研究主题",
});
```

项目仓库：<https://github.com/huxleylee329-dancer/wenxianzongshu_research>
