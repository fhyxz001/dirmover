# DirMover

极简的「定时目录迁移」工具：用 Web UI 管理多组「源目录 → 目标目录」任务，内置调度器按 cron 自动把源目录**下一级的全部内容**（含隐藏文件）移动到目标目录，无需系统 crontab、无需数据库、无需登录。

- 后端：Python + FastAPI + APScheduler
- 前端：单页原生 HTML / CSS / JS，无 UI 框架
- 存储：JSON 文件（`data/tasks.json`、`data/runs.json`）
- 部署：单机运行，支持 systemd / Docker / 离线镜像

## 功能

- 任务 CRUD、启用/禁用、立即执行、试运行（dry-run）
- 每个任务可配置：名称、源目录、目标目录、cron 表达式、同名冲突策略（跳过 / 覆盖 / 重命名）、启用开关
- 任务列表展示：源 → 目标、cron、下次执行时间、上次执行时间与结果（成功 / 失败 / 移动数量）
- 执行记录：时间、触发方式（定时 / 手动）、移动条目数 / 文件数 / 目录数、跳过与失败详情
- 内置调度器：修改 cron 或启停后立即生效，服务重启自动恢复
- 健壮性：路径校验（源目录存在可读、目标不能在源目录内部）、文件锁防并发、JSON 原子写入、控制台 + 滚动文件日志

## 项目结构

```
dirmover/
├── app/
│   ├── main.py           # FastAPI 入口：API + 静态页 + 调度器生命周期
│   ├── config.py         # 环境变量配置
│   ├── migrator.py       # 迁移核心（移动/冲突处理/试运行/路径校验）
│   ├── scheduler.py      # APScheduler 封装，cron 校验
│   ├── service.py        # 执行任务：加锁 → 迁移 → 记录
│   ├── store.py          # JSON 持久化（原子写）
│   ├── locks.py          # 跨平台文件锁
│   ├── logging_setup.py  # 控制台 + 文件日志
│   └── static/           # 单页 UI（index.html / style.css / app.js）
├── tests/                # pytest 测试
├── deploy/dirmover.service
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .github/workflows/docker.yml   # CI：测试 + 构建离线镜像
```

## 快速开始

要求 Python 3.10+（推荐 3.11）。

```bash
cd dirmover
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 或：python -m app.main
```

浏览器打开 <http://127.0.0.1:8000>，点「新增任务」即可。

开发 / 测试：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DIRMOVER_HOST` | `0.0.0.0` | 监听地址 |
| `DIRMOVER_PORT` | `8000` | 监听端口 |
| `DIRMOVER_DATA` | `./data` | 任务与执行记录目录 |
| `DIRMOVER_LOG_DIR` | `./logs` | 日志目录 |
| `DIRMOVER_LOG_LEVEL` | `INFO` | 日志级别 |
| `DIRMOVER_RUN_HISTORY` | `50` | 每个任务保留的执行记录条数 |
| `DIRMOVER_SCHEDULER` | `1` | 设为 `0` 关闭内置调度器（仅调试 API 时用） |

## 调度说明

- 使用 5 段标准 cron：`分 时 日 月 周`，按**本机时区**触发（容器内用 `TZ=Asia/Shanghai` 指定）。
- 常用预设：每 5/15/30 分钟、每小时整点、每 6 小时、每天 00:00 / 02:00、每周一 02:00、每月 1 日 02:00，也可直接手写 cron。
- 修改 cron、启停任务后调度立即重建；服务启动时自动恢复所有启用任务。

## 迁移语义与冲突策略

等价于：

```bash
find "$SOURCE" -mindepth 1 -maxdepth 1 -exec mv -t "$TARGET" -- {} +
```

- 只处理源目录的**第一层**内容（文件和子文件夹，含 `.hidden`），子文件夹内部层级原样保留；
- 目标目录不存在时自动创建；
- 跨磁盘/分区时自动退化为「复制 + 删除」；
- 同名冲突：`skip`（默认，跳过并记录警告）/ `overwrite`（先删目标同名项）/ `rename`（`a.txt` → `a_1.txt`）；
- 软链接只移动链接本身，不跟随。

**试运行**只列举将要移动的条目，不移动文件、也不创建目标目录。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks` | 任务列表（含下次执行时间、上次结果） |
| POST | `/api/tasks` | 新增任务 |
| PUT | `/api/tasks/{id}` | 编辑任务（调度立即生效） |
| DELETE | `/api/tasks/{id}` | 删除任务及记录 |
| POST | `/api/tasks/{id}/toggle` | 启用/禁用 |
| POST | `/api/tasks/{id}/run` | 立即执行，body `{"dry_run": true}` 为试运行 |
| GET | `/api/tasks/{id}/runs?limit=20` | 最近执行记录 |
| GET | `/api/health` | 健康检查 |

