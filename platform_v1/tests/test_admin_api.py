"""管理类接口回归测试：登录会话、客户/车辆/参数/用户 CRUD、留痕、距离校核。"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import api, auth, db  # noqa: E402
from app.config import build_settings  # noqa: E402


def call(method, target, payload=None, settings=None, cookie=None, raw_body=None):
    body = raw_body if raw_body is not None else (
        json.dumps(payload).encode('utf-8') if payload is not None else b'')
    headers = {'Cookie': cookie} if cookie else {}
    response = api.handle_api(method, target, body, settings, headers)
    return response


class AdminApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = build_settings({
            'PLATFORM_ROOT': str(ROOT),
            'PLATFORM_DB': str(Path(self.tmp.name) / 'platform.db'),
        })
        auth.bootstrap(self.settings)
        self.cookie = self.login('admin', 'admin123')
        self.admin_cookie = self.cookie

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username, password):
        response = call('POST', '/api/auth/login', {'username': username, 'password': password},
                        self.settings)
        self.assertEqual(response.status, 200, response.payload)
        raw = response.headers.get('Set-Cookie', '')
        match = re.search(r'platform_session=([^;]+)', raw)
        self.assertTrue(match, f'登录未下发会话 Cookie：{raw}')
        return f'platform_session={match.group(1)}'


class TestLogin(AdminApiCase):
    def test_login_me_logout(self):
        me = call('GET', '/api/auth/me', settings=self.settings, cookie=self.cookie)
        self.assertEqual(me.status, 200)
        self.assertEqual(me.payload['user']['username'], 'admin')
        self.assertTrue(me.payload['must_change_password'])
        self.assertIn('*', me.payload['permissions'])

        out = call('POST', '/api/auth/logout', settings=self.settings, cookie=self.cookie)
        self.assertEqual(out.status, 200)
        self.assertIn('Max-Age=0', out.headers['Set-Cookie'])

    def test_me_without_session(self):
        response = call('GET', '/api/auth/me', settings=self.settings)
        self.assertEqual(response.status, 401)

    def test_login_failure(self):
        response = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'nope'}, self.settings)
        self.assertEqual(response.status, 401)
        self.assertIn('用户名或口令错误', response.payload['error'])

    def test_change_password(self):
        response = call('POST', '/api/auth/password',
                        {'old_password': 'admin123', 'new_password': 'abc12345'},
                        self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 200)
        self.login('admin', 'abc12345')
        # 原口令失效
        old = call('POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'}, self.settings)
        self.assertEqual(old.status, 401)
        # 改密后 must_change 标记清除
        me = call('GET', '/api/auth/me', settings=self.settings, cookie=self.login('admin', 'abc12345'))
        self.assertFalse(me.payload['must_change_password'])

    def test_endpoints_require_login(self):
        for method, path in (('GET', '/api/customers'), ('GET', '/api/vehicles'),
                             ('GET', '/api/params'), ('GET', '/api/users')):
            response = call(method, path, settings=self.settings)
            self.assertEqual(response.status, 401, f'{path} 未登录时应 401')


class TestCustomers(AdminApiCase):
    def test_list_and_summary(self):
        response = call('GET', '/api/customers', settings=self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.payload['items']), 60)
        self.assertEqual(response.payload['summary']['by_scope'], {'姜蒜': 30, '鲜面条': 30})
        scoped = call('GET', '/api/customers?scope=姜蒜', settings=self.settings, cookie=self.cookie)
        self.assertEqual(len(scoped.payload['items']), 30)

    def test_keyword_search(self):
        response = call('GET', '/api/customers?keyword=姜蒜客户1', settings=self.settings, cookie=self.cookie)
        self.assertGreaterEqual(len(response.payload['items']), 1)

    def test_create_update_delete_with_geo_check(self):
        created = call('POST', '/api/customers', {
            'code': '900', 'product_scope': '鲜面条', 'lon': 104.20, 'lat': 30.80,
            'name': '测试客户', 'service_min': 15, 'exp_start': '06:00', 'exp_end': '07:00',
            'acc_start': '05:30', 'acc_end': '07:30', 'batch_code': '批次二',
            'demands': [{'product_code': 'noodles', 'demand_kg': 50}],
        }, self.settings, cookie=self.cookie)
        self.assertEqual(created.status, 200, created.payload)
        item = created.payload['item']
        self.assertEqual(item['exp_start'], 360)
        self.assertAlmostEqual(item['demands'][0]['volume_m3'], 0.143, places=3)
        # 新增客户不在既有矩阵里，应被列为 extra，而不是判为"距离不一致"
        check = created.payload['geo_check']
        self.assertIn('900', check['extra_customers'])
        self.assertTrue(check['consistent'])

        # 改动**既有**客户坐标 → 距离校核必须报出不一致
        target = next(row for row in call('GET', '/api/customers?scope=鲜面条', settings=self.settings,
                                          cookie=self.cookie).payload['items'] if row['code'] == '1')
        moved = call('PATCH', f"/api/customers/{target['id']}", {'lon': 104.30, 'lat': 30.90},
                     self.settings, cookie=self.cookie)
        self.assertEqual(moved.status, 200)
        self.assertFalse(moved.payload['geo_check']['consistent'])
        self.assertGreater(moved.payload['geo_check']['mismatch_count'], 0)

        # 改回权威坐标 → 恢复一致
        back = call('PATCH', f"/api/customers/{target['id']}", {'lon': 104.19, 'lat': 30.83},
                    self.settings, cookie=self.cookie)
        self.assertTrue(back.payload['geo_check']['consistent'])

        deleted = call('DELETE', f"/api/customers/{item['id']}", settings=self.settings, cookie=self.cookie)
        self.assertEqual(deleted.status, 200)
        listed = call('GET', '/api/customers?scope=鲜面条', settings=self.settings, cookie=self.cookie)
        self.assertEqual(len(listed.payload['items']), 30)

    def test_duplicate_code_rejected(self):
        response = call('POST', '/api/customers', {
            'code': '1', 'product_scope': '姜蒜', 'lon': 104.09, 'lat': 30.69,
        }, self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 400)
        self.assertIn('已存在', response.payload['error'])

    def test_time_window_validation(self):
        bad = call('POST', '/api/customers', {
            'code': '901', 'product_scope': '姜蒜', 'lon': 104.10, 'lat': 30.70,
            'exp_start': '09:00', 'exp_end': '08:00',
        }, self.settings, cookie=self.cookie)
        self.assertEqual(bad.status, 400)
        self.assertIn('开始时间不能晚于结束时间', bad.payload['error'])

        outside = call('POST', '/api/customers', {
            'code': '902', 'product_scope': '姜蒜', 'lon': 104.10, 'lat': 30.70,
            'exp_start': '05:00', 'exp_end': '07:00', 'acc_start': '06:00', 'acc_end': '08:00',
        }, self.settings, cookie=self.cookie)
        self.assertEqual(outside.status, 400)
        self.assertIn('可接受时间窗', outside.payload['error'])

    def test_coordinate_range(self):
        response = call('POST', '/api/customers', {
            'code': '903', 'product_scope': '姜蒜', 'lon': 12.0, 'lat': 30.7,
        }, self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 400)
        self.assertIn('经度', response.payload['error'])

    def test_export_and_import(self):
        exported = call('GET', '/api/customers/export?scope=姜蒜', settings=self.settings, cookie=self.cookie)
        self.assertEqual(exported.status, 200)
        self.assertIn('customer_id', exported.payload['content'])
        self.assertEqual(exported.payload['count'], 30)

        csv_text = ('customer_id,lon,lat,demand_kg,exp_start,exp_end,acc_start,acc_end,service_min,batch\n'
                    '1,104.19,30.83,88,05:30,06:30,05:00,07:00,20,批次二\n'
                    '777,104.05,30.60,45,06:00,07:00,05:30,07:30,15,批次二\n')
        imported = call('POST', '/api/customers/import',
                        {'content': csv_text, 'product_scope': '鲜面条'},
                        self.settings, cookie=self.cookie)
        self.assertEqual(imported.status, 200, imported.payload)
        self.assertEqual(imported.payload['created'], 1)
        self.assertEqual(imported.payload['updated'], 1)

        listed = call('GET', '/api/customers?scope=鲜面条', settings=self.settings, cookie=self.cookie)
        self.assertEqual(len(listed.payload['items']), 31)

    def test_audit_written(self):
        call('POST', '/api/customers', {
            'code': '910', 'product_scope': '姜蒜', 'lon': 104.10, 'lat': 30.70,
            'demands': [{'product_code': 'garlic', 'demand_kg': 10}],
        }, self.settings, cookie=self.cookie)
        audit = call('GET', '/api/audit?limit=20', settings=self.settings, cookie=self.cookie)
        self.assertEqual(audit.status, 200)
        actions = [row['action'] for row in audit.payload['items']]
        self.assertIn('customer.create', actions)
        self.assertIn('login.ok', actions)


class TestVehicles(AdminApiCase):
    def test_list(self):
        response = call('GET', '/api/vehicles', settings=self.settings, cookie=self.cookie)
        self.assertEqual(len(response.payload['items']), 38)   # 四种车队口径共 38 辆
        self.assertEqual(response.payload['summary']['baseline'] if 'baseline' in response.payload['summary']
                         else response.payload['summary']['total'], response.payload['summary']['total'])
        self.assertEqual(response.payload['summary']['fuel'], 18)   # 现状10 + c3保留2 + c3_10保留4 + c4保留2
        self.assertEqual(response.payload['summary']['electric'], 20)  # c3 6 + c3_10 6 + c4 8
        baseline = call('GET', '/api/vehicles?fleet=baseline', settings=self.settings, cookie=self.cookie)
        self.assertEqual(len(baseline.payload['items']), 10)

    def test_crud(self):
        created = call('POST', '/api/vehicles', {
            'code': 'X9', 'name': '新增小型燃油车', 'fleet_tag': 'baseline', 'vehicle_type': '小型',
            'energy_type': '燃油', 'capacity_kg': 1500, 'capacity_m3': 15, 'fixed_cost': 200,
            'unit_cost': 2.5, 'range_km': 600, 'effective_range_km': 600,
        }, self.settings, cookie=self.cookie)
        self.assertEqual(created.status, 200, created.payload)
        vehicle_id = created.payload['item']['id']

        updated = call('PATCH', f'/api/vehicles/{vehicle_id}', {'fixed_cost': 260, 'energy_type': '纯电'},
                       self.settings, cookie=self.cookie)
        self.assertEqual(updated.status, 200)
        self.assertEqual(updated.payload['item']['fixed_cost'], 260)
        self.assertEqual(updated.payload['item']['energy_type'], '纯电')

        deleted = call('DELETE', f'/api/vehicles/{vehicle_id}', settings=self.settings, cookie=self.cookie)
        self.assertEqual(deleted.status, 200)
        after = call('GET', '/api/vehicles', settings=self.settings, cookie=self.cookie)
        self.assertEqual(len(after.payload['items']), 38)

    def test_invalid_type(self):
        response = call('POST', '/api/vehicles', {
            'code': 'Z9', 'vehicle_type': '大型', 'energy_type': '燃油',
        }, self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 400)


class TestParamsAndUsers(AdminApiCase):
    def test_params_update(self):
        listed = call('GET', '/api/params', settings=self.settings, cookie=self.cookie)
        self.assertEqual(listed.payload['count'], 60)
        updated = call('PATCH', '/api/params', {'updates': {'penalty_late': 20}},
                       self.settings, cookie=self.cookie)
        self.assertEqual(updated.status, 200)
        values = {row['key']: row['value'] for row in updated.payload['items']}
        self.assertEqual(values['penalty_late'], '20')

    def test_user_management_and_role_scope(self):
        created = call('POST', '/api/users', {
            'username': 'disp1', 'password': 'abc12345', 'role': 'dispatcher', 'display_name': '调度一',
        }, self.settings, cookie=self.cookie)
        self.assertEqual(created.status, 200, created.payload)

        dispatcher = self.login('disp1', 'abc12345')
        self.assertEqual(call('GET', '/api/customers', settings=self.settings, cookie=dispatcher).status, 200)
        denied = call('POST', '/api/vehicles', {'code': 'N1', 'vehicle_type': '小型', 'energy_type': '燃油'},
                      self.settings, cookie=dispatcher)
        self.assertEqual(denied.status, 403)
        self.assertEqual(call('GET', '/api/users', settings=self.settings, cookie=dispatcher).status, 403)

    def test_password_policy_on_create(self):
        response = call('POST', '/api/users', {'username': 'weak', 'password': 'abc', 'role': 'viewer'},
                        self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 400)

    def test_geo_check_endpoint(self):
        response = call('GET', '/api/geo/check', settings=self.settings, cookie=self.cookie)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.payload['consistent'])
        for item in response.payload['results']:
            self.assertEqual(item['mismatch_count'], 0)
            self.assertEqual(item['compared_cells'], 31 * 31)


if __name__ == '__main__':
    unittest.main()
