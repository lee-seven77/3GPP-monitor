#!/usr/bin/env python3
"""
3GPP 会议信息查询工具
====================

查询历史会议信息（地点、日期、酒店预订链接等）。

用法：
  python query_meetings.py --search Kobe          # 按关键词搜索
  python query_meetings.py --search "RAN3#133"    # 按会议号搜索
  python query_meetings.py --list                  # 列出最近的会议邀请
  python query_meetings.py --group RAN3            # 只搜特定工作组
  python query_meetings.py --volumes 20            # 搜更多历史卷
"""
import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from monitor import (
    fetch_volumes, fetch_volume_mails, build_invite,
    INVITE_RE, MAIL_LISTS, WAM,
)


def _parse_volume_date(label):
    """从存档标签提取年月，如 'October 2026,  2' -> (2026, 10)。"""
    MONTH_MAP = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
    m = re.match(r'([A-Za-z]+)\s+(\d{4})', label)
    if m:
        mon = MONTH_MAP.get(m.group(1)[:3].lower(), 0)
        return (int(m.group(2)), mon)
    return (0, 0)


def search_meetings(keyword=None, groups=None, max_volumes=50,
                    year=None, month=None, quarter=None, log=print):
    """搜索历史会议邀请。支持按年份/月/季度筛选。

    会议邀请通常提前 2-6 个月发出，搜索时优先扫描对应时间段的存档。
    year=2027, month=1  -> 搜 2026 年 7-12 月存档（提前半年找邀请）+ 标题含 2027
    """
    default = ['RAN1', 'RAN2', 'RAN3', 'RAN4', 'RAN5', 'RAN6',
               'SA1', 'SA2', 'SA3', 'SA4', 'SA5', 'SA6',
               'RAN(全部)', 'SA(全部)']
    groups = groups or default

    # 计算月份范围（用于筛选存档的发送时间）
    month_range = None
    if quarter:
        q = int(quarter)
        month_range = range((q - 1) * 3 + 1, (q - 1) * 3 + 4)
    elif month:
        month_range = [int(month)]

    # 会议邀请提前 2-6 个月发出：搜会议日期前 2-6 个月的存档
    # 例如搜 2027 年 1 月的会议 → 邀请可能在 2026 年 7-11 月发出
    # 所以存档筛选放宽到会议月份前 2-8 个月
    archive_month_range = None
    if year and month_range:
        y = int(year)
        expanded = set()
        for m in month_range:
            for offset in range(2, 9):  # 提前 2-8 个月
                am = m - offset
                ay = y
                while am < 1:
                    am += 12
                    ay -= 1
                expanded.add((ay, am))
        archive_month_range = expanded
    elif year and quarter:
        y = int(year)
        q = int(quarter)
        expanded = set()
        for m in range((q - 1) * 3 + 1, (q - 1) * 3 + 4):
            for offset in range(2, 9):
                am = m - offset
                ay = y
                while am < 1:
                    am += 12
                    ay -= 1
                expanded.add((ay, am))
        archive_month_range = expanded

    all_mails = []
    for g in groups:
        vols = fetch_volumes(g)
        if not vols:
            continue

        # 按存档时间筛选（用扩展范围）
        if archive_month_range:
            filtered_vols = []
            for label, url in vols:
                vy, vm = _parse_volume_date(label)
                if (vy, vm) in archive_month_range:
                    filtered_vols.append((label, url))
            todo = filtered_vols[:max_volumes]
        elif month_range:
            filtered_vols = []
            for label, url in vols:
                vy, vm = _parse_volume_date(label)
                if vm in month_range:
                    filtered_vols.append((label, url))
            todo = filtered_vols[:max_volumes]
        else:
            todo = vols[:max_volumes]

        if not todo:
            continue
        log(f'[搜索] {g}: 扫描 {len(todo)} 个存档...')

        def _fetch(vol_url):
            return fetch_volume_mails(vol_url)

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch, url): (g, url) for _, url in todo}
            for fut in as_completed(futures):
                g_name, _ = futures[fut]
                try:
                    mails = fut.result() or []
                    for m in mails:
                        if INVITE_RE.search(m['subject']):
                            m['group'] = g_name
                            all_mails.append(m)
                except Exception:
                    pass

    # 去重
    seen = set()
    unique = []
    for m in all_mails:
        key = re.sub(r'\s+', ' ', m['subject'].strip().lower())
        key = re.sub(r'^(fw|fwd|re|sv|vs|wg|antw)\s*:\s*', '', key)
        if key not in seen:
            seen.add(key)
            unique.append(m)

    # 关键词过滤
    if keyword:
        kw = keyword.lower()
        filtered = []
        for m in unique:
            if kw in m['subject'].lower():
                filtered.append(m)
            elif kw.replace('#', '').replace('-', '') in m['subject'].lower().replace('#', '').replace('-', ''):
                filtered.append(m)
        unique = filtered

    # 按年份过滤会议（标题里含年份，如 "2027 January"）
    if year:
        y = str(year)
        unique = [m for m in unique if y in m['subject']]

    # 按会议日期的月份过滤（标题里的月份，如 "April 2026"）
    MONTH_WORDS = {
        1: ['january', 'jan'], 2: ['february', 'feb'], 3: ['march', 'mar'],
        4: ['april', 'apr'], 5: ['may'], 6: ['june', 'jun'],
        7: ['july', 'jul'], 8: ['august', 'aug'], 9: ['september', 'sep', 'sept'],
        10: ['october', 'oct'], 11: ['november', 'nov'], 12: ['december', 'dec'],
    }
    if quarter:
        q = int(quarter)
        target_months = range((q - 1) * 3 + 1, (q - 1) * 3 + 4)
    elif month:
        target_months = [int(month)]
    else:
        target_months = None

    if target_months:
        def _match_month(subject):
            low = subject.lower()
            for m in target_months:
                for word in MONTH_WORDS.get(m, []):
                    if word in low:
                        return True
            return False
        unique = [m for m in unique if _match_month(m['subject'])]

    return unique


