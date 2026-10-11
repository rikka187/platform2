# 部署到 Ubuntu Server 20.04 + 宝塔面板（/www/wwwroot）

本文件是给“把平台放到服务器上做成一个网站”用的完整操作手册。
按顺序做完第 1~4 节，就能用域名打开平台。

- 目标系统：Ubuntu Server 20.04 LTS 64bit（宝塔面板）
- 站点根目录：`/www/wwwroot`
- 运行方式：Python 服务只监听 `127.0.0.1:8787`，由 Nginx 反向代理对外
- 一次性依赖：Python 3.8（系统自带）+ OR-Tools（安装脚本自动装）

---

## 0. 架构与目录

```text
浏览器 ──► Nginx（80/443，宝塔站点）──► 127.0.0.1:8787（Python 服务，systemd 托管）
                                              │
                                              ├─ /api/*   接口（求解、对比、健康检查）
                                              └─ /        前端页面（app/static）
```

```text
/www/wwwroot/platform_v1/
├── server.py                 生产入口（http.server，systemd 调用）
├── wsgi.py                   可选入口（gunicorn / 宝塔“Python 项目”）
├── app/
│   ├── config.py             环境变量配置
│   ├── api.py                路由 / 校验 / 缓存 / 健康检查
│   └── static/               前端页面（index.html、app.js、styles.css）
├── simulation/               启发式 + CP-SAT 求解器
├── data/demo/                演示数据（6 个 CSV，可整体替换）
├── deploy/                   部署脚本与模板
│   ├── install.sh            一键安装
│   ├── check.sh              一键体检
│   ├── run.sh                前台调试运行
│   ├── platform.service      systemd 服务模板
│   ├── platform.env.example  运行配置模板
│   ├── nginx-baota-site.conf 宝塔站点配置模板
│   └── nginx-standalone.conf 系统 Nginx 配置模板
└── docs/部署到Ubuntu宝塔面板.md   本文件
```

**为什么不用宝塔“Python 项目”也能跑？** 本项目是纯标准库应用，自带 HTTP 服务，
用 systemd 托管最稳；宝塔面板只负责 Nginx 和证书。想用面板的 Python 项目管理，
见第 8 节（已提供 `wsgi.py`）。

---

## 1. 上传代码

### 方式 A：面板上传（推荐）

1. 本地把 `platform_v1` 整个文件夹打成 zip（不要只传 `server.py`）。
2. 宝塔面板 → **文件** → 进入 `/www/wwwroot` → **上传** → 上传 zip。
3. 右键 zip → **解压**，得到 `/www/wwwroot/platform_v1`。
4. 确认解压后目录里能看到 `server.py` 和 `deploy/`。

### 方式 B：命令行上传

```bash
# 本地（Windows PowerShell）
scp -r D:\platform2\platform_v1 root@服务器IP:/www/wwwroot/
```

### 从 Windows 上传后必做一步（换行符）

Windows 编辑器保存的 `.sh` 是 CRLF 换行，Linux 下 bash 会报
`/usr/bin/env: 'bash\r': No such file or directory`。统一处理：

```bash
cd /www/wwwroot/platform_v1
sed -i 's/\r$//' deploy/*.sh
```

> 用 `scp`/`zip` 原样上传的文件通常已经是 LF，跑一次上面的命令也没有副作用。

---

## 2. 一键安装（systemd）

```bash
cd /www/wwwroot/platform_v1
sudo bash deploy/install.sh
```

脚本会依次完成：

1. 检查系统版本与 Python（Ubuntu 20.04 自带 3.8.10，满足要求）；
2. 在项目目录创建独立虚拟环境 `.venv`；
3. 安装依赖 `requirements.txt`（含 OR-Tools；官方源失败自动切换清华/阿里镜像）；
4. 生成运行配置 `deploy/platform.env`（已存在则不覆盖）；
5. 注册 systemd 服务 `yinli-platform` 并启动；
6. 健康检查通过后打印访问地址和后续步骤。

常用参数：

