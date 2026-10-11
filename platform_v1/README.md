# 银犁物流仓配一体化协同平台（Prototype v1.3）

面向竞赛演示的仓配一体化平台原型，把第二、三、四章的成果串成一条可操作业务链：

- 订单与配送批次
- 路径优化与趟次方案
- 车辆匹配与新能源替换
- 备货、装车、月台和发车时序协同
- H0、S0、S1、C3、D3 多情景评价
- KPI、甘特图、执行监控和CSV导出

> **要部署到服务器（Ubuntu 20.04 + 宝塔面板）？**
> 直接看 [`docs/部署到Ubuntu宝塔面板.md`](docs/部署到Ubuntu宝塔面板.md)，三条命令搞定：
>
> ```bash
> cd /www/wwwroot/platform_v1
> sed -i 's/\r$//' deploy/*.sh      # 从 Windows 上传过才需要
> sudo bash deploy/install.sh
> ```

---

## 一、本机启动（Windows / Linux 通用）

在 `platform_v1` 目录打开命令行，运行：

```bash
python server.py --host 0.0.0.0 --port 8787
```

浏览器访问：

```text
http://127.0.0.1:8787
```

同一局域网内的同学访问时，把 `127.0.0.1` 换成你的电脑 IP，例如
`http://10.69.92.26:8787`。如果打不开，先检查防火墙是否放行 Python 或 8787 端口。

Windows 用户也可以直接双击上一级目录的 `一键启动平台.bat`。

服务启动时会先做自检（Python 版本、前端页面、数据文件、OR-Tools），
问题会直接打印出来；只看自检不启动：

```bash
python server.py --check-only
```

常用参数：

```bash
python server.py --port 9000                # 换端口
python server.py --data-dir /path/to/data   # 换成自己的 CSV 数据目录
python server.py --no-access-log            # 关闭逐条访问日志
```

---

## 二、服务器部署（Ubuntu Server 20.04 + 宝塔面板）

完整步骤见 [`docs/部署到Ubuntu宝塔面板.md`](docs/部署到Ubuntu宝塔面板.md)，要点：

1. 上传整个 `platform_v1` 到 `/www/wwwroot/`；
2. `sudo bash deploy/install.sh`：自动建虚拟环境、装依赖、注册 systemd 服务、
   启动并做健康检查（服务只监听 `127.0.0.1:8787`）；
3. 宝塔【网站】添加站点 → 【反向代理】指向 `http://127.0.0.1:8787`；
   **记得删掉面板默认的 `location ~ .*\.(js|css)?$` 规则**，否则页面没样式；
4. 面板申请 SSL 证书，云安全组放行 80/443（不要放行 8787）。

运维命令：

```bash
bash deploy/check.sh                  # 一键体检（系统/依赖/数据/服务/端口/Nginx）
systemctl status yinli-platform       # 服务状态
systemctl restart yinli-platform      # 改配置后重启
journalctl -u yinli-platform -f       # 实时日志
bash deploy/run.sh                    # 前台调试运行
```

运行参数全部通过 `deploy/platform.env` 配置（端口、数据目录、并发、默认求解口径等），
详见部署文档第 6 节。

---

## 三、为什么没有 CP-SAT

CP-SAT 不是 Python 自带模块，而是 Google OR-Tools 里的约束规划/混合整数求解器。
Python 只提供“运行环境”，OR-Tools 需要另外安装。

先在命令行检查：

```bash
python -c "from ortools.sat.python import cp_model; print('CP-SAT OK')"
```

如果显示 `No module named 'ortools'`，安装：

```bash
python -m pip install -r requirements.txt
```

如果官方源下载较慢，可使用：

```bash
python -m pip install ortools -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Windows 上更省事的做法是双击 `install_cpsat.bat`；服务器上 `deploy/install.sh`
会自动安装并检测。`requirements.txt` 已针对 Python 3.8（Ubuntu 20.04 自带版本）
锁定兼容的 OR-Tools 版本。

### 两套求解器的口径差异（重要）

页面左侧“求解方式”提供三档：

- `自动选择 CP-SAT/启发式`：优先用 CP-SAT；不可用时明确回退到启发式。
- `仅启发式`：始终使用内置快速调度逻辑（与汇报材料的五情景数字一致）。
- `仅 CP-SAT`：只使用 OR-Tools CP-SAT；不可用时直接提示错误，不伪装成 CP-SAT。

部署默认口径是 **启发式**（`PLATFORM_DEFAULT_SOLVER=heuristic`），这样 KPI 卡片与
“五情景效益对比”同口径、自洽；答辩现场可以在页面上切到 CP-SAT 演示。

需要注意的是，CP-SAT 会自由选择最省钱的车辆（可用车队里有 6 台纯电车），
而第三章口径要求 C3/D3 的两条长线保留燃油车，因此 CP-SAT 下的情景数值
与启发式不同（详见部署文档第 7 节）。

---

## 四、求解器怎么换

后台真正的切换入口在 `simulation/scheduler.py`。对外统一接口始终是：

```python
solve_scenario(scenario, data, params)
```

因此前端、服务器和CSV数据结构不需要跟着求解器一起改。

以后要接入其他求解器（例如 ALNS、Gurobi、COPT），只需要：

1. 新建模块，例如 `simulation/alns_scheduler.py`；
2. 实现同名接口：

```python
def solve_with_alns(scenario, data, params):
    return {
        'scenario': scenario,
        'solver': 'alns',
        'schedule': [],
        'kpi': {},
        'notes': []
    }
