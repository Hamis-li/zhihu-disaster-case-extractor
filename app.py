import os
import re
import time
import json
import streamlit as st
import requests
import pandas as pd

# ============================================================
# 灾害应急案例AI萃取助手 - 完整四层流程
# 赛事：知乎黑客松 2026 校园新锐季 · 知识炼金场赛道
# 架构：智能检索 → 案例萃取 → 案例分析 → AI综合复盘
# 接口：按官方 zhihu-cli skill 0.7.2（HTTP API）实现
#       域名 developer.zhihu.com · Bearer Access Secret + X-Request-Timestamp
# 防幻觉：所有结论必附原文证据片段，未提及标记⚠️
# 部署：Streamlit Cloud 兼容，Access Secret 从侧边栏输入不硬编码
# ============================================================


# ===== 知乎开放接口常量（按官方开发者文档 2026-07-16 核验） =====
ZHIHU_API_BASE = "https://developer.zhihu.com"
ZHIHU_SEARCH_PATH = "/api/v1/content/zhihu_search"
ZHIHU_AGENT_PATH = "/v1/chat/completions"
ZHIHU_QUOTA_PATH = "/api/v1/quota"

# 直答模型档位（官方支持）
MODEL_FAST = "zhida-fast-1p5"          # 快速回答
MODEL_THINKING = "zhida-thinking-1p5"  # 深度思考
MODEL_AGENT = "zhida-agent"            # 智能检索生成

# 业务错误码
ERROR_MAP = {
    10001: "参数错误",
    20001: "鉴权失败（请检查 Access Secret）",
    30001: "频率限制或当日额度耗尽",
    90001: "服务内部错误",
}

# 维度定义（用于萃取层）
DIMENSIONS = [
    ("事件概况", "🏗️"),
    ("风险因素", "⚠️"),
    ("应急处置措施", "🚑"),
    ("经验教训", "📚"),
]

# 防幻觉萃取提示词（严格基于原文）
EXTRACT_PROMPT_TEMPLATE = """你是应急案例信息抽取专家，**只允许使用提供的原文内容，严禁编造任何不存在的事件、时间、数据**。
任务：从给定知乎原文，提取下面4个维度内容，每一条结论后面附带【原文证据片段】。
维度列表：
1.事件概况
2.风险因素
3.应急处置措施
4.经验教训

规则：
1. 如果原文没有对应信息，字段内容写：⚠️原文未提及，不需要编造内容。
2. 每一条提取结论，必须附上原文截取的证据片段。
3. 不要扩写、不要推断原文不存在的信息，不要臆测。
输出格式（严格JSON）：
{
  "事件概况": {"内容": "xxx", "证据片段": "原文摘抄xxx"},
  "风险因素": {"内容": "xxx", "证据片段": "原文摘抄xxx"},
  "应急处置措施": {"内容": "xxx", "证据片段": "原文摘抄xxx"},
  "经验教训": {"内容": "xxx", "证据片段": "原文摘抄xxx"}
}
信息完整度统计：统计✅有依据 / ⚠️原文未提及 的维度数量，计算完整度百分比。

原文：
__ORIGINAL_TEXT__
"""


# ============================================================
# 通用工具：鉴权头构造、文本清理
# ============================================================