```bash
sudo bash deploy/install.sh --port 9000                # 换端口
sudo bash deploy/install.sh --user www                # 用 www 用户运行
sudo bash deploy/install.sh --with-nginx demo.example.com   # 顺手写 Nginx 配置
sudo bash deploy/install.sh --skip-deps                # 复用已有 .venv，不重装依赖
sudo bash deploy/install.sh --uninstall                # 卸载服务（保留文件）
```

只看自检、不安装：

```bash
.venv/bin/python server.py --check-only
```

期望输出示例：

```text
[信息] Python 3.8.10（/www/wwwroot/platform_v1/.venv/bin/python）
[信息] 静态目录：/www/wwwroot/platform_v1/app/static
[信息] 数据目录：/www/wwwroot/platform_v1/data/demo（orders 8、trips 8、vehicles 10、...）
[信息] CP-SAT 可用：OR-Tools 9.7.2996
[信息] 页面默认口径：heuristic；对比口径：heuristic
自检结果：通过
```

服务管理：

```bash
systemctl status yinli-platform      # 状态
systemctl restart yinli-platform     # 重启（改配置后执行）
systemctl stop yinli-platform        # 停止
journalctl -u yinli-platform -f      # 实时日志
```

---

## 3. 在宝塔建站并反向代理（关键）

### 3.1 添加站点

面板 → **网站** → **添加站点**：

| 项 | 填写 |
| --- | --- |
| 域名 | 你的域名（如 `demo.example.com`），已解析到服务器公网 IP |
| 根目录 | 默认 `/www/wwwroot/demo.example.com` 即可（页面文件由 Python 服务返回） |
| PHP 版本 | **纯静态** |
| 数据库 | 不创建 |

### 3.2 添加反向代理

站点 → **设置** → **反向代理** → **添加反向代理**：

| 项 | 填写 |
| --- | --- |
| 代理名称 | `platform` |
| 目标 URL | `http://127.0.0.1:8787` |
| 发送域名 | `$host` |

### 3.3 删除面板默认的 js/css 静态规则（最容易踩的坑）

宝塔站点默认配置里有这两段正则 location：

```nginx
location ~ .*\.(gif|jpg|jpeg|png|bmp|swf)$ { expires 30d; ... }
location ~ .*\.(js|css)?$ { expires 12h; ... }
```

Nginx 里**正则 location 优先级高于 `location /`**，所以 `/app.js`、`/styles.css`
会被 Nginx 直接去磁盘找文件，结果 404，页面变成没有样式的白板。

处理办法（二选一）：

- 站点 → **设置** → **配置文件**，把这两段 `location ~ ...` 注释掉（前面加 `#`）或删除，保存；
- 或者直接用仓库里的模板整体覆盖：`deploy/nginx-baota-site.conf`
  （把 `__DOMAIN__`、`__PORT__`、`__APP_DIR__` 替换成实际值）。
  注意覆盖会丢掉面板生成的 `#SSL-START ... #SSL-END` 段，所以请**先覆盖配置、再申请证书**。

也可以让安装脚本代劳：

```bash
sudo bash deploy/install.sh --with-nginx demo.example.com
```

### 3.4 开启 HTTPS

站点 → **设置** → **SSL** → **Let's Encrypt** → 申请 → **强制 HTTPS**。

### 3.5 放行端口

- 宝塔 → **安全**：放行 `80`、`443`（若开了防火墙）。
- 云厂商控制台 → **安全组**：放行 `80`、`443`。
- **不要**放行 `8787`：服务只监听 `127.0.0.1`，对外不可见才是正确状态。

---

## 4. 验收清单

打开 `https://你的域名`，逐项确认：

- [ ] 页面正常显示（标题“银犁物流仓配一体化协同平台”，有样式、有图标）
- [ ] 左侧“求解器状态”显示 CP-SAT 可用（安装成功时）
- [ ] 点击 **生成调度方案**，KPI 卡片、调度计划表、月台甘特图都有数据
- [ ] 切换情景（H0/S0/S1/C3/D3）后重新生成，结果变化
- [ ] “五情景效益对比”柱子按 H0 > S0 ≈ S1 > C3 > D3 递减
- [ ] **导出 KPI 报表**能下载 CSV
- [ ] `https://你的域名/api/health` 返回 `{"status": "ok", ...}`
- [ ] 随机路径（如 `/nope`）返回 404 页面，而不是目录列表

