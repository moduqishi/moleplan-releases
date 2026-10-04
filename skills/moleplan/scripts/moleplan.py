#!/usr/bin/env python3
"""MolePlan API 命令行客户端 —— 只用标准库,不装任何依赖。

设计取向:底层是 HTTP 原语(get/post/patch/put/delete + 任意路径),
任何后端端点都能直接打到;上层是一层薄薄的语义化命令,覆盖高频操作。
课程相关的命令(today / tomorrow / week / next)负责把
「学期起始日 + 周次 + day_of_week + 作息表」这套原始数据算成人话。

凭据来自环境变量(由 agent 的 skill 机制注入,不会出现在命令行历史里):
    MOLEPLAN_API_KEY   必需,形如 mpk_xxxxxxxx
    MOLEPLAN_BASE_URL  可选,默认 https://kb.555615.xyz
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

DEFAULT_BASE_URL = "https://kb.555615.xyz"
DEFAULT_TIMEOUT = 30.0
DEFAULT_TZ = "Asia/Shanghai"

# 线上有 Cloudflare 前置:默认的 Python-urllib UA 会被浏览器指纹检查拦成 403。
USER_AGENT = "moleplan-cli/1.1 (+hermes-skill)"

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 5xx / 网络抖动重试次数
MAX_RETRIES = 3


class CliError(Exception):
    """面向使用者的错误,只打印消息,不打栈。"""


# ── 时区 ──────────────────────────────────────────────────

def _tz(name: str | None) -> datetime.tzinfo:
    try:
        return zoneinfo.ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        raise CliError(f"不认识的时区:{name}") from None


def _today(args) -> datetime.date:
    override = getattr(args, "date", None)
    if override:
        try:
            return datetime.date.fromisoformat(override)
        except ValueError:
            raise CliError(f"--date 要写成 YYYY-MM-DD,收到 {override}") from None
    return datetime.datetime.now(_tz(getattr(args, "tz", None))).date()


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
            "  在 MolePlan 管理后台「Agent 密钥」页签发一把,然后导出到环境变量:\n"
            "    export MOLEPLAN_API_KEY=mpk_...\n"
            "  agent 工具会依据 skill 声明的 required_environment_variables 自动注入。"
        )
    return key


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


def _if_missing_key(value: str, key: str):
    """mask 占位符原样留着,别把 '****' 当成真值又写回去。"""
    if isinstance(value, str) and value.startswith("****"):
        raise CliError(f"{key} 当前是脱敏值,不能回写。要改请用 --show-secrets 之外的方式提供真值。")
    return value


def request(args, method: str, path: str, *, body=None, params=None, raw=False):
    """发一个请求。非 2xx 抛 CliError,附上后端返回的 message。

    5xx 与网络抖动做指数退避重试;4xx 是确定性错误,不重试。
    """
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

    timeout = getattr(args, "timeout", None) or DEFAULT_TIMEOUT
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            # 4xx 是确定性错误:重试没有意义
            if exc.code < 500 or attempt == MAX_RETRIES - 1:
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
            last_error = exc
        except urllib.error.URLError as exc:
            last_error = exc
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
    else:
        raise CliError(f"连不上 {_base_url(args)}:{last_error}")

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


# ── 归一化 ────────────────────────────────────────────────

def norm_week_numbers(raw) -> list[int]:
    """把 week_numbers 归一化成有序去重数组。

    这套数据在不同路径下形态不一致:写库时存的是 {"data": [...]},
    courses 接口直接回传存储形态,而导出接口已经摊平成数组。
    先按快照过滤周次的写法很容易一条都过滤不出来,所以统一在这里收敛。
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("weekNumbers") or []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(raw, dict):
            raw = raw.get("data") or []
    if not isinstance(raw, list):
        return []

    seen: list[int] = []
    for item in raw:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in seen:
            seen.append(number)
    return sorted(seen)


def normalize_schedule(schedule: dict) -> dict:
    out = dict(schedule)
    out["week_numbers"] = norm_week_numbers(schedule.get("week_numbers"))
    return out


