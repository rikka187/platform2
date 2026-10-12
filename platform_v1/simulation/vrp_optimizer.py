"""路径优化求解器：Clarke-Wright 节约法 + ALNS + 车辆区间调度。

算法结构与约束来自团队既有的路径优化程序（`vrp_solver.py`），
算法结构、约束与成本口径与原程序保持一致，只把数据来源从硬编码参数表改为平台数据库。

硬约束
------
* 品类禁混（模型式29）：同一条路线只装同一品类；
* 批次禁混：同一条路线只装同一批次客户；
* 载重 ≤ 车型额定载重；单趟里程 ≤ 续航；单趟时长（发车→返场）≤ T_MAX；
* 各客户**到达时刻 ≤ 可接受窗上界 L**；返场时刻 ≤ 配送中心结束作业 T0L；
* 车辆池配额（现状 8 小型 + 2 中型），同一辆车可多趟次复用（返回 + 周转后方可再出发）。

发车时刻是**决策变量**：可行区间为 ``[批次就绪时间, min_i(L_i − 该点累计行驶时间)]``；
复用趟次取 ``max(就绪时间, 前一趟返场 + 周转)``。

目标函数（运营成本 + 时效损失）
------------------------
``F1 = Σ(每趟出车费 chT) + Σ(里程 × 单位运输成本) + 配送/等待制冷 + 服务制冷``
``F2 = 早到惩罚(x) + 晚到惩罚``
碳排暂不计入目标函数。**注意：本口径不含维护费、货损与能耗**，
与"配送成本"口径一致；平台综合口径（含维护/货损/能耗/碳排）由 costing 另算。
"""
from __future__ import annotations

import math
import random
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from simulation.geo import haversine_km, round_km

# ---- 与团队求解程序一致的常数（来源：论文表2-1/表2-2 与模型式(15)(21)）----
SPEED_KMH = 40.0            # 平均行驶速度（表2-2）
T_MAX = 600.0               # 单条路径最大时长（min）—— 模型式(21)
MAX_ROUTE_DIST = 600.0      # 车辆续航里程（km）—— 表2-1
T0E = 180.0                 # 配送中心开始作业 03:00 —— 模型式(15)
T0L = 720.0                 # 配送中心结束作业 12:00 —— 模型式(15)
THREADY = 30.0              # 车辆周转准备时间（卸货+装货+检查，案例未给，按模型建议取值）
CROSS_PRODUCT_REUSE = False  # 鲜面条严禁与姜蒜混装（模型式16/17）→ 禁跨品类复用
ALLOW_EARLY_SERVICE = False  # False：早到等待至期望窗开始（等待计入配送制冷）
DEPARTURE_POLICY = 'ready'   # 'ready' = 固定批次就绪时刻发车（与原程序逐行一致，可复现论文 S0）
                             # 'optimise' = 在可行发车窗口内优化发车时刻（论文 o_vk 的完整实现，成本更低）
MIN_CUSTOMERS_PER_ROUTE = 1  # 原程序写的是 len(route) < 4 即至少 2 户；但 [0,c,0] 是合法单客户趟次，
# 该判断会让 greedy_insertion 的单点兜底永远不可达，真实时间窗下直接无解 → 这里放开为 1 户。
DEFAULT_RESTARTS = 5         # ALNS 独立重启次数
DEFAULT_ITER = 3000          # 每次重启的迭代次数
DEFAULT_SEED = 42            # 固定随机种子 → 结果可复现

COOL_DELIVER = 8.5           # 配送/等待制冷（元/h）—— 表2-2
COOL_SERVICE = 10.0          # 服务制冷（元/h）—— 表2-2
PEN_EARLY = 5.0              # 早到惩罚（元/h）—— 表2-2
PEN_LATE = 15.0              # 晚到惩罚（元/h）—— 表2-2


class VrpError(RuntimeError):
    """数据不足或找不到可行解。"""


# ======================================================================
# 问题构造：从平台数据库读客户、需求、时间窗、批次与车辆池
# ======================================================================
class Node:
    __slots__ = ('product', 'code', 'index', 'lon', 'lat', 'demand', 'volume', 'service',
                 'e', 'l', 'E', 'L', 'batch', 'name')

    def __init__(self, product: str, code: str, index: int, lon: float, lat: float, demand: float,
                 volume: float, service: float, e: float, l: float, E: float, L: float,
                 batch: str, name: str = ''):
        self.product = product
        self.code = code
        self.index = index
        self.lon = lon
        self.lat = lat
        self.demand = demand
        self.volume = volume
        self.service = service
        self.e = e if e is not None else E
        self.l = l if l is not None else L
        self.E = E if E is not None else self.e
        self.L = L if L is not None else self.l
        self.batch = batch
        self.name = name


