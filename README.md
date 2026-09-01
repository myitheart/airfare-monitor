# Airfare Monitor

个人使用的多航程航班价格监控工具。程序使用 DrissionPage 控制一个独立 Chromium Profile，按照正常页面流程在去哪儿国际机票搜索框中选择起运地、目的地和日期，然后监听 `/touch/api/inter/wwwsearch` 的增量响应。

只有 `result.ctrlInfo.completed == true` 的最终响应才会作为成功结果。程序默认保留每程含税总价最低的 10 个直达航班，写入 SQLite、生成一份合并 Excel，并发送一封手机可读的摘要邮件。

本项目只负责查询、记录和通知，不登录去哪儿，不自动下单或支付，也不处理或绕过验证码、设备验证。

## 已实现功能

- 任意数量的启用航程串行采集，避免搜索会话互相干扰。
- 机场/城市 IATA 代码联想选择，保存航班实际起降机场。
- 仅接受完整搜索响应，不把中间增量结果记为成功。
- 直达和 ETD 时间窗筛选，按 CNY 含税总价升序排列。
- 同一行程的多个供应商报价按完整航班签名去重，保留最低总价。
- SQLite 历史记录、24 小时最低价历史和原始响应短期保存。
- 一份合并 Excel 和一封合并邮件，不按航程分别发送。
- 心理价位命中后二次确认，再使用低价命中邮件主题。
- 单程失败自动重试一次；连续失败后重启独立浏览器。
- CAPTCHA 或设备验证只标记为需要人工处理，不尝试绕过。
- 进程锁防止两个调度任务同时运行。

## 目录结构

```text
airfare-monitor/
├── config/
│   ├── routes.example.yaml      航程配置样例
│   └── settings.example.yaml    运行配置样例
├── data/                        SQLite、运行锁、独立浏览器 Profile
├── docs/                        设计、数据模型、邮件与 Excel 说明
├── logs/                        可用于保存重定向后的运行日志
├── outputs/                     生成的 Excel
├── src/airfare_monitor/         Python 源码
├── tests/                       离线测试
├── .env.example                 SMTP 环境变量样例
└── pyproject.toml               项目及依赖定义
```

真实的 `.env`、`config/routes.yaml`、`config/settings.yaml`、数据库、浏览器 Profile、日志和 Excel 都被 Git 忽略，不会随代码推送。新设备第一次使用时需要从样例复制并填写。

## 一、新设备快速安装

### 环境要求

- Windows 10/11（当前已验证环境）。
- Python 3.11 或更新版本，推荐 Python 3.12。
- 可用的 Chrome、Chromium 或 Edge 浏览器。
- 一个支持 SMTP 的邮箱及其 SMTP 授权码。
- 能正常访问去哪儿国际机票页面。

### 1. 克隆代码

从 Gitee 克隆：

```powershell
git clone https://gitee.com/wu_wei_shu/airfare-monitor.git
cd airfare-monitor
```

GitHub 网络可用后也可以从 GitHub 克隆：

```powershell
git clone https://github.com/myitheart/airfare-monitor.git
cd airfare-monitor
```

### 2. 创建虚拟环境并安装依赖

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

如果没有 `py -3.12`，可换成已安装的 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### 3. 创建本地配置

```powershell
Copy-Item config\routes.example.yaml config\routes.yaml
Copy-Item config\settings.example.yaml config\settings.yaml
Copy-Item .env.example .env
```

这些真实配置已在 `.gitignore` 中，不要使用 `git add -f` 强制提交。

## 二、配置待监控航程

编辑 `config/routes.yaml`。每个 `legs` 项代表一个航程：

```yaml
legs:
  - id: leg-1
    enabled: true

    origin_airport_iata: SHA
    origin_name_zh: 上海
    destination_airport_iata: KUL
    destination_name_zh: 吉隆坡

    departure_date: "2026-09-27"
    etd_window:
      start: "00:00"
      end: "23:59"

    direct_only: true
    expected_total_price_cny: 1000
    top_n: 10

    adult_count: 1
    child_count: 0
    cabin_class: economy

    # 可选：在邮件中单独展示某个关注时刻的实时价格
    preferred_schedules:
      - label: 上海浦东 → 吉隆坡
        departure_time: "07:25"
        arrival_time: "13:00"
        arrival_day_offset: 0
        origin_airport_iata: PVG
        destination_airport_iata: KUL
```

