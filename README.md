# A股市值全景图

全市场 A 股的 **SpaceSniffer 风格市值占比图**：方块面积 = 市值占比，颜色深浅 = 当日涨跌幅（红涨绿跌），按行业分区。

- 覆盖 **5574 只** A 股（沪深京），**128 个**行业分区
- 单页纯静态，数据内联或按日期 `fetch` 加载，无任何第三方依赖
- 深色科技风，Canvas 手写渲染

## 页面功能

| 操作 | 说明 |
|---|---|
| 悬停方块 | 显示名称/代码/最新价/涨跌幅/市值/全市场占比 |
| 点击方块 | 进入该行业分区；`Esc` 或左上角返回 |
| 顶部日期选择器 | 切换历史快照（`‹ ›` 快速翻页，URL 同步为 `?d=YYYY-MM-DD`） |
| 侧栏行业榜 | 按总市值排序，显示只数、市值、平均涨跌；点击钻取 |
| 搜索框 | 输入代码或名称直达（`/` 聚焦，`Esc` 清空） |
| 总市值 / 流通市值 | 切换面积口径 |
| 按市值 / 按涨跌 | 切换方块排序 |

## 目录结构

```
index.html            # 构建产物（可直接打开）
template.html         # 页面模板，数据在 /*__DATA__*/ 处注入
build.py              # 注入数据 + 生成 data/YYYY-MM-DD.json 与 manifest
fetch_ashare.py       # 抓取全市场行情 → data.json
daily.sh              # 每日流水线：抓取 → 构建 → 提交推送
data/
  manifest.json       # 日期清单（页面读它来渲染日期下拉）
  YYYY-MM-DD.json     # 每个交易日的快照（永久保留，即历史数据）
.github/workflows/
  daily.yml           # GitHub Actions：定时抓取 + 自动部署 Pages
register_task.ps1     # Windows 任务计划程序注册脚本（本地替代方案）
```

## 自动化更新

### 方案 A：GitHub Actions（推荐，**云端运行，本机不用开机**）

`.github/workflows/daily.yml`：

- **触发**：工作日 UTC 17:20（北京 01:20 次日）+ 每日 UTC 01:50 补跑；也可手动 `workflow_dispatch`
- **流程**：抓取 → 用上证指数接口确认真实交易日 → 写入 `data/<交易日>.json` → 提交推送 → 自动部署 Pages
- **交易日语义**：非交易日接口返回上一个交易日的数据，因此用指数的 `f124` 字段作为权威交易日；该日期已有快照则直接跳过（幂等，不会重复提交）
- **防错**：抓取行数低于接口报告总数的 97% 视为不完整，直接放弃并保留上一份数据，绝不写出残缺快照

### 方案 B：Windows 本机定时任务

```powershell
powershell -ExecutionPolicy Bypass -File register_task.ps1
```

注册为工作日 17:30 执行 `daily.sh`。同样内置交易日判断与完整性校验。

> 方案 B 需要电脑处于开机且未休眠状态；方案 A 无此限制。

## 交易日判断的两层保护

1. `fetch_ashare.py` 先按星期过滤，周末直接 `exit 3` 跳过
2. `daily.sh` 再用上证指数接口（`f124`）确认真实交易日，覆盖法定节假日
3. 目标日期快照已存在则跳过 —— 同一天无论跑多少次都只提交一次

## 手动命令

```bash
# 抓取并更新页面
python fetch_ashare.py
python build.py

# 完整流水线（含 git 提交推送）
./daily.sh

# 强制刷新（忽略"已存在"检查）
FORCE=1 ./daily.sh

# 只构建不内联数据（CI 用，减小提交体积）
EMBED=0 SNAPSHOT_DATE=2026-10-07 python build.py
```

## 数据来源

东方财富公开行情接口（`push2.eastmoney.com`），字段：最新价、涨跌幅、总市值、流通市值、所属行业。

数据为**每日收盘后快照**，仅供研究与可视化参考，**不构成任何投资建议**。

## 部署

仓库 Settings → Pages → Source 选 **GitHub Actions**。首次推送后自动发布，后续每次数据更新推送都会重新部署。
