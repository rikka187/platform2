let lastResult = null;
const scenarioNames = {H0:'人工调度基准',S0:'路径优化方案',S1:'时效优化方案',C3:'车辆新能源方案',D3:'仓配全链路协同方案'};
const waitDefaults = {H0:120,S0:72,S1:60,C3:60,D3:18};
const solverNames = {heuristic:'启发式',cpSat:'CP-SAT',cp_sat:'CP-SAT','cp-sat':'CP-SAT'};
const engineLabels = {auto:'自动',heuristic:'启发式',cpsat:'CP-SAT'};

function minuteLabel(v){v=Number(v);return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');}

// 服务端通过 /api/solver/status 下发默认口径（宝塔部署默认使用启发式，
// 保证五情景对比与汇报材料一致；答辩时可在页面上切到 CP-SAT）。
function applyDefaultEngine(engine){
  if(!engine) return;
  const sel = document.getElementById('solver');
  if([...sel.options].some(o=>o.value===engine)) sel.value = engine;
}

async function loadSolverStatus(){
  const box = document.getElementById('solverStatus');
  try {
    const r = await fetch('/api/solver/status');
    const d = await r.json();
    applyDefaultEngine(d.default_engine);
    const engine = engineLabels[d.default_engine] || '';
    if (d.cp_sat_ready) {
      box.className = 'solver-status ready';
      box.textContent = `CP-SAT 可用 · OR-Tools ${d.ortools_version || ''}` + (engine ? ` · 默认口径：${engine}` : '');
    } else {
      box.className = 'solver-status fallback';
      box.textContent = 'CP-SAT 未安装 · 平台使用启发式求解器' + (engine ? `（默认口径：${engine}）` : '');
    }
  } catch (e) {
    box.className = 'solver-status fallback';
    box.textContent = '求解器状态检测失败';
  }
}

async function loadDemo(){
  const r = await fetch('/api/demo');
  const d = await r.json();
  const counts = [['订单',d.orders.length],['配送趟次',d.trips.length],['车辆',d.vehicles.length],['月台',d.docks.length],['充电计划',d.charging.length],['备货任务',d.prep.length]];
  document.getElementById('dataOverview').innerHTML = counts.map(([k,v])=>`<div><b>${k}</b><span>${v}</span></div>`).join('');
  document.getElementById('status').textContent = '演示数据已加载。选择情景与求解方式后点击“生成调度方案”。';
}

function syncScenario(){
  const s = document.getElementById('scenario').value;
  document.getElementById('waitMin').value = waitDefaults[s];
  document.getElementById('waitLabel').textContent = waitDefaults[s] + ' 分钟';
  document.getElementById('heroTitle').textContent = s + ' ' + scenarioNames[s];
}

async function solve(){
  const s = document.getElementById('scenario').value;
  const w = Number(document.getElementById('waitMin').value);
  const solverChoice = document.getElementById('solver').value;
  document.getElementById('status').textContent = `正在生成 ${s} 方案（${solverChoice}）...`;
  try {
    const r = await fetch('/api/solve', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scenario:s,params:{wait_min:w,solver:solverChoice}})
    });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || '求解失败');
    lastResult = d;
    render(d);
    await loadCompare(solverChoice);
    const actual = d.solver || '未知';
    document.getElementById('status').textContent = `${s} 方案已生成 · 实际求解器：${actual}`
      + (d.solver_status ? `（${d.solver_status}）` : '');
  } catch (e) {
    document.getElementById('status').textContent = `求解失败：${e.message}`;
  }
}

