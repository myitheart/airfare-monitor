# AirfareMonitor（航价守望）macOS 分发包 · AI 安装说明

本文件面向 AI 编码助手（ZCode / Claude Code / Cursor 等）。用户安装本应用后，可能会对你说：
「帮我把 AirfareMonitor 的终端命令装好」。请按本文档执行，完成后向用户报告结果。

> 人类用户看这里：把本文件所在的应用拖入 /Applications，然后对你的 AI 助手说
> 「按 /Applications/AirfareMonitor.app 里的 AGENTS.md 安装 CLI」即可，无需手动跑脚本。

## 产品形态

单个 .app 内置 GUI 与 CLI 双形态，共用同一份冻结运行时与数据：

- 图形客户端：直接打开 AirfareMonitor.app。
- 终端 CLI：同一二进制按 argv 分流（符号链接名或子命令触发）。
- 数据根：`~/Library/Application Support/AirfareMonitor`（GUI 与 CLI 共享，进程锁互斥——两者同时只允许一个在跑）。

## 安装步骤

1. **定位应用**：`/Applications/AirfareMonitor.app`；不存在则检查 `~/Applications/`，
   仍找不到就问用户拖到了哪里。
2. **验证 CLI 通道**（只读校验，不启动浏览器、不触发查询）：
   ```sh
   /Applications/AirfareMonitor.app/Contents/MacOS/AirfareMonitor validate
   ```
   期望输出「配置有效：…」且退出码 0。
3. **建立命令链接**（无需 sudo）：
   ```sh
   mkdir -p ~/.local/bin
   ln -sf /Applications/AirfareMonitor.app/Contents/MacOS/AirfareMonitor ~/.local/bin/airfare-monitor
   ```
4. **PATH**：确认 `~/.local/bin` 在 PATH 中；不在则把
   `export PATH="$HOME/.local/bin:$PATH"` 追加到 `~/.zshrc`（bash 用户用 `~/.bash_profile`），
   并提醒用户重开终端生效。
5. **终验**：`airfare-monitor --help` 显示子命令、`airfare-monitor validate` 退出码 0。

## CLI 子命令

| 命令 | 作用 |
|---|---|
| `validate` | 校验配置，不启动浏览器 |
| `run-once` | 采集一轮并生成 Excel（`--send-mail` 才发邮件） |
| `daemon` | 按配置间隔常驻轮询并发邮件 |
| `price [--leg ID] [--last N]` | 各航程最近价格与涨跌 |
| `history [--leg ID] [--hours N]` | 价格历史明细 |
| `routes list/add/edit/show/remove/enable/disable` | 航程全生命周期管理 |
| `status` | 调度锁/最近轮次/各航程最新价 |
| `doctor` | 环境自检（配置/浏览器/数据库/签名身份） |
| `open [ID]` | 用默认浏览器打开来源网站同口径搜索页 |
| `--user-root <dir>` | 指定独立数据根（测试/隔离用，缺省与 GUI 共享） |
| `--json` | 全局旗标：机器可读输出（routes/price 等），供脚本/AI 消费 |

## 自然语言 → 命令对照（AI Cookbook）

用户说一句话时，映射到命令执行（先 `routes list` / `status` 摸清现状，再动手）：

| 用户意图 | 命令 |
|---|---|
| 「看下现在什么价」 | `airfare-monitor price` |
| 「上海飞大阪最近趋势」 | `airfare-monitor history --hours 48` |
| 「加一条 11 月 26 上海到大阪、往返 12 月 1 回、1500 以下提醒我」 | `airfare-monitor routes add --from 上海 --to 大阪 --date 2026-11-26 --return 2026-12-01 --threshold 1500` |
| 「那条航线只看浦东出发」 | `airfare-monitor routes edit <ID> --from PVG` |
| 「帮我盯早上的直飞班次」 | `airfare-monitor routes edit <ID> --preferred "早班直飞,08:00,12:00,30" --etd-start 06:00 --etd-end 12:00` |
| 「先别监控大阪这条」 | `airfare-monitor routes disable <ID>` |
| 「现在查一轮看看」 | `airfare-monitor run-once` |
| 「程序健康吗」 | `airfare-monitor doctor` + `airfare-monitor status` |

要点：`--from/--to` 接受 IATA 码、城市码或中文名；裸城市名（如"上海"）= 同城全部机场聚合比价，
传 `PVG` 这类机场码 = 单机场精确；歧义时报错会列出候选供你改写。所有变更下一轮自动生效
（GUI 与 daemon 每轮重读配置），改完用 `routes show <ID>` 核对、`validate` 兜底校验。

## 注意事项

- 不要修改 .app 内部内容；不要使用 sudo（除非用户明确要求装进 `/usr/local/bin`）。
- 安装包使用本机自签名证书（未经 Apple 公证）。首次打开可能被 Gatekeeper 拦截：
  引导用户右键 → 打开，或在征得同意后执行
  `xattr -dr com.apple.quarantine /Applications/AirfareMonitor.app`。
- `daemon` 或 GUI 报「已有监控进程持有运行锁」属预期互斥，不是故障。
- 卸载：删除符号链接 + 拖 .app 进废纸篓即可；用户数据在
  `~/Library/Application Support/AirfareMonitor`，按需另行清理。