`legs` 是非空列表，没有固定为 4 程。可以继续复制航程块并使用唯一的 `id` 添加 `leg-5`、`leg-6` 等，也可以用 `enabled: false` 临时停用某一程。邮件、Excel 和历史记录会根据本次实际启用的航程数量动态生成。

### 航程字段说明

| 字段 | 说明 |
|---|---|
| `id` | 稳定且不重复的航程编号，例如 `leg-1` |
| `enabled` | `true` 表示启用，`false` 表示临时停用 |
| `origin_airport_iata` | 起运地 IATA 三字码，例如 `SHA`、`PVG`、`KUL` |
| `destination_airport_iata` | 目的地 IATA 三字码 |
| `origin_name_zh` | 邮件和 Excel 显示的起运地中文名，可选但建议填写 |
| `destination_name_zh` | 邮件和 Excel 显示的目的地中文名，可选但建议填写 |
| `departure_date` | 出发日期，必须是 `YYYY-MM-DD` |
| `etd_window.start/end` | 当地计划起飞时间窗，必须是 `HH:MM` |
| `direct_only` | `true` 只保留直达，`false` 允许中转 |
| `expected_total_price_cny` | 心理价位，使用解析后的 CNY 含税总价比较 |
| `top_n` | 每程最多保留多少个最低价行程 |
| `adult_count` | 成人数量，至少为 1 |
| `child_count` | 儿童数量，可以为 0 |
| `cabin_class` | `economy`、`premium_economy`、`business` 或 `first` |
| `preferred_schedules` | 可选关注时刻列表；邮件会在最低价 3 条之外展示匹配航班的实时含税价 |

每个 `preferred_schedules` 项使用以下字段：

| 字段 | 说明 |
|---|---|
| `label` | 邮件中显示的自定义航线名称 |
| `departure_time` | 精确计划起飞时间，格式 `HH:MM` |
| `arrival_time` | 精确计划到达时间，格式 `HH:MM` |
| `arrival_day_offset` | 到达日相对出发日的天数；当天为 `0`，次日为 `1` |
| `origin_airport_iata` | 可选的实际起飞机场，用于在城市代码搜索结果中精确匹配机场 |
| `destination_airport_iata` | 可选的实际到达机场 |

关注航班按机场、日期、ETD 和 ETA 精确匹配。即使其价格没有进入最低价前 `top_n`，邮件仍会单独展示；本轮没有匹配航班时会显示“本次未找到匹配直达航班”。配置中的关注时刻不会改变心理价位，也不会直接触发低价命中。

全天监控可使用：

```yaml
etd_window:
  start: "00:00"
  end: "23:59"
```

限制早晨起飞可使用：

```yaml
etd_window:
  start: "06:00"
  end: "11:30"
```

时间窗也支持跨午夜，例如 `22:00-02:00`。

注意：机场 IATA 和航司代码是不同字段。`PVG`、`KUL` 是地点代码，`MU`、`9C` 是航司代码，不要混用。去哪儿的城市联想可能覆盖同城多个实际机场，Excel 中保存的是接口返回的真实起降机场。

## 三、配置刷新间隔和浏览器

编辑 `config/settings.yaml`。

### 调度配置

```yaml
schedule:
  timezone: Asia/Shanghai
  interval_minutes: 30
  jitter_seconds: 120
  prevent_overlapping_runs: true
```

- `interval_minutes`：采集间隔，必须是正整数。修改为 `10` 即约每 10 分钟运行一次。
- `jitter_seconds`：每轮额外增加的随机延迟上限。设置为 `0` 表示不增加随机延迟。
- `prevent_overlapping_runs`：保留为 `true`。程序还会使用进程锁，防止启动第二个守护任务。
- 配置修改后需要停止并重新启动 `daemon` 才会生效。

### 浏览器配置

```yaml
browser:
  engine: drissionpage
  headless: false
  user_data_path: data/browser-profile
  local_port: 9333
  page_load_timeout_seconds: 45
  search_completion_timeout_seconds: 75
  restart_after_consecutive_failures: 2
  search_url_template: "https://flight.qunar.com/site/oneway_list_inter.htm"
```

