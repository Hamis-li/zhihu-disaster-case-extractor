import os
import re
import time
import json
import hashlib
import streamlit as st
import requests
import pandas as pd
import concurrent.futures

# ============================================================
# 知危鉴 · 灾害应急案例AI萃取助手 - 完整四层流程
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

# ===== 知乎 OAuth 登录（黑客松官方协议 2026-09-13 核验） =====
ZHIHU_OAUTH_AUTHORIZE = "https://openapi.zhihu.com/authorize"      # 授权页
ZHIHU_OAUTH_TOKEN = "https://openapi.zhihu.com/access_token"       # 换 token
ZHIHU_OAUTH_USER = "https://openapi.zhihu.com/user"                # 用户基础信息
# 默认回调地址 = 提报表单登记的 Demo 链接（提报时回调留空=作品链接）
DEFAULT_REDIRECT_URI = "https://zhihu-disaster-case-extractor-kqojwnlr7fdrzhesgew7ma.streamlit.app"

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
4. 标题、作者、评论可作为辅助理解上下文，但证据片段只能引用正文原文。
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
# 知乎 OAuth 登录（人气奖：接入知乎登录的用户数）
# 官方协议：authorize 授权 -> authorization_code 回调 -> access_token -> /user
# App ID / App Key 由黑客松赛事页面分配，配置在 Streamlit Secrets：
#   ZHIHU_OAUTH_APP_ID / ZHIHU_OAUTH_APP_KEY / ZHIHU_OAUTH_REDIRECT_URI（可选）
# ============================================================

def oauth_config():
    """
    读取 OAuth 配置。优先级：Streamlit secrets -> 环境变量。
    App ID 可公开；App Key 必须留在服务端（secrets），不进源码。
    返回 (app_id, app_key, redirect_uri)；未配置返回空串。
    """
    try:
        app_id = st.secrets.get("ZHIHU_OAUTH_APP_ID", "")
    except Exception:
        app_id = ""
    try:
        app_key = st.secrets.get("ZHIHU_OAUTH_APP_KEY", "")
    except Exception:
        app_key = ""
    try:
        redirect_uri = st.secrets.get("ZHIHU_OAUTH_REDIRECT_URI", "")
    except Exception:
        redirect_uri = ""
    app_id = app_id or os.environ.get("ZHIHU_OAUTH_APP_ID", "")
    app_key = app_key or os.environ.get("ZHIHU_OAUTH_APP_KEY", "")
    redirect_uri = redirect_uri or os.environ.get("ZHIHU_OAUTH_REDIRECT_URI", "") or DEFAULT_REDIRECT_URI
    return app_id, app_key, redirect_uri


def build_oauth_auth_url(app_id, redirect_uri, state):
    """
    构造知乎授权页 URL（OAuth2 authorization code flow）。
    参数：redirect_uri / app_id / response_type=code / state
    """
    import urllib.parse
    params = {
        "redirect_uri": redirect_uri,
        "app_id": app_id,
        "response_type": "code",
        "state": state,
    }
    return ZHIHU_OAUTH_AUTHORIZE + "?" + urllib.parse.urlencode(params)


