# Research Workbench Next.js UI

Research Workbench 的 Next.js 用户界面与可嵌入 React 组件。

## 本地开发

```powershell
npm install
npm run dev
```

打开 <http://localhost:3000/>。默认后端地址为 <http://localhost:8000/>。

## 构建

```powershell
npm run build
npm run start
```

## 组件库

为保持现有消费者兼容，组件导出名和历史 npm 包名暂不更改。新的产品展示名称统一为 `Research Workbench`。

```tsx
import { GPTResearcher } from "gpt-researcher-ui";

export default function ResearchPage() {
  return (
    <GPTResearcher
      apiUrl="http://localhost:8000"
      defaultPrompt="分析一个研究主题"
      onResultsChange={(results) => console.log(results)}
    />
  );
}
```

项目仓库：<https://github.com/huxleylee329-dancer/wenxianzongshu_research>