class Problem:
    """一次联合优化所需的全部输入（两个品类共享同一个车辆池）。"""

    def __init__(self, products: Sequence[str], nodes: Dict[str, List[Node]], dc: Tuple[float, float],
                 vehicles: Dict[str, dict], depart_ready: Dict[Tuple[str, str], float],
                 params: Optional[dict] = None):
        self.products = list(products)
        self.nodes = nodes
        self.dc = dc
        self.vehicles = vehicles                     # {vehicle_type: {...}}
        self.depart_ready = depart_ready             # {(product, batch): 就绪时间}
        self.params = params or {}
        self.dist: Dict[str, List[List[float]]] = {}
        self.time: Dict[str, List[List[float]]] = {}
        for product in self.products:
            points = [dc] + [(n.lon, n.lat) for n in nodes[product]]
            n = len(points)
            dist = [[0.0] * n for _ in range(n)]
            for i in range(n):
                for j in range(i + 1, n):
                    value = round_km(haversine_km(points[i][0], points[i][1], points[j][0], points[j][1]))
                    dist[i][j] = dist[j][i] = value
            self.dist[product] = dist
            self.time[product] = [[d / SPEED_KMH * 60.0 for d in row] for row in dist]
        # 车型按载重升序，便于"选能装下的最小车型"
        self.types = sorted(self.vehicles.keys(),
                            key=lambda t: float(self.vehicles[t].get('capacity') or 0))

    # ---- 基础工具 ----
    def node(self, product: str, code: str) -> Node:
        for item in self.nodes[product]:
            if item.code == str(code):
                return item
        raise VrpError(f'{product} 下没有客户 {code}')

    def ix(self, token) -> int:
        """路线里的节点记号 → 距离矩阵下标：0 表示配送中心，其余为 (品类, index)。"""
        if token == 0:
            return 0
        return token[1]

    def ready_time(self, product: str, batch: str) -> float:
        return float(self.depart_ready.get((product, batch), T0E))

    def pick_vehicle(self, demand: float, volume: float = 0.0) -> Optional[str]:
        """按载重（并校验容积）选最小可用车型。"""
        for name in self.types:
            spec = self.vehicles[name]
            if demand <= float(spec.get('capacity') or 0) + 1e-6 and \
                    volume <= float(spec.get('volume') or 0) + 1e-6:
                return name
        return None


def build_problem(conn: sqlite3.Connection, fleet_tag: str = 'baseline',
                  products: Sequence[str] = ('姜蒜', '鲜面条'),
                  overrides: Optional[dict] = None, profile: str = 'case2') -> Problem:
    """从平台数据库构造问题实例。

    ``profile='case2'``（默认）：需求、服务时间、两级时间窗取自表2-3/2-4 的真实数据；
    ``profile='paper'``：改用论文基准构造算例（团队脚本的 CUSTOMERS 表，窗口更宽），
    用于复现论文 S0 的 4226.68 元 / 717.16 km / 8 趟次 / 75.0% 准时率。
    两种口径共用客户坐标（论文构造未改坐标）。
    """
    params = {row['key']: row['value'] for row in conn.execute('SELECT key, value FROM params')}
    params.update(overrides or {})
    dc_lon = float(params.get('dc_lon', 104.26))
    dc_lat = float(params.get('dc_lat', 30.85))

    alt_rows: Dict[Tuple[str, str], dict] = {}
    if profile == 'paper':
        alt_rows = _load_paper_profile()
        if not alt_rows:
            raise VrpError('找不到论文基准构造算例数据（data/case2/customer_profile_paper.csv），'
                           '可执行 tools/build_paper_profile.py 重新生成。')

    nodes: Dict[str, List[Node]] = {}
    for product in products:
        rows = list(conn.execute(
            'SELECT c.code, c.name, c.lon, c.lat, c.batch_code, c.service_min, '
            '       c.exp_start, c.exp_end, c.acc_start, c.acc_end, '
            '       (SELECT SUM(d.demand_kg) FROM customer_demand d WHERE d.customer_id = c.id) AS kg, '
            '       (SELECT SUM(d.volume_m3) FROM customer_demand d WHERE d.customer_id = c.id) AS m3 '
            'FROM customers c WHERE c.product_scope = ? AND c.active = 1 '
            'ORDER BY c.batch_code, CAST(c.code AS INTEGER)', (product,)))
        items = []
        for index, row in enumerate(rows, start=1):
            alt = alt_rows.get((product, str(row['code'])))
            demand = float(alt['demand_kg']) if alt else float(row['kg'] or 0)
            service = float(alt['service_min']) if alt else float(row['service_min'] or 0)
            batch = alt['batch_code'] if alt else (row['batch_code'] or '未分批')
            volume = float(row['m3'] or 0)
            if alt and not volume:
                volume = demand / 500.0        # 论文构造算例未给体积，按品类密度折算
            items.append(Node(product, str(row['code']), index, float(row['lon']), float(row['lat']),
                              demand, volume, service,
                              alt['exp_start'] if alt else row['exp_start'],
                              alt['exp_end'] if alt else row['exp_end'],
                              alt['acc_start'] if alt else row['acc_start'],
                              alt['acc_end'] if alt else row['acc_end'],
                              batch, row['name'] or ''))
        if not items:
            raise VrpError(f'{product} 下没有可用客户。')
        nodes[product] = items

    vehicles: Dict[str, dict] = {}
    for row in conn.execute(
            'SELECT vehicle_type, code, energy_type, capacity_kg, capacity_m3, fixed_cost, unit_cost, '
            '       range_km, maintenance_cost FROM vehicles WHERE fleet_tag = ? AND available = 1 '
            "ORDER BY vehicle_type, CASE energy_type WHEN '纯电' THEN 0 ELSE 1 END, code", (fleet_tag,)):
        item = vehicles.setdefault(row['vehicle_type'], {
            'count': 0, 'fixed': float(row['fixed_cost']), 'unit_cost': float(row['unit_cost']),
            'capacity': float(row['capacity_kg']), 'volume': float(row['capacity_m3']),
            'range': float(row['range_km']), 'codes': [], 'energy': [], 'enable': 0.0,
            'maintenance': float(row['maintenance_cost'] or 0)})
        item['count'] += 1
        item['codes'].append(row['code'])
        item['energy'].append(row['energy_type'])
    if not vehicles:
        raise VrpError(f'车队 {fleet_tag} 下没有可用车辆。')

    depart_ready = {}
    for row in conn.execute('SELECT code, product_scope, depart_after FROM batches'):
        if row['product_scope'] in products:
            depart_ready[(row['product_scope'], row['code'])] = float(row['depart_after'])
    if profile == 'paper':                      # 论文构造算例自带批次就绪时刻
        for (product, _code), alt in alt_rows.items():
            depart_ready[(product, alt['batch_code'])] = float(alt['depart_after'])
    if not depart_ready:
        raise VrpError('没有读到批次起送时刻（batches 表为空）。')
    return Problem(products, nodes, (dc_lon, dc_lat), vehicles, depart_ready, params)