## 常驻运行

### systemd

```bash
sudo useradd -r -s /usr/sbin/nologin dirmover
sudo mkdir -p /opt/dirmover /var/lib/dirmover /var/log/dirmover
sudo rsync -a ./ /opt/dirmover/
cd /opt/dirmover && sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo chown -R dirmover:dirmover /opt/dirmover /var/lib/dirmover /var/log/dirmover

sudo cp deploy/dirmover.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dirmover
sudo systemctl status dirmover
```

需要迁移的目录要保证 `dirmover` 用户有读写权限；按实际路径修改 unit 文件里的 `User` / `WorkingDirectory`。

### 后台方式（无 systemd）

```bash
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  >> logs/service.out 2>&1 &
```

### Docker

```bash
docker build -t dirmover:latest .
docker run -d --name dirmover --restart unless-stopped \
  -p 8000:8000 -e TZ=Asia/Shanghai \
  -v "$PWD/data:/data" -v "$PWD/logs:/logs" \
  -v /path/on/host/inbox:/mnt/inbox \
  -v /path/on/host/archive:/mnt/archive \
  dirmover:latest
```

注意：容器内执行迁移，任务里填写的是**容器内路径**（如 `/mnt/inbox`），源/目标目录都要挂载进容器。也可以直接 `docker compose up -d`（见 `docker-compose.yml`）。

## 离线镜像（GitHub Actions）

`.github/workflows/docker.yml` 在每次 push / PR / 手动触发时：

1. 跑 pytest；
2. `docker build` 构建镜像（linux/amd64）；
3. `docker save | gzip` 导出离线镜像，作为 Actions Artifact 上传（30 天）；
4. 非 PR 场景推送到 GHCR：`ghcr.io/fhyxz001/dirmover:latest`、`:版本号`；
5. 打 `v*` tag 时，自动创建 GitHub Release 并附带离线镜像 tar.gz 与 `SHA256SUMS.txt`。

使用离线镜像：

```bash
# 从 Actions 运行页下载 artifact，或从 Release 下载
gunzip -c dirmover-1.0.0-linux-amd64.tar.gz | docker load
docker images | grep dirmover

docker run -d --name dirmover --restart unless-stopped \
  -p 8000:8000 -e TZ=Asia/Shanghai \
  -v "$PWD/data:/data" -v "$PWD/logs:/logs" \
  -v /path/inbox:/mnt/inbox -v /path/archive:/mnt/archive \
  ghcr.io/fhyxz001/dirmover:1.0.0
```

无外网的机器上，只需把 tar.gz 拷过去 `docker load` 即可运行，镜像内已包含全部 Python 依赖。

## 数据与日志

- `data/tasks.json`：任务配置（含每条任务的 `last_run` 摘要）
- `data/runs.json`：每个任务最近 N 条执行记录
- `data/locks/*.lock`：执行锁文件
- `logs/dirmover.log`：滚动日志（2 MB × 3）

两个 JSON 文件都是原子写入，可随时备份；删除任务会同时清掉它的执行记录。

## 安全提示

工具本身无登录鉴权，默认监听 `0.0.0.0`。请只在可信内网使用，或绑定 `127.0.0.1` 后通过反向代理加认证；不要把迁移路径配置为系统关键目录。