```

3. 在 `simulation/scheduler.py` 的 `solve_scenario` 中增加分支；
4. 页面只增加一个选项，不用改数据库和接口。

情景 ID、默认等待上限、里程折算系数等口径统一放在
`simulation/scenarios.py`，避免前后端与两个求解器各写一份。

---

## 五、代码文件说明

**服务与配置**

- `server.py`：生产入口（http.server），含静态文件、安全响应头、请求限制、启动自检
- `wsgi.py`：可选 WSGI 入口（gunicorn / 宝塔“Python 项目”）
- `app/config.py`：环境变量配置（端口、数据目录、并发、缓存、默认口径）
- `app/api.py`：API 路由、参数校验、结果缓存、健康检查、静态资源解析

**前端**

- `app/static/index.html`：页面结构
- `app/static/styles.css`：企业驾驶舱视觉样式
- `app/static/app.js`：页面交互、求解调用和结果展示
- `app/static/favicon.svg`：站点图标

**求解**

- `simulation/scenarios.py`：情景定义与口径常量
- `simulation/scheduler.py`：求解器切换入口、CSV 数据加载（UTF-8/GBK 自适应、列校验）
- `simulation/scheduler.py::_solve_heuristic`：内置启发式调度器
- `simulation/cpsat_scheduler.py`：OR-Tools CP-SAT 调度模型

**部署与文档**

- `deploy/install.sh`：一键部署（虚拟环境、依赖、systemd、健康检查）
- `deploy/check.sh`：部署体检
- `deploy/run.sh`：前台调试运行
- `deploy/platform.service`：systemd 服务模板
- `deploy/platform.env.example`：运行配置模板
- `deploy/nginx-baota-site.conf` / `deploy/nginx-standalone.conf`：Nginx 反向代理模板
- `data/demo/`：演示CSV数据；`data/templates/`：给企业或队友填写的数据模板
- `tests/`：单元与端到端测试（接口、HTTP 服务、WSGI、求解器）
- `docs/部署到Ubuntu宝塔面板.md`：服务器部署手册
- `docs/求解器替换与代码说明.md`：给队友阅读的求解器说明

---

## 六、代码附件怎么交给队友

推荐直接打包整个 `platform_v1` 文件夹，不要只发一个 `server.py`。
队友收到压缩包后：

1. 解压；
2. 安装 Python 3.8+；
3. 在文件夹里运行：

```bash
python -m pip install -r requirements.txt
python server.py --host 0.0.0.0 --port 8787
```

4. 浏览器打开 `http://127.0.0.1:8787`。

如果只是演示平台流程，不安装 OR-Tools 也能运行；如果答辩时强调“CP-SAT 求解”，
必须安装 OR-Tools，并在页面上选择 `仅 CP-SAT` 确认能够成功出结果。

---

## 七、测试

```bash
python -m unittest discover -s tests -v
```

- 没有 OR-Tools 时，测试中的 CP-SAT 部分会自动跳过，其余全部执行；
- 测试覆盖：五情景求解、CSV 容错（GBK/缺列）、API 契约与错误码、
  真实 HTTP 服务（静态资源、304、404、目录穿越）、WSGI 入口。

---

## 八、接口一览（部署/联调用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 前端页面 |
| GET | `/api/health` | 健康检查（Nginx/监控探活用） |
| GET | `/api/config` | 前端默认口径与情景元数据 |
| GET | `/api/demo` | 演示数据 |
| GET | `/api/scenarios` | 情景列表 |
| GET | `/api/solver/status` | 求解器可用性 |
| GET | `/api/compare?solver=` | 五情景对比 |
| POST | `/api/solve` | 生成调度方案，body：`{"scenario":"D3","params":{"wait_min":18,"solver":"auto"}}` |
