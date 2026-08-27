# SourceSonar v0.2.8

SourceSonar 是一个面向新闻热点聚合、事件去重、专题追踪、舆情报告和智能问答的 Web 工具。它会从配置的新闻源中持续抓取内容，结合 Embedding、OpenAI-compatible 大模型、Crawl4AI/Playwright 正文补抓与结构化分析能力，对新闻进行聚类、摘要、分类、情感分析、关键词实体提取、专题整理和报告生成。

本项目适合用于搭建个人或团队内部的资讯观察台，例如：跟踪行业动态、观察公共事件进展、沉淀关键词报告、生成每日热点简报，或通过智能体按自然语言检索本地新闻库。

> ⚠️ AI 分析结果依赖于新闻源质量、模型能力、提示词和数据积累时间，建议作为辅助阅读与分析工具使用，重要结论仍应回看原文校验。

---

## 在线演示

| 场景 | 地址 |
|------|------|
| 全网综合新闻聚合 | [https://ainews.izam.cn](https://ainews.izam.cn) |
| 医药垂直行业新闻 | [https://mednews.izam.cn](https://mednews.izam.cn) |

---

## 功能概览

### 新闻采集与处理

- **多源配置**：通过 `data/news_sources.json` 配置多个新闻源，支持启用状态、来源权重、地区、分类、Cookie 等字段。
- **多种格式**：兼容 RSS/XML、JSON 接口、部分网页类热点源。
- **管理后台**：支持卡片式新增、编辑、删除、测试新闻源，实时查看健康状态（最近抓取结果、测试结果、失败次数和错误信息）。
- **正文补抓**：使用 Crawl4AI 和 Playwright 对动态页面补抓完整正文，支持动态等待、超时、重试与并发控制。
- **微博/Reddit 支持**：支持微博 Cookie、忽略域名、关注关键词过滤等采集辅助配置。

### 热点列表与语义搜索

- 首页按热度或时间展示新闻，支持分页、时间范围、分类、地区和来源筛选。
- 支持 `today / 24h / 3d / 7d / 30d / week / month / year / all` 及自定义日期范围。
- 关键词搜索优先使用向量召回，同时结合文本匹配提升检索可用性。
- 新闻详情弹窗展示摘要、来源、关键词、实体、情感、关联报道和相似新闻。
- 支持生成热点新闻图片和智能体新闻卡片图片，便于分享或归档。

### AI 分析与聚类

- 自动为热点新闻生成 AI 摘要，正文不足时使用来源摘要兜底。
- 自动补全分类、地区、情感倾向、关键词和实体。
- 使用 Embedding 相似度与 AI 校验对同一事件多来源报道进行去重聚合。
- 支持主力模型、备用模型和按功能配置的 AI 路由（摘要、情感、聚类、专题、报告、对话）。
- 支持在管理后台测试 Embedding、主力模型和备用模型连通性。

### 专题追踪

- 自动从近期高热新闻中发现候选事件簇，并通过 AI 批量审核生成专题。
- 支持专题列表、专题详情、时间轴、相关新闻和专题趋势仪表盘。
- 支持手动创建、改名、删除专题，并在后台扫描匹配相关新闻。
- 支持刷新专题综述、刷新单个时间轴节点摘要。
- 可通过配置调整专题召回池、候选簇数量、AI 审核批次数、相似度阈值、质量等级、最低新闻数和来源数。

### 报告与图表

- 支持综合报告和关键词报告，可按时间、分类、地区、来源和样本数量筛选。
- 提供来源分布、词云、情感分布、正负面关键词、热度趋势、相关新闻和词项共现网络等图表。
- 支持 AI 流式生成报告综述，分析趋势、事件和风险点。
- 自动定时生成每日、每周、每月报告缓存。
- 报告历史管理与缓存删除。

### 图谱分析

- 从新闻关键词/实体中聚合节点与边，生成总览图谱。
- 支持词项节点展开、词项详情和相关新闻查询。
- 支持按时间、分类、地区和来源筛选图谱数据。

### 事件溯源

- 支持输入事件描述，自动搜索相关新闻并按时间线梳理事件脉络。
- 结合 NewsAPI 外部数据源扩展事件覆盖范围。
- 生成事件时间轴、参与方与关键节点分析。

### 智能体

- 支持自然语言问答，基于本地新闻库检索相关报道并给出分析。
- 支持网页搜索、网页正文抓取、新闻图片生成等工具调用。
- 自动解析用户问题中的时间范围提示。

### 管理后台

- 可视化编辑 `config.yaml` 配置项。
- 管理新闻源（新增、编辑、删除、测试、健康状态查看）。
- 管理员登录/退出（Cookie 会话），支持密码校验与失败锁定。
- 手动触发全流程任务、全量情感重分析、阻断新闻清理。
- 查看实时日志与历史日志文件。
- 自定义 AI 提示词（摘要、聚类、专题、报告等场景）。
- 测试 AI 模型连通性（Embedding / Chat / 流式）。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3.10+ / FastAPI + Uvicorn |
| 数据库 | PostgreSQL（推荐） / SQLite（轻量模式） |
| ORM | SQLAlchemy 2.0（异步） |
| AI 模型 | OpenAI-compatible API（支持硅基流动、DeepSeek 等） |
| 向量检索 | AI Embedding + PostgreSQL pgvector / NumPy 本地计算 |
| 网页抓取 | aiohttp + Crawl4AI + Playwright + BeautifulSoup |
| 前端 | 原生 HTML/CSS/JS（无框架依赖），ECharts + Graphology 图谱 |
| 容器化 | Docker + Docker Compose |

---

## 项目结构

```
SourceSonar/
├── app/
│   ├── api/                 # FastAPI 路由
│   │   ├── api.py           # 路由聚合
│   │   ├── deps.py          # 依赖注入（settings、templates、鉴权）
│   │   └── endpoints/       # 各业务 API 端点
│   │       ├── graph.py     # 图谱
│   │       ├── news.py      # 新闻列表/搜索/详情
│   │       ├── prompts.py   # 提示词管理
│   │       ├── reports.py   # 报告生成
│   │       ├── system.py    # 系统管理
│   │       ├── topics.py    # 专题
│   │       └── trace.py     # 事件溯源
│   ├── core/                # 核心配置与基础设施
│   │   ├── config.py        # 配置模型与读取
│   │   ├── database.py      # 数据库连接管理
│   │   ├── exceptions.py    # 自定义异常
│   │   ├── logger.py        # 日志系统
│   │   └── prompts.py       # 提示词管理器
│   ├── models/              # SQLAlchemy 数据模型
│   │   ├── news.py          # 新闻
│   │   ├── topic.py         # 专题与时间轴
│   │   ├── report.py        # 报告缓存
│   │   └── clustering_history.py  # 聚类历史
│   ├── schemas/             # Pydantic 请求/响应模型
│   │   └── system.py
│   ├── services/            # 业务逻辑层
│   │   ├── admin_service.py       # 管理后台
│   │   ├── ai_service.py          # AI 调用封装
│   │   ├── cluster_service.py     # 新闻聚类
│   │   ├── concurrency_service.py # 并发控制
│   │   ├── crawler_service.py     # 新闻抓取
│   │   ├── graph_service.py       # 图谱分析
│   │   ├── news_title_service.py  # 标题优化
│   │   ├── newsapi_service.py     # NewsAPI 外部源
│   │   ├── pipeline_service.py    # 全流程编排
│   │   ├── report_service.py      # 报告生成
│   │   ├── similar_news_service.py# 相似新闻
│   │   ├── source_health_service.py# 源健康状态
│   │   ├── task_manager.py        # 后台任务管理
│   │   ├── topic_discovery_service.py # 专题发现
│   │   ├── topic_service.py       # 专题管理
│   │   └── trace_service.py       # 事件溯源
│   └── utils/               # 通用工具函数
│       ├── agent_web.py           # 智能体网络工具
│       ├── browser_process.py     # 浏览器进程管理
│       ├── config_io.py           # YAML 读写
│       ├── json_news_payload.py   # JSON 新闻解析
│       ├── network.py             # 网络工具
│       ├── news_content_filter.py # 无效内容过滤
│       ├── news_image.py          # 新闻图片生成
│       ├── news_query.py          # 新闻查询构建
│       ├── news_ranking.py        # 新闻排序
│       ├── news_search.py         # 语义搜索
│       ├── postgres_search_indexes.py # 数据库索引
│       ├── retry.py               # 异步重试
│       ├── schema_migration.py    # 数据库迁移
│       ├── summary_material.py    # 摘要素材
│       ├── title_tools.py         # 标题工具
│       ├── tools.py               # 通用工具
│       ├── topic_preprocess.py    # 专题预处理
│       └── ttl_cache.py           # TTL 内存缓存
├── data/                   # 数据文件（新闻源配置等）
├── docker/                 # Docker 配置
├── docs/                   # 文档与截图
├── logs/                   # 日志文件
├── static/                 # 前端静态资源
│   ├── css/                # 样式
│   │   ├── base.css
│   │   └── pages/          # 各页面样式
│   └── js/
│       ├── base.js
│       └── pages/          # 各页面脚本
├── templates/              # Jinja2 模板
│   ├── base.html           # 基础布局
│   ├── index.html          # 首页
│   ├── topics.html         # 专题列表
│   ├── topic_detail.html   # 专题详情
│   ├── graph.html          # 图谱
│   ├── report.html         # 报告
│   ├── trace.html          # 事件溯源
│   └── admin.html          # 管理后台
├── config.yaml             # 主配置文件
├── docker-compose.yml      # Docker Compose 编排
├── Dockerfile              # Docker 镜像构建
├── main.py                 # 应用入口
├── requirements.txt        # Python 依赖
└── README.md
```

---

## 快速开始

### 环境要求

- Python 3.10+
- PostgreSQL 16+（推荐，支持 pgvector 向量检索）或 SQLite
- 一个 OpenAI-compatible API Key（如硅基流动、DeepSeek 等）

### 本地运行

```bash
# 1. 克隆项目
git clone https://github.com/aicezam/trendsonar.git
cd trendsonar

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 config.yaml（至少配置 AI API Key 和数据库连接）

# 4. 启动
python main.py
```

访问 `http://localhost:8193` 即可使用。

### Docker 部署

```bash
docker-compose up -d
```

---

## 配置说明

核心配置文件为 `config.yaml`，主要配置项：

| 配置项 | 说明 |
|--------|------|
| `APP_NAME` | 应用名称（默认 SourceSonar） |
| `PORT` | 服务端口（默认 8193） |
| `DATABASE_URL` | 数据库连接串（留空使用 SQLite） |
| `MAIN_AI_*` | 主力 AI 模型配置（API Key、Base URL、Model） |
| `BACKUP_AI_*` | 备用 AI 模型配置 |
| `EMBEDDING_MODEL` | Embedding 模型名称 |
| `CRAWLER_*` | 爬虫参数（并发数、超时、重试等） |
| `AUTO_*` | 自动任务参数（分析 TopN、摘要数等） |
| `TOPIC_*` | 专题参数（召回池、相似度阈值、审核批次等） |
| `FOLLOW_KEYWORDS` | 关注关键词（逗号分隔） |
| `LOG_*` | 日志参数（级别、保留天数） |

完整配置项请参考 `config.yaml` 中的注释。

---

## 定时任务调度

服务启动后自动运行调度器，按以下节奏执行：

1. 从新闻源抓取最新内容并入库
2. 对入库新闻去重聚合
3. 为 TopN 新闻生成 AI 摘要
4. 向量补全分类、地区、情感、关键词和实体
5. 为热点新闻生成 AI 摘要
6. 生成或刷新日报缓存
7. 按专题间隔刷新专题
8. 按配置清理低热历史数据

此外，调度器会在特定时间生成每日、每周和每月最终报告缓存。全流程任务完成后，服务会按当前逻辑尝试重启以释放内存。

---

## 使用建议

- 初始运行时数据量少，聚类、专题和报告效果会比较有限，建议运行一段时间后再评估质量。
- 新闻源质量直接影响结果。若某来源经常失败，可在后台查看健康状态并单独测试。
- 动态页面正文补抓会占用更多内存，低配机器建议将 `CRAWLER_CONCURRENCY` 控制在 `1-2`。
- 聚类阈值过低可能误合并，过高可能漏合并；专题质量等级越高，生成数量越少但更稳。
- 关键词报告和智能体问答只基于已入库新闻与可调用工具，不代表完整互联网信息。
- 涉及法律、医疗、投资、公共安全等高风险判断时，请以原文和权威来源为准。

---

## 推荐新闻源

如果需要扩展 RSS 或热点来源，可以参考：

- [Hot News](https://github.com/orz-ai/hot_news)：每日热点新闻聚合
- [NewsNow](https://github.com/ourongxing/newsnow)：多平台热榜聚合，提供部分 RSS/API 接口
- [RSSHub](https://github.com/DIYgod/RSSHub)：为许多网站生成 RSS
- [AnyFeeder](https://plink.anyfeeder.com/)：RSS 源聚合服务

---

## 界面预览

### 热点新闻列表

<img src="docs/images/index.png" alt="热点新闻列表" width="100%">

### 专题追踪

<img src="docs/images/topic.png" alt="专题追踪" width="100%">

### 深度报告

<img src="docs/images/baobiao1.png" alt="报告预览1" width="100%">
<img src="docs/images/baobiao2.png" alt="报告预览2" width="100%">

---

## 更新日志

- **v0.2.8**：补强新闻智能体能力，新增网页搜索、网页正文抓取、新闻图片生成和管理端自定义 HTTP 工具；优化新闻源卡片管理、健康状态展示、AI 连通性测试、日志查看、专题候选簇发现与 AI 批量审核参数；强化报告词项分析、专题趋势和新闻详情体验。
- **v0.2.7**：优化新闻详情、相似新闻召回、报告交互和管理端配置体验。
- **v0.2.6**：重构 UI 视觉样式，优化性能、提示词和专题生成逻辑。
- **v0.2.5**：优化搜索与向量召回，专题模块新增关键词趋势分析，报告页新增关键词分析，优化 UI 交互和新闻详情弹窗。
- **v0.2.1**：专题模块新增报告能力，包括词云、来源分布、情感分析、相关新闻和关键词趋势等。
- **v0.2.0**：优化 token 消耗、日志显示和专题重复生成问题。
- **v0.1.7**：优化 token 消耗、聚合流程和首页筛选交互。
- **v0.1.6**：优化关键词深度分析交互，支持在管理后台自定义提示词。
- **v0.1.5**：优化专题生成逻辑，支持手动新增、编辑、删除专题。
- **v0.1.4**：新增专题质量审核等级配置，优化专题生成逻辑。
- **v0.1.3**：优化内存占用和专题追踪复核逻辑。
- **v0.1.2**：优化配置异常时的定时任务流程。
- **v0.1.1**：修复部分鉴权问题。
- **v0.1.0**：初始版本，发布至 Docker Hub。

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=aicezam/trendsonar&type=date&legend=top-left)](https://www.star-history.com/#aicezam/trendsonar&type=date&legend=top-left)

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。