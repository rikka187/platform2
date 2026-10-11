"""从选题案例2的原始数据文件生成平台种子数据（data/case2/*.csv）。

用法：
    python tools/build_case2_data.py [源目录]

默认源目录为团队桌面上的 物流设计大赛 文件夹。生成结果写入项目内
``data/case2/``，随交付包一起上传服务器，因此服务器上不依赖这些原始文件。

数据来源：
    客户：my work/第四章/姜蒜客户需求点数据.csv（= 选题表2-3）
          my work/第四章/鲜面条客户需求点数据.csv（= 选题表2-4）
    车辆：选题表2-5 + 第三章新能源参数
    参数：选题表2-6 + 第三章能源与碳排参数
"""
from __future__ import annotations

import csv
import io
import re
import sys
from collections import OrderedDict
from pathlib import Path

DEFAULT_SRC = Path(r'C:\Users\rikka\Desktop\物流设计大赛')
OUT_DIR = Path(__file__).resolve().parents[1] / 'data' / 'case2'

GINGER_CSV = Path('my work') / '第四章' / '姜蒜客户需求点数据.csv'
NOODLE_CSV = Path('my work') / '第四章' / '鲜面条客户需求点数据.csv'


def read_rows(path: Path) -> list:
    raw = path.read_bytes()
    encoding = 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'gb18030'
    return [row for row in csv.reader(io.StringIO(raw.decode(encoding))) if any(c.strip() for c in row)]


def split_window(text: str):
    """'07:00-08:00' -> ('07:00', '08:00')"""
    parts = re.split(r'[-—~]', text.strip())
    if len(parts) != 2:
        raise ValueError(f'时间窗格式无法解析：{text!r}')
    return parts[0].strip(), parts[1].strip()


def parse_batch(text: str):
    """'批次一（04:30起送）' -> ('批次一', '04:30')"""
    text = text.strip()
    name = re.match(r'([^（(]+)', text)
    time = re.search(r'(\d{1,2}:\d{2})', text)
    return (name.group(1).strip() if name else text), (time.group(1) if time else '')


def build_customers(src: Path) -> tuple:
    """返回 (姜蒜客户行, 鲜面条客户行)"""
    gg_rows = read_rows(src / GINGER_CSV)
    nm_rows = read_rows(src / NOODLE_CSV)

    gg_out = []
    for row in gg_rows[1:]:
        code, lon, lat = row[0].strip(), row[1].strip(), row[2].strip()
        if '配送中心' in code or not lon or not lat or len(row) < 10 or not row[6].strip():
            continue            # 配送中心行 / 空行
        exp_start, exp_end = split_window(row[6])
        acc_start, acc_end = split_window(row[7])
        batch, depart = parse_batch(row[9]) if len(row) > 9 else ('', '')
        gg_out.append(OrderedDict(
            customer_id=re.sub(r'\D', '', code) or code,
            name=f'姜蒜客户{re.sub(r"[^0-9]", "", code)}',
            lon=lon, lat=lat,
            garlic_kg=row[3].strip(), ginger_kg=row[4].strip(),
            total_kg=row[5].strip(),
            exp_start=exp_start, exp_end=exp_end,
            acc_start=acc_start, acc_end=acc_end,
            service_min=row[8].strip(),
            batch=batch, batch_depart=depart,
            customer_type='', region='',
        ))

    nm_out = []
    for row in nm_rows[1:]:
        code, lon, lat = row[0].strip(), row[1].strip(), row[2].strip()
        if '配送中心' in code or not lon or not lat or len(row) < 8 or not row[4].strip():
            continue
        exp_start, exp_end = split_window(row[4])
        acc_start, acc_end = split_window(row[5])
        batch, depart = parse_batch(row[7]) if len(row) > 7 else ('', '')
        nm_out.append(OrderedDict(
            customer_id=re.sub(r'\D', '', code) or code,
            name=f'鲜面条客户{re.sub(r"[^0-9]", "", code)}',
            lon=lon, lat=lat,
            demand_kg=row[3].strip(),
            exp_start=exp_start, exp_end=exp_end,
            acc_start=acc_start, acc_end=acc_end,
            service_min=row[6].strip(),
            batch=batch, batch_depart=depart,
            customer_type='', region='',
        ))
    return gg_out, nm_out


PRODUCTS = [
    # code, name, category, unit_price, density, temp_min, temp_max, mix_group, loss_rate
    ('ginger', '生姜', '姜蒜', '3.0', '0.5', '0', '15', 'ginger_garlic', '0.008'),
    ('garlic', '大蒜', '姜蒜', '3.2', '0.5', '0', '15', 'ginger_garlic', '0.008'),
    ('noodles', '鲜面条', '鲜面条', '3.5', '0.35', '0', '4', 'noodles', '0.008'),
]

SMALL = dict(capacity_kg='1500', capacity_m3='15', box_size='4.05*2.10*2.10',
             fixed_cost='200', unit_cost='2.5', maintenance_cost='40')
