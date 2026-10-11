"""数据层与登录权限的回归测试（案例2 种子数据 + 用户/会话/留痕）。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = build_settings({
            'PLATFORM_ROOT': str(ROOT),
            'PLATFORM_DB': str(Path(self.tmp.name) / 'platform.db'),
        })
        db.init_db(self.settings)
        self.conn = db.connect(self.settings)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestSeedData(BaseCase):
    def test_case2_seed_counts(self):
        counts = db.table_counts(self.conn)
        self.assertEqual(counts['customers'], 60)
        self.assertEqual(counts['customer_demand'], 90)   # 姜蒜 30×2 + 鲜面条 30×1
        # baseline 10 + c3 8 + c3_10 10 + c4 10（第三章 C0/C3/C4 口径并存）
        self.assertEqual(counts['vehicles'], 38)
        self.assertEqual(counts['products'], 3)
        self.assertEqual(counts['params'], 60)

    def test_ginger_customer_matches_table_2_3(self):
        rows = db.list_customers(self.conn, '姜蒜')
        self.assertEqual(len(rows), 30)
        first = rows[0]
        self.assertEqual(first['code'], '1')
        self.assertAlmostEqual(first['lon'], 104.09, places=2)
        self.assertAlmostEqual(first['lat'], 30.69, places=2)
        self.assertEqual(first['exp_start'], 7 * 60)      # 07:00
        self.assertEqual(first['exp_end'], 8 * 60)        # 08:00
        self.assertEqual(first['acc_start'], 6 * 60 + 30)  # 06:30
        self.assertEqual(first['acc_end'], 8 * 60 + 30)    # 08:30
        self.assertEqual(first['service_min'], 15)
        self.assertEqual(first['batch_code'], '批次一')
        self.assertAlmostEqual(first['total_kg'], 110.0)   # 大蒜80 + 生姜30

    def test_noodle_customer_matches_table_2_4(self):
        rows = db.list_customers(self.conn, '鲜面条')
        self.assertEqual(len(rows), 30)
        first = rows[0]
        self.assertEqual(first['code'], '1')
        self.assertAlmostEqual(first['lon'], 104.19, places=2)
        self.assertAlmostEqual(first['lat'], 30.83, places=2)
        self.assertEqual(first['exp_start'], 5 * 60 + 30)   # 05:30
        self.assertEqual(first['exp_end'], 6 * 60 + 30)     # 06:30
        self.assertEqual(first['service_min'], 20)
        self.assertEqual(first['batch_code'], '批次二')
        self.assertAlmostEqual(first['total_kg'], 70.0)

    def test_volume_uses_category_density(self):
        """姜蒜客户1：大蒜80kg + 生姜30kg，密度 0.5 t/m³ -> 0.16 + 0.06 = 0.22 m³"""
        rows = db.list_customers(self.conn, '姜蒜')
        self.assertAlmostEqual(rows[0]['total_m3'], 0.22, places=3)
        noodles = db.list_customers(self.conn, '鲜面条')
        # 鲜面条客户1：70kg / 0.35 t/m³ = 0.2 m³
        self.assertAlmostEqual(noodles[0]['total_m3'], 0.2, places=3)

    def test_vehicle_fleets(self):
        baseline = db.list_vehicles(self.conn, 'baseline')
        c3 = db.list_vehicles(self.conn, 'c3')
        self.assertEqual(len(baseline), 10)
        self.assertEqual(len(c3), 8)
        small = [v for v in baseline if v['vehicle_type'] == '小型']
        medium = [v for v in baseline if v['vehicle_type'] == '中型']
        self.assertEqual(len(small), 8)
        self.assertEqual(len(medium), 2)
        self.assertEqual(small[0]['capacity_kg'], 1500)
        self.assertEqual(small[0]['capacity_m3'], 15)
        self.assertEqual(small[0]['fixed_cost'], 200)
        self.assertEqual(small[0]['unit_cost'], 2.5)
        self.assertEqual(medium[0]['capacity_kg'], 7000)
        self.assertEqual(medium[0]['fixed_cost'], 400)
        ev = [v for v in c3 if v['energy_type'] == '纯电']
        fuel = [v for v in c3 if v['energy_type'] == '燃油']
        self.assertEqual(len(ev), 6)
        self.assertEqual(len(fuel), 2)
        self.assertTrue(all(v['effective_range_km'] < v['range_km'] for v in ev))

    def test_params_from_tables(self):
        params = db.get_params(self.conn)
        self.assertEqual(params['reefer_delivery_cost']['value'], '8.5')
        self.assertEqual(params['reefer_service_cost']['value'], '10')
        self.assertEqual(params['penalty_early']['value'], '5')
        self.assertEqual(params['penalty_late']['value'], '15')
        self.assertEqual(params['avg_speed_kmh']['value'], '40')
        self.assertEqual(params['dc_lon']['value'], '104.26')
        self.assertEqual(params['density_noodles']['value'], '0.35')

    def test_products(self):
        rows = {row['code']: dict(row) for row in self.conn.execute('SELECT * FROM products')}
        self.assertEqual(rows['ginger']['unit_price'], 3.0)
        self.assertEqual(rows['garlic']['unit_price'], 3.2)
        self.assertEqual(rows['noodles']['unit_price'], 3.5)
        self.assertEqual(rows['noodles']['mix_group'], 'noodles')
        self.assertEqual(rows['garlic']['mix_group'], 'ginger_garlic')

    def test_seed_is_idempotent(self):
        before = db.table_counts(self.conn)
        db.init_db(self.settings)
        after = db.table_counts(self.conn)
        self.assertEqual(before['customers'], after['customers'])
        self.assertEqual(before['vehicles'], after['vehicles'])


class TestAuth(BaseCase):
    def test_bootstrap_admin_requires_password_change(self):
        user = auth.ensure_default_admin(self.conn)
        self.assertIsNotNone(user)
        self.assertEqual(user['role'], auth.ROLE_ADMIN)
        self.assertEqual(user['must_change_password'], 1)
        # 第二次调用不再创建
        self.assertIsNone(auth.ensure_default_admin(self.conn))

    def test_login_success_and_failure(self):
        auth.ensure_default_admin(self.conn)
        user = auth.authenticate(self.conn, 'admin', 'admin123')
        self.assertEqual(user['username'], 'admin')
        self.assertIsNotNone(user['last_login_at'])
        with self.assertRaises(auth.AuthError):
            auth.authenticate(self.conn, 'admin', 'wrong-password')
        actions = [row['action'] for row in self.conn.execute('SELECT action FROM audit_log')]
        self.assertIn('login.ok', actions)
        self.assertIn('login.fail', actions)

    def test_session_lifecycle(self):
        user = auth.ensure_default_admin(self.conn)
        token = auth.create_session(self.conn, user['id'], ip='127.0.0.1')
        current = auth.get_session_user(self.conn, token)
        self.assertEqual(current['username'], 'admin')
        auth.delete_session(self.conn, token)
        self.assertIsNone(auth.get_session_user(self.conn, token))

    def test_expired_session_is_rejected(self):
        user = auth.ensure_default_admin(self.conn)
        token = auth.create_session(self.conn, user['id'], hours=-1)
        self.assertIsNone(auth.get_session_user(self.conn, token))

    def test_password_policy(self):
        auth.ensure_default_admin(self.conn)
        with self.assertRaises(auth.AuthError):
            auth.create_user(self.conn, 'short', 'abc123', auth.ROLE_VIEWER)
        with self.assertRaises(auth.AuthError):
            auth.create_user(self.conn, 'alpha', 'abcdefgh', auth.ROLE_VIEWER)
        with self.assertRaises(auth.AuthError):
            auth.create_user(self.conn, 'badrole', 'abcd1234', 'superuser')

    def test_role_permissions(self):
        self.assertTrue(auth.has_permission(auth.ROLE_ADMIN, 'anything.at.all'))
        self.assertTrue(auth.has_permission(auth.ROLE_DISPATCHER, 'customers.edit'))
        self.assertFalse(auth.has_permission(auth.ROLE_VIEWER, 'customers.edit'))
        self.assertTrue(auth.has_permission(auth.ROLE_VIEWER, 'customers.view'))
        self.assertTrue(auth.has_permission(auth.ROLE_FLEET, 'vehicles.edit'))
        self.assertFalse(auth.has_permission(auth.ROLE_FLEET, 'customers.edit'))
        self.assertTrue(auth.has_permission(auth.ROLE_WAREHOUSE, 'docks.edit'))

    def test_require_raises_for_missing_permission(self):
        with self.assertRaises(auth.AuthError) as ctx:
            auth.require({'id': 2, 'username': 'v', 'role': auth.ROLE_VIEWER}, 'customers.edit')
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(auth.AuthError):
            auth.require(None, 'customers.view')

    def test_user_management_writes_audit(self):
        admin = auth.ensure_default_admin(self.conn)
        created = auth.create_user(self.conn, 'dispatcher1', 'abc12345', auth.ROLE_DISPATCHER,
                                   '调度小王', operator=admin)
        self.assertEqual(created['role'], auth.ROLE_DISPATCHER)
        auth.set_role(self.conn, created['id'], auth.ROLE_FLEET, operator=admin)
        auth.set_active(self.conn, created['id'], False, operator=admin)
        auth.set_password(self.conn, created['id'], 'xyz98765', operator=admin)
        actions = [row['action'] for row in self.conn.execute('SELECT action FROM audit_log')]
        for expected in ('user.create', 'user.role', 'user.active', 'user.password'):
            self.assertIn(expected, actions)

    def test_disabled_user_cannot_login(self):
        admin = auth.ensure_default_admin(self.conn)
        user = auth.create_user(self.conn, 'temp', 'abc12345', auth.ROLE_VIEWER, operator=admin)
        auth.set_active(self.conn, user['id'], False, operator=admin)
        with self.assertRaises(auth.AuthError):
            auth.authenticate(self.conn, 'temp', 'abc12345')


if __name__ == '__main__':
    unittest.main()