def build_headers(access_secret):
    """
    构造官方要求的请求头：
    - Authorization: Bearer <Access Secret>
    - X-Request-Timestamp: 秒级 Unix 时间戳（必填，服务端校验）
    - Content-Type: application/json
    """
    return {
        "Authorization": f"Bearer {access_secret}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    }


def clean_text(text):
    """清理搜索摘要中的 <em> 高亮标签（官方 ContentText 可能携带）。"""
    if not text:
        return ""
    return re.sub(r"</?em>", "", text)


def record_error(message):
    st.session_state["last_error"] = message


def env_secret():
    """
    从部署环境读取预置的 Access Secret（公网 Demo 供评委直接使用）。
    优先级：Streamlit secrets -> 环境变量；未配置返回空串。
    本地开发未配置时，走侧边栏输入。
    """
    try:
        s = st.secrets.get("ZHIHU_ACCESS_SECRET", "")
    except Exception:
        s = ""
    return s or os.environ.get("ZHIHU_ACCESS_SECRET", "")


# ============================================================
# 第一层：智能检索 - 函数定义
# ============================================================

def zhihu_search(keyword, access_secret):
    """
    调用知乎搜索API，根据关键词检索灾害应急相关文章。
    官方端点: GET https://developer.zhihu.com/api/v1/content/zhihu_search
    参数: Query（必填）、Count（可选，默认10，最大10）
    额度: 默认每租户每自然日 100 次（以 /api/v1/quota 实际查询为准）

    入参:
        keyword (str): 搜索关键词
        access_secret (str): 开放平台 Access Secret
    返回:
        list[dict]: 搜索结果列表
    """
    if not access_secret:
        return []

    url = ZHIHU_API_BASE + ZHIHU_SEARCH_PATH
    params = {"Query": keyword, "Count": 10}
    try:
        resp = requests.get(url, headers=build_headers(access_secret),
                            params=params, timeout=15)
        data = resp.json()
        code = data.get("Code")
        if code == 0:
            items = data.get("Data", {}).get("Items", []) or []
            results = []
            for it in items:
                results.append({
                    "title": it.get("Title", ""),
                    "id": str(it.get("ContentID", "")),
                    "url": it.get("Url", ""),
                    "excerpt": clean_text(it.get("ContentText", "")),
                    "author": it.get("AuthorName", ""),
                    "voteup": it.get("VoteUpCount", 0),
                })
            return results
        else:
            record_error(f"搜索接口错误 Code {code}: {ERROR_MAP.get(code, data.get('Message', '未知'))}")
            return []
    except Exception as e:
        record_error(f"搜索请求异常: {e}")
        return []


# ============================================================
# 第二层：案例萃取 - 调用直答Agent + 防幻觉提示词
# ============================================================

def call_zhihu_agent(messages, access_secret, model=MODEL_FAST):
    """
    调用知乎直答Agent接口（OpenAI兼容格式）。
    官方端点: POST https://developer.zhihu.com/v1/chat/completions
    支持字段: model / messages / stream（其他字段不保证生效）
    额度: 默认每租户每自然日 100 次（以 /api/v1/quota 实际查询为准）

    入参:
        messages (list): OpenAI格式消息列表
        access_secret (str): 开放平台 Access Secret
        model (str): zhida-fast-1p5 / zhida-thinking-1p5 / zhida-agent
    返回:
        str: agent回答文本
    """
    if not access_secret:
        return ""

    url = ZHIHU_API_BASE + ZHIHU_AGENT_PATH
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    try:
        resp = requests.post(url, headers=build_headers(access_secret),
                             json=payload, timeout=60)
        data = resp.json()
        if data.get("Code") == 0 or "choices" in data:
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            code = data.get("Code")
            record_error(f"直答接口错误 Code {code}: {ERROR_MAP.get(code, data.get('Message', '未知'))}")
            return ""
    except Exception as e:
        record_error(f"直答调用异常: {e}")
        return ""


def extract_case_info(original_text, access_secret, model=MODEL_FAST):
    """
    案例萃取：调用直答Agent，按四维结构化提取案例信息。
    防幻觉：每条结论必附原文证据片段，未提及标记⚠️原文未提及。
    """
    if not original_text:
        return {}

    # 用占位符替换而非 format()：模板内含 JSON 示例大括号，format 会误解析为占位符
    prompt = EXTRACT_PROMPT_TEMPLATE.replace("__ORIGINAL_TEXT__", original_text[:6000])  # 截断防超长
    messages = [
        {"role": "system", "content": "你是应急案例信息抽取专家，严格基于原文，不编造。"},
        {"role": "user", "content": prompt},
    ]
    raw = call_zhihu_agent(messages, access_secret, model=model)
    if not raw:
        return {}

    # 解析JSON（兼容LLM可能包裹```json的情况）
    try:
        # 去除可能的```json代码块包裹
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
            if text.endswith("```"):
                text = text[:-3]
        result = json.loads(text)
    except json.JSONDecodeError:
        # JSON解析失败，尝试用正则提取
        result = {}
        for dim_name, _ in DIMENSIONS:
            pattern = rf'["\']?{dim_name}["\']?\s*:\s*\{{[^}}]*["\']?内容["\']?\s*:\s*["\']([^"\']*)["\'][^}}]*["\']?证据片段["\']?\s*:\s*["\']([^"\']*)["\']'
            m = re.search(pattern, raw, re.DOTALL)
            if m:
                result[dim_name] = {"内容": m.group(1), "证据片段": m.group(2)}

    # 计算完整度
    has_evidence = 0
    missing = 0
    for dim_name, _ in DIMENSIONS:
        content = result.get(dim_name, {}).get("内容", "")
        if "⚠️原文未提及" in content or not content:
            missing += 1
        else:
            has_evidence += 1
    result["completeness"] = {
        "total": len(DIMENSIONS),
        "has_evidence": has_evidence,
        "missing": missing,
        "percent": f"{int(has_evidence / len(DIMENSIONS) * 100)}%",
    }
    return result


# ============================================================
# 第三层：案例分析 - 风险高亮 + 多案例对比 + 共性汇总
# ============================================================

def analyze_risks(extracted_cases):
    """
    风险高亮：从萃取结果中提取所有风险因素，高亮展示。
    """
    risks = []
    for case in extracted_cases:
        title = case.get("title", "未知案例")
        extract = case.get("extract", {})
        risk = extract.get("风险因素", {})
        content = risk.get("内容", "")
        if content and "⚠️原文未提及" not in content:
            risks.append({
                "case": title,
                "risk": content,
                "evidence": risk.get("证据片段", ""),
            })
    return risks


def extract_timeline(case, access_secret, model=MODEL_FAST):
    """
    时间线抽取：调用直答Agent，从案例原文+萃取结果中提取事件演进时间线。
    防幻觉：严格基于原文证据，未提及节点标注⚠️原文未提及。
    """
    original_text = case.get("original_text", "")
    extract = case.get("extract", {})
    title = case.get("title", "")

    # 拼接萃取结果作为辅助材料
    material = f"案例标题: {title}\n\n原文片段:\n{original_text[:4000]}\n\n已萃取四维信息:\n"
    for dim_name, _ in DIMENSIONS:
        d = extract.get(dim_name, {})
        material += f"{dim_name}: {d.get('内容', '')}\n"

    prompt = f"""你是应急案例时间线分析专家。**严格基于提供的原文与萃取信息，严禁编造任何不存在的事件、时间、数据。**
任务：从案例材料中提取事件演进时间线，分为5个节点：预警、发生、响应、处置、复盘。

规则：
1. 如果原文没有对应节点的信息，该节点内容写：⚠️原文未提及，不编造。
2. 每个节点的"动作"必须有原文证据支持。
3. 时间字段如原文未明确，写"原文未明确时间"。

输出格式（严格JSON）：
{{
  "预警": {{"时间": "xxx或原文未明确时间", "动作": "xxx", "证据": "原文摘抄xxx"}},
  "发生": {{"时间": "...", "动作": "...", "证据": "..."}},
  "响应": {{"时间": "...", "动作": "...", "证据": "..."}},
  "处置": {{"时间": "...", "动作": "...", "证据": "..."}},
  "复盘": {{"时间": "...", "动作": "...", "证据": "..."}}
}}

材料：
{material}
"""
    messages = [
        {"role": "system", "content": "你是应急案例时间线分析专家，严格基于原文，不编造。"},
        {"role": "user", "content": prompt},
    ]
    raw = call_zhihu_agent(messages, access_secret, model=model)
    if not raw:
        return {}

    # 解析JSON
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
            if text.endswith("```"):
                text = text[:-3]
        result = json.loads(text)
    except json.JSONDecodeError:
        # 解析失败，返回空节点占位
        result = {node: {"时间": "解析失败", "动作": "—", "证据": "—"} for node in ["预警", "发生", "响应", "处置", "复盘"]}
    return result


def compare_cases_table(extracted_cases):
    """
    多案例横向对比：构建对比表格 DataFrame。
    """
    rows = []
    for case in extracted_cases:
        title = case.get("title", "未知案例")
        extract = case.get("extract", {})
        row = {"案例名称": title}
        for dim_name, _ in DIMENSIONS:
            content = extract.get(dim_name, {}).get("内容", "")
            row[dim_name] = content if content else "—"
        rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["案例名称"] + [d[0] for d in DIMENSIONS])


