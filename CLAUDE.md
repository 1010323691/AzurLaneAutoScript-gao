# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本文基于对源码的实际阅读整理。标注「未验证」的内容是推断，修改前请先核对源码。
> 路径都相对于仓库根目录；行号可能随上游更新漂移，以符号名搜索为准。

---

## 0. 本仓库（fork）特有约定 —— 先读

这是 `1010323691/AzurLaneAutoScript-gao`，上游 `LmeSzinc/AzurLaneAutoScript` 的 fork。

- **git remote**：`origin` 指向 fork，`upstream` 指向上游。同步上游的做法：`git pull upstream master`，解决冲突，再 `git push origin master`。
- **自动更新已关闭**：`config/deploy.yaml` 里 `AutoUpdate: false`、`CheckUpdateInterval: 0`、`AutoRestartTime: null`，`Repository` 指向 fork。这个文件被 `.gitignore` 忽略，不会进提交。
  - 原因：更新流程会执行 `git reset --hard origin/<branch>`（`deploy/git.py` 的 `git_repository_init`，由 `git_install` 调用）。只要打开 `Alas.exe` 或触发 GUI 更新，未提交的本地改动就会被**直接抹掉**，本仓库已经因此丢过一次改动。
  - **规则**：改动要尽快提交；在提交前不要让用户重启 `Alas.exe`。
- **fork 新增功能**（上游没有；同步上游时要注意冲突）：
  1. **卡死处理** `Restart.StuckHandle`（重启设置页）。`Mode: restart | skip_task`，`SkipDelay: 1h | 3h | 6h | server_update`。
     - 实现：`alas.py` 的 `run()` / `stuck_skip_task()` / `stuck_skip_cleanup()`，异常类型 `module/exception.py` 的 `TaskRequirementNotMet`。
  2. **舰队职能新增选项** `Fleet.FleetOrder = fleet1_mob_fleet2_boss_auto_swap`：每次出击前，按心情自动互换道中队和 Boss 队。
     - 伪值在 `AzurLaneConfig.bind()` 里解析为具体职能，同时设置 `config.FLEET_ORDER_AUTO_SWAP=True`。
     - 互换逻辑在 `module/combat/emotion.py` 的 `Emotion.check_reduce()`。
     - 全清模式（`MAP_CLEAR_ALL_THIS_TIME`，刷三星时）要打完地图上全部敌人，但这个标志在进图时的 `map_get_info()` 才确定，晚于出击前的心情检查。所以 `enter_map` 在点关卡准备之前调用 `CampaignBase.handle_clear_all_emotion()`，按 `_map_battle_clear_all`（`spawn_data` 里全部敌人数，`FLEET_BOSS = 1` 时再加 1）用 `Emotion.check_swap()`（内部调 `Emotion.check_reduce(delay=False)`，避开 `GemsEmotion` 的覆盖）重新判断换队；两种职能都不够时保持原职能，不推迟任务，进图后照常等心情。
  3. **自律设置侧边栏兼容 2 个标签**：没解锁指挥喵的账号，侧边栏只有「编队 / 自律设置」两个标签，见 `module/handler/auto_search.py` 的 `fleet_preparation_sidebar_ensure()`。
  4. 地图内等待心情改用 `fleet_show_index`（`module/map/map.py`）：心情是按出击槽位记录的，反转职能时 `fleet_current_index` 是角色编号，会查错舰队。
  5. **委托条件不满足时跳过**（`module/commission/commission.py`）：点过「推荐」后，限制条件行如果仍有红色 ✗（`_commission_requirement_unmet`），抛出 `CommissionRequirementNotMet`，设置 `comm_requirement_unmet`，然后**停止派遣其余委托**：这时意味着已经没有合格的空闲舰船，换别的委托也一样派不出去。任务结束后，`run()` 把下次运行推迟到最早一个进行中委托完成的时间，也就是舰船回来时；如果没有进行中的委托，就用 `FailureInterval`。红色检测目前只在日服截图上验证过。
  6. **`task_delay` 按上限截断**：`AzurLaneConfig.next_run_limit()` 集中定义每个任务 NextRun 的上限（委托、收获 12 小时，科研 24 小时，部分大世界任务 7 天或 31 天，其余任务 24 小时）。`override()` 和 `task_delay()` 共用这份定义。
  7. **出击战败后撤退并延迟重试**：上游的地图战只认 S/A/B 结算，战败（C/D）结算会一直等到 `GameStuckError`；在跳过模式下，主线会因此被推迟到次日。
     - 现在 `Combat.handle_exp_info_defeat()` 点掉 `EXP_INFO_C/D`，并设置 `combat_defeated`。前提是本场先出现过 `BATTLE_STATUS_C/D`（`battle_status_defeated`）：`EXP_INFO_C` 只是颜色检查，曾在 S 胜利后的获得新船画面上误判，并屏蔽了 `handle_get_ship`。之后画面回到地图或关卡页时，`combat_status` 抛出 `CampaignDefeat`（`CampaignEnd` 的子类）。`expected_end` 不在 `MAP_EXPECTED_END` 里的地图外战斗（raid、医院、每日用回调函数，公会 Boss 用 `in_ui`）不抛，仍按各自的结束条件退出。
     - `CampaignBase.run` 捕获后调用 `handle_campaign_defeat()`：还在地图里就撤退，然后 `task_delay(minute=DEFEAT_RETRY_MINUTE)`（60 分钟），发送通知，再 `task_stop()`。
     - 自律寻敌模式：游戏不等点击 D 结算，直接停在「DEFEAT / 艦隊戦力を強化しよう！」提示页（素材 `DEFEAT_TIPS`：锚图标模板匹配，点底部「戻る」）。`auto_search_combat_execute` 遇到 C/D 结算或提示页就结束等待；`auto_search_combat_status` 由 `handle_combat_defeat_tips()` 点掉提示页并设置 `combat_defeated`，之后回到自律中、自律结算菜单、地图或关卡页时抛 `CampaignDefeat`。`handle_campaign_defeat()` 先 `ensure_auto_search_exit()` 关掉自律结算菜单再撤退。普通战斗的 `combat_execute` / `combat_status` 也会点掉这个提示页。点「戻る」之后回到哪个画面还没在真机上确认过。大世界有自己的 C/D 处理，不受影响。D 素材和提示页素材只用日服截图验证过。
  8. **低心情弹窗时撤退并推迟**：ALAS 只推算心情；主线、活动等任务各记各的心情，和手动游玩一起用同一批舰队时，推算值会比实际高。游戏弹出低心情确认框（实际心情 30 以下）时，上游只在 `Emotion.Mode` 含 `ignore` 时点确定，否则一直等到 `GameStuckError`，跳过模式下任务会被推迟到次日。
     - 现在 `CampaignBase.handle_combat_low_emotion()` 点「取消」，再由 `handle_low_emotion_withdraw()` 撤退（关卡准备界面则退出准备）。然后把相关舰队的心情记录校正为 `LOW_EMOTION_POPUP_VALUE`（30）：地图内只校正 `fleet_show_index` 那一队，进图时两队都校正。最后用 `check_reduce` 推迟到心情恢复，抛 `ScriptEnd`。
     - 只在 `CampaignBase.run()` 里生效（`low_emotion_withdraw`）：联动活动、daemon 也继承 `CampaignBase`，但不捕获 `ScriptEnd`，保持原行为。心情控制为「防止红脸」（下限低于 30）时改为点确定继续出击：地图内只看 `fleet_show_index` 那一队的设置，进图时（不知道是哪一队）要两队都是「防止红脸」。`GemsFarming` 有自己的覆盖，不受影响。
