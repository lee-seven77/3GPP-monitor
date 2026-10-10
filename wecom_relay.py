#!/usr/bin/env python3
"""
企业微信自建应用中转云函数（腾讯云 SCF / 阿里云 FC 通用）
========================================================

用途：GitHub Actions 调用这个云函数，再由云函数转发到企业微信自建应用。
好处：云函数 IP 固定，加到企业微信可信 IP 后就不会报 60020。

部署步骤（腾讯云 SCF，免费额度完全够）：
1. 打开 https://cloud.tencent.com/scf  → 新建函数
2. 运行环境选 Python 3.9+，函数代码选「在线编辑」
3. 把本文件内容粘贴进去
4. 在「环境变量」里配置：
     WECOM_CORP_ID   = 你的企业ID
     WECOM_AGENT_ID  = 应用AgentId
     WECOM_API_SECRET = 应用Secret
5. 「触发器」→ 新建 API 网关触发器 → 记录生成的 URL
6. 把这个 URL 填到 GitHub Secrets 的 WECOM_RELAY_URL

GitHub Actions 调用方式：
  POST <WECOM_RELAY_URL>
  Body: {"title": "...", "description": "...", "url": "..."}

云函数固定 IP → 加到企业微信「可信IP」里 → 就通了！
"""
import json
import os

import requests


def get_access_token():
    corp_id = os.environ.get('WECOM_CORP_ID', '')
    secret = os.environ.get('WECOM_API_SECRET', '')
    url = f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}'
    res = requests.get(url, timeout=10).json()
    return res.get('access_token') if res.get('errcode') == 0 else None


def send_textcard(title, description, jump_url, btntxt='查看详情'):
    token = get_access_token()
    if not token:
        return {'ok': False, 'err': '获取 token 失败'}
    agent_id = int(os.environ.get('WECOM_AGENT_ID', '0'))
    data = {
        'touser': '@all',
        'msgtype': 'textcard',
        'agentid': agent_id,
        'textcard': {
            'title': title,
            'description': description,
            'url': jump_url or 'https://www.3gpp.org/',
            'btntxt': btntxt,
        },
        'safe': 0,
    }
    res = requests.post(
        f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}',
        json=data, timeout=15).json()
    return {'ok': res.get('errcode') == 0, 'err': res.get('errmsg', '')}


def main_handler(event, context):
    """云函数入口。event 里传 title / description / url / btntxt。"""
    try:
        body = event.get('body', '{}')
        if isinstance(body, str):
            body = json.loads(body)
    except Exception:
        body = {}

    # 兼容直接传 JSON 的调用方式
    if not body and isinstance(event, dict):
        body = event

    title = body.get('title', '3GPP 监控通知')
    description = body.get('description', '有新消息')
    jump_url = body.get('url', 'https://www.3gpp.org/')
    btntxt = body.get('btntxt', '查看详情')

    result = send_textcard(title, description, jump_url, btntxt)
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(result, ensure_ascii=False),
    }


# ── 本地测试 ──
if __name__ == '__main__':
    # 先配置好环境变量再跑
    os.environ.setdefault('WECOM_CORP_ID', '')
    os.environ.setdefault('WECOM_AGENT_ID', '')
    os.environ.setdefault('WECOM_API_SECRET', '')
    ret = main_handler({
        'body': json.dumps({
            'title': '✅ 3GPP 中转测试',
            'description': '<div class="gray">云函数中转通道测试</div><div class="normal">如果你看到这条，说明配置成功！</div>',
            'url': 'https://www.3gpp.org/',
            'btntxt': '访问3GPP官网',
        })
    }, None)
    print(ret)