def _load_paper_profile() -> Dict[Tuple[str, str], dict]:
    """读取论文基准构造算例（由 tools/build_paper_profile.py 从团队脚本导出）。"""
    import csv
    path = Path(__file__).resolve().parents[1] / 'data' / 'case2' / 'customer_profile_paper.csv'
    if not path.is_file():
        return {}

    def minutes(value) -> float:
        text = str(value or '').strip()
        if ':' in text:
            hour, _, minute = text.partition(':')
            return float(hour) * 60 + float(minute or 0)
        return float(text or 0)

    rows: Dict[Tuple[str, str], dict] = {}
    with path.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            rows[(row['product_scope'], str(row['code']))] = {
                'batch_code': row['batch_code'], 'depart_after': minutes(row['depart_after']),
                'demand_kg': float(row['demand_kg']), 'service_min': float(row['service_min']),
                'exp_start': minutes(row['exp_start']), 'exp_end': minutes(row['exp_end']),
                'acc_start': minutes(row['acc_start']), 'acc_end': minutes(row['acc_end']),
            }
    return rows


# ======================================================================
# 路线评估（可行性 + 成本）——与团队程序逐行对应
# ======================================================================
def route_time_window(problem: Problem, route: List) -> Optional[Tuple[float, float]]:
    """可行发车窗口 [t_min, t_max]；违反品类/批次一致性或窗口为空返回 None。"""
    if len(route) < MIN_CUSTOMERS_PER_ROUTE + 2:
        return None
    product = route[1][0]
    time_mat = problem.time[product]
    customers = problem.nodes[product]
    if any(token[0] != product for token in route[1:-1]):
        return None
    batch = customers[route[1][1] - 1].batch
    if any(customers[token[1] - 1].batch != batch for token in route[1:-1]):
        return None
    t_min = problem.ready_time(product, batch)
    t_max = float('inf')
    cumulative = 0.0
    prev = 0
    for token in route[1:-1]:
        cumulative += time_mat[prev][token[1]]
        t_max = min(t_max, customers[token[1] - 1].L - cumulative)
        prev = token[1]
    if t_max < t_min:
        return None
    return t_min, t_max