- **用户环境**：Windows 平台，日服（`com.YoStarJP.AzurLane`），雷电模拟器 `emulator-5554`，低等级账号。

---

## 1. 项目概览

- **用途**：Alas（AzurLaneAutoScript）是碧蓝航线的 7x24 自动化脚本，通过 adb 控制安卓模拟器，用截图加图像识别驱动操作。支持国服、国际服、日服、台服（cn / en / jp / tw）。
- **核心功能**：
  - 主线和活动推图、开荒（`StopCondition.StageIncrease`）
  - 收菜：委托、科研、后宅、指挥喵、大舰队、商店、船坞等
  - 每日任务、困难图、演习
  - 大世界（Operation Siren）
  - 岛屿计划
  - 统一的任务调度器（按完成时间自动排期）
- **技术栈**：
  - Python **3.7**（依赖锁死在 3.7：mxnet 1.6、numpy 1.16、cnocr 1.2）
  - OpenCV、adbutils / uiautomator2、cnocr + MXNet（OCR）
  - pywebio + uvicorn（Web GUI）、Electron 壳（`webapp/`、`Alas.exe`）
- **运行方式**：
  - 一键包里的 `toolkit/` 是自带的 Python 3.7.6、Git 和 WebApp，已被 `.gitignore` 忽略。
  - `Alas.exe` 启动时依次：执行 `deploy` 安装与更新流程 → 启动 `gui.py` → 打开浏览器或 Electron 访问 `http://127.0.0.1:22267`。
- **模拟器要求**：分辨率必须是 1280x720，游戏内设置要按 README 的「修改游戏设置」表格改。比如 60 帧、剧情自动播放开、一键退役按 R > SR > N。设置不符会导致识别失败。

## 2. 常用命令

本机系统 Python 是 3.10 以上，**跑不了**本项目。所有命令都用 `./toolkit/python.exe`（Bash）或 `.\toolkit\python.exe`（PowerShell），并在仓库根目录执行。

```bash
# 启动 Web GUI（默认端口来自 deploy.yaml 的 WebuiPort=22267；--port 可临时另开一个实例做验证）
./toolkit/python.exe gui.py --host 127.0.0.1 --port 22268

# 不经 GUI，直接跑调度器（读 config/alas.json）
./toolkit/python.exe alas.py

# 改了 module/config/argument/*.yaml 或 campaign/Readme.md 之后，必须重新生成配置相关文件
./toolkit/python.exe -m module.config.config_updater

# 往 assets/<server>/<module>/ 加了图片之后，重新生成 module/<module>/assets.py
./toolkit/python.exe -m dev_tools.button_extract

# 单元测试：toolkit 里没有装 pytest；unittest 风格的测试可以直接跑
./toolkit/python.exe -m unittest tests.alas.test_stuck_handle tests.campaign.test_fleet_auto_swap tests.commission.test_commission_unmet tests.combat.test_combat_defeat tests.campaign.test_low_emotion
./toolkit/python.exe -m unittest tests.campaign.test_fleet_auto_swap.TestEmotionAutoSwap.test_swap_back

# tests/island、tests/meowfficer 等目录的测试依赖 pytest，要先在 toolkit 里安装（会修改运行环境，先征得用户同意）
./toolkit/python.exe -m pip install pytest
./toolkit/python.exe -m pytest tests/meowfficer -q

# 语法检查（仓库没有 lint 配置，也没有 CI）
./toolkit/python.exe -m py_compile <file.py>

# 通过 adb 截一张模拟器当前画面（序列号在 config/alas.json 的 Alas.Emulator.Serial）
./toolkit/Lib/site-packages/adbutils/binaries/adb.exe -s emulator-5554 exec-out screencap -p > shot.png
```

