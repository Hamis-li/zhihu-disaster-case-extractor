# 知危鉴 · 灾害应急案例 AI 萃取助手

> **让每一条应急经验都有据可查。**
>
> 把知乎上海量的灾害应急讨论，一键萃取成可复盘、可教学、可决策的应急案例知识卡片。
>
> 知乎黑客松 2026 校园新锐季 · **知识炼金场**赛道参赛作品 ｜ 团队：**应急有我**

## 参赛快照（截至 2026-09-20）

| 项目 | 链接 / 数据 |
|---|---|
| 🚀 在线 Demo | https://zhihu-disaster-case-extractor-kqojwnlr7fdrzhesgew7ma.streamlit.app |
| 🏆 赛事项目页 | https://www.zhihu.com/hackathon/project/90033?activity_code=zhihu_hackathon_2026_p2 |
| 📝 知乎投稿文章 | https://zhuanlan.zhihu.com/p/2082410743996195009 |
| 🐙 GitHub 仓库 | https://github.com/Hamis-li/zhihu-disaster-case-extractor |
| 项目状态 | 已提报发布；评审得分 5.53；项目热度 200 |

## 产品介绍

「知危鉴」是一款面向应急管理领域的知识生产工具。它基于知乎开放平台**搜索 API** 与**直答 Agent API**，构建了「智能检索 → 案例萃取 → 案例分析 → AI 综合复盘」四层完整产品链路：

1. **智能检索**：输入关键词或一键点击热门案例（城市内涝、台风、矿山事故、地质灾害等），精准检索高价值灾害应急内容；
2. **案例萃取**：AI 从原文中提取「事件概况 / 风险因素 / 应急处置措施 / 经验教训」四个维度，**每条结论强制附原文证据片段**；
3. **案例分析**：对多案例进行风险高亮、时间线还原、横向对比与共性汇总，快速发现处置短板与规律；
4. **AI 综合复盘**：一键生成结构化复盘报告，支持追问式深度分析，并可导出 Markdown，把零散信息转化为可复用的应急知识资产。

核心设计原则是**防幻觉**：所有 AI 结论必须引用原文证据，原文未提及的内容明确标注，确保知识生产可追溯、可验证。

**应用场景**：应急管理部门案例学习、高校应急专业教学、企业应急预案复盘、公众灾害科普。

## 功能链路

```
知乎账号登录（OAuth 解锁） → 智能检索 → 案例萃取 → 案例分析 → AI综合复盘
```

### 登录与解锁机制

- 采用知乎 OAuth 2.0（Authorization Code Flow）三方登录：授权页 → `authorization_code` 回调 → 换取 `access_token` → 拉取用户信息
- 未登录时显示产品引导页；**登录知乎账号后解锁全部四层功能**（多案例并发萃取、时间线、横向对比、复盘报告、导出、追问）
- 授权页通过新标签页打开，兼容知乎站点内 iframe 预览场景
- 换 token / 拉取用户信息失败时页面透传服务端原始报错，便于排查

### 第一层 · 智能检索

- 调用知乎搜索 API，检索台风、城市内涝、矿山事故、地质灾害等灾害相关优质内容
- 搜索结果展示权威等级标识（超高权威/高权威）、赞同数、内容类型
- 搜索结果数量可选（3/5/10），内置 4 个快捷案例关键词
- 支持收藏感兴趣的文章到侧边栏收藏夹

### 第二层 · 案例萃取

- 按「事件概况 / 风险因素 / 应急处置措施 / 经验教训」四维结构化提取
- 每条结论**强制附原文证据片段**，未提及内容标注 ⚠️ 原文未提及（防幻觉）
- 萃取输入含标题、作者、精选评论作为辅助上下文，提升信息覆盖度
- 多案例并发萃取（ThreadPoolExecutor，max_workers=3），超时自动重试 1 次
- 四维知识卡片用彩色边框区分（蓝/红/绿/紫）

### 第三层 · 案例分析

- 风险因素高亮标注
- 事件时间线（预警 → 发生 → 响应 → 处置 → 复盘），含状态标识
- 多案例横向对比表格（含完整度百分比、有依据/未提及数）
- 案例完整度对比柱状图
- 共性汇总（共性风险、高频处置、共性短板）

### 第四层 · AI 综合复盘

- 标准化知识卡片（渐变背景卡片样式）
- 结构化复盘报告（五段式：事件概述 / 风险分析 / 处置评估 / 问题与改进 / 可复用经验）
- 多轮追问对话（保留最近 4 轮上下文，连续追问）
- 一键导出 Markdown 报告（含全部萃取案例 + 共性分析 + 复盘报告）

## 防幻觉机制

本项目的核心设计原则：**所有萃取结论严格基于知乎原文证据片段**。

- 萃取提示词强制要求每条结论附原文证据片段
- 原文未提及内容统一标注「⚠️ 原文未提及」
- 不编造灾害数据、事件、时间
- 追问回答引用原文证据，无答案时明确回复「根据已有材料无法回答此问题」

## 技术栈