- `headless: false` 会显示浏览器窗口，首次部署和排查问题时建议保持此设置。
- `user_data_path` 必须指向项目独立 Profile，不要改成日常 Chrome Profile。
- `local_port` 被占用时可换成其他未使用端口，例如 `9444`。
- `search_completion_timeout_seconds` 是等待最终完整响应的最长时间。
- `search_url_template` 是已经验证的国际单程页面，除非页面结构变化，不建议修改。

### 低价二次确认

```yaml
collection:
  source: qunar
  currency: CNY
  sort_by: total_price
  require_completed_response: true
  secondary_confirmation_delay_seconds: 120
```

当最低含税总价达到心理价位时，程序默认等待约 120 秒重新查询命中航程。第二次仍命中才使用 `[低价命中]` 主题。

## 四、配置 SMTP 邮件

### 邮件服务器设置

在 `config/settings.yaml` 中配置非敏感 SMTP 参数：

```yaml
mail:
  enabled: true
  smtp_host: smtp.example.com
  smtp_port: 465
  security: ssl
  username_env: SMTP_USERNAME
  password_env: SMTP_PASSWORD
  sender_env: SMTP_SENDER
  recipients_env: SMTP_RECIPIENTS
  attach_excel: true
  normal_subject_prefix: "[航价监控]"
  threshold_subject_prefix: "[低价命中]"
```

- 端口 `465` 通常配合 `security: ssl`。
- 使用 STARTTLS 的服务通常需要相应端口并设置 `security: starttls`。
- `mail.enabled: false` 可关闭真实邮件发送。

### 邮箱地址与授权码

编辑项目根目录的 `.env`：

```dotenv
SMTP_USERNAME=your-account@example.com
SMTP_PASSWORD=your-smtp-app-password
SMTP_SENDER=your-account@example.com
SMTP_RECIPIENTS=recipient@example.com
```

多个收件地址使用英文逗号分隔：

```dotenv
SMTP_RECIPIENTS=first@example.com,second@example.com
```

`.env` 只适合个人设备上的本地使用。不要把 SMTP 登录密码填入 `settings.example.yaml`、README、测试或任何被 Git 跟踪的文件。共享设备或长期部署建议改用操作系统环境变量；同名系统环境变量优先于 `.env`。

## 五、校验和手动测试

以下命令都在项目根目录执行。

### 1. 仅校验配置

```powershell
.\.venv\Scripts\airfare-monitor.exe validate
```

该命令不会启动浏览器、访问去哪儿或发送邮件。成功时会输出启用航程数量。

### 2. 运行离线测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

离线测试不会访问去哪儿，也不会发送邮件。

### 3. 采集一次但不发邮件

```powershell
.\.venv\Scripts\airfare-monitor.exe run-once
```

该命令会真实访问去哪儿、写入 SQLite 并生成 Excel，但不会发送邮件。

### 4. 完整运行一次并发送邮件

```powershell
.\.venv\Scripts\airfare-monitor.exe run-once --send-mail
```

该命令适合首次部署后的全链路验证。需要同时满足：

- `mail.enabled: true`；
- `.env` 或操作系统中包含四个 SMTP 环境变量；
- SMTP 主机、端口和安全方式正确。

## 六、开启持续监控

前台启动：

```powershell
cd D:\path\to\airfare-monitor
.\.venv\Scripts\airfare-monitor.exe daemon
```

行为说明：

1. 启动后立即运行一次。
2. 所有启用航程串行采集，等待各程最终完整响应。
3. 生成 Excel，并在 `mail.enabled: true` 时发送合并邮件。
4. 等待配置的间隔和随机抖动后继续下一轮。
5. 浏览器在守护进程生命周期内保持运行，仅在连续失败达到阈值时重启。

保持 PowerShell 窗口开启。按 `Ctrl+C` 停止监控。修改航程、刷新间隔或 SMTP 配置后，也应先按 `Ctrl+C` 停止，再重新执行 `daemon`。

### 后台启动（可选）

首次部署建议先前台运行并确认邮件正常。验证后可在 PowerShell 中隐藏窗口启动，并把输出保存到日志：

```powershell
New-Item -ItemType Directory -Force logs | Out-Null
Start-Process `
  -FilePath ".\.venv\Scripts\airfare-monitor.exe" `
  -ArgumentList "daemon" `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden `
  -RedirectStandardOutput "logs\monitor.stdout.log" `
  -RedirectStandardError "logs\monitor.stderr.log"