def evaluate_route(problem: Problem, route: List, depart_t: Optional[float] = None,
                   earliest: Optional[float] = None):
    """计算一条闭合路线在给定发车时刻下的可行性与成本。返回 (ok, info)。

    ``depart_t`` 缺省时**在可行发车窗口内搜索成本最低的发车时刻**——模型里发车时刻
    本就是决策变量 ``o_vk ∈ [批次就绪时间, min_i(L_i − 累计行驶时间)]``；原程序固定取
    区间下界，会把"晚一点发车才可行"的合并误判为不可行（用真实时间窗时直接导致无解）。
    """
    if depart_t is None:
        if DEPARTURE_POLICY == 'ready':
            # 与原程序一致：固定取批次就绪时刻（复用时取 max(就绪, 车辆可用)）
            product = route[1][0]
            batch = problem.nodes[product][route[1][1] - 1].batch
            depart_t = problem.ready_time(product, batch)
            if earliest is not None:
                depart_t = max(depart_t, earliest)
            return _evaluate_at(problem, route, depart_t)
        window = route_time_window(problem, route)
        if window is None:
            return False, None
        low = max(window[0], T0E if earliest is None else earliest)
        if low > window[1] + 1e-6:
            return False, None
        return _scan_departure(problem, route, low, window[1])
    return _evaluate_at(problem, route, depart_t)


def _scan_departure(problem: Problem, route: List, low: float, high: float, step: float = 5.0):
    """在 [low, high] 内按 step 分钟扫描，取该趟成本最低的发车时刻。"""
    best = None
    point = low
    while point <= high + 1e-6:
        ok, info = _evaluate_at(problem, route, point)
        if ok and (best is None or info['route_cost'] < best[1]['route_cost'] - 1e-9):
            best = (point, info)
        point += step
    if (high - low) % step > 1e-6:            # 上界本身可能不是 step 的整数倍
        ok, info = _evaluate_at(problem, route, high)
        if ok and (best is None or info['route_cost'] < best[1]['route_cost'] - 1e-9):
            best = (high, info)
    if best is None:
        return False, None
    return True, best[1]


def _evaluate_at(problem: Problem, route: List, depart_t: float):
    """给定发车时刻的可行性检查与成本计算（对应原程序 evaluate_route）。"""
    if len(route) < MIN_CUSTOMERS_PER_ROUTE + 2:
        return False, None
    product = route[1][0]
    time_mat = problem.time[product]
    dist_mat = problem.dist[product]
    customers = problem.nodes[product]
    if any(token[0] != product for token in route[1:-1]):
        return False, None
    batch = customers[route[1][1] - 1].batch
    if any(customers[token[1] - 1].batch != batch for token in route[1:-1]):
        return False, None

    demand = sum(customers[token[1] - 1].demand for token in route[1:-1])
    volume = sum(customers[token[1] - 1].volume for token in route[1:-1])
    vtype = problem.pick_vehicle(demand, volume)
    if vtype is None:
        return False, None
    spec = problem.vehicles[vtype]

    distance = 0.0
    drive_time = 0.0
    wait = 0.0
    serve_min = sum(customers[token[1] - 1].service for token in route[1:-1])
    current = depart_t
    arrive: Dict = {}
    serve: Dict = {}
    leave: Dict = {}
    for idx in range(1, len(route) - 1):
        token = route[idx]
        prev = route[idx - 1]
        current += time_mat[problem.ix(prev)][token[1]]
        distance += dist_mat[problem.ix(prev)][token[1]]
        drive_time += time_mat[problem.ix(prev)][token[1]]
        node = customers[token[1] - 1]
        if current > node.L + 1e-6:                       # 可接受时间窗硬约束
            return False, None
        a = current
        if ALLOW_EARLY_SERVICE:
            b, w = a, 0.0
        else:
            b = max(a, node.e)                            # 早到等待至期望窗开始
            w = max(0.0, node.e - a)
        if b > node.L + 1e-6:
            return False, None
        current = b + node.service
        arrive[token] = a
        serve[token] = b
        leave[token] = current
        wait += w

    last = route[-2]
    back = current + time_mat[problem.ix(last)][0]
    distance += dist_mat[problem.ix(last)][0]
    drive_time += time_mat[problem.ix(last)][0]
    if back - depart_t > T_MAX + 1e-6:                    # 单趟时长上限
        return False, None
    if distance > MAX_ROUTE_DIST + 1e-6:                  # 续航约束
        return False, None

    cost_fixed = float(spec['fixed'])
    cost_transport = distance * float(spec['unit_cost'])
    cost_cool_deliver = COOL_DELIVER * (drive_time + wait) / 60.0
    cost_cool_service = COOL_SERVICE * serve_min / 60.0
    cost_early = sum(PEN_EARLY * max(0.0, customers[t[1] - 1].e - serve[t]) / 60.0 for t in route[1:-1])
    cost_late = sum(PEN_LATE * max(0.0, serve[t] - customers[t[1] - 1].l) / 60.0 for t in route[1:-1])
    route_cost = cost_fixed + cost_transport + cost_cool_deliver + cost_cool_service + cost_early + cost_late
    info = {
        'vehicle': vtype, 'product': product, 'batch': batch, 'load': demand, 'volume': volume,
        'dist': distance, 'drive_min': drive_time, 'wait_min': wait, 'serve_min': serve_min,
        'cost_fixed': cost_fixed, 'cost_transport': cost_transport,
        'cost_cool_deliver': cost_cool_deliver, 'cost_cool_service': cost_cool_service,
        'cost_early': cost_early, 'cost_late': cost_late, 'route_cost': route_cost,
        'depart': depart_t, 'back': back, 'arrive': arrive, 'serve': serve, 'leave': leave,
    }
    return True, info


