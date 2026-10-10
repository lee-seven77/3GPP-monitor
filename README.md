# 🏨 3GPP 会议酒店监控 —— GitHub Actions 版

不用开电脑、不用装任何东西、**完全免费**。GitHub Actions 每 15 分钟跑一次脚本盯 3GPP 网站，发现会议邀请就推到你**手机微信**。

```
GitHub Actions（大脑+眼睛）  →  每 15 分钟扫一次 3GPP 邮件列表
        ↓ 发现 "Meeting invitation"
   下载 .doc 附件 → 提取酒店预订链接
        ↓
WxPusher（快递员）  →  通过微信公众号推到你手机
        ↓
点通知 → 直达订房平台 🔥
```

---

## 一、WxPusher 拿「钥匙」（约 5 分钟，免费）

1. 电脑打开 <https://wxpusher.zjiecode.com/>，**微信扫码登录**
2. 左侧点 **创建应用**：名字随便填（如 `3GPP监控助手`），关注提示语随便填
3. 创建成功后弹出 **AppToken**（`AT_` 开头）→ **复制保存**（关掉就看不到了）
4. 手机扫网页上的 **应用关注二维码**
5. 关注后，在 **用户管理 → 用户列表** 里看到你的微信，后面有一串 **UID**（`UID_` 开头）→ **复制保存**

## 二、在 GitHub 存「钥匙」（不写进代码）

> ⚠️ 千万别把 AppToken / UID 直接写在代码里。放 GitHub Secrets 才安全。

1. 把本文件夹（`3GPP云端监控`）内容**上传为一个 GitHub 仓库**
2. 仓库 → **Settings** → **Secrets and variables** → **Actions**
3. 点 **New repository secret**，加这两个：

| Name | Value |
|---|---|
| `WXPUSHER_APP_TOKEN` | 你的 `AT_xxxx...` |
| `WXPUSHER_UID` | 你的 `UID_xxxx...` |

**（可选）飞书推送**：再加一个 `FEISHU_WEBHOOK` = 你的飞书机器人 webhook
（不填就只推微信；建议飞书群机器人配「IP 白名单」）

**（可选）改监控范围**：`Settings → Secrets and variables → Actions → Variables` 加 `GROUPS`，如 `RAN3`（默认 `RAN(全部),SA(全部)`）

## 三、开跑

推上去之后它就自动跑了。想立刻看效果：

仓库 → **Actions** → 左侧选 **3GPP 会议酒店监控** → 右侧 **Run workflow** → **Run**

日志里会看到：

```
 监控列表 : RAN(全部), SA(全部)
 微信推送 : 已配置
[扫描] RAN(全部): 命中 1 封，新增 1 封
[新] Meeting invitation form for 3GPP TSGs#115 March 2027 in Rotterdam
     → ✅ 微信已推送 (WxPusher)
     → 订房: https://...
```

手机微信上「WxPusher」公众号会弹出消息，点进去有**「🔥 点此立即预订酒店」**链接。

## 四、验证配置（可选）

在仓库 Actions 页面手动 Run 一次，看日志末尾：
- `✅ 微信已推送 (WxPusher)` = 成功
- `跳过微信（未配置 WXPUSHER_*）` = Secrets 没填对
- `❌ 微信推送失败: ...` = AppToken 或 UID 不对

---

## 常见问题

**Q：多久查一次？能改吗？**
A：默认监控**全部 14 个列表**（RAN1-6 / SA1-6 / RAN 全会 / SA 全会）—— 实测只盯全会列表会漏邀请（RAN3 的邀请不在全会列表里）。可用 `GROUPS` 变量缩窄。每 15 分钟一次。改 `.github/workflows/monitor.yml` 里的 `cron`。注意 GitHub Actions 是 **UTC 时间**，而且高峰期会延迟几分钟。

**Q：会不会重复推送？**
A：不会。已通知的记录存在 `seen.json`（按**标题**去重，同一封邀请被转到多个列表也只推一次） 并由 Actions 自动提交回仓库，下次跳过。

**Q：会不会一堆 commit？**
A：不会。只有**状态变了**（发现新邀请）才提交，平时静默。

**Q：安全吗？**
A：密钥在 GitHub Secrets，日志和代码里都看不到。GitHub Actions 跑在微软的机器上，不涉及你的公司内网；只是访问公网的 3GPP 邮件列表和 WxPusher。

**Q：为什么手机要装东西？**
A：不用装。只用微信扫码关注 WxPusher 的公众号就行。

**Q：会议是几个月后的，还会提醒吗？**
A：会。只提醒**还没过期**的会议（按标题里的月份判断），所以看到的就是还能订房的。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `monitor.py` | 监控脚本（自包含，含扫描 + 提取订房链接 + 推送） |
| `.github/workflows/monitor.yml` | GitHub Actions 定时任务 |
| `requirements.txt` | 依赖（只要 requests） |
| `seen.json` | 已通知记录（Actions 自动提交回仓库；按标题去重，同一邀请多个列表只推一次） |
