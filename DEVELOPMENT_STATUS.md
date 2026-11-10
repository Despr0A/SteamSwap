# SteamSwap 开发状态

最后更新：2026-11-10

## 环境

- 推荐环境：`D:\software\miniconda3\envs\steamswap`，使用 `conda activate steamswap` 激活。
- Python：3.11.15
- pytest：9.1.1
- 当前仓库分支：`main`
- 测试命令：`D:\software\miniconda3\envs\steamswap\python.exe -m pytest`

## 最近完成

- 2026-11-10 按用户要求整理当前代码、测试与文档，准备提交至现有 `origin/main`。将本机自定义策略与用户模块配置加入 Git 忽略规则；账号、凭据、数据库、浏览器资料、日志及项目外备份不纳入提交。

- 2026-11-10 按用户要求直接将原有 Conda 环境目录重命名为小写 `steamswap`，更新 Conda 环境登记，修正 25 个命令入口和库配置文件中的旧绝对路径。未使用克隆流程，未下载、重装或升级依赖，46 个已安装 Python 包的版本保持一致；旧环境目录已不存在。迁移前入口备份、执行脚本及校验记录保存在项目外 `D:\13299\Documents\py_CODE\SteamSwap-local-history-20261110`。

- 2026-11-10 项目标识统一为 SteamSwap：后端应用标题、启动提示、付款提示、Windows 通知标识、前端引导、错误导出文件名及浏览器存储键均已更新。启动环境变量统一为 `STEAMSWAP_` 前缀，不保留旧前缀兼容；README 提供升级说明。
- README 集中记录项目来源与原作者致谢，说明架构变化及独立维护动机；反馈入口改为本项目 Issues。环境示例改为实际支持的服务监听变量，并明确程序不会自动加载 `.env`。
- 4 张未被当前代码引用的旧界面截图与 15 个包含旧名称的历史诊断文件已原样移至项目外 `D:\13299\Documents\py_CODE\SteamSwap-local-history-20261110`，逐个 SHA-256 校验一致。未迁移交易数据库或登录凭据；Git 历史与现有未提交业务修改保留。

- 2026-11-10 前端按用户提供的浅色 Fluent 参考改为 SteamSwap 交易工作台：独立交换图标、分组左侧导航、任务操作台、常用模块入口和启动前检查说明。保留 8 个模块、原页面 99 个带 ID 的操作控件、付款确认、报告关联及现有业务绑定。
- 新增 `web/css/fluent.css`，统一蓝色主操作、亚克力面板、8px 卡片/2px 控件圆角、焦点环与减少动效支持；覆盖账号、策略、代理、令牌、设置、交易列表、报告和弹窗。窄屏保留左侧精简导航，修正账号详情和策略工具栏挤压。默认浅色，继续尊重已保存的外观和缩放设置。
- 新增快捷入口导航和页面说明，代理策略卡片、Steam 令牌支持 Enter/Space 操作；导航及策略选择同步无障碍状态。新增离线 Edge 验证脚本 `tests/check_fluent_ui.py`，使用当时的项目环境的 Playwright，全部页面请求均拦截为本地资源或模拟数据。

- 2026-11-10 根据发布前文档要求再次改写 README，以项目使用者为读者，围绕安装启动、首次配置、观测与购买、策略调整、通知代理、备份迁移和常见问题组织。删除上游关系介绍、内部开发状态与测试说明、源码结构、个人环境名称和审查式叙述；对齐现有界面的“仅观测”“启动任务”等入口。实际环境变量名保留；免责声明正文不变并折叠展示。此版替代此前偏向实现审查的 README。

- 2026-11-10 按当前代码重写 README：重新组织功能边界、安装与首次配置、观测/真实买入、付款确认、策略中心、配置、数据备份和排查入口；保留原有历史缓存、币种归一化、单品限量与分层报告说明，以及项目来源、联系方式和免责声明正文。
- 修正文档中“99% 无人工干预”、每日金额上限、仅微信支付、配置全部立即生效、一次性观测、完整进销存收益展示和 Docker 已交付等不准确描述。补充支付宝实现、单笔结账、默认暂停出售、模拟使用样例数据、目标金额实际统计口径、备份缺失范围及首次上架触发限制。原 GPL 徽章指向不存在的 LICENSE，已移除该标记；未新增或修改授权条款。

