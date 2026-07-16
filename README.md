# cdk-gpt发卡

一个面向账号文件交付场景的 CDK 发卡系统，提供账号库存导入、测活分组、随机卡密生成、一次性兑换、历史文件找回，以及 CPA / SUB JSON 格式转换。

- 在线站点：<https://cdk.ambition.qzz.io>
- 技术栈：FastAPI、SQLAlchemy、Jinja2、PostgreSQL / SQLite
- 运行方式：Python 应用 + Uvicorn + Apache / CDN 反向代理
- 访问模型：兑换、查找和格式转换公开使用；管理功能需要管理员登录

> 本公开仓库只包含应用源码、测试和脱敏部署示例，不包含生产配置、管理员账号或密码、CDK、账号 JSON、数据库、日志、临时下载文件及管理密钥。

## 主要能力

| 模块 | 功能 |
| --- | --- |
| CDK 兑换 | 输入单个或多个 CDK，选择 CPA 或 SUB 格式，生成一次性下载链接 |
| 历史查找 | 使用已经兑换过的 CDK 重建原绑定文件，不会重新分配库存 |
| 格式转换 | 自动识别原始 auth JSON、CPA、SUB、SUB2 和 ZIP，完成 JSON → CPA / SUB、CPA → SUB、SUB → CPA |
| 库存管理 | 上传、筛选、下载、禁用账号文件，记录来源格式、邮箱、状态、上传和交付时间 |
| CDK 管理 | 批量生成、下载、禁用、删除、设置兑换次数和过期时间，查看完整卡密与兑换记录 |
| 账号测活 | 对接独立 sub2 监测器，将库存分为正常号、问题号和待测号；兑换只提取正常号 |
| 权限管理 | 区分超级管理员和普通管理员；普通管理员的数据按创建者隔离 |
| 安全控制 | CSRF、会话校验、登录与兑换限流、上传限制、ZIP 炸弹防护、临时链接自动失效 |
| 审计记录 | 记录上传、生成、兑换、状态修改、重新生成链接和管理员操作时间 |

## 工作流程

```mermaid
flowchart LR
    A["管理员导入 JSON / CPA / SUB / ZIP"] --> B["格式识别与账号拆分"]
    B --> C["待测号库存"]
    C --> D["sub2 专属监测器"]
    D -->|正常| E["正常号分组"]
    D -->|401 或检测失败| F["问题号分组"]
    G["生成 32 位随机 CDK"] --> H["用户兑换"]
    E --> H
    H --> I["绑定原始账号文件"]
    I --> J["CPA / SUB 临时下载链接"]
    J -->|首次下载| K["链接撤销并清理文件"]
    I --> L["使用原 CDK 查找或再次兑换原文件"]
```

核心约束：CDK 首次兑换时绑定账号文件；之后增加兑换次数、查找或重新生成下载链接时，始终使用第一次绑定的文件，不能换号。

## 页面与接口

### 公开页面

| 路径 | 用途 |
| --- | --- |
| `/` | CDK 兑换和正常号、问题号、待测号库存展示 |
| `/convert` | CPA / SUB JSON 格式转换 |
| `/lookup` | 使用已兑换 CDK 查找原绑定文件 |
| `/admin/login` | 管理员登录入口 |
| `/d/{token}` | 一次性临时下载地址 |

### 公开 API

| 方法与路径 | 说明 |
| --- | --- |
| `GET /api/inventory` | 返回正常、问题、待测和总库存统计 |
| `POST /api/redeem` | 校验 CDK、绑定库存并生成 CPA / SUB 下载链接 |
| `POST /api/lookup` | 根据已兑换 CDK 重建原文件下载链接 |
| `POST /api/convert` | 上传文件并转换为 CPA 或 SUB |

公开 API 使用表单或 multipart 请求。成功的兑换、查找和转换响应会返回 `download_url`、文件名与过期时间；业务校验失败返回 JSON 错误，触发限流时返回 HTTP 429 和 `Retry-After`。