**离线调试单个模块**：很多模块文件末尾有 `if __name__ == '__main__':` 调试入口。`ModuleBase('alas', task='Main')` 会自动构造 config 和 Device，构造后可以设置 `self.image_file = '<截图路径>'`，用本地截图代替实时截图（见 `module/research/research.py` 末尾的示例）。

## 3. 目录结构（只列需要知道的）

| 路径 | 作用 |
|---|---|
| `alas.py` | 调度器主体 `AzurLaneAutoScript`：每个任务对应一个方法，异常与恢复的总入口 |
| `gui.py` | Web GUI 入口：uvicorn + pywebio，主进程 `Process` 守护重启 |
| `module/config/` | 配置系统：参数定义、代码生成、调度队列、服务器全局变量 |
| `module/device/` | 设备层：adb 连接、截图和点击方法、App 启停、卡死与重复点击检测 |
| `module/base/` | 基础设施：`ModuleBase`、`Button`、`Template`、`Timer`、`Config.when`、资源缓存 |
| `module/ui/` | 页面图（`page.py`）与导航（`ui.py` 的 `UI`） |
| `module/handler/` | 通用处理器 mixin：弹窗、剧情、登录、自律与周回开关 |
| `module/campaign/`、`module/map/`、`module/map_detection/`、`module/combat/` | 出击：战役运行、海图寻路、海图识别、战斗与心情 |
| `campaign/<folder>/*.py` | **每张地图一个脚本**（地图数据加战斗策略）。`campaign/Readme.md` 是活动表，是代码生成的输入 |
| `module/<业务>/` | 各任务的业务实现，映射见第 5 节 |
| `module/os*`、`module/campaign/os_run.py` | 大世界 |
| `assets/<cn\|en\|jp\|tw>/<module>/*.png` | 识别用的截图素材，生成到 `module/<module>/assets.py` |
| `bin/cnocr_models/` | OCR 模型（`azur_lane`、`azur_lane_jp`、`cnocr`、`jp`、`tw`） |
| `config/` | `alas.json`（用户实例配置，git 忽略）、`deploy.yaml`（部署配置，git 忽略）、`template.json`（生成的） |
| `deploy/` | 安装和更新（git、pip、adb、Electron App），启动器流程 |
| `dev_tools/` | 素材提取、地图与数据生成等开发脚本 |
| `log/` | 运行日志与错误现场（git 忽略） |
| `tests/` | 测试：多数是 pytest 风格；fork 新增的 `tests/alas`、`tests/campaign` 是 unittest 风格 |
| `submodule/`、`module/submodule/` | 外部 mod 加载（`load_mod`） |

## 4. 核心架构

### 4.1 进程与调度主循环

```
Alas.exe → deploy（git/pip 更新）→ gui.py → module/webui/app.py
   └─ 每个实例由 module/webui/process_manager.py 的 ProcessManager.run_process 拉起子进程
        └─ AzurLaneAutoScript(config_name).loop()            # alas.py
             loop: get_next_task() → self.run(inflection.underscore(task))
                   run(): self.<method>() + 全部异常处理
```

- **`get_next_task()`**（alas.py）调用 `config.get_next()`：在 `config.data` 里找所有 `Scheduler.Enable=True` 的任务，按 `NextRun` 分成 pending 和 waiting，pending 再按 `ManualConfig.SCHEDULER_PRIORITY` 排序（在 `module/config/config_manual.py`）。
  - 没有到期任务时，按 `Optimization.WhenTaskQueueEmpty` 处理：`stay_there` / `goto_main` / `close_game`，然后 `wait_until()`。
  - 等待期间每 5 秒检查一次 `config.should_reload()`：GUI 改了配置会立即重新排期。
- **任务名到方法名**：调度器用 `inflection.underscore("OpsiExplore") → "opsi_explore"` 找方法。新增任务必须同时改 `task.yaml`、alas.py 的方法，以及 `SCHEDULER_PRIORITY`。
- **每个任务结束后**，`del_cached_property(self, 'config')` 丢掉 config，下个任务重新从文件读取。所以 `config.override()` 只在单个任务内有效。
- **首个 `Restart` 会被跳过**（`is_first_task`），因为假定用户启动时游戏已经开着。
- **同一任务连续失败 3 次**：`failure_record` 计数，到 3 次就 `RequestHumanTakeover` 并退出。

### 4.2 任务在执行中对调度的控制（`module/config/config.py` 的 `AzurLaneConfig`）

| API | 作用 |
|---|---|
| `task_delay(success=, server_update=, target=, minute=, task=)` | 设置当前（或指定）任务的 `NextRun`，多个参数同时给时取最近的一个。`success` 用 `Scheduler.SuccessInterval / FailureInterval` |
| `task_call('Restart')` | 让另一个任务立即可运行，当前任务结束后按优先级执行 |
| `task_stop()` | 抛出 `TaskEnd`，`run()` 视为成功 |
| `task_switched()` / `check_task_switch()` | 长任务里检查是否有更高优先级任务到期，有则让出 |
| `override(**kw)` | 只在内存里覆盖参数（不写文件），对本任务后续读取有效，`bind()` 后仍然保留 |
| `cross_get('Task.Group.Arg')` / `cross_set` | 读写其他任务的配置（当前任务只绑定了 `General`、`Alas` 和本任务的分组） |
| `set_record(**kw)` | 同时写值和 `*Record` 时间戳（心情等使用） |
| `temporary(**kw)` / `ConfigBackup` | 临时修改配置再恢复 |