def normalize_snapshot(data: dict) -> dict:
    """导出结构:排课扁平列表,courses[].schedules 一律填好。

    上游的 courses 里不带 schedules(是 None),而 schedules 列表里带着
    course_id —— 直接按 courses[].schedules 过滤会一条都拿不到。这里两个
    方向都补齐,调用方想用哪个都行。
    """
    if not isinstance(data, dict):
        return data

    schedules = [normalize_schedule(s) for s in (data.get("schedules") or [])]
    by_course: dict[str, list[dict]] = {}
    for schedule in schedules:
        by_course.setdefault(str(schedule.get("course_id")), []).append(schedule)

    courses = []
    for course in data.get("courses") or []:
        item = dict(course)
        cid = str(item.get("id"))
        nested = item.get("schedules")
        if nested:
            item["schedules"] = [normalize_schedule(s) for s in nested]
        else:
            item["schedules"] = by_course.get(cid, [])
        courses.append(item)

    out = dict(data)
    out["courses"] = courses
    out["schedules"] = schedules
    return out


# ── 输出 ──────────────────────────────────────────────────

def _auto_compact(args) -> bool:
    """非交互时自动压成单行 —— 多行缩进 JSON 在聊天里刷屏。"""
    if getattr(args, "pretty", False):
        return False
    if getattr(args, "compact", False):
        return True
    if getattr(args, "format", None):
        return False
    return not sys.stdout.isatty()


def _row_to_cells(row: dict, columns: list[tuple[str, str]]) -> list[str]:
    cells = []
    for key, _label in columns:
        value = row.get(key)
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        cells.append("" if value is None else str(value))
    return cells