### 管理中心

| 页面 | 功能 |
| --- | --- |
| `/admin` | 库存、CDK、兑换、临时链接和当日流转概览 |
| `/admin/uploads` | 本地批量上传或手动粘贴 JSON |
| `/admin/files` | 账号文件筛选、状态修改、测活和批量下载 |
| `/admin/cards` | CDK 生成、策略设置、禁用、删除、下载及使用详情 |
| `/admin/liveness` | sub2 测活配置、刷新、同步和失败账号处理 |
| `/admin/profile` | 当前管理员修改自己的密码 |
| `/admin/users` | 超级管理员创建、启停和重置普通管理员 |

## 权限模型

| 能力 | 超级管理员 | 普通管理员 |
| --- | :---: | :---: |
| 登录管理中心 | ✓ | ✓ |
| 查看自己创建的文件和 CDK | ✓ | ✓ |
| 查看全部管理员的数据 | ✓ | — |
| 导入库存、生成和管理 CDK | ✓ | ✓ |
| 创建、禁用、重置普通管理员 | ✓ | — |
| 查看或修改 sub2 地址和管理密钥 | ✓ | — |
| 上传测活、手动刷新、同步、删除死号 | ✓ | — |
| 查看账号测活状态和错误标签 | ✓ | ✓（只读） |

系统没有公开注册入口。只有角色为 `super_admin` 或 `admin` 且状态启用的账号可以进入管理中心。

## CDK 与交付规则

- CDK 使用加密安全随机数生成，为 16 字节、32 位小写十六进制字符串。
- 新生成的 CDK 状态为可使用，无需再执行“上架”操作。
- 一户一码默认绑定一个账号文件。
- 单张 CDK 可设置 1–100 次兑换和可选过期时间。
- 单个 CDK 兑换 CPA 时返回一个 JSON；多个 CDK 返回 ZIP。
- SUB 输出会生成 sub2api 配置；多个 CDK 时使用 ZIP 打包。
- 单次最多输入 100 个 CDK，交付文件总大小不能超过 25 MB。
- 同一批次不能重复输入相同 CDK。
- 并发兑换通过数据库条件更新锁定使用次数和库存，避免超量兑换或一号多发。
- 第一次兑换后会保存 CDK、文件 ID、输出格式和兑换时间。
- 追加兑换次数只恢复原 CDK 的使用额度，再次兑换仍返回第一次绑定的账号。
- 卡密被禁用或过期后，兑换和公开查找均会拒绝。
- 管理员可以从兑换详情重新生成一次性下载链接。

## 账号库存与测活

导入后，每个账号保存为独立的库存记录。系统会记录原文件名、存储路径、上传管理员、来源格式、账号邮箱、上传时间、售出时间、绑定 CDK 和最近测活结果。

### 库存状态

| 状态 | 含义 |
| --- | --- |
| `available` | 可进入测活或兑换候选池 |
| `locked` | 正在被兑换流程原子占用 |
| `sold` | 已绑定并交付给 CDK |
| `voided` | 已禁用，不再参与兑换 |

### 测活分组

| 分组 | 判定 | 是否参与兑换 |
| --- | --- | :---: |
| 正常号 | `account_status=available` 且存在测活时间 | ✓ |
| 问题号 | `account_status=unavailable` | — |
| 待测号 | 尚无有效检测结果 | — |

sub2 连接只作为 CDK 库存监测器。普通管理员只能查看分组和错误标签；超级管理员可以配置监测器、上传并立即测活、刷新指定账号、同步到期状态和处理失败账号。

- 自动同步周期：20 分钟。
- 每次同步或手动刷新最多处理 50 个账号。
- 新上传账号会进入容量受限的后台测活队列。
- 兑换查询固定过滤为“库存可用 + 测活正常 + 已记录测活时间”。

