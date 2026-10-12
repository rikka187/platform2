"""多情景五维评分（经济 / 时效 / 绿色 / 协同 / 效率）。

维度与口径
----------
* **经济**：日总成本（越低越好，相对基准 S0 归一）
* **时效**：准时率 + 时间窗惩罚（惩罚越低越好）
* **绿色**：碳排放（越低越好）与新能源里程占比
* **协同**：月台冲突数、平均等待、平均装载率
* **效率**：车辆利用率、总里程（越短越好）

归一化方式（保证可比、可解释）：以基准情景 S0 为 60 分锚点，对每个指标按
"基准值 / 本情景值"的比例映射到 0-100，正向指标直接取比例。
所有分数均四舍五入到 1 位小数，输入相同则结果相同（确定性）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

DIMENSIONS = ['经济', '时效', '绿色', '协同', '效率']
ANCHOR = 60.0     # 基准情景得分锚点


def _safe(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ratio_score(baseline: float, current: float, higher_is_better: bool) -> float:
    """把"基准/本情景"比例映射到 0-100 分（基准 = 60 分）。"""
    if current <= 0:
        return 100.0 if higher_is_better else 0.0
    if baseline <= 0:
        return ANCHOR
    ratio = (current / baseline) if higher_is_better else (baseline / current)
    return max(0.0, min(100.0, round(ANCHOR * ratio, 1)))


def score_scenario(kpi: dict, baseline: Optional[dict] = None,
                   dock_summary: Optional[dict] = None, carbon_ratio: Optional[float] = None) -> dict:
    """给单个情景打分；给定 ``baseline`` 时按基准归一，否则给绝对分。"""
    baseline = baseline or kpi
    breakdown = kpi.get('cost_breakdown') or {}
    on_time = _safe(kpi.get('on_time_rate'))
    penalty = _safe(breakdown.get('penalty_cost'))
    load_rate = _safe(kpi.get('avg_load_rate'))
    utilization = _safe(kpi.get('vehicle_utilization'))
    distance = _safe(kpi.get('total_distance_km'))

    dock_summary = dock_summary or {}
    conflicts = _safe(dock_summary.get('conflicts'))
    wait = _safe(dock_summary.get('avg_wait_min'))

    base_penalty = _safe((baseline.get('cost_breakdown') or {}).get('penalty_cost'), 1.0) or 1.0
    base_wait = _safe((baseline.get('dock') or {}).get('avg_wait_min'), wait) or wait or 1.0
    base_conflicts = _safe((baseline.get('dock') or {}).get('conflicts'), conflicts)

    # 每个维度都由"比例得分"加权而成：同一情景与基准比较时比例恒为 1 → 恰好 ANCHOR 分。
    scores = {
        '经济': _ratio_score(_safe(baseline.get('total_cost')), _safe(kpi.get('total_cost')), False),
        '时效': round(
            _ratio_score(_safe(baseline.get('on_time_rate')), on_time, True) * 0.7
            + _ratio_score(base_penalty, penalty, False) * 0.3, 1),
        '绿色': _ratio_score(_safe(baseline.get('carbon_kg')), _safe(kpi.get('carbon_kg')), False),
        '协同': round(
            _ratio_score(1 + base_conflicts, 1 + conflicts, False) * 0.5
            + _ratio_score(base_wait, wait, False) * 0.2
            + _ratio_score(_safe(baseline.get('avg_load_rate')), load_rate, True) * 0.3, 1),
        '效率': round(
            _ratio_score(_safe(baseline.get('vehicle_utilization')), utilization, True) * 0.5
            + _ratio_score(_safe(baseline.get('total_distance_km')), distance, False) * 0.5, 1),
    }
    total = round(sum(scores.values()) / len(DIMENSIONS), 1)
    return {
        'scores': scores,
        'total_score': total,
        'metrics': {
            'total_cost': _safe(kpi.get('total_cost')),
            'on_time_rate': on_time,
            'carbon_kg': _safe(kpi.get('carbon_kg')),
            'avg_load_rate': load_rate,
            'vehicle_utilization': utilization,
            'total_distance_km': distance,
            'penalty_cost': penalty,
            'dock_conflicts': conflicts,
            'avg_wait_min': wait,
        },
        'carbon_ratio': carbon_ratio,
    }


def score_all(items: List[dict]) -> dict:
    """对 S0-S3 一组结果打分，返回矩阵与边际效益。

    ``items`` 每项需含 ``scenario``、``kpi``，可含 ``dock``（月台汇总）。
    """
    if not items:
        return {'dimensions': DIMENSIONS, 'items': []}
    ordered = sorted(items, key=lambda item: str(item.get('scenario', '')))
    baseline_kpi = ordered[0].get('kpi') or {}
    baseline_view = dict(baseline_kpi)
    baseline_view['dock'] = ordered[0].get('dock') or {}

    rows = []
    previous = None
    for item in ordered:
        kpi = item.get('kpi') or {}
        view = dict(kpi)
        view['dock'] = item.get('dock') or {}
        scored = score_scenario(kpi, baseline_view, dock_summary=item.get('dock'))
        row = {
            'scenario': item.get('scenario'),
            'label': item.get('label'),
            'fleet_tag': item.get('fleet_tag'),
            'solver': item.get('solver'),
            'kpi': kpi,
            'scores': scored['scores'],
            'total_score': scored['total_score'],
            'metrics': scored['metrics'],
        }
        if previous is not None:
            row['delta'] = {
                'cost': round(scored['metrics']['total_cost'] - previous['metrics']['total_cost'], 2),
                'carbon': round(scored['metrics']['carbon_kg'] - previous['metrics']['carbon_kg'], 2),
                'on_time': round(scored['metrics']['on_time_rate'] - previous['metrics']['on_time_rate'], 1),
                'distance': round(scored['metrics']['total_distance_km'] - previous['metrics']['total_distance_km'], 2),
                'score': round(scored['total_score'] - previous['total_score'], 1),
            }
        else:
            row['delta'] = {'cost': 0.0, 'carbon': 0.0, 'on_time': 0.0, 'distance': 0.0, 'score': 0.0}
        previous = scored
        rows.append(row)

    for row in rows:
        row['vs_baseline'] = {
            'cost_saving': round(rows[0]['metrics']['total_cost'] - row['metrics']['total_cost'], 2),
            'carbon_saving': round(rows[0]['metrics']['carbon_kg'] - row['metrics']['carbon_kg'], 2),
            'on_time_gain': round(row['metrics']['on_time_rate'] - rows[0]['metrics']['on_time_rate'], 1),
            'score_gain': round(row['total_score'] - rows[0]['total_score'], 1),
        }
    best = max(rows, key=lambda row: row['total_score'])
    return {
        'dimensions': DIMENSIONS,
        'anchor': ANCHOR,
        'items': rows,
        'best': best['scenario'],
        'ranking': [row['scenario'] for row in sorted(rows, key=lambda r: -r['total_score'])],
    }


__all__ = ['ANCHOR', 'DIMENSIONS', 'score_all', 'score_scenario']
