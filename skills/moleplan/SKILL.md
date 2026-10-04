---
name: moleplan
description: 查 MolePlan（分子计划）的课表与课程数据，并远程操作其管理后台——今天/明天/本周上什么课、下节课是什么、学期课程排课、全量快照导出，以及用户与会员管理、注册码、公告、系统设置、版本中心。用 MOLEPLAN_API_KEY 鉴权，不需要账号密码。
version: 1.2.0
author: cake
license: MIT
metadata:
  hermes:
    tags: [MolePlan, 课程表, 课表, 明天上什么, Productivity, API, Admin]
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

MolePlan 的课程数据在 `kb.555615.xyz`。用一把管理员密钥就能查课表，并操作管理后台的
全部功能——用户、会员、注册码、公告、系统设置、版本发布。

**不要用账号密码登录。** 用 `MOLEPLAN_API_KEY`（`mpk_` 开头），它由管理员在后台签发，
可以随时撤销和轮换。密钥由 agent 的环境变量机制注入，不会出现在对话里。

所有命令都通过一个零依赖脚本调用：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py <command> [args]
```

默认打 `https://kb.555615.xyz`。如果你的 skill 配置里 `moleplan.base_url` 是别的地址，
在每条命令上加 `--base-url <那个地址>`（这个配置项不会变成环境变量，只有 API key 会）。

## When to Use

- 「明天上什么」「这周有哪几节课」「下节课是什么」「后天呢」
- 「我这学期有哪些课」「某门课在哪些周上」「课表导出来」
- 要在后台改东西：封禁用户、发公告、发注册码、续会员、看统计、推版本
- 用户给了 `mpk_` 开头的密钥，或提到 MolePlan / 分子计划 / 课表后端

## 先自检

不确定环境是否正常时，先跑这两条：

```bash
# 我是谁、有什么权限
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py whoami

# 整条链路健康矩阵:读接口 + 同值写回回读 + 密钥签发撤销,输出 Markdown 表格
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py selftest
```

`whoami` 返回 `auth_type: "api_key"`、`is_admin`、`can_write` 和 `capabilities`。
`can_write: false` 说明是只读密钥，写操作会 403。

## 查课表（最高频）

这几条命令已经把「学期起始日 + 周次 + 星期几 + 作息表」算好了，直接输出人话，
**不要自己去拉 snapshot 再算周次**：

```bash
# 今天 / 明天
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py today
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py tomorrow

# 本周整周(从周一算)
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py week

# 下节课(往后找 14 天)
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py next

# 指定日期 —— 「放假回来那天有什么课」就用这个
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py today --date 2026-10-08
```

输出形如：

```
周四 10-08 · 第 6 周
  周四 10-08 第5-6节 14:00–15:35 神经网络与深度学习 @12117
```

时区默认 `Asia/Shanghai`，用 `--tz` 改。周次按学期 `start_date` 推算，超出
`total_weeks` 的日子视为假期，显示「没有课」。

**放假与调休不在课表数据里。** 想标注假日，把日期传进去：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py week \
  --holidays '{"2026-10-01":"国庆","2026-10-08":"复课"}'
```

`--holidays` 也接受文件路径。刻意不内置法定节假日表——放假和调休每年由国务院通知
确定，写死在 skill 里迟早会给错答案。学校层面的调课、补课同样不在数据里，回答
「今天上不上课」这类问题时要说清这一点。

## Quick Reference

课表原始数据：

```bash
# 学期 / 某学期课程 / 某门课的排课
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py semesters
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py courses --semester <学期ID>
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py schedules --course <课程ID>

# 全量快照(已归一化),落盘用 --out
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py snapshot --out /tmp/moleplan.json
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py export --out /tmp/dump.json

# 导入前先预览差异,避免直接覆盖
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py import --file /tmp/dump.json --dry-run
```

管理后台：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py overview
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py users --query 张 --role admin --limit 20
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py user <用户ID>
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py snapshot --user <用户ID>

# 用户操作:ban / unban / kick-sessions / password（成功后自动回读并打 diff）
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py user-action <用户ID> ban --field "reason=违规刷课"
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py user-action <用户ID> password --field "password=<新密码>"

# 会员（同上,自动回读 status / expires_at）
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py membership <用户ID> grant --field days=90
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py membership <用户ID> revoke

# 注册码 / 公告 / 密钥
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py codes --limit 50
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py new-code --code INVITE2026 --field "label=内测邀请"
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py announcements
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py new-announcement --json '{"title":"停服通知","body":"周六 02:00 停服 30 分钟"}'
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py keys
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py new-key --name hermes-lab --read-only --days 30 --out-file /tmp/agent.key

# 系统设置 / 版本中心 / 小说（与课程无关,是后台的内容模块）
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py settings
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py set-settings --field registration_requires_code=true
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py releases
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py novels --limit 20
```

