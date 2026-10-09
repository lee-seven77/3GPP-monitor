#!/usr/bin/env python3
"""
3GPP 会议酒店监控 —— GitHub Actions 版
====================================

GitHub Actions 当「大脑和眼睛」（定时跑），WxPusher / 飞书当「信使」（推到微信）。
流程：定时启动 → 扫 3GPP 邮件列表标题 → 发现 "Meeting invitation"
     → 下载 .doc 提取订房链接 → 微信/飞书推送 → 记录已通知（提交回仓库）

配置全部走环境变量 / GitHub Secrets，**代码里不写任何密钥**：
  WXPUSHER_APP_TOKEN   WxPusher 的 AppToken（AT_ 开头）
  WXPUSHER_UID         WxPusher 的 UID（UID_ 开头）
  FEISHU_WEBHOOK       飞书机器人 webhook（可选，不填就不推飞书）
  GROUPS               监控哪些组，默认 RAN(全部),SA(全部)

本地也能跑：
  FEISHU_WEBHOOK=https://... python monitor.py --once
"""

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
import urllib3

warnings.filterwarnings('ignore', message='Unverified HTTPS request')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
}

WAM = 'https://list.etsi.org/scripts/wa.exe'
INVITE_RE = re.compile(r'meeting\s*invitation', re.I)

MAIL_LISTS = {
    'RAN1': '3GPP_TSG_RAN_WG1', 'RAN2': '3GPP_TSG_RAN_WG2',
    'RAN3': '3GPP_TSG_RAN_WG3', 'RAN4': '3GPP_TSG_RAN_WG4',
    'RAN5': '3GPP_TSG_RAN_WG5', 'RAN6': '3GPP_TSG_RAN_WG6',
    'SA1': '3GPP_TSG_SA_WG1', 'SA2': '3GPP_TSG_SA_WG2',
    'SA3': '3GPP_TSG_SA_WG3', 'SA4': '3GPP_TSG_SA_WG4',
    'SA5': '3GPP_TSG_SA_WG5', 'SA6': '3GPP_TSG_SA_WG6',
    'RAN(全部)': '3GPP_TSG_RAN', 'SA(全部)': '3GPP_TSG_SA',
}

MONTHS = ('January|February|March|April|May|June|July|'
          'August|September|October|November|December')
_MONTH_NUM = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6,
    'jul': 7, 'july': 7, 'aug': 8, 'august': 8, 'sep': 9, 'sept': 9,
    'september': 9, 'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}
_HLINK_FIELD = re.compile(rb'HYPERLINK\s+"([^"]+)"')

STATE_FILE = Path(__file__).parent / 'seen.json'


# ══════════════════════════════════════════════════════════
#  网络
# ══════════════════════════════════════════════════════════

def http_get(url, timeout=40, retries=3):
    resp = None
    for i in range(max(1, retries)):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
            if resp.status_code == 200:
                return resp
            if resp.status_code not in (429, 500, 502, 503, 504):
                return resp
        except Exception:
            resp = None
        if i < retries - 1:
            time.sleep(1.5 * (i + 1))
    return resp


# ══════════════════════════════════════════════════════════
#  扫描邮件列表（只读目录页标题，不打开每封邮件）
# ══════════════════════════════════════════════════════════

def resolve(name):
    return MAIL_LISTS.get(name, name)


_TR_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.I | re.S)
_A2_RE = re.compile(r'href="([^"]*A2=[^"]*)"[^>]*>(.*?)</a>', re.I | re.S)
_CELL_RE = re.compile(r'<div class="archive forcewrap">(.*?)</div>', re.I | re.S)


def _strip(s):
    return html_mod.unescape(re.sub(r'<[^>]+>', '', s)).strip()


def fetch_volumes(list_name):
    """从存档目录页拿到所有月份卷（最新在前）。"""
    key = resolve(list_name)
    resp = http_get(f'{WAM}?A0={key}', retries=2)
    if resp is None or resp.status_code != 200:
        return []
    out = []
    for m in re.finditer(
            rf'A1=(ind[0-9A-Za-z]+)(?:&amp;|&)L={re.escape(key)}"[^>]*>([^<]+)<',
            resp.text):
        out.append((html_mod.unescape(m.group(2)).strip(),
                    f'{WAM}?A1={m.group(1)}&L={key}'))
    return out


