# sync-python 运营手册

> 适用：负责 `sync-python` 的运维 / 联调 / 定时化。本文是操作指引，**不涉及代码改动**。
> 关联：[`stage1-minimal-contract.md`](../../docs/stage1-minimal-contract.md)、[`data-completeness-plan.md`](../../docs/data-completeness-plan.md)、[`generalization-proposal.md`](../../docs/generalization-proposal.md)
> 作者：zm ｜ 日期：2026-10-23

---

## 1. 本地灌库操作指引（对应 Issue #61）

### 1.1 前置准备

- Python 3.11+，安装依赖（见 `sync-python` 的 `requirements.txt` / `pyproject.toml`）。
- **敏感值只从环境变量读取，不写入仓库**（见 `config.py`）。常用变量：
  - `OTAKU_INTERNAL_TOKEN`：Java 后端导入令牌（**正式提交必需**；`--dry-run` 不需要）。
  - `OTAKU_SOURCE_BASE_URL`：来源站地址（默认 `http://154.8.194.198:8000`）。
  - `OTAKU_JAVA_BASE_URL`：后端地址（默认 `http://localhost:8080`）。
  - `OTAKU_ARCHIVE_PATH`：Bangumi 档案 `dump.zip` 路径（`--source bangumi` 必需）。
  - `OTAKU_SOURCE`：`otaku_site`（默认）或 `bangumi`。
  - `OTAKU_WORK_TYPES`：逗号分隔，默认 `anime,comic,game,novel`。
  - `OTAKU_BATCH_SIZE`：每批条数，默认 100（**上限 500**，超限直接报错退出）。
  - `OTAKU_SAMPLE_METHOD`：`head`（按档案顺序取前 N 条）或 `uniform`（蓄水池随机抽样）。
  - 其余限流参数（`OTAKU_SOURCE_PAGE_SIZE` / `OTAKU_REQUEST_INTERVAL_SECONDS` / `OTAKU_MAX_PAGES` / `OTAKU_TIMEOUT_SECONDS` / `OTAKU_MAX_ATTEMPTS` / `OTAKU_BACKOFF_SECONDS` / `OTAKU_OUTPUT_DIR`）均可环境变量覆盖，详见 `config.py`。

### 1.2 Bangumi 档案灌库（约 55.6 万条）

档案 = Bangumi 官方数据集，本项目只读 `subject.jsonlines`（**不含图片字段 → 封面全空**，见 `data-completeness-plan.md`）。

1. **先看规模**：统计各契约类型的条数分布
   ```bash
   python -m sync --source bangumi --archive dump.zip --stats
   ```
2. **小批量 dry-run 验证载荷**（不联系 Java，只落盘将要发送的载荷）
   ```bash
   python -m sync --source bangumi --archive dump.zip --limit 100 --dry-run
   ```
   检查 `runs/batch-*.json` 里的条目字段是否合理。
3. **抽代表性样本**（蓄水池抽样，消除「按 id 顺序取前 N 条」的偏差）
   ```bash
   python -m sync --source bangumi --archive dump.zip --sample uniform --limit 5000 --dry-run
   ```
4. **正式全量**（去掉 `--dry-run`，需 `OTAKU_INTERNAL_TOKEN`）
   ```bash
   python -m sync --source bangumi --archive dump.zip
   ```
   - **流式处理**：`iter_many` / `fetch_sample` 不整体载入内存，内存只保留当前批次（`batch_size`），可吃下全量 55.6 万条（全量整体载入外推约 900MB，故必须走流式）。
   - 纯本地 zip 读取，**不请求来源站网络**；限流只作用于「提交后端」阶段。

### 1.3 Otaku 站点灌库（约 1.8 万条）

默认来源即 `otaku_site`，读环境变量即可：

```bash
python -m sync            # 等价于 --source otaku_site，默认同步四类
```

- **串行拉取**，请求间隔 `0.6s`（≈ 1.67 req/s），遵守来源站限流口径（`page_size=200`、并发 1、每天 ≤ 5 万请求）。
- 来源站是公网服务，实测约 23 / 55 页会断连；抓取层有**指数退避重试 + 失败页记账**，跑一次收多少是多少，失败的页重跑补齐。

### 1.4 运行报告与失败补齐

每次运行在 `runs/` 下生成 `run-<时间戳>.json`（`RunReport`），终端也打印摘要。关键字段：