命令行体检：

```bash
bash deploy/check.sh
```

---

## 5. 日常运维

| 需求 | 操作 |
| --- | --- |
| 改端口/并发/默认求解口径 | 编辑 `deploy/platform.env` → `systemctl restart yinli-platform` |
| 换演示数据 | 覆盖 `data/demo/*.csv`（列名照 `data/templates/`）→ 刷新页面即生效 |
| 用别处的数据目录 | `platform.env` 里设 `PLATFORM_DATA_DIR=/www/wwwroot/mydata` |
| 看访问日志 | `journalctl -u yinli-platform -f` |
| 更新代码 | 覆盖文件 → `systemctl restart yinli-platform`（依赖变更再跑一次 `install.sh`） |
| 备份 | 备份整个 `platform_v1` 目录（`data/` 与 `deploy/platform.env` 最重要） |

数据文件要求：目录内必须有 `orders.csv`、`trips.csv`、`vehicles.csv`、`docks.csv`、
`prep.csv`、`charging.csv` 六个文件，表头与 `data/templates/` 一致。
编码支持 UTF-8（含 BOM）、GBK/GB18030；缺文件或缺列时页面会直接给出中文错误提示。

---

## 6. 环境变量一览（deploy/platform.env）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PLATFORM_HOST` | `127.0.0.1` | 监听地址。生产环境保持本机回环，由 Nginx 代理 |
| `PLATFORM_PORT` | `8787` | 监听端口（与反向代理目标一致） |
| `PLATFORM_DATA_DIR` | `data/demo` | CSV 数据目录（可指向 `/www/wwwroot/mydata`） |
| `PLATFORM_DEFAULT_SOLVER` | `heuristic` | 页面默认求解口径：`heuristic` / `auto` / `cpsat` |
| `PLATFORM_COMPARE_SOLVER` | `heuristic` | 五情景对比默认口径 |
| `PLATFORM_MAX_CONCURRENT_SOLVES` | `2` | 同时求解的请求数，超出的排队。1 核 2G 建议 1~2 |
| `PLATFORM_SOLVE_QUEUE_TIMEOUT` | `120` | 排队超时（秒），超时返回 503 |
| `PLATFORM_SOLVE_CACHE_TTL` | `300` | 相同参数结果缓存秒数，0 关闭 |
| `PLATFORM_CPSAT_MAX_TIME` | `10` | 单次 CP-SAT 求解时间上限（秒） |
| `PLATFORM_CPSAT_WORKERS` | `0` | CP-SAT 搜索线程数，0=按 CPU 自动（上限 4） |
| `PLATFORM_MAX_BODY_BYTES` | `65536` | POST 请求体上限（字节） |
| `PLATFORM_ACCESS_LOG` | `1` | 是否打印逐条访问日志 |

---

## 7. 关于“求解口径”的说明（重要）

平台有两套求解器，**它们的 KPI 口径不同**，因此页面默认口径设为启发式：

| | 启发式（heuristic） | CP-SAT |
| --- | --- | --- |
| 成本口径 | 路线成本 + 等待成本 + 能源成本（与汇报材料一致） | 同一套成本项，但车辆/月台由模型自由优化 |
| 五情景结果 | H0 2724 → S0/S1 2554 → C3 2093 → D3 2045 元，碳排 382 → 313 → 137 → 137 kg | H0 1945 → S0/S1 1915 → C3 2093 → D3 2045 元，碳排 84 → 69 → 137 → 137 kg |
| 求解时间 | 毫秒级 | 单情景 20~350 毫秒（数据量很小） |

原因：CP-SAT 会自由选择最省钱的车辆（可用车队里有 6 台纯电车），
而启发式按“H0/S0/S1 现状口径 → C3/D3 新能源替换”的章节口径排班；
CP-SAT 里 C3/D3 又被第三章假设强制 R2/R4 用燃油车，所以它的碳排反而高于基准。
这是**模型口径差异，不是代码缺陷**。

因此本项目的部署默认：