- **属性写入即落盘**：`AzurLaneConfig.__setattr__` 对已绑定参数（在 `self.bound` 里）会记入 `modified` 并 `update()` 写文件。只想临时改，就用 `override`，或者用 `super().__setattr__`。
- **`bind(task)` 的绑定顺序**：`General`、`Alas`，再加上 `OpsiGeneral` / `EventGeneral` / `TaskBalancer`（按任务名前缀决定），最后是任务本身。同名参数先绑定的生效。绑定后得到扁平属性 `Group_Arg`，比如 `Fleet_FleetOrder`、`Emotion_Fleet1Value`。

### 4.3 配置定义与代码生成

```
module/config/argument/
  task.yaml      菜单 → 任务 → 参数分组      ┐
  argument.yaml  分组 → 参数（默认值/选项） ├─ config_updater ─→ args.json、menu.json
  default.yaml   某任务的特定默认值          │                    → module/config/config_generated.py（GeneratedConfig 类属性）
  override.yaml  某任务的强制值/隐藏项       │                    → module/config/i18n/<lang>.json（新键填占位符）
  gui.yaml       GUI 文案键                 ┘                    → config/template.json、deploy 模板
campaign/Readme.md（活动表）── 生成活动下拉选项
```

- **新增或修改参数**：改 yaml，运行 `python -m module.config.config_updater`，再给 `i18n/{zh-CN,zh-TW,en-US,ja-JP}.json` 里新增的键补上文案（生成器只会填 `Group.Arg.name` 这类占位符），然后**再跑一次**生成器确认没有差异。
- **生成的文件不要手改**：`args.json`、`menu.json`、`config_generated.py`、`config/template.json`。
- **加载流程**：`ConfigUpdater.read_file()` → `config_update()` 用模板补全用户配置中缺失的键，并把不在 `option` 里的非法值重置为默认值 → `config_redirect`（旧键迁移，`redirection` 列表）。所以新增参数不需要手动迁移用户的 `alas.json`。

### 4.4 服务器（cn / en / jp / tw）

- **全局变量**：`module/config/server.py` 的 `server`。**必须** `import module.config.server as server`，不能 `from ... import server`，否则拿到的是导入时的旧值。
- **设置时机**：`module/device/connection.py` 根据包名 `Emulator.PackageName` 调 `set_server()`，`auto` 时自动检测。`config.SERVER` 是只读属性。
- **按服务器分支的三种写法**：
  1. `@Config.when(SERVER='en')`（`module/base/decorator.py`）：同名方法的多个变体，`SERVER=None` 作兜底放在**最后**。示例：`module/combat/level.py`、`module/commission/project.py`。
  2. 行内判断 `if server.server == 'jp'`。如果写在**模块顶层**，会在 import 时求值。所以 alas.py 的任务方法都在方法体内延迟 import，确保服务器已经设置好。
  3. 素材差异：在 `assets/<server>/<module>/` 放同名图片。

### 4.5 设备层（`module/device/`）

- **类组成**：`Device(Screenshot, Control, AppControl)`，连接逻辑在 `connection.py`。截图和控制方式由 `Emulator.ScreenshotMethod / ControlMethod` 选择，实现在 `module/device/method/`：adb、uiautomator2、minitouch、MaaTouch、DroidCast、nemu_ipc（MuMu）、ldopengl（雷电）、scrcpy 等。
- **卡死检测**（`device.py`）：
  - `stuck_record_check()`：同一画面等待超过 60 秒（`stuck_long_wait_list` 中的按钮为 180 秒）→ `GameStuckError`；如果此时 App 已退出 → `GameNotRunningError`。
  - `click_record_check()`：最近 15 次点击里同一按钮 ≥12 次，或两个按钮各 ≥6 次 → `GameTooManyClickError`。
  - 进入新阶段前惯例先调 `device.stuck_record_clear()` 和 `click_record_clear()`。

### 4.6 识别原语（`module/base/`）

- **`ModuleBase`**：所有业务类的基类，构造方式 `Cls(config, device)`。常用方法：
  - `appear(button, offset=, interval=, similarity=, threshold=)`：`offset` 非 0 时做模板匹配，否则做颜色比对；`interval` 是同一按钮两次触发的最小间隔。每次调用都会记录 stuck。
  - `appear_then_click`、`match_template_color`、`image_color_count`、`image_crop`
  - `wait_until_appear` / `wait_until_disappear` / `wait_until_stable`
  - `interval_clear / interval_reset`
- **状态机惯例**：`for _ in self.loop(): ...`（`loop()` 内部负责截图和超时）。返回值约定和 Pages 注释（`in:` / `out:`）是理解流程的关键。
- **`Button`**：`area` 识别区域、`color` 平均色、`button` 点击区域、`file` 文件；每个属性都是按服务器区分的 dict，运行时按 `server.server` 取值（`module/base/resource.py` 的 `parse_property`）。**`Template`**：模板匹配素材。

### 4.7 UI 导航（`module/ui/`）

- **页面图**：`page.py` 用 `Page(check_button)` 定义约 50 个页面，用 `link(button, destination)` 建立有向边。
- **导航**：`UI.ui_ensure(page)` 或 `ui_goto(page)` 先用 `ui_get_current_page()` 识别当前页面，再按 BFS 路径逐个点击。
- **无法识别当前页面时**：依次尝试 `GOTO_MAIN` / `RPG_HOME` 和 `ui_additional()`（处理各种弹窗），约 10 秒仍无法识别就抛 `GamePageUnknownError`。如果游戏服务器可用，Alas 会直接退出。
- **常见原因**：画面上盖着没处理的弹窗，或停在一个没有右上角 Home 按钮的页面。

