# GPT发卡网 · CDK 兑换与账号交付

基于 FastAPI 的卡密（CDK）兑换与账号交付系统：前台兑换 / 反查 / 转换，后台管理商品、库存、卡密与管理员。原生 JS + 手写 CSS，无需构建。

## 前台功能（公开）

- **CDK 兑换** `/`：输入一个或多个 CDK，选择 **CPA**（原始账号 JSON）或 **SUB**（sub2api 配置）输出；单次 ≤100 文件、≤25MB，返回 6 小时有效的一次性下载链接。
- **原文件反查** `/lookup`：输入单个已兑换 CDK，按原格式重建交付文件并重新生成下载链接。
- **格式转换** `/convert`：上传账号 JSON（`.json/.cpa/.sub/.sub2`），在 CPA 与 SUB 间互转；单次 ≤20MB、≤500 账号，链接 10 分钟有效。

## 后台功能（`/admin`，需登录）

- **运行概览**：库存、可用 CDK、今日兑换、有效链接、累计上传/交付等指标与审计日志。
- **商品管理** `/admin/products`：SKU 唯一，状态 草稿/上架/隐藏，低库存阈值。
- **账号库存** `/admin/files`：上传、作废、删除、下载；文件加密落盘，已售或被引用的文件不可删。
- **批量导入** `/admin/uploads`：批量导入账号 JSON / 压缩包（含防 zip 炸弹限制）。
- **CDK 管理** `/admin/cards`：生成 CDK（绑定商品、每码文件数、**可兑换次数 1–100**、可选过期时间），支持追加次数、禁用、作废、删除、导出 TXT、重发下载链接。CDK 为 32 位小写十六进制。
- **管理员管理** `/admin/users`（超管）：新建/停用管理员、重置密码、配置号池。

## 安全

CSRF + 同源校验、CSP 与安全响应头、strict 会话（8h）、登录/兑换限速（429 + Retry-After）、一次性下载（下载即删、过期返回 410）、定时清理过期数据。

## 快速开始（Windows）

1. 复制 `config.example.ini` 为 `config.ini`，设置 `session_secret`（≥32 位随机串）与超级管理员账号密码。
2. 安装依赖并启动（工作目录须为项目根目录）：
   ```bash
   pip install -r requirements.txt
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
3. 前台 `http://127.0.0.1:8000`，后台 `/admin/login`；超级管理员按 `config.ini` 配置在启动时自动创建。

配置优先级：环境变量（`TIKAWANG_` 前缀）> `config.ini` > 默认值。生产部署见 [`deploy/README.md`](deploy/README.md)。

## 测试

```bash
python -m pytest -q
```

## 友情链接

- [Linux.do](https://linux.do/)