def fetch_volume_mails(vol_url):
    resp = http_get(vol_url, retries=2)
    if resp is None or resp.status_code != 200:
        return []
    mails = []
    for tr in _TR_RE.finditer(resp.text):
        row = tr.group(1)
        am = _A2_RE.search(row)
        if not am:
            continue
        href = am.group(1)
        mails.append({
            'subject': _strip(am.group(2)),
            'url': href if href.startswith('http') else f'https://list.etsi.org{href}',
        })
    return mails


def parse_meeting_time(subject):
    m = re.search(r'\b([A-Za-z]{3,9})\.?\s+(\d{4})\b', subject)
    if not m:
        return None
    mon = _MONTH_NUM.get(m.group(1).lower().rstrip('.'))
    return (int(m.group(2)), mon) if mon else None


def is_past_meeting(subject, now=None):
    mt = parse_meeting_time(subject)
    if not mt:
        return False
    now = now or (datetime.now().year, datetime.now().month)
    return mt < now


def scan_invitations(list_name, max_volumes=8, include_past=False, log=print):
    """并行扫月份卷，返回会议邀请列表（只读标题）。"""
    vols = fetch_volumes(list_name)
    if not vols:
        log(f'[扫描] {list_name}: 无法读取存档目录')
        return []
    now = datetime.now()
    cutoff = (now.year - 1, now.month)
    todo = []
    for label, url in vols:
        if len(todo) >= max_volumes:
            break
        vm = re.match(r'([A-Z][a-z]+)\s+(\d{4})', label)
        if vm:
            t = (int(vm.group(2)), _MONTH_NUM.get(vm.group(1).lower(), 0))
            if t < cutoff:
                break
        todo.append(url)

    found, seen_urls = [], set()
    if not todo:
        return []

    def consume(mails):
        for m in mails:
            if m['url'] in seen_urls:
                continue
            if not INVITE_RE.search(m['subject']):
                continue
            if not include_past and is_past_meeting(m['subject'], (now.year, now.month)):
                continue
            seen_urls.add(m['url'])
            found.append(m)

    for bs in range(0, len(todo), 8):
        batch = todo[bs:bs + 8]
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            for fut in as_completed([ex.submit(fetch_volume_mails, u) for u in batch]):
                try:
                    consume(fut.result() or [])
                except Exception:
                    pass
    return found


# ══════════════════════════════════════════════════════════
#  从邮件附件 .doc 提取酒店预订链接
# ══════════════════════════════════════════════════════════

def find_doc_attachment(msg_html):
    for href in re.findall(r'href="([^"]+)"', msg_html):
        u = html_mod.unescape(href)
        if 'A3=' in u and re.search(r'msword|\.docx?', u, re.I):
            return u if u.startswith('http') else f'https://list.etsi.org{u}'
    return None


def extract_hotel_info(data):
    """从 .doc 二进制提取超链接，识别订房/班车平台。"""
    links = []
    for m in _HLINK_FIELD.finditer(data):
        url = m.group(1).decode('ascii', 'ignore')
        tail = data[m.end():m.end() + 240]
        lm = re.match(rb'[\x00-\x1f\x7f-\xff]{0,10}([A-Za-z0-9][ -~]{2,110})', tail)
        label = ''
        if lm:
            seg = lm.group(1).decode('ascii', 'ignore')
            label = re.split(r'[.]{1,}|\x00', seg)[0].strip()
        links.append((label.lower(), url))

    hotel = coach = ''
    for low, url in links:
        if not hotel and 'hotel' in low and ('book' in low or 'platform' in low):
            hotel = url
        elif not coach and ('coach' in low or 'shuttle' in low or 'bus' in low) \
                and ('book' in low or 'platform' in low):
            coach = url
    if not hotel:
        for kw in (b'hotel booking platform', b'hotel booking', b'booking platform'):
            pos = data.find(kw)
            if pos > 0:
                ms = list(_HLINK_FIELD.finditer(data[max(0, pos - 600):pos]))
                if ms:
                    hotel = ms[-1].group(1).decode('ascii', 'ignore')
                    break
    return hotel, coach


