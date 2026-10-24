# sync-python · 数据同步作业

从现有 Otaku 网站读取作品数据，清洗校验后经内部导入 API 提交给 Java 后端。

**当前状态**：作业与清洗逻辑已实现，可脱离 Java 独立运行（dry-run），也已按 `docs/openapi.yaml` 的最终形状对接真实后端跑通——来源站真实数据 108 条提交后 `inserted=108`、`rejected=0`，入库 `summary` 中 `\r\n` 已统一为 `\n`。

## 目录

```text
src/sync/
  config.py     配置，敏感值只从环境变量读取
  models.py     与 data-contract.md 对应的数据结构
  cleaning.py   清洗与校验（换行规范化、空值处理、评分校验）
  source.py     来源适配：现有网站的 JSON API
  bangumi.py    来源适配：Bangumi 官方数据集档案（本地 jsonlines 快照）
  importer.py   内部导入接口客户端：重试、幂等、失败记录
  pipeline.py   编排：拉取 -> 校验 -> 分批 -> 提交 -> 记录
  __main__.py   命令行入口
tests/          单元测试与本地桩服务
```

## 安装

```bash
pip install -r requirements.txt
```

## 运行测试

```bash
python -m pytest -q
```

已验证：57 项通过。测试使用本地桩服务与临时 zip，**不访问外网，不依赖 Java**，
覆盖验收项 D5（空标题拒绝）、D7（未授权拒绝）、D8（换行规范化）、D9（长文本不截断）、D10（重名各自成作品），
以及 Bangumi 档案的类型映射、`score=0` 语义、标签三态、标题回落。

## 脱离 Java 试跑（已验证）

只落盘将要发送的载荷与运行报告，不联系 Java：

```bash
PYTHONPATH=src python -m sync --type anime --limit 100 --dry-run --out runs
```

输出示例：

```text
[dry-run] 抓取 100，通过校验 100，拒绝 0，批次 1（已送达 0）
```

产物在 `runs/`：`batch-<batch_id>.json` 为将要提交给 Java 的载荷，`run-<时间>.json` 为运行报告。

已实测确认：回车换行已统一为 `\n`；封面为绝对地址（来源相对路径 + 站点域名）；
标签按三态语义映射（有值取列表、空列表为 `[]`、字段缺失才为 `null`）；年份多数为空；评分尺度 `0-10`；
同一批输入重复运行得到**同一个** `batch_id`（幂等键），这是验收 D2「重跑不新增作品」的前提。

## 从 Bangumi 档案入库（本地快照）

`dump.zip` 是 Bangumi 官方数据集档案（9 个 jsonlines，解压约 1.86 GB），
本项目只用其中的 `subject.jsonlines`（695909 条作品）。档案**不进仓库**，
路径由 `--archive` 或 `OTAKU_ARCHIVE_PATH` 指定。

先看档案里各类有多少条、会被排除多少：

```bash
PYTHONPATH=src python -m sync --source bangumi --archive <dump.zip> --stats
```

再按契约整理后落盘（不联系 Java）：

```bash
PYTHONPATH=src python -m sync --source bangumi --archive <dump.zip> \
  --type anime --limit 100 --dry-run --out runs
```

### 抽样方式

默认 `--sample head`：按档案顺序（= id 升序）取前 N 条，会偏向早期录入的知名作品，
其分布（中文名比例、评分比例）与全量并不相同。`--sample uniform` 用**蓄水池随机抽样**：
扫描全部匹配条目后等概率保留 N 条，使样本分布接近全量真实比例。

已验证（N=10000，固定随机种子）：均匀样本的中文名比例 **21.78%** vs 全量 **21.09%**；
四类占比误差 **< 0.3%**（comic 57.5% / game 20.8% / novel 16.4% / anime 5.4%，全量 57.6/21.1/15.7/5.6）。

固定种子保证可复现：同一组参数两次运行得到同一批 source_id、同一个 batch_id，重跑不重复入库。
代价是必须扫完整份 `subject.jsonlines`（约 69 万行，单次约 25 秒），比 head 慢，但只在生成样本时付一次。

### 类型映射（用全量 695909 条实测确定）

