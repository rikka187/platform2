/* 管理后台公共外壳：登录守卫、侧边导航、API 封装、提示与表格工具。
 * 所有管理页共用，页面只需提供 <body class="console"> 与 #app 内容。
 */
const Shell = (() => {
  const NAV = [
    { href: '/', icon: '🏠', label: '调度驾驶舱', perm: 'dashboard.view' },
    { href: '/customers.html', icon: '📍', label: '客户管理', perm: 'customers.view' },
    { href: '/vehicles.html', icon: '🚚', label: '车辆管理', perm: 'vehicles.view' },
    { href: '/params.html', icon: '⚙️', label: '参数设置', perm: 'vehicles.view' },
    { href: '/plan.html', icon: '🧭', label: '路径与方案', perm: 'plan.view' },
    { href: '/plan.html', icon: '🏭', label: '月台协同', perm: 'docks.view' },
    { href: '/scenarios.html', icon: '📈', label: '多情景评价', perm: 'scenarios.view' },
    { href: '/monitor.html', icon: '🛰️', label: '监控反馈', perm: 'monitor.view' },
    { href: '/users.html', icon: '👥', label: '用户与留痕', perm: 'users.manage' }
  ];

  // 冷链作业时段划分：与平台口径一致（谷段充电 01:00-07:00、装车高峰 03:00-07:00）
  const SHIFTS = [
    { from: 1, to: 7, label: '谷段充电 · 装车高峰' },
    { from: 7, to: 11, label: '早班配送' },
    { from: 11, to: 14, label: '午间到货窗口' },
    { from: 14, to: 18, label: '下午配送' },
    { from: 18, to: 22, label: '晚班收尾 · 返场' },
    { from: 22, to: 25, label: '夜间备货 · 待充电' }
  ];

  function shiftLabel(hour) {
    const shift = SHIFTS.find(item => hour >= item.from % 24 && hour < item.to)
      || SHIFTS[SHIFTS.length - 1];
    return shift.label;
  }

  function nowMinutes() {
    const now = new Date();
    return now.getHours() * 60 + now.getMinutes();
  }

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  let me = null;
  let mePayload = null;

  function toast(message, type = '') {
    let box = document.querySelector('.toast-box');
    if (!box) {
      box = document.createElement('div');
      box.className = 'toast-box';
      document.body.appendChild(box);
    }
    const item = document.createElement('div');
    item.className = 'toast ' + type;
    item.textContent = message;
    box.appendChild(item);
    setTimeout(() => item.remove(), type === 'err' ? 6000 : 3200);
  }

  async function api(path, options = {}) {
    const opts = Object.assign({ credentials: 'same-origin', headers: {} }, options);
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, opts);
    let data = null;
    const text = await response.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
    }
    if (!response.ok) {
      const error = new Error((data && data.error) || `请求失败（HTTP ${response.status}）`);
      error.status = response.status;
      error.payload = data;
      throw error;
    }
    return data || {};
  }

  function has(permission) {
    const perms = (mePayload && mePayload.permissions) || [];
    if (perms.includes('*')) return true;
    if (perms.includes(permission)) return true;
    return perms.includes(permission.split('.')[0] + '.*');
  }

  function roleLabel(role) {
    const labels = (mePayload && mePayload.roles) || {};
    return labels[role] || role;
  }

  function renderSide(active) {
    const side = document.createElement('aside');
    side.className = 'side';
    const links = NAV.filter(item => has(item.perm)).map(item => `
      <a class="nav ${item.href === active ? 'active' : ''}" href="${item.soon ? '#' : item.href}"
         ${item.soon ? 'data-soon="1" title="后续阶段实现"' : ''}>
        <span class="ico">${item.icon}</span><span>${item.label}</span>
        ${item.soon ? '<span class="tag grey" style="margin-left:auto">待建</span>' : ''}
      </a>`).join('');
    side.innerHTML = `
      <div class="brand">
        <img class="mark-logo" src="/logo.png" alt="银犁物流" title="银犁物流城市冷链仓配一体化协同平台">
        <div><b>银犁物流</b><span>城市冷链仓配一体化协同平台</span></div>
      </div>
      ${links}
      <div class="foot">
        距离口径 Haversine · 半径 6371.0 km<br>
        <span id="shellGeo">距离矩阵校核中…</span>
      </div>`;
    side.querySelectorAll('[data-soon]').forEach(node => node.addEventListener('click', event => {
      event.preventDefault();
      toast('该模块在后续阶段交付（当前为第一阶段：数据与登录）', '');
    }));
    return side;
  }

  async function geoBadge() {
    const node = document.getElementById('shellGeo');
    if (!node) return;
    try {
      const data = await api('/api/geo/check');
      if (data.consistent) {
        node.innerHTML = '<span class="tag ok">距离矩阵一致</span>';
      } else {
        const bad = (data.results || []).filter(r => !r.consistent)
          .map(r => `${r.scope}:${r.mismatch_count}处`).join(' ');
        node.innerHTML = `<span class="tag bad">距离不一致 ${bad}</span>`;
      }
    } catch (e) {
      node.textContent = '';
    }
  }

  async function boot(active, title, subtitle) {
    try {
      mePayload = await api('/api/auth/me');
    } catch (e) {
      location.replace('/login.html?next=' + encodeURIComponent(location.pathname));
      return null;
    }
    me = mePayload.user;
    document.body.classList.add('console');
    const shell = document.createElement('div');
    shell.className = 'shell';
    shell.appendChild(renderSide(active));

    const main = document.createElement('div');
    main.className = 'main';
    main.innerHTML = `
      <div class="topbar">
        <div><h2>${title || ''}</h2><div class="sub">${subtitle || ''}</div></div>
        <div class="who">
          <div class="clock" id="shellClock" title="平台时间与当前作业时段"></div>
          <span class="chip">${roleLabel(me.role)}</span>
          <span>${me.display_name || me.username}</span>
          <button class="btn ghost sm" id="shellPwd">改口令</button>
          <button class="btn ghost sm" id="shellOut">退出</button>
        </div>
      </div>`;
    const content = document.createElement('div');
    content.className = 'content';
    content.innerHTML = '<div id="pageMount"></div>';
    main.appendChild(content);
    shell.appendChild(main);

    const holder = document.getElementById('app') || document.body;
    holder.innerHTML = '';
    holder.appendChild(shell);

    document.getElementById('shellOut').addEventListener('click', async () => {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) {}
      location.replace('/login.html');
    });
    document.getElementById('shellPwd').addEventListener('click', () => passwordModal());
    startClock();
    geoBadge();
    if (mePayload.must_change_password) {
      toast('当前口令为初始口令，请立即修改', 'err');
      passwordModal(true);
    }
    return mePayload;
  }

  function startClock() {
    const node = document.getElementById('shellClock');
    if (!node) return;
    const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    const render = () => {
      const now = new Date();
      node.innerHTML =
        `<b>${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}</b>` +
        `<small>${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
        `${days[now.getDay()]} · ${shiftLabel(now.getHours())}</small>`;
    };
    render();
    // 页面隐藏时停表，回来立即补一次，避免后台标签页白耗 CPU
    setInterval(() => { if (!document.hidden) render(); }, 1000);
  }

  function passwordModal(force = false) {
    const mask = document.createElement('div');
    mask.className = 'modal-mask open';
    mask.innerHTML = `
      <div class="modal" style="width:min(430px,100%)">
        <h3>修改登录口令</h3>
        <div class="modal-body grid" style="gap:12px">
          ${force ? '<div class="banner bad">首次登录必须修改初始口令后才能继续使用</div>' : ''}
          <div class="field"><label>原口令</label><input type="password" id="pwdOld"></div>
          <div class="field"><label>新口令（至少 8 位，含字母与数字）</label><input type="password" id="pwdNew"></div>
          <div class="field"><label>确认新口令</label><input type="password" id="pwdRe"></div>
        </div>
        <div class="modal-foot">
          ${force ? '' : '<button class="btn ghost" data-close>取消</button>'}
          <button class="btn primary" id="pwdSave">保存</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => mask.remove()));
    mask.querySelector('#pwdSave').addEventListener('click', async () => {
      const oldPwd = mask.querySelector('#pwdOld').value;
      const newPwd = mask.querySelector('#pwdNew').value;
      const rePwd = mask.querySelector('#pwdRe').value;
      if (newPwd !== rePwd) { toast('两次输入的新口令不一致', 'err'); return; }
      try {
        await api('/api/auth/password', { method: 'POST', body: { old_password: oldPwd, new_password: newPwd } });
        toast('口令已修改', 'ok');
        mask.remove();
        if (force) setTimeout(() => location.reload(), 600);
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  function fmtTime(minutes) {
    if (minutes === null || minutes === undefined || minutes === '') return '';
    const total = Number(minutes);
    return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
  }

  function table(columns, rows, options = {}) {
    if (!rows.length) return '<div class="empty">暂无数据</div>';
    const head = columns.map(c => `<th>${c.title}</th>`).join('');
    const body = rows.map(row => `<tr data-id="${row[options.idField || 'id']}">` +
      columns.map(c => `<td>${c.render ? c.render(row) : (row[c.key] ?? '')}</td>`).join('') + '</tr>').join('');
    return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function download(filename, content, type = 'text/csv;charset=utf-8') {
    const blob = new Blob([content], { type });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  return { api, boot, download, escapeHtml, fmtTime, has, me: () => me, nowMinutes,
           payload: () => mePayload, roleLabel, shiftLabel, table, toast };
})();

window.Shell = Shell;