def summarize_commonality(extracted_cases, access_secret, model=MODEL_FAST):
    """
    共性汇总：调用直答Agent汇总共性风险、高频处置、共性短板。
    严格基于已萃取的原文证据，不编造。
    """
    if not extracted_cases:
        return ""

    material = ""
    for i, case in enumerate(extracted_cases, 1):
        material += f"\n【案例{i}: {case.get('title', '')}】\n"
        extract = case.get("extract", {})
        for dim_name, _ in DIMENSIONS:
            d = extract.get(dim_name, {})
            material += f"{dim_name}: {d.get('内容', '')}\n证据: {d.get('证据片段', '')}\n"

    prompt = f"""你是应急案例分析专家。基于以下多个案例的萃取信息（含原文证据），总结共性问题。
**严格基于提供的材料，不编造任何未提及的事件或数据。**

材料：
{material}

请输出：
1. 共性风险（多个案例共同暴露的风险点）
2. 高频处置措施（多次出现的有效处置做法）
3. 共性短板（多个案例共同存在的问题）

每条结论后附【证据案例编号】。如果材料不足，标注⚠️材料不足。"""

    messages = [
        {"role": "system", "content": "你是应急案例分析专家，严格基于材料，不编造。"},
        {"role": "user", "content": prompt},
    ]
    return call_zhihu_agent(messages, access_secret, model=model)


# ============================================================
# 第四层：AI综合复盘 - 知识卡片 + 复盘报告 + 追问
# ============================================================