| Bangumi | 判据 | 档案条数 | 映射 |
| --- | --- | --- | --- |
| `type=2` | 动画 | 31174 | `anime` |
| `type=4` | 游戏 | 117295 | `game` |
| `type=1` + `platform=1001` | 漫画（含 29416 条泛用 `Book` 模板的单行本） | 320631 | `comic` |
| `type=1` + `platform=1002` | 小说（含 2079 条 `Book` 模板） | 87116 | `novel` |
| `type=1` + 其他 platform | 画集 / 写真 / 公式书 / 杂项 | 12317 | 排除 |
| `type=3` | 音乐 | 100751 | 排除（契约无此类型） |
| `type=6` | 三次元 | 26625 | 排除（契约无此类型） |

判据用 `platform` 而不是 `infobox` 模板名：`Book` 模板横跨漫画与小说，
只看模板会漏掉 3 万多条漫画单行本。

### 字段映射与实测缺口

| 契约字段 | 来源 | 实测 |
| --- | --- | --- |
| `source_id` | `id` | 转字符串 |
| `work_type` | `type` + `platform` | 见上表 |
| `title` | `name_cn`，为空回落 `name` | **78.91% 无中文名**，回落后多为日文原名 |
| `summary` | `summary` | 17.76% 为空；`\r\n` 已统一为 `\n` |
| `cover_url` | —— | **档案不含图片字段，恒为 `null`** |
| `tags` | `tags[].name` | 50.13% 为 `[]`，原样保留为"明确无标签" |
| `main_rating` | `score` | **`score == 0` 表示无人评分**，占 58.83%，映射为 `null` |

`release_year` 能从 `date` 取到（92.3% 有值），但第一阶段接口不出口，仅本地记录。

### 来源站原页（`source_url`）

用于客户端「在来源站查看」的**回流入口**，两个来源都能可靠确定，因此直接提交：

| 来源 | 原页形态 | 依据 |
| --- | --- | --- |
| `otaku_site` | `{base_url}/contents/{content_id}` | 2026-10-21 实测：站点首页渲染的卡片链接即此形态，`/contents/476` 返回 200 且标题为作品名 |
| `bangumi` | `https://bgm.tv/subject/{subject_id}` | 档案自身可推导 |

注意 `otaku_site` 用的是**人读页** `/contents/{id}`，不是接口地址 `/api/contents/{id}`——
后者返回 JSON，给客户端当回流入口等于给个无效链接。
原页无法可靠确定时提交 `null`，不猜测生成（契约第 2.1 节）。

### 与站点来源的差异（换源的代价）

| 项 | 站点 API | Bangumi 档案 |
| --- | --- | --- |
| 封面 | 100% 有值 | **完全没有** |
| 中文标题 | 全部是中文 | 21.09% 有，其余是原文 |
| 简介缺失 | 较少 | 17.76% 为空 |
| 评分 | 有值比例高 | 58.83% 无评分 |
| 系列 / 别名 / 话数 | 接口未出口 | 档案里有（`infobox` / `subject-relations` / `episode.jsonlines`），第一阶段不解析 |
| 限流 | 必须遵守 1–2 req/s | 无（本地文件） |

`source` 取值定为 `bangumi`，与站点来源的 `otaku_site` 并存：两者 id 体系不同
（站点 `content_id` vs Bangumi `subject_id`），不能共用同一个 `source`，
否则 `(source, source_id)` 唯一键会把两套 id 混在一起。

## 提交给 Java（已验证）

本地已端到端跑通：**Bangumi 档案 → 清洗 → 分批 → 内部导入接口 → 入库**。
实测：10000 条 `source=bangumi` 全部 `inserted=10000`、`rejected=0`、20 批全部送达。

> **关于 `source_url`**：条目现提交 8 个字段（原 7 个 + `source_url`）。
> 后端 `application.yml` 是 `fail-on-unknown-properties: false`，在 Java 真正接收该字段之前，
> 多传的这个字段**会被忽略、不会 400**，本作业行为不变；Java 侧接上后无需再改 Python。

前置：Java 后端先启动并暴露 `/internal/import/batches`（见 `backend-java/README.md`），
且 `source=bangumi` 的取值已在后端放开（见 `docs/openapi.yaml` 的 `ImportBatchRequest.source`）。

```bash
export OTAKU_INTERNAL_TOKEN=<内部导入令牌>
export OTAKU_JAVA_BASE_URL=http://localhost:8080   # 后端地址，按实际情况改
PYTHONPATH=src python -m sync --source bangumi --archive <dump.zip> \
  --type anime --type comic --type game --type novel \
  --sample uniform --limit 10000 --batch-size 500
```

