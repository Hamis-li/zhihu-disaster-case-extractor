# 灾害应急案例AI萃取助手

> 知乎黑客松 2026 校园新锐季 · 知识炼金场赛道
> 面向应急管理专业场景的知乎内容萃取工具：输入灾害关键词，自动检索知乎优质问答，AI 按四大维度结构化萃取，生成可溯源、可复制的应急案例知识卡片。

## 功能链路

```
智能检索 → 案例萃取 → 案例分析 → AI综合复盘
```

- **智能检索**：调用知乎搜索 API，检索台风、城市内涝、矿山事故、地质灾害等灾害相关优质内容
- **案例萃取**：按「事件概况 / 风险因素 / 应急处置措施 / 经验教训」四维结构化提取，每条结论强制附原文证据片段，未提及内容标注 ⚠️原文未提及（防幻觉）
- **案例分析**：风险高亮、事件时间线（预警→发生→响应→处置→复盘）、多案例横向对比、共性汇总
- **AI综合复盘**：知识卡片、结构化复盘报告、针对案例追问深入

## 技术栈

- 前端/应用：Streamlit
- 数据源：知乎开放平台（知乎搜索 API + 直答 Agent API）
- 鉴权：知乎开放平台 Access Secret（Bearer + X-Request-Timestamp）

## 本地运行

```bash
pip install -r requirements.txt
streamlit run app.py
```

打开 http://localhost:8501，在左侧粘贴知乎开放平台 Access Secret（[developer.zhihu.com/profile](https://developer.zhihu.com/profile) 获取）即可使用。

## 公网部署（Streamlit Cloud）

1. 将本仓库推送到 GitHub
2. 在 [share.streamlit.io](https://share.streamlit.io) 连接仓库并部署
3. 在部署平台的 **Secrets** 管理界面添加：`ZHIHU_ACCESS_SECRET = <你的 Access Secret>`
   （也可参考 `.streamlit/secrets.toml.example`；密钥不会提交到仓库）
4. 部署后评委打开公网链接即可直接使用，无需输入密钥

## 说明

- 所有萃取结论严格基于知乎原文证据，原文未提及内容统一标注，不编造灾害数据与事件
- 密钥仅在会话/部署环境中使用，不写入代码、日志或前端响应
