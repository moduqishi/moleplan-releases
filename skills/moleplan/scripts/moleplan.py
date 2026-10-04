#!/usr/bin/env python3
"""MolePlan API 命令行客户端 —— 只用标准库,不装任何依赖。

设计取向:底层是 HTTP 原语(get/post/patch/put/delete + 任意路径),
任何后端端点都能直接打到;上层是一层薄薄的语义化命令,覆盖高频操作。

凭据来自环境变量(由 Hermes skill 注入,不会出现在命令行历史里):
    MOLEPLAN_API_KEY   必需,形如 mpk_xxxxxxxx
    MOLEPLAN_BASE_URL  可选,默认 https://kb.555615.xyz
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://kb.555615.xyz"
DEFAULT_TIMEOUT = 30.0

# 线上有 Cloudflare 前置:默认的 Python-urllib UA 会被浏览器指纹检查拦成 403。
USER_AGENT = "moleplan-cli/1.0 (+hermes-skill)"


class CliError(Exception):
    """面向使用者的错误,只打印消息,不打栈。"""


# ── HTTP 层 ────────────────────────────────────────────────

def _base_url(args) -> str:
    url = (getattr(args, "base_url", None) or os.environ.get("MOLEPLAN_BASE_URL")
           or DEFAULT_BASE_URL)
    return url.rstrip("/")


def _api_key(args) -> str:
    key = getattr(args, "api_key", None) or os.environ.get("MOLEPLAN_API_KEY") or ""
    key = key.strip()
    if not key:
        raise CliError(
            "缺少 MOLEPLAN_API_KEY。\n"
            "  在管理后台「Agent 密钥」页签发一把,然后:\n"
            "    export MOLEPLAN_API_KEY=mpk_...\n"
            "  Hermes 用户写进 ~/.hermes/.env 即可自动注入。"
        )
    return key


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


def request(args, method: str, path: str, *, body=None, params=None, raw=False):
    """发一个请求。非 2xx 抛 CliError,附上后端返回的 message。"""
    url = f"{_base_url(args)}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"

    data = None
    headers = {
        "Authorization": f"Bearer {_api_key(args)}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    timeout = getattr(args, "timeout", None) or DEFAULT_TIMEOUT

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        detail = payload
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                detail = parsed.get("message") or parsed.get("detail") or payload
        except json.JSONDecodeError:
            pass
        raise CliError(
            f"HTTP {exc.code} {method} {path}\n{detail}\n"
            f"提示:401=密钥无效/已撤销/已过期;403=密钥只读或该用户不是管理员。"
        ) from None
    except urllib.error.URLError as exc:
        raise CliError(f"连不上 {url}:{exc.reason}") from None

    # 后端的 SPA catch-all 会把不存在的路径渲染成首页 HTML 并返回 200。
    # 不拦住的话,agent 会把一整页 HTML 当成数据读进去。
    if not raw and _looks_like_html(payload):
        raise CliError(
            f"{method} {path} 返回的是网页而不是数据 —— 这个接口在 {_base_url(args)} 上不存在。\n"
            f"检查路径拼写,或者这些接口还没部署到该环境。"
        )

    if raw or not payload.strip():
        return payload if raw else None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload


# ── 输出 ──────────────────────────────────────────────────

def emit(data, args) -> None:
    if isinstance(data, str):
        print(data)
        return
    if getattr(args, "compact", False):
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def emit_ok(message: str, args, payload=None) -> None:
    if getattr(args, "quiet", False):
        return
    print(message)
    if payload is not None:
        emit(payload, args)


# ── 参数解析助手 ──────────────────────────────────────────

def parse_kv(pairs) -> dict:
    """把 `k=v` 解析成 dict,值做宽松类型推断(数字/布尔/null)。"""
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise CliError(f"参数要写成 k=v 的形式,收到:{item}")
        key, value = item.split("=", 1)
        out[key.strip()] = coerce(value)
    return out


def coerce(value: str):
    text = value.strip()
    if text == "":
        return ""
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return value


def build_body(args) -> dict | None:
    """--json 优先;否则用 --field k=v 拼;都没有则 None。"""
    if getattr(args, "json_body", None):
        try:
            parsed = json.loads(args.json_body)
        except json.JSONDecodeError as exc:
            raise CliError(f"--json 不是合法 JSON:{exc}") from None
        if not isinstance(parsed, dict):
            raise CliError("--json 必须是 JSON 对象")
        return parsed
    fields = parse_kv(getattr(args, "field", None))
    return fields or None


# ── 命令实现 ──────────────────────────────────────────────

def cmd_whoami(args):
    emit(request(args, "GET", "/api/agent/me"), args)


def cmd_overview(args):
    emit(request(args, "GET", "/api/admin/overview"), args)


def cmd_export(args):
    data = request(args, "GET", "/api/data/export")
    out = getattr(args, "out", None)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        emit_ok(f"已写入 {out}", args)
        return
    emit(data, args)


def cmd_import(args):
    with open(args.file, encoding="utf-8") as fh:
        payload = json.load(fh)
    emit(request(args, "POST", "/api/data/import", body=payload), args)


def cmd_semesters(args):
    if getattr(args, "user", None):
        data = request(args, "GET", f"/api/admin/users/{args.user}/snapshot")
        sems = data.get("semesters", []) if isinstance(data, dict) else data
        emit(sems, args)
        return
    emit(request(args, "GET", "/api/semesters"), args)


def cmd_courses(args):
    if getattr(args, "user", None):
        data = request(args, "GET", f"/api/admin/users/{args.user}/snapshot")
        if getattr(args, "semester", None):
            data = [c for c in data.get("courses", [])
                    if c.get("semester_id") == args.semester]
            emit(data, args)
            return
        emit(data.get("courses", []) if isinstance(data, dict) else data, args)
        return

    semester = getattr(args, "semester", None)
    if not semester:
        sems = request(args, "GET", "/api/semesters")
        if not sems:
            emit([], args)
            return
        current = next((s for s in sems if s.get("is_current")), sems[0])
        semester = current["id"]
    emit(request(args, "GET", f"/api/semesters/{semester}/courses"), args)


def cmd_schedules(args):
    emit(request(args, "GET", f"/api/courses/{args.course}/schedules"), args)


def cmd_snapshot(args):
    user = getattr(args, "user", None)
    if user:
        emit(request(args, "GET", f"/api/admin/users/{user}/snapshot"), args)
        return
    # 自己的数据走用户级导出 —— 不需要管理员身份,普通用户密钥也能用。
    emit(request(args, "GET", "/api/data/export"), args)


def cmd_users(args):
    params = {
        "query": getattr(args, "query", None),
        "role": getattr(args, "role", None),
        "status": getattr(args, "status", None),
    }
    emit(request(args, "GET", "/api/admin/users", params=params), args)


def cmd_user(args):
    emit(request(args, "GET", f"/api/admin/users/{args.user_id}"), args)


def cmd_user_action(args):
    path = f"/api/admin/users/{args.user_id}/{args.action}"
    body = build_body(args) if args.action != "unban" else None
    if args.action == "unban":
        emit(request(args, "POST", path), args)
        return
    if args.action == "kick-sessions":
        emit(request(args, "POST", path), args)
        return
    emit(request(args, "POST", path, body=body), args)


def cmd_batch(args):
    emit(request(args, "POST", "/api/admin/users/batch", body=build_body(args)), args)


def cmd_membership(args):
    path = f"/api/admin/memberships/{args.user_id}/{args.action}"
    emit(request(args, "POST", path, body=build_body(args)), args)


def cmd_codes(args):
    emit(request(args, "GET", "/api/admin/registration-codes"), args)


def cmd_new_code(args):
    body = build_body(args) or {}
    if getattr(args, "code", None):
        body.setdefault("code", args.code)
    if not body.get("code"):
        raise CliError("需要 --code 指定注册码,或用 --field code=XXX")
    emit(request(args, "POST", "/api/admin/registration-codes", body=body), args)


def cmd_keys(args):
    emit(request(args, "GET", "/api/admin/api-keys"), args)


def cmd_new_key(args):
    body = build_body(args) or {}
    if getattr(args, "name", None):
        body.setdefault("name", args.name)
    if getattr(args, "read_only", False):
        body["scopes"] = ["admin:read"]
    if getattr(args, "days", None):
        exp = datetime.datetime.utcnow() + datetime.timedelta(days=args.days)
        body["expires_at"] = exp.isoformat()
    if not body.get("name"):
        raise CliError("需要 --name 指定密钥名称")
    emit(request(args, "POST", "/api/admin/api-keys", body=body), args)


def cmd_key_action(args):
    path = f"/api/admin/api-keys/{args.key_id}/{args.action}"
    emit(request(args, "POST", path), args)


def cmd_announcements(args):
    emit(request(args, "GET", "/api/admin/announcements"), args)


def cmd_new_announcement(args):
    emit(request(args, "POST", "/api/admin/announcements", body=build_body(args)), args)


def cmd_settings(args):
    emit(request(args, "GET", "/api/admin/settings"), args)


def cmd_set_settings(args):
    emit(request(args, "PATCH", "/api/admin/settings", body=build_body(args)), args)


def cmd_releases(args):
    emit(request(args, "GET", "/api/admin/release-center"), args)


def cmd_novels(args):
    emit(request(args, "GET", "/api/admin/novels"), args)


def cmd_raw(args):
    verb = args.verb.upper()
    if verb not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        raise CliError(f"不支持的方法:{verb}")
    body = build_body(args) if verb in ("POST", "PATCH", "PUT") else None
    emit(request(args, verb, args.path, body=body,
                 params=parse_kv(getattr(args, "param", None))), args)


COMMANDS = {
    "whoami": cmd_whoami,
    "overview": cmd_overview,
    "export": cmd_export,
    "import": cmd_import,
    "semesters": cmd_semesters,
    "courses": cmd_courses,
    "schedules": cmd_schedules,
    "snapshot": cmd_snapshot,
    "users": cmd_users,
    "user": cmd_user,
    "user-action": cmd_user_action,
    "batch": cmd_batch,
    "membership": cmd_membership,
    "codes": cmd_codes,
    "new-code": cmd_new_code,
    "keys": cmd_keys,
    "new-key": cmd_new_key,
    "key-action": cmd_key_action,
    "announcements": cmd_announcements,
    "new-announcement": cmd_new_announcement,
    "settings": cmd_settings,
    "set-settings": cmd_set_settings,
    "releases": cmd_releases,
    "novels": cmd_novels,
    "api": cmd_raw,
}


# ── CLI 装配 ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moleplan",
        description="MolePlan 后端命令行客户端(零依赖)。",
        epilog="示例:moleplan courses --semester <id> | moleplan api GET /api/admin/overview",
    )
    parser.add_argument("--base-url", help="后端地址,默认读 MOLEPLAN_BASE_URL")
    parser.add_argument("--key", help="覆盖 MOLEPLAN_API_KEY")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--compact", action="store_true", help="单行 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出数据")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_write_flags(p):
        p.add_argument("--json", dest="json_body", help="完整 JSON 请求体")
        p.add_argument("--field", action="append", help="k=v,可重复")
        return p

    sub.add_parser("whoami", help="自检:身份、密钥 scope、可用能力")

    api_p = add_write_flags(sub.add_parser("api", help="直通任意端点"))
    api_p.add_argument("verb", help="GET/POST/PATCH/PUT/DELETE")
    api_p.add_argument("path", help="例如 /api/admin/overview")
    api_p.add_argument("--param", action="append", help="查询参数 k=v")

    sub.add_parser("overview", help="后台概览统计")

    export_p = sub.add_parser("export", help="导出我的全部课程数据")
    export_p.add_argument("--out", help="写入文件而非打印")

    import_p = sub.add_parser("import", help="导入课程数据(覆盖)")
    import_p.add_argument("--file", required=True)

    sem_p = sub.add_parser("semesters", help="学期列表")
    sem_p.add_argument("--user", help="管理员:查看指定用户的学期")

    course_p = sub.add_parser("courses", help="课程列表(默认当前学期)")
    course_p.add_argument("--semester", help="学期 ID")
    course_p.add_argument("--user", help="管理员:查看指定用户的课程")

    sch_p = sub.add_parser("schedules", help="某门课的排课")
    sch_p.add_argument("--course", required=True)

    snap_p = sub.add_parser("snapshot", help="课程数据全量快照")
    snap_p.add_argument("--user", help="管理员:指定用户,默认自己")

    users_p = sub.add_parser("users", help="用户列表")
    users_p.add_argument("--query", help="按用户名/显示名搜索")
    users_p.add_argument("--role", help="user / admin / writer")
    users_p.add_argument("--status", help="active / banned")

    user_p = sub.add_parser("user", help="用户详情")
    user_p.add_argument("user_id")

    ua_p = add_write_flags(sub.add_parser("user-action", help="封禁/解封/踢会话/重置密码"))
    ua_p.add_argument("user_id")
    ua_p.add_argument("action", choices=["ban", "unban", "kick-sessions", "password"])

    add_write_flags(sub.add_parser("batch", help="批量操作用户"))

    mem_p = add_write_flags(sub.add_parser("membership", help="会员授予/撤销"))
    mem_p.add_argument("user_id")
    mem_p.add_argument("action", choices=["grant", "revoke"])

    sub.add_parser("codes", help="注册码列表")

    nc_p = add_write_flags(sub.add_parser("new-code", help="新建注册码"))
    nc_p.add_argument("--code", help="注册码内容")

    sub.add_parser("keys", help="API 密钥列表")

    nk_p = add_write_flags(sub.add_parser("new-key", help="签发 API 密钥"))
    nk_p.add_argument("--name", help="密钥名称")
    nk_p.add_argument("--read-only", action="store_true", help="只读密钥")
    nk_p.add_argument("--days", type=int, help="有效天数,默认永久")

    ka_p = sub.add_parser("key-action", help="轮换/撤销密钥")
    ka_p.add_argument("key_id")
    ka_p.add_argument("action", choices=["rotate", "revoke"])

    sub.add_parser("announcements", help="公告列表")
    add_write_flags(sub.add_parser("new-announcement", help="发布公告"))

    sub.add_parser("settings", help="系统设置")
    add_write_flags(sub.add_parser("set-settings", help="更新系统设置"))

    sub.add_parser("releases", help="版本中心状态")
    sub.add_parser("novels", help="小说列表")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.error(f"未知命令:{args.command}")
    try:
        handler(args)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"文件不存在:{exc.filename}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