- **应用框架**：Streamlit（单文件应用 `app.py`）
- **数据源**：知乎开放平台（搜索 API + 直答 Agent API）
- **账号登录**：知乎 OAuth 2.0（Authorization Code Flow）
- **数据处理**：pandas；HTTP 请求：requests
- **部署**：Streamlit Community Cloud（连接 GitHub 仓库，push 后自动部署）

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

打开 http://localhost:8501，在左侧栏粘贴知乎开放平台 Access Secret（在 [developer.zhihu.com/profile](https://developer.zhihu.com/profile) 申请）即可使用检索/萃取功能；知乎 OAuth 登录需另行配置 OAuth 应用凭证（见下节）。

## 配置说明（Secrets / 环境变量）

在 `.streamlit/secrets.toml`（本地，已被 .gitignore 排除）或 Streamlit Cloud 的 Settings → Secrets 中配置。**TOML 格式要求：每行一个 `键 = "值"`，等号后不能换行。**

| 变量 | 必填 | 说明 |
|---|---|---|
| `ZHIHU_ACCESS_SECRET` | 是 | 知乎数据开放平台个人中心申请的 Access Secret，用于搜索 API 与直答 Agent API |
| `ZHIHU_OAUTH_APP_ID` | OAuth 登录需要 | 知乎 OAuth 应用 App ID（向开放平台申请，或由黑客松赛事平台分配） |
| `ZHIHU_OAUTH_APP_KEY` | OAuth 登录需要 | 知乎 OAuth 应用 App Key |

可选覆盖项（一般使用默认值即可）：`ZHIHU_API_BASE`、`ZHIHU_SEARCH_PATH`、`ZHIHU_AGENT_PATH`、`ZHIHU_QUOTA_PATH`、`ZHIHU_OAUTH_AUTHORIZE`、`ZHIHU_OAUTH_TOKEN`、`ZHIHU_OAUTH_USER`、`ZHIHU_OAUTH_REDIRECT_URI`。

> OAuth 应用申请邮箱：openplatform@zhihu.com（通用通道），申请材料需含应用名称、简介、≥256×256 图标、授权回调地址、申请人信息；`redirect_uri` 必须与申请/赛事登记值**逐字符一致**（协议、域名、路径、末尾斜杠都要相同）。

## 部署指南（Streamlit Cloud）

1. 将本仓库推送到 GitHub
2. 在 [share.streamlit.io](https://share.streamlit.io) 连接仓库并部署，Main file path 填 `app.py`
3. 在应用 **Settings → Secrets** 中添加 `ZHIHU_ACCESS_SECRET`（搜索/直答）以及 `ZHIHU_OAUTH_APP_ID`、`ZHIHU_OAUTH_APP_KEY`（登录）
4. push 到 main 分支后 1–2 分钟自动重新部署
5. 注意：Streamlit 页面默认不允许被第三方站点 iframe 嵌套（CSP `frame-ancestors`），在知乎站内预览框中可能显示拦截页，**直接新标签页打开 Demo 链接即可正常使用**

## 项目结构

```
├── app.py                              # 唯一部署入口，全部页面与逻辑（含 OAuth 全流程）
├── requirements.txt                    # 依赖：streamlit / requests / pandas
├── config.toml                         # 主题配置备份
├── .streamlit/
│   ├── config.toml                     # Streamlit 主题（知乎蓝 + 应急蓝）
│   └── secrets.toml.example            # 密钥配置示例（复制为 secrets.toml 后填值）
├── .devcontainer/devcontainer.json     # 开发容器配置
├── assets/
│   └── bell.png                        # 原创「应急警钟」IP 形象
├── OAuth申请件_草稿.txt                # OAuth 应用申请邮件草稿
├── 演示视频脚本.md                      # 产品演示视频脚本
├── 灾害应急案例AI萃取助手 项目计划书.pdf  # 项目计划书（最终版）
├── .gitignore
└── README.md
```

## 设计与版权说明

- 项目 IP 形象「**应急警钟**」（警钟 + 水滴 + 翻开的书本，寓意"安全警钟长鸣、知识守护生命"）为**团队原创设计**，版权归本项目团队所有
- 项目早期曾试用知乎官方吉祥物刘看山素材，正式版本已全部移除，未在发布产物中使用第三方受保护 IP
- 应用提取与展示的知乎内容版权归原作者及知乎所有，本项目仅作学习、教学与应急研究用途

## 已知限制

- 知乎 OAuth 登录依赖真实有效的 App ID / App Key；赛事临时测试凭证无法换取 token（服务端返回 `code=20001 Access denied: not exists`），需以开放平台/赛事平台正式分配的凭证为准
- 直答 Agent 输出为生成式结果，虽有强制证据引用机制，关键结论仍建议人工复核后用于真实应急决策
- API 调用受开放平台额度限制，额度耗尽时页面给出友好提示

## 团队

- **品牌名**：知危鉴
- **团队名**：应急有我
- **队长 / 知乎 ID**：木心引力（李鑫，应急管理大学 MPA）
- **赛道**：知识炼金场

---

> 本仓库为知乎黑客松 2026 参赛封存版本（封存于 2026-09-20）。
