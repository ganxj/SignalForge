# Reddit Scrapper

一个用于从 Reddit 抓取帖子/评论，并用 AI 筛选产品机会、痛点和商业洞察的 Python 项目。项目会把抓取到的数据写入本地 SQLite，再通过 OpenAI/Anthropic 或 OpenAI 兼容接口进行两阶段分析，最后可以用 Streamlit 图形界面查看结果。

## 主要功能

- 抓取指定 subreddit 的帖子和评论。
- 支持多种 Reddit 抓取方式：
  - `reddit/scraper.py`：使用 PRAW 和 Reddit API 凭据抓取。
  - `reddit/scraper_public.py`：使用 Reddit 公开 JSON 接口抓取，不需要 Reddit API 凭据，带缓存、限速和退避。
  - `reddit/scraper_web.py`：使用 HTTP 请求抓取 Reddit 页面 HTML。
  - `reddit/scraper_browser.py`：使用独立 Playwright Chromium 浏览器实例抓取。
- 使用 AI 做两阶段处理：
  - 过滤阶段：给帖子/评论打分，包括相关性、痛点清晰度、情绪强度、可实现性、技术深度等。
  - 深度洞察阶段：对高分内容提取痛点、产品机会、目标人群、商业模式、技术壁垒等信息。
- 支持 OpenAI Batch API、Anthropic Batch API，以及本地 OpenAI 兼容 `/v1/chat/completions` 接口。
- 使用 SQLite 保存抓取记录、评分、洞察和处理历史。
- 使用 Streamlit 提供结果浏览、筛选和排序界面。

## 后续规划

后续计划把系统从 Reddit 单一数据源扩展成多平台社区洞察工具。计划接入的数据源包括：

- Facebook Group
- Discord 社区
- YouTube / TikTok 评论区
- App Store / Google Play 评论
- 知乎
- 小红书

这些平台的数据会尽量复用当前流程：抓取原始内容、写入统一数据库、按状态去重处理、AI 过滤和深度洞察、页面展示和人工分类。不同平台会根据访问方式和风控要求选择 API、公开页面抓取或独立浏览器实例。

## 项目结构

```text
.
├── main.py                  # 完整批处理管线入口：按 reddit.mode 抓取 + Batch AI 分析
├── run_local.py             # 本地/兼容 OpenAI Chat Completions 管线入口
├── run_crawl_public.py      # 只使用 Reddit 公开 JSON 抓取，不做 AI 分析
├── analyze_local.py         # 分析 SQLite 中尚未处理的数据
├── config/config.yaml       # 抓取、模型、评分、数据库等配置
├── reddit/                  # Reddit 抓取逻辑
├── gpt/                     # Prompt、Batch API、本地 Chat Completions 分析逻辑
├── db/                      # SQLite 建表、读写、清理
├── scheduler/               # 管线调度、预算和成本控制
├── gui/gui.py               # Streamlit 可视化界面
├── data/                    # 数据库、缓存、批处理结果等运行数据
└── logs/                    # 日志文件
```

## 环境要求

- Python 3.8+
- 可访问 Reddit 公开页面，或拥有 Reddit API 应用凭据
- 至少一种 AI 服务：
  - OpenAI API
  - Anthropic API
  - OpenAI 兼容本地服务，例如 `http://127.0.0.1:8000/v1`

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Docker Compose 部署

首次部署前先准备环境变量：

```bash
cp .env.template .env
```

Windows PowerShell：

```powershell
Copy-Item .env.template .env
```

然后编辑 `.env`，填入需要的 API Key。启动服务：

```bash
docker compose up -d --build
```

访问：

```text
http://localhost:8501
```

查看日志：

```bash
docker compose logs -f reddit-scrapper
```

停止服务：

```bash
docker compose down
```

Compose 会挂载：

- `./data:/app/data`：SQLite 数据库、缓存和任务状态。
- `./logs:/app/logs`：运行日志。
- `./config/config.yaml:/app/config/config.yaml:ro`：读取本地配置文件。

如果 `OPENAI_BASE_URL` 指向宿主机上的本地模型服务，容器内不能使用 `http://127.0.0.1:端口` 访问宿主机；通常需要改成 `http://host.docker.internal:端口/v1`。

## 配置

复制环境变量模板：

```bash
cp .env.template .env
```

Windows PowerShell：

```powershell
Copy-Item .env.template .env
```

然后编辑 `.env`。

### Reddit 抓取模式

在 `config/config.yaml` 中通过 `reddit.mode` 控制抓取方式：

