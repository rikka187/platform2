"""仓配月台协同：备货反推、月台预约、装车时序与冲突检测。

输入是求解器给出的装车/发车时序（``schedule_entries``），本模块负责：
1. **备货反推**：由装车开始时刻倒推备货开始时刻（``prep_min`` 来自趟次的拣货/组盘时长）；
2. **月台预约**：按 ``dock_code`` 汇总占用时段，算出各月台利用率与排队情况；
3. **装车时序**：车辆到达 → 开始装车 → 装车完成 → 发车 的完整时间轴；
4. **冲突检测**：月台重叠占用、备货未完成就开始装车、装车晚于发车、同一车辆时间重叠、
   超出月台可用时段（03:00-12:00），并给出调整建议。

注意：CP-SAT 求解器本身带"月台不重叠"约束，因此 CP-SAT 方案通常零冲突；
启发式求解器按序号轮转分配月台，可能报出冲突——这正是本模块要暴露的问题。
"""
from __future__ import annotations

from typing import Dict, List, Optional

DOCK_OPEN_MIN = 3 * 60
DOCK_CLOSE_MIN = 12 * 60
TURNAROUND_MIN = 30


def _hhmm(minutes) -> str:
    if minutes is None:
        return ''
    total = int(minutes)
    return f'{total // 60:02d}:{total % 60:02d}'