function render(d){
  const k = d.kpi;
  const cards = [['日总成本',k.total_cost+' 元','成本结果'],['准时率',k.on_time_rate+'%','服务水平'],['平均等待',k.avg_wait_min+' 分钟','仓配效率'],['车辆数',k.used_vehicles+' 辆','运力投入'],['总里程',k.total_distance_km+' km','路径结果'],['碳排放',k.carbon_kg+' kg','绿色结果'],['总货量',k.total_load_kg+' kg','订单规模'],['车辆利用率',k.vehicle_utilization+'%','资源利用']];
  document.getElementById('kpis').innerHTML = cards.map(([a,b,c])=>`<div class="kpi"><span>${a}</span><strong>${b}</strong><small>${c}</small></div>`).join('');

  const h = ['趟次','批次','品类','车辆','能源','月台','备货完成','车辆到达','开始装车','装车完成','发车','等待(分)'];
  const rows = d.schedule.map(s=>[s.trip_id,s.batch_id,s.product,s.vehicle_id,s.energy_type,s.dock_id,minuteLabel(s.prep_end),minuteLabel(s.vehicle_arrival),minuteLabel(s.load_start),minuteLabel(s.load_end),minuteLabel(s.depart),s.wait_min]);
  document.getElementById('scheduleTable').innerHTML = '<thead><tr>'+h.map(x=>`<th>${x}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>`<td>${x}</td>`).join('')+'</tr>').join('')+'</tbody>';

  const min = Math.min(...d.schedule.map(s=>s.load_start));
  const max = Math.max(...d.schedule.map(s=>s.load_end));
  const span = Math.max(1,max-min);
  document.getElementById('gantt').innerHTML = d.schedule.map(s=>{
    const left=(s.load_start-min)/span*100;
    const width=Math.max(6,(s.load_end-s.load_start)/span*100);
    return `<div class="bar" style="margin-left:${left}%;width:${width}%">${s.dock_id} · ${s.trip_id} ${minuteLabel(s.load_start)}–${minuteLabel(s.load_end)}</div>`;
  }).join('');

  document.getElementById('monitor').innerHTML = [['车辆状态','按方案执行调度','good'],['温度状态','鲜面条0–4℃达标','good'],['充电桩状态','3台设备可用','good'],['月台状态','已按预约时段分配','good'],['异常事件','当前无未处置异常','good'],['数据状态','订单、车辆、库存数据一致','good']].map(([a,b,c])=>`<div class="monitor-card"><b>${a}</b><p class="${c}">${b}</p></div>`).join('');
}

async function loadCompare(solverChoice){
  const q = solverChoice ? `?solver=${encodeURIComponent(solverChoice)}` : '';
  const r = await fetch('/api/compare' + q);
  const d = await r.json();
  if (!d.items || !d.items.length) return;
  const max = Math.max(...d.items.map(x=>x.kpi.total_cost));
  document.getElementById('compare').innerHTML = d.items.map(x=>{
    const p=x.kpi;
    const w=p.total_cost/max*100;
    return `<div class="compare-row"><b>${x.scenario}</b><div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div><span>${p.total_cost} 元</span></div>`;
  }).join('');
}

function exportKpi(){
  if(!lastResult){alert('请先生成方案');return;}
  const k=lastResult.kpi;
  const rows=[['scenario','solver','total_cost','on_time_rate','avg_wait_min','vehicle_utilization','total_distance_km','carbon_kg'],[lastResult.scenario,lastResult.solver || '',k.total_cost,k.on_time_rate,k.avg_wait_min,k.vehicle_utilization,k.total_distance_km,k.carbon_kg]];
  const blob=new Blob(['\ufeff'+rows.map(r=>r.join(',')).join('\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='kpi_summary.csv';a.click();
}

document.getElementById('solveBtn').onclick=solve;
document.getElementById('loadBtn').onclick=loadDemo;
document.getElementById('exportBtn').onclick=exportKpi;
document.getElementById('scenario').onchange=syncScenario;
document.getElementById('solver').onchange=e=>loadCompare(e.target.value);
document.getElementById('waitMin').oninput=e=>document.getElementById('waitLabel').textContent=e.target.value+' 分钟';
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById('tab-'+b.dataset.tab).classList.add('active');
});

// 先取回服务端默认口径，再加载数据与对比图，保证卡片与对比图同口径。
(async function bootstrap(){
  await loadSolverStatus();
  try { await loadDemo(); } catch (e) { console.error(e); }
  syncScenario();
  await loadCompare(document.getElementById('solver').value);
})();