```yaml
reddit:
  mode: browser  # public_json、html、browser 或 api
  scheduled_crawl_enabled: true

ui:
  default_language: zh  # zh 或 en

ai:
  scheduled_analysis_enabled: true
```

- `public_json`：使用 Reddit 公开 JSON 接口，不需要 Reddit API 凭据。
- `html`：使用普通 HTTP 请求抓取 Reddit 页面 HTML，不需要 Reddit API 凭据。
- `browser`：使用 Playwright 启动独立 Chromium 浏览器实例抓取，不接管你正在使用的浏览器窗口；不需要 Reddit API 凭据。
- `api`：使用 `reddit/scraper.py` 的 PRAW + Reddit API 凭据方式。
- `scheduled_crawl_enabled`：Streamlit 页面运行期间是否在每小时 0 分自动抓取 Reddit。
- `ai.scheduled_analysis_enabled`：Streamlit 页面运行期间是否在每小时 30 分自动分析已有数据。
- `public_scraper.cache_ttl_hours`：公开 JSON 响应缓存时间。
- `ui.default_language`：页面默认语言，`zh` 为中文，`en` 为英文。页面侧边栏也可以随时切换语言。

如果使用 `reddit.mode: browser`，除了 `pip install -r requirements.txt`，还需要安装 Playwright 的 Chromium：

```bash
playwright install chromium
```

浏览器抓取模式会用独立、无界面的 Chromium 访问 Reddit 页面，解析帖子和评论后写入 SQLite。

### Reddit API 模式需要的变量

如果 `reddit.mode: api`，需要配置 Reddit API 凭据：

```env
REDDIT_CLIENT_ID=your_client_id_here
REDDIT_CLIENT_SECRET=your_client_secret_here
REDDIT_USER_AGENT=script:your-app-name:v1.0 (by /u/your_username)
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password
```

如果只运行公开 JSON 抓取或本地管线，通常不需要 Reddit API 凭据，但建议设置一个明确的 `REDDIT_USER_AGENT`。

### AI 配置

OpenAI：

```env
OPENAI_API_KEY=your_openai_api_key_here
```

Anthropic：

```env
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

本地或第三方 OpenAI 兼容接口：

```env
OPENAI_API_KEY=EMPTY
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
```

在 `config/config.yaml` 中选择 provider 和模型：

```yaml
ai:
  provider: openai  # openai 或 anthropic
  openai:
    model_filter: deepseek-3.2
    model_deep: deepseek-3.2