## 支持的文件与转换

### 输入格式

- 原始 ChatGPT auth JSON。
- CPA 单账号 JSON 或账号数组。
- SUB / SUB2 配置。
- 包含上述 JSON 文件的 ZIP。

### 转换关系

| 输入 | 输出 CPA | 输出 SUB |
| --- | :---: | :---: |
| 原始 auth JSON | ✓ | ✓ |
| CPA | ✓（规范化或合并） | ✓ |
| SUB / SUB2 | ✓ | ✓（规范化或合并） |
| ZIP / 多文件 | ✓（账号数组） | ✓（单份 sub2api 配置） |

### 资源限制

| 项目 | 限制 |
| --- | ---: |
| 管理端单批上传原始大小 | 25 MB |
| 格式转换单次上传原始大小 | 20 MB |
| 手动 JSON 输入 | 2 MB |
| 单个 JSON 文档 | 2 MB |
| 单批上传文件数 | 100 |
| ZIP 内文档数 | 500 |
| ZIP 解压后总大小 | 25 MB |
| ZIP 最大压缩比 | 200:1 |
| 单次导入或转换账号数 | 500 |
| 单次兑换 CDK / 文件数 | 100 |
| 单次兑换文件总大小 | 25 MB |

ZIP 内只读取允许的 JSON 类文件，不支持加密 ZIP；路径会取安全文件名，避免归档路径穿越。

## 一次性下载链接

- 兑换、查找和后台重建链接：未下载时最长保留 24 小时。
- 格式转换链接：未下载时最长保留 10 分钟。
- 第一次成功下载会立即把数据库链接标记为撤销。
- 文件响应完成后会删除对应临时文件。
- 过期或已使用链接返回 HTTP 410。
- 应用启动时和运行期间每 10 分钟清理过期记录与孤立临时文件。

## 安全设计

### 管理安全

- 管理会话默认最长 8 小时，生产环境使用 `Secure`、`HttpOnly` 和严格 SameSite Cookie。
- 所有管理端写请求检查来源，并验证会话中的 CSRF token。
- 密码使用 bcrypt 哈希；密码修改会递增会话版本，使旧会话失效。
- 超级管理员不能通过普通管理员页面被查看、修改或禁用。
- 所有文件、CDK 和兑换查询均应用管理员数据范围过滤。
- 管理操作写入审计表。

### 防爆破与限流

限流记录只保存客户端和目标标识的 SHA-256 哈希，不保存原始 IP、账号名或 CDK。

| 操作 | 时间窗口 | 请求或失败阈值 |
| --- | ---: | --- |
| 兑换链接生成 | 60 秒 | 每客户端最多 5 次 |
| CDK 兑换 | 15 分钟 | 20 次请求；5 次失败；同一码 3 次失败 |
| CDK 查找 | 15 分钟 | 20 次请求；5 次失败；同一码 3 次失败 |
| 管理登录 | 15 分钟 | 60 次请求；5 次连续失败 |
| 格式转换 | 1 小时 | 20 次请求；10 次失败 |

### 资源与响应保护

- 上传、转换和兑换分别使用容量信号量，避免高并发同时占满 CPU 和内存。
- 请求体大小在应用和 Apache 两层限制。
- ZIP 文档数量、解压总量和压缩比均有上限。
- 临时下载响应使用 `private, no-store`。
- 默认发送 `nosniff`、CSP、Frame、Referrer 和 Permissions Policy 安全响应头。
- `public_base_url` 必须是 HTTPS；仅本机开发允许 `127.0.0.1` 或 `localhost` 使用 HTTP。
- 只有可信反向代理覆盖转发头时才能启用 `trust_proxy_headers`。

## 快速开始

### 环境要求

- Python 3.11 或更高版本。
- SQLite 可用于本地体验和测试。
- PostgreSQL 推荐用于生产环境。
- Linux 生产部署建议使用 systemd、Apache 或其他可信反向代理。