- 修复网页确认付款后命令行仍停留在输入提示的问题：共用付款等待流程结束时，终端立即输出确认、取消/超时、停止或异常结果，并提示确认后无需再输入 y/n。输入说明在启动监听线程前输出，避免快速确认后才出现旧提示。此变更同步的是 SteamSwap 的人工确认结果，未新增 BUFF 付款状态自动查询。

- 按用户澄清统一采买顺序：先由历史服务复用有效的本地数据或刷新 SQLite（默认 12 小时有效期），然后共用同一份历史数据计算参考价、检查折扣和稳定性；原始折扣不再挡在历史更新之前。刷新失败暂缓该饰品；付款前二次验证也通过缓存服务读取有效历史，缺失时不下单。
- 调试页面默认显示可展开的饰品报告，可按结果筛选，并跳转到同次检查的网络报告。详情包含数据时间、来源、参考价、公式和阈值。候选阶段的逐行细节收纳到报告内；历史读取报告区分内存、数据库、刷新和实际适配器调用，Steam/BUFF 卖单读取也提供关联报告。API、下载、导出、恢复均保留结构化字段与脱敏。
- 新增数据库缓存有效期边界、刷新落库后共享数据、无效历史暂缓、拒绝分支报告、关联筛选和转义测试；修复报告中历史分析状态与检查状态重名导致的异常。

- 修复历史表残留未换算记录导致整份行情不可读的问题：读取时跳过 `price_cny=NULL` 的点并记录未换算/可用数量，保留原始记录及币种，不根据最新响应猜测旧记录币种；最新响应币种不可用或没有可用价格时仍拒绝使用。
- 2026-11-02 运行日志确认 MAG-7 在 18:59:56（CNY）和 19:00:10（USD）入库成功后均报归一化失败；数据库有 10 个旧点缺币种/人民币价格，触发了原先“任一点为空则整份拒绝”的判断，并导致有效期内重复请求。
- 将 Steam 历史成交价格的默认刷新间隔从 300 秒调整为 12 小时；持久化历史点仍按时间点增量合并。
- 修复账号管理界面“添加”、编辑弹窗“取消”和“保存”按钮没有事件绑定的问题。
- 修复账号搜索框没有 `input` 事件绑定的问题。
- 新增 `tests/test_frontend_contracts.py`：
  - 检查 HTML 中的交互控件是否被 JavaScript 引用。
  - 检查前端调用的 API 路径是否存在后端路由。
  - 检查账号管理按钮和搜索框的绑定是否存在。
- 创建项目专属 `AGENTS.md`，记录环境、验证方式、前后端契约和工作区纪律。
- 修复 Steam 历史响应 `currency=null` 的归一化路径：对身份匹配的账号池 Cookie，在无币种字段或仅返回 `¥/￥` 时按已有币种或 CNY 默认规则处理；未知的非空货币符号仍拒绝入库。
- 将 Steam 历史接口返回的普通 `¥` 符号直接识别为 CNY；明确的 `JPY`、`JP¥`、`円` 标记仍优先识别为 JPY。

## 验证记录

- 2026-11-10 环境重命名后：`conda activate steamswap` 选中正确解释器；Conda 环境列表不再包含旧环境。Python 3.11.15、pytest 9.1.1、NumPy、SSL、SQLite、FastAPI 和桌面窗口模块导入正常；`python -m pip check` 无依赖冲突。`pip`、`pytest`、`playwright` 命令与 `run.py --help` 通过，完整 pytest `624 passed`。受限执行中 `pip.exe` 一度出现 Windows DLL 重定位错误，随后在受限环境外直接执行验证通过。未启动交易服务。

- 2026-11-10 标识迁移：先将运行环境和通知回归用例更新为新标识，复现 7 项失败；实现更新后，使用原有 Python 环境运行完整 pytest：`624 passed`。前端报告测试 `3 passed`，全部 JavaScript 语法检查及 `git diff --check` 通过。README 本地链接、代码围栏与折叠标签检查通过，`run.py --help` 显示新项目名称与环境变量。清理 44 个含旧路径或旧标识的字节码缓存后，全目录扫描（含隐藏文件、被忽略的文件及二进制内容，排除 `.git` 和 README）无旧项目名称命中。未启动交易服务或进行真实支付、上架。

