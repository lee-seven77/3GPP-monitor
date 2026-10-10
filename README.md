# 🏨 3GPP 会议酒店监控 —— GitHub Actions 版

不用开电脑、不用装任何东西、**完全免费**。GitHub Actions 每 15 分钟跑一次脚本盯 3GPP 网站，发现会议邀请就推送到你**手机微信**。

```
GitHub Actions（大脑+眼睛）  →  每 15 分钟扫一次 3GPP 邮件列表
        ↓ 发现 "Meeting invitation"
   下载 .doc 附件 → 提取酒店预订链接
        ↓
企业微信 / 飞书（快递员）  →  推送到微信
        ↓
点消息 → 直达订房平台 🔥
```

---

## 推送方式（三选一，也可组合）

| 方式 | 费用 | 配置难度 | 消息效果 | 同事收消息 |
|------|:---:|:---:|---------|-----------|
| **群机器人**（推荐） | 免费 | ⭐ 1分钟 | markdown 文字 | 群里都看得到 |
| **自建应用** | 免费 | ⭐⭐⭐ 10分钟 | textcard 卡片 | 扫码即收，不用装App |
| **飞书机器人** | 免费 | ⭐ 1分钟 | 卡片消息 | 群里都看得到 |

---

## 方式一：企业微信群机器人（最简单）

### 1. 创建群机器人
1. 企业微信建一个群（或用已有群）
2. 群设置 → **群机器人** → **添加机器人**
3. 起个名字（如 `3GPP监控助手`）→ 复制 **webhook URL**

### 2. 配置 GitHub Secrets
仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Name | Value |
|---|---|
| `WECOM_WEBHOOK` | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` |

### 3. 开跑
Actions → **Run workflow** → **Run** → 看日志 `✅ 群机器人已推送`

---

## 方式二：企业微信自建应用（卡片效果+同事扫码收）

> 适合团队使用，同事不用装企业微信 App，在普通微信里收卡片。

### 1. 注册企业微信（个人免费，无需营业执照）
1. 打开 [企业微信官网](https://work.weixin.qq.com/)，点 **「立即注册」**
2. 企业名称随便填（如 "3GPP科研小分队"），类型选"其他"，人数选"1-50人"
3. 用微信扫码绑定为管理员

### 2. 创建自建应用
1. 后台 → **「应用管理」** → **「自建」** → **「创建应用」**
2. 应用名称：`3GPP 监控助手`，可见范围选你自己和同事
3. 点 **「创建应用」**

### 3. 记录 3 个关键参数

| 参数 | 在哪里找 |
|------|---------|
| **AgentId** | 应用详情页（数字） |
| **Secret** | 应用详情页，点「发送」在手机企业微信上查看 |
| **CorpId** | 「我的企业」→ 页面最底部（`ww` 开头） |

### 4. 开启「微信直接接收通知」
1. 「我的企业」→ **「微信插件」**
2. 用**普通微信扫码**关注
3. 确保「允许成员在微信中接收消息」**已开启**
4. **把二维码发给同事** → 同事普通微信扫码即可收消息

### 5. 解决 IP 白名单问题（关键！）

自建应用有 IP 白名单，GitHub Actions 的 IP 不固定会报 `60020`。解决方案：

**方案 A：用云函数中转（推荐）**
1. 开通 [腾讯云 SCF](https://cloud.tencent.com/scf)（免费额度够用）
2. 新建 Python 函数，粘贴 `wecom_relay.py` 的内容
3. 环境变量配 3 个参数（CorpId / AgentId / Secret）
4. 创建 **API 网关触发器** → 拿到 URL
5. 把云函数出站 IP 加到企业微信「可信IP」
6. GitHub Secrets 加 `WECOM_RELAY_URL` = 云函数 URL

**方案 B：本地跑（不走 GitHub）**
```bash
WECOM_CORP_ID=ww... WECOM_AGENT_ID=1000002 WECOM_API_SECRET=xxx python monitor.py --once
```

### 6. 配置 GitHub Secrets

| Name | Value |
|---|---|
| `WECOM_CORP_ID` | 企业ID（`ww` 开头） |
| `WECOM_AGENT_ID` | 应用 AgentId（数字） |
| `WECOM_API_SECRET` | 应用 Secret |
| `WECOM_RELAY_URL` | 云函数 URL（方案A需要） |

---

## 方式三：飞书机器人

1. 飞书群 → 设置 → **群机器人** → **添加机器人** → **自定义机器人**
2. 复制 webhook URL
3. GitHub Secrets 加 `FEISHU_WEBHOOK` = webhook URL

> ⚠️ 注意保管好 webhook URL，不要公开发布。GitHub Secrets 是加密存储的，安全。

---

## 组合使用

可以同时配多种推送方式，都配置就都推：

```yaml
# GitHub Secrets 里同时配：
WECOM_WEBHOOK:   https://qyapi.weixin.qq.com/...   # 群机器人
WECOM_RELAY_URL: https://service-xxx.tencentcs.com/...  # 自建应用（中转）
FEISHU_WEBHOOK:  https://open.feishu.cn/...        # 飞书
```

---

## 验证配置

Actions → **Run workflow** → 勾选 **「发送测试卡片验证推送通道」** → **Run**

| 日志 | 含义 |
|------|------|
| `✅ 群机器人已推送` | 成功 |
| `✅ 自建应用(中转)已推送` | 成功 |
| `✅ 自建应用卡片已推送` | 成功（直连模式） |
| `✅ 飞书已推送` | 成功 |
| `跳过xxx（未配置）` | 对应 Secrets 没填 |
| `❌ ... 60020` | 自建应用 IP 白名单问题，用中转模式 |

---

## 常见问题

**Q：多久查一次？能改吗？**
A：默认监控**全部 14 个列表**（RAN1-6 / SA1-6 / RAN 全会 / SA 全会）。每 15 分钟一次。改 `.github/workflows/monitor.yml` 里的 `cron`。注意 GitHub Actions 是 **UTC 时间**。

**Q：会不会重复推送？**
A：不会。已通知的记录存在 `seen.json`（按标题去重）并由 Actions 自动提交回仓库。

**Q：安全吗？**
A：密钥在 GitHub Secrets（加密存储，日志和代码里看不到）。飞书/企业微信 webhook URL 注意不要公开发布即可。

**Q：手机要装企业微信吗？**
A：不用！扫码关注「微信插件」后，直接在普通微信里收消息。

**Q：其他人也能收到吗？**
A：可以。自建应用：把同事加到「可见范围」，他们扫微信插件二维码即可。群机器人：群里所有人可见。

**Q：同事不想装企业微信？**
A：不用装。扫「微信插件」二维码后，消息直接出现在普通微信聊天列表里。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `monitor.py` | 监控脚本（扫描 + 提取订房链接 + 多通道推送） |
| `wecom_relay.py` | 企业微信自建应用云函数中转（可选部署） |
| `.github/workflows/monitor.yml` | GitHub Actions 定时任务 |
| `test_wecom.py` | 本地测试企业微信推送连通性 |
| `requirements.txt` | 依赖（只要 requests） |
| `seen.json` | 已通知记录（Actions 自动提交回仓库） |