MEDIUM = dict(capacity_kg='7000', capacity_m3='38', box_size='7.40*2.40*2.45',
              fixed_cost='400', unit_cost='4.0', maintenance_cost='140')


def vehicle_rows():
    """两套车队：baseline（现状 10 辆燃油）与 c3（第三章新能源替换方案）。"""
    rows = []

    def add(fleet, code, name, vtype, energy, spec, range_nominal, eff_range, energy_use, reefer_use, price):
        rows.append(OrderedDict(
            fleet_tag=fleet, code=code, name=name, vehicle_type=vtype, energy_type=energy,
            capacity_kg=spec['capacity_kg'], capacity_m3=spec['capacity_m3'], box_size=spec['box_size'],
            fixed_cost=spec['fixed_cost'], unit_cost=spec['unit_cost'],
            maintenance_cost=spec['maintenance_cost'],
            range_km=range_nominal, effective_range_km=eff_range,
            energy_consumption=energy_use, reefer_consumption=reefer_use,
            purchase_price_wan=price, available='1',
        ))

    # 现状车队：8 小型 + 2 中型，全燃油
    for i in range(1, 9):
        add('baseline', f'S{i}', f'小型冷藏车{i}', '小型', '燃油', SMALL, '600', '600', '0.118 L/km', '0.4 L/h', '12')
    for i in range(1, 3):
        add('baseline', f'M{i}', f'中型冷藏车{i}', '中型', '燃油', MEDIUM, '600', '600', '0.166 L/km', '0.67 L/h', '22')

    # C3 新能源替换：6 纯电（5 小 1 中）+ 2 保留燃油（中型 M1、小型 S4）
    for i in range(1, 6):
        add('c3', f'ES{i}', f'纯电小型冷藏车{i}', '小型', '纯电', SMALL, '200', '135', '0.42 kWh/km', '1.4 kWh/h', '15')
    add('c3', 'EM1', '纯电中型冷藏车1', '中型', '纯电', MEDIUM, '280', '189', '1.96 kWh/km', '4 kWh/h', '35')
    add('c3', 'M1', '保留燃油中型车', '中型', '燃油', MEDIUM, '600', '600', '0.166 L/km', '0.67 L/h', '22')
    add('c3', 'S4', '保留燃油小型车', '小型', '燃油', SMALL, '600', '600', '0.118 L/km', '0.4 L/h', '12')
    return rows