### Windows / PowerShell

```powershell
git clone https://github.com/dqt081228OHVGFY/cdk-gpt-card.git
Set-Location cdk-gpt-card

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Copy-Item config.example.ini config.ini
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

把最后一条命令生成的值写入 `config.ini` 的 `session_secret`，同时修改超级管理员账号和强密码，然后启动：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 18743
```

访问：

- 公开首页：<http://127.0.0.1:18743/>
- 格式转换：<http://127.0.0.1:18743/convert>
- 管理登录：<http://127.0.0.1:18743/admin/login>

### Linux

```bash
git clone https://github.com/dqt081228OHVGFY/cdk-gpt-card.git
cd cdk-gpt-card

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp config.example.ini config.ini
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

修改 `config.ini` 后运行：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 18743
```

## 配置说明

配置优先级为：环境变量 > `config.ini` > 应用默认值。生产 `config.ini` 已被 `.gitignore` 排除，禁止提交到公开仓库。

### `config.ini`

```ini
[database]
url = postgresql+psycopg://cdk_user:replace-password@127.0.0.1:5432/cdk

[storage]
dir = /var/lib/cdk/storage

[server]
public_base_url = https://cdk.example.com

[security]
session_secret = replace-with-at-least-32-random-characters
super_admin_username = owner@example.com
super_admin_password = replace-with-a-strong-super-admin-password
admin_username =
admin_password =
trust_proxy_headers = true
cookie_secure = true

[liveness]
dashboard_url = https://sub2.example.com/admin/cdk-liveness
```

### 环境变量

| 环境变量 | 说明 |
| --- | --- |
| `TIKAWANG_CONFIG_FILE` | 指定配置文件路径 |
| `TIKAWANG_DATABASE_URL` | 覆盖数据库连接地址 |
| `TIKAWANG_STORAGE_DIR` | 覆盖持久化存储目录 |
| `TIKAWANG_SERVER_PUBLIC_BASE_URL` | 对外 HTTPS 基础地址 |
| `TIKAWANG_SECURITY_SESSION_SECRET` | 管理会话签名密钥，至少 32 位 |
| `TIKAWANG_SECURITY_SUPER_ADMIN_USERNAME` | 首次初始化的超级管理员账号 |
| `TIKAWANG_SECURITY_SUPER_ADMIN_PASSWORD` | 首次初始化的超级管理员密码 |
| `TIKAWANG_SECURITY_ADMIN_USERNAME` | 可选的普通管理员账号 |
| `TIKAWANG_SECURITY_ADMIN_PASSWORD` | 可选的普通管理员密码 |
| `TIKAWANG_SECURITY_TRUST_PROXY_HEADERS` | 是否信任反向代理覆盖后的来源头 |
| `TIKAWANG_SECURITY_COOKIE_SECURE` | 是否仅通过 HTTPS 发送会话 Cookie |

### 管理员初始化

空数据库第一次启动时会根据当前配置创建超级管理员。普通管理员配置留空时不会自动创建，可在超级管理后台中添加。

已有账号不会因为之后修改 `config.ini` 而自动覆盖密码；请登录管理中心修改，或通过超级管理员重置普通管理员密码。

## 数据库与存储

应用启动时会创建基础表，并兼容补充当前版本需要的字段。

| 数据 | 默认位置或表 |
| --- | --- |
| SQLite 数据库 | `data/tikawang.db` |
| 上传账号文件 | `storage/uploads/` |
| 临时交付文件 | `storage/downloads/` |
| 管理员 | `users` |
| 账号库存 | `files` |
| CDK | `cards` |
| 兑换记录 | `redemptions` |
| 一次性链接 | `temporary_downloads` |
| 限流记录 | `security_attempts` |
| 审计日志 | `audit_logs` |