def _gen_hotel_search_urls(city, dates):
    """根据城市和日期生成携程/Booking 酒店搜索链接。"""
    if not city:
        return {}
    city_enc = city.split(',')[0].strip()  # 只取城市名

    # 解析日期（会议前一天入住，后一天离开）
    checkin = checkout = ''
    if dates:
        from datetime import datetime, timedelta
        m = re.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', dates)
        if m:
            mon = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5,
                   'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10,
                   'november': 11, 'december': 12}.get(m.group(3).lower(), 1)
            y, mo = int(m.group(4)), mon
            d1, d2 = int(m.group(1)), int(m.group(2))
            try:
                meeting_start = datetime(y, mo, d1)
                meeting_end = datetime(y, mo, d2)
                checkin_dt = meeting_start - timedelta(days=1)   # 会前一天
                checkout_dt = meeting_end + timedelta(days=1)    # 会后一天
                checkin = checkin_dt.strftime('%Y-%m-%d')
                checkout = checkout_dt.strftime('%Y-%m-%d')
            except ValueError:
                checkin = f'{y}-{mo:02d}-{d1:02d}'
                checkout = f'{y}-{mo:02d}-{d2:02d}'

    urls = {}
    # Booking.com
    booking_q = f'ss={city_enc}'
    if checkin:
        booking_q += f'&checkin={checkin}&checkout={checkout}'
    urls['Booking'] = f'https://www.booking.com/searchresults.html?{booking_q}'

    # 携程（国际版）
    ctrip_q = f'city={city_enc}'
    if checkin:
        ctrip_q += f'&checkin={checkin}&checkout={checkout}'
    urls['携程'] = f'https://hotels.ctrip.com/hotels/list?{ctrip_q}'

    # Google Maps 搜酒店
    urls['Google'] = f'https://www.google.com/maps/search/hotels+near+{city_enc}'

    return urls


def show_details(mails, fetch_detail=False, push=False, log=print):
    """显示会议列表，可选获取详细信息（含酒店链接），可选推送。"""
    if not mails:
        log('未找到匹配的会议邀请')
        return

    log(f'\n找到 {len(mails)} 个会议邀请：\n')
    for i, m in enumerate(mails, 1):
        log(f'  [{i}] {m["subject"]}')
        log(f'      组: {m.get("group", "?")}  来源: {m["url"][:80]}...')
        if fetch_detail or push:
            log('      获取详情...')
            info = build_invite(m['url'], log=log)
            if info.get('city'):
                log(f'      📍 地点: {info["city"]}')
            if info.get('dates'):
                log(f'      📅 日期: {info["dates"]}')
            if info.get('venue'):
                log(f'      🏛 场地: {info["venue"]}')
            if info.get('hotel_url'):
                log(f'      🏨 酒店: {info["hotel_url"]}')
            if info.get('coach_url'):
                log(f'      🚌 班车: {info["coach_url"]}')

            # 没有酒店链接 → 生成搜索链接
            if not info.get('hotel_url') and info.get('city'):
                hotel_urls = _gen_hotel_search_urls(info['city'], info.get('dates', ''))
                if hotel_urls:
                    log('      💡 无官方订房链接，可搜索附近酒店：')
                    for name, url in hotel_urls.items():
                        log(f'         {name}: {url}')

            if push:
                from monitor import send_wecom, send_feishu
                log(f'      → 企业微信: {send_wecom(info)}')
                log(f'      → 飞书: {send_feishu(info)}')
        log('')


def main():
    ap = argparse.ArgumentParser(description='3GPP 会议信息查询')
    ap.add_argument('--search', '-s', type=str, help='按关键词搜索（城市、会议号等）')
    ap.add_argument('--list', '-l', action='store_true', help='列出最近的会议邀请')
    ap.add_argument('--group', '-g', type=str, help='只搜特定工作组（如 RAN3）')
    ap.add_argument('--volumes', '-v', type=int, default=30, help='搜索的历史卷数（默认30）')
    ap.add_argument('--detail', '-d', action='store_true', help='获取详情（含酒店链接，较慢）')
    ap.add_argument('--push', '-p', action='store_true', help='获取详情并推送到企业微信/飞书')
    ap.add_argument('--year', '-y', type=str, help='按年份筛选（如 2027）')
    ap.add_argument('--month', '-m', type=str, help='按月份筛选（如 1=1月）')
    ap.add_argument('--quarter', '-q', type=str, help='按季度筛选（1-4）')
    args = ap.parse_args()

    groups = [args.group] if args.group else None

    if args.search or args.list:
        keyword = args.search if args.search else None
        mails = search_meetings(keyword=keyword, groups=groups,
                                max_volumes=args.volumes,
                                year=args.year, month=args.month,
                                quarter=args.quarter, log=print)
        show_details(mails, fetch_detail=args.detail, push=args.push, log=print)
    else:
        # 默认列出最近的
        mails = search_meetings(groups=groups, max_volumes=args.volumes,
                                year=args.year, month=args.month,
                                quarter=args.quarter, log=print)
        show_details(mails, fetch_detail=args.detail, push=args.push, log=print)


if __name__ == '__main__':
    main()