PARAMS = [
    # key, value, unit, category, source
    ('dc_lon', '104.26', '度', '配送中心', '选题表2-3/2-4'),
    ('dc_lat', '30.85', '度', '配送中心', '选题表2-3/2-4'),
    ('service_radius_km', '45', 'km', '配送中心', '选题2.3.2'),
    ('avg_speed_kmh', '40', 'km/h', '行驶', '选题表2-6'),
    ('peak_window', '03:00-07:00', '-', '仓储月台', '选题2.3.2'),
    ('dock_open_count', '252', '个', '仓储月台', '选题2.3.2'),
    ('dock_closed_count', '24', '个', '仓储月台', '选题2.3.2'),
    ('dock_peak_utilization', '85', '%', '仓储月台', '选题2.3.2'),
    ('reefer_delivery_cost', '8.5', '元/小时', '制冷成本', '选题表2-6'),
    ('reefer_service_cost', '10', '元/小时', '制冷成本', '选题表2-6'),
    ('penalty_early', '5', '元/小时', '时间窗惩罚', '选题表2-6'),
    ('penalty_late', '15', '元/小时', '时间窗惩罚', '选题表2-6'),
    ('loss_rate_delivery', '0.008', '-', '货损', '选题表2-6'),
    ('loss_rate_service', '0.0016', '-', '货损', '选题表2-6'),
    ('price_ginger', '3.0', '元/kg', '货值', '选题表2-6'),
    ('price_garlic', '3.2', '元/kg', '货值', '选题表2-6'),
    ('price_noodles', '3.5', '元/kg', '货值', '选题表2-6'),
    ('density_ginger_garlic', '0.5', 't/m³', '装载换算', '系统默认假设（可改）'),
    ('density_noodles', '0.35', 't/m³', '装载换算', '系统默认假设（可改）'),
    ('small_capacity_kg', '1500', 'kg', '车辆', '选题表2-5'),
    ('small_capacity_m3', '15', 'm³', '车辆', '选题表2-5'),
    ('small_fixed_cost', '200', '元/趟', '车辆', '选题表2-5'),
    ('small_unit_cost', '2.5', '元/km', '车辆', '选题表2-5'),
    ('medium_capacity_kg', '7000', 'kg', '车辆', '选题表2-5'),
    ('medium_capacity_m3', '38', 'm³', '车辆', '选题表2-5'),
    ('medium_fixed_cost', '400', '元/趟', '车辆', '选题表2-5'),
    ('medium_unit_cost', '4.0', '元/km', '车辆', '选题表2-5'),
    ('nominal_range_km', '600', 'km', '车辆', '选题表2-5'),
    ('winter_battery_decay', '25', '%', '新能源', '第三章'),
    ('reefer_range_loss', '10', '%', '新能源', '第三章'),
    ('ev_effective_range_small', '135', 'km', '新能源', '第三章（已计入衰减与冷机折损）'),
    ('ev_effective_range_medium', '189', 'km', '新能源', '第三章（已计入衰减与冷机折损）'),
    ('energy_small_fuel', '0.118', 'L/km', '能耗', '第三章'),
    ('energy_small_ev', '0.42', 'kWh/km', '能耗', '第三章'),
    ('energy_medium_fuel', '0.166', 'L/km', '能耗', '第三章'),
    ('energy_medium_ev', '1.96', 'kWh/km', '能耗', '第三章'),
    ('reefer_energy_small_fuel', '0.4', 'L/h', '能耗', '第三章'),
    ('reefer_energy_small_ev', '1.4', 'kWh/h', '能耗', '第三章'),
    ('reefer_energy_medium_fuel', '0.67', 'L/h', '能耗', '第三章'),
    ('reefer_energy_medium_ev', '4', 'kWh/h', '能耗', '第三章'),
    ('diesel_price', '6.5', '元/L', '能源价格', '第三章'),
    ('power_valley', '0.256', '元/kWh', '能源价格', '四川分时电价（低谷 01:00-07:00）'),
    ('power_flat', '0.653', '元/kWh', '能源价格', '四川分时电价'),
    ('power_peak', '1.043', '元/kWh', '能源价格', '四川分时电价'),
    ('diesel_carbon_factor', '2.63', 'kgCO2/L', '碳排', '第三章'),
    ('grid_carbon_factor', '0.1564', 'kgCO2/kWh', '碳排', '4.2 表4-3'),
    ('carbon_price', '0.095', '元/kg', '碳排', '上海环交所 CEA'),
    ('charger_count', '3', '个', '充电', '第三章 C3'),
    ('charger_power_kw', '120', 'kW', '充电', '第三章 C3'),
    ('charger_efficiency', '0.95', '-', '充电', '第三章'),
    ('charger_soc_range', '20-80', '%', '充电', '第三章'),
    ('depreciation_years', '5', '年', '折旧', '第三章'),
    ('operating_days', '300', '天', '折旧', '第三章'),
    ('purchase_small_fuel', '12', '万元', '购置', '第三章'),
    ('purchase_small_ev', '15', '万元', '购置', '第三章'),
    ('purchase_medium_fuel', '22', '万元', '购置', '第三章'),
    ('purchase_medium_ev', '35', '万元', '购置', '第三章'),
    ('maintenance_small', '40', '元/日', '运营', '第三章'),
    ('maintenance_medium', '140', '元/日', '运营', '第三章'),
    ('max_route_duration_min', '600', '分钟', '行驶', '第二章模型式(21)'),
]


def write_csv(path: Path, rows: list, header=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise SystemExit(f'没有数据可写入 {path}')
    header = header or list(rows[0].keys())
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f'  {path.name}: {len(rows)} 行')


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not (src / GINGER_CSV).is_file():
        print(f'找不到源数据：{src / GINGER_CSV}', file=sys.stderr)
        print('用法：python tools/build_case2_data.py <选题数据目录>', file=sys.stderr)
        return 1

    print(f'源目录：{src}')
    print(f'输出目录：{OUT_DIR}')
    gg, nm = build_customers(src)

    # 批次字典
    batches = OrderedDict()
    for row, scope in [(r, '姜蒜') for r in gg] + [(r, '鲜面条') for r in nm]:
        key = (row['batch'], row['batch_depart'], scope)
        batches[key] = True

    write_csv(OUT_DIR / 'customers_ginger_garlic.csv', gg)
    write_csv(OUT_DIR / 'customers_noodles.csv', nm)
    write_csv(OUT_DIR / 'vehicles.csv', vehicle_rows())

    product_rows = [OrderedDict(code=c, name=n, category=cat, unit_price=p, density_t_per_m3=d,
                                temp_min=t0, temp_max=t1, mix_group=m, loss_rate=lr)
                    for c, n, cat, p, d, t0, t1, m, lr in PRODUCTS]
    write_csv(OUT_DIR / 'products.csv', product_rows)

    param_rows = [OrderedDict(key=k, value=v, unit=u, category=cat, source=s) for k, v, u, cat, s in PARAMS]
    write_csv(OUT_DIR / 'params.csv', param_rows)

    batch_rows = [OrderedDict(code=code, product_scope=scope, depart_after=depart)
                  for (code, depart, scope) in batches]
    write_csv(OUT_DIR / 'batches.csv', batch_rows)

    print(f'\n客户合计：姜蒜 {len(gg)} + 鲜面条 {len(nm)} = {len(gg) + len(nm)}')
    print('完成。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