任一端点的直通（CLI 没封装的接口都能这么打）：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api GET /api/admin/overview
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api POST /api/semesters \
  --json '{"name":"2026春季学期","start_date":"2026-02-23","total_weeks":20}'
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py api GET /api/admin/users --param "query=张"
```

## 开关写在哪个位置（很容易踩）

**全局开关必须写在子命令前面**，子命令自己的开关写在后面：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py --format table users --limit 20
#                                            ^^^^^^^^^^^^^^ 全局    ^^^^^^^^^ 子命令
```

全局：`--base-url` `--key` `--timeout` `--compact` `--pretty` `--format` `--quiet`
`--no-verify` `--show-secrets` `--tz`

子命令：`--limit` `--offset`（列表类）、`--date` `--holidays`（课表类）、
`--json` `--field`（写请求）

写反了会报 `invalid choice` 或 `unrecognized arguments`。

## 输出格式

默认：终端里给多行缩进 JSON，**管道或非交互环境自动压成单行**（聊天里不刷屏）。

```bash
--format table   # 列表类命令给对齐的表格
--format md      # Markdown 表格,适合直接贴进回复
--pretty         # 强制多行,即使在被管道里
--compact        # 强制单行
```

## 写请求怎么传参数

两种方式，**优先用 `--json`**：

```bash
--json '{"name":"高等数学","teacher":"王教授"}'     # 稳妥,值里有空格/标点也不会出错
--field name=高等数学 --field teacher=王教授        # 简短,但含空格的值必须整体加引号
```

`--field "content=今晚 23:00 维护"` 能工作；`--field content=今晚 23:00 维护` 会被 shell
拆成两段参数直接报错。中文长文本、时间、标点一律用 `--json`。

## 数据结构

`snapshot` / `export` 出来的结构已经归一化，可以直接用：

- `schedules` 是**扁平列表**，每条带 `course_id`
- `courses[].schedules` 也已填好（上游这里是空的，直接按它过滤会一条都拿不到）
- `week_numbers` **永远是数组**。这套数据在不同路径下形态不一致（写库时是
  `{"data":[...]}`，有的接口原样回传），CLI 统一收敛成有序去重数组

排课语义：`day_of_week` 是 1–7（周一到周日）；`start_period`/`end_period` 是第几节；
`week_type` 取 `all`/`odd`/`even`/`custom`，`custom` 时用 `week_numbers` 判断周次。
具体时钟时间在 `timeConfigs` 里按 `period_number` 对应。

**注意**：没有 `/api/time-configs` 这个路径。作息要么从 `snapshot.timeConfigs` 取，
要么用 `/api/semesters/{sid}/time-configs`。

## Pitfalls

**字段名写错会被静默忽略。** 后端对请求体里的未知字段不报错，直接丢掉。公告的正文
字段是 `body`，不是 `content`——传 `content` 会得到一条正文为空的公告。系统设置也有
白名单，写白名单外的键同样静默无效（CLI 会提示「不在后端白名单里」）。

**`settings` 里的密钥类键读不出真值。** `llm_api_key`、`asr_app_key` 这些由后端脱敏成
`****xxxx`，只能覆盖写入、不能读回。`--show-secrets` 只是关掉 CLI 这一层的二次遮蔽，
拿不到后端本来就不返回的明文。

**`exit 0` 不代表业务生效。** `user-action`、`membership`、`set-settings` 这些命令已经
自动回读并打印 `旧值 -> 新值`，看到 diff 才算数。加 `--no-verify` 可跳过。

**密钥明文只出现一次。** 后台签发/轮换时显示，关掉就再也拿不到。用 `--out-file` 让它
以 0600 权限落盘而不是进 stdout（stdout 容易进日志），验完立刻删。不要写进脚本、
不要提交到仓库。

**401 与 403 含义不同。** 401 = 密钥无效、已撤销或已过期；403 = 密钥是只读的，
或密钥归属的用户不是管理员。遇 401 去后台重新签发，不要改代码重试。

**`--user` 系列要求管理员密钥。** 普通用户身份签发的密钥读别人的数据会 403；
读自己的用 `snapshot`（不带 `--user`）。

**列表接口没有服务端分页。** `--limit/--offset` 是拉全量后本地切片；用户量很大时
先加 `--query` 缩小范围。

**小说（`novels`）与课程无关。** 它是管理后台的内容模块，列在这里只是为了让
「后台有什么」这件事完整。

## Verification

```bash
# 一条命令自证:身份 → 读接口 → 同值写回回读 → 密钥签发撤销
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py selftest

# 只读路径的最小验证
python3 ${HERMES_SKILL_DIR}/scripts/moleplan.py today
```

`selftest` 输出一张 Markdown 表格，全 OK 就是整条链路通。只读密钥会把管理端那几项
标成 SKIP，属正常。

写入路径的安全验法：拿一个已知值同值写回再读回（`set-settings` 已内建），不要用
「造一条测试数据」的方式验证生产环境。