### 4.8 出击 / 海图（主线、活动、困难等所有地图战）

```
CampaignRun(CampaignEvent)               module/campaign/run.py
  run(name, folder, mode, total):
    handle_stage_name → load_campaign(name, folder)
        importlib.import_module(f'campaign.{folder}.{name}')  # 地图脚本里的 Campaign 类
    循环：ensure_campaign_ui → triggered_stop_condition → self.campaign.run()
Campaign(CampaignBase)                   campaign/<folder>/<name>.py
CampaignBase(CampaignUI, Map, AutoSearchCombat)     module/campaign/campaign_base.py
  run(): emotion.check_reduce(_map_battle) → enter_map → map_init → battle_N()/battle_function() 循环
Map(Fleet) → Fleet(Camera, AmbushHandler) → Camera(MapOperation)
MapOperation(MysteryHandler, FleetPreparation, Retirement, FastForwardHandler)   # 进图、编队、自律与周回开关
Combat(Level, HPBalancer, Retirement, SubmarineCall, CombatAuto, CombatManual, AutoSearchHandler)
```

- **地图脚本格式**（以 `campaign/campaign_main/campaign_2_2.py` 为例）：
  - `MAP.shape` 网格尺寸
  - `MAP.map_data` 网格字符：`++` 陆地、`--` 海、`SP` 出生点、`ME` 敌人、`MB` Boss、`MM` 神秘、`MA` 弹药、`MS` 塞壬；完整含义见 `module/map_detection/grid_info.py`
  - `MAP.spawn_data`：第几战刷新什么，**Boss 出现在哪一战**，`CampaignBase._map_battle` 的预计战斗数就来自这里
  - `class Config`：`MAP_*` 开关
  - `class Campaign`：`battle_0()`、`battle_3()` 等每一战的策略，其余用 `battle_default()`
- **两种执行模式**：
  - 地图未解锁周回时：ALAS 自己识别海图、寻路、点击敌人（`map_init`、`clear_chosen_enemy`）。
  - 已解锁自律寻敌（`map_is_auto_search`）：游戏自己走图，ALAS 只处理战斗（`auto_search_combat`）。**这种模式下地图内不会再等待心情。**
- **关卡成就与开荒**：
  - 进图前在关卡准备界面读 `map_get_info()`：通关百分比、星级、安全海域。
  - `triggered_map_stop()` 满足 `StopCondition.MapAchievement` 时调 `handle_map_stop()`：开了 `StageIncrease` 就用 `campaign_name_increase()` 切到下一关，否则停用任务。
  - **`MapAchievement=non_stop` 时永远不会推进到下一关**，用户常在这里配错。
  - 推进顺序表是 `FastForwardHandler.STAGE_INCREASE`。
- **心情**（`module/combat/emotion.py`）：
  - ALAS 只做**计算**，不读取游戏里的数值：按 `Emotion.Fleet{1,2}Value / Record / Recover / Control / Oath` 推算。
  - 心情按**出击槽位**（`fleet_show_index`）记录，每战扣 2 点（用加倍书时扣 4）。
  - 进图前 `check_reduce()` 不够就 `task_delay` + `ScriptEnd`；战前 `wait()` 会 `sleep` 等待。
- **舰队索引两套编号**：`fleet_show_index` 是游戏里显示的第几舰队（等于出击槽位），`fleet_current_index` 是角色编号（1 道中、2 Boss）。反转职能时两者不同，`map_operation.fleets_reversed` 为真。

### 4.9 异常与恢复（`alas.py` 的 `run()`，必须熟悉）

| 异常 | 来源 | 默认处理 |
|---|---|---|
| `TaskEnd` | `task_stop()` | 视为成功 |
| `GameNotRunningError` | App 掉线 | `task_call('Restart')` |
| `GameStuckError` / `GameTooManyClickError` | 设备层的卡死、重复点击检测 | 保存错误现场 → 重启游戏（fork：`skip_task` 模式下推迟任务并回到主界面） |
| `GameBugError` | 游戏客户端 bug | 重启游戏 |
| `GamePageUnknownError` | `ui_get_current_page` | 检查服务器状态，可用就**退出 Alas** |
| `ScriptError` | 开发者错误 | 退出 |
| `TaskRequirementNotMet`（fork，`RequestHumanTakeover` 的子类） | 等级、进度、属性、编队不满足；`HardNotSatisfied` 是它的子类 | `skip_task` 模式：推迟到服务器刷新；否则同下一行 |
| `RequestHumanTakeover` | 配置或环境错误 | 通知后退出 |
| 其他 `Exception` | — | 保存错误现场、通知后退出 |

`ScriptEnd`、`CampaignEnd` 在战役内部捕获，不会传到 `run()`。

## 5. 功能映射（任务 → 入口）

alas.py 的每个方法都在方法体内延迟 import，执行形式是 `Cls(config=self.config, device=self.device).<method>()`。

