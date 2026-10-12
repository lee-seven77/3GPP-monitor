#!/usr/bin/env python3
"""
3GPP 会议酒店监控 —— GitHub Actions 版
====================================

GitHub Actions 当「大脑和眼睛」（定时跑），企业微信自建应用 / 飞书当「信使」。
流程：定时启动 → 扫 3GPP 邮件列表标题 → 发现 "Meeting invitation"
     → 下载 .doc 提取订房链接 → 企业微信卡片/飞书推送 → 记录已通知（提交回仓库）

配置全部走环境变量 / GitHub Secrets，**代码里不写任何密钥**：
  WECOM_WEBHOOK        企业微信群机器人 webhook（推荐，无 IP 限制）
  FEISHU_WEBHOOK       飞书机器人 webhook（可选）
  GROUPS               监控哪些组，默认全部 14 个（RAN1-6 + SA1-6 + RAN/SA 全会）

本地也能跑：
  WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx python monitor.py --once
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
    index_url = f'{WAM}?A0={key}'
    resp = http_get(index_url, retries=2)
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
    vol_id = vol_url.split('A1=')[-1].split('&')[0] if 'A1=' in vol_url else vol_url
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


def dedupe_key(subject: str) -> str:
    """
    同一封邀请常被转发到多个邮件列表（RAN3 / RAN 全会...），
    标题可能带 Fw:/Re: 前缀。用「去掉转发前缀后的标题」做唯一键，
    这样同一个会议只通知一次。
    """
    s = re.sub(r'\s+', ' ', subject.strip().lower())
    s = re.sub(r'^(fw|fwd|re|sv|vs|wg|antw)\s*:\s*', '', s)
    return s.strip()


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

    # 显示扫描的存档范围，便于排查
    if todo:
        first_vol = todo[0].split('A1=')[-1].split('&')[0] if 'A1=' in todo[0] else '?'
        last_vol = todo[-1].split('A1=')[-1].split('&')[0] if 'A1=' in todo[-1] else '?'
        log(f'[扫描] {list_name}: 检查 {len(todo)} 个存档 ({first_vol} ~ {last_vol})')

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


def _extract_docx_text_and_links(data):
    """从 .docx 提取纯文本和超链接 (r:id -> text, url)。"""
    import io, zipfile
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        doc_xml = zf.read('word/document.xml').decode('utf-8', 'ignore')
        rels_xml = zf.read('word/_rels/document.xml.rels').decode('utf-8', 'ignore')
    except Exception:
        return '', []

    # r:id -> url 映射
    rid_map = {}
    for m in re.finditer(r'Id="(rId\d+)"[^>]*?Target="(http[^"]+)"', rels_xml):
        rid_map[m.group(1)] = html_mod.unescape(m.group(2))
    for m in re.finditer(r'Target="(http[^"]+)"[^>]*?Id="(rId\d+)"', rels_xml):
        rid_map.setdefault(m.group(2), html_mod.unescape(m.group(1)))

    # 纯文本
    texts = [html_mod.unescape(t) for t in re.findall(r'<w:t[^>]*>(.*?)</w:t>', doc_xml, re.S)]
    full_text = re.sub(r'\s+', ' ', ' '.join(texts))
    # 归一化：数字之间被空格拆开的（"2 5" → "25"），月份前的空格数字
    full_text = re.sub(r'(\d)\s+(\d)', r'\1\2', full_text)
    # 再清理一次多余空格
    full_text = re.sub(r'\s+', ' ', full_text)

    # 超链接: (text, url)
    links = []
    for m in re.finditer(r'<w:hyperlink[^>]*r:id="(rId\d+)"[^>]*>(.*?)</w:hyperlink>', doc_xml, re.S):
        rid, inner = m.group(1), m.group(2)
        text = _strip(inner)
        url = rid_map.get(rid, '')
        if url:
            links.append((text, url))

    return full_text, links


def extract_hotel_info(data):
    """从 .doc/.docx 提取订房/班车链接。返回 (hotel_url, coach_url)。"""
    hotel = coach = ''

    if data[:2] == b'PK':
        # .docx
        full_text, links = _extract_docx_text_and_links(data)
        low_full = full_text.lower()

        # 按超链接显示文本 + 上下文匹配（排除注册/签证/地址链接）
        for text, url in links:
            low = text.lower()
            url_low = url.lower()
            if any(k in low or k in url_low for k in
                   ('registration', '3gppregistration', 'visa', 'mofa.go.jp', 'kobe-cc.jp')):
                continue  # 跳过注册/签证/场馆链接
            if not hotel and any(k in low for k in ('hotel', 'accommodation', 'book', 'reserve')):
                hotel = url
            if not coach and any(k in low for k in ('coach', 'shuttle', 'bus')):
                coach = url

        # 如果链接文本是 URL 本身，用文档文本上下文判断
        if not hotel and links:
            for kw in ('hotel booking', 'book your hotel', 'hotel reservation',
                       'booking platform', 'book your room', 'hotel booking platform',
                       'hotel booking site'):
                idx = low_full.find(kw)
                if idx >= 0:
                    for text, url in links:
                        url_low = url.lower()
                        if url and not any(k in url_low for k in
                                           ('registration', 'visa', 'mofa.go.jp', 'kobe-cc.jp', 'ttc.or.jp')):
                            hotel = url
                            break
                    break

        # 检查是否有"自行预订"的说明
        if not hotel:
            for kw in ('make your own hotel', 'no block rooms', 'no exclusive nor designated',
                       'book your own accommodation'):
                if kw in low_full:
                    # 没有官方订房链接，记录说明
                    hotel = ''
                    break

    else:
        # 老式 .doc
        for m in _HLINK_FIELD.finditer(data):
            url = m.group(1).decode('ascii', 'ignore')
            tail = data[m.end():m.end() + 240]
            lm = re.match(rb'[\x00-\x1f\x7f-\xff]{0,10}([A-Za-z0-9][ -~]{2,110})', tail)
            label = ''
            if lm:
                seg = lm.group(1).decode('ascii', 'ignore')
                label = re.split(r'[.]{1,}|\x00', seg)[0].strip()
            low = label.lower()
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
    """从邮件页 + .doc/.docx 里提取会议信息。"""
    info = {'subject': '', 'meeting': '', 'city': '', 'dates': '',
            'venue': '', 'hotel_url': '', 'coach_url': ''}
    sm = re.search(r'Subject:\s*</b>\s*</td>\s*<td[^>]*>(.*?)</td>', msg_html, re.I | re.S)
    if sm:
        info['subject'] = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', sm.group(1))).strip()

    # 从标题提取城市（如 "Meeting invitation for Kobe, Japan 2027 January"）
    mm = re.search(r'for\s+(.+?)\s+in\s+([A-Za-z][A-Za-z \-]{2,40})\s*$', info['subject'], re.I)
    if mm:
        info['meeting'], info['city'] = mm.group(1).strip(), mm.group(2).strip()
    else:
        # 尝试 "for Kobe, Japan 2027 January" 格式
        mm2 = re.search(r'for\s+(.+?),\s*([A-Za-z]+)\s+(\d{4})', info['subject'], re.I)
        if mm2:
            info['city'] = f'{mm2.group(1).strip()}, {mm2.group(2).strip()}'
            info['meeting'] = info['subject']
        else:
            info['meeting'] = info['subject']

    if doc_data:
        if doc_data[:2] == b'PK':
            # .docx：提取纯文本
            full_text, links = _extract_docx_text_and_links(doc_data)
            plain = full_text
            # 提取日期（如 "25 - 29 January 2027"）
            dm = re.search(rf'(\d{{1,2}}\s*[-–]\s*\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})', plain, re.I)
            if dm:
                info['dates'] = re.sub(r'\s+', ' ', dm.group(1)).strip()
            # 提取场地
            vm = re.search(r'((?:[A-Z][A-Za-z]+ )*(?:International )?Conference Cent(?:re|er)[^.,;]{0,50})', plain, re.I)
            if vm:
                info['venue'] = vm.group(1).strip()
            # 提取城市（从正文找 "in <City>, <Country>" 格式）
            if not info['city']:
                cm = re.search(r'in\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*),\s*([A-Z][A-Za-z]+)', plain)
                if cm:
                    info['city'] = f'{cm.group(1).strip()}, {cm.group(2).strip()}'
        else:
            # 老式 .doc
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

def _gen_hotel_search_urls(city, dates):
    """根据城市和日期生成携程/Booking 酒店搜索链接（会前一天入住、会后一天离开）。"""
    if not city:
        return {}
    city_enc = city.split(',')[0].strip()
    checkin = checkout = ''
    if dates:
        from datetime import datetime, timedelta
        m = re.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', dates)
        if m:
            mon = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5,
                   'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10,
                   'november': 11, 'december': 12}.get(m.group(3).lower(), 1)
            try:
                start = datetime(int(m.group(4)), mon, int(m.group(1)))
                end = datetime(int(m.group(4)), mon, int(m.group(2)))
                checkin = (start - timedelta(days=1)).strftime('%Y-%m-%d')
                checkout = (end + timedelta(days=1)).strftime('%Y-%m-%d')
            except ValueError:
                pass
    urls = {}
    q = f'ss={city_enc}'
    if checkin:
        q += f'&checkin={checkin}&checkout={checkout}'
    urls['Booking'] = f'https://www.booking.com/searchresults.html?{q}'
    q2 = f'city={city_enc}'
    if checkin:
        q2 += f'&checkin={checkin}&checkout={checkout}'
    urls['携程'] = f'https://hotels.ctrip.com/hotels/list?{q2}'
    return urls


def _send_wecom_webhook(info):
    """群机器人 webhook 推送（无 IP 限制）。"""
    webhook = os.environ.get('WECOM_WEBHOOK', '').strip()
    if not webhook:
        return None  # 未配置

    title = f"🏨 3GPP 会议邀请！{info.get('city') or ''}".strip()
    lines = [f'**{title}**', '---']
    if info.get('meeting') or info.get('subject'):
        lines.append(f'**会议：** {info.get("meeting") or info.get("subject")}')
    if info.get('city'):
        lines.append(f'**📍 地点：** {info["city"]}')
    if info.get('dates'):
        lines.append(f'**📅 日期：** {info["dates"]}')
    if info.get('venue'):
        lines.append(f'**🏛 场地：** {info["venue"]}')
    if info.get('hotel_url'):
        lines.append(f'**🔥 酒店：** [点此立即预订]({info["hotel_url"]})')
    elif info.get('city'):
        # 没有官方订房链接 → 生成搜索链接
        hotel_urls = _gen_hotel_search_urls(info['city'], info.get('dates', ''))
        if hotel_urls:
            parts = [f'[{name}]({url})' for name, url in hotel_urls.items()]
            lines.append(f'**🏨 酒店搜索：** {" | ".join(parts)}')
    if info.get('coach_url'):
        lines.append(f'**🚌 班车：** [班车预订]({info["coach_url"]})')
    if info.get('msg_url'):
        lines.append(f'**📧 邮件：** [打开原邮件]({info["msg_url"]})')
    content = '\n'.join(lines)

    payload = {'msgtype': 'markdown', 'markdown': {'content': content}}
    try:
        r = requests.post(webhook, json=payload, timeout=15)
        res = r.json()
        return '✅ 群机器人已推送' if res.get('errcode') == 0 else f"❌ 群机器人失败: {res.get('errmsg')}"
    except Exception as e:
        return f'❌ 群机器人异常: {e}'


def _send_wecom_app(info):
    """自建应用 textcard 推送。支持两种方式：
    1. 直连（本地跑，IP 在白名单内）
    2. 云函数中转（GitHub Actions，通过 WECOM_RELAY_URL 转发）
    """
    # 优先走云函数中转（无 IP 限制）
    relay_url = os.environ.get('WECOM_RELAY_URL', '').strip()
    if relay_url:
        title = f"🏨 3GPP 会议邀请！{info.get('city') or ''}".strip()
        desc = [f'<div class="gray">{datetime.now():%Y-%m-%d %H:%M}</div>']
        if info.get('meeting') or info.get('subject'):
            desc.append(f'<div class="normal"><b>会议：</b>{info.get("meeting") or info.get("subject")}</div>')
        if info.get('city'):
            desc.append(f'<div class="normal"><b>📍 地点：</b>{info["city"]}</div>')
        if info.get('dates'):
            desc.append(f'<div class="normal"><b>📅 日期：</b>{info["dates"]}</div>')
        if info.get('venue'):
            desc.append(f'<div class="normal"><b>🏛 场地：</b>{info["venue"]}</div>')
        if info.get('hotel_url'):
            desc.append('<div class="highlight">🔥 酒店已按优惠价预锁定，请尽快下单</div>')
        jump_url = info.get('hotel_url') or info.get('msg_url') or 'https://www.3gpp.org/'
        try:
            r = requests.post(relay_url, json={
                'title': title, 'description': '\n'.join(desc),
                'url': jump_url,
                'btntxt': '🔥 立即预订酒店' if info.get('hotel_url') else '📧 打开原邮件',
            }, timeout=15)
            res = r.json()
            body = res.get('body', '{}')
            if isinstance(body, str):
                body = json.loads(body)
            return '✅ 自建应用(中转)已推送' if body.get('ok') else f"❌ 自建应用(中转)失败: {body.get('err')}"
        except Exception as e:
            return f'❌ 自建应用(中转)异常: {e}'

    # 直连模式（本地跑）
    corp_id = os.environ.get('WECOM_CORP_ID', '').strip()
    agent_id = os.environ.get('WECOM_AGENT_ID', '').strip()
    secret = os.environ.get('WECOM_API_SECRET', '').strip()
    if not corp_id or not agent_id or not secret:
        return None  # 未配置

    # 获取 token
    token = None
    try:
        res = requests.get(
            f'https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}',
            timeout=10).json()
        if res.get('errcode') == 0:
            token = res.get('access_token')
        else:
            return f'❌ 自建应用token失败: {res.get("errmsg")}'
    except Exception as e:
        return f'❌ 自建应用token异常: {e}'

    title = f"🏨 3GPP 会议邀请！{info.get('city') or ''}".strip()
    desc = [f'<div class="gray">{datetime.now():%Y-%m-%d %H:%M}</div>']
    if info.get('meeting') or info.get('subject'):
        desc.append(f'<div class="normal"><b>会议：</b>{info.get("meeting") or info.get("subject")}</div>')
    if info.get('city'):
        desc.append(f'<div class="normal"><b>📍 地点：</b>{info["city"]}</div>')
    if info.get('dates'):
        desc.append(f'<div class="normal"><b>📅 日期：</b>{info["dates"]}</div>')
    if info.get('venue'):
        desc.append(f'<div class="normal"><b>🏛 场地：</b>{info["venue"]}</div>')
    if info.get('hotel_url'):
        desc.append('<div class="highlight">🔥 酒店已按优惠价预锁定，请尽快下单</div>')

    jump_url = info.get('hotel_url') or info.get('msg_url') or 'https://www.3gpp.org/'
    data = {
        'touser': '@all',
        'msgtype': 'textcard',
        'agentid': int(agent_id),
        'textcard': {
            'title': title,
            'description': '\n'.join(desc),
            'url': jump_url,
            'btntxt': '🔥 立即预订酒店' if info.get('hotel_url') else '📧 打开原邮件',
        },
        'safe': 0,
    }
    try:
        r = requests.post(
            f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}',
            json=data, timeout=15)
        res = r.json()
        return '✅ 自建应用卡片已推送' if res.get('errcode') == 0 else f"❌ 自建应用失败: {res.get('errmsg')}"
    except Exception as e:
        return f'❌ 自建应用异常: {e}'


def send_wecom(info):
    """企业微信推送：同时尝试群机器人 + 自建应用，配置了哪个就用哪个。"""
    results = []
    r1 = _send_wecom_webhook(info)
    if r1:
        results.append(r1)
    r2 = _send_wecom_app(info)
    if r2:
        results.append(r2)
    return ' | '.join(results) if results else '跳过企业微信（未配置 WECOM_WEBHOOK 或 WECOM_CORP_ID 等）'


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
        rows.append(f'**🏨 酒店:** [点此立即预订]({info["hotel_url"]})')
    elif info.get('city'):
        hotel_urls = _gen_hotel_search_urls(info['city'], info.get('dates', ''))
        if hotel_urls:
            parts = [f'[{name}]({url})' for name, url in hotel_urls.items()]
            rows.append(f'**🏨 酒店搜索:** {" | ".join(parts)}')
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

def run(groups=None, max_volumes=2):
    # 默认监控全部 14 个列表 —— 各工作组是独立邮件列表，
    # 只盯全会列表会漏掉（实测 RAN3 的 Kobe 邀请就不在全会列表里）
    default = ('RAN1,RAN2,RAN3,RAN4,RAN5,RAN6,'
               'SA1,SA2,SA3,SA4,SA5,SA6,RAN(全部),SA(全部)')
    groups = groups or [g.strip() for g in
                        os.environ.get('GROUPS', default).split(',') if g.strip()]
    seen = load_seen()
    print('=' * 58)
    print(' 3GPP 会议酒店监控 (GitHub Actions)')
    print('=' * 58)
    print(f' 监控列表 : {", ".join(groups)}')
    wecom_bot = '✅' if os.environ.get('WECOM_WEBHOOK') else '—'
    wecom_app = '✅' if all([os.environ.get('WECOM_CORP_ID'), os.environ.get('WECOM_AGENT_ID'), os.environ.get('WECOM_API_SECRET')]) else '—'
    print(f' 企业微信 : 群机器人 {wecom_bot} | 自建应用 {wecom_app}')
    print(f' 飞书推送 : {"已配置" if os.environ.get("FEISHU_WEBHOOK") else "未配置"}')
    print(f' 已记录   : {len(seen)} 封')
    print(f' 运行时间 : {datetime.now():%Y-%m-%d %H:%M:%S}')
    print('=' * 58, flush=True)

    new_count = 0
    for g in groups:
        invites = scan_invitations(g, max_volumes=max_volumes, log=print)
        fresh = [m for m in invites if dedupe_key(m['subject']) not in seen]
        print(f'[扫描] {g}: 命中 {len(invites)} 封，新增 {len(fresh)} 封')
        for m in fresh:
            print(f'[新] {m["subject"][:70]}')
            info = build_invite(m['url'], log=print)
            print(f"     → {send_wecom(info)}")
            print(f"     → {send_feishu(info)}")
            if info.get('hotel_url'):
                print(f"     → 订房: {info['hotel_url']}")
            seen.add(dedupe_key(m['subject']))
            new_count += 1
    save_seen(seen)
    print(f'\n[完成] 本轮新增 {new_count} 封，累计 {len(seen)} 封', flush=True)
    return new_count


def main():
    ap = argparse.ArgumentParser(description='3GPP 会议酒店监控 (GitHub Actions 版)')
    ap.add_argument('--once', action='store_true', help='只跑一轮')
    ap.add_argument('--interval', type=int, default=0, metavar='MIN',
                    help='本地跑时的轮询间隔（分钟），GitHub 上由 workflow 控制')
    ap.add_argument('--volumes', type=int, default=2, help='每次扫多少个月份卷（默认2，仅最新）')
    ap.add_argument('--test-send', action='store_true', help='发一条测试消息验证配置')
    args = ap.parse_args()

    if args.test_send:
        print('=' * 40)
        print(' 发送测试卡片验证推送通道')
        print('=' * 40)
        demo = {
            'meeting': '3GPP 监控助手 — 连通性测试',
            'city': '（测试）',
            'dates': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'venue': '推送通道正常，后续发现会议邀请会自动推送',
            'msg_url': 'https://www.3gpp.org/',
            'hotel_url': '',
        }
        print(f' 企业微信: {send_wecom(demo)}')
        print(f' 飞书推送: {send_feishu(demo)}')
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