| 字段 | 含义 | 处理 |
|---|---|---|
| `fetched` / `accepted` / `rejected` | 抓取数 / 通过校验数 / 被拒数 | 被拒看 `rejected_items` 原因 |
| `batches[].delivered` | 每批是否送达后端 | 未送达需重跑该批 |
| `failed_pages` | 重试到底仍失败的页 | **非空 = 本次不是全量**，重跑同一命令补齐（按 `(source, source_id)` 幂等，不重复入库） |
| `detail_failures` | 资讯详情页抓取失败的条目（**资讯链路专有**） | 这类条目**仍已入库**（标题/摘要/时间/封面齐，只缺正文与原文链接）；既非 `rejected` 也非整页失败 → 重跑只需补抓这几条详情 |
| `server_error_items` | 被服务端故障拒绝的条目（如并发导入 `DuplicateKey`，Issue #81） | 数据本身没问题，用同一 `batch_id` 重跑补齐 |
| `rejected_items` | 数据问题（`TITLE_REQUIRED` / `INVALID_RELEASE_YEAR` 等） | 重试无益，需查源数据 |

### 1.5 注意事项

- **开跑前先自检**：`python -m sync --health` —— 几秒钟确认来源站 / Java 后端可达、内部令牌已配置。一轮全量几十分钟，别等跑完才发现提交不上去（详见 `README.md`「跑同步前先自检」）。
- **单批上限 500 条**（`--batch-size > 500` 直接报错退出）。
- **Ctrl-C 中断**会先落盘已完成报告，不会全丢。
- **封面**：bangumi 档案无图片字段，导入后 `cover_url` 全空（已知缺口，W3 路线 C 接受 + 客户端兜底）。
- **评分**：bangumi `score==0` 映射为 `None`（无人评分 ≠ 0 分），非空率约 36%（W3 不补）。

---

## 2. 定时化运行方案

### 2.1 现状

`sync-python` 是**一次性批处理**：`python -m sync ...` 跑完即退出，`__main__.py` 无内置 scheduler，需外部定时触发（`config.py` 注释已写明「需外部 cron」）。

### 2.2 推荐方案：外部调度

**不内置 scheduler**（避免常驻进程 + 多实例协调复杂度）。用操作系统级定时器：

- Linux / macOS：`cron` 或 `systemd.timer`
- 容器：镜像内 cron（如 `fcron`）或 Sidecar
- 开发机临时：本地 cron 暂代（无服务器时的过渡）

### 2.3 crontab 示例

```cron
# 每日 03:00 跑来源站全量（串行、限流内，约 1.8 万条）
# flock -n：拿不到锁立即放弃，防止上一次没跑完又叠加一轮（会打爆来源站限流）
0 3 * * *  flock -n /tmp/sync-site.lock -c 'cd /path/to/sync-python && /usr/bin/python -m sync --source otaku_site >> runs/cron.log 2>&1'

# 每周日 04:00 跑 Bangumi 档案全量（静态快照，幂等重跑安全）
0 4 * * 0  flock -n /tmp/sync-archive.lock -c 'cd /path/to/sync-python && OTAKU_ARCHIVE_PATH=/data/dump.zip /usr/bin/python -m sync --source bangumi >> runs/cron.log 2>&1'

# 每日 03:50 补跑：来源站全量常有断连页，隔 50 分钟再跑一次补齐 failed_pages（幂等，不会重复入库）
50 3 * * *  flock -n /tmp/sync-site.lock -c 'cd /path/to/sync-python && /usr/bin/python -m sync --source otaku_site >> runs/cron.log 2>&1'
```

> 来源站每天 ≤ 5 万请求口径，单次全量 1.8 万条远低于此；Bangumi 档案不请求来源站网络。
> **不要依赖退出码判断成败**——见 §2.5（退出码恒为 0）。

### 2.4 限流 / 重试 / 失败记账（已在代码，方案层无需处理）

- **限流**：`OtakuSiteSource._throttle`，间隔 `0.6s`（可用 `OTAKU_REQUEST_INTERVAL_SECONDS` 调）。
- **重试**：`RETRYABLE_STATUS`（408 / 425 / 429 / 5xx）+ `TransportError`，指数退避 2 / 4 / 8s，`max_attempts=3`。
- **失败记账**：`failed_pages` 写进 `RunReport`，供重跑补齐。

### 2.5 监控告警

#### ⚠️ 先记住：退出码恒为 0，不能只看它

