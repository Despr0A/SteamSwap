# SteamSwap 项目开发入口

本文件是本项目专属的 Codex/开发代理说明。每次进入项目先阅读本文件和 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)，再检查 `git status --short` 与相关 `git diff`；不要覆盖、回滚或整理用户已有的未提交修改。

## 项目与环境

- 工作目录：`D:\13299\Documents\py_CODE\SteamSwap`
- 操作系统：Windows 11，默认 shell 为 PowerShell 7
- 推荐 Python 环境：`D:\software\miniconda3\envs\steamswap`
- 推荐解释器：`D:\software\miniconda3\envs\steamswap\python.exe`
- 环境已完成重命名，可通过 `conda activate steamswap` 激活。
- 当前已验证：Python 3.11.15、pytest 9.1.1
- 项目入口：`python run.py`

不要因为缺少依赖而创建新环境或私有依赖目录。需要安装依赖时，先说明原因并取得用户明确授权；优先使用已有的 `steamswap` 环境。网络安装优先使用 `https://pypi.tuna.tsinghua.edu.cn/simple`。

## Windows 操作约定

- 使用 PowerShell 语法，不使用 Bash heredoc、Bash 引号或 Bash 文件操作习惯。
- 搜索代码优先使用 `rg`；PowerShell 中不要把带通配符的目录直接传给 `rg`。
- 修改前先读取目标文件和当前 diff，只做与任务直接相关的最小改动。
- 测试临时目录优先放在项目可写目录，例如 `.pytest-run-tmp`，测试结束后清理。

## 验证命令

在当前受限环境中，pytest 默认临时目录可能不可写，使用项目内 basetemp：

```powershell
$base = Join-Path (Get-Location) '.pytest-run-tmp'
& 'D:\software\miniconda3\envs\steamswap\python.exe' -m pytest -q --basetemp $base
if (Test-Path $base) { Remove-Item -LiteralPath $base -Recurse -Force }
```

前端脚本语法检查使用：

```powershell
node --check web/js/main.js
node --check web/js/accounts.js
```

完成修改后更新 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)，准确记录已完成、待处理、阻塞和未验证事项。不要把静态检查、模拟请求或模型推断写成真实运行验证。

## 前端与后端契约

- HTML 交互控件必须有对应 JavaScript 引用和事件绑定；动态渲染的控件必须在渲染后绑定。
- 前端 `API + ...` 调用的路径必须能在 `app/` 路由中找到；相关回归检查位于 `tests/test_frontend_contracts.py`。
- 后端路由或响应字段发生变化时，先同步检查前端调用、错误处理和按钮状态，再运行前端契约测试与相关后端测试。
- 账号管理按钮与搜索框的绑定集中在 `web/js/main.js`，账号业务实现位于 `web/js/accounts.js`。

## 当前工作区纪律

工作区可能同时包含用户进行中的业务修改。提交、重构或修复前先区分本次任务改动与既有改动；除非用户明确要求，不执行全局格式化、批量重命名、回滚或清理无关文件。