# ======================================================================
# 车辆区间调度（多趟次复用）
# ======================================================================
def schedule_vehicles(problem: Problem, solution: List[List]) -> Optional[dict]:
    """带发车窗口的区间调度；配额不足或任一车次不可行返回 None。"""
    items = []
    for route in solution:
        if len(route) <= 2:
            continue
        window = route_time_window(problem, route)
        if window is None:
            return None
        t_min, t_max = window
        product = route[1][0]
        customers = problem.nodes[product]
        demand = sum(customers[t[1] - 1].demand for t in route[1:-1])
        volume = sum(customers[t[1] - 1].volume for t in route[1:-1])
        vtype = problem.pick_vehicle(demand, volume)
        if vtype is None:
            return None
        items.append((vtype, t_min, t_max, product, route))

    result: Dict[str, list] = {name: [] for name in problem.types}
    for vtype in problem.types:
        subset = sorted([it for it in items if it[0] == vtype], key=lambda x: x[1])
        trucks: List[list] = []      # 每辆: [可用时刻, [趟次记录], 绑定品类]
        for _, t_min, t_max, product, route in subset:
            t_min = max(t_min, T0E)                      # 首趟不早于配送中心开始作业
            best = None
            for truck in trucks:
                if CROSS_PRODUCT_REUSE or truck[2] == product:
                    if truck[0] + THREADY <= t_max and (best is None or truck[0] < best[0]):
                        best = truck
            if best is None:
                if len(trucks) >= int(problem.vehicles[vtype]['count']):
                    return None                          # 车型配额不足
                ok, info = evaluate_route(problem, route, earliest=t_min)
                if not ok or info['back'] > T0L + 1e-6:
                    return None
                trucks.append([info['back'], [(info['depart'], info['back'], route, info)], product])
            else:
                # 复用：返场 + 周转后方可再出发；在该下界与窗口上界之间选最优发车时刻
                ok, info = evaluate_route(problem, route, earliest=max(t_min, best[0] + THREADY))
                if not ok or info['back'] > T0L + 1e-6:
                    return None
                best[0] = info['back']
                best[1].append((info['depart'], info['back'], route, info))
        result[vtype] = trucks
    return result


def solution_cost(problem: Problem, solution: List[List]) -> float:
    schedule = schedule_vehicles(problem, solution)
    if schedule is None:
        return float('inf')
    total = 0.0
    for vtype, trucks in schedule.items():
        for truck in trucks:
            for _st, _en, _route, info in truck[1]:
                total += info['route_cost']
        total += float(problem.vehicles[vtype].get('enable') or 0) * len(trucks)
    return total


def vehicle_available(problem: Problem, vtype: str, solution: List[List]) -> bool:
    schedule = schedule_vehicles(problem, solution)
    if schedule is None:
        return False
    return len(schedule[vtype]) < int(problem.vehicles[vtype]['count'])


# ======================================================================
# Clarke-Wright 节约法（初始解，按 品类 × 批次 分组构造）
# ======================================================================
def clarke_wright(problem: Problem, tokens: List[Tuple[str, int]]) -> List[List]:
    groups: Dict[Tuple[str, str], List[Tuple[str, int]]] = defaultdict(list)
    for product, index in tokens:
        groups[(product, problem.nodes[product][index - 1].batch)].append((product, index))

    all_routes: List[List] = []
    for (product, _batch), ids in groups.items():
        routes = [[0, (product, index), 0] for (product, index) in ids]
        dist_mat = problem.dist[product]
        savings = []
        for a in ids:
            for b in ids:
                if a[1] < b[1]:
                    value = dist_mat[0][a[1]] + dist_mat[0][b[1]] - dist_mat[a[1]][b[1]]
                    savings.append((value, a, b))
        savings.sort(reverse=True, key=lambda x: x[0])
        for _value, a, b in savings:
            ra = rb = None
            for route in routes:
                if a in route:
                    ra = route
                if b in route:
                    rb = route
                if ra is not None and rb is not None:
                    break
            if ra is None or rb is None or ra is rb:
                continue
            merged = None
            for left, right in (
                (ra[1:-1], rb[1:-1]),
                (list(reversed(ra[1:-1])), rb[1:-1]),
                (ra[1:-1], list(reversed(rb[1:-1]))),
                (list(reversed(ra[1:-1])), list(reversed(rb[1:-1]))),
            ):
                candidate = [0] + left + right + [0]
                ok, _ = evaluate_route(problem, candidate)
                if ok:
                    merged = candidate
                    break
            if merged is not None:
                routes.remove(ra)
                routes.remove(rb)
                routes.append(merged)
        all_routes.extend(routes)
    return all_routes