`__main__.py` 的 `main()` 在正常路径上一律 `return 0`（见 `__main__.py` 的 4 处 `return 0`）。
**即使 `failed_pages` 非空（本次根本没拿到全量），进程退出码仍是 0。**

所以 cron / systemd 按惯例「看退出码判断成败」会**全部漏报**。正确做法：跑完之后**后处理检查 `RunReport`**，由检查脚本决定成败退出码。

#### 检查脚本（放到 cron 里串在 sync 之后）

仓库已提供 **`scripts/check_run_report.py`**，不依赖 `jq`（环境不一定有），直接读最近一份 `runs/run-*.json`：

```bash
# 检查 runs/ 下最新的报告
python scripts/check_run_report.py

# 指定某份报告
python scripts/check_run_report.py --report runs/run-20260101-030000.json

# 放宽 rejected 占比告警阈值（默认 5%）
python scripts/check_run_report.py --threshold 0.1
```

退出码约定（cron / systemd 靠它判断成败）：

| 退出码 | 含义 |
|---|---|
| `0` | 正常，无需处置 |
| `1` | 发现需要处置的问题（判定口径见下） |
| `2` | 没找到报告，或报告无法解析 —— 与「有问题」区分开，便于分别处置 |

判定逻辑在脚本的 `evaluate()` 里，判定与退出码均有测试覆盖（`tests/test_check_run_report.py`）。
**不要在本文里另抄一份脚本**——改动一律落到 `scripts/check_run_report.py`，避免两份实现漂移。

串进 cron（检查脚本非 0 退出 → 邮件/告警通道才能收到）：

```cron
0 3 * * *  flock -n /tmp/sync-site.lock -c 'cd /path/to/sync-python && /usr/bin/python -m sync --source otaku_site >> runs/cron.log 2>&1 && /usr/bin/python scripts/check_run_report.py'
```

#### 告警判定口径

| 指标 | 判定 | 处置 |
|---|---|---|
| `failed_pages` 非空 | 本次**未全量** | 重跑同一命令（幂等，不重复入库） |
| `server_error_items` 非空 | 服务端故障（如并发 `DuplicateKey`） | 同 `batch_id` 重跑补齐 |
| `detail_failures` 非空 | 资讯条目缺正文/原文链接（其余字段已入库） | 重跑补抓这几条详情 |
| `rejected` 占比 > 5% | 可能来源站字段变化 | 人工看 `rejected_items` 原因，**重试无益** |

- 建议把 `runs/run-*.json` 保留 N 天，便于回溯。
- 告警通道由部署环境定（cron 默认走本地邮件；容器环境建议接 webhook）。

### 2.6 幂等与重跑策略

- 幂等由 `(source, source_id)` 唯一键 + 合并逻辑保证；`batch_id` 确定性（同输入同 `batch_id`）。
- 因此**每日全量重跑安全**：不会新增重复作品，内容变更走更新（验收 D2 / D3）。
- **增量可选**：`source.py` 预留 `search_raw`（`/api/contents/search`）未启用；当前用全量更简单可靠。

### 2.7 无服务器时的过渡

当前无生产服务器，sync 定时化**执行暂缓**（与 W3 回填、部署 Issue #78 同因——「服务器先按没有算」）。过渡方案：

- 开发机本地 cron 暂代（仅联调 / 小批量，不扛全量生产）。
- 待服务器到位：容器化部署 + 镜像内 cron，或独立调度服务。
- **文档先行**：本方案已就绪，服务器到位即可落地，不阻塞设计。

### 2.8 与未来目标的关系

通用化（PR #117）若启动，第二站接入会在 `source.py` 加新适配器；届时定时化需覆盖多来源调度，本方案的「外部 cron + 幂等重跑」范式仍适用，无需推翻。

### 2.9 systemd.timer 落地示例（服务器到位即用）

cron 之外的推荐形态（更适合容器/云主机，且自带 `Persistent` 补跑与日志归集）：

```ini
# /etc/systemd/system/otaku-sync.service
[Unit]
Description=Otaku site sync
After=network-online.target

[Service]
Type=oneshot
User=otaku
WorkingDirectory=/opt/otaku/sync-python
# 令牌等敏感值只走环境变量文件，绝不入库（与 config.py 口径一致）
EnvironmentFile=/etc/otaku/sync.env
ExecStart=/usr/bin/python -m sync --source otaku_site
```