```

常用配置项：

- `subreddits.primary`：目标 subreddit 列表。
- `scraper.max_items_per_day`：单次运行最多处理的条目数。
- `scraper.include_comments`：是否抓取评论。
- `public_scraper.max_requests_per_run`：公开 JSON 模式单次最多请求数。
- `public_scraper.max_items_to_analyze`：本地管线最多分析的条目数。
- `scoring.output_top_n`：最终推荐输出数量。
- `database.path`：SQLite 数据库路径，默认 `data/db.sqlite`。

## 用法

### 1. 页面触发抓取

启动 Streamlit 页面：

```bash
streamlit run gui/gui.py --server.port 8501 --server.address localhost
```

浏览器打开：

```text
http://localhost:8501
```

页面顶部会显示当前 `reddit.mode`、AI provider 和目标 subreddit。在 `Scraped Posts` 标签页点击 `Crawl Reddit` 后，页面会按 `config/config.yaml` 的 `reddit.mode` 选择抓取器：

- `public_json`：调用 `reddit/scraper_public.py`。
- `html`：调用 `reddit/scraper_web.py`。
- `browser`：调用 `reddit/scraper_browser.py`，使用独立 Playwright Chromium 实例。
- `api`：调用 `reddit/scraper.py`。

抓取结果会写入 `data/db.sqlite`，并立即显示在页面的 `Scraped Posts` 标签页。

如果 `scheduled_crawl_enabled: true`，页面服务运行期间会在每小时 0 分自动抓取 Reddit；如果 `ai.scheduled_analysis_enabled: true`，会在每小时 30 分自动分析已有数据。同类任务如果上一轮还没结束，会最多排队 1 次，上一轮结束后立刻补跑。

### 2. 使用本地/兼容 OpenAI 接口抓取并分析

适合使用本地模型或第三方 OpenAI 兼容服务，不使用 Batch API。

```bash
python run_local.py
```

流程：

1. 使用 Reddit 公开 JSON 抓取数据。
2. 调用 `OPENAI_BASE_URL` 指向的 `/v1/chat/completions` 接口。
3. 对帖子/评论评分。
4. 对高潜内容生成深度洞察。
5. 将结果写入 SQLite 和 `data/batch_responses/*.jsonl`。

### 3. 分析数据库中已有的未处理数据

如果已经通过 `run_crawl_public.py` 抓取过数据，可以只分析数据库里的未处理内容：

```bash
python analyze_local.py
```

限制分析数量：

```bash
python analyze_local.py --limit 50
```

调整进入深度洞察阶段的分数阈值：

```bash
python analyze_local.py --threshold 6.5
```

也可以组合使用：

```bash
python analyze_local.py --limit 50 --threshold 6.5
```

### 4. 完整 Batch API 管线

适合正式批量运行。该模式会按 `reddit.mode` 选择抓取方式，并通过配置的 provider 提交 Batch 任务。

```bash
python main.py
```

或在类 Unix 环境中：

```bash
./run.sh
```

流程：

1. 初始化目录、数据库和成本跟踪。
2. 清理过期数据和旧批处理文件。
3. 按 `reddit.mode` 抓取主 subreddit。`api` 模式还会尝试探索相邻 subreddit。
4. 提交过滤 Batch。
5. 解析过滤结果，筛出高潜内容。
6. 提交深度洞察 Batch。
7. 更新 SQLite，并在日志中输出当天 Top 结果。

### 5. 查看结果

界面支持：

- 通过侧边栏切换中文/英文。
- 在 `Scraped Posts` 点击 `Crawl Reddit` 触发抓取。
- 在 `Scraped Posts` 查看刚抓到的原始帖子和评论。
- 在 `Scraped Posts` 查看已抓取总数、已处理数、已生成洞察数、待处理数。
- 在 `Scraped Posts` 按处理状态筛选，默认展示待处理内容。
- 在 `AI Insights` 点击 `Analyze Existing Posts` 分析现有已抓取数据。
- 在 `AI Insights` 查看已经完成 AI 深度洞察的数据。
- 按 ROI、相关性、痛点分、情绪分、技术深度筛选。
- 按 subreddit 过滤。
- 按分数或创建时间排序。
- 查看 AI 生成的产品机会、目标用户、商业模式、技术壁垒等字段。

### 6. 定时运行

项目提供了 `scheduler/daily_scheduler.py` 和 `run_scheduler.sh`，目标是每天 UTC 08:00 运行完整管线：

```bash
python scheduler/daily_scheduler.py
```

或：

```bash
./run_scheduler.sh
```

注意：如果直接运行定时器时报 `scheduler.schedulers.background` 相关导入错误，需要把 `scheduler/daily_scheduler.py` 中的 APScheduler 导入改为：

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
```

## 输出文件

- `data/db.sqlite`：主 SQLite 数据库。
- `data/cache/reddit_json/`：Reddit 公开 JSON 缓存。
- `data/batch_responses/`：过滤和洞察阶段的 JSONL 结果。
- `data/deferred/`：Batch 失败后延迟处理的条目。
- `logs/`：运行日志。

## 数据库表

项目会自动创建以下表：

- `posts`：帖子/评论主体、评分、洞察状态、subreddit、URL 等。
- `history`：已处理 ID，避免重复分析。

`posts` 中的重要字段包括：

- `id`：Reddit 帖子或评论 ID。
- `type`：`post` 或 `comment`。
- `parent_post_id`：评论所属帖子 ID。
- `relevance_score`、`pain_score`、`emotion_score`、`technical_depth_score`：过滤评分。
- `roi_weight`、`tags`：深度洞察结果。
- `insight_processed`：是否已经完成深度洞察。

## 推荐使用流程

第一次使用建议按下面顺序：

```bash
streamlit run gui/gui.py --server.port 8501 --server.address localhost
```

然后在页面点击 `Crawl Reddit`。抓取完成后，如需生成 AI 洞察，再运行：

```bash
python analyze_local.py --limit 20
```

确认抓取、分析和展示都正常后，再根据需要切换到：

- `python run_local.py`：公开 JSON 抓取 + 本地/兼容 OpenAI 分析。
- `python main.py`：Reddit API 抓取 + Batch API 正式管线。

## 注意事项

- `.env` 中包含 API Key 和账号信息，不要提交到 Git。
- 公开 JSON 模式已内置限速、缓存和退避，但仍应保持较小的请求量。
- Batch API 管线可能运行很久，取决于 provider 的 Batch 状态和队列容量。
- `config/config.yaml` 里当前默认抓取量较小，适合 MVP 验证；正式运行前可以逐步调大。
- 如果使用本地模型，模型必须能稳定返回 JSON，否则过滤或洞察解析会失败。
