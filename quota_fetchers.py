# -*- coding: utf-8 -*-
"""额度数据源（Antigravity + Codex）。

每个 fetch_* 返回统一的 ServiceState；ring 是主环显示的"剩余百分比"（0-100，
取该服务所有窗口里最紧张的那个），windows 是 tooltip 里的明细。
纯 requests/标准库实现，无 Qt 依赖。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field

import requests
from oauth_config import google_client

try:  # 本地自签证书请求会刷 InsecureRequestWarning，挂件场景无意义
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

CODEX_AUTH_PATH = os.path.join(os.path.expanduser("~"), ".codex", "auth.json")
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
# Codex CLI 的 OAuth client_id（openai/codex 公开源码中的固定值）
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

TIMEOUT = 15

# Windows 上 subprocess 调控制台程序（powershell/netstat/npm cmd）会闪黑窗，
# 无控制台的 GUI 进程尤其明显——全部压掉。
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# ── Antigravity 云端（原生 Google OAuth + Cloud Code API，免 npm CLI）──
# 移植自 antigravity-usage（MIT）的 oauth.ts / cloudcode.ts / parser.ts。
AGY_OAUTH = {
    "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "scope": "https://www.googleapis.com/auth/cloud-platform "
             "https://www.googleapis.com/auth/userinfo.email",
}
AGY_CLOUD_BASE = "https://cloudcode-pa.googleapis.com"
AGY_UA = "antigravity"
AGY_METADATA = {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI"}
AGY_ACCOUNTS_DIR = os.path.join(os.environ.get("APPDATA", ""),
                                "antigravity-usage", "accounts")


@dataclass
class Window:
    label: str                    # "5小时" / "每周" / "每月" ...
    remaining: float | None       # 剩余百分比 0-100
    reset: str | None = None      # 人话重置时间


@dataclass
class ServiceState:
    key: str
    ok: bool = False
    ring: float | None = None     # 主环值：所有窗口里最低的剩余
    windows: list[Window] = field(default_factory=list)
    error: str | None = None
    method: str | None = None     # 取数路径备注（如 local/google）


def _lowest(windows: list[Window]) -> float | None:
    vals = [w.remaining for w in windows if w.remaining is not None]
    return min(vals) if vals else None


def _fmt_reset(ts: float | int | str | None) -> str | None:
    """Unix 时间戳（秒或毫秒，int/float/数字字符串）→ '14:30 重置'。"""
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    if t > 1e12:  # 毫秒
        t /= 1000.0
    if t > 4e10:  # 微秒级防御
        t /= 1e6
    diff = t - time.time()
    if diff <= 0:
        return "已重置"
    h, m = int(diff // 3600), int(diff % 3600 // 60)
    return f"{h}小时{m:02d}分后重置" if h else f"{m}分钟后重置"


# ── Codex ────────────────────────────────────────────────────────────

def _codex_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "codex_cli_rs/1.0.0 (Windows arm64)",
        "Accept": "application/json",
    }


def _codex_refresh(auth: dict) -> str | None:
    """用 refresh_token 换新 access_token，成功则写回 auth.json 并返回新 token。"""
    tokens = auth.get("tokens") or {}
    rt = tokens.get("refresh_token")
    if not rt:
        return None
    try:
        r = requests.post(CODEX_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": CODEX_CLIENT_ID,
        }, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        new_at = data.get("access_token")
        if not new_at:
            return None
        tokens["access_token"] = new_at
        if data.get("refresh_token"):
            tokens["refresh_token"] = data["refresh_token"]
        auth["tokens"] = tokens
        auth["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        with open(CODEX_AUTH_PATH, "w", encoding="utf-8") as f:
            json.dump(auth, f, indent=2)
        return new_at
    except requests.RequestException:
        return None


def _parse_codex_usage(body: dict) -> list[Window]:
    """wham/usage 返回 rate_limit.primary_window(5h)/secondary_window(周)，值为已用百分比。"""
    wins: list[Window] = []
    rl = body.get("rate_limit") or body.get("rate_limits") or body
    for key, label in (("primary_window", "5小时"), ("secondary_window", "每周"),
                       ("tertiary_window", "每月"), ("primary", "5小时"), ("secondary", "每周")):
        item = rl.get(key)
        if not isinstance(item, dict):
            continue
        used = item.get("used_percent")
        if used is None:
            used = item.get("used_pct") or item.get("percent_used")
        if used is None:
            continue
        wins.append(Window(label, round(100.0 - float(used), 1),
                           _fmt_reset(item.get("reset_at") or item.get("resets_in_seconds"))))
    return wins


def fetch_codex() -> ServiceState:
    st = ServiceState("codex")
    try:
        with open(CODEX_AUTH_PATH, encoding="utf-8") as f:
            auth = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        st.error = f"读不到 {CODEX_AUTH_PATH}: {e}（先跑一次 codex 登录）"
        return st
    token = (auth.get("tokens") or {}).get("access_token")
    if not token:
        st.error = "auth.json 里没有 access_token（先跑一次 codex 登录）"
        return st
    try:
        r = requests.get(CODEX_USAGE_URL, headers=_codex_headers(token), timeout=TIMEOUT)
        if r.status_code in (401, 403):
            new_token = _codex_refresh(auth)
            if new_token:
                r = requests.get(CODEX_USAGE_URL, headers=_codex_headers(new_token), timeout=TIMEOUT)
            if r.status_code in (401, 403):
                st.error = "token 已过期且刷新失败（跑一次 codex 重新登录）"
                return st
        r.raise_for_status()
        wins = _parse_codex_usage(r.json())
        if not wins:
            st.error = "响应里没有识别到 rate_limits（接口可能又变形了）"
            return st
        st.ok, st.windows, st.ring, st.method = True, wins, _lowest(wins), "wham/usage"
        return st
    except requests.RequestException as e:
        st.error = f"网络错误: {e}"
        return st


# ── Antigravity（原生本地模式，直连 IDE language server；CLI 云端兜底）──

_AGY_PS = ("Get-CimInstance Win32_Process -Filter \"Name='language_server_windows_arm.exe'\" "
           "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Depth 2")
_AGY_RPC = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
_SSL_CTX = None  # lazy


def _agy_processes() -> list[dict]:
    """[(pid, csrf_token, is_daily)]，从进程命令行提取。"""
    import re
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _AGY_PS],
                             capture_output=True, text=True, timeout=30,
                             encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW).stdout
        procs = json.loads(out)
        if isinstance(procs, dict):
            procs = [procs]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    result = []
    for pr in procs or []:
        cl = pr.get("CommandLine") or ""
        m = re.search(r"--csrf_token ([0-9a-f-]{36})", cl)
        if m:
            result.append({"pid": pr.get("ProcessId"), "csrf": m.group(1),
                           "daily": "daily-" in cl})
    return result


def _agy_ports(pid: int) -> list[int]:
    import re
    try:
        ns = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                            timeout=30, encoding="utf-8", errors="replace",
                            creationflags=_NO_WINDOW).stdout
    except OSError:
        return []
    ports = []
    for line in ns.splitlines():
        m = re.match(rf"\s+TCP\s+127\.0\.0\.1:(\d+)\s+[\d.:]+\s+LISTENING\s+{pid}\s*$", line)
        if m:
            ports.append(int(m.group(1)))
    return sorted(ports)


def _agy_rpc(port: int, csrf: str):
    """POST GetUserStatus，https(自签)/http 双协议都试，成功返回 dict。"""
    global _SSL_CTX
    if _SSL_CTX is None:
        import ssl
        _SSL_CTX = ssl._create_unverified_context()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "X-Codeium-Csrf-Token": csrf,
    }
    for scheme in ("https", "http"):
        try:
            r = requests.post(f"{scheme}://127.0.0.1:{port}{_AGY_RPC}",
                              json={}, headers=headers, timeout=4, verify=False)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            continue
    return None


def _parse_agy_user_status(data: dict) -> list[Window]:
    us = data.get("userStatus") or data
    wins: list[Window] = []
    ps = us.get("planStatus") or {}
    info = ps.get("planInfo") or {}
    # ⚠️ availablePromptCredits/availableFlowCredits 实测语义是"已用"而非"可用"：
    # 用户几乎没用时该值为 500/50000，同时模型 remainingFraction=99.6%（自洽）；
    # 若按"剩余"解释则与模型窗口直接矛盾。
    prompt_total = float(info.get("monthlyPromptCredits") or 0)
    if prompt_total > 0:
        used = float(ps.get("availablePromptCredits") or 0)
        wins.append(Window("提示额度", round(max(0.0, 100.0 * (1 - used / prompt_total)), 1)))
    flow_total = float(info.get("monthlyFlowCredits") or 0)
    if flow_total > 0:
        used = float(ps.get("availableFlowCredits") or 0)
        wins.append(Window("Flow额度", round(max(0.0, 100.0 * (1 - used / flow_total)), 1)))
    # 模型配额：clientModelConfigs 有多档重复，按 modelId 去重取最紧张值
    best: dict[str, float] = {}
    resets: dict[str, str] = {}
    for cfg in (us.get("cascadeModelConfigData") or {}).get("clientModelConfigs") or []:
        qi = cfg.get("quotaInfo") or {}
        frac = qi.get("remainingFraction")
        if frac is None:
            continue
        mid = (cfg.get("modelOrAlias") or {}).get("model") or cfg.get("label") or "?"
        v = float(frac) * 100.0
        if mid not in best or v < best[mid]:
            best[mid] = v
            resets[mid] = qi.get("resetTime") or ""
    for mid, v in best.items():
        label = f"模型 {mid.replace('MODEL_PLACEHOLDER_', '')}"
        wins.append(Window(label, round(v, 1), resets[mid].replace("T", " ").replace("Z", "") or None))
    return wins


def _agy_cloud_paths() -> list[str]:
    """所有已存登录的 tokens.json 路径（兼容 CLI 的 accounts/<email>/ 结构）。"""
    paths = []
    try:
        for name in os.listdir(AGY_ACCOUNTS_DIR):
            p = os.path.join(AGY_ACCOUNTS_DIR, name, "tokens.json")
            if os.path.exists(p):
                paths.append(p)
    except OSError:
        pass
    return paths


def _agy_cloud_load() -> tuple[str | None, dict | None]:
    """取任一可用登录（email, tokens），过期没关系——刷新流程会处理。"""
    for p in _agy_cloud_paths():
        try:
            with open(p, encoding="utf-8") as f:
                tokens = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(tokens, dict) and tokens.get("refreshToken"):
            email = tokens.get("email") or os.path.basename(os.path.dirname(p))
            return email, tokens
    return None, None


def _agy_cloud_save(email: str | None, tokens: dict) -> None:
    safe = (email or "default").replace("/", "_").replace("\\", "_")
    d = os.path.join(AGY_ACCOUNTS_DIR, safe)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "tokens.json"), "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)


def _agy_cloud_refresh(email: str | None, tokens: dict) -> str | None:
    """refresh_token 换新 access_token 并落盘，返回新 token；失败返回 None。"""
    client = google_client()
    if not client:
        return None
    rt = tokens.get("refreshToken")
    if not rt:
        return None
    try:
        r = requests.post(AGY_OAUTH["token_url"], data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": rt,
            "grant_type": "refresh_token",
        }, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
    except requests.RequestException:
        return None
    access = data.get("access_token")
    if not access:
        return None
    tokens["accessToken"] = access
    tokens["expiresAt"] = int(time.time() * 1000) + int(data.get("expires_in", 3600)) * 1000
    _agy_cloud_save(email, tokens)
    return access


def _agy_cloud_headers(access: str) -> dict:
    return {"Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
            "User-Agent": AGY_UA}


def _agy_project_of(value) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
        return value["id"]
    return None


def fetch_agy_cloud() -> ServiceState:
    """原生云端模式：Google Cloud Code API 查额度（免 IDE、免 npm CLI）。"""
    st = ServiceState("agy")
    email, tokens = _agy_cloud_load()
    if not tokens:
        st.error = "云端未登录（托盘 → 设置 → 打开 Google 登录窗口）"
        return st
    try:
        access = tokens.get("accessToken")
        # 提前 1 分钟视为过期
        if not access or time.time() * 1000 >= float(tokens.get("expiresAt", 0)) - 60_000:
            access = _agy_cloud_refresh(email, tokens)
            if not access:
                st.error = "云端登录已过期且刷新失败（重新登录一次）"
                return st
        headers = _agy_cloud_headers(access)

        load = requests.post(f"{AGY_CLOUD_BASE}/v1internal:loadCodeAssist",
                             json={"metadata": AGY_METADATA}, headers=headers,
                             timeout=TIMEOUT)
        if load.status_code in (401, 403):
            st.error = "云端鉴权失败（重新登录一次）"
            return st
        load.raise_for_status()
        data = load.json()

        wins: list[Window] = []
        info = data.get("planInfo") or {}
        monthly = float(info.get("monthlyPromptCredits") or 0)
        if monthly > 0 and data.get("availablePromptCredits") is not None:
            avail = float(data["availablePromptCredits"])
            wins.append(Window("提示额度", round(avail / monthly * 100, 1)))
        flow_monthly = float(info.get("monthlyFlowCredits") or 0)
        if flow_monthly > 0 and data.get("availableFlowCredits") is not None:
            avail = float(data["availableFlowCredits"])
            wins.append(Window("Flow额度", round(avail / flow_monthly * 100, 1)))

        project = _agy_project_of(data.get("cloudaicompanionProject"))
        body = {"project": project} if project else {}
        models = requests.post(f"{AGY_CLOUD_BASE}/v1internal:fetchAvailableModels",
                               json=body, headers=headers, timeout=TIMEOUT)
        if models.status_code == 200:
            for mid, m in (models.json().get("models") or {}).items():
                if not isinstance(m, dict):
                    continue
                # 与 CLI parser 一致的过滤：内部/图片/实验模型不展示
                if (mid.startswith(("chat_", "tab_", "rev")) or "image" in mid
                        or "mquery" in mid or "lite" in mid):
                    continue
                qi = m.get("quotaInfo") or {}
                frac = qi.get("remainingFraction")
                if frac is None:
                    continue
                label = m.get("displayName") or m.get("label") or mid
                wins.append(Window(label, round(float(frac) * 100, 1),
                                   (qi.get("resetTime") or "").replace("T", " ").replace("Z", "") or None))

        if not wins:
            st.error = "云端响应里没有额度数据"
            return st
        st.ok, st.windows, st.ring = True, wins, _lowest(wins)
        st.method = f"cloud{('-' + email) if email else ''}"
        return st
    except requests.RequestException as e:
        st.error = f"云端网络错误: {e}"
        return st


def agy_cloud_login() -> tuple[bool, str]:
    """浏览器授权登录（阻塞 ~授权完成，需在后台线程调用）。

    返回 (ok, email 或错误信息)。与 antigravity-usage CLI 共享存储格式。
    """
    client = google_client()
    if not client:
        return False, "请先在额度设置中配置 Google OAuth 客户端 ID 和密钥"
    import http.server
    import secrets
    import threading
    import urllib.parse
    import webbrowser

    holder: dict = {}
    state = secrets.token_urlsafe(16)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != '/callback':
                self.send_error(404)
                return
            q = urllib.parse.parse_qs(parsed.query)
            if not secrets.compare_digest((q.get('state') or [''])[0], state):
                self.send_error(400, 'Invalid OAuth state')
                return
            if q.get("code"):
                holder["code"] = q["code"][0]
                holder["state"] = (q.get("state") or [""])[0]
                self.send_response(200)
            else:
                holder["error"] = (q.get("error") or ["授权被拒绝"])[0]
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in holder
            msg = "授权已收到，请回到 GlassDash 查看登录结果" if ok else "登录未完成，可关闭此页面重试"
            self.wfile.write(f"<meta charset='utf-8'><body style='font-size:20px;text-align:center;padding-top:40px'>{msg}</body>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    redirect = f"http://127.0.0.1:{port}/callback"
    srv.timeout = 0.5

    auth = AGY_OAUTH["auth_url"] + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": AGY_OAUTH["scope"],
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    try:
        try:
            opened = webbrowser.open(auth)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            return False, "无法打开默认浏览器，请在 Windows 中设置默认浏览器后重试。"
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not holder:
            srv.handle_request()
    finally:
        srv.server_close()
    if not holder:
        return False, "授权超时（5 分钟内未完成浏览器授权）"
    if holder.get("error"):
        return False, f"授权失败: {holder['error']}"
    if holder.get("state") != state:
        return False, "state 校验失败（请重试）"

    try:
        r = requests.post(AGY_OAUTH["token_url"], data={
            "code": holder["code"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }, timeout=TIMEOUT)
        r.raise_for_status()
        tok = r.json()
    except requests.RequestException as e:
        return False, f"token 交换失败: {e}"
    access = tok.get("access_token")
    if not access:
        return False, "token 响应里没有 access_token"

    email = None
    try:
        ui = requests.get("https://www.googleapis.com/oauth2/v2/userinfo",
                          headers={"Authorization": f"Bearer {access}"}, timeout=TIMEOUT).json()
        email = ui.get("email")
    except requests.RequestException:
        pass

    project = None
    try:
        la = requests.post(f"{AGY_CLOUD_BASE}/v1internal:loadCodeAssist",
                           json={"metadata": AGY_METADATA},
                           headers=_agy_cloud_headers(access), timeout=TIMEOUT).json()
        project = _agy_project_of(la.get("cloudaicompanionProject"))
    except requests.RequestException:
        pass

    stored = {"accessToken": access, "refreshToken": tok.get("refreshToken") or tok.get("refresh_token") or "",
              "expiresAt": int(time.time() * 1000) + int(tok.get("expires_in", 3600)) * 1000,
              "email": email, "projectId": project}
    _agy_cloud_save(email, stored)
    return True, email or "已登录"


def agy_cloud_status() -> tuple[bool, str | None]:
    """Antigravity 云端登录状态（原生存储，兼容 CLI 的 accounts 结构）。"""
    email, tokens = _agy_cloud_load()
    if tokens:
        return True, email
    return False, None


def fetch_agy(mode: str = "auto") -> ServiceState:
    """mode: auto=本地优先→原生云端兜底 / local=仅IDE / cloud=仅原生云端。"""
    st = ServiceState("agy")
    if mode != "cloud":                       # auto / local：先试本地直连
        procs = _agy_processes()
        procs.sort(key=lambda p: p["daily"])  # PROD(False) 在前
        for pr in procs:
            for port in _agy_ports(pr["pid"]):
                data = _agy_rpc(port, pr["csrf"])
                if data:
                    wins = _parse_agy_user_status(data)
                    if wins:
                        st.ok, st.windows, st.ring = True, wins, _lowest(wins)
                        st.method = "local-lsp" + ("(daily)" if pr["daily"] else "")
                        return st
        if mode == "local":
            st.error = ("Antigravity IDE 没在运行或 RPC 不通"
                        if procs else "Antigravity IDE 没在运行")
            return st
    # auto 的兜底 / cloud：原生云端模式（Google Cloud Code API，免 CLI 免 IDE）
    cloud = fetch_agy_cloud()
    if cloud.ok:
        return cloud
    if mode == "cloud":
        st.error = cloud.error or "云端模式失败"
    elif mode == "auto":
        st.error = "IDE 没开且云端未登录（托盘 → 设置 → 登录）"
    return st


# ── 汇总 ────────────────────────────────────────────────────────────

def fetch_all(cfg: dict) -> list[ServiceState]:
    return [fetch_agy(cfg.get("agy_mode", "auto")), fetch_codex()]