```ini
# /etc/systemd/system/otaku-sync.timer
[Unit]
Description=Daily Otaku site sync

[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=300      # 错峰，避免多实例同时打来源站
Persistent=true             # 关机错过的时间点，开机后补跑

[Install]
WantedBy=timers.target
```

启用：`systemctl enable --now otaku-sync.timer`，用 `systemctl list-timers otaku-sync*` 与 `journalctl -u otaku-sync` 观察。

> 同样**不要依赖退出码**：`Type=oneshot` 退出 0 时 systemd 视为成功，§2.5 的检查脚本仍是必需的（可作为第二个 `ExecStart` 串在后面）。

### 2.10 日志与报告留存

```cron
# 每天 05:00 清理 14 天前的运行报告（保留窗口便于回溯）
0 5 * * *  find /path/to/sync-python/runs -name 'run-*.json' -mtime +14 -delete
```

```
# /etc/logrotate.d/otaku-sync
/opt/otaku/sync-python/runs/cron.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate      # sync 以 >> 追加写入，copytruncate 避免重启进程
}
```

---

## 3. 审查记录（sync 主责自查）

- **`#105` schedule-sync / `#110` article-sync**：在现有 `OtakuSiteSource` 上**追加** `fetch_schedule_page` / `iter_schedule_raw` / `fetch_articles_page` / `fetch_article_detail_html` 等方法，**复用** `_get()`（含 `_throttle` 限流 + 退避重试）、`RETRYABLE_STATUS`、`failed_pages` 记账，**没有另起限流逻辑**；`__main__.py` 用 `--schedule` / `--articles` 与 `--type` 互斥、限定 `otaku_site`，且都带测试。**规范符合度良好，可作为 sync 主责放行合入**。
- **技术债（记录，当前不改）**：`OtakuSiteSource` 随来源站功能增多持续膨胀；若未来启动通用化（多站），应按 PR #117 的方向把各来源端点拆为独立适配器 / 声明式映射。当前「单站点多端点」架构不阻塞。

### 3.1 技术债：`OtakuSiteSource` 拆分蓝图（只设计，不改代码）

**现状实测**（`source.py` 541 行，单类 `OtakuSiteSource` 同时承担三类抓取）：

| 内容类型 | 方法 | 端点 |
|---|---|---|
| 作品 | `fetch_page` / `iter_raw` / `fetch_tags` / `search_raw` / `to_work_item` / `fetch` / `iter_work_items` / `fetch_many` | `/api/contents` 系 |
| 档期 | `fetch_schedule_page` / `iter_schedule_raw` / `iter_schedule_items` / `to_schedule_item` | `/api/schedule*` |
| 资讯 | `fetch_articles_page` / `fetch_article_detail_html` / `iter_articles_raw` / `fetch_article_detail` / `to_article_item` | `/api/article*` |

新增端点就往同一个类里加方法 —— 每加一类，这个类的职责就宽一圈（档期 #105、资讯 #110 已各加一批）。

**建议拆分方向**（通用化 PR #117 启动时执行）：

1. **抽基类 `BaseSource`**（共用，与内容类型无关）：`_throttle()` / `_get()`（含限流 + 指数退避重试）/ `RETRYABLE_STATUS` / `failed_pages` 记账 / `close()` 与上下文管理。
2. **按内容类型拆适配器**：`WorkSource` / `ScheduleSource` / `ArticleSource`，各自只管自己的端点解析与 `to_*_item` 映射。
3. **装配用注册表**：按 `(来源站, 内容类型)` 取适配器 —— 第二站接入时新增适配器，不动核心。
4. **映射声明式化**：字段映射（如 `to_work_item` / `to_schedule_item` / `to_article_item` 里的取值与兜底）尽量写成声明式配置，减少每站复制粘贴的映射代码。

**触发条件**（满足其一即可启动，当前均不成立）：

- 通用化（PR #117）启动 / 第二站接入需要新适配器；
- 单类方法数继续增长导致改动互相踩踏；
- 出现「同一来源站不同内容类型需要不同限流口径」的需求（当前共用 `_throttle`，暂不需要）。

**为什么现在不做**：拆分会触碰到已随 #134 落地的资讯 / 新番抓取代码，同事仍在同一批代码上推进 —— 现在重构易造成冲突与回归，收益却要到通用化才兑现。故**只留蓝图、不动代码**。
