# CDK 兑换与 JSON 转换后台

正式域名：<https://cdk.ambition.qzz.io>

这是一个基于 FastAPI + PostgreSQL/SQLite 的 CDK 兑换、账号 JSON 管理与 CPA/SUB 互转服务。公开页面不要求用户登录；只有管理员可以进入后台。

> [linux-do/cdk](https://github.com/linux-do/cdk) 仅作为主页布局、动画与视觉风格参考。本项目不接入 Linux.do 登录、OAuth 或任何 Linux.do 账号体系，也没有公开用户注册/登录入口。

## 功能

- 后台支持本地批量上传或手动输入，自动识别 JSON、CPA、SUB、SUB2 与 ZIP，管理账号文件和使用状态。
- 后台使用安全随机数生成 16 字节（32 位小写十六进制）CDK；每张码独立绑定一个账号，即“一户一码”。
- 兑换支持单个 JSON、批量 ZIP，以及 CPA 或 SUB 输出；输入多个 CDK 时统一打包为 ZIP 下载。
- 独立的 `/convert` 页面支持原始 ChatGPT auth JSON → CPA/SUB，以及 CPA → SUB、SUB → CPA；转换结果通过临时链接下载。
- 所有兑换只返回 `https://cdk.ambition.qzz.io/d/...` 一次性下载地址，客户端不能切换为永久直传。
- CDK 兑换和格式转换链接下载后立即删除链接记录与临时文件；未下载的兑换链接最长保留 6 小时，格式转换链接最长保留 10 分钟。
- 前台可自动识别、去重并按行合并当前输入的 CDK；不再保存兑换码到浏览器，并会清理历史本地保存 key。
- 后台可按 CDK 反查兑换次数、北京时间、格式和文件数，并为历史兑换重新生成新的一次性下载链接。
- 兑换、转换和后台登录均有基于客户端及目标标识的防爆破限制，触发后返回 HTTP 429 和 `Retry-After`；同一客户端 1 分钟最多生成 5 次 CDK 兑换链接。

## 页面与权限

- `/`：公开 CDK 兑换页，无需登录。
- `/convert`：公开 CPA/SUB 互转页，无需登录。
- `/admin/login`：管理员登录。
- `/admin`：管理后台；仅 `super_admin` 和 `admin` 可以登录，历史 `user` 角色不能登录后台。

生产环境必须修改默认管理员密码和会话密钥。不要在公网继续使用示例密码。

### 管理员怎么登录

本地启动后打开 <http://127.0.0.1:18743/admin/login>；部署后打开 <https://cdk.ambition.qzz.io/admin/login>。公开兑换页 `/` 和格式转换页 `/convert` 也提供“管理入口”，点击后会进入同一登录页。

超级管理员账号和密码来自项目根目录 `config.ini` 的 `[security]` 配置：

```ini
[security]
super_admin_username = owner@example.com
super_admin_password = 请替换为超级管理员强密码
admin_username =
admin_password =
```

配置来源的优先级为：环境变量、`config.ini`、应用默认值。超级管理员也可通过 `TIKAWANG_SECURITY_SUPER_ADMIN_USERNAME` 和 `TIKAWANG_SECURITY_SUPER_ADMIN_PASSWORD` 设置。空数据库首次启动时会用当时生效的配置创建超级管理员；普通管理员配置留空时不会创建默认账号，可登录超级管理后台后再创建。已有账号不会因之后修改配置而自动覆盖密码。账号管理页面仅超级管理员可访问，且只能创建普通管理员。

生产上线前先复制 `config.example.ini` 为 `config.ini`，再将超级管理员密码和 `session_secret` 改为随机强值。不要把示例里的占位密码带到公网。

## 本地启动（Windows）

复制配置并修改密钥：

```powershell
Copy-Item config.example.ini config.ini
.\start-background.bat
```

重启服务：

```powershell
.\restart-background.bat
```

默认监听 `127.0.0.1:18743`。空数据库首次启动会自动建表并创建配置中指定的管理员。

## 配置

配置文件默认为项目根目录的 `config.ini`，示例见 `config.example.ini`：

```ini
[database]
url = postgresql+psycopg://cdk_user:strong-password@127.0.0.1:5432/cdk

[storage]
dir = /var/lib/cdk/storage

[server]
public_base_url = https://cdk.ambition.qzz.io

[security]
session_secret = replace-with-at-least-32-random-characters
super_admin_username = owner@example.com
super_admin_password = replace-with-a-strong-super-admin-password
admin_username =
admin_password =
trust_proxy_headers = true
```

环境变量可覆盖配置文件：

- `TIKAWANG_DATABASE_URL`
- `TIKAWANG_STORAGE_DIR`
- `TIKAWANG_SERVER_PUBLIC_BASE_URL`
- `TIKAWANG_SECURITY_SESSION_SECRET`
- `TIKAWANG_SECURITY_SUPER_ADMIN_USERNAME`
- `TIKAWANG_SECURITY_SUPER_ADMIN_PASSWORD`
- `TIKAWANG_SECURITY_ADMIN_USERNAME`
- `TIKAWANG_SECURITY_ADMIN_PASSWORD`
- `TIKAWANG_SECURITY_TRUST_PROXY_HEADERS`
- `TIKAWANG_SECURITY_COOKIE_SECURE`

只有服务确实位于可信反向代理之后时才启用 `trust_proxy_headers`，并在代理层覆盖客户端传入的 `X-Forwarded-For`，否则攻击者可能伪造来源地址绕过限流。
正式 HTTPS 域名应设置 `cookie_secure = true`；仅本地 HTTP 调试时使用 `false`。

## 上传与交付规则

- 管理员可上传 `.json`、`.cpa`、`.sub`、`.sub2` 或 `.zip`，也可手动粘贴 JSON；ZIP 内仅允许这些账号文件。
- 文件名不能包含中文；批量上传时合法文件继续导入，错误文件会被报告。
- CPA/SUB 转换单次上传上限为 50 MB；ZIP 解压后 JSON 总大小上限为 100 MB。
- 单次兑换最多打包 1000 个文件。
- CDK 必须先由管理员改为“待使用”才能兑换；每个 CDK 只能成功兑换一次，并发或重复请求都会拒绝。
- CDK 兑换链接下载后立即删除，未下载最长保留 6 小时；格式转换链接下载后立即删除，未下载最长保留 10 分钟。兑换链接过期后可在后台重新生成，不会重新分配账号。

## Linux 部署

生产部署步骤、systemd 与 Apache 反向代理说明见 [deploy/README.md](deploy/README.md)。仓库附带的 `deploy/cdk.ambition.qzz.io.apache.conf` 按正式域名配置。

## 测试

```powershell
python -m pytest -q
```

交付链路的重点回归测试位于 `tests/test_delivery_features.py`，覆盖 CPA/SUB 双向转换、正式域名一次性临时链接、过期清理与链接重建、防爆破 429，以及普通用户不能登录后台。