def exchange_oauth_token(app_id, app_key, redirect_uri, code):
    """
    用 authorization_code 换取 OAuth access_token。
    官方端点: POST https://openapi.zhihu.com/access_token (x-www-form-urlencoded)
    返回 (access_token, error_msg)：成功时 error_msg 为空串；失败时 token 为空串并带服务端原文错误。
    """
    try:
        resp = requests.post(
            ZHIHU_OAUTH_TOKEN,
            data={
                "app_id": app_id,
                "app_key": app_key,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
        token = data.get("access_token") or (data.get("data") or {}).get("access_token") if isinstance(data.get("data"), dict) else data.get("access_token")
        if token:
            return token, ""
        # 透传服务端真实错误（如 code=20001 "Access denied: not exists"），便于定位
        biz_msg = data.get("data") if isinstance(data.get("data"), str) else data.get("message") or data.get("Message")
        err = f"服务端返回 code={data.get('code')}：{biz_msg or '未返回 access_token'}"
        record_error(f"OAuth 换取 token 失败: {err}")
        return "", err
    except Exception as e:
        record_error(f"OAuth 换取 token 异常: {e}")
        return "", f"请求异常：{e}"


def _parse_oauth_user(data):
    """从 /user 响应中提取用户对象（兼容 data/Data/顶层三种包裹，成功判据看 fullname/uid/name）。"""
    if not isinstance(data, dict):
        return None
    source = data.get("data") or data.get("Data") or data.get("user") or data
    if isinstance(source, dict) and (source.get("fullname") or source.get("Fullname") or source.get("uid") or source.get("name")):
        return source
    return None


def fetch_oauth_user(access_token, access_secret):
    """
    获取授权用户基础信息 GET https://openapi.zhihu.com/user。
    官方两份材料鉴权写法不一致，这里两种都试，任一成功即可：
      方式A（官方可运行模板 oauth.mjs，三头）：
        Authorization: Bearer <Access Secret> + X-OAuth-Token: <OAuth token> + X-Request-Timestamp
      方式B（oauth.md 协议文档，单头）：
        Authorization: Bearer <OAuth access_token>
    成功返回用户 dict；失败返回 None。
    """
    if not access_token:
        return None
    ts = str(int(time.time()))
    attempts = []
    if access_secret:
        attempts.append({
            "Authorization": f"Bearer {access_secret}",
            "X-OAuth-Token": access_token,
            "X-Request-Timestamp": ts,
            "Content-Type": "application/json",
        })
    attempts.append({
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    })
    last_err = ""
    for headers in attempts:
        try:
            resp = requests.get(ZHIHU_OAUTH_USER, headers=headers, timeout=15)
            user = _parse_oauth_user(resp.json())
            if user:
                return user
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = str(e)
    record_error(f"OAuth 获取用户信息失败: {last_err}")
    return None


def handle_oauth_callback():
    """
    处理 OAuth 回调：页面 URL 携带 authorization_code（兼容 code）时，
    换取 token -> 拉取用户信息 -> 存入 session_state -> 清理 URL 参数。
    在侧边栏/主界面渲染前调用。
    """
    try:
        qp = st.query_params
    except Exception:
        return

    code = qp.get("authorization_code") or qp.get("code")
    if not code:
        return
    if isinstance(code, list):
        code = code[0] if code else ""
    if not code:
        return

    # 已登录则忽略重复回调
    if st.session_state.get("oauth_user"):
        _clear_oauth_params(qp)
        return

    app_id, app_key, redirect_uri = oauth_config()
    if not (app_id and app_key):
        st.session_state["oauth_error"] = "OAuth 未配置（缺少 App ID / App Key）"
        _clear_oauth_params(qp)
        return

    # state 校验：官方黑客松协议支持 state 透传；若平台未返回则不阻塞登录
    state = qp.get("state")
    if isinstance(state, list):
        state = state[0] if state else ""
    saved_state = st.session_state.get("oauth_state", "")
    if saved_state and state and state != saved_state:
        st.session_state["oauth_error"] = "登录校验失败（state 不匹配），请重试"
        _clear_oauth_params(qp)
        return

    with st.spinner("正在完成知乎登录..."):
        access_secret = env_secret()
        access_token, token_err = exchange_oauth_token(app_id, app_key, redirect_uri, code)
        if access_token:
            user = fetch_oauth_user(access_token, access_secret)
            if user:
                user["_oauth_token"] = access_token
                st.session_state["oauth_user"] = user
                st.session_state.pop("oauth_error", None)
            else:
                # token 已换到（授权有效），仅用户信息拉取失败：仍建立登录态，昵称降级显示
                st.session_state["oauth_user"] = {"fullname": "知乎用户", "_oauth_token": access_token, "_profile_pending": True}
                st.session_state["oauth_error"] = "已登录，但用户资料拉取失败（不影响登录状态）"
        else:
            # 透传真实原因，便于区分：凭证无效 / code 过期 / 回调地址不匹配
            st.session_state["oauth_error"] = f"登录失败（换取令牌被拒）：{token_err}"

    st.session_state.pop("oauth_state", None)
    _clear_oauth_params(qp)
    st.rerun()


def _clear_oauth_params(qp):
    """清理回调后残留的 URL 参数（authorization_code / code / state）。"""
    for key in ("authorization_code", "code", "state"):
        try:
            if key in qp:
                del qp[key]
        except Exception:
            pass


# ============================================================
# 第一层：智能检索 - 函数定义
# ============================================================

def zhihu_search(keyword, access_secret, count=10):
    """
    调用知乎搜索API，根据关键词检索灾害应急相关文章。
    官方端点: GET https://developer.zhihu.com/api/v1/content/zhihu_search
    参数: Query（必填）、Count（可选，默认10，最大10）
    额度: 默认每租户每自然日 100 次（以 /api/v1/quota 实际查询为准）

    入参:
        keyword (str): 搜索关键词
        access_secret (str): 开放平台 Access Secret
        count (int): 返回数量，最大10
    返回:
        list[dict]: 搜索结果列表
    """
    if not access_secret:
        return []

    url = ZHIHU_API_BASE + ZHIHU_SEARCH_PATH
    params = {"Query": keyword, "Count": min(count, 10)}
    try:
        resp = requests.get(url, headers=build_headers(access_secret),
                            params=params, timeout=15)
        data = resp.json()
        code = data.get("Code")
        if code == 0:
            items = data.get("Data", {}).get("Items", []) or []
            results = []
            for it in items:
                comment_list = it.get("CommentInfoList") or []
                results.append({
                    "title": it.get("Title", ""),
                    "id": str(it.get("ContentID", "")),
                    "url": it.get("Url", ""),
                    "excerpt": clean_text(it.get("ContentText", "")),
                    "author": it.get("AuthorName", ""),
                    "voteup": it.get("VoteUpCount", 0),
                    "comments": [c.get("Content", "") for c in comment_list if c.get("Content")],
                    "authority_level": it.get("AuthorityLevel", ""),
                    "content_type": it.get("ContentType", ""),
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
    超时自动重试 1 次；额度耗尽返回友好提示。

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
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=build_headers(access_secret),
                                 json=payload, timeout=60)
            data = resp.json()
            if data.get("Code") == 0 or "choices" in data:
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                code = data.get("Code")
                if code == 30001:
                    record_error("今日直答额度已用完，请在侧边栏查看额度，明日重置")
                else:
                    record_error(f"直答接口错误 Code {code}: {ERROR_MAP.get(code, data.get('Message', '未知'))}")
                return ""
        except requests.exceptions.Timeout:
            if attempt == 0:
                time.sleep(1)
                continue
            record_error("直答调用超时（已重试1次），请稍后重试")
            return ""
        except Exception as e:
            record_error(f"直答调用异常: {e}")
            return ""
    return ""


def extract_case_info(original_text, access_secret, model=MODEL_FAST, title="", author="", comments=None):
    """
    案例萃取：调用直答Agent，按四维结构化提取案例信息。
    防幻觉：每条结论必附原文证据片段，未提及标记⚠️原文未提及。
    萃取输入包含标题/作者/精选评论作为辅助上下文，提升信息覆盖度。
    """
    if not original_text:
        return {}

    # 构造辅助上下文（不替代原文，仅帮助 LLM 理解场景）
    context_parts = []
    if title:
        context_parts.append(f"【标题】{title}")
    if author:
        context_parts.append(f"【作者】{author}")
    if comments:
        comment_text = " | ".join(comments[:3])
        context_parts.append(f"【精选评论】{comment_text}")
    context = "\n".join(context_parts)

    full_input = f"{context}\n\n原文：\n{original_text[:6000]}" if context else original_text[:6000]
    # 用占位符替换而非 format()：模板内含 JSON 示例大括号，format 会误解析为占位符
    prompt = EXTRACT_PROMPT_TEMPLATE.replace("__ORIGINAL_TEXT__", full_input)
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
    多案例横向对比：构建对比表格 DataFrame，含结构化维度。
    """
    rows = []
    for case in extracted_cases:
        title = case.get("title", "未知案例")
        extract = case.get("extract", {})
        comp = extract.get("completeness", {})
        row = {"案例名称": title[:20]}
        row["完整度"] = comp.get("percent", "—")
        row["有依据"] = comp.get("has_evidence", 0)
        row["未提及"] = comp.get("missing", 0)
        for dim_name, _ in DIMENSIONS:
            content = extract.get(dim_name, {}).get("内容", "")
            row[dim_name] = content[:60] if content else "—"
        rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["案例名称", "完整度", "有依据", "未提及"] + [d[0] for d in DIMENSIONS])


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


def ask_followup(question, extracted_cases, access_secret, model=MODEL_FAST, history=None):
    """
    追问：用户针对案例提问，LLM基于原文证据回答。
    支持多轮对话：将历史问答作为上下文传入，保持连续性。
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
    ]
    if history:
        for h in history[-4:]:
            messages.append({"role": "user", "content": h["question"]})
            messages.append({"role": "assistant", "content": h["answer"]})
    messages.append({"role": "user", "content": prompt})
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
# 导出功能：将萃取结果导出为 Markdown 报告
# ============================================================

def export_cases_markdown(extracted_cases, commonality_summary="", review_report=""):
    """
    将已萃取案例、共性分析、复盘报告汇总为 Markdown 文本，供下载。
    """
    if not extracted_cases:
        return ""

    lines = ["# 知危鉴 · 灾害应急案例AI萃取报告\n"]
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    for i, case in enumerate(extracted_cases, 1):
        lines.append(f"\n---\n\n## 案例{i}：{case.get('title', '未知')}\n")
        lines.append(f"**原文链接**：{case.get('url', '无')}\n")
        extract = case.get("extract", {})
        completeness = extract.get("completeness", {})
        if completeness:
            lines.append(f"\n**完整度**：{completeness.get('percent', '—')}（✅{completeness.get('has_evidence', 0)} / ⚠️{completeness.get('missing', 0)}）\n")
        for dim_name, dim_emoji in DIMENSIONS:
            d = extract.get(dim_name, {})
            content = d.get("内容", "")
            evidence = d.get("证据片段", "")
            lines.append(f"\n### {dim_emoji} {dim_name}\n")
            lines.append(f"{content if content else '⚠️原文未提及'}\n")
            if evidence and "⚠️" not in content:
                lines.append(f"\n> 📖 证据片段：{evidence}\n")

    if commonality_summary:
        lines.append(f"\n---\n\n## 共性分析\n\n{commonality_summary}\n")

    if review_report:
        lines.append(f"\n---\n\n## 复盘报告\n\n{review_report}\n")

    lines.append("\n---\n")
    lines.append("\n*本报告由知危鉴自动生成，所有结论严格基于原文证据片段，未提及内容已标注。*\n")
    return "\n".join(lines)


# ============================================================
# 搜索历史（会话级，不持久化）
# ============================================================

def add_search_history(keyword, count):
    """记录搜索历史到 session_state，最多保留 5 条。"""
    history = st.session_state.get("search_history", [])
    entry = {"keyword": keyword, "count": count, "time": time.strftime("%H:%M")}
    history = [h for h in history if h["keyword"] != keyword]
    history.insert(0, entry)
    st.session_state["search_history"] = history[:5]


# ============================================================
# 缓存装饰器（减少重复调用，适配限额）
# ============================================================

@st.cache_data(show_spinner=False, ttl=3600)
def cached_search(keyword, access_secret, count=10):
    return zhihu_search(keyword, access_secret, count)


@st.cache_data(show_spinner=False, ttl=3600)
def cached_extract(text_hash, original_text, access_secret, model, title="", author="", comments=None):
    return extract_case_info(original_text, access_secret, model, title=title, author=author, comments=comments)


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(page_title="知危鉴 · 灾害应急案例AI萃取助手", page_icon="🌊", layout="wide")

# ============================================================
# OAuth 回调处理（必须在侧边栏/主界面渲染前执行）
# ============================================================
handle_oauth_callback()

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
.stButton > button, [data-testid="stBaseButton"] button { background: #4D9FFF !important; color: #FFFFFF !important; border: none !important; border-radius: 8px !important; font-weight: 600 !important; letter-spacing: 0.02em !important; transition: all 0.2s ease !important; }
.stButton > button:hover, [data-testid="stBaseButton"] button:hover { background: #3B8BF5 !important; color: #FFF !important; box-shadow: 0 2px 8px rgba(0,102,255,0.25) !important; }
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

/* 四维知识卡片彩色边框 */
.dim-card-overview { border-left: 4px solid #0066FF !important; }
.dim-card-risk { border-left: 4px solid #EF4444 !important; }
.dim-card-measure { border-left: 4px solid #10B981 !important; }
.dim-card-lesson { border-left: 4px solid #8B5CF6 !important; }

/* 时间线节点样式 */
.timeline-node { background: #FFFFFF !important; border: 1px solid #E2E8F0 !important; border-radius: 10px !important; padding: 12px !important; transition: box-shadow 0.2s ease !important; }
.timeline-node:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.06) !important; }
.timeline-node-done { border-top: 3px solid #10B981 !important; }
.timeline-node-missing { border-top: 3px solid #F59E0B !important; }

/* 搜索结果卡片悬停 */
.search-result-card { transition: background 0.2s ease !important; border-radius: 8px !important; padding: 8px 12px !important; }
.search-result-card:hover { background: #F1F5F9 !important; }

/* 搜索历史标签 */
.history-tag { display: inline-block; padding: 4px 10px; margin: 2px 4px 2px 0; background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 16px; font-size: 0.8rem; color: #334155; cursor: pointer; transition: all 0.2s; }
.history-tag:hover { background: #E6F0FF; border-color: #0066FF; color: #0066FF; }

/* 收藏星标 */
.bookmark-star { font-size: 1.2rem; cursor: pointer; transition: transform 0.2s; }
.bookmark-star:hover { transform: scale(1.2); }

/* 移动端适配：小屏幕四列变两列 */
@media (max-width: 768px) {
  .dim-card-overview, .dim-card-risk, .dim-card-measure, .dim-card-lesson {
    margin-bottom: 8px !important;
  }
  [data-testid="stHorizontalBlock"] { gap: 0.3rem !important; }
  h1 { font-size: 1.8rem !important; }
  h2 { font-size: 1.2rem !important; }
  .block-container { padding-top: 0.8rem !important; padding-left: 0.8rem !important; padding-right: 0.8rem !important; }
}
</style>""", unsafe_allow_html=True)

# 知乎蓝头部：巨型粗体标题 + 知乎蓝渐变封面
st.markdown("""
<div style="background: linear-gradient(120deg, #0066FF, #0044AA); color: #FFFFFF; padding: 26px 30px; margin-bottom: 10px; border-radius: 10px;">
  <div style="font-family: 'Helvetica Neue', 'Microsoft YaHei', sans-serif; font-weight: 900; font-size: 38px; letter-spacing: -0.02em; line-height: 1.15;">知危鉴 <span style="font-size:18px; font-weight:600; opacity:0.8;">· 灾害应急案例AI萃取助手</span></div>
  <div style="font-size: 13px; opacity: 0.92; font-weight: 500; margin-top: 6px; letter-spacing: 0.02em;">让每一条应急经验都有据可查 · 知乎黑客松 2026 · 知识炼金场</div>
</div>
""", unsafe_allow_html=True)

# 初始化session_state
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "extracted_cases" not in st.session_state:
    st.session_state.extracted_cases = []
if "last_error" not in st.session_state:
    st.session_state.last_error = ""
if "oauth_user" not in st.session_state:
    st.session_state.oauth_user = None
if "oauth_state" not in st.session_state:
    st.session_state.oauth_state = ""
if "oauth_error" not in st.session_state:
    st.session_state.oauth_error = ""
if "search_history" not in st.session_state:
    st.session_state.search_history = []
if "last_commonality" not in st.session_state:
    st.session_state.last_commonality = ""
if "last_report" not in st.session_state:
    st.session_state.last_report = ""
if "last_card" not in st.session_state:
    st.session_state.last_card = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "bookmarks" not in st.session_state:
    st.session_state.bookmarks = []
if "restored_hint" not in st.session_state:
    st.session_state.restored_hint = False

# ===== 侧边栏：Access Secret 配置与API测试 =====
# 部署环境（Streamlit secrets / 环境变量）已预置密钥时，评委可直接使用，无需输入
_deploy_secret = env_secret()
with st.sidebar:
    st.image("assets/bell.png", width=96)
    st.caption("知危鉴 · 🔔 应急警钟陪你萃取案例知识")

    # ===== 知乎账号登录（OAuth，人气奖参考：接入知乎登录的用户数） =====
    st.markdown("### 👤 知乎账号登录")
    oauth_app_id, oauth_app_key, oauth_redirect = oauth_config()
    oauth_user = st.session_state.get("oauth_user")
    oauth_error = st.session_state.get("oauth_error", "")
    if oauth_user:
        # 已登录：显示用户信息
        st.success(f"✅ 已登录：{oauth_user.get('fullname', '知乎用户')}")
        avatar = oauth_user.get("avatar_path", "")
        if avatar:
            st.image(avatar, width=48)
        st.caption("感谢登录支持！人气奖以接入知乎登录的用户数为重要参考")
        if st.button("🚪 退出登录", key="oauth_logout"):
            st.session_state.oauth_user = None
            st.rerun()
    elif oauth_app_id and oauth_app_key:
        # 未登录但有配置：显示登录按钮
        st.caption("🔓 登录后解锁：多案例萃取 · 时间线 · 对比分析 · 复盘报告 · 导出 · 追问")
        if st.button("🔑 登录知乎账号", key="oauth_login", type="primary"):
            import secrets as _secrets
            state = _secrets.token_urlsafe(16)
            st.session_state.oauth_state = state
            auth_url = build_oauth_auth_url(oauth_app_id, oauth_redirect, state)
            st.link_button("👉 点此跳转知乎授权页", auth_url)
    else:
        # 未配置 OAuth
        st.caption("⚙️ 尚未配置知乎登录（需在部署 Secrets 配置 App ID / App Key）")
        with st.expander("📋 OAuth 配置状态诊断"):
            st.caption(f"App ID: {'✅ 已配置' if oauth_app_id else '❌ 未配置'}")
            st.caption(f"App Key: {'✅ 已配置' if oauth_app_key else '❌ 未配置'}")
            st.caption(f"回调地址: {oauth_redirect}")
            st.caption("OAuth 是人气奖加分项，不影响主功能使用")
            st.caption("凭证获取：赛事答疑群追问小助理 或 邮件 openplatform@zhihu.com")
    if oauth_error:
        st.warning(oauth_error)
    st.markdown("---")

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

    # 搜索历史
    if st.session_state.get("search_history"):
        st.markdown("---")
        st.subheader("🕑 搜索历史")
        for h in st.session_state["search_history"]:
            st.caption(f"🔑 {h['keyword']} · {h['count']}条 · {h['time']}")

    # 清空操作
    if st.session_state.extracted_cases:
        st.markdown("---")
        if st.button("🗑️ 清空萃取结果", use_container_width=True):
            st.session_state.extracted_cases = []
            st.session_state.last_commonality = ""
            st.session_state.last_report = ""
            st.rerun()

    # 收藏夹
    if st.session_state.get("bookmarks"):
        st.markdown("---")
        st.subheader("⭐ 收藏夹")
        for i, bm in enumerate(st.session_state["bookmarks"]):
            st.caption(f"⭐ {bm['title'][:25]} · 赞同{bm.get('voteup', 0)}")
            if st.button("❌", key=f"del_bm_{i}", help="取消收藏"):
                st.session_state.bookmarks.pop(i)
                st.rerun()

st.markdown("---")

# 登录状态变量（用于分层门控）
_is_logged_in = bool(st.session_state.get("oauth_user"))

# 未登录时在主区域顶部显示登录引导横幅
if not _is_logged_in:
    st.markdown("""<div style="background: linear-gradient(135deg, #F0F4FF, #FAF5FF); border: 2px solid #0066FF; border-radius: 12px; padding: 16px 22px; margin: 10px 0 16px;">
    <div style="font-weight: 800; color: #0066FF; font-size: 1.05rem;">🔓 登录知乎账号，解锁完整功能</div>
    <div style="color: #334155; font-size: 0.88rem; margin-top: 4px;">未登录可体验检索和萃取（限 1 篇），登录后解锁：多案例并发萃取 · 时间线 · 对比分析 · 复盘报告 · 导出 · 追问</div>
    <div style="color: #64748B; font-size: 0.8rem; margin-top: 2px;">👉 在左侧栏点击「🔑 登录知乎账号」即可授权</div>
    </div>""", unsafe_allow_html=True)

# ============================================================
# 第一层：智能检索区
# ============================================================
st.markdown('<div style="font-family: \'Helvetica Neue\', \'Microsoft YaHei\', sans-serif; font-size: 1.45rem; font-weight: 800; color: #0066FF; margin: 1.4rem 0 0.1rem;">🔍 第一层 · 智能检索</div>', unsafe_allow_html=True)
st.caption("输入关键词或点击快捷按钮，调用知乎搜索API检索灾害应急相关文章")

col_kw, col_cnt, col_btn = st.columns([5, 1.5, 1])
with col_kw:
    keyword = st.text_input(
        "检索关键词",
        key="search_keyword",
        placeholder="例如：城市内涝、台风灾害、矿山事故、地质灾害应对",
    )
with col_cnt:
    result_count = st.select_slider("结果数", options=[3, 5, 10], value=10, key="result_count")
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
            results = cached_search(actual_kw, access_secret, result_count)
        if results:
            st.session_state.search_results = results
            add_search_history(actual_kw, len(results))
            st.success(f"找到 {len(results)} 条结果")
        else:
            err = st.session_state.get("last_error", "")
            st.info(f"暂无结果。{('原因: ' + err) if err else ''}")

# 展示检索结果 + 选择文章萃取
if st.session_state.search_results:
    st.markdown("### 检索结果（勾选文章后点击萃取）")
    selected_indices = []
    for i, item in enumerate(st.session_state.search_results):
        col_chk, col_show, col_bm = st.columns([1, 9, 0.8])
        with col_chk:
            if st.checkbox("", key=f"chk_{i}"):
                selected_indices.append(i)
        with col_show:
            auth_level = item.get("authority_level", "")
            auth_badge = ""
            if auth_level == "4":
                auth_badge = '<span style="background:#FEF3C7; color:#92400E; padding:1px 6px; border-radius:4px; font-size:0.7rem; font-weight:600;">超高权威</span>'
            elif auth_level == "3":
                auth_badge = '<span style="background:#DBEAFE; color:#1E40AF; padding:1px 6px; border-radius:4px; font-size:0.7rem; font-weight:600;">高权威</span>'
            st.markdown(f"""<div class="search-result-card">
            <b style="font-size:1.05rem; color:#0F172A;">{item.get('title', '无标题')}</b> {auth_badge}
            <br><span style="color:#64748B; font-size:0.8rem;">作者: {item.get('author', '')} · 赞同: {item.get('voteup', 0)} · 类型: {item.get('content_type', '')}</span>
            </div>""", unsafe_allow_html=True)
            if item.get("excerpt"):
                st.caption(item["excerpt"][:200] + "...")
            st.markdown(f"[🔗 查看原文]({item.get('url', '')})")
        with col_bm:
            bm_ids = [b.get("id") for b in st.session_state.get("bookmarks", [])]
            is_bookmarked = item.get("id") in bm_ids
            star = "⭐" if is_bookmarked else "☆"
            if st.button(star, key=f"bm_{i}", help="收藏/取消收藏"):
                if is_bookmarked:
                    st.session_state.bookmarks = [b for b in st.session_state.bookmarks if b.get("id") != item.get("id")]
                else:
                    st.session_state.bookmarks.append({
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "author": item.get("author", ""),
                        "voteup": item.get("voteup", 0),
                    })
                st.rerun()

    if selected_indices and st.button("🃏 萃取选中案例", type="primary"):
        if not access_secret:
            st.warning("请先填写 Access Secret")
        elif not _is_logged_in and len(selected_indices) > 1:
            st.warning("🔒 未登录用户每次最多萃取 1 篇文章，请在左侧栏登录知乎账号解锁多案例并发萃取")
        else:
            st.session_state.extracted_cases = []
            items_to_extract = []
            indices_to_use = selected_indices[:1] if not _is_logged_in else selected_indices
            for i in indices_to_use:
                item = st.session_state.search_results[i]
                original_text = item.get("excerpt", "")
                if not original_text:
                    st.warning(f"「{item.get('title', '')}」无内容摘要，已跳过")
                    continue
                items_to_extract.append((item, original_text))

            if items_to_extract:
                progress = st.progress(0)
                status_text = st.empty()
                done_count = 0

                def do_extract(item, text):
                    text_hash = hashlib.md5(text.encode()).hexdigest()
                    return item, text, cached_extract(
                        text_hash, text, access_secret, model_choice,
                        title=item.get("title", ""),
                        author=item.get("author", ""),
                        comments=item.get("comments", []),
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    futures = {
                        executor.submit(do_extract, item, text): idx
                        for idx, (item, text) in enumerate(items_to_extract)
                    }
                    for future in concurrent.futures.as_completed(futures):
                        item, original_text, extract = future.result()
                        if extract:
                            st.session_state.extracted_cases.append({
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "id": item.get("id", ""),
                                "original_text": original_text,
                                "extract": extract,
                            })
                        done_count += 1
                        progress.progress(done_count / len(items_to_extract))
                        status_text.info(f"已完成 {done_count}/{len(items_to_extract)}：{item.get('title', '')[:30]}")

                if st.session_state.extracted_cases:
                    st.success(f"✅ 萃取完成，共 {len(st.session_state.extracted_cases)} 个案例")
                    st.session_state.restored_hint = True
                    col_c, _, _ = st.columns([1, 3, 1])
                    col_c.image("assets/bell.png", width=72)
                    col_c.caption("应急警钟：案例已就绪，去分析吧！")
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"萃取失败，请检查 Access Secret 与接口连通性。{('原因: ' + err) if err else ''}")

st.markdown("---")

# ============================================================
# 第二层：案例萃取卡片区
# ============================================================
st.markdown('<div style="font-family: \'Helvetica Neue\', \'Microsoft YaHei\', sans-serif; font-size: 1.45rem; font-weight: 800; color: #10B981; margin: 1.4rem 0 0.1rem;">🃏 第二层 · 案例萃取</div>', unsafe_allow_html=True)
st.caption("从原文提取四维信息，每条结论附原文证据片段，未提及标记⚠️")

if st.session_state.restored_hint and st.session_state.extracted_cases:
    st.info(f"📌 本会话已保留 {len(st.session_state.extracted_cases)} 个萃取结果，可直接进入下方分析层")

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

        # 四维卡片（彩色边框区分维度）
        dim_cols = st.columns(4)
        dim_classes = ["dim-card-overview", "dim-card-risk", "dim-card-measure", "dim-card-lesson"]
        dim_colors = ["#0066FF", "#EF4444", "#10B981", "#8B5CF6"]
        for i, (dim_name, dim_emoji) in enumerate(DIMENSIONS):
            with dim_cols[i]:
                d = extract.get(dim_name, {})
                content = d.get("内容", "")
                evidence = d.get("证据片段", "")
                is_missing = "⚠️原文未提及" in content or not content
                status = "⚠️" if is_missing else "✅"
                border_color = dim_colors[i]
                st.markdown(f"""<div class="{dim_classes[i]}" style="background:#FFFFFF; border:1px solid #E2E8F0; border-left:4px solid {border_color}; border-radius:8px; padding:12px; min-height:120px;">
                <div style="font-weight:700; color:{border_color}; font-size:0.95rem; margin-bottom:6px;">{dim_emoji} {dim_name} {status}</div>
                <div style="color:#334155; font-size:0.88rem; line-height:1.6;">{content if content else '—'}</div>
                </div>""", unsafe_allow_html=True)
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

if not _is_logged_in:
    st.markdown("""<div style="background: #FEF3C7; border: 2px dashed #F59E0B; border-radius: 10px; padding: 16px 20px; margin: 8px 0;">
    <div style="font-weight: 700; color: #92400E; font-size: 1rem;">🔒 第三层功能需要登录知乎账号</div>
    <div style="color: #78350F; font-size: 0.85rem; margin-top: 4px;">时间线 · 横向对比 · 完整度图表 · 共性分析 — 请在左侧栏登录后解锁</div>
    </div>""", unsafe_allow_html=True)
elif not st.session_state.extracted_cases:
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
                timeline_nodes = ["预警", "发生", "响应", "处置", "复盘"]
                timeline_emojis = ["🟡", "🔴", "🟠", "🟢", "🔵"]
                timeline_cols = st.columns(5)
                for i, node in enumerate(timeline_nodes):
                    with timeline_cols[i]:
                        node_data = timeline.get(node, {})
                        is_missing = "⚠️" in node_data.get("动作", "") or not node_data.get("动作")
                        status = "⚠️" if is_missing else "✅"
                        css_class = "timeline-node-missing" if is_missing else "timeline-node-done"
                        st.markdown(f"""<div class="timeline-node {css_class}" style="text-align:center;">
                        <div style="font-size:1.5rem;">{timeline_emojis[i]}</div>
                        <div style="font-weight:700; color:#0F172A; font-size:0.95rem; margin:4px 0;">{node} {status}</div>
                        <div style="color:#64748B; font-size:0.75rem; margin-bottom:4px;">⏰ {node_data.get('时间', '—')}</div>
                        <div style="color:#334155; font-size:0.82rem; line-height:1.5; text-align:left;">📝 {node_data.get('动作', '—')}</div>
                        </div>""", unsafe_allow_html=True)
                        ev = node_data.get("证据", "")
                        if ev and "⚠️" not in ev:
                            st.caption(f"📖 {ev[:100]}")
                # 箭头连接线
                st.markdown("""<div style="text-align:center; color:#CBD5E1; font-size:1.2rem; margin:-4px 0 8px;">→ → → → →</div>""", unsafe_allow_html=True)
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"时间线抽取失败. {err}")
    else:
        st.warning("请先填写 Access Secret")

    # 多案例对比表格
    st.subheader("📋 多案例横向对比")
    df = compare_cases_table(st.session_state.extracted_cases)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 完整度对比图
    if len(st.session_state.extracted_cases) > 1:
        st.subheader("📊 案例完整度对比")
        chart_data = pd.DataFrame([
            {
                "案例": c.get("title", f"案例{i+1}")[:12],
                "✅ 有依据": c.get("extract", {}).get("completeness", {}).get("has_evidence", 0),
                "⚠️ 未提及": c.get("extract", {}).get("completeness", {}).get("missing", 0),
            }
            for i, c in enumerate(st.session_state.extracted_cases)
        ])
        st.bar_chart(chart_data.set_index("案例"), color=["#10B981", "#F59E0B"], use_container_width=True)
        st.caption("绿色=有原文依据，黄色=原文未提及，对比多案例的信息覆盖度")

    # 共性汇总
    st.subheader("🔍 共性汇总")
    if access_secret and st.button("生成共性分析", type="primary"):
        with st.spinner("正在调用Agent生成共性分析..."):
            summary = summarize_commonality(st.session_state.extracted_cases, access_secret, model_choice)
        if summary:
            st.session_state["last_commonality"] = summary
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

if not _is_logged_in:
    st.markdown("""<div style="background: #FEF3C7; border: 2px dashed #F59E0B; border-radius: 10px; padding: 16px 20px; margin: 8px 0;">
    <div style="font-weight: 700; color: #92400E; font-size: 1rem;">🔒 第四层功能需要登录知乎账号</div>
    <div style="color: #78350F; font-size: 0.85rem; margin-top: 4px;">知识卡片 · 复盘报告 · 导出报告 · 多轮追问 — 请在左侧栏登录后解锁</div>
    </div>""", unsafe_allow_html=True)
elif not st.session_state.extracted_cases:
    st.info("暂无案例可复盘，请先萃取")
else:
    preview_cols = st.columns(2)
    with preview_cols[0]:
        st.subheader("📇 知识卡片")
        if st.button("生成知识卡片", type="primary"):
            with st.spinner("生成中..."):
                card = generate_knowledge_card(st.session_state.extracted_cases, access_secret, model_choice)
            if card:
                st.session_state["last_card"] = card
                st.markdown(f"""<div style="background:linear-gradient(135deg, #F0F4FF, #FAF5FF); border:1px solid #C7D2FE; border-radius:12px; padding:18px 20px; margin:8px 0;">
                <div style="font-weight:800; color:#4338CA; font-size:1.1rem; margin-bottom:10px;">📇 应急案例知识卡片</div>
                </div>""", unsafe_allow_html=True)
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
                st.session_state["last_report"] = report
                st.session_state["last_commonality"] = summary
                st.markdown(f"""<div style="background:linear-gradient(135deg, #ECFDF5, #F0FDF4); border:1px solid #A7F3D0; border-radius:12px; padding:18px 20px; margin:8px 0;">
                <div style="font-weight:800; color:#065F46; font-size:1.1rem; margin-bottom:10px;">📝 灾害应急复盘报告</div>
                </div>""", unsafe_allow_html=True)
                st.markdown(report)
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"失败. {err}")

    # 导出报告
    st.markdown("---")
    st.subheader("📤 导出报告")
    export_md = export_cases_markdown(
        st.session_state.extracted_cases,
        st.session_state.get("last_commonality", ""),
        st.session_state.get("last_report", ""),
    )
    if export_md:
        st.download_button(
            label="⬇️ 下载 Markdown 报告",
            data=export_md,
            file_name=f"知危鉴_应急案例报告_{time.strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        st.caption("含全部萃取案例、共性分析、复盘报告，可用于离线复盘与分享")

    # 追问（多轮对话）
    st.markdown("---")
    st.subheader("💬 追问深入（多轮对话）")
    st.caption("支持连续追问，AI 会结合上下文回答。最多保留最近 4 轮对话。")

    # 显示历史对话
    if st.session_state.get("chat_history"):
        for h in st.session_state["chat_history"]:
            st.markdown(f"""<div style="background:#F1F5F9; border-radius:10px; padding:10px 14px; margin:6px 0;">
            <span style="color:#64748B; font-size:0.8rem;">🙋 问</span>
            <div style="color:#0F172A; margin-top:2px;">{h['question']}</div>
            </div>""", unsafe_allow_html=True)
            st.markdown(f"""<div style="background:#EFF6FF; border-left:3px solid #0066FF; border-radius:10px; padding:10px 14px; margin:6px 0;">
            <span style="color:#0066FF; font-size:0.8rem;">🤖 答</span>
            <div style="color:#334155; margin-top:2px;">{h['answer'][:500]}{'...' if len(h['answer']) > 500 else ''}</div>
            </div>""", unsafe_allow_html=True)

    ask_cols = st.columns([4, 1])
    with ask_cols[0]:
        ask_input = st.text_input("针对案例或复盘报告追问", key="ask_input", placeholder="例如：这次救援中物资调度为何滞后？")
    with ask_cols[1]:
        st.write("")
        ask_btn = st.button("追问", type="primary", use_container_width=True)

    if st.session_state.get("chat_history"):
        if st.button("🗑️ 清空对话", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

    if ask_btn:
        if not ask_input:
            st.warning("请输入追问内容")
        elif not access_secret:
            st.warning("请先填写 Access Secret")
        else:
            with st.spinner("正在追问..."):
                answer = ask_followup(
                    ask_input, st.session_state.extracted_cases, access_secret, model_choice,
                    history=st.session_state.get("chat_history", []),
                )
            if answer:
                st.session_state.chat_history.append({"question": ask_input, "answer": answer})
                st.rerun()
            else:
                err = st.session_state.get("last_error", "")
                st.error(f"追问失败. {err}")

# 底部说明
st.markdown("---")
st.markdown("""<div style="background:#F8FAFC; border:1px solid #E2E8F0; border-radius:10px; padding:16px 20px; margin-top:10px;">
<div style="font-weight:700; color:#0F172A; font-size:0.95rem; margin-bottom:8px;">🏗️ 防幻觉机制</div>
<div style="color:#64748B; font-size:0.82rem; line-height:1.7;">
所有萃取结论严格基于知乎原文证据片段 · 未提及内容标记⚠️原文未提及 · 不编造灾害数据与事件<br>
四维萃取：事件概况 · 风险因素 · 应急处置措施 · 经验教训 · 每条附原文溯源
</div>
<div style="margin-top:10px; padding-top:10px; border-top:1px dashed #E2E8F0; color:#94A3B8; font-size:0.78rem;">
知危鉴 · 灾害应急案例AI萃取助手 · 知乎黑客松 2026 校园新锐季 · 知识炼金场 · 应急有我团队
</div>
</div>""", unsafe_allow_html=True)