def generate_knowledge_card(extracted_cases, access_secret, model=MODEL_FAST):
    """
    生成结构化知识卡片：提炼核心知识点。
    严格基于原文证据，不编造。
    """
    if not extracted_cases:
        return ""

    material = ""
    for i, case in enumerate(extracted_cases, 1):
        material += f"\n【案例{i}: {case.get('title', '')}】\n"
        extract = case.get("extract", {})
        for dim_name, _ in DIMENSIONS:
            d = extract.get(dim_name, {})
            material += f"{dim_name}: {d.get('内容', '')}\n"

    prompt = f"""你是应急知识整理专家。基于以下案例萃取信息，提炼可复用的核心知识点生成知识卡片。
**严格基于提供的材料，不编造未提及的内容。**

材料：
{material}

输出格式：
## 知识卡片
### 核心要点
- 要点1（来源：案例N）
- 要点2
### 处置原则
- 原则1
### 注意事项
- 注意1
如果材料不足，标注⚠️材料不足。"""

    messages = [
        {"role": "system", "content": "你是应急知识整理专家，严格基于材料。"},
        {"role": "user", "content": prompt},
    ]
    return call_zhihu_agent(messages, access_secret, model=model)


def generate_review_report(extracted_cases, commonality_summary, access_secret, model=MODEL_FAST):
    """
    生成结构化复盘报告：整合多案例。
    严格基于原文证据，不编造。
    """
    if not extracted_cases:
        return ""

    material = ""
    for i, case in enumerate(extracted_cases, 1):
        material += f"\n【案例{i}: {case.get('title', '')}】\n"
        extract = case.get("extract", {})
        for dim_name, _ in DIMENSIONS:
            d = extract.get(dim_name, {})
            material += f"{dim_name}: {d.get('内容', '')}\n证据: {d.get('证据片段', '')}\n"

    prompt = f"""你是应急复盘专家。基于以下案例萃取信息与共性分析，生成结构化复盘报告。
**严格基于提供的材料，不编造任何未提及的事件、数据、时间。**

案例材料：
{material}

共性分析：
{commonality_summary}

输出格式：
## 灾害应急复盘报告
### 一、事件概述
（基于案例材料汇总）
### 二、风险分析
（基于共性风险）
### 三、处置评估
（基于高频处置）
### 四、问题与改进
（基于共性短板）
### 五、可复用经验
（基于知识要点）
如果某部分材料不足，标注⚠️材料不足，不编造。"""

    messages = [
        {"role": "system", "content": "你是应急复盘专家，严格基于材料，不编造。"},
        {"role": "user", "content": prompt},
    ]
    return call_zhihu_agent(messages, access_secret, model=model)


def ask_followup(question, extracted_cases, access_secret, model=MODEL_FAST):
    """
    追问：用户针对案例提问，LLM基于原文证据回答。
    严格基于原文，不编造。
    """
    if not question or not extracted_cases:
        return ""

    material = ""
    for i, case in enumerate(extracted_cases, 1):
        material += f"\n【案例{i}: {case.get('title', '')}】\n"
        extract = case.get("extract", {})
        for dim_name, _ in DIMENSIONS:
            d = extract.get(dim_name, {})
            material += f"{dim_name}: {d.get('内容', '')}\n证据: {d.get('证据片段', '')}\n"
        material += f"原文链接: {case.get('url', '')}\n"

    prompt = f"""你是应急案例问答助手。基于以下案例材料（含原文证据片段），回答用户问题。
**严格基于提供的材料，不编造未提及的事件、数据、时间。如果材料中没有答案，明确回答"根据已有材料无法回答此问题"。**

案例材料：
{material}

用户问题：{question}

回答格式：
【回答】
（基于材料的回答，附【案例N】来源标注）
【依据】
（引用原文证据片段）"""

    messages = [
        {"role": "system", "content": "你是应急案例问答助手，严格基于材料，不编造。"},
        {"role": "user", "content": prompt},
    ]
    return call_zhihu_agent(messages, access_secret, model=model)


# ============================================================
# 额度查询（官方 /api/v1/quota，查询本身不消耗业务额度）
# ============================================================

def get_quota(access_secret):
    """
    查询当前 Access Secret 所属账号的当日开放 API 额度。
    官方端点: GET https://developer.zhihu.com/api/v1/quota
    返回:
        list[dict]: [{"APIID":..., "APIName":..., "TotalQuota":..., "TotalUsed":..., "RemainingQuota":...}]
    """
    if not access_secret:
        return []
    url = ZHIHU_API_BASE + ZHIHU_QUOTA_PATH
    try:
        resp = requests.get(url, headers=build_headers(access_secret), timeout=15)
        data = resp.json()
        if data.get("Code") == 0:
            return data.get("Data", []) or []
        else:
            code = data.get("Code")
            record_error(f"额度查询错误 Code {code}: {ERROR_MAP.get(code, data.get('Message', '未知'))}")
            return []
    except Exception as e:
        record_error(f"额度查询异常: {e}")
        return []


