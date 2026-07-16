# cdk.ambition.qzz.io 部署说明

以下示例以 Debian/Ubuntu、项目目录 `/opt/cdk`、运行账号 `cdk`、本机监听端口 `18743` 为准。正式访问地址固定为 <https://cdk.ambition.qzz.io>。

## 1. 系统与应用

```bash
sudo useradd --system --create-home --home-dir /opt/cdk --shell /usr/sbin/nologin cdk
sudo -u cdk python3 -m venv /opt/cdk/.venv
sudo -u cdk /opt/cdk/.venv/bin/pip install -r /opt/cdk/requirements.txt
sudo install -d -o cdk -g cdk -m 0750 /var/lib/cdk/storage
sudo -u cdk cp /opt/cdk/config.example.ini /opt/cdk/config.ini
```

编辑 `/opt/cdk/config.ini`：

- 数据库建议使用 PostgreSQL，并创建独立的最小权限数据库账号。
- `storage.dir` 使用可持久化、仅服务账号可写的绝对路径。
- `server.public_base_url` 必须是 `https://cdk.ambition.qzz.io`，否则 API 返回的临时下载链接域名会错误。
- 更换 `session_secret` 和 `super_admin_password`；密钥不要提交到仓库。
- 保持 `cookie_secure = true`，让管理员会话 Cookie 仅通过 HTTPS 发送。
- 仅当反向代理可信并会覆盖转发头时设置 `trust_proxy_headers = true`。

## 2. systemd

创建 `/etc/systemd/system/cdk.service`：

```ini
[Unit]
Description=CDK redemption service
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=cdk
Group=cdk
WorkingDirectory=/opt/cdk
Environment=TIKAWANG_CONFIG_FILE=/opt/cdk/config.ini
ExecStart=/opt/cdk/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18743 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=3
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/cdk /opt/cdk/data

[Install]
WantedBy=multi-user.target
```

如果数据库不是 SQLite，可以从 `ReadWritePaths` 移除 `/opt/cdk/data`。随后启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cdk
sudo systemctl status cdk
```

## 3. Apache 与 HTTPS

启用反向代理模块并安装仓库配置：

```bash
sudo a2enmod proxy proxy_http headers remoteip
sudo cp /opt/cdk/deploy/cdk.ambition.qzz.io.apache.conf /etc/apache2/sites-available/cdk.ambition.qzz.io.conf
sudo a2ensite cdk.ambition.qzz.io.conf
sudo apachectl configtest
sudo systemctl reload apache2
```

仓库配置适合 HTTPS 已在可信 CDN/上游代理终止、Apache 接收回源 HTTP 的场景。如果 TLS 直接在 Apache 终止，请用 Certbot 或等效工具创建 `*:443` 虚拟主机，并保留相同的 `ProxyPass`、`ProxyPassReverse` 和转发头设置；`*:80` 只做 HTTPS 跳转。

配置会先删除请求中原有的 `X-Forwarded-For`，再用 Apache 看到的 `REMOTE_ADDR` 覆盖它，避免攻击者伪造来源地址绕过登录和兑换限流。若 Apache 前还有 CDN，必须仅允许 CDN 回源，并用 `mod_remoteip` 的可信代理网段白名单把 `REMOTE_ADDR` 还原为真实客户端地址；不要直接信任任意来源传入的 `X-Forwarded-For`。

DNS 中将 `cdk.ambition.qzz.io` 指向部署服务器或 CDN。上线后检查：

```bash
curl -I https://cdk.ambition.qzz.io/
curl -I https://cdk.ambition.qzz.io/convert
curl -I https://cdk.ambition.qzz.io/admin/login
```

## 4. 数据、清理与备份

- 备份 PostgreSQL 数据库和 `storage.dir`；两者需要保持同一恢复时间点。
- 兑换、查找和后台重建链接未下载时最长保留 24 小时，格式转换链接最长保留 10 分钟；首次下载后立即撤销并清理临时文件。应用启动时和请求处理中每 10 分钟执行过期清理。
- 不要把 `storage`、`data`、`config.ini` 或日志目录暴露成 Apache 静态目录。
- 反向代理应按路径限制请求体大小，并与应用的 25 MB 管理上传、20 MB 格式转换和 2 MB 手动输入上限保持一致或略高。

## 5. 安全验收

- 公开兑换、查找和格式转换页面无需登录，站点没有公开注册入口。
- `/admin` 未登录时跳转到 `/admin/login`，普通用户角色不能登录。
- 连续错误兑换或登录会返回 HTTP 429，并带 `Retry-After`。
- API 返回的临时链接以 `https://cdk.ambition.qzz.io/d/` 开头，过期后返回 HTTP 410。