| 任务方法 | 入口 | 文件 |
|---|---|---|
| `research` / `commission` / `tactical` / `dorm` / `meowfficer` / `guild` | `RewardResearch` / `RewardCommission` / `RewardTacticalClass` / `RewardDorm` / `RewardMeowfficer` / `RewardGuild` `.run` | `module/research/research.py`、`module/commission/commission.py`、`module/tactical/tactical_class.py`、`module/dorm/dorm.py`、`module/meowfficer/meowfficer.py`、`module/guild/guild_reward.py` |
| `reward`（收获和任务奖励） | `Reward.run` | `module/reward/reward.py` |
| `awaken` | `Awaken.run` | `module/awaken/awaken.py` |
| `shop_frequent` / `shop_once` | `RewardShop.run_frequent / run_once` | `module/shop/shop_reward.py` |
| `event_shop` / `shipyard` / `gacha` / `freebies` / `minigame` / `private_quarters` | `EventShop` / `RewardShipyard` / `RewardGacha` / `Freebies` / `Minigame` / `PrivateQuarters` | `module/shop_event/shop_event.py`、`module/shipyard/shipyard_reward.py`、`module/gacha/gacha_reward.py`、`module/freebies/freebies.py`、`module/minigame/minigame.py`、`module/private_quarters/private_quarters.py` |
| `daily` / `hard` / `exercise` / `sos` / `war_archives` / `raid_daily` | `Daily` / `CampaignHard` / `Exercise` / `CampaignSos` / `CampaignWarArchives` / `RaidDaily` | `module/daily/daily.py`、`module/hard/hard.py`、`module/exercise/exercise.py`、`module/sos/sos.py`、`module/war_archives/war_archives.py`、`module/raid/daily.py` |
| `main` / `main2` / `main3` / `event` / `event2` / `c72_mystery_farming` / `c122_medium_leveling` / `c124_large_leveling` | `CampaignRun.run(Campaign_Name, Campaign_Event, Campaign_Mode)` | `module/campaign/run.py` |
| `event_a/b/c/d`、`event_sp`、`maritime_escort` | `CampaignABCD`、`CampaignSP`、`MaritimeEscort` | `module/event/` |
| `gems_farming` | `GemsFarming.run`（自带 `GemsEmotion`，覆盖了 `check_reduce`） | `module/campaign/gems_farming.py` |
| `raid` / `hospital` / `coalition` / `coalition_sp` | `RaidRun` / `Hospital` / `Coalition` / `CoalitionSP` | `module/raid/run.py`、`module/event_hospital/hospital.py`、`module/coalition/` |
| `opsi_*`（explore、shop、voucher、daily、obscure、month_boss、abyssal、archive、stronghold、meowfficer_farming、hazard1_leveling、cross_month） | `OSCampaignRun.<同名方法>` → `OperationSiren`，它聚合了 `module/os/tasks/*.py` | `module/campaign/os_run.py`、`module/os/` |
| `opsi_ash_beacon` / `opsi_ash_assist` | `OpsiAshBeacon` / `AshBeaconAssist` | `module/os_ash/meta.py` |
| `island_*`（production、order、transport、freebie、collect、season_task、business） | `Island*` `.run` | `module/island/*.py`（`island_production_planner` 在 `module/island_handler/production_planner.py`） |
| `daemon` / `opsi_daemon` / `event_story` / `azur_lane_uncensored` / `benchmark` / `game_manager` | 工具类任务 | `module/daemon/`、`module/eventstory/eventstory.py` |
| `restart` / `start` / `goto_main` | `LoginHandler.app_restart / app_start`、`UI.ui_goto_main` | `module/handler/login.py`、`module/ui/ui.py` |

- **只有代码没有 GUI 入口的任务**：`sos`、`maritime_escort`、`c72_mystery_farming`、`c122_medium_leveling`、`c124_large_leveling` 这 5 个方法存在，但 `task.yaml` 里没有对应任务（未验证是否已弃用）。
- **通用处理器**（`module/handler/`），继承链 `InfoHandler → EnemySearchingHandler → AutoSearchHandler → FastForwardHandler`：
  - `info_handler.py`：信息条、弹窗确认与取消、剧情跳过、紧急委托、低心情、获得物品/船/皮肤
  - `login.py`：启动、登录、重启
  - `auto_search.py`：编队界面侧边栏与自律职能设置
  - `fast_forward.py`：周回、自律开关，地图信息，开荒，`STAGE_INCREASE`
  - `ambush.py`：伏击与空袭
  - `mystery.py`：神秘点
  - `strategy.py`：图内策略面板
- **大世界**：
  - `OSMap(OSFleet, Map, GlobeCamera, StrategicSearchHandler)` 在 `module/os/map.py`
  - 行动力、港口、事件处理在 `module/os_handler/`
  - 商店在 `module/os_shop/`
  - 余烬（ash）在 `module/os_ash/`
  - 地图数据 `module/os/map_data.py` 是生成文件

## 6. 素材、OCR 与生成文件

- **素材 → Button**：
  - 图片放在 `assets/cn/<module>/NAME.png`，必须是 **1280x720 全屏截图**，非识别区域透明；文件名大写。
  - 运行 `python -m dev_tools.button_extract` 生成 `module/<module>/assets.py`。该文件头写着 "Don't modify it manually"，**禁止手改**。
  - 后缀命名覆盖单个属性：`NAME.AREA.png`、`NAME.COLOR.png`、`NAME.BUTTON.png`。`TEMPLATE_*` 前缀生成 `Template`。
  - 只扫描 `assets/cn` 下已有的模块目录：**新素材必须先有 cn 版本**；其他服务器缺图时沿用 cn 的图。