- `PLATFORM_DEFAULT_SOLVER=heuristic`：页面初始就是汇报口径，KPI 卡片与五情景对比同口径、自洽；
- 现场答辩要演示 CP-SAT：在页面“求解方式”里选 **自动选择 CP-SAT/启发式** 或 **仅 CP-SAT**，
  卡片与对比图会同时切到 CP-SAT 口径（对比图会带上当前求解方式）。

如果你希望“装好 OR-Tools 后默认就走 CP-SAT”，把 `platform.env` 改成：

```ini
PLATFORM_DEFAULT_SOLVER=auto
PLATFORM_COMPARE_SOLVER=auto
```

---

## 8. 可选：用宝塔“Python 项目”+ gunicorn 部署

不想用 systemd 时，可以用面板自带的 Python 项目管理器（宝塔 7.7+ 为
**网站 → Python 项目**）。项目已经提供了 WSGI 入口，开箱可用：

| 面板项 | 填写 |
| --- | --- |
| 项目路径 | `/www/wwwroot/platform_v1` |
| 启动方式 | `gunicorn` |
| 启动文件 | `wsgi.py` |
| 应用对象 | `application` |
| 端口 | `8787`（与站点反向代理目标一致） |
| 依赖文件 | `requirements.txt`（另外执行 `pip install -r deploy/requirements-gunicorn.txt`） |

等价的手工命令：

```bash
cd /www/wwwroot/platform_v1
.venv/bin/pip install -r deploy/requirements-gunicorn.txt
.venv/bin/gunicorn -w 2 -k gthread --threads 4 -b 127.0.0.1:8787 wsgi:application
```

注意：两种方式不要同时占用 8787；切换前先 `systemctl stop yinli-platform`。

---

## 9. 常见问题排查

| 现象 | 原因与处理 |
| --- | --- |
| `bash: $'\r': command not found` | 脚本是 CRLF：`sed -i 's/\r$//' deploy/*.sh` |
| 页面打开是白板/没有样式 | 站点配置里默认的 `location ~ .*\.(js|css)?$` 抢走了静态请求，见 3.3 节 |
| 502 Bad Gateway | Python 服务没起来：`systemctl status yinli-platform`、`journalctl -u yinli-platform -n 50` |
| 端口被占用 | `ss -ltnp | grep 8787` 找到占用进程；或改 `PLATFORM_PORT` |
| 页面提示“输入数据有问题/缺少必需列” | CSV 列名与 `data/templates/` 不一致，或文件缺失；错误信息里会写明文件和列名 |
| CSV 是 Excel 另存的乱码 | 支持 GBK/UTF-8；保险做法是 Excel 另存为“CSV UTF-8(逗号分隔)”后重传 |
| 求解按钮报“CP-SAT 不可用” | 依赖没装成功：`.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`，然后重启服务 |
| 五情景对比柱子和 KPI 对不上 | 求解口径不同，见第 7 节；把 `PLATFORM_DEFAULT_SOLVER` 与页面下拉框统一即可 |
| 修改 `platform.env` 不生效 | 需要 `systemctl restart yinli-platform` |
| HTTPS 申请失败 | 域名未解析/未备案/80 端口被安全组拦截；先在安全组放行 80 |
| 健康检查一直失败 | 用 `bash deploy/check.sh` 逐项定位 |

### 需要临时看堆栈

```bash
bash deploy/run.sh            # 前台运行，日志直接打在终端
```

---

## 10. 安全与性能提示

- 服务绑定 `127.0.0.1`，公网只暴露 Nginx 的 80/443；**不要**把 8787 开到公网。
- 已内置：请求体大小限制、并发求解排队、静态目录穿越防护、关闭目录列表、
  `nosniff/SAMEORIGIN` 响应头、JSON 统一错误结构。
- systemd 单元启用了 `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=full` 等加固项。
- 数据量很小（8 个趟次），1 核 2G 服务器足够；并发求解上限默认 2，可按 CPU 调整。
- 平台是演示原型：`on_time_rate`（准时率）与“执行监控”面板是展示口径，
  不是实时计算值；`data/` 里的是演示数据。若要接真实业务数据，按第 5 节替换 CSV 即可。
