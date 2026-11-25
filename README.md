# 维修日报核查服务（工时单 × 领料单）

维修日报按时段把**工时单**和**领料单**关联起来。本服务按工单核查哪些单据会
**漏计**（匹配数为 0）或**重计**（匹配数超过 1）；重计单据附两个不同的对方单号。

- FastAPI + SQLite（标准库 `sqlite3`，显式事务）
- `docker compose up -d --build` 一键启动
- 配对仅限**同一工单**
- 领料时刻 `t` 落在工时区间 `[start_time, end_time)`：**含起点、不含终点**
- 换交一类单据时，另一类照旧；换交与**两类核查结果在同一事务**内生效，失败整体回滚
- 同一工单两类单据**并发换交且都成功**时，最终核查结果反映两次更正

---

## 一、快速开始

### Docker Compose（推荐）

```bash
docker compose up -d --build
# 服务地址 http://localhost:8000  （可用 HOST_PORT=8081 docker compose up -d 改宿主端口）
curl http://localhost:8000/healthz
```

SQLite 数据库位于命名卷 `repair-check-data` 内的 `/data/repair_check.db`。

### 本地运行

```bash
python -m pip install -r requirements.txt
python run.py                 # 或 uvicorn app.main:app --port 8000
```

接口文档（Swagger）：<http://localhost:8000/docs> ，OpenAPI：`/openapi.json`。

### 自测

```bash
python -m pip install -r requirements-dev.txt
python -m pytest selftest -q
```

`selftest/` 覆盖区间边界、漏计/重计、两个不同对方单号、整份提交、换交保留另一类、
失败回滚、工单隔离，以及真实 uvicorn HTTP 并发下的两类同时换交。

---

## 二、接口契约（规范路径）

### 1. 按工单整份提交两类单据

`POST /api/work-orders/{work_order_no}/documents`
（等价：`PUT /api/work-orders/{work_order_no}`、`POST /api/submit`（工单号放 body））

```bash
curl -s -X POST http://localhost:8000/api/work-orders/WO-001/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "work_hours": [
      {"doc_no": "GH-001", "start_time": "2026-11-25 08:00:00", "end_time": "2026-11-25 17:00:00"},
      {"doc_no": "GH-002", "start_time": "2026-11-25 13:30:00", "end_time": "2026-11-25 15:00:00"}
    ],
    "materials": [
      {"doc_no": "LL-001", "pick_time": "2026-11-25 08:00:00"},
      {"doc_no": "LL-002", "pick_time": "2026-11-25 12:30:00"},
      {"doc_no": "LL-003", "pick_time": "2026-11-25 14:00:00"},
      {"doc_no": "LL-004", "pick_time": "2026-11-25 17:00:00"}
    ]
  }'
```

整份提交会用请求中的两个列表整体替换该工单的两类单据。

### 2. 换交一类单据（另一类照旧）

```bash
# 只换交该工单的全部工时单，领料单保持库内原样
curl -s -X PUT http://localhost:8000/api/work-orders/WO-001/work-hours \
  -H 'Content-Type: application/json' \
  -d '{"work_hours":[{"doc_no":"GH-010","start_time":"08:30","end_time":"17:30"}]}'

# 只换交领料单，工时单照旧（POST 与 /replace、/exchange、/reissue 等写法也支持）
curl -s -X PUT http://localhost:8000/api/work-orders/WO-001/materials \
  -H 'Content-Type: application/json' \
  -d '{"materials":[{"doc_no":"LL-010","pick_time":"10:00"}]}'
```

请求体也支持直接传裸数组：

```bash
curl -s -X PUT http://localhost:8000/api/work-orders/WO-001/materials \
  -H 'Content-Type: application/json' \
  -d '[{"doc_no":"LL-011","pick_time":"11:00"}]'
```

### 3. 查询核查结果

`GET /api/work-orders/{work_order_no}/check`
（等价：`GET /api/check?work_order_no=WO-001`、`GET /api/check/WO-001`、
`GET /api/work-orders/WO-001/audit|verify|report` 等）

返回的规范字段：

```jsonc
{
  "code": 200,
  "success": true,
  "work_order_no": "WO-001",
  "version": 3,

  // 仅包含“匹配 0 次（漏计）”或“匹配 >1 次（重计）”的单据
  "work_hours": [
    {
      "doc_no": "GH-001",
      "match_count": 3,
      "issue_type": "duplicate",          // missing=漏计, duplicate=重计
      "counterpart_nos": ["LL-001", "LL-002", "LL-003"],
      "counterpart_no_1": "LL-001",       // 重计时两个不同的对方单号
      "counterpart_no_2": "LL-002"
    }
  ],
  "materials": [
    {"doc_no": "LL-003", "match_count": 2, "issue_type": "duplicate",
     "counterpart_nos": ["GH-001", "GH-002"],
     "counterpart_no_1": "GH-001", "counterpart_no_2": "GH-002"},
    {"doc_no": "LL-004", "match_count": 0, "issue_type": "missing",
     "counterpart_nos": []}
  ],

  // 每单全量计数（含恰好匹配一次的正常单）与汇总
  "work_hours_all": [ /* ... */ ],
  "materials_all":  [ /* ... */ ],
  "work_hour_counts": {"GH-001": 3, "GH-002": 1},
  "material_counts":  {"LL-001": 1, "LL-002": 1, "LL-003": 2, "LL-004": 0},
  "summary": { "missing_total": 1, "duplicate_total": 2, "anomaly_total": 3 },

  // 同一份结果也放在 data / result 中
  "data": { /* 与上面相同的核查结果 */ }
}
```

