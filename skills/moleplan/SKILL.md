---
name: moleplan
description: 读写 MolePlan（分子计划）的课程数据并远程操作其管理后台——学期/课程/排课、全量快照、用户与会员管理、注册码、公告、系统设置、版本中心。用 MOLEPLAN_API_KEY 鉴权，不需要账号密码。
version: 1.0.0
author: cake
license: MIT
metadata:
  hermes:
    tags: [MolePlan, 课程表, 课表, Productivity, API, Admin]
    config:
      - key: moleplan.base_url
        description: MolePlan 后端地址
        default: "https://kb.555615.xyz"
        prompt: MolePlan 后端地址（默认线上）
required_environment_variables:
  - name: MOLEPLAN_API_KEY
    prompt: MolePlan API Key
    help: 在 MolePlan 管理后台 →「Agent 密钥」页签发，明文形如 mpk_xxxx
    required_for: 调用 MolePlan 后端（读课表、管后台）
---

# MolePlan 课程数据与管理后台

MolePlan 的课程数据在 `kb.555615.xyz`。用一把管理员密钥就能读写课表，并操作
管理后台的全部功能——用户、会员、注册码、公告、系统设置、版本发布——路由不变。

**不要用账号密码登录。** 用 `MOLEPLAN_API_KEY`（`mpk_` 开头），它由管理员在后台签发，
可以随时撤销和轮换。密钥由 Hermes 注入到环境变量，不会出现在对话里。

所有命令都通过一个零依赖脚本调用：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py <command> [args]
```

它默认打 `https://kb.555615.xyz`。后端换地址时，加 `--base-url https://...`。

## When to Use

- 用户问「我这学期有什么课」「明天上什么」「帮我把课表导出来」
- 要把课表/课程数据拉下来做分析、提醒、周报
- 要在后台改东西：封禁用户、发公告、发注册码、续会员、看统计、推版本
- 用户给了 `mpk_` 开头的密钥，或提到 MolePlan / 分子计划 / 课表后端

## 先自检

任何操作之前先跑一条命令确认密钥可用、权限够：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py whoami
```

返回 `auth_type: "api_key"`、`is_admin`、`can_write` 和 `capabilities`。
`can_write: false` 说明这是一把只读密钥，写操作会返回 403。

## Quick Reference

课堂数据：

```bash
# 全部课程数据一次性拉下来（学期 + 课程 + 排课 + 作息 + 设置）
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py snapshot

# 学期列表 / 某学期的课程（不带 --semester 时用当前学期）
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py semesters
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py courses --semester <学期ID>
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py courses --user <用户ID>   # 管理员看别人

# 某门课的排课
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py schedules --course <课程ID>
```

管理后台：

```bash
# 统计概览 / 用户 / 用户详情
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py overview
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py users --query 张 --role admin
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py user <用户ID>
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py snapshot --user <用户ID>

# 用户操作：ban / unban / kick-sessions / password
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py user-action <用户ID> ban --field "reason=违规刷课"
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py user-action <用户ID> password --field "password=<新密码>"

# 会员 / 注册码 / 公告 / 密钥
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py membership <用户ID> grant --field days=90
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py new-code --code INVITE2026 --field "label=内测邀请"
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py new-announcement --json '{"title":"停服通知","body":"周六 02:00 停服 30 分钟"}'
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py keys
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py new-key --name hermes-lab --read-only --days 30

# 系统设置 / 版本中心
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py settings      # 值在返回的 .settings 下面
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py set-settings --field registration_requires_code=true
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py releases
```

任一端点的直通（CLI 没封装的接口都能这么打）：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api GET /api/admin/overview
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api POST /api/semesters \
  --json '{"name":"2026春季学期","start_date":"2026-02-23","total_weeks":20}'
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api GET /api/admin/users --param "query=张" --param "role=admin"
```

## 写请求怎么传参数

两种方式，**优先用 `--json`**：

```bash
--json '{"name":"高等数学","teacher":"王教授"}'     # 稳妥，值里有空格/标点也不会出错
--field name=高等数学 --field teacher=王教授        # 简短，但含空格的值必须整体加引号
```

`--field "content=今晚 23:00 维护"` 能工作；`--field content=今晚 23:00 维护` 会被 shell
拆成两段参数直接报 `unrecognized arguments`。所以涉及中文长文本、时间、标点，一律用 `--json`。

## 典型任务

**导出课表做分析**

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py snapshot --out /tmp/moleplan.json
```

拿到 `semesters` / `courses` / `schedules` / `timeConfigs`。`schedules` 里
`day_of_week` 是 1–7（周一到周日），`start_period`/`end_period` 是第几节；
`week_type` 为 `all`/`odd`/`even`/`custom`，`custom` 时 `week_numbers.data` 是周次数组。
作息时间在 `timeConfigs` 里按 `period_number` 对应具体时钟。

**新建一门课并排课**

先拿学期 ID，再建课程，再排课：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py semesters
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api POST /api/semesters/<学期ID>/courses \
  --json '{"name":"高等数学","teacher":"王教授","location":"教三楼301"}'
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api POST /api/courses/<课程ID>/schedules \
  --json '{"day_of_week":1,"start_period":1,"end_period":2,"week_type":"all"}'
```

**查某个用户的学习情况**

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py users --query <用户名>
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py snapshot --user <用户ID>
```

## Pitfalls

**字段名写错会被静默忽略。** 后端对请求体里的未知字段不报错，直接丢掉。公告的正文
字段是 `body`，不是 `content`——传 `content` 会得到一条正文为空的公告。写之前先用
`api GET` 看一眼现有数据的字段名。

**系统设置只认白名单里的 key，写错同样静默忽略。** `set-settings` 传了白名单外的键
不报错也不写入。而且 `settings` 返回的是 `{"settings": {...}}` 嵌套结构，值在
`.settings` 下面，直接取顶层会全部读成空。写完务必读回确认：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py settings \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['settings']['registration_requires_code'])"
```

**exit code 0 不代表业务生效。** `user-action`、`membership`、`batch` 这类操作返回
`{"ok": true}` 只说明请求被受理。要确认结果得回读：改完用户状态就 `user <ID>` 看
`status`，改完会员就看详情里的 `membership`。

**401 与 403 含义不同。** 401 = 密钥无效、已撤销或已过期；403 = 密钥是只读的，
或密钥归属的用户不是管理员。遇到 401 要去后台重新签发，不要改代码重试。

**撤销和轮换是立即生效的。** 后台一撤销，使用它的 agent 下一个请求就 401；
轮换会给出一把全新的明文，旧的整体作废。

**密钥明文只出现一次。** 后台签发/轮换时显示，关掉就再也拿不到，只能轮换重发。
不要把它写进脚本、提交到仓库、或打印到会话里。要换机器就让管理员重签一把。

**`--user` 系列命令要求管理员密钥。** 用普通用户身份签发的密钥去读别人的数据会 403，
读自己的用 `snapshot`（不带 `--user`）。

**返回的是 JSON,不是格式化文本。** 用 `python3 -c` 或 `jq` 在管道里处理。加 `--compact`
输出单行，方便接管道。

## Verification

自检链路，30 秒能确认整条通路：

```bash
# 1. 密钥有效且知道自己是谁
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py whoami

# 2. 能读到真实数据
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py semesters

# 3. 管理员密钥能读到后台统计
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py overview
```

三步都返回 JSON 就是通的。`whoami` 返回 `capabilities` 就是权限齐的；
只读密钥会在 `overview` 那步报 403，此时读课表仍然正常。