- **OCR**：
  - 实现：`module/ocr/al_ocr.py` 的 `AlOcr(CnOcr)`（cnocr 1.2 / MXNet），模型在 `bin/cnocr_models/`，懒加载（`module/ocr/models.py`）。
  - 常用类（`module/ocr/ocr.py`）：`Ocr`、`Digit`、`DigitCounter`（读取「x/y」）、`Duration`，以及对应的 `*Yuv` 变体。
  - `lang='azur_lane'` 在日服会自动换成 `azur_lane_jp`。
  - 可选 OCR 服务：`deploy.yaml` 的 `UseOcrServer`，实现见 `module/ocr/rpc.py`。
- **其他生成文件**（只能用 `dev_tools/` 下的脚本从游戏 Lua 数据重新生成，不要手改）：
  - 地图脚本：`map_extractor.py`
  - `module/os/map_data.py`：`os_extract.py`
  - `module/research/project_data.py`：`research_extractor.py`
  - `module/island/data.py`：`island_extractor.py`

## 7. 问题定位

### 7.1 日志与现场

- **日志文件**：
  - 运行日志：`log/<YYYY-MM-DD>_<config_name>.txt`，比如 `log/2026-10-29_alas.txt`
  - GUI 日志：`log/<date>_gui.txt`
- **错误现场**：`log/error/<毫秒时间戳>/` 下有最近若干张截图和 `log.txt`，由 `Error.SaveError` 控制，`Error.ScreenshotLength` 设截图数量。**先看最后一张截图**，判断游戏当时处在什么画面。
- **日志里最有用的行**：
  - `Function calls:`：报错前的调用栈，由 `show_function_call` 打印
  - `[Map_info]`、`[StopCondition_MapAchievement]`、`Expect emotion reduce`、`EMOTION WAIT`
  - `Fleet_sidebar a/b`、`Pending tasks: [...]`
  - `Scheduler: Start task` / `End task`
- **实时截图**：用第 2 节的 adb 命令。模拟器停在什么画面，比日志可靠。

### 7.2 常见症状 → 原因 → 修改点

| 症状 | 常见原因 | 先看哪里 |
|---|---|---|
| `GameTooManyClickError: Too many click for a button: X` | 按钮还在，但点击无效：资源到上限、游戏改版、条件不足 | 日志的 `Function calls` 定位到循环位置。例如领任务奖励时资源超上限，游戏只弹信息条，`module/reward/reward.py` 的 `_reward_mission_claim_click` 不认识这种情况 |
| 反复点同一个关卡或页面，不进入下一步 | 点到了弹窗外，弹窗被关掉，回到上一页 | 确认坐标对应的 UI 元素真实存在。例如日服的编队侧边栏少一个标签（fork 已修） |
| 推图不推进 | `MapAchievement=non_stop`；或关卡素材、OCR 识别失败 | `module/handler/fast_forward.py` 的 `triggered_map_stop` / `handle_map_stop` |
| 自律寻敌时卡在红色 DEFEAT 提示页，任务被推迟到次日 | 战败提示页没被识别（fork 第 7 条已处理） | 错误现场截图；日志的 `Combat defeated, DEFEAT_TIPS` |
| 推图打完一章就停了，任务显示完成 | Boss 战战败（日志有 `Battle Status D`、`Combat defeated`），任务被延迟。2~6 章地图脚本写死了 `FLEET_BOSS = 1`，由道中队带伤打 Boss | 错误现场截图；`campaign/campaign_main/campaign_N_1.py` 的 `Config`；fork 第 7 条 |
| 出击任务被推迟到次日，错误现场是「コンディションが良くない」低心情确认框 | 推算心情比实际高（多个任务或手动游玩共用舰队）。fork 第 8 条后会撤退并推迟到心情恢复 | 日志的 `Low emotion popup, correct emotion`；各任务的 `Emotion.*` 与 `Fleet.Fleet1/2` |
| 出击中途长时间不动 | 心情等待（日志有 `EMOTION WAIT`），不是故障 | `module/combat/emotion.py`；用户配置 `Emotion.*` 或舰队职能 |
| `GamePageUnknownError` 后 Alas 退出 | 画面被未处理的弹窗挡住，或停在没有 Home 按钮的页面 | `module/ui/ui.py` 的 `ui_additional`、`ui_page_main_popups`；用最后一张截图补素材和处理分支 |
| `Request human takeover` 后退出 | 配置或环境问题，或同一任务连续失败 3 次 | 往前看 `logger.critical` 的提示，它通常写明了原因 |
| 某个服务器识别失败，其他服务器正常 | 缺少该服务器的素材，或 `@Config.when(SERVER=...)` 分支缺失 | `assets/<server>/<module>/`，对应模块的 `assets.py` |
| 改了配置但不生效 | 没跑 `config_updater`；值不在 `option` 里，被重置为默认值；运行中的进程仍是旧代码 | `module/config/argument/*.yaml`；重启实例或 GUI |
| 改动消失了 | 自动更新执行了 `git reset --hard` | 第 0 节；`git reflog` 可以看到 `reset: moving to origin/master` |

### 7.3 排查思路

1. 用错误现场的截图和日志里的调用栈，确定是哪个状态机循环（`for _ in self.loop()`）卡住。
2. 对照该循环的退出条件（`appear` 了哪些按钮）和截图，判断是**素材不匹配**、**未处理的新弹窗或新界面**，还是**业务条件不满足**。
3. 素材问题：补充或更新 `assets/`，重新生成；新界面：在对应循环里加 `appear` 分支或用 `handle_*` 处理；条件问题：抛出带明确日志的异常（fork 里可以用 `TaskRequirementNotMet`）。

## 8. 开发规范与验证

- **代码风格**：
  - 遵循上游风格：4 空格缩进、`snake_case`，按钮常量用大写。
  - docstring 用 `Args:` / `Returns:` / `Pages: in/out` 格式。
  - 用 `logger.info/attr/hr/warning` 记录状态，不用 `print`。
  - 优先用状态机 `for _ in self.loop()`，不要写裸 `while` 加 `sleep`。