> 注意：本机若设置了 `HTTP_PROXY` 等全局代理，Python 的 httpx 会把对 `localhost` 的请求也走代理，
> 导致连不上本机后端。推送本机后端时设 `NO_PROXY=localhost,127.0.0.1`（或临时清空代理变量）。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OTAKU_SOURCE` | `otaku_site` | 来源：`otaku_site`（在线站点）或 `bangumi`（本地档案） |
| `OTAKU_SAMPLE_METHOD` | `head` | 抽样方式：`head`（按档案顺序取前 N 条）或 `uniform`（蓄水池随机抽样） |
| `OTAKU_ARCHIVE_PATH` | 无 | Bangumi 档案路径，`OTAKU_SOURCE=bangumi` 时必填 |
| `OTAKU_SOURCE_BASE_URL` | `http://154.8.194.198:8000` | 来源站点 |
| `OTAKU_JAVA_BASE_URL` | `http://localhost:8080` | Java 后端 |
| `OTAKU_INTERNAL_TOKEN` | 无 | 内部导入令牌，**不得写入仓库** |
| `OTAKU_WORK_TYPES` | `anime` | 逗号分隔的作品类型 |
| `OTAKU_BATCH_SIZE` | `100` | 每批条数，上限 500 |
| `OTAKU_SOURCE_PAGE_SIZE` | `200` | 来源分页大小，**来源方要求不超过 200** |
| `OTAKU_REQUEST_INTERVAL_SECONDS` | `0.6` | 来源请求间隔，对应来源方要求的 1–2 req/s |
| `OTAKU_MAX_PAGES` | `1000` | 翻页上限，安全阀 |
| `OTAKU_TIMEOUT_SECONDS` | `30` | 请求超时 |
| `OTAKU_MAX_ATTEMPTS` | `3` | 最大尝试次数 |
| `OTAKU_BACKOFF_SECONDS` | `2` | 重试间隔 |
| `OTAKU_OUTPUT_DIR` | `runs` | 产物目录 |

## 定时化与运行检查

定期运行 / 生产运维见 [`docs/operations.md`](docs/operations.md)：灌库指引、外部 cron 与 systemd.timer 配置、日志轮转与报告留存。

⚠️ **不要只靠退出码判断成败**：`python -m sync` 在正常路径上一律返回 `0`，**即使 `failed_pages` 非空（本次根本没拿到全量），退出码也是 0**。跑完请用检查脚本判定：

```bash
python scripts/check_run_report.py          # 检查 runs/ 下最新的运行报告
python scripts/check_run_report.py --help   # 查看参数与阈值
```

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常，无需处置 |
| `1` | 发现需要处置的问题（未全量 / 服务端故障 / 资讯详情缺失 / 被拒占比过高） |
| `2` | 没找到报告，或报告无法解析 |

判定与退出码均有测试覆盖：`pytest tests/test_check_run_report.py`。

### 跑同步前先自检（`--health`）

一轮全量要几十分钟（串行限流 ≈1.67 req/s）。后端没起、或令牌没设，往往要等抓取跑完、到提交阶段才发现整轮白跑。`--health` 花几秒先确认：

```bash
python -m sync --health
```

| 检查项 | 不通过意味着 |
| --- | --- |
| 来源站 | 抓不到数据（地址写错 / 站点没起） |
| Java 后端 | 抓到了也提交不上去 |
| 内部令牌 | 只能 `--dry-run`，正式提交会整批 401 |

任一项不通过即退出码 `1`。连通性判定：连不上（超时 / DNS / 端口）与「连上了但状态码非 2xx/3xx」分两种方式提示——前者多半是地址写错或服务没起，后者多半是端点变了。

> 若本机设了全局 `HTTP_PROXY`，连 `localhost` 也会被代理拦掉、表现为 **502**，看起来像后端挂了其实是代理。设 `NO_PROXY=localhost,127.0.0.1` 再试（同源问题见「提交给 Java」一节）。

## 约定

- Python **不直接写业务数据库**，只通过内部导入 API 提交。
- 只有网络错误、超时、5xx、429 才重试；4xx 与认证失败不重试，直接记为未送达。
- 缺失字段传 `null`，不传空字符串、不填 0 分、不造占位值。
- 请求失败不静默吞掉，逐条记进运行报告，便于定位与重提。
- 提交的字段集以 [`../docs/stage1-minimal-contract.md`](../docs/stage1-minimal-contract.md) 为准（第一阶段）；来源站能力、限流与待确认问题见 [`../docs/旧站能力复用清单`](../docs/旧站能力复用清单)。