# ============================================================
# 缓存装饰器（减少重复调用，适配限额）
# ============================================================

@st.cache_data(show_spinner=False, ttl=3600)
def cached_search(keyword, access_secret):
    return zhihu_search(keyword, access_secret)


@st.cache_data(show_spinner=False, ttl=3600)
def cached_extract(text_hash, original_text, access_secret, model):
    return extract_case_info(original_text, access_secret, model)


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(page_title="灾害应急案例AI萃取助手", page_icon="🌊", layout="wide")

# ============================================================
# 知乎蓝主题：专业蓝 · 知乎系配色（#0066FF 主色 + 蓝白灰文字层级）
# ============================================================
st.markdown("""<style>
html, body, [data-testid="stAppViewContainer"] { background: #F8FAFC !important; }
[data-testid="stHeader"] { background: rgba(248,250,252,0.9) !important; }
[data-testid="stSidebar"] { background: #FFFFFF !important; border-right: 1px solid #E2E8F0 !important; }
.block-container { padding-top: 1.6rem !important; max-width: 1280px; }
h1 { font-family: 'Helvetica Neue', 'Microsoft YaHei', 'PingFang SC', sans-serif !important; font-weight: 900 !important; font-size: 2.4rem !important; color: #0F172A !important; letter-spacing: -0.02em !important; }
h2 { font-family: 'Helvetica Neue', 'Microsoft YaHei', sans-serif !important; font-weight: 800 !important; font-size: 1.4rem !important; color: #0F172A !important; }
h3 { font-family: 'Helvetica Neue', 'Microsoft YaHei', sans-serif !important; font-weight: 700 !important; color: #0F172A !important; }
p, .stMarkdown { color: #334155 !important; font-family: 'Helvetica Neue', 'Microsoft YaHei', Arial, sans-serif !important; line-height: 1.75 !important; }
[data-testid="stCaptionContainer"] p { color: #64748B !important; font-size: 0.84rem !important; }
.stButton > button, [data-testid="stBaseButton"] button { background: #4D9FFF !important; color: #FFFFFF !important; border: none !important; border-radius: 8px !important; font-weight: 600 !important; letter-spacing: 0.02em !important; }
.stButton > button:hover, [data-testid="stBaseButton"] button:hover { background: #3B8BF5 !important; color: #FFF !important; }
.stButton > button:active, [data-testid="stBaseButton"] button:active { background: #2E7AE6 !important; }
.stTextInput input, [data-testid="stTextInput"] input { background: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-radius: 8px !important; color: #0F172A !important; box-shadow: none !important; }
.stTextInput input:focus { border-color: #0066FF !important; box-shadow: 0 0 0 3px #E6F0FF !important; }
[data-testid="stMetric"] { background: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-left: 4px solid #0066FF !important; border-radius: 8px !important; padding: 10px 14px !important; }
[data-testid="stMetricLabel"] { color: #64748B !important; font-weight: 700 !important; text-transform: uppercase; font-size: 0.75rem !important; }
[data-testid="stMetricValue"] { color: #0F172A !important; font-weight: 900 !important; font-size: 1.8rem !important; }
hr { border-color: #E2E8F0 !important; }
[data-testid="stDataFrame"] { border: 1px solid #E2E8F0 !important; border-radius: 8px !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] > div { background: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-radius: 8px !important; }
[data-testid="stSuccess"] { border-left: 4px solid #10B981 !important; border-radius: 8px !important; }
[data-testid="stInfo"] { border-left: 4px solid #0066FF !important; border-radius: 8px !important; }
[data-testid="stWarning"] { border-left: 4px solid #F59E0B !important; border-radius: 8px !important; }
[data-testid="stError"] { border-left: 4px solid #EF4444 !important; border-radius: 8px !important; }
</style>""", unsafe_allow_html=True)

# 知乎蓝头部：巨型粗体标题 + 知乎蓝渐变封面
st.markdown("""
<div style="background: linear-gradient(120deg, #0066FF, #0044AA); color: #FFFFFF; padding: 26px 30px; margin-bottom: 10px; border-radius: 10px;">
  <div style="font-family: 'Helvetica Neue', 'Microsoft YaHei', sans-serif; font-weight: 900; font-size: 38px; letter-spacing: -0.02em; line-height: 1.15;">灾害应急案例AI萃取助手</div>
  <div style="font-size: 13px; opacity: 0.92; font-weight: 500; margin-top: 6px; letter-spacing: 0.02em;">知乎黑客松 2026 · 知识炼金场 · 智能检索 → 案例萃取 → 案例分析 → AI综合复盘</div>
</div>
""", unsafe_allow_html=True)

