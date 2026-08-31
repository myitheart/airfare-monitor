# Airfare Monitor

个人四程航班价格监控项目。计划使用 DrissionPage 打开去哪儿国际机票搜索页，监听完整的航班搜索响应，筛选每一程总价最低的 10 个直达航班，每 30 分钟生成 Excel 并发送邮件摘要。

当前已实现配置校验、DrissionPage 监听框架、完整响应硬门槛、航班筛选、SQLite、Excel、合并邮件和非重叠调度。去哪儿未公开的响应字段与搜索页参数仍需使用脱敏抓包样本或一次获授权的集成运行校准。

## 目标

- 监控 4 个航程。
- 每程按机场 IATA 三字码、出发日期和 ETD 时间窗查询。
- 默认仅保留直达航班，按含税总价升序取前 10。
- 每 30 分钟发送一封摘要邮件并附 Excel。
- 任一航程最低价不高于心理预期价格时，在邮件主题中明确标识。
- 只采集和通知，不自动下单或支付。

## 目录

```text
airfare-monitor/
├── config/                  配置样例
├── data/                    SQLite 与浏览器独立 Profile（运行期生成）
├── docs/                    设计和字段说明
├── logs/                    运行日志（运行期生成）
├── outputs/                 Excel 输出（运行期生成）
├── src/airfare_monitor/     后续 Python 包
├── tests/                   后续测试
├── .env.example             邮件凭据环境变量样例
└── pyproject.toml           Python 项目元数据
```

## 配置原则

“起运地/目的地三字码”指机场 IATA 三字码，例如 `PVG`、`KUL`；航司代码是另一字段，通常为两字符，例如 `MU`、`9C`。

复制以下样例后再填写真实信息：

```text
config/routes.example.yaml   -> config/routes.yaml
config/settings.example.yaml -> config/settings.yaml
```

邮件用户名、发件地址、收件地址和密码分别从 `SMTP_USERNAME`、`SMTP_SENDER`、`SMTP_RECIPIENTS`、`SMTP_PASSWORD` 环境变量读取。命令行启动时也会加载项目根目录中被 Git 忽略的 `.env`。不要把 `.env`、浏览器 Profile、Cookie 或邮件密码提交到版本库；长期使用仍推荐操作系统环境变量。

## 命令

使用 Python 3.11 或更新版本创建虚拟环境并安装：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

仅校验配置（不会启动浏览器）：

```powershell
.\.venv\Scripts\airfare-monitor.exe validate
```

采集一次并生成 Excel，默认不发邮件：

```powershell
.\.venv\Scripts\airfare-monitor.exe run-once
```

明确添加 `--send-mail` 才发送单次真实邮件；`daemon` 会依据 `mail.enabled` 每 30 分钟运行和发送。任何真实采集或邮件测试都应在明确授权后执行。