- 2026-11-10 Fluent UI：最终完整 pytest `624 passed`；`node --test tests/test_console_reports.cjs`：3 passed；全部 `web/js/*.js` 通过 `node --check`。原 HTML 控件 ID 保留检查：99/99，8 个导航模块均保留。
- 本机 Edge 离线检查：1440/1024/768/420/360px × 8 模块，共 40 组空状态布局；1440/420/360px × 账号/交易/策略，共 9 组模拟数据布局，均无页面或面板横向溢出（表格允许内部滚动）。快捷入口焦点、账号搜索与添加/取消弹窗、代理键盘选择、令牌键盘显示、70%/100%/120% 缩放、报告展开与关联跳转、模拟请求失败提示、付款提示、深色切换、新手向导检查通过，未捕获页面 JavaScript 异常。人工查看了桌面、窄屏、带数据页面和弹窗截图。
- UI 复现：`D:\software\miniconda3\envs\steamswap\python.exe tests/check_fluent_ui.py <截图输出目录>`。需要本机 Edge 和已有 Playwright；当前沙箱无法启动 Edge，验证通过受限环境外执行完成。截图保存在 `C:\Users\13299\.codex\visualizations\2026\11\10\01a1c47a-4dc9-7d32-bbc6-39c3d3ae30ad\fluent-ui`。

- 2026-11-10 使用手册版：本地链接与目录锚点、代码围栏、折叠标签、界面入口和安装文件引用检查通过；PowerShell 示例通过语法解析，未执行安装命令；免责声明正文保持一致。文档 `git diff --check` 通过。本轮仅编辑文档，未重跑业务测试，未启动或部署应用。

- 2026-11-10 README 核验：使用当时的项目环境运行 `run.py --help`，确认命令参数；静态检查 22 个 Markdown 链接（本地目标和目录锚点有效，外链仅检查格式）、13 项代码默认值、JSON 模块示例与代码围栏。免责声明正文与改写前一致。
- 文档相关回归：`tests/test_runtime_env.py tests/test_background_startup.py tests/test_frontend_contracts.py tests/test_config_schema.py tests/test_strategies.py tests/test_paid_purchases.py tests/test_steam_price_history.py tests/test_steam_reference_price_expiry.py tests/test_windows_notifications.py`：112 passed；`node --test tests/test_console_reports.cjs`：3 passed；`web/js/` 全部脚本语法检查及本次文档 `git diff --check` 通过。测试临时目录已清理。本次只修改 README 和本状态文档，未安装依赖、未重启服务、未执行真实登录/支付/上架；未重新运行完整 pytest。

- 2026-11-09 付款终端提示修复：新增 5 个回归用例，先复现失败后修复；`tests/test_windows_notifications.py tests/test_payment_confirmation_state.py tests/test_buff_purchase_flow_safety.py tests/test_buff_checkout_integration.py tests/test_frontend_contracts.py`：64 passed。覆盖实际状态对象与网页确认路由唤醒等待、清理输入监听、取消/超时、停止及异常提示；相关 `git diff --check` 通过。未调用真实支付接口、未重启服务。