def extract_meeting_info(msg_html, doc_data):
    """从邮件页 + .doc 里提取会议信息。"""
    info = {'subject': '', 'meeting': '', 'city': '', 'dates': '',
            'venue': '', 'hotel_url': '', 'coach_url': ''}
    sm = re.search(r'Subject:\s*</b>\s*</td>\s*<td[^>]*>(.*?)</td>', msg_html, re.I | re.S)
    if sm:
        info['subject'] = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', sm.group(1))).strip()

    mm = re.search(r'for\s+(.+?)\s+in\s+([A-Za-z][A-Za-z \-]{2,40})\s*$', info['subject'], re.I)
    if mm:
        info['meeting'], info['city'] = mm.group(1).strip(), mm.group(2).strip()
    else:
        info['meeting'] = info['subject']

    if doc_data:
        plain = ' '.join(re.sub(rb'[^\x20-\x7e]', b' ', doc_data).decode('ascii', 'ignore').split())
        dm = re.search(rf'(\d{{1,2}}\s*[-–]\s*\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})', plain, re.I)
        if dm:
            info['dates'] = dm.group(1)
        vm = re.search(r'(MECC[^.]{0,50}|conference cent(?:re|er))', plain, re.I)
        if vm:
            info['venue'] = vm.group(1).strip()
        info['hotel_url'], info['coach_url'] = extract_hotel_info(doc_data)
    return info


def build_invite(msg_url, log=print):
    """打开邮件 → 下载 .doc → 提取酒店信息。"""
    resp = http_get(msg_url, retries=2)
    if resp is None:
        return {'subject': '', 'msg_url': msg_url}
    doc_data = b''
    att = find_doc_attachment(resp.text)
    if att:
        dr = http_get(att, timeout=60, retries=2)
        if dr is not None and dr.status_code == 200:
            doc_data = dr.content
    info = extract_meeting_info(resp.text, doc_data)
    info['msg_url'] = msg_url
    return info


# ══════════════════════════════════════════════════════════
#  通知：WxPusher（微信）+ 飞书
# ══════════════════════════════════════════════════════════

def send_wechat(info):
    """WxPusher 推送到微信。"""
    app_token = os.environ.get('WXPUSHER_APP_TOKEN', '').strip()
    uid = os.environ.get('WXPUSHER_UID', '').strip()
    if not app_token or not uid:
        return '跳过微信（未配置 WXPUSHER_*）'

    title = f"🏨 会议邀请来了！{info.get('city') or '3GPP'}"
    rows = [f"<b>{info.get('meeting') or info.get('subject') or '3GPP 会议邀请'}</b>"]
    for k, label in (('city', '📍 地点'), ('dates', '📅 日期'), ('venue', '🏛 场地')):
        if info.get(k):
            rows.append(f"{label}: {info[k]}")
    if info.get('hotel_url'):
        rows.append(f"<a href=\"{info['hotel_url']}\">🔥 点此立即预订酒店</a>")
    if info.get('coach_url'):
        rows.append(f"<a href=\"{info['coach_url']}\">🚌 班车预订</a>")
    if info.get('msg_url'):
        rows.append(f"<a href=\"{info['msg_url']}\">📧 打开原邮件</a>")
    content = '<br/>'.join(rows)

    payload = {
        'appToken': app_token,
        'content': content,
        'summary': title,
        'contentType': 2,          # 2 = HTML，可以放超链接
        'uids': [uid],
    }
    try:
        r = requests.post('https://wxpusher.zjiecode.com/api/send/message',
                          json=payload, timeout=15)
        res = r.json()
        if res.get('code') == 1000:
            return '✅ 微信已推送 (WxPusher)'
        return f"❌ 微信推送失败: {res.get('msg')}"
    except Exception as e:
        return f'❌ 微信推送异常: {e}'


