#!/usr/bin/env python3
"""
企业微信自建应用推送连通性测试。
用法：
  python test_wecom.py
然后按提示填入 3 个参数，成功后微信会收到一张测试卡片。
"""
import requests

# ==================== 填入你的配置信息 ====================
CORP_ID = ""       # 企业ID（ww 开头，在「我的企业」最下方）
AGENT_ID = 0       # 应用 AgentId（数字，在应用详情页）
API_SECRET = ""    # 应用 Secret（在应用详情页）
# =========================================================

def get_access_token(corp_id, secret):
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}"
    try:
        res = requests.get(url, timeout=10).json()
        if res.get("errcode") == 0:
            print(f"✅ Access Token 获取成功")
            return res.get("access_token")
        else:
            print(f"❌ 获取 Access Token 失败: {res}")
            return None
    except Exception as e:
        print(f"❌ 请求出错: {e}")
        return None

def send_test_card(token, agent_id):
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    data = {
        "touser": "@all",
        "msgtype": "textcard",
        "agentid": agent_id,
        "textcard": {
            "title": "✅ 3GPP 监控助手 — 连通性测试",
            "description": (
                '<div class="gray">这是一条测试消息</div>\n\n'
                '<div class="normal"><b>状态：</b>推送通道正常！</div>\n'
                '<div class="normal"><b>说明：</b>后续发现会议邀请会自动推送卡片</div>\n'
                '<div class="highlight">🎉 恭喜，配置成功！</div>'
            ),
            "url": "https://www.3gpp.org/",
            "btntxt": "访问 3GPP 官网",
        },
        "safe": 0,
    }
    try:
        res = requests.post(url, json=data, timeout=15).json()
        if res.get("errcode") == 0:
            print("🚀 测试卡片发送成功！请检查微信聊天列表！")
        else:
            print(f"❌ 发送失败: {res}")
    except Exception as e:
        print(f"❌ 请求出错: {e}")

if __name__ == "__main__":
    if not CORP_ID or not API_SECRET or not AGENT_ID:
        print("请先在脚本顶部填入 CORP_ID、AGENT_ID、API_SECRET")
        print()
        print("获取方式：")
        print("  CORP_ID   → 企业微信后台「我的企业」页面最底部")
        print("  AGENT_ID  → 「应用管理」→ 你的应用详情页")
        print("  API_SECRET → 同上，点「发送」在手机上查看")
    else:
        print(f"CORP_ID:   {CORP_ID}")
        print(f"AGENT_ID:  {AGENT_ID}")
        print(f"API_SECRET: {API_SECRET[:6]}...")
        print()
        token = get_access_token(CORP_ID, API_SECRET)
        if token:
            send_test_card(token, AGENT_ID)