- 本次历史归一化修复：`tests/test_steam_price_history.py tests/test_error_diagnostics.py tests/test_steam_history_currency_symbol.py tests/test_steam_history_account_pool.py tests/test_steam_reference_price_expiry.py tests/test_currency_conversion.py`：104 passed。新增用例先复现 5 项失败，修复后通过；覆盖旧缺币种/无汇率点隔离、原始记录保留、6 小时后清空内存仍复用数据库、最新未知币种和无可用价格仍拒绝使用。
- 实际 `config/steam_market_history.db` 使用 SQLite `mode=ro` 验证：MAG-7 共 2896 点，修复后的读取函数返回 2886 个人民币价格点，跳过 10 个未换算点；10 个原始行与抓取时间均保持不变。读取使用已存响应时间后 1 秒验证，检查时该响应仍在 12 小时有效期内；未触发 Steam 请求。
- 本次相关代码 `git diff --check` 通过；以下为此前阶段验证记录。
- `tests/test_frontend_contracts.py tests/test_static_routes.py`：6 passed。
- `tests/test_steam_price_history.py`：11 passed；覆盖默认缓存 12 小时内复用、超过 12 小时才重新请求。
- `tests/test_steam_history.py tests/test_steam_price_history.py tests/test_steam_reference_price_expiry.py tests/test_market_orders.py`：56 passed。
- 相关 JavaScript `node --check`：通过。
- `tests/test_steam_history_currency_symbol.py tests/test_steam_history_account_pool.py tests/test_steam_price_history.py tests/test_exchange_rate_worker.py`：41 passed。
- 2026-11-04 完整 pytest：`619 passed`，使用当时的项目环境和项目内临时目录。覆盖默认历史 TTL 边界、内存/SQLite 复用、更新后价格和分析共享快照、低/高成本候选均先准备数据。
- `node --test tests/test_console_reports.cjs`：3 passed，验证分层筛选、展开内容转义、单件关联和轮询字段保留；前端契约包含于完整 pytest。
- Edge 无头浏览器使用本地页面代码和模拟数据验证：饰品报告展开、成本率公式、关联网络报告跳转、420px 窄屏无横向溢出均通过；未连接运行中的应用或交易接口。
- 本次前端 JavaScript 语法检查和 `git diff --check` 通过。
- 本次未重新执行真实 Steam 在线请求；结论依据现有历史诊断日志、数据库响应记录和本地测试。

完整测试使用项目内临时目录运行，避免当前沙箱默认临时目录不可写：

```powershell
$base = Join-Path (Get-Location) '.pytest-run-tmp'
& 'D:\software\miniconda3\envs\steamswap\python.exe' -m pytest -q --basetemp $base
if (Test-Path $base) { Remove-Item -LiteralPath $base -Recurse -Force }
```

## 当前待处理事项

- 用户确认沿用远程仓库名称 `Despr0A/SteamSwap` 并解除 fork 关系。远端 fork 解除尚未执行，本次代码上传不改变 GitHub fork 关系；本地仅配置自己的 `origin`，没有需要移除的上游远程。

- Fluent UI 未连接真实运行服务验证付款、账号登录、策略保存/启用或交易结果；浏览器检查仅使用模拟响应。没有重启服务、修改账号配置或触发交易。已有界面缩放设置继续生效，截图使用 100% 缩放；现有配置为 70% 时界面会相应缩小。

- README 核验发现首次上架触发缺口：`receive_worker` 收货成功后仅记录日志，`try_receive_once` 更新购买记录但未调用出售入口；当前实际调用 `run_sell_phase_on_inventory_update` 的后台路径是在售检查确认售出后的库存刷新与补挂。文档已明确不能承诺首次收货自动上架，本次未修改交易逻辑。
- 页面“导出配置”未包含自定义策略、用户模块、行情/参考价数据库、浏览器资料及对账/请求限制文件；README 已说明范围，完整迁移需另外保留运行数据。仓库未提供 Dockerfile/Compose 或独立 LICENSE 文件，本次未补建。

- 付款终端提示修改需重启程序后生效；当前运行进程未重启，真实终端与支付页面联动未实测。用户已确认问题发生在 SteamSwap 网页点击“已完成支付”之后，本次按此范围修复。
- 运行中的程序需要重启才能加载本次历史读取修复；本次未重启服务，重启后的实际流水线表现未验证。
- 本次参考价流程和报告修改尚未重启运行中的程序；真实线上候选通过率和网络报告样例未验证。
- 未验证当前网络环境下 Steam 最新接口是否持续返回相同的币种字段；本地历史诊断已确认曾返回 `price_prefix=¥`、`currency=null`。
- 出售上架链路仍保留实时钱包币种检查，与买入历史归一化路径分开。

## 工作区状态

本轮提交范围包括项目标识与环境命名、README、Fluent 工作台、账号地区同步、历史行情缓存与货币归一化、分层报告、付款终端提示及相关测试。提交与推送结果以 Git 记录及远端分支为准；后续 Codex 必须先查看 `git status --short` 和目标文件 diff，再决定是否修改。

## 后续入口

1. 如需确认线上响应漂移，采集最新 `HISTORY_CURRENCY_UNSUPPORTED` 诊断中的 `currency` 与 `currency_symbol`。
2. 前端或后端路由改动后，优先运行前端契约测试，确认按钮绑定和 API 路径仍然匹配。
3. 每次完成一个可验证阶段，更新本文件的验证记录和待处理项。