def render_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "(空)"
    headers = [label for _key, label in columns]
    body = [_row_to_cells(row, columns) for row in rows]
    widths = [len(h) for h in headers]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    # 中文按两格宽算,否则列对不齐
    def display_width(text: str) -> int:
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)

    widths = [max(display_width(h), *(display_width(r[i]) for r in body)) for i, h in enumerate(headers)]

    def pad(text: str, width: int) -> str:
        return text + " " * (width - display_width(text))

    lines = ["  ".join(pad(h, widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def render_markdown(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_(空)_"
    headers = [label for _key, label in columns]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_row_to_cells(row, columns)) + " |")
    return "\n".join(lines)


def emit(data, args, columns: list[tuple[str, str]] | None = None) -> None:
    fmt = getattr(args, "format", None)
    if fmt in ("table", "md") and isinstance(data, list) and columns:
        print(render_table(data, columns) if fmt == "table" else render_markdown(data, columns))
        return
    if isinstance(data, str):
        print(data)
        return
    if _auto_compact(args):
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def emit_ok(message: str, args, payload=None) -> None:
    if getattr(args, "quiet", False):
        return
    print(message)
    if payload is not None:
        emit(payload, args)


def emit_diff(label: str, before, after, args) -> None:
    """写操作回读:exit 0 只说明请求被受理,不等于业务生效。"""
    if getattr(args, "quiet", False) or getattr(args, "no_verify", False):
        return
    if before == after:
        print(f"  {label}: {after} (与写入前一致)")
        return
    print(f"  {label}: {before} -> {after}")


# ── 参数解析助手 ──────────────────────────────────────────

def parse_kv(pairs) -> dict:
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


def paginate(rows: list, args) -> list:
    limit = getattr(args, "limit", None)
    offset = getattr(args, "offset", None) or 0
    if offset:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


# ── 课程视图 ──────────────────────────────────────────────
#
# 把「学期起始日 + 周次 + day_of_week + 作息表」算成「几点上什么课」。
# 这是最高频的需求(「明天上什么」「下周呢」),不该让每个调用方自己算。

def load_timetable(args) -> dict:
    data = normalize_snapshot(request(args, "GET", "/api/data/export"))
    semesters = data.get("semesters") or []
    if not semesters:
        return {"semesters": [], "courses": [], "schedules": [], "periods": {}, "current": None}

    target = _today(args)
    current = None
    for semester in semesters:
        if semester.get("is_current"):
            current = semester
            break
    if current is None:
        # 没有标记当前学期时,取日期区间覆盖今天的那个
        for semester in semesters:
            start = _parse_date(semester.get("start_date"))
            if not start:
                continue
            weeks = int(semester.get("total_weeks") or 20)
            if start <= target <= start + datetime.timedelta(weeks=weeks):
                current = semester
                break
    if current is None:
        current = semesters[0]

    periods: dict[int, dict] = {}
    for config in data.get("timeConfigs") or []:
        if config.get("semester_id") and config.get("semester_id") != current.get("id"):
            continue
        try:
            periods[int(config.get("period_number"))] = config
        except (TypeError, ValueError):
            continue

    return {
        "semesters": semesters,
        "courses": data.get("courses") or [],
        "schedules": data.get("schedules") or [],
        "periods": periods,
        "current": current,
    }


def _parse_date(value) -> datetime.date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def week_of(semester: dict, target: datetime.date) -> int | None:
    start = _parse_date(semester.get("start_date"))
    if not start:
        return None
    delta = (target - start).days
    if delta < 0:
        return None
    number = delta // 7 + 1
    total = int(semester.get("total_weeks") or 20)
    return number if number <= total else None


def _matches_weeks(schedule: dict, week: int) -> bool:
    week_type = (schedule.get("week_type") or "all").lower()
    if week_type == "all":
        return True
    if week_type == "odd":
        return week % 2 == 1
    if week_type == "even":
        return week % 2 == 0
    numbers = norm_week_numbers(schedule.get("week_numbers"))
    return week in numbers


def _format_period_time(periods: dict[int, dict], start: int, end: int) -> str:
    begin = periods.get(start, {}).get("start_time")
    finish = periods.get(end, {}).get("end_time")
    if not begin or not finish:
        return ""
    return f"{str(begin)[:5]}–{str(finish)[:5]}"


def entries_for(timetable: dict, target: datetime.date) -> list[dict]:
    semester = timetable.get("current")
    if not semester:
        return []
    week = week_of(semester, target)
    if week is None:
        return []

    courses = {str(c.get("id")): c for c in timetable["courses"]}
    periods = timetable["periods"]
    isoweekday = target.isoweekday()

    result = []
    for schedule in timetable["schedules"]:
        if int(schedule.get("day_of_week") or 0) != isoweekday:
            continue
        if not _matches_weeks(schedule, week):
            continue
        course = courses.get(str(schedule.get("course_id")))
        if not course:
            continue
        start = int(schedule.get("start_period") or 1)
        end = int(schedule.get("end_period") or start)
        result.append({
            "date": target.isoformat(),
            "weekday": WEEKDAY_CN[isoweekday - 1],
            "week": week,
            "periods": f"{start}-{end}",
            "time": _format_period_time(periods, start, end),
            "course": course.get("name") or "",
            "teacher": course.get("teacher") or "",
            "location": course.get("location") or "",
            "course_id": course.get("id"),
            "_start": start,
        })
    result.sort(key=lambda e: e["_start"])
    return result


def load_holidays(args) -> dict[str, str]:
    """--holidays 收 JSON:数组 ["2026-10-01",...] 或映射 {"2026-10-01":"国庆"}。

    刻意不内置法定节假日表 —— 放假与调休每年由国务院通知确定,写死在 skill 里
    会给出过期甚至错误的答案。课表数据本身也不含调课/补课信息。
    """
    raw = getattr(args, "holidays", None)
    if not raw:
        return {}
    text = raw
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as fh:
            text = fh.read()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(f"--holidays 不是合法 JSON:{exc}") from None

    if isinstance(parsed, list):
        return {str(item)[:10]: "假日" for item in parsed}
    if isinstance(parsed, dict):
        return {str(k)[:10]: str(v) for k, v in parsed.items()}
    raise CliError("--holidays 要传数组或对象")


def render_day(args, target: datetime.date) -> None:
    timetable = load_timetable(args)
    if not timetable.get("current"):
        emit_ok(f"{target.isoformat()} 没有学期数据。", args)
        return

    holidays = load_holidays(args)
    entries = entries_for(timetable, target)
    week = week_of(timetable["current"], target)
    mark = holidays.get(target.isoformat())

    header = f"{WEEKDAY_CN[target.isoweekday() - 1]} {target.strftime('%m-%d')}"
    if week:
        header += f" · 第 {week} 周"
    if mark:
        header += f" · {mark}"

    if getattr(args, "quiet", False):
        for entry in entries:
            emit_line("", entry, args)
        return

    print(header)
    if not entries:
        print("  没有课。")
        if mark:
            print("  （课表数据不含调休与补课,以学校通知为准）")
        return
    for entry in entries:
        emit_line("  ", entry, args)


def emit_line(indent: str, entry: dict, args) -> None:
    parts = [f"{entry['weekday']} {entry['date'][5:]}", f"第{entry['periods']}节"]
    if entry["time"]:
        parts.append(entry["time"])
    parts.append(entry["course"])
    if entry["location"]:
        parts.append(f"@{entry['location']}")
    print(indent + " ".join(parts))


# ── 命令实现 ──────────────────────────────────────────────

def cmd_whoami(args):
    data = request(args, "GET", "/api/agent/me")
    emit(data, args)
    key = (data or {}).get("key") or {}
    if not getattr(args, "quiet", False):
        if key.get("expires_at") is None:
            print("  提示:这把密钥没有过期时间,建议在后台给它设一个。", file=sys.stderr)
        if key.get("scopes") and "admin:full" not in key["scopes"]:
            print("  提示:这是只读密钥,写操作会被拒。", file=sys.stderr)


def cmd_overview(args):
    emit(request(args, "GET", "/api/admin/overview"), args)


def cmd_export(args):
    data = request(args, "GET", "/api/data/export")
    out = getattr(args, "out", None)
    if out:
        _write_private(out, json.dumps(data, ensure_ascii=False, indent=2))
        emit_ok(f"已写入 {out}", args)
        return
    emit(data, args)


def cmd_import(args):
    with open(args.file, encoding="utf-8") as fh:
        payload = json.load(fh)

    if getattr(args, "dry_run", False):
        current = normalize_snapshot(request(args, "GET", "/api/data/export"))
        incoming = normalize_snapshot(payload)
        before = {str(c.get("id")) for c in current.get("courses") or []}
        after = {str(c.get("id")) for c in incoming.get("courses") or []}
        print("导入预览(未写入):")
        print(f"  学期: {len(current.get('semesters') or [])} -> {len(incoming.get('semesters') or [])}")
        print(f"  课程: {len(before)} -> {len(after)}  (新增 {len(after - before)},移除 {len(before - after)})")
        print(f"  排课: {len(current.get('schedules') or [])} -> {len(incoming.get('schedules') or [])}")
        print("  注意:导入是整体覆盖,不在文件里的课程会被删除。")
        return

    emit(request(args, "POST", "/api/data/import", body=payload), args)


def cmd_selftest(args):
    """一条命令自证整条链路,输出 Markdown 表格。"""
    rows: list[dict] = []
    created_key_id: str | None = None

    def step(name: str, fn):
        try:
            detail = fn()
            rows.append({"step": name, "result": "OK", "detail": detail or ""})
            return True
        except CliError as exc:
            rows.append({"step": name, "result": "FAIL", "detail": str(exc).split("\n")[0][:120]})
            return False

    identity = request(args, "GET", "/api/agent/me")
    key_info = (identity or {}).get("key") or {}
    is_admin = bool((identity or {}).get("is_admin"))
    can_write = bool((identity or {}).get("can_write"))
    rows.append({
        "step": "身份 / 密钥",
        "result": "OK",
        "detail": f"{identity['user']['username']} ({identity['user']['role']}), "
                  f"写权限={'有' if can_write else '无'}, 过期={key_info.get('expires_at') or '永不过期'}",
    })

    step("导出课程数据", lambda: (
        lambda d: f"学期 {len(d.get('semesters') or [])} / 课程 {len(d.get('courses') or [])} / 排课 {len(d.get('schedules') or [])}"
    )(normalize_snapshot(request(args, "GET", "/api/data/export"))))

    if not is_admin:
        rows.append({"step": "管理端读接口", "result": "SKIP", "detail": "密钥不是管理员权限"})
    else:
        step("概览统计", lambda: f"用户 {request(args, 'GET', '/api/admin/overview').get('totals', {}).get('users', '?')}")
        step("用户列表", lambda: f"{len(request(args, 'GET', '/api/admin/users') or [])} 人")
        step("系统设置读取", lambda: f"{len((request(args, 'GET', '/api/admin/settings') or {}).get('settings', {}))} 项")
        step("公告列表", lambda: f"{len(request(args, 'GET', '/api/admin/announcements') or [])} 条")

    if is_admin and can_write:
        def roundtrip():
            current = (request(args, "GET", "/api/admin/settings") or {}).get("settings", {})
            key = "version_control_enabled"
            before = current.get(key)
            request(args, "PATCH", "/api/admin/settings", body={key: before})
            after = (request(args, "GET", "/api/admin/settings") or {}).get("settings", {}).get(key)
            if before != after:
                raise CliError(f"同值写回后不一致:{before} -> {after}")
            return f"{key} 同值写回后仍为 {after}"

        step("设置同值写回+回读", roundtrip)

        def key_lifecycle():
            nonlocal created_key_id
            created = request(args, "POST", "/api/admin/api-keys",
                              body={"name": "selftest-probe", "scopes": ["admin:read"]})
            created_key_id = created.get("id")
            probe = created.get("secret")
            if not probe:
                raise CliError("签发未返回明文")
            # 用新密钥自证可用,再撤销
            saved = os.environ.get("MOLEPLAN_API_KEY")
            os.environ["MOLEPLAN_API_KEY"] = probe
            try:
                request(args, "GET", "/api/agent/me")
            finally:
                if saved:
                    os.environ["MOLEPLAN_API_KEY"] = saved
                else:
                    os.environ.pop("MOLEPLAN_API_KEY", None)
            request(args, "POST", f"/api/admin/api-keys/{created_key_id}/revoke")
            return f"{created.get('prefix')} 签发→自洽→已撤销"

        step("密钥签发/撤销", key_lifecycle)

    print(render_markdown(rows, [("step", "步骤"), ("result", "结果"), ("detail", "详情")]))
    failed = [r for r in rows if r["result"] == "FAIL"]
    print()
    print(f"共 {len(rows)} 项,失败 {len(failed)} 项。" + ("链路正常。" if not failed else "有失败项,见上表。"))
    if failed:
        raise CliError("自检未全部通过")


def cmd_today(args):
    render_day(args, _today(args))


def cmd_tomorrow(args):
    render_day(args, _today(args) + datetime.timedelta(days=1))


def cmd_week(args):
    """默认从今天所在周的周一开始,给出一周课表。"""
    target = _today(args)
    monday = target - datetime.timedelta(days=target.isoweekday() - 1)
    timetable = load_timetable(args)
    if not timetable.get("current"):
        emit_ok("没有学期数据。", args)
        return
    holidays = load_holidays(args)
    total = 0
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        entries = entries_for(timetable, day)
        week = week_of(timetable["current"], day)
        mark = holidays.get(day.isoformat())
        title = f"{WEEKDAY_CN[day.isoweekday() - 1]} {day.strftime('%m-%d')}"
        if week:
            title += f" 第{week}周"
        if mark:
            title += f" [{mark}]"
        if not entries:
            print(f"{title}  —")
            continue
        print(title)
        for entry in entries:
            emit_line("  ", entry, args)
        total += len(entries)
    print(f"\n本周共 {total} 节。")


def cmd_next(args):
    """下节课:从今天算起,最多往后找 14 天。"""
    target = _today(args)
    timetable = load_timetable(args)
    if not timetable.get("current"):
        emit_ok("没有学期数据。", args)
        return
    now = datetime.datetime.now(_tz(getattr(args, "tz", None)))
    for offset in range(14):
        day = target + datetime.timedelta(days=offset)
        entries = entries_for(timetable, day)
        if not entries:
            continue
        for entry in entries:
            if offset == 0 and entry["time"]:
                end = entry["time"].split("–")[-1]
                try:
                    finish = datetime.datetime.strptime(end, "%H:%M").time()
                except ValueError:
                    continue
                if finish <= now.time():
                    continue
            print("下节课：")
            emit_line("  ", entry, args)
            return
    emit_ok("未来两周没有排课。", args)


def cmd_semesters(args):
    if getattr(args, "user", None):
        data = request(args, "GET", f"/api/admin/users/{args.user}/snapshot")
        sems = data.get("semesters", []) if isinstance(data, dict) else data
        emit(sems, args, columns=[("id", "ID"), ("name", "名称"), ("start_date", "开始"), ("total_weeks", "周数"), ("is_current", "当前")])
        return
    emit(request(args, "GET", "/api/semesters"), args,
         columns=[("id", "ID"), ("name", "名称"), ("start_date", "开始"), ("total_weeks", "周数"), ("is_current", "当前")])


def cmd_courses(args):
    columns = [("id", "ID"), ("name", "课程"), ("teacher", "教师"), ("location", "地点")]
    if getattr(args, "user", None):
        data = request(args, "GET", f"/api/admin/users/{args.user}/snapshot")
        items = data.get("courses", []) if isinstance(data, dict) else data
        if getattr(args, "semester", None):
            items = [c for c in items if c.get("semester_id") == args.semester]
        emit(paginate(items, args), args, columns=columns)
        return

    semester = getattr(args, "semester", None)
    if not semester:
        sems = request(args, "GET", "/api/semesters")
        if not sems:
            emit([], args)
            return
        current = next((s for s in sems if s.get("is_current")), sems[0])
        semester = current["id"]
    emit(paginate(request(args, "GET", f"/api/semesters/{semester}/courses"), args), args, columns=columns)


def cmd_schedules(args):
    rows = [normalize_schedule(s) for s in request(args, "GET", f"/api/courses/{args.course}/schedules")]
    emit(rows, args, columns=[("day_of_week", "星期"), ("start_period", "起"), ("end_period", "止"), ("week_type", "周型"), ("week_numbers", "周次")])


def cmd_snapshot(args):
    normalized = normalize_snapshot(request(args, "GET", "/api/data/export"))
    if getattr(args, "user", None):
        normalized = normalize_snapshot(request(args, "GET", f"/api/admin/users/{args.user}/snapshot"))
    out = getattr(args, "out", None)
    if out:
        _write_private(out, json.dumps(normalized, ensure_ascii=False, indent=2))
        emit_ok(f"已写入 {out}", args)
        return
    emit(normalized, args)


def cmd_users(args):
    params = {"query": getattr(args, "query", None), "role": getattr(args, "role", None),
              "status": getattr(args, "status", None)}
    rows = paginate(request(args, "GET", "/api/admin/users", params=params), args)
    emit(rows, args, columns=[("id", "ID"), ("username", "用户名"), ("display_name", "显示名"), ("role", "角色"), ("status", "状态"), ("last_login_at", "最后登录")])


def cmd_user(args):
    emit(request(args, "GET", f"/api/admin/users/{args.user_id}"), args)


def cmd_user_action(args):
    path = f"/api/admin/users/{args.user_id}/{args.action}"
    before = None
    try:
        before = (request(args, "GET", f"/api/admin/users/{args.user_id}") or {}).get("user", {})
    except CliError:
        pass

    if args.action in ("unban", "kick-sessions"):
        request(args, "POST", path)
    else:
        request(args, "POST", path, body=build_body(args))

    emit_ok(f"{args.action} 已提交", args)
    if before and not getattr(args, "no_verify", False):
        after = (request(args, "GET", f"/api/admin/users/{args.user_id}") or {}).get("user", {})
        for field in ("status", "ban_reason"):
            emit_diff(field, before.get(field), after.get(field), args)


def cmd_batch(args):
    emit(request(args, "POST", "/api/admin/users/batch", body=build_body(args)), args)


def cmd_membership(args):
    path = f"/api/admin/memberships/{args.user_id}/{args.action}"
    before = None
    try:
        before = (request(args, "GET", f"/api/admin/users/{args.user_id}") or {}).get("user", {}).get("membership", {})
    except CliError:
        pass
    request(args, "POST", path, body=build_body(args))
    emit_ok(f"会员 {args.action} 已提交", args)
    if before is not None and not getattr(args, "no_verify", False):
        after = (request(args, "GET", f"/api/admin/users/{args.user_id}") or {}).get("user", {}).get("membership", {})
        for field in ("status", "expires_at"):
            emit_diff(field, before.get(field), after.get(field), args)


def cmd_codes(args):
    rows = paginate(request(args, "GET", "/api/admin/registration-codes") or [], args)
    emit(rows, args, columns=[("code", "注册码"), ("label", "标签"), ("is_active", "启用"), ("used_count", "已用"), ("max_uses", "上限"), ("expires_at", "过期")])


def cmd_new_code(args):
    body = build_body(args) or {}
    if getattr(args, "code", None):
        body.setdefault("code", args.code)
    if not body.get("code"):
        raise CliError("需要 --code 指定注册码,或用 --field code=XXX")
    emit(request(args, "POST", "/api/admin/registration-codes", body=body), args)


def cmd_keys(args):
    rows = paginate(request(args, "GET", "/api/admin/api-keys") or [], args)
    if not getattr(args, "show_secrets", False):
        rows = [{**row, "prefix": row.get("prefix", "")} for row in rows]
    emit(rows, args, columns=[("name", "名称"), ("prefix", "前缀"), ("owner_username", "归属"), ("owner_role", "角色"), ("scopes", "权限"), ("is_active", "启用"), ("use_count", "调用"), ("last_used_at", "最后使用")])


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

    created = request(args, "POST", "/api/admin/api-keys", body=body)
    out_file = getattr(args, "out_file", None)
    if out_file:
        _write_private(out_file, created.get("secret", ""))
        created = {k: v for k, v in created.items() if k != "secret"}
        created["secret_file"] = out_file
        emit(created, args)
        if not getattr(args, "quiet", False):
            print(f"明文已写入 {out_file}(权限 0600)。验完请立即删除:rm {out_file}", file=sys.stderr)
        return
    emit(created, args)
    if not getattr(args, "quiet", False):
        print("明文只显示这一次,请立刻保存;别落进日志或仓库。", file=sys.stderr)


def cmd_key_action(args):
    path = f"/api/admin/api-keys/{args.key_id}/{args.action}"
    emit(request(args, "POST", path), args)


def cmd_announcements(args):
    rows = paginate(request(args, "GET", "/api/admin/announcements") or [], args)
    emit(rows, args, columns=[("id", "ID"), ("title", "标题"), ("is_active", "启用"), ("publish_at", "发布"), ("expires_at", "过期")])


def cmd_new_announcement(args):
    emit(request(args, "POST", "/api/admin/announcements", body=build_body(args)), args)


def cmd_settings(args):
    data = (request(args, "GET", "/api/admin/settings") or {}).get("settings", {})
    hide = not getattr(args, "show_secrets", False)
    item_columns = [("key", "键"), ("value", "值")]
    if isinstance(data, dict):
        rows = []
        for key, value in data.items():
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
            if hide and _looks_secret(key) and text and not text.startswith("****"):
                text = _mask_local(text)
            rows.append({"key": key, "value": text})
        rows.sort(key=lambda r: r["key"])
        emit(rows, args, columns=item_columns)
        return
    emit(data, args)


def _looks_secret(key: str) -> bool:
    lowered = (key or "").lower()
    return any(hint in lowered for hint in ("api_key", "app_key", "secret", "token", "password"))


def _mask_local(value: str) -> str:
    return "****" + value[-4:] if len(value) > 4 else "*" * len(value)


def cmd_set_settings(args):
    body = build_body(args) or {}
    if not body:
        raise CliError("需要 --field k=v 指定要改的设置项")
    for key, value in body.items():
        if isinstance(value, str):
            _if_missing_key(value, key)

    before = (request(args, "GET", "/api/admin/settings") or {}).get("settings", {})
    request(args, "PATCH", "/api/admin/settings", body=body)
    emit_ok("已提交", args)

    if getattr(args, "no_verify", False) or getattr(args, "quiet", False):
        return
    after = (request(args, "GET", "/api/admin/settings") or {}).get("settings", {})
    for key in body:
        if key not in after:
            print(f"  {key}: 未写入 —— 这个键不在后端白名单里,被静默忽略了。")
            continue
        # 脱敏值不能用来判断是否生效,只报告写前是否已经是目标值
        emit_diff(key, before.get(key), after.get(key), args)


def cmd_releases(args):
    emit(request(args, "GET", "/api/admin/release-center"), args)


def cmd_novels(args):
    rows = paginate(request(args, "GET", "/api/admin/novels") or [], args)
    emit(rows, args, columns=[("id", "ID"), ("title", "标题"), ("status", "状态"), ("updated_at", "更新")])


def cmd_raw(args):
    verb = args.verb.upper()
    if verb not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        raise CliError(f"不支持的方法:{verb}")
    body = build_body(args) if verb in ("POST", "PATCH", "PUT") else None
    emit(request(args, verb, args.path, body=body,
                 params=parse_kv(getattr(args, "param", None))), args)


def _write_private(path: str, content: str) -> None:
    """0600 写盘 —— 密钥文件不该给同机其他账号可读。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception:
        os.close(fd)
        raise


COMMANDS = {
    "whoami": cmd_whoami,
    "selftest": cmd_selftest,
    "today": cmd_today,
    "tomorrow": cmd_tomorrow,
    "week": cmd_week,
    "next": cmd_next,
    "overview": cmd_overview,
    "export": cmd_export,
    "import": cmd_import,
    "snapshot": cmd_snapshot,
    "semesters": cmd_semesters,
    "courses": cmd_courses,
    "schedules": cmd_schedules,
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
        epilog="示例:moleplan tomorrow | moleplan week | moleplan next | moleplan selftest",
    )
    # 全局开关必须写在子命令之前 —— `moleplan courses --compact` 会被子解析器拒掉。
    parser.add_argument("--base-url", help="后端地址,默认读 MOLEPLAN_BASE_URL")
    parser.add_argument("--key", help="覆盖 MOLEPLAN_API_KEY")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--compact", action="store_true", help="单行 JSON(非 TTY 时自动开启)")
    parser.add_argument("--pretty", action="store_true", help="强制多行缩进 JSON")
    parser.add_argument("--format", choices=["json", "table", "md"], help="列表类命令的呈现方式")
    parser.add_argument("--quiet", action="store_true", help="只输出数据")
    parser.add_argument("--no-verify", action="store_true", help="写操作后不做回读校验")
    parser.add_argument("--show-secrets", action="store_true", help="显示被遮蔽的敏感键真值")
    parser.add_argument("--tz", default=DEFAULT_TZ, help=f"时区,默认 {DEFAULT_TZ}")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_write_flags(p):
        p.add_argument("--json", dest="json_body", help="完整 JSON 请求体")
        p.add_argument("--field", action="append", help="k=v,可重复")
        return p

    def add_paging(p):
        p.add_argument("--limit", type=int, help="最多返回多少条")
        p.add_argument("--offset", type=int, help="跳过前多少条")
        return p

    def add_day_flags(p):
        p.add_argument("--date", help="按指定日期算,YYYY-MM-DD")
        p.add_argument("--holidays", help="假日 JSON(数组或 {日期:名称}),也接受文件路径")
        return p

    sub.add_parser("whoami", help="自检:身份、密钥 scope、可用能力")
    sub.add_parser("selftest", help="跑完整健康矩阵,输出 Markdown 表格")

    add_day_flags(sub.add_parser("today", help="今天上什么课"))
    add_day_flags(sub.add_parser("tomorrow", help="明天上什么课"))
    add_day_flags(sub.add_parser("week", help="本周课表(从周一算)"))
    add_day_flags(sub.add_parser("next", help="下节课是什么"))

    api_p = add_write_flags(sub.add_parser("api", help="直通任意端点"))
    api_p.add_argument("verb", help="GET/POST/PATCH/PUT/DELETE")
    api_p.add_argument("path", help="例如 /api/admin/overview")
    api_p.add_argument("--param", action="append", help="查询参数 k=v")

    sub.add_parser("overview", help="后台概览统计")

    export_p = sub.add_parser("export", help="导出我的全部课程数据")
    export_p.add_argument("--out", help="写入文件而非打印")

    import_p = sub.add_parser("import", help="导入课程数据(覆盖)")
    import_p.add_argument("--file", required=True)
    import_p.add_argument("--dry-run", action="store_true", help="只预览差异,不写入")

    sem_p = add_paging(sub.add_parser("semesters", help="学期列表"))
    sem_p.add_argument("--user", help="管理员:查看指定用户的学期")

    course_p = add_paging(sub.add_parser("courses", help="课程列表(默认当前学期)"))
    course_p.add_argument("--semester", help="学期 ID")
    course_p.add_argument("--user", help="管理员:查看指定用户的课程")

    sch_p = sub.add_parser("schedules", help="某门课的排课")
    sch_p.add_argument("--course", required=True)

    snap_p = sub.add_parser("snapshot", help="课程数据全量快照(已归一化)")
    snap_p.add_argument("--user", help="管理员:指定用户,默认自己")
    snap_p.add_argument("--out", help="写入文件而非打印")

    users_p = add_paging(sub.add_parser("users", help="用户列表"))
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

    add_paging(sub.add_parser("codes", help="注册码列表"))

    nc_p = add_write_flags(sub.add_parser("new-code", help="新建注册码"))
    nc_p.add_argument("--code", help="注册码内容")

    add_paging(sub.add_parser("keys", help="API 密钥列表"))

    nk_p = add_write_flags(sub.add_parser("new-key", help="签发 API 密钥"))
    nk_p.add_argument("--name", help="密钥名称")
    nk_p.add_argument("--read-only", action="store_true", help="只读密钥")
    nk_p.add_argument("--days", type=int, help="有效天数,默认永久")
    nk_p.add_argument("--out-file", help="明文写入该文件(0600),不进 stdout")

    ka_p = sub.add_parser("key-action", help="轮换/撤销密钥")
    ka_p.add_argument("key_id")
    ka_p.add_argument("action", choices=["rotate", "revoke"])

    add_paging(sub.add_parser("announcements", help="公告列表"))
    add_write_flags(sub.add_parser("new-announcement", help="发布公告"))

    sub.add_parser("settings", help="系统设置(密钥类键已遮蔽)")
    add_write_flags(sub.add_parser("set-settings", help="更新系统设置(自动回读)"))

    sub.add_parser("releases", help="版本中心状态")
    add_paging(sub.add_parser("novels", help="小说列表(与课程无关,属管理后台内容模块)"))

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "show_secrets", False):
        print("警告:--show-secrets 会把密钥真值打进输出,注意别落进日志。", file=sys.stderr)

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
