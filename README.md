# 知危鉴 · 灾害应急案例AI萃取助手

> **让每一条应急经验都有据可查**
> 知乎黑客松 2026 校园新锐季 · 知识炼金场赛道
> 从知乎应急讨论中萃取可溯源的案例知识：输入灾害关键词，自动检索知乎优质问答，AI 按四大维度结构化萃取，生成可溯源的应急案例知识卡片。

## 在线 Demo

🔗 **[点击体验](https://zhihu-disaster-case-extractor-kqojwnlr7fdrzhesgew7ma.streamlit.app)**（Streamlit Cloud 部署，直接可用）

## 功能链路

```
智能检索 → 案例萃取 → 案例分析 → AI综合复盘
```

### 第一层 · 智能检索
- 调用知乎搜索 API，检索台风、城市内涝、矿山事故、地质灾害等灾害相关优质内容
- 搜索结果支持权威等级标识（超高权威/高权威）、赞同数、内容类型展示
- 搜索结果数量可选（3/5/10），内置 4 个快捷案例关键词
- 支持收藏感兴趣的文章到侧边栏收藏夹

### 第二层 · 案例萃取
- 按「事件概况 / 风险因素 / 应急处置措施 / 经验教训」四维结构化提取
- 每条结论**强制附原文证据片段**，未提及内容标注 ⚠️原文未提及（防幻觉）
- 萃取输入含标题、作者、精选评论作为辅助上下文，提升信息覆盖度
- 多案例并发萃取（ThreadPoolExecutor，max_workers=3）
- 四维知识卡片用彩色边框区分（蓝/红/绿/紫）

### 第三层 · 案例分析
- 风险高亮标注
- 事件时间线（预警→发生→响应→处置→复盘），含 emoji 状态标识
- 多案例横向对比表格（含完整度百分比、有依据/未提及数）
- 案例完整度对比柱状图
- 共性汇总（共性风险、高频处置、共性短板）

### 第四层 · AI综合复盘
- 标准化知识卡片（渐变背景卡片样式）
- 结构化复盘报告（五段式：事件概述/风险分析/处置评估/问题与改进/可复用经验）
- 多轮追问对话（支持最近 4 轮上下文，连续追问）
- 一键导出 Markdown 报告（含全部萃取案例 + 共性分析 + 复盘报告）

## 技术栈

- **前端/应用**：Streamlit
- **数据源**：知乎开放平台（知乎搜索 API + 直答 Agent API）
- **鉴权**：知乎开放平台 Access Secret（Bearer + X-Request-Timestamp）
- **部署**：Streamlit Cloud

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/Hamis-li/zhihu-disaster-case-extractor.git
cd zhihu-disaster-case-extractor

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
streamlit run app.py
```

打开 http://localhost:8501，在左侧粘贴知乎开放平台 Access Secret（[developer.zhihu.com/profile](https://developer.zhihu.com/profile) 获取）即可使用。

## 部署指南（Streamlit Cloud）

1. 将本仓库推送到 GitHub
2. 在 [share.streamlit.io](https://share.streamlit.io) 连接仓库并部署
3. 在部署平台的 **Secrets** 管理界面添加：`ZHIHU_ACCESS_SECRET = <你的 Access Secret>`
   （也可参考 `.streamlit/secrets.toml.example`；密钥不会提交到仓库）
4. 部署后评委打开公网链接即可直接使用，无需输入密钥

## 防幻觉机制

本项目核心设计原则：**所有萃取结论严格基于知乎原文证据片段**。

- 萃取提示词强制要求每条结论附原文证据片段
- 原文未提及内容统一标注 ⚠️原文未提及
- 不编造灾害数据、事件、时间
- 追问回答引用原文证据，无答案时明确回复"根据已有材料无法回答此问题"

## 项目结构

```
├── app.py                          # 唯一部署入口，全部逻辑
├── requirements.txt                # 依赖
├── .streamlit/
│   ├── config.toml                 # 主题配置（知乎蓝）
│   ├── secrets.toml.example        # 密钥配置示例
│   └── secrets.toml                # 实际密钥（gitignore，不入库）
├── assets/
│   ├── bell.png                    # 警钟 IP 形象
│   └── brand/project_icon.png      # 品牌图标
├── .gitignore
└── README.md
```

## 说明

- 所有萃取结论严格基于知乎原文证据，原文未提及内容统一标注，不编造灾害数据与事件
- 密钥仅在会话/部署环境中使用，不写入代码、日志或前端响应
- 超时自动重试 1 次，额度耗尽显示友好提示

## 团队

- **品牌名**：知危鉴
- **团队名**：应急有我
- **知乎ID**：木心引力
- **赛道**：知识炼金场
