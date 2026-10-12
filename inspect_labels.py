"""盘点界面上暴露的英文标识：参数键、车队、求解器、留痕动作、能源/车型等。"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'platform_v1'
sys.path.insert(0, str(BASE))
from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402

tmp = tempfile.TemporaryDirectory()
settings = build_settings({'PLATFORM_ROOT': str(BASE), 'PLATFORM_DB': str(Path(tmp.name) / 'p.db')})
auth.bootstrap(settings)
conn = db.connect(settings)

print('=== 参数表（key / value / 分组线索）===')
rows = list(conn.execute('SELECT key, value, note FROM params ORDER BY key'))
for row in rows:
    note = (row['note'] or '')[:34]
    print(f"  {row['key']:26s} = {str(row['value']):22s} {note}")
print(f'  共 {len(rows)} 项')

print('\n=== 车队标识 ===')
for row in conn.execute('SELECT fleet_tag, COUNT(*) c FROM vehicles GROUP BY fleet_tag'):
    print(f"  {row['fleet_tag']:10s} {row['c']} 辆")

print('\n=== 产品/能源/车型/角色 ===')
for row in conn.execute('SELECT code, name, unit, category FROM products'):
    print(f"  product {row['code']:8s} {row['name']:8s} {row['unit']} {row['category']}")
for row in conn.execute('SELECT DISTINCT energy_type, vehicle_type FROM vehicles'):
    print(f"  车型 {row['vehicle_type']} / 能源 {row['energy_type']}")
for row in conn.execute('SELECT DISTINCT role FROM users'):
    print(f"  角色 {row['role']}")
for row in conn.execute('SELECT DISTINCT action FROM audit_log LIMIT 12'):
    print(f"  留痕动作 {row['action']}")
conn.close()
