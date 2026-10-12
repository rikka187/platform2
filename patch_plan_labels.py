"""把中文标签接到 api_plan 的响应里（求解器、车队、情景）。"""
import pathlib

path = pathlib.Path(r'D:\platform2\platform_v1\app\api_plan.py')
text = path.read_text(encoding='utf-8')

if 'from app import api_admin, auth, db, labels' not in text and 'db, labels' not in text.split('\n')[0:40].__str__():
    text = text.replace('from app import db', 'from app import db, labels', 1)
    print('api_plan: 导入 labels')

old = "        'solver': solution.get('solver'),"
new = ("        'solver': solution.get('solver'),\n"
       "        'solver_label': labels.solver_label(solution.get('solver')),")
if old in text and "'solver_label'" not in text:
    text = text.replace(old, new, 1)
    print('api_plan: 单次方案响应加 solver_label')

old2 = "        item['scenario_label'] = SCENARIO_LABELS.get(item['scenario'], item['scenario'])"
new2 = ("        item['scenario_label'] = SCENARIO_LABELS.get(item['scenario'], item['scenario'])\n"
        "        item['fleet_label'] = labels.fleet_label(item.get('fleet_tag'))\n"
        "        item['solver_label'] = labels.solver_label(item.get('solver'))")
if old2 in text:
    text = text.replace(old2, new2, 1)
    print('api_plan: 历史列表加中文标签')

# get_run 回放也带中文
old3 = "        'profile': plan.get('profile'),"
new3 = ("        'solver_label': labels.solver_label(item.get('solver')),\n"
        "        'fleet_label': labels.fleet_label(item.get('fleet_tag')),\n"
        "        'profile': plan.get('profile'),")
if old3 in text and "'fleet_label': labels.fleet_label(item.get('fleet_tag'))" not in text.split('def get_run')[1][:1500]:
    text = text.replace(old3, new3, 1)
    print('api_plan: 回放加中文标签')

path.write_text(text, encoding='utf-8')
print('solver_label 出现次数:', text.count('labels.solver_label'))