# ======================================================================
# ALNS：破坏 / 修复算子
# ======================================================================
def random_removal(solution, rng, n_remove):
    all_customers = [c for route in solution for c in route[1:-1]]
    if len(all_customers) <= n_remove:
        return list(all_customers), None
    return rng.sample(all_customers, n_remove), None


def worst_removal(problem, solution, rng, n_remove):
    removal_cost = {}
    for route in solution:
        ok, info = evaluate_route(problem, route)
        base = info['route_cost'] if ok else float('inf')
        for customer in route[1:-1]:
            shorter = [x for x in route if x != customer]
            if len(shorter) <= 2:
                removal_cost[customer] = base
            else:
                ok2, info2 = evaluate_route(problem, shorter)
                removal_cost[customer] = base - (info2['route_cost'] if ok2 else 0.0)
    ranked = sorted(removal_cost.items(), key=lambda x: x[1], reverse=True)
    n = min(n_remove, len(ranked))
    pool = [c for c, _ in ranked[:max(n * 3, n)]]
    return rng.sample(pool, n), None


def related_removal(problem, solution, rng, n_remove):
    all_customers = [c for route in solution for c in route[1:-1]]
    if not all_customers:
        return [], None
    seed = rng.choice(all_customers)
    removed = [seed]
    while len(removed) < n_remove:
        candidates = []
        for customer in all_customers:
            if customer in removed:
                continue
            related = [problem.dist[customer[0]][customer[1]][other[1]]
                       for other in removed if other[0] == customer[0]]
            if not related:
                continue
            candidates.append((min(related), customer))
        if not candidates:
            break
        candidates.sort(key=lambda x: x[0])
        pool = [c for _, c in candidates[:max(len(candidates) // 3, 1)]]
        removed.append(rng.choice(pool))
    return removed, None


def route_removal(solution, rng, k):
    routes = [r for r in solution if len(r) > 2]
    rng.shuffle(routes)
    removed, picked = [], []
    for route in routes:
        if picked and len(removed) >= max(k, 1):
            break
        removed.extend(route[1:-1])
        picked.append(route)
        if len(removed) >= max(k, 1):
            break
    return removed, picked


def greedy_insertion(problem, removed, solution, rng):
    for customer in removed:
        best_pos = None
        best_delta = float('inf')
        for ri, route in enumerate(solution):
            for pos in range(1, len(route)):
                candidate = route[:pos] + [customer] + route[pos:]
                ok, info = evaluate_route(problem, candidate)
                if not ok:
                    continue
                ok_old, info_old = evaluate_route(problem, route)
                delta = info['route_cost'] - (info_old['route_cost'] if ok_old else 0.0)
                if delta < best_delta:
                    best_delta, best_pos = delta, (ri, pos, candidate)
        if best_pos is None:
            ok, info = evaluate_route(problem, [0, customer, 0])
            if ok and vehicle_available(problem, info['vehicle'], solution):
                solution.append([0, customer, 0])
                continue
            return None
        ri, pos, candidate = best_pos
        solution[ri] = candidate
    return solution


def _choice_cost(problem, choice):
    ri, _pos, candidate = choice
    if ri == 'new':
        return 0.0
    ok, info = evaluate_route(problem, candidate)
    return info['route_cost'] if ok else float('inf')


def regret2_insertion(problem, removed, solution, rng):
    remaining = list(removed)
    while remaining:
        best_customer = None
        best_choice = None
        best_regret = -float('inf')
        for customer in remaining:
            deltas, choices = [], []
            for ri, route in enumerate(solution):
                for pos in range(1, len(route)):
                    candidate = route[:pos] + [customer] + route[pos:]
                    ok, info = evaluate_route(problem, candidate)
                    if not ok:
                        continue
                    ok_old, info_old = evaluate_route(problem, route)
                    deltas.append(info['route_cost'] - (info_old['route_cost'] if ok_old else 0.0))
                    choices.append((ri, pos, candidate))
            if not deltas:
                ok, info = evaluate_route(problem, [0, customer, 0])
                if ok and vehicle_available(problem, info['vehicle'], solution):
                    deltas.append(0.0)
                    choices.append(('new', None, [0, customer, 0]))
            if not deltas:
                return None
            deltas.sort()
            regret = (deltas[1] if len(deltas) > 1 else deltas[0]) - deltas[0]
            if regret > best_regret:
                best_regret = regret
                best_customer = customer
                best_choice = min(choices, key=lambda ch: _choice_cost(problem, ch))
        if best_customer is None:
            return None
        if best_choice[0] == 'new':
            solution.append(best_choice[2])
        else:
            ri, _pos, candidate = best_choice
            solution[ri] = candidate
        remaining.remove(best_customer)
    return solution


def _roulette(weights):
    total = sum(weights)
    x = random.uniform(0, total)
    acc = 0.0
    for index, weight in enumerate(weights):
        acc += weight
        if x <= acc:
            return index
    return len(weights) - 1


def alns(problem: Problem, init_solution: List[List], iterations: int) -> Tuple[List[List], float]:
    solution = [list(r) for r in init_solution if len(r) > 2]
    best = [list(r) for r in solution]
    best_cost = solution_cost(problem, best)
    current = [list(r) for r in best]
    current_cost = best_cost

    destroy_ops = [
        ('random', lambda sol, k: random_removal(sol, random, k)),
        ('worst', lambda sol, k: worst_removal(problem, sol, random, k)),
        ('related', lambda sol, k: related_removal(problem, sol, random, k)),
        ('route', lambda sol, k: route_removal(sol, random, k)),
    ]
    repair_ops = [
        ('greedy', lambda rm, sol: greedy_insertion(problem, rm, sol, random)),
        ('regret', lambda rm, sol: regret2_insertion(problem, rm, sol, random)),
    ]
    w_d = [1.0] * len(destroy_ops)
    w_r = [1.0] * len(repair_ops)
    score_d = [0.0] * len(destroy_ops)
    score_r = [0.0] * len(repair_ops)
    cnt_d = [0] * len(destroy_ops)
    cnt_r = [0] * len(repair_ops)
    n_cust = sum(len(r) - 2 for r in solution)

    for it in range(iterations):
        temperature = max(60.0 * (1.0 / 60.0) ** (it / iterations), 1.0)
        k = max(1, int(random.uniform(0.05, 0.25) * n_cust))
        di = _roulette(w_d)
        ri = _roulette(w_r)
        cnt_d[di] += 1
        cnt_r[ri] += 1
        removed, _ = destroy_ops[di][1](current, k)
        removed_set = set(removed)
        base_solution = []
        for route in current:
            trimmed = [x for x in route if x not in removed_set]
            if len(trimmed) > 2:
                base_solution.append(trimmed)
        new_solution = repair_ops[ri][1](list(removed), base_solution)
        if new_solution is None:
            continue
        new_cost = solution_cost(problem, new_solution)
        delta = new_cost - current_cost
        if new_cost < best_cost:
            best = [list(r) for r in new_solution]
            best_cost = new_cost
            score_d[di] += 1.5
            score_r[ri] += 1.5
        if delta < 0 or random.random() < math.exp(-delta / max(temperature, 1e-6)):
            current = new_solution
            current_cost = new_cost
            if delta < 0:
                score_d[di] += 1.0
                score_r[ri] += 1.0
        if (it + 1) % 100 == 0:
            for j in range(len(destroy_ops)):
                w_d[j] = w_d[j] * 0.8 + 0.2 * (score_d[j] / cnt_d[j] if cnt_d[j] else 1.0)
                score_d[j] = 0.0
                cnt_d[j] = 0
            for j in range(len(repair_ops)):
                w_r[j] = w_r[j] * 0.8 + 0.2 * (score_r[j] / cnt_r[j] if cnt_r[j] else 1.0)
                score_r[j] = 0.0
                cnt_r[j] = 0
    return best, best_cost


# ======================================================================
# 对外入口
# ======================================================================
def solve(problem: Problem, restarts: int = DEFAULT_RESTARTS, iterations: int = DEFAULT_ITER,
          seed: int = DEFAULT_SEED) -> dict:
    """跑 RESTARTS 次（取最优），返回路线、排班与汇总指标。"""
    tokens = [(product, node.index) for product in problem.products for node in problem.nodes[product]]
    best, best_cost = None, float('inf')
    for restart in range(restarts):
        random.seed(seed + restart)
        init = clarke_wright(problem, tokens)
        solution, cost = alns(problem, init, iterations)
        if cost < best_cost:
            best, best_cost = solution, cost
    if best is None or math.isinf(best_cost):
        raise VrpError('未找到满足全部约束（含车辆池配额）的可行解。')

    random.seed(seed)
    schedule = schedule_vehicles(problem, best)
    if schedule is None:
        raise VrpError('方案排班失败（车辆池配额或时间窗冲突）。')

    # 车型内按顺序绑定具体车辆编号与能源类型（用于充电排班与碳排核算）
    vehicle_of = {}
    for vtype, trucks in schedule.items():
        codes = problem.vehicles[vtype]['codes']
        energies = problem.vehicles[vtype]['energy']
        for position, truck in enumerate(trucks):
            code = codes[position % len(codes)]
            energy = energies[position % len(energies)]
            for _st, _en, route, info in truck[1]:
                vehicle_of[id(route)] = (code, energy)

    trips = []
    ontime = total_customers = 0
    counters: Dict[Tuple[str, str], int] = defaultdict(int)
    ordered = []
    for vtype, trucks in schedule.items():
        for truck in trucks:
            for st, en, route, info in truck[1]:
                ordered.append((info['depart'], st, en, route, info, vtype))
    ordered.sort(key=lambda x: (x[0], x[3][1][0]))
    for _depart, st, en, route, info, vtype in ordered:
        product = route[1][0]
        customers = problem.nodes[product]
        batch = customers[route[1][1] - 1].batch
        counters[(product, batch)] += 1
        trip_id = f'{product}-{batch}-T{counters[(product, batch)]}'
        code, energy = vehicle_of.get(id(route), (problem.vehicles[vtype]['codes'][0],
                                                   problem.vehicles[vtype]['energy'][0]))
        sequence = []
        for token in route[1:-1]:
            node = customers[token[1] - 1]
            sequence.append({
                'code': node.code, 'name': node.name,
                'arrival_min': round(info['arrive'][token], 1),
                'service_start_min': round(info['serve'][token], 1),
                'service_end_min': round(info['leave'][token], 1),
                'batch_code': node.batch,
            })
            total_customers += 1
            if node.e - 1e-6 <= info['serve'][token] <= node.l + 1e-6:
                ontime += 1
        trip = {
            'trip_id': trip_id, 'product': product, 'batch_id': batch,
            'customer_codes': [s['code'] for s in sequence], 'sequence': sequence,
            'load_kg': round(info['load'], 2), 'volume_m3': round(info['volume'], 3),
            'distance_km': round_km(info['dist']),
            'duration_min': round(info['back'] - info['depart'], 1),
            'drive_min': round(info['drive_min'], 1), 'wait_min': round(info['wait_min'], 1),
            'service_total_min': round(info['serve_min'], 1),
            'planned_departure_min': int(round(st)), 'back_min': int(round(en)),
            'vehicle_code': code, 'vehicle_type': vtype, 'energy_type': energy,
            'capacity_kg': float(problem.vehicles[vtype]['capacity']),
            'capacity_m3': float(problem.vehicles[vtype]['volume']),
            'load_rate': round(info['load'] / max(1e-6, float(problem.vehicles[vtype]['capacity'])) * 100, 1),
            'cost': {
                'fixed': round(info['cost_fixed'], 2),
                'transport': round(info['cost_transport'], 2),
                'cool_delivery': round(info['cost_cool_deliver'], 2),
                'cool_service': round(info['cost_cool_service'], 2),
                'early': round(info['cost_early'], 2),
                'late': round(info['cost_late'], 2),
                'total': round(info['route_cost'], 2),
            },
        }
        trips.append(trip)

    vehicles_used = sum(len(trucks) for trucks in schedule.values())
    summary = {
        'trips': len(trips),
        'vehicles_used': vehicles_used,
        'reused_trips': max(0, len(trips) - vehicles_used),
        'customers': total_customers,
        'on_time_customers': ontime,
        'on_time_rate': round(ontime / total_customers * 100, 1) if total_customers else 0.0,
        'total_distance_km': round_km(sum(t['distance_km'] for t in trips)),
        'total_kg': round(sum(t['load_kg'] for t in trips), 2),
        'total_m3': round(sum(t['volume_m3'] for t in trips), 3),
        # 论文 F1 + F2 口径的日总配送成本
        'delivery_cost': round(sum(t['cost']['total'] for t in trips), 2),
        'cost_fixed': round(sum(t['cost']['fixed'] for t in trips), 2),
        'cost_transport': round(sum(t['cost']['transport'] for t in trips), 2),
        'cost_cool_delivery': round(sum(t['cost']['cool_delivery'] for t in trips), 2),
        'cost_cool_service': round(sum(t['cost']['cool_service'] for t in trips), 2),
        'cost_early': round(sum(t['cost']['early'] for t in trips), 2),
        'cost_late': round(sum(t['cost']['late'] for t in trips), 2),
        'avg_load_rate': round(sum(t['load_rate'] for t in trips) / len(trips), 1) if trips else 0.0,
        'by_product': {product: {
            'trips': sum(1 for t in trips if t['product'] == product),
            'km': round_km(sum(t['distance_km'] for t in trips if t['product'] == product)),
            'cost': round(sum(t['cost']['total'] for t in trips if t['product'] == product), 2),
        } for product in problem.products},
    }
    return {'trips': trips, 'summary': summary, 'schedule': schedule, 'problem': problem}


__all__ = ['ALLOW_EARLY_SERVICE', 'COOL_DELIVER', 'COOL_SERVICE', 'CROSS_PRODUCT_REUSE',
           'DEFAULT_ITER', 'DEFAULT_RESTARTS', 'DEFAULT_SEED', 'MAX_ROUTE_DIST', 'PEN_EARLY',
           'PEN_LATE', 'Problem', 'SPEED_KMH', 'T0E', 'T0L', 'THREADY', 'T_MAX', 'VrpError',
           'alns', 'build_problem', 'clarke_wright', 'evaluate_route', 'route_time_window',
           'schedule_vehicles', 'solution_cost', 'solve']
