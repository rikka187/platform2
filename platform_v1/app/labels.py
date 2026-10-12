"""界面中文标签层：参数键、车队、求解器、留痕动作、角色、能源与车型。

数据库里保留英文键名（程序要按它取值、也便于与团队参数表对照），
**对外展示一律走这里的映射**，接口把 ``label/unit/note`` 一并下发，前端直接显示中文。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 参数：key -> (中文名称, 单位, 口径说明)
# --------------------------------------------------------------------------
PARAMS: Dict[str, Tuple[str, str, str]] = {
    'avg_speed_kmh': ('平均行驶速度', 'km/h', '表2-2；行驶时间 = 里程 ÷ 该速度'),
    'carbon_price': ('碳价', '元/kg', '碳成本折算用'),
    'charger_count': ('充电桩数量', '个', '第三章配置：3 × 120 kW'),
    'charger_efficiency': ('充电效率', '—', '输入功率到入车电量的转换效率'),
    'charger_power_kw': ('单桩功率', 'kW', '第三章配置'),
    'charger_soc_range': ('充电 SOC 区间', '%', '低于下限补、充到上限停'),
    'dc_lat': ('配送中心纬度', '°', '银犁仓库'),
    'dc_lon': ('配送中心经度', '°', '银犁仓库'),
    'density_ginger_garlic': ('姜蒜折算密度', 't/m³', '重量→体积，用于容积双约束'),
    'density_noodles': ('鲜面条折算密度', 't/m³', '重量→体积，用于容积双约束'),
    'depreciation_years': ('折旧年限', '年', '第三章车辆全生命周期摊销'),
    'diesel_carbon_factor': ('柴油碳排放因子', 'kg/L', '碳排 = 油耗 × 该因子'),
    'diesel_price': ('柴油价格', '元/L', '表2-6 口径'),
    'dock_closed_count': ('封闭式月台数', '个', '仓库月台资源'),
    'dock_open_count': ('开放式月台数', '个', '仓库月台资源'),
    'dock_peak_utilization': ('月台峰值利用率', '%', '装车高峰的可用上限'),
    'energy_medium_ev': ('中型纯电行驶能耗', 'kWh/km', '第三章能耗口径'),
    'energy_medium_fuel': ('中型燃油行驶能耗', 'L/km', '第三章能耗口径'),
    'energy_small_ev': ('小型纯电行驶能耗', 'kWh/km', '第三章能耗口径'),
    'energy_small_fuel': ('小型燃油行驶能耗', 'L/km', '第三章能耗口径'),
    'ev_effective_range_medium': ('中型纯电有效续航', 'km', '已计入冬季衰减与冷机折损'),
    'ev_effective_range_small': ('小型纯电有效续航', 'km', '已计入冬季衰减与冷机折损'),
    'grid_carbon_factor': ('电网碳排放因子', 'kg/kWh', '四川电网口径'),
    'loss_rate_delivery': ('配送过程货损率', '—', '货损成本 = 货值 × 该费率'),
    'loss_rate_service': ('服务过程货损率', '—', '货损成本 = 货值 × 该费率'),
    'maintenance_medium': ('中型车维护费', '元/日', '按日摊销'),
    'maintenance_small': ('小型车维护费', '元/日', '按日摊销'),
    'max_route_duration_min': ('单趟最大时长', '分钟', '模型式(21)'),
    'medium_capacity_kg': ('中型车载重', 'kg', '载重约束'),
    'medium_capacity_m3': ('中型车容积', 'm³', '容积约束'),
    'medium_fixed_cost': ('中型车每趟出车成本', '元/趟', '表2-5“固定成本”按每趟计'),
    'medium_unit_cost': ('中型车单位里程成本', '元/km', '表2-5'),
    'nominal_range_km': ('额定续航', 'km', '表2-1'),
    'operating_days': ('年运营天数', '天', '年口径换算用'),
    'peak_window': ('装车高峰时段', '—', '仓库作业窗口'),
    'penalty_early': ('早到惩罚系数', '元/小时', '表2-6；对标期望时间窗'),
    'penalty_late': ('晚到惩罚系数', '元/小时', '表2-6；对标期望时间窗'),
    'power_flat': ('平段电价', '元/kWh', '第三章充电成本'),
    'power_peak': ('峰段电价', '元/kWh', '第三章充电成本'),
    'power_valley': ('谷段电价', '元/kWh', '第三章充电成本（排班只排谷段）'),
    'price_garlic': ('大蒜单价', '元/kg', '货值→货损成本'),
    'price_ginger': ('生姜单价', '元/kg', '货值→货损成本'),
    'price_noodles': ('鲜面条单价', '元/kg', '货值→货损成本'),
    'purchase_medium_ev': ('中型纯电购置价', '万元', '第三章资产摊销'),
    'purchase_medium_fuel': ('中型燃油购置价', '万元', '第三章资产摊销'),
    'purchase_small_ev': ('小型纯电购置价', '万元', '第三章资产摊销'),
    'purchase_small_fuel': ('小型燃油购置价', '万元', '第三章资产摊销'),
    'reefer_delivery_cost': ('配送阶段制冷系数', '元/小时', '表2-6：行驶 + 等待'),
    'reefer_energy_medium_ev': ('中型纯电制冷能耗', 'kWh/h', '第三章能耗口径'),
    'reefer_energy_medium_fuel': ('中型燃油制冷能耗', 'L/h', '第三章能耗口径'),
    'reefer_energy_small_ev': ('小型纯电制冷能耗', 'kWh/h', '第三章能耗口径'),
    'reefer_energy_small_fuel': ('小型燃油制冷能耗', 'L/h', '第三章能耗口径'),
    'reefer_range_loss': ('制冷导致的续航折损', '%', '有效续航 = 额定续航 ×(1 − 折损)'),
    'reefer_service_cost': ('服务阶段制冷系数', '元/小时', '表2-6：卸货服务期间'),
    'service_radius_km': ('服务半径', 'km', '城配覆盖范围'),
    'small_capacity_kg': ('小型车载重', 'kg', '载重约束'),
    'small_capacity_m3': ('小型车容积', 'm³', '容积约束'),
    'small_fixed_cost': ('小型车每趟出车成本', '元/趟', '表2-5“固定成本”按每趟计'),
    'small_unit_cost': ('小型车单位里程成本', '元/km', '表2-5'),
    'winter_battery_decay': ('冬季电池衰减', '%', '有效续航折算用'),
}

# 参数分组（页面按组展示，便于查找）
PARAM_GROUPS: List[Tuple[str, Tuple[str, ...]]] = [
    ('时间窗与成本系数', ('penalty_early', 'penalty_late', 'reefer_delivery_cost', 'reefer_service_cost',
                    'loss_rate_delivery', 'loss_rate_service', 'avg_speed_kmh', 'max_route_duration_min')),
    ('车辆与运力', ('small_capacity_kg', 'small_capacity_m3', 'small_fixed_cost', 'small_unit_cost',
                'medium_capacity_kg', 'medium_capacity_m3', 'medium_fixed_cost', 'medium_unit_cost',
                'nominal_range_km', 'service_radius_km')),
    ('新能源与充电', ('charger_count', 'charger_power_kw', 'charger_efficiency', 'charger_soc_range',
                 'power_valley', 'power_flat', 'power_peak', 'ev_effective_range_small',
                 'ev_effective_range_medium', 'reefer_range_loss', 'winter_battery_decay')),
    ('能耗与碳排', ('energy_small_fuel', 'energy_medium_fuel', 'energy_small_ev', 'energy_medium_ev',
                'reefer_energy_small_fuel', 'reefer_energy_medium_fuel', 'reefer_energy_small_ev',
                'reefer_energy_medium_ev', 'diesel_price', 'diesel_carbon_factor',
                'grid_carbon_factor', 'carbon_price')),
    ('货物与品类', ('density_ginger_garlic', 'density_noodles', 'price_garlic', 'price_ginger',
                'price_noodles')),
    ('仓库与月台', ('dock_open_count', 'dock_closed_count', 'dock_peak_utilization', 'peak_window',
                'dc_lon', 'dc_lat')),
    ('第三章资产口径', ('purchase_small_fuel', 'purchase_medium_fuel', 'purchase_small_ev',
                  'purchase_medium_ev', 'depreciation_years', 'maintenance_small',
                  'maintenance_medium', 'operating_days')),
]

# 车队口径
FLEETS: Dict[str, str] = {
    'baseline': '现状 10 辆（8 小 + 2 中，全燃油）',
    'c3': 'C3 推荐 8 辆（6 纯电 + 2 保留燃油）',
    'c3_10': 'C3 替换 10 辆（6 纯电 + 4 保留燃油）',
    'c4': 'C4 升级 10 辆（8 纯电 + 2 保留燃油）',
}

# 求解器
SOLVERS: Dict[str, str] = {
    'auto': '自动（优先 CP-SAT）',
    'heuristic': '启发式',
    'cpsat': 'CP-SAT',
    'cp-sat': 'CP-SAT',
    'cp_sat': 'CP-SAT',
    'alns': '论文口径 ALNS',
    'alns-paper': '论文口径 ALNS（论文基准算例）',
    'alns-case2': '论文口径 ALNS（表2-3/2-4 真实窗口）',
}

# 情景
SCENARIOS: Dict[str, str] = {
    'S0': '人工调度基准', 'S1': '仅路径优化', 'S2': '路径优化 + 车辆新能源',
    'S3': '路径优化 + 新能源 + 仓配月台协同',
    'C0': '全燃油配置', 'C3': '推荐方案', 'C4': '备选升级',
    'H0': '人工调度基准（求解器口径）', 'D3': '仓配协同（求解器口径）',
}

# 留痕动作
ACTIONS: Dict[str, str] = {
    'login.ok': '登录成功', 'login.fail': '登录失败', 'logout': '退出登录',
    'password.change': '修改口令', 'user.bootstrap': '初始化管理员', 'user.create': '新增用户',
    'user.update': '修改用户', 'user.delete': '删除用户',
    'customer.create': '新增客户', 'customer.update': '修改客户', 'customer.delete': '删除客户',
    'customer.import': '导入客户', 'customer.export': '导出客户',
    'vehicle.create': '新增车辆', 'vehicle.update': '修改车辆', 'vehicle.delete': '删除车辆',
    'params.update': '修改参数', 'geo.check': '距离矩阵校核',
    'plan.run': '生成调度方案', 'plan.export': '导出方案报表',
    'monitor.ack': '预警处置',
}

# 角色
ROLES: Dict[str, str] = {
    'admin': '系统管理员', 'dispatcher': '调度员', 'warehouse': '仓储',
    'fleet': '车队', 'viewer': '只读',
}

# 车辆能源与车型（数据库已是中文，这里兜底英文写法）
ENERGY: Dict[str, str] = {'纯电': '纯电', '燃油': '燃油', 'ev': '纯电', 'fuel': '燃油',
                          'electric': '纯电', 'diesel': '燃油'}
VEHICLE_TYPES: Dict[str, str] = {'小型': '小型', '中型': '中型', 'small': '小型', 'medium': '中型'}

# 监控指标与状态
METRICS: Dict[str, str] = {
    '温度': '厢温', '电量': '电量', '油量': '油量', '延误': '延误', '月台': '月台占用',
}


def param_label(key: str) -> dict:
    name, unit, note = PARAMS.get(key, (key, '', ''))
    return {'key': key, 'label': name, 'unit': unit, 'note': note}


def param_group(key: str) -> str:
    for title, keys in PARAM_GROUPS:
        if key in keys:
            return title
    return '其它参数'


def fleet_label(tag: Optional[str]) -> str:
    return FLEETS.get(str(tag or ''), str(tag or ''))


def solver_label(name: Optional[str]) -> str:
    text = str(name or '')
    if text in SOLVERS:
        return SOLVERS[text]
    for key, label in SOLVERS.items():
        if text.startswith(key):
            return label
    return text


def action_label(action: Optional[str]) -> str:
    text = str(action or '')
    return ACTIONS.get(text, text)


def role_label(role: Optional[str]) -> str:
    return ROLES.get(str(role or ''), str(role or ''))


def scenario_label(code: Optional[str]) -> str:
    return SCENARIOS.get(str(code or ''), str(code or ''))


def metric_label(metric: Optional[str]) -> str:
    return METRICS.get(str(metric or ''), str(metric or ''))


__all__ = ['ACTIONS', 'ENERGY', 'FLEETS', 'METRICS', 'PARAMS', 'PARAM_GROUPS', 'ROLES',
           'SCENARIOS', 'SOLVERS', 'VEHICLE_TYPES', 'action_label', 'fleet_label', 'metric_label',
           'param_group', 'param_label', 'role_label', 'scenario_label', 'solver_label']
