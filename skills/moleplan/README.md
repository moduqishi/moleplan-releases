# MolePlan Agent 接入（API Key + Skill）

让 hermes 这类 agent 免密码读写课程数据、并操作管理后台的全部功能。

核心设计：**agent 不持有账号密码**，而是用一把由管理员在后台签发的 API Key。
密钥可随时撤销、轮换，带权限范围，且每一次调用都留审计记录。

## 一键接入

主仓库是私密的，skill 同步发布在公开仓库。**把下面这段整段复制发给你的 agent**：

```text
帮我接入 MolePlan（分子计划）课程数据。

1. 装 skill：hermes skills install moduqishi/moleplan-releases/skills/moleplan --category productivity --yes
2. 检查 MOLEPLAN_API_KEY 是否已配置。没有就先停下，告诉我需要密钥，
   我会去管理后台签发（用户 → Agent 密钥 页）。拿到之后存进你的密钥机制，不要回显明文。
3. 自检：跑 skill 里的 whoami，把 auth_type / is_admin / can_write 报给我。
4. 读出我当前学期的课程，列出来给我看。
```

没密钥时 agent 会停在第 2 步等你，这是刻意的 —— 密钥不经过对话，也就不会进入
模型上下文。

不是 Hermes 的 agent 工具（Cursor、Codex 等）用同一段 prompt 也行，只是第 1 步
换成手动取文件、第 2 步换成自己设环境变量 `MOLEPLAN_API_KEY`，之后照常调用
skill 目录下的 `scripts/moleplan.py`。

在本仓库开发时，用 `scripts/install_moleplan_skill.sh` 把 `skills/moleplan/`
装进本地 skill 目录。

## 为什么不是「给 agent 一个管理员账号」

| | 账号密码 | API Key（方案） |
|---|---|---|
| 凭据形态 | 长期不变，泄漏即失守 | 可撤销、可轮换、可设有效期 |
| 权限 | 全有或全无 | `admin:read` 只读 / `admin:full` 读写 |
| 审计 | 只能看到「某账号登录过」 | 每次调用记 `last_used_at` 与调用次数 |
| 改密影响 | 会踢掉真人登录 | 与真人登录互不干扰 |
| 泄漏面 | 密码可能被复用 | 明文只出现一次，库里只有 sha256 |

## 后端接口