# 初始化session_state
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "extracted_cases" not in st.session_state:
    st.session_state.extracted_cases = []
if "last_error" not in st.session_state:
    st.session_state.last_error = ""

# ===== 侧边栏：Access Secret 配置与API测试 =====
# 部署环境（Streamlit secrets / 环境变量）已预置密钥时，评委可直接使用，无需输入
_deploy_secret = env_secret()
with st.sidebar:
    st.image("assets/kanshan/idle.gif", width=96)
    st.caption("🐾 刘看山陪你学习应急案例")
    st.header("⚙️ 配置")
    if _deploy_secret:
        st.caption("✅ 已从部署环境加载密钥，可直接使用；如需覆盖可输入")
    else:
        st.caption("Access Secret 仅在本会话使用，不保存到文件/服务器")
    access_secret_input = st.text_input("Access Secret", type="password",
                                        placeholder="知乎开放平台个人中心获取，部署环境已配置时可留空",
                                        key="cfg_secret")
    access_secret = access_secret_input or _deploy_secret

    st.markdown("---")
    st.subheader("🤖 直答模型档位")
    st.caption("萃取/分析/复盘使用，深度思考更稳但更慢")
    model_choice = st.selectbox(
        "模型",
        options=[MODEL_FAST, MODEL_THINKING, MODEL_AGENT],
        format_func=lambda m: {
            MODEL_FAST: "快速回答 (zhida-fast-1p5)",
            MODEL_THINKING: "深度思考 (zhida-thinking-1p5)",
            MODEL_AGENT: "智能检索 (zhida-agent)",
        }[m],
        index=0,
        key="cfg_model",
    )

    st.markdown("---")
    st.subheader("🔬 API 连通性测试")
    st.caption("验证 Access Secret 能否调通知乎搜索接口")
    if st.button("测试API", type="primary"):
        if not access_secret:
            st.warning("请先填写 Access Secret")
        else:
            with st.spinner("测试中..."):
                results = zhihu_search("地震应急", access_secret)
            if results:
                st.success(f"✅ 连通正常，返回 {len(results)} 条")
                st.json(results[:2])
            else:
                err = st.session_state.get("last_error", "未知错误")
                st.error(f"❌ 失败: {err}")

    st.markdown("---")
    st.subheader("📊 今日额度")
    if st.button("查询剩余额度"):
        if not access_secret:
            st.warning("请先填写 Access Secret")
        else:
            with st.spinner("查询中..."):
                quota_list = get_quota(access_secret)
            if quota_list:
                quota_df = pd.DataFrame(quota_list)
                quota_df.columns = ["APIID", "名称", "总额度", "已用", "剩余"]
                st.dataframe(quota_df, use_container_width=True, hide_index=True)
            else:
                err = st.session_state.get("last_error", "未知错误")
                st.error(f"查询失败: {err}")
    st.caption("默认每个能力每自然日 100 次，注意节约")
    st.write(f"已萃取案例数: {len(st.session_state.extracted_cases)}")

st.markdown("---")

# ============================================================
# 第一层：智能检索区
# ============================================================
st.markdown('<div style="font-family: \'Helvetica Neue\', \'Microsoft YaHei\', sans-serif; font-size: 1.45rem; font-weight: 800; color: #0066FF; margin: 1.4rem 0 0.1rem;">🔍 第一层 · 智能检索</div>', unsafe_allow_html=True)
st.caption("输入关键词或点击快捷按钮，调用知乎搜索API检索灾害应急相关文章")

col_kw, col_btn = st.columns([4, 1])
with col_kw:
    keyword = st.text_input(
        "检索关键词",
        key="search_keyword",
        placeholder="例如：城市内涝、台风灾害、矿山事故、地质灾害应对",
    )
with col_btn:
    st.write("")
    search_btn = st.button("检索", type="primary", use_container_width=True)

st.markdown("**热门案例快捷检索：**")
quick_cols = st.columns(4)
quick_cases = ["城市内涝", "台风", "矿山事故", "地质灾害"]
quick_clicked = None
for i, case in enumerate(quick_cases):
    with quick_cols[i]:
        if st.button(case, key=f"quick_{case}", use_container_width=True):
            quick_clicked = case

# 检索触发
trigger = search_btn or quick_clicked is not None
if trigger:
    actual_kw = keyword or quick_clicked or ""
    if not access_secret:
        st.warning("请先在左侧填写 Access Secret")
    elif not actual_kw:
        st.warning("请输入关键词或选择快捷案例")
    else:
        with st.spinner(f"正在检索：{actual_kw}..."):
            results = cached_search(actual_kw, access_secret)
        if results:
            st.session_state.search_results = results
            st.success(f"找到 {len(results)} 条结果")
        else:
            err = st.session_state.get("last_error", "")
            st.info(f"暂无结果。{('原因: ' + err) if err else ''}")