生产环境应同时备份数据库和 `storage/uploads`，并保持相同恢复时间点。`storage/downloads` 是可清理的临时目录，不应作为长期备份。

## 生产部署

仓库提供：

- `deploy/cdk.service`：带 CPU、内存、任务数、文件描述符和 systemd 沙箱限制的服务单元。
- `deploy/cdk.ambition.qzz.io.apache.conf`：适用于本机 Cloudflare Tunnel 回源的 Apache 反向代理示例。
- `deploy/README.md`：Linux 用户、虚拟环境、systemd、Apache、DNS、备份和安全验收步骤。

部署前必须：

1. 创建独立的系统用户和最小权限数据库账号。
2. 生成随机会话密钥和管理员强密码。
3. 确认 `storage`、`data` 和日志目录不可被 Web 服务器直接访问。
4. 只允许可信 CDN / Tunnel 回源，并由代理覆盖客户端提供的转发头。
5. 设置 `cookie_secure = true` 和正确的 HTTPS `public_base_url`。
6. 在重载 Apache 前运行 `apachectl configtest`。
7. 上线后检查首页、转换页、管理登录、429 限流和过期下载链接。

完整步骤见 [deploy/README.md](deploy/README.md)。

## 项目结构

```text
.
├── app/
│   ├── main.py          # FastAPI 路由、中间件、权限和测活调度
│   ├── services.py      # 导入、转换、库存、兑换和临时下载逻辑
│   ├── models.py        # SQLAlchemy 数据模型
│   ├── database.py      # 数据库引擎与存储目录
│   ├── rate_limit.py    # 持久化防爆破限制
│   ├── security.py      # 密码哈希、认证和 CDK 生成
│   ├── cliproxy.py      # sub2 监测器客户端
│   └── config.py        # 配置文件和环境变量
├── templates/           # 公开页与管理中心 Jinja2 模板
├── static/              # 样式、动画、交互脚本和站点图标
├── tests/               # API、交付、卡密策略和严格限流测试
├── deploy/              # Linux、systemd 和 Apache 示例
├── config.example.ini   # 无敏感信息的配置模板
├── requirements.txt
└── run.py
```

## 测试

测试会使用独立 SQLite 数据库和测试存储目录。运行前设置测试专用管理员配置：

```powershell
$env:TIKAWANG_SECURITY_SESSION_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:TIKAWANG_SECURITY_SUPER_ADMIN_USERNAME = "test-admin@example.invalid"
$env:TIKAWANG_SECURITY_SUPER_ADMIN_PASSWORD = python -c "import secrets; print(secrets.token_urlsafe(24))"
python -m pytest -q
```

主要覆盖范围：

- 管理登录、角色与数据范围。
- CSRF 和跨站写请求拒绝。
- JSON / CPA / SUB / ZIP 识别与转换。
- 一户一码、并发领取、兑换次数和过期策略。
- 多 CDK ZIP 交付和原文件再次兑换。
- 一次性链接撤销、过期清理与历史重建。
- 登录、兑换、查找和转换限流。
- 上传大小、账号数量、ZIP 文档数和压缩比限制。
- sub2 测活同步、状态标签和问题号隔离。

## 公开仓库安全约定

以下内容禁止提交：

- `config.ini`、`.env` 或任何生产环境变量导出。
- 数据库、账号 JSON、CDK 文本、上传和下载目录。
- 管理员账号、密码哈希、session secret、sub2 管理密钥。
- TLS 私钥、Cloudflare Tunnel 凭据、服务器 SSH 信息。
- 生产日志和包含用户信息的调试响应。

提交前可先检查：

```bash
git status --ignored
git grep -n -E 'BEGIN .*PRIVATE KEY|Authorization: Bearer|super_admin_password[[:space:]]*=[[:space:]]*[^r]'
```

如果敏感信息曾经进入 Git 历史，仅在新提交中删除并不安全；应立即轮换凭据，并使用历史重写工具清除旧对象。