- **兼容性**：必须兼容 **Python 3.7**（`toolkit` 是 3.7.6），不能用海象运算符 `:=`、`match` 或 3.9 以上的类型标注语法。
  - 执行含中文的 Python 脚本时，写成文件并加上 `# -*- coding: utf-8 -*-`；从 stdin 管道传入中文会报编码错误。
- **服务器差异**：只改一个服务器时，要确认其他服务器的分支（`SERVER=None`）没有受影响。
- **验证方法**（本仓库没有 CI）：
  1. `py_compile` 检查语法。
  2. 写离线单元测试：用假的 config/device 对象替身，参考 `tests/alas/test_stuck_handle.py`、`tests/campaign/test_fleet_auto_swap.py`。
  3. 配置相关改动：重跑 `config_updater` 确认无差异；另开一个 GUI 端口（`gui.py --port 22268`）查看设置页是否正常。
  4. 真机验证：在模拟器上跑对应任务，观察日志和画面。没在真机上跑过就不能说「已验证」。
- **不能直接改的东西**：用户实例配置 `config/alas.json`（GUI 可能正在写它）、所有生成文件、`toolkit/`。

## 9. 修改指南

| 想做的事 | 优先查看 |
|---|---|
| 新增一个调度任务 | `module/config/argument/task.yaml`、`argument.yaml` → `config_updater` → alas.py 加同名的 snake_case 方法 → `config_manual.py` 的 `SCHEDULER_PRIORITY` → i18n |
| 新增或修改任务参数 | `argument.yaml`（`default.yaml` / `override.yaml` 按任务覆盖）→ `config_updater` → i18n 四语言 → 代码里用 `self.config.Group_Arg` |
| 适配新活动 | `campaign/Readme.md` 加一行 → `config_updater`；地图脚本放进 `campaign/event_<date>_<server>/`（可以用 `dev_tools/map_extractor.py` 生成）；活动特殊规则写在 `module/campaign/run.py` 的 `handle_stage_name` |
| 修复某个界面识别 | 对应模块的 `assets/<server>/<module>/` → `button_extract`；页面跳转看 `module/ui/page.py` |
| 处理新弹窗 | `module/handler/info_handler.py`（通用弹窗）、`module/ui/ui.py` 的 `ui_additional`（导航时的弹窗） |
| 出击、战斗行为 | `module/campaign/campaign_base.py`、`module/map/map.py`（选敌）、`module/combat/combat.py`、`module/handler/fast_forward.py`（周回、自律、开荒） |
| 心情、舰队职能 | `module/combat/emotion.py`、`module/map/map_operation.py` 的 `fleets_reversed`、`module/config/config.py` 的 `FLEET_BOSS` 和 `bind()`、`module/handler/auto_search.py` |
| 卡死、重启、跳过策略 | alas.py 的 `run()` / `stuck_skip_task()`、`module/device/device.py` 的检测阈值、`module/exception.py` |
| 某服务器特有行为 | `@Config.when(SERVER=...)` 变体加 `SERVER=None` 兜底，或素材覆盖 |

## 10. 易踩的坑（汇总）

- 系统 Python 跑不了本项目，只能用 `toolkit/python.exe`。toolkit 里没有 pytest。
- 自动更新会 `git reset --hard`：本 fork 已关闭，但重新安装或替换 `config/deploy.yaml` 后会恢复默认的 `AutoUpdate: true`。
- `from module.config.server import server` 拿到的是旧值。模块顶层的服务器判断在 import 时就已求值。
- 给已绑定的 config 属性赋值会直接写进用户配置文件；临时值用 `override`。
- `override` 和 `FLEET_BOSS` 等派生属性只在当前任务内有效，任务结束后 config 会重新加载。
- 选项值不在 `option` 列表里时，加载会被静默重置为默认值。新增选项值必须先加到 yaml 并重新生成。
- `fleet_show_index`（槽位）和 `fleet_current_index`（角色）在反转职能时不同；心情按槽位记录。
- 自律寻敌模式下 ALAS 不控制走图，地图内不会等待心情；「继续出击」不经过编队界面，所以改了职能也不会生效。fork 在自动互换模式下禁用了继续出击。
- 编队侧边栏标签数随账号解锁进度变化：没有指挥喵时只有 2 个标签。点不存在的标签会点到弹窗外，导致弹窗被关掉。
- `GamePageUnknownError` 在服务器可用时会直接退出 Alas；处理异常后要保证画面停在可识别的页面上。
- 资源（石油、物资）到上限时，游戏只弹信息条、不发奖励，会导致「一键领取」类按钮被反复点击。
- **NextRun 上限**：每次加载配置都会执行 `config_override()`，NextRun 超过上限（委托、收获 12 小时，其余多数任务 24 小时）的任务会被**重置为现在**，也就是马上运行。上游的 `task_delay` 不截断，把委托推迟到次日服务器刷新就会形成无限循环；fork 已在 `task_delay` 里截断。直接写 `Scheduler.NextRun` 时也要遵守 `next_run_limit()`。
- 低等级账号的委托有等级、舰船数量等限制，「推荐」选不出合格的舰船时「开始」按钮是灰色的。上游代码会一直点「推荐」，直到判定为 `GameStuckError`（commission list flashing bug）。
- 截图和素材都以 1280x720 为准；游戏内画面设置（帧数、剧情自动、退役设置等）必须符合 README 的要求。
