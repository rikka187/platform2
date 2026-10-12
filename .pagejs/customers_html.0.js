
(async () => {
  const payload = await Shell.boot('/customers.html', '客户数据管理',
    '选题案例2 表2-3 / 表2-4：坐标、需求量、期望与可接受时间窗、服务时间、批次归属');
  if (!payload) return;

  const mount = document.getElementById('pageMount');
  let state = { scope: '', keyword: '', items: [], products: [], batches: [], summary: {}, geo: null };
  const canEdit = Shell.has('customers.edit');

  mount.innerHTML = `
    <div id="geoBanner"></div>
    <div class="grid k4" id="stats"></div>
    <div class="card">
      <div class="card-head">
        <h3>客户列表</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn ghost" id="btnExport">导出 CSV</button>
          <button class="btn ghost" id="btnImport" ${canEdit ? '' : 'disabled'}>导入 CSV</button>
          <button class="btn primary" id="btnNew" ${canEdit ? '' : 'disabled'}>+ 新增客户</button>
        </div>
      </div>
      <div class="filters">
        <div class="field" style="min-width:150px">
          <label>产品品类</label>
          <select id="fScope">
            <option value="">全部（60）</option>
            <option value="姜蒜">姜蒜（30）</option>
            <option value="鲜面条">鲜面条（30）</option>
          </select>
        </div>
        <div class="field" style="min-width:220px">
          <label>关键字</label>
          <input id="fKeyword" placeholder="编号 / 名称 / 区域 / 客户类型">
        </div>
        <button class="btn" id="btnSearch">查询</button>
        <span class="muted" id="tableHint"></span>
      </div>
      <div id="table" style="margin-top:14px"></div>
    </div>`;

  const el = id => document.getElementById(id);

  function renderStats() {
    const s = state.summary || {};
    el('stats').innerHTML = `
      <div class="stat"><b>客户总数</b><strong>${s.total ?? 0}</strong><small>当前筛选结果</small></div>
      <div class="stat"><b>姜蒜客户</b><strong>${(s.by_scope || {})['姜蒜'] ?? 0}</strong><small>表2-3</small></div>
      <div class="stat"><b>鲜面条客户</b><strong>${(s.by_scope || {})['鲜面条'] ?? 0}</strong><small>表2-4</small></div>
      <div class="stat"><b>合计需求 / 体积</b><strong>${Number(s.total_kg ?? 0).toFixed(1)} kg</strong><small>${Number(s.total_m3 ?? 0).toFixed(3)} m³（按品类密度折算）</small></div>`;
  }

  function renderGeo() {
    const box = el('geoBanner');
    if (!state.geo) { box.innerHTML = ''; return; }
    const bad = (state.geo.results || []).filter(r => !r.consistent);
    if (!bad.length) {
      box.innerHTML = `<div class="banner ok">✓ 距离矩阵与团队既有 31×31 矩阵逐格一致（Haversine，地球半径 6371.0 km，保留 2 位小数）</div>`;
      return;
    }
    const detail = bad.map(r => `${r.scope}：${r.mismatch_count} 处不一致` +
      (r.missing_customers.length ? `，缺少客户 ${r.missing_customers.join('、')}` : '') +
      (r.mismatches.length ? `，例如 ${r.mismatches[0].from}→${r.mismatches[0].to} 既有 ${r.mismatches[0].reference} km / 当前 ${r.mismatches[0].current} km` : '')
    ).join('；');
    box.innerHTML = `<div class="banner bad">距离矩阵已与既有矩阵不一致：${detail}。如需按新数据出方案，请先确认口径或重新生成矩阵。</div>`;
  }

  function renderTable() {
    const rows = state.items;
    const demandText = row => (row.demand_summary || '').split(',').filter(Boolean).map(part => {
      const [code, kg] = part.split(':');
      const name = (state.products.find(p => p.code === code) || {}).name || code;
      return `${name} ${kg}kg`;
    }).join(' / ');
    const columns = [
      { title: '编号', render: r => `<b>${Shell.escapeHtml(r.code)}</b>` },
      { title: '名称', render: r => Shell.escapeHtml(r.name || '') },
      { title: '品类', render: r => `<span class="tag ${r.product_scope === '姜蒜' ? 'info' : 'ok'}">${r.product_scope}</span>` },
      { title: '经度', render: r => Number(r.lon).toFixed(2) },
      { title: '纬度', render: r => Number(r.lat).toFixed(2) },
      { title: '客户类型', render: r => r.customer_type ? Shell.escapeHtml(r.customer_type) : '<span class="muted">待填</span>' },
      { title: '区域', render: r => r.region ? Shell.escapeHtml(r.region) : '<span class="muted">待填</span>' },
      { title: '需求量', render: r => demandText(r) || '<span class="muted">未填</span>' },
      { title: '合计', render: r => `${Number(r.total_kg || 0).toFixed(1)} kg` },
      { title: '体积', render: r => `${Number(r.total_m3 || 0).toFixed(3)} m³` },
      { title: '服务(分)', render: r => r.service_min },
      { title: '期望窗', render: r => `${r.exp_start_text || '-'} ~ ${r.exp_end_text || '-'}` },
      { title: '可接受窗', render: r => `<span class="muted">${r.acc_start_text || '-'} ~ ${r.acc_end_text || '-'}</span>` },
      { title: '批次', render: r => r.batch_code ? `<span class="tag grey">${Shell.escapeHtml(r.batch_code)}</span>` : '' },
      { title: '操作', render: r => canEdit
        ? `<button class="btn sm" data-edit="${r.id}">编辑</button>
           <button class="btn sm danger" data-del="${r.id}">删除</button>` : '<span class="muted">只读</span>' }
    ];
    el('table').innerHTML = Shell.table(columns, rows);
    el('tableHint').textContent = `共 ${rows.length} 条`;
    el('table').querySelectorAll('[data-edit]').forEach(btn => btn.addEventListener('click', () => openEditor(Number(btn.dataset.edit))));
    el('table').querySelectorAll('[data-del]').forEach(btn => btn.addEventListener('click', () => removeCustomer(Number(btn.dataset.del))));
  }

  async function load() {
    const params = new URLSearchParams();
    if (state.scope) params.set('scope', state.scope);
    if (state.keyword) params.set('keyword', state.keyword);
    const data = await Shell.api('/api/customers?' + params.toString());
    state.items = data.items; state.summary = data.summary;
    state.products = data.products || []; state.batches = data.batches || [];
    const geo = await Shell.api('/api/geo/check').catch(() => null);
    state.geo = geo;
    renderStats(); renderGeo(); renderTable();
  }

  function productOptions(scope) {
    return state.products.filter(p => (scope === '姜蒜' ? p.category === '姜蒜' : p.category === '鲜面条'));
  }

  function demandRows(scope, demands = []) {
    const products = productOptions(scope);
    return products.map(p => {
      const found = demands.find(d => d.product_code === p.code) || {};
      return `<div class="field"><label>${p.name} 需求量(kg) · 密度 ${p.density_t_per_m3} t/m³</label>
        <input type="number" step="0.1" min="0" data-demand="${p.code}" value="${found.demand_kg ?? ''}"></div>`;
    }).join('');
  }

  async function openEditor(id) {
    const isNew = !id;
    let row = isNew ? { product_scope: '姜蒜', lon: 104.26, lat: 30.85, service_min: 15 } :
      state.items.find(r => r.id === id);
    if (!isNew) {
      // 列表接口只带需求摘要，编辑时取详情以获得各品类需求量
      try {
        const detail = await Shell.api('/api/customers/' + id);
        row = Object.assign({}, row, detail.item);
      } catch (e) { Shell.toast('读取客户详情失败：' + e.message, 'err'); }
    }
    const mask = document.createElement('div');
    mask.className = 'modal-mask open';
    mask.innerHTML = `
      <div class="modal">
        <h3>${isNew ? '新增客户' : '编辑客户 ' + Shell.escapeHtml(row.code)}</h3>
        <div class="muted">坐标决定 Haversine 距离矩阵；时间窗需满足"期望窗 ⊆ 可接受窗"</div>
        <div class="modal-body">
          <div class="form-grid">
            <div class="field"><label>客户编号 *</label><input id="cCode" value="${row.code ?? ''}" ${isNew ? '' : 'readonly'}></div>
            <div class="field"><label>客户名称</label><input id="cName" value="${Shell.escapeHtml(row.name || '')}"></div>
            <div class="field"><label>产品品类 *</label>
              <select id="cScope" ${isNew ? '' : 'disabled'}>
                <option value="姜蒜" ${row.product_scope === '姜蒜' ? 'selected' : ''}>姜蒜</option>
                <option value="鲜面条" ${row.product_scope === '鲜面条' ? 'selected' : ''}>鲜面条</option>
              </select></div>
            <div class="field"><label>批次归属</label>
              <select id="cBatch">
                <option value="">（不指定）</option>
                ${state.batches.filter(b => b.product_scope === row.product_scope).map(b =>
                  `<option value="${b.code}" ${row.batch_code === b.code ? 'selected' : ''}>${b.code}（${Shell.fmtTime(b.depart_after)} 起送）</option>`).join('')}
              </select></div>
            <div class="field"><label>经度 *</label><input id="cLon" type="number" step="0.01" value="${row.lon ?? ''}"></div>
            <div class="field"><label>纬度 *</label><input id="cLat" type="number" step="0.01" value="${row.lat ?? ''}"></div>
            <div class="field"><label>客户类型</label><input id="cType" placeholder="餐饮门店 / 商超 / 农贸市场…" value="${Shell.escapeHtml(row.customer_type || '')}"></div>
            <div class="field"><label>所属区域</label><input id="cRegion" placeholder="青白江 / 新都 / 金牛…" value="${Shell.escapeHtml(row.region || '')}"></div>
            <div class="field"><label>期望时间窗开始</label><input id="cExpS" placeholder="07:00" value="${row.exp_start_text || ''}"></div>
            <div class="field"><label>期望时间窗结束</label><input id="cExpE" placeholder="08:00" value="${row.exp_end_text || ''}"></div>
            <div class="field"><label>可接受窗开始</label><input id="cAccS" placeholder="06:30" value="${row.acc_start_text || ''}"></div>
            <div class="field"><label>可接受窗结束</label><input id="cAccE" placeholder="08:30" value="${row.acc_end_text || ''}"></div>
            <div class="field"><label>服务时间(分)</label><input id="cService" type="number" min="0" max="480" value="${row.service_min ?? 15}"></div>
            <div class="field span2"><label>备注</label><input id="cNote" value="${Shell.escapeHtml(row.note || '')}"></div>
            <div class="span4" style="border-top:1px solid #eef4f8;padding-top:12px">
              <div class="muted" style="margin-bottom:8px">需求量（按品类，体积由密度自动折算）</div>
              <div class="form-grid" id="cDemands">${demandRows(row.product_scope, row.demands || [])}</div>
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" data-close>取消</button>
          <button class="btn primary" id="cSave">保存</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => mask.remove()));
    mask.querySelector('#cScope').addEventListener('change', event => {
      const scope = event.target.value;
      mask.querySelector('#cDemands').innerHTML = demandRows(scope, []);
      const batch = mask.querySelector('#cBatch');
      batch.innerHTML = '<option value="">（不指定）</option>' + state.batches
        .filter(b => b.product_scope === scope)
        .map(b => `<option value="${b.code}">${b.code}（${Shell.fmtTime(b.depart_after)} 起送）</option>`).join('');
    });
    mask.querySelector('#cSave').addEventListener('click', async () => {
      const demands = [...mask.querySelectorAll('[data-demand]')]
        .filter(input => input.value !== '')
        .map(input => ({ product_code: input.dataset.demand, demand_kg: Number(input.value) }));
      const body = {
        code: mask.querySelector('#cCode').value.trim(),
        name: mask.querySelector('#cName').value.trim(),
        product_scope: mask.querySelector('#cScope').value,
        lon: mask.querySelector('#cLon').value,
        lat: mask.querySelector('#cLat').value,
        customer_type: mask.querySelector('#cType').value.trim(),
        region: mask.querySelector('#cRegion').value.trim(),
        exp_start: mask.querySelector('#cExpS').value.trim(),
        exp_end: mask.querySelector('#cExpE').value.trim(),
        acc_start: mask.querySelector('#cAccS').value.trim(),
        acc_end: mask.querySelector('#cAccE').value.trim(),
        service_min: mask.querySelector('#cService').value,
        batch_code: mask.querySelector('#cBatch').value,
        note: mask.querySelector('#cNote').value.trim(),
        demands
      };
      try {
        const result = isNew
          ? await Shell.api('/api/customers', { method: 'POST', body })
          : await Shell.api('/api/customers/' + id, { method: 'PATCH', body });
        Shell.toast(isNew ? '客户已新增' : '客户已更新', 'ok');
        mask.remove();
        state.geo = { results: [result.geo_check], consistent: result.geo_check.consistent };
        await load();
      } catch (e) { Shell.toast(e.message, 'err'); }
    });
  }

  async function removeCustomer(id) {
    const row = state.items.find(r => r.id === id);
    if (!confirm(`确认删除客户 ${row.code}（${row.product_scope}）？该操作会写入留痕日志。`)) return;
    try {
      await Shell.api('/api/customers/' + id, { method: 'DELETE' });
      Shell.toast('客户已删除', 'ok');
      await load();
    } catch (e) { Shell.toast(e.message, 'err'); }
  }

  function importDialog() {
    const mask = document.createElement('div');
    mask.className = 'modal-mask open';
    mask.innerHTML = `
      <div class="modal" style="width:min(680px,100%)">
        <h3>导入客户 CSV</h3>
        <div class="muted">列名需包含 customer_id、lon、lat；可选 name、service_min、exp_start、exp_end、acc_start、acc_end、batch、garlic_kg、ginger_kg、demand_kg。同编号自动更新。</div>
        <div class="modal-body">
          <div class="field"><label>默认品类（CSV 未写 product_scope 时使用）</label>
            <select id="iScope"><option value="姜蒜">姜蒜</option><option value="鲜面条">鲜面条</option></select></div>
          <div class="field" style="margin-top:10px"><label>CSV 内容</label>
            <textarea id="iText" rows="12" placeholder="customer_id,lon,lat,demand_kg,exp_start,exp_end,acc_start,acc_end,service_min,batch"></textarea></div>
          <div class="modal-foot" style="margin-top:10px">
            <input type="file" id="iFile" accept=".csv,text/csv" style="margin-right:auto">
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" data-close>取消</button>
          <button class="btn primary" id="iGo">开始导入</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => mask.remove()));
    mask.querySelector('#iFile').addEventListener('change', event => {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => { mask.querySelector('#iText').value = reader.result; };
      reader.readAsText(file, 'utf-8');
    });
    mask.querySelector('#iGo').addEventListener('click', async () => {
      try {
        const result = await Shell.api('/api/customers/import', {
          method: 'POST',
          body: { content: mask.querySelector('#iText').value, product_scope: mask.querySelector('#iScope').value }
        });
        Shell.toast(`导入完成：新增 ${result.created}，更新 ${result.updated}`, 'ok');
        mask.remove();
        await load();
      } catch (e) { Shell.toast(e.message, 'err'); }
    });
  }

  el('fScope').addEventListener('change', e => { state.scope = e.target.value; load(); });
  el('btnSearch').addEventListener('click', () => { state.keyword = el('fKeyword').value.trim(); load(); });
  el('fKeyword').addEventListener('keydown', e => { if (e.key === 'Enter') el('btnSearch').click(); });
  el('btnNew').addEventListener('click', () => openEditor(null));
  el('btnImport').addEventListener('click', importDialog);
  el('btnExport').addEventListener('click', async () => {
    try {
      const data = await Shell.api('/api/customers/export' + (state.scope ? '?scope=' + encodeURIComponent(state.scope) : ''));
      Shell.download(data.filename, data.content);
      Shell.toast(`已导出 ${data.count} 条客户数据`, 'ok');
    } catch (e) { Shell.toast(e.message, 'err'); }
  });

  await load();
})();