def plan_docks(schedule: List[dict], trips: List[dict], params: Optional[Dict[str, str]] = None,
               dock_count: Optional[int] = None) -> dict:
    """把求解器的装车时序整理成月台协同视图 + 冲突清单。"""
    trip_map = {str(t.get('trip_id')): t for t in (trips or [])}
    conflicts: List[dict] = []
    entries = []

    for entry in schedule or []:
        trip = trip_map.get(str(entry.get('trip_id')), {})
        # 求解器输出的键是 dock_id（与前端/存储层同一契约），这里兼容两种写法
        dock = str(entry.get('dock_id') or entry.get('dock_code') or '未分配')
        prep_min = int(float(trip.get('prep_min') or 15))
        load_start = entry.get('load_start')
        load_end = entry.get('load_end')
        prep_end = entry.get('prep_end')
        # 备货反推：备货必须在装车开始前完成
        prep_start = entry.get('prep_start')
        if prep_start is None and load_start is not None:
            prep_start = max(DOCK_OPEN_MIN, int(load_start) - prep_min)
        item = {
            'trip_id': entry.get('trip_id'),
            'dock_code': dock,
            'vehicle_code': entry.get('vehicle_id'),
            'batch_id': trip.get('batch_id'),
            'customer_codes': trip.get('customer_codes') or [],
            'prep_start': prep_start,
            'prep_end': prep_end,
            'vehicle_arrival': entry.get('vehicle_arrival'),
            'load_start': load_start,
            'load_end': load_end,
            'depart': entry.get('depart'),
            'wait_min': entry.get('wait_min'),
            'prep_min': prep_min,
            'timeline': [
                {'label': '备货开始', 'minute': prep_start},
                {'label': '备货完成', 'minute': prep_end},
                {'label': '车辆到达', 'minute': entry.get('vehicle_arrival')},
                {'label': '开始装车', 'minute': load_start},
                {'label': '装车完成', 'minute': load_end},
                {'label': '发车', 'minute': entry.get('depart')},
            ],
        }
        item['timeline_text'] = [{'label': t['label'], 'time': _hhmm(t['minute']),
                                  'minute': t['minute']} for t in item['timeline']]
        entries.append(item)

    # ---- 冲突检测 ----
    for item in entries:
        trip_label = f"{item['trip_id']}（{item['dock_code']}）"
        if item['prep_end'] is not None and item['load_start'] is not None and \
                item['prep_end'] > item['load_start']:
            conflicts.append({'level': 'error', 'type': '备货未完成即装车', 'objects': [item['trip_id']],
                              'message': f"{trip_label} 备货完成 {_hhmm(item['prep_end'])} 晚于开始装车 "
                                         f"{_hhmm(item['load_start'])}",
                              'suggestion': '提前备货或推迟装车，必要时调整发车时刻'})
        if item['load_end'] is not None and item['depart'] is not None and item['load_end'] > item['depart']:
            conflicts.append({'level': 'error', 'type': '装车晚于发车', 'objects': [item['trip_id']],
                              'message': f"{trip_label} 装车完成 {_hhmm(item['load_end'])} 晚于发车 "
                                         f"{_hhmm(item['depart'])}",
                              'suggestion': '推迟发车或增加月台/人手'})
        for key, label in (('load_start', '开始装车'), ('load_end', '装车完成')):
            value = item.get(key)
            if value is not None and not DOCK_OPEN_MIN <= value <= DOCK_CLOSE_MIN:
                conflicts.append({'level': 'warn', 'type': '超出月台可用时段', 'objects': [item['trip_id']],
                                  'message': f"{trip_label} {label} {_hhmm(value)} 不在月台可用时段 "
                                             f"{_hhmm(DOCK_OPEN_MIN)}-{_hhmm(DOCK_CLOSE_MIN)} 内",
                                  'suggestion': '调整装车时段或延长月台开放时间'})

    # 月台重叠占用
    by_dock: Dict[str, List[dict]] = {}
    for item in entries:
        if item['load_start'] is None or item['load_end'] is None:
            continue
        by_dock.setdefault(item['dock_code'], []).append(item)
    for dock, items in by_dock.items():
        items.sort(key=lambda i: i['load_start'])
        for previous, current in zip(items, items[1:]):
            if current['load_start'] < previous['load_end']:
                conflicts.append({'level': 'error', 'type': '月台占用冲突',
                                  'objects': [previous['trip_id'], current['trip_id']],
                                  'message': f"{dock} 上 {previous['trip_id']}"
                                             f"（{_hhmm(previous['load_start'])}-{_hhmm(previous['load_end'])}）与 "
                                             f"{current['trip_id']}（{_hhmm(current['load_start'])}-"
                                             f"{_hhmm(current['load_end'])}）时间重叠",
                                  'suggestion': '把后一趟改派其他月台或推迟装车'})

    # 同一车辆时间重叠（装车→返场）
    by_vehicle: Dict[str, List[dict]] = {}
    for item in entries:
        code = str(item.get('vehicle_code') or '')
        if code and item['load_start'] is not None:
            by_vehicle.setdefault(code, []).append(item)
    for code, items in by_vehicle.items():
        items.sort(key=lambda i: i['load_start'])
        for previous, current in zip(items, items[1:]):
            if previous['depart'] is None:
                continue
            # 上一趟返场时刻（用趟次时长近似：装车完成 + 行程）
            trip = trip_map.get(str(previous['trip_id']), {})
            duration = float(trip.get('duration_min') or 0)
            back_at = float(previous['depart']) + duration + TURNAROUND_MIN
            # 容差 1 分钟：求解器内部用小数小时推算返场时刻，本模块用取整分钟重算，
            # 亚分钟差会被误判成"刚返场又装车"。真实冲突都是几十分钟量级，加容差不会漏报。
            if current['load_start'] < back_at - 1.0:
                conflicts.append({'level': 'error', 'type': '车辆时间冲突',
                                  'objects': [previous['trip_id'], current['trip_id']],
                                  'message': f"{code} 执行 {previous['trip_id']} 后预计 {_hhmm(back_at)} 返场，"
                                             f"但 {current['trip_id']} 已安排在 {_hhmm(current['load_start'])} 装车",
                                  'suggestion': '改用其他车辆或推迟该趟装车'})

    # ---- 月台视图 ----
    docks = []
    for dock, items in sorted(by_dock.items()):
        intervals = [{'trip_id': i['trip_id'], 'vehicle_code': i['vehicle_code'],
                      'start': i['load_start'], 'end': i['load_end'],
                      'start_text': _hhmm(i['load_start']), 'end_text': _hhmm(i['load_end']),
                      'duration_min': int(i['load_end'] - i['load_start'])} for i in items]
        busy = sum(i['duration_min'] for i in intervals)
        span = max(1, DOCK_CLOSE_MIN - DOCK_OPEN_MIN)
        docks.append({
            'dock_code': dock,
            'trips': len(intervals),
            'busy_min': busy,
            'utilization': min(100.0, round(busy / span * 100, 1)),
            'overbooked': busy > span,
            'intervals': intervals,
        })

    total_trips = len(entries)
    waiting = [i['wait_min'] for i in entries if i.get('wait_min') is not None]
    return {
        'entries': entries,
        'docks': docks,
        'conflicts': conflicts,
        'summary': {
            'trips': total_trips,
            'docks_used': len(by_dock),
            'dock_count': dock_count or len(by_dock),
            'avg_wait_min': round(sum(waiting) / len(waiting), 1) if waiting else 0,
            'max_wait_min': max(waiting) if waiting else 0,
            'conflicts': len(conflicts),
            'errors': sum(1 for c in conflicts if c['level'] == 'error'),
            'warnings': sum(1 for c in conflicts if c['level'] == 'warn'),
        },
    }


__all__ = ['DOCK_CLOSE_MIN', 'DOCK_OPEN_MIN', 'plan_docks']
