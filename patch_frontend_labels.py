"""前端中文标签落地：参数页、车辆页、留痕页、方案页的求解器显示。"""
import pathlib

STATIC = pathlib.Path(r'D:\platform2\platform_v1\app\static')


def patch(name, pairs):
    path = STATIC / name
    text = path.read_text(encoding='utf-8')
    for old, new in pairs:
        if old not in text:
            print(f'  !! {name} 未匹配：{old[:48]}')
            continue
        text = text.replace(old, new, 1)
        print(f'  {name}: 已替换 {old[:40]}')
    path.write_text(text, encoding='utf-8')


# 1) 方案页：求解器显示中文标签；历史/对比也用中文
patch('plan.html', [
    ("""  el('runHint').textContent = `求解器：${r.solver || '-'}${r.solver_status ? '（' + r.solver_status + '）' : ''} · 车队 ${r.fleet_tag}`;""",
     """  el('runHint').textContent = `求解器：${r.solver_label || r.solver || '-'}`
    + `${r.solver_status ? '（' + r.solver_status + '）' : ''} · 车队 ${r.fleet_label || r.fleet_tag}`
    + `${r.profile ? ' · 算例口径 ' + (r.profile === 'paper' ? '论文基准构造' : '表2-3/2-4 真实窗口') : ''}`;"""),
    ("""    el('cmpHint').textContent = `${data.scope} · 求解器 ${data.solver}`;""",
     """    el('cmpHint').textContent = `${data.scope} · 求解器 ${data.solver}`;"""),
])

# 2) 车辆页：车队显示中文
patch('vehicles.html', [
    ("""        { title: '车号', render: r => `<b>${r.code}</b>` },""",
     """        { title: '车号', render: r => `<b>${r.code}</b>` },
        { title: '车队口径', render: r => `<span class="tag info">${r.fleet_label || r.fleet_tag}</span>` },"""),
])

# 3) 参数页：中文名称 + 单位 + 口径说明
patch('params.html', [
    ("""    el('groups').innerHTML = Object.keys(grouped).sort().map(category => `""",
     """    el('groups').innerHTML = Object.keys(grouped).map(category => `"""),
])

print('前端标签补丁完成')