新增 `/api/admin/api-keys`（管理端点）与 `/api/agent/me`（自检端点）。

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/admin/api-keys` | 列出所有密钥（不含明文） |
| `POST` | `/api/admin/api-keys` | 签发，**明文只在本次响应返回** |
| `PATCH` | `/api/admin/api-keys/{id}` | 改名称/备注/权限/有效期/启用状态 |
| `POST` | `/api/admin/api-keys/{id}/rotate` | 轮换：旧密钥立即作废，返回新明文 |
| `POST` | `/api/admin/api-keys/{id}/revoke` | 撤销（软删除，保留审计记录） |
| `DELETE` | `/api/admin/api-keys/{id}` | 彻底删除记录 |
| `GET` | `/api/agent/me` | 当前调用方的身份、权限范围与可用能力 |

鉴权在 `app/api/deps.py` 的 `get_current_user` 里单点收口，因此**全部既有端点
（含所有 `/api/admin/*` 与 `/v1/chat/completions`）自动支持 Key 鉴权**，无需逐个改造。

```bash
curl -s https://kb.555615.xyz/api/admin/api-keys \
  -H "Authorization: Bearer $MOLEPLAN_API_KEY"
```

也支持 `X-API-Key: <key>` 头，方便不便自定义 `Authorization` 的客户端。

### 密钥形态

```
mpk_<48 位十六进制>        192 bit 熵，secrets.token_hex(24)
```

- 库里只存 `sha256(明文)`，不存明文，也不存可逆加密
- `prefix` 字段保留前 12 字符，供后台列表展示与排查
- 因为熵足够高，用 sha256 即可；不需要 bcrypt 这类慢哈希（那是给低熵口令用的）

### 权限模型

密钥的权限 = **归属用户的角色** × **密钥自身的 scope**。

- 归属管理员 + `admin:full` → 管理后台全量读写（本方案默认）
- 归属管理员 + `admin:read` → 只读；非 GET/HEAD 请求直接 403
- 归属普通用户 + `admin:full` → 只能读写自己的课程数据，碰管理端点报 403

## 签发与使用

1. 登录管理后台 → **用户 → Agent 密钥** → 签发密钥
2. 名称随便取（例如 `hermes-课表助手`），归属默认你自己，权限默认「读写」
3. **立刻复制明文**——关掉弹窗就再也取不到了，丢了只能轮换
4. 把它配置为环境变量。agent 会通过 skill 声明的
   `required_environment_variables` 接管它，明文不会进入模型上下文：

```bash
MOLEPLAN_API_KEY=mpk_xxxxxxxxxxxx
MOLEPLAN_BASE_URL=https://kb.555615.xyz
```

5. 自检：

```bash
python3 <skill 目录>/scripts/moleplan.py whoami
```

`whoami` 返回 `auth_type: "api_key"`、`is_admin: true`、`can_write: true`
以及能力清单，就说明整条链路通了。

## CLI 能做什么

零依赖（只用标准库），位于 `skills/moleplan/scripts/moleplan.py`：

```bash
# 课表 —— 周次和作息都算好了,直接给人话
moleplan today / tomorrow / week / next
moleplan today --date 2026-10-08 --holidays '{"2026-10-08":"复课"}'

# 原始数据
moleplan semesters / courses / schedules / snapshot --out /tmp/x.json
moleplan import --file /tmp/x.json --dry-run    # 导入前预览差异

# 后台
moleplan overview / users / user / codes / announcements / settings / releases

# 写入(成功后自动回读并打印 旧值 -> 新值)
moleplan user-action <用户ID> ban --field 'reason=...'
moleplan membership <用户ID> grant --field days=90
moleplan set-settings --field registration_requires_code=true

# 自检
moleplan selftest    # 健康矩阵,输出 Markdown 表格
moleplan whoami
```

全局开关写在子命令**前面**：`moleplan --format table users --limit 20`。写反会报
`invalid choice`。完整说明见 [SKILL.md](SKILL.md)。

## 安全须知

- **明文只在签发与轮换时显示一次。** 后台列表、日志、接口响应都只有 `mpk_` 前缀。
- **不要提交密钥。** 别写进脚本、别贴进 prompt。skill 声明的
  `required_environment_variables` 由 agent 自己保管，不会进入模型上下文。
- **按需给权限。** 只做查询的 agent 就签 `admin:read`。
- **吊销优先于删除。** `revoke` 会保留审计痕迹（谁在什么时候用过），`delete` 只用于清理。
- **轮换走 `/rotate`**，不要「删掉重签」——轮换保留历史记录与归属关系。

## 已知陷阱

**路径不存在时返回 200 而不是 404。** 后端末尾有 SPA catch-all，任何未注册路径都会
返回管理后台的 `index.html`。直接 `curl` 很难分辨「接口不存在」和「接口正常但返回空」——
skill 里的 CLI 已经拦掉这种情况并明确报错，自己写脚本时要留意。

**线上有 Cloudflare 前置。** 默认的 `Python-urllib/3.x` User-Agent 会被浏览器指纹
检查拦成 403。CLI 已显式设置 UA，自写客户端请照做。

**写请求字段名错误会被静默忽略。** 后端对请求体里的未知字段不报错。例如公告的正文
字段是 `body` 而非 `content`，传错会得到一条空正文公告。

**权限不足时的状态码。** 401 = 密钥无效/已撤销/已过期；403 = 只读密钥执行写操作，
或归属用户不是管理员。

**课表数据不含放假与调休。** `week` 视图里的「没有课」只代表没有排课，不代表放假。
法定节假日每年由国务院通知确定，skill 刻意不内置这张表；要标注就传 `--holidays`。
学校的调课、补课同样不在数据里。

**同一份数据在不同接口里形态不一致。** 导出接口的 `courses[].schedules` 是空的
（排课在扁平的 `schedules` 里），而 `week_numbers` 写库时是 `{"data":[...]}`、
读出来有可能是裸数组。CLI 已统一归一化（`week_numbers` 永远是数组、
`courses[].schedules` 一定填好），自己写客户端时要注意。

**`settings` 的密钥值读不出来。** `llm_api_key`、`asr_app_key` 由后端脱敏成
`****xxxx` 后返回，只能覆盖写入。`--show-secrets` 关掉的只是 CLI 层的二次遮蔽。