```

需要开机自动运行时，可在 Windows 任务计划程序中创建“登录时”任务，程序指向虚拟环境中的 `airfare-monitor.exe`，参数填写 `daemon`，起始目录填写项目根目录。不要同时手工再启动一个 `daemon`。

## 七、输出和数据位置

### Excel

默认输出到 `outputs/`，文件名为：

```text
airfare-monitor_YYYYMMDD_HHMM.xlsx
```

工作簿包含：

- `本次汇总`：每程心理价位、最低价、变化、命中状态和采集状态。
- `航程1`～`航程N`：根据启用航程数量动态生成，每程保存最低价直达航班。
- `24小时历史`：各程每次采集的最低总价。

### SQLite

默认数据库：

```text
data/airfare-monitor.sqlite3
```

包含运行状态、每程结果、航班快照和短期原始响应。不要在监控运行时手工修改数据库。

### 浏览器 Profile

默认位置：

```text
data/browser-profile/
```

这是监控专用的独立 Profile。不要替换成用户日常 Chrome Profile，也不要把其中的 Cookie 提交到 Git。

## 八、邮件规则

正常邮件主题：

```text
[航价监控] N程更新 | YYYY-MM-DD HH:mm
```

确认命中心理价位时：

```text
[低价命中][命中数/N程] 上海（SHA） → 吉隆坡（KUL） ¥980 ≤ ¥1,000 | YYYY-MM-DD HH:mm
```

部分航程失败时，主题包含 `[部分失败]`。邮件正文使用“中文名（IATA）”显示航程，并列出每程最低价、心理价位、与上次变化、最便宜的三个航班，以及配置的关注时刻实时含税价。价格和库存仍需在 App 中最终确认。

## 九、常见问题

### `validate` 提示配置不存在

确认已经复制两个样例文件：

```powershell
Copy-Item config\routes.example.yaml config\routes.yaml
Copy-Item config\settings.example.yaml config\settings.yaml
```

### 机场代码没有联想项

- 检查是否误填了航司代码。
- 确认代码是去哪儿页面可识别的 IATA 地点代码。
- 手工打开去哪儿国际机票页面，确认该地点能正常搜索。

程序始终选择页面返回的第一条联想，不会自行构造 Bella 或重放接口。

### 一直等待完整响应或超时

- 确认网络能够正常访问去哪儿。
- 保持 `headless: false`，观察是否出现验证码或设备验证。
- 适当提高 `search_completion_timeout_seconds`。
- 不完整响应不会被保存为成功，也不会触发低价提醒。

### 出现验证码或设备验证

程序会停止当前航程并标记需要人工处理，不会自动破解。可关闭监控、在独立浏览器中人工确认页面状态，然后重新运行。

### 浏览器端口被占用

修改 `config/settings.yaml`：

```yaml
browser:
  local_port: 9444
```

确保没有两个监控进程使用相同端口和 Profile。

### SMTP 登录失败

- 确认使用的是 SMTP 授权码，不是网页登录密码。
- 检查主机、端口和 `ssl/starttls` 配置。
- 确认 `.env` 中没有多余引号或空格。
- 先执行 `validate`，再使用 `run-once --send-mail` 做全链路测试。

### 邮件没有发送

确认：

```yaml
mail:
  enabled: true
```

`run-once` 默认不发邮件，单次测试必须显式添加 `--send-mail`。`daemon` 会在邮件启用时自动发送。

### 修改配置后没有生效

运行中的进程不会热加载配置。按 `Ctrl+C` 停止后重新执行 `daemon`。

## 十、更新代码

真实配置和运行数据均被 Git 忽略，正常拉取代码不会覆盖它们：

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\airfare-monitor.exe validate
```

依赖或源码更新后，先停止旧的 `daemon`，重新安装并校验，再重新启动。

## 安全边界

- 不在 Git 中保存 SMTP 密码、Cookie、浏览器 Token 或个人邮箱配置。
- 不使用用户日常浏览器 Profile。
- 不实现登录、下单、支付、验证码绕过或设备挑战绕过。
- 测试默认只使用本地模拟响应；真实采集和真实邮件由用户通过运行命令明确触发。
- 报价是采集时的页面结果，最终价格、税费、库存和行李规则以 App 或航司确认结果为准。