# 展示检索结果 + 选择文章萃取
if st.session_state.search_results:
    st.markdown("### 检索结果（勾选文章后点击萃取）")
    selected_indices = []
    for i, item in enumerate(st.session_state.search_results):
        col_chk, col_show = st.columns([1, 10])
        with col_chk:
            if st.checkbox("", key=f"chk_{i}"):
                selected_indices.append(i)
        with col_show:
            st.markdown(f"**{item.get('title', '无标题')}**")
            st.caption(f"id: {item.get('id', '')} | 作者: {item.get('author', '')} | url: {item.get('url', '')}")
            if item.get("excerpt"):
                st.write(item["excerpt"][:200] + "...")

    if selected_indices and st.button("🃏 萃取选中案例", type="primary"):
        if not access_secret:
            st.warning("请先填写 Access Secret")
        else:
            st.session_state.extracted_cases = []
            progress = st.progress(0)
            for idx, i in enumerate(selected_indices):
                item = st.session_state.search_results[i]
                with st.spinner(f"萃取: {item.get('title', '')[:30]}..."):
                    # 官方搜索返回的 ContentText 即原文素材（含较完整内容文本），直接作为萃取输入
                    original_text = item.get("excerpt", "")
                    if not original_text:
                        st.warning(f"「{item.get('title', '')}」无内容摘要，已跳过")
                        continue
                    import hashlib
                    text_hash = hashlib.md5(original_text.encode()).hexdigest()
                    extract = cached_extract(text_hash, original_text, access_secret, model_choice)
                    if extract:
                        st.session_state.extracted_cases.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "id": item.get("id", ""),
                            "original_text": original_text,
                            "extract": extract,
                        })
                progress.progress((idx + 1) / len(selected_indices))
            if st.session_state.extracted_cases:
                st.success(f"✅ 萃取完成，共 {len(st.session_state.extracted_cases)} 个案例")
                col_c, _, _ = st.columns([1, 3, 1])
                col_c.image("assets/kanshan/wave.gif", width=72)
                col_c.caption("刘看山：案例已就绪，去分析吧！")
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"萃取失败，请检查 Access Secret 与接口连通性。{('原因: ' + err) if err else ''}")

st.markdown("---")

# ============================================================
# 第二层：案例萃取卡片区
# ============================================================
st.markdown('<div style="font-family: \'Helvetica Neue\', \'Microsoft YaHei\', sans-serif; font-size: 1.45rem; font-weight: 800; color: #10B981; margin: 1.4rem 0 0.1rem;">🃏 第二层 · 案例萃取</div>', unsafe_allow_html=True)
st.caption("从原文提取四维信息，每条结论附原文证据片段，未提及标记⚠️")

if not st.session_state.extracted_cases:
    st.info("暂无萃取结果，请先在第一层检索并萃取案例")
else:
    for case_idx, case in enumerate(st.session_state.extracted_cases):
        st.markdown(f"### 案例{case_idx + 1}: {case.get('title', '未知')}")
        extract = case.get("extract", {})
        completeness = extract.get("completeness", {})

        # 完整度展示
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            st.metric("完整度", completeness.get("percent", "—"))
        with col_c2:
            st.metric("✅ 有依据", completeness.get("has_evidence", 0))
        with col_c3:
            st.metric("⚠️ 未提及", completeness.get("missing", 0))

        # 四维卡片
        dim_cols = st.columns(4)
        for i, (dim_name, dim_emoji) in enumerate(DIMENSIONS):
            with dim_cols[i]:
                d = extract.get(dim_name, {})
                content = d.get("内容", "")
                evidence = d.get("证据片段", "")
                is_missing = "⚠️原文未提及" in content or not content
                status = "⚠️" if is_missing else "✅"
                st.markdown(f"**{dim_emoji} {dim_name}** {status}")
                st.markdown(content if content else "—")
                if evidence and not is_missing:
                    st.caption(f"📖 证据: {evidence[:150]}...")

        # 原文溯源
        st.markdown(f"🔗 原文来源: [{case.get('url', '无链接')}]({case.get('url', '')})")
        st.markdown("---")

# ============================================================
# 第三层：案例分析区
# ============================================================
st.markdown('<div style="font-family: \'Helvetica Neue\', \'Microsoft YaHei\', sans-serif; font-size: 1.45rem; font-weight: 800; color: #EF4444; margin: 1.4rem 0 0.1rem;">📊 第三层 · 案例分析</div>', unsafe_allow_html=True)
st.caption("风险高亮、多案例横向对比、共性汇总")

if not st.session_state.extracted_cases:
    st.info("暂无案例可分析，请先萃取")