核查只处理路径指定的本工单，响应只给“每单聚合次数 + 对方单号”，
不输出逐一枚举的匹配对（没有 `pairs` 列表）。

### 4. 查询已存单据 / 删除工单

- `GET /api/work-orders/{work_order_no}/documents`：返回库内两类原始单据
- `DELETE /api/work-orders/{work_order_no}`：清空该工单

---

## 三、字段与写法兼容性

题面未固定字段命名，服务对常见写法做了宽容解析（snake_case / camelCase / 中文键 / 拼音）：

| 含义 | 兼容键（举例） |
| --- | --- |
| 工单号 | `work_order_no`、`workOrderNo`、`order_no`、`wo_no`、`gdbh`、`工单号` |
| 单据号 | `doc_no`、`bill_no`、`sheet_no`、`ticket_no`、`code`、`id`、`单号` |
| 工时开始 | `start_time`、`startTime`、`start`、`begin_time`、`kssj`、`开始时间` |
| 工时结束 | `end_time`、`endTime`、`end`、`finish_time`、`jssj`、`结束时间` |
| 领料时刻 | `pick_time`、`pickTime`、`requisition_time`、`time`、`llsj`、`领料时间`、`领料时刻` |

时间支持 `2026-11-25 08:00:00`、ISO8601（含时区/`Z`）、`08:00` 仅时刻、
`2026年11月25日 8时00分`、Unix 秒/毫秒。

除规范路径外还挂载了大量别名，例如 `/api/workorders/{wo}/check`、
`/api/orders/{wo}/verify`、`/api/audit/{wo}`、`/check/{wo}`、
`PUT /api/materials/{wo}`、`POST /api/replace`（body 带 `type=work_hour|material`）
等；响应同时提供 `data` 外壳与 `code/success/message`。

---

## 四、并发与失败一致性

每次换交在同一把“工单级互斥锁”内执行一个 `BEGIN IMMEDIATE` SQLite 事务：

1. upsert 工单；
2. `DELETE + INSERT` 替换被换交的那一类（另一类不触碰）；
3. 读取该工单两类单据，重算两侧匹配次数；
4. upsert `check_reports`（结果 JSON 带版本号）；
5. 提交。

任一步骤抛错都会 `ROLLBACK`，因此**原单据与上一版核查结果保持不变**
（结果版本号也不会前进）。SQLite 启用 WAL 与 `busy_timeout=30s`；
单进程内用工单级锁串行化，跨进程由 `BEGIN IMMEDIATE` 写锁兜底。
同一工单的工时单、领料单两个并发换交请求会先后进入临界区，
后提交者基于“两次更正都已落库”的状态重算，故最终核查结果同时反映两次更正，
不会发生读-改-写丢失。

## 五、附：等价核查 SQL（数仓口径）

半开区间 `[start_text, end_text)` 与同工单限制可直接写成条件聚合：

```sql
-- 每张工时单匹配到的领料单数（不同领料单号）
SELECT h.work_order_no, h.doc_no,
       COUNT(DISTINCT m.doc_no) AS match_count,
       GROUP_CONCAT(DISTINCT m.doc_no) AS counterpart_nos
FROM work_hours h
LEFT JOIN materials m
  ON m.work_order_no = h.work_order_no
 AND m.pick_text >= h.start_text   -- 含起点
 AND m.pick_text <  h.end_text     -- 不含终点
WHERE h.work_order_no = :work_order_no
GROUP BY h.id
HAVING match_count = 0 OR match_count > 1;

-- 每张领料单匹配到的工时单数，方向相反
SELECT m.work_order_no, m.doc_no,
       COUNT(DISTINCT h.doc_no) AS match_count
FROM materials m
LEFT JOIN work_hours h
  ON h.work_order_no = m.work_order_no
 AND m.pick_text >= h.start_text
 AND m.pick_text <  h.end_text
WHERE m.work_order_no = :work_order_no
GROUP BY m.id
HAVING match_count = 0 OR match_count > 1;
```

（服务内用等价的 Python 聚合实现，时间统一解析后比较，兼容仅时刻 / 带时区等写法。）

## 六、目录结构

```
app/
├── main.py       # FastAPI 应用、中间件、异常处理、OpenAPI
├── routes.py     # 规范接口 + 路径/方法别名
├── parsing.py    # 宽容载荷与字段别名解析
├── matching.py   # 时间解析、[start,end) 配对与计数（纯逻辑）
├── shapes.py     # 核查结果结构与响应别名
├── service.py    # 工单锁、原子换交+重算+结果持久化
├── db.py         # SQLite 连接、建表、BEGIN IMMEDIATE 事务
└── config.py     # DB_PATH 等配置
selftest/         # pytest 自测（含真实 HTTP 并发用例）
Dockerfile
docker-compose.yml
```
