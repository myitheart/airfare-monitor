# 航价守望 Airfare Monitor

一个面向个人旅行者的 Windows 航价监控工具。安装后可以直接在桌面界面中选择机场、设置日期和心理价位，并持续查看 CNY 含税最低价、历史变化和同一轮查询中的全部航班候选。

> 当前版本：v0.6.18 · Windows 10/11 x64 / macOS 12+ · 个人工具 · 每台设备最多同时启用 10 条航程

## 下载安装

- [下载航价守望 v0.6.18 安装包](https://github.com/myitheart/airfare-monitor/releases/download/v0.6.18/AirfareMonitorSetup-0.6.18.exe)
- [下载 SHA-256 校验文件](https://github.com/myitheart/airfare-monitor/releases/download/v0.6.18/AirfareMonitorSetup-0.6.18.exe.sha256)
- [查看全部 GitHub Releases](https://github.com/myitheart/airfare-monitor/releases)

当前安装包尚未进行商业代码签名，因此 Windows 可能显示“未知发布者”或 SmartScreen 提示。请确认下载地址属于本仓库，并在安装前核对 SHA-256。

![航价守望概览](docs/screenshots/v0.6.17/overview.png)

上图是当前概览页。左侧栏底部的“支持一下”是完全自愿的支持入口，不打赏也不会影响任何功能或后续使用。

> 📍 支持入口：**左侧栏底部 → “支持一下” → 选择支付宝或微信**

## 它能做什么

- 使用中文、拼音或 IATA 搜索内置机场目录，保存真实机场三字码。
- 中国大陆国内航线使用同程旅行；跨境、港澳台和国际航线使用去哪儿。
- 同时管理最多 10 条启用航程，新增、编辑、复制、暂停、启用和删除均可在界面完成。
- 支持国际单程、国际往返组合价，以及直达或中转航班筛选。
- 记录 CNY 含税总价、价格历史、心理价位和本轮全部航班候选。
- 对候选航班按价格、时段和行程类型筛选，并可选择 2～3 个航班进行横向对比。
- 支持桌面提醒和 SMTP 邮件提醒；SMTP 授权码保存在 Windows 系统凭据中。
- 使用独立 Chrome/Edge Profile，可选择显示浏览器过程或后台隐藏运行。
- 关闭主窗口后继续在系统托盘运行，并提供立即查询、暂停/继续和完全退出入口。
- 遇到验证码或设备验证时转为人工处理，不尝试绕过。

## 界面导览

### 概览

概览页集中展示运行状态、今日最低价、启用航程数、最近成功时间和需要处理的事件。每张航程卡片都可以直接进入本轮航班候选。

- 右上角：等待下轮/正在查询/已暂停状态，以及暂停、继续、立即查询和添加航程。
- 中间：各航程的最低含税价、心理价位、更新时间和查询状态。
- 右侧：最近查询、配置变化、低价命中和需要人工处理的动态。
- 左下角：低调的“支持一下”入口，点击后可查看支付宝或微信二维码。

### 我的航程

![我的航程](docs/screenshots/v0.6.17/routes.png)

“我的航程”以卡片形式展示具体机场、日期、出发时间窗、直达/中转偏好、心理价位和最近状态。复制出的航程默认暂停，删除航程不会清除已有价格历史。

### 三步添加或编辑航程

第一步选择具体机场、单程/往返、日期和去返程起飞时间窗：

![航线与日期](docs/screenshots/v0.6.17/route-wizard-route.png)

第二步设置心理价位、直达/中转、重点班次、乘客人数和舱位：

![价格与班次](docs/screenshots/v0.6.17/route-wizard-preferences.png)

第三步确认后保存。达到 10 条启用上限时，仍可将新航程保存为暂停状态。

国内航线当前由同程提供单程直达查询。需要关注返程时，请再创建一条方向相反的国内单程航程；界面会给出相应提示。国际/跨境航线支持去哪儿返回的往返组合总价。

### 历史价格

![历史价格](docs/screenshots/v0.6.17/history.png)

历史页展示当前含税价、区间最低、区间最高、有效价格曲线和最近查询记录。可切换最近 24 小时/7 天，并打开最新 Excel 或报告目录。

蓝线代表获得了有效完整结果；红点代表查询失败、需要人工处理或该轮没有取得有效价格。橙色虚线是当前航程的心理价位。

### 航班候选与对比

![航班候选](docs/screenshots/v0.6.17/flight-candidates.png)

最低价只是入口。每轮查询完成后，可以查看设置范围内保存的全部航班候选，包括：

- 航班号、去返程时刻和总耗时；
- 直达/中转以及最长中转等待；
- CNY 含税总价、基础票价、税费、行李和余票提示；
- 国际往返组合中的去程与返程信息。

勾选 2～3 个候选后可以打开横向对比：

![航班对比](docs/screenshots/v0.6.17/flight-comparison.png)

### 通知设置

桌面通知和邮件通知可以独立开启。桌面通知覆盖低价命中、查询失败、需要人工处理和邮件失败；普通成功轮次不会持续打扰。

邮件支持 SSL/STARTTLS、多个收件人、测试邮件和 Excel 附件。授权码只进入 Windows Credential Locker，不写入 YAML、日志、诊断包或安装包。

### 系统状态

系统状态页可以查看监控服务、数据存储和最近查询健康度，并调整：

- 用于查询的 Chrome/Edge；
- 30/60/120 分钟自动查询间隔；
- 显示或隐藏浏览器运行过程；
- 桌面通知和登录 Windows 后自动启动；
- 独立浏览器 Profile 与本地数据库位置。

## “支持一下”说明

“支持一下”位于所有主页面左侧栏的底部，在版本号上方。入口保持低调，不占用主导航，也不会弹出强制付款页面。

点击后会显示支付宝和微信二维码，并明确说明：

> 如果航价守望帮你守到了合适的价格、少花了一点时间，欢迎请作者喝杯咖啡。完全自愿，不支持也不会影响任何功能和正常使用。

收款码属于发布者的私有打包资源：

- 不保存在 GitHub 或 Gitee 仓库中；
- 不进入配置、日志、数据库或诊断包；
- 源码构建缺少私有收款码时，该入口会自动隐藏；
- 应用不会记录用户是否扫码、支付或支付金额。

## 安装和首次使用

### 普通用户

1. 从可信发布渠道取得 `AirfareMonitorSetup-<版本>.exe` 和同名 `.sha256` 文件。
2. 校验 SHA-256 后运行安装器。当前内部版本未购买代码签名证书，Windows 可能显示“未知发布者”。
3. 首次启动时选择检测到的 Chrome 或 Edge，并确认默认 30 分钟查询间隔和浏览器显示方式。
4. 点击“添加航程”，完成三步设置后开始监控。
5. 需要完全退出时，从系统托盘选择“退出并停止监控”。

覆盖安装不会删除航程、历史价格、浏览器 Profile、Excel 报告或 Windows 凭据。普通卸载默认也保留用户数据。

### 本地数据位置

默认用户数据目录：

```text
%LOCALAPPDATA%/AirfareMonitor/
├── config/                 航程和非敏感设置
├── data/
│   ├── airfare-monitor.sqlite3
│   └── browser-profile/   独立浏览器空间
├── logs/                   脱敏、轮转日志
└── outputs/                Excel 报告
```

应用不会读取用户日常 Chrome/Edge Profile，也不会把 Cookie、Token 或个人邮箱写入 Git。

## 查询来源与价格口径

| 航线类型 | 查询来源 | 当前能力 |
| --- | --- | --- |
| 中国大陆国内 | 同程旅行 | 单程、直达、CNY 含税总价 |
| 国际/跨境/港澳台 | 去哪儿 | 单程或往返组合、直达或中转、CNY 含税总价 |

程序始终按解析后的 CNY 含税总价排序、保存和判断心理价位，不使用基础票价代替最终价格。

去哪儿只有 `result.ctrlInfo.completed == true` 的最终响应才会作为成功结果；同程必须满足既有最终页面状态、航线和日期一致性校验。不完整响应不会写成成功价格，也不会触发低价提醒。

国际往返候选是一组不可拆分的“去程 + 返程 + 往返含税合计价”，不会把两个单程最低价自行相加。

## 运行边界

- 所有启用航程使用一个独立浏览器严格串行查询，不并发放大访问量。
- 每台设备最多同时启用 10 条航程，最短自动查询间隔为 30 分钟。
- 不登录查询网站，不自动下单、预订或支付。
- 不识别或绕过 CAPTCHA、设备验证和账号挑战。
- 报价是查询时刻观察到的结果，最终价格、税费、库存和行李规则以平台或航司确认结果为准。

## 开发者运行

需要 Python 3.11+，推荐 Python 3.12。

```powershell
git clone https://github.com/myitheart/airfare-monitor.git
cd airfare-monitor
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,build]"
.\.venv\Scripts\airfare-monitor-gui.exe
```

也可以从 Gitee 克隆：

```powershell
git clone https://gitee.com/wu_wei_shu/airfare-monitor.git
```

### 离线测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

离线测试不会访问去哪儿或同程，也不会发送真实邮件。

### 构建 Windows 安装包

安装 Inno Setup 7 后执行：

```powershell
.\.venv\Scripts\python.exe packaging\build_release.py
```

构建脚本会执行打包 UI 烟测，然后生成安装器和 SHA-256 文件。私有收款码应只放在被 Git 忽略的 `private-assets/support/` 目录中；没有这些资源时仍可正常构建，只是不显示支持入口。

## 项目结构

```text
airfare-monitor/
├── docs/                         产品、开发、UI 和发布文档
├── packaging/                    PyInstaller 与 Inno Setup 配置
├── resources/                    可公开打包资源和默认配置
├── src/airfare_monitor/          采集核心、桌面适配和 PySide6 UI
├── tests/                        离线测试
├── data/ logs/ outputs/          开发环境运行目录（Git 忽略）
└── private-assets/               私有打包资源（Git 忽略）
```

## 相关文档

- [产品需求文档](docs/EXE_DEMO_PRODUCT_REQUIREMENTS.md)
- [桌面开发手册](docs/EXE_DEMO_DEVELOPMENT_GUIDE.md)
- [UI 设计与页面映射](docs/ui-design/README.md)
- [数据模型](docs/DATA_MODEL.md)
- [邮件与 Excel 说明](docs/EMAIL_AND_EXCEL.md)
- [R4 安装与稳定性说明](docs/R4_RELEASE_NOTES.md)

## 安全与隐私

- 不在 Git 中保存 SMTP 密码、授权码、Cookie、浏览器 Token、个人邮箱或私人收款码。
- 独立浏览器 Profile、SQLite、日志和 Excel 都保存在用户目录，不随源码上传。
- 诊断包只包含脱敏运行摘要和日志片段，不包含配置、数据库、原始响应、浏览器 Profile 或凭据。
- 真实网站查询和真实邮件发送不会由离线测试或构建脚本触发。