else:
    # 风险高亮
    st.subheader("⚠️ 风险高亮")
    risks = analyze_risks(st.session_state.extracted_cases)
    if risks:
        for r in risks:
            st.markdown(f"**{r['case']}**")
            st.markdown(f"- 风险: {r['risk']}")
            st.caption(f"📖 证据: {r['evidence'][:200]}")
    else:
        st.info("暂无风险因素（所有案例均标注⚠️原文未提及）")

    # 时间线
    st.subheader("⏱️ 事件时间线")
    st.caption("从原文提取事件演进5节点，每节点附原文证据，未提及标注⚠️")
    if access_secret:
        case_options = [c.get("title", f"案例{i+1}") for i, c in enumerate(st.session_state.extracted_cases)]
        selected_case_idx = st.selectbox("选择案例", range(len(case_options)), format_func=lambda x: case_options[x], key="timeline_case_sel")
        if st.button("生成时间线", type="primary"):
            with st.spinner("正在抽取时间线..."):
                timeline = extract_timeline(st.session_state.extracted_cases[selected_case_idx], access_secret, model_choice)
            if timeline:
                timeline_cols = st.columns(5)
                timeline_nodes = ["预警", "发生", "响应", "处置", "复盘"]
                for i, node in enumerate(timeline_nodes):
                    with timeline_cols[i]:
                        node_data = timeline.get(node, {})
                        is_missing = "⚠️" in node_data.get("动作", "") or not node_data.get("动作")
                        status = "⚠️" if is_missing else "✅"
                        st.markdown(f"**{node}** {status}")
                        st.markdown(f"⏰ {node_data.get('时间', '—')}")
                        st.markdown(f"📝 {node_data.get('动作', '—')}")
                        ev = node_data.get("证据", "")
                        if ev and "⚠️" not in ev:
                            st.caption(f"📖 {ev[:120]}")
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"时间线抽取失败. {err}")
    else:
        st.warning("请先填写 Access Secret")

    # 多案例对比表格
    st.subheader("📋 多案例横向对比")
    df = compare_cases_table(st.session_state.extracted_cases)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 共性汇总
    st.subheader("🔍 共性汇总")
    if access_secret and st.button("生成共性分析", type="primary"):
        with st.spinner("正在调用Agent生成共性分析..."):
            summary = summarize_commonality(st.session_state.extracted_cases, access_secret, model_choice)
        if summary:
            st.markdown(summary)
        else:
            err = st.session_state.get("last_error", "")
            st.error(f"生成失败. {err}")

st.markdown("---")

# ============================================================
# 第四层：AI综合复盘区
# ============================================================
st.markdown('<div style="font-family: \'Helvetica Neue\', \'Microsoft YaHei\', sans-serif; font-size: 1.45rem; font-weight: 800; color: #8B5CF6; margin: 1.4rem 0 0.1rem;">🧠 第四层 · AI综合复盘</div>', unsafe_allow_html=True)
st.caption("知识卡片、复盘报告、追问深入")

if not st.session_state.extracted_cases:
    st.info("暂无案例可复盘，请先萃取")
else:
    preview_cols = st.columns(2)
    with preview_cols[0]:
        st.subheader("📇 知识卡片")
        if st.button("生成知识卡片", type="primary"):
            with st.spinner("生成中..."):
                card = generate_knowledge_card(st.session_state.extracted_cases, access_secret, model_choice)
            if card:
                st.markdown(card)
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"失败. {err}")
    with preview_cols[1]:
        st.subheader("📝 复盘报告")
        if st.button("生成复盘报告", type="primary"):
            with st.spinner("生成中..."):
                summary = summarize_commonality(st.session_state.extracted_cases, access_secret, model_choice)
                report = generate_review_report(st.session_state.extracted_cases, summary, access_secret, model_choice)
            if report:
                st.markdown(report)
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"失败. {err}")

    # 追问
    st.markdown("---")
    st.subheader("💬 追问深入")
    ask_cols = st.columns([4, 1])
    with ask_cols[0]:
        ask_input = st.text_input("针对案例或复盘报告追问", key="ask_input", placeholder="例如：这次救援中物资调度为何滞后？")
    with ask_cols[1]:
        st.write("")
        ask_btn = st.button("追问", type="primary", use_container_width=True)

    if ask_btn:
        if not ask_input:
            st.warning("请输入追问内容")
        elif not access_secret:
            st.warning("请先填写 Access Secret")
        else:
            with st.spinner("正在追问..."):
                answer = ask_followup(ask_input, st.session_state.extracted_cases, access_secret, model_choice)
            if answer:
                st.markdown(answer)
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"追问失败. {err}")

# 底部说明
st.markdown("---")
st.caption("🏗️ 所有信息严格基于原文证据片段 · 未提及标记⚠️原文未提及 · 防范大模型幻觉")