def send_feishu(info):
    """飞书自定义机器人推送（可选）。"""
    webhook = os.environ.get('FEISHU_WEBHOOK', '').strip()
    if not webhook:
        return '跳过飞书（未配置 FEISHU_WEBHOOK）'
    rows = [f"**🎟 会议:** {info.get('meeting') or info.get('subject') or '3GPP 会议邀请'}"]
    for k, label in (('city', '📍 地点'), ('dates', '📅 日期'),
                     ('venue', '🏛 场地')):
        if info.get(k):
            rows.append(f'**{label}:** {info[k]}')
    if info.get('hotel_url'):
        rows.append('**🏨 酒店:** 已按优惠价预锁定，请尽快下单')
    actions = []
    if info.get('hotel_url'):
        actions.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': '🔥 立即预订酒店'},
                        'type': 'primary', 'url': info['hotel_url']})
    if info.get('msg_url'):
        actions.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': '📧 打开原邮件'},
                        'type': 'default', 'url': info['msg_url']})
    elements = [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': '\n'.join(rows)}}]
    if actions:
        elements.append({'tag': 'action', 'actions': actions})
    card = {'msg_type': 'interactive', 'card': {
        'header': {'title': {'tag': 'plain_text',
                             'content': f"🏨 3GPP 会议邀请来了！{info.get('city') or ''}".strip()},
                   'template': 'orange'},
        'elements': elements}}
    try:
        r = requests.post(webhook, json=card, timeout=15)
        return '✅ 飞书已推送' if r.status_code == 200 else f'❌ 飞书返回 {r.status_code}'
    except Exception as e:
        return f'❌ 飞书异常: {e}'


# ══════════════════════════════════════════════════════════
#  状态（避免重复通知）—— 存在 seen.json，由 workflow 提交回仓库
# ══════════════════════════════════════════════════════════

def load_seen():
    try:
        return set(json.loads(STATE_FILE.read_text(encoding='utf-8')))
    except Exception:
        return set()


def save_seen(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=1),
                          encoding='utf-8')


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def run(groups=None, max_volumes=8):
    groups = groups or [g.strip() for g in
                        os.environ.get('GROUPS', 'RAN(全部),SA(全部)').split(',') if g.strip()]
    seen = load_seen()
    print('=' * 58)
    print(' 3GPP 会议酒店监控 (GitHub Actions)')
    print('=' * 58)
    print(f' 监控列表 : {", ".join(groups)}')
    print(f' 微信推送 : {"已配置" if os.environ.get("WXPUSHER_APP_TOKEN") else "未配置"}')
    print(f' 飞书推送 : {"已配置" if os.environ.get("FEISHU_WEBHOOK") else "未配置"}')
    print(f' 已记录   : {len(seen)} 封')
    print(f' 运行时间 : {datetime.now():%Y-%m-%d %H:%M:%S}')
    print('=' * 58, flush=True)

    new_count = 0
    for g in groups:
        invites = scan_invitations(g, max_volumes=max_volumes, log=print)
        fresh = [m for m in invites if m['url'] not in seen]
        print(f'[扫描] {g}: 命中 {len(invites)} 封，新增 {len(fresh)} 封')
        for m in fresh:
            print(f'[新] {m["subject"][:70]}')
            info = build_invite(m['url'], log=print)
            print(f"     → {send_wechat(info)}")
            print(f"     → {send_feishu(info)}")
            if info.get('hotel_url'):
                print(f"     → 订房: {info['hotel_url']}")
            seen.add(m['url'])
            new_count += 1
    save_seen(seen)
    print(f'\n[完成] 本轮新增 {new_count} 封，累计 {len(seen)} 封', flush=True)
    return new_count


def main():
    ap = argparse.ArgumentParser(description='3GPP 会议酒店监控 (GitHub Actions 版)')
    ap.add_argument('--once', action='store_true', help='只跑一轮')
    ap.add_argument('--interval', type=int, default=0, metavar='MIN',
                    help='本地跑时的轮询间隔（分钟），GitHub 上由 workflow 控制')
    ap.add_argument('--volumes', type=int, default=8, help='每次扫多少个月份卷')
    ap.add_argument('--test-send', action='store_true', help='发一条测试消息验证配置')
    args = ap.parse_args()

    if args.test_send:
        demo = {'meeting': '连通性测试', 'city': '（测试）',
                'dates': '—', 'venue': '—', 'msg_url': ''}
        print(send_wechat(demo))
        print(send_feishu(demo))
        return

    if args.interval and args.interval > 0:
        n = 0
        while True:
            n += 1
            print(f'\n########## 第 {n} 轮 ##########')
            run(max_volumes=args.volumes)
            print(f'--- {_fmt(args.interval * 60)}后 ---')
            time.sleep(args.interval * 60)
    else:
        run(max_volumes=args.volumes)


def _fmt(s):
    return f'{s // 3600}小时{(s % 3600) // 60}分' if s >= 3600 else f'{s // 60}分'


if __name__ == '__main__':
    main()
