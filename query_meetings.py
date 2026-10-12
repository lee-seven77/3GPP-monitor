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


def search_meetings(keyword=None, groups=None, max_volumes=20, log=print):
    """搜索历史会议邀请。返回 [(group, subject, url), ...]"""
    default = ['RAN1', 'RAN2', 'RAN3', 'RAN4', 'RAN5', 'RAN6',
               'SA1', 'SA2', 'SA3', 'SA4', 'SA5', 'SA6',
               'RAN(全部)', 'SA(全部)']
    groups = groups or default

    all_mails = []
    for g in groups:
        vols = fetch_volumes(g)
        if not vols:
            continue
        todo = vols[:max_volumes]
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

    # 去重（同一邀请转发到多个列表）
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
        unique = [m for m in unique if kw in m['subject'].lower()]

    return unique


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
    args = ap.parse_args()

    groups = [args.group] if args.group else None

    if args.search or args.list:
        keyword = args.search if args.search else None
        mails = search_meetings(keyword=keyword, groups=groups,
                                max_volumes=args.volumes, log=print)
        show_details(mails, fetch_detail=args.detail, push=args.push, log=print)
    else:
        # 默认列出最近的
        mails = search_meetings(groups=groups, max_volumes=args.volumes, log=print)
        show_details(mails, fetch_detail=args.detail, push=args.push, log=print)


if __name__ == '__main__':
    main()
