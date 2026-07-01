# 存档目录检测规则

本文档整理当前项目中的存档目录检测逻辑，方便后续维护、调参和排查误判。规则实现主要在 `save_dir_detector.py`，入口由主程序的“检测存档目录”按钮调用。

## 总体目标

检测功能只做“候选推荐”，不会自动写入配置。用户必须在候选列表中确认目录，点击“使用选中目录”后才会把目录写入当前游戏配置。

核心原则：

- 优先推荐路径中包含当前游戏关键词的目录。
- 优先推荐真正存放存档文件的最小有效目录。
- 如果多个同级账号 ID 或存档槽位目录都有证据，推荐它们的父目录，避免只备份一个账号或一个槽位。
- 检测结果仅供参考，用户需要打开目录确认。

## 游戏关键词提取

默认关键词来自“模拟器/游戏路径”，不再使用配置里的游戏名。

提取来源：

- EXE 文件名，例如 `HogwartsLegacy.exe` 提取 `hogwartslegacy`。
- 从 EXE 所在目录向上找第一个有意义的父目录名，例如 `Red Dead Redemption 2` 提取 `reddeadredemption2`。

过滤规则：

- 过滤通用词：`game`、`save`、`launcher`、`steam`、`steamapps`、`common`、`win64`、`setup`、`repack` 等。
- 过滤模拟器程序名：`cemu`、`ryujinx`、`yuzu`、`pcsx2`、`rpcs3`、`ppsspp`、`dolphin`、`retroarch` 等。
- 对英文和数字会做紧凑匹配，例如 `Red Dead Redemption 2` 会保留 `reddeadredemption2`，不会再拆成 `red`、`dead` 这类弱关键词。
- 路径匹配时同时支持普通文本和去掉空格、横线、下划线后的紧凑文本。

用户在检测窗口里可以手动新增、删除或清空关键词。关键词列表为空时，不再强制要求候选路径包含游戏关键词。

## 未启动检测流程

未启动检测指用户只填写了模拟器/游戏路径，尚未启动游戏保存一次时的规则推荐。

候选来源分三类：

- 规则参考：根据模拟器或普通 PC 游戏常见位置生成参考目录。
- 游戏目录搜索：从 EXE 所在目录及其上级目录附近搜索。
- 全局常见位置搜索：在用户目录和常见游戏目录下搜索包含游戏关键词的目录。

常见搜索根目录包括：

- `Documents`
- `Saved Games`
- `%LOCALAPPDATA%`
- `%APPDATA%`
- `%LOCALAPPDATA%\..\LocalLow`
- 各磁盘下的 `Games`
- 各磁盘下的 `SteamLibrary`
- 各磁盘下的 `steamapps\common`
- 各磁盘下的 `Rockstar Games`
- 各磁盘下的 `GOG Games`
- 各磁盘下的 `Epic Games`
- 各磁盘下的 `Program Files`
- 各磁盘下的 `Program Files (x86)`

普通 PC 游戏常见参考目录：

- `Documents\My Games`
- `Saved Games`
- `Documents`
- `%LOCALAPPDATA%`
- `%LOCALAPPDATA%\..\LocalLow`
- `%APPDATA%`
- 游戏安装目录

模拟器参考目录：

- Cemu：`mlc01\usr\save`、`mlc\usr\save`
- Ryujinx：`bis\user\save`
- Yuzu / Suyu：`nand\user\save`
- PCSX2：`memcards`、`sstates`
- RPCS3：`dev_hdd0\home\...\savedata`
- PPSSPP：`PSP\SAVEDATA`
- Dolphin：`GC`、`Wii\title`
- RetroArch：`saves`、`states`

## 存档特征

强存档扩展名：

- `.sav`
- `.save`
- `.slot`
- `.state`
- `.srm`
- `.dsv`
- `.rpgsave`
- `.rvdata`
- `.rvdata2`
- `.rxdata`

弱存档扩展名：

- `.dat`
- `.bin`
- `.db`

弱扩展名不会单独作为强证据。只有文件名或路径同时像存档时，才提高可信度。

特征文件名：

- `user.dat`
- `account.dat`
- `sav.dat`
- `save.dat`
- `game_data`
- `gamedata`
- `progress`
- `checkpoint`

为了避免误判，`gamedata`、`game_data`、`progress`、`checkpoint` 这类词只在弱扩展名文件中按存档特征处理，不会让 `.archive` 等游戏资源包直接变成存档候选。

## 存档目录名评分

候选目录的末端名称会影响排序。

较高权重：

- `save`
- `saves`
- `savedata`
- `savegame`
- `savegames`
- `gamesaves`

中等权重：

- `profile`
- `profiles`
- `playerprofile`
- `playerprofiles`
- `userdata`
- `user_data`
- `steam_settings`

较低权重：

- `remote`
- `slot`
- `slots`
- `checkpoint`
- `checkpoints`
- `autosave`
- `autosaves`

低权重且容易误判：

- `settings`
- `config`

`profile` 如果没有命中游戏关键词，会主动降权；`settings/config` 如果没有命中游戏关键词，不加分。

## ID 和槽位目录规则

ID 目录识别：

- 纯数字且长度不少于 4，例如 `1278235987`。
- 十六进制字符串且长度不少于 8，例如 `1B315D22`。

槽位目录识别：

- ID 目录。
- 目录名像 `save`、`autosave`、`slot`、`checkpoint` 等存档目录。
- 目录名符合 `AutoSave-0`、`ManualSave1`、`QuickSave2` 这类模式。

推荐规则：

- 如果某个目录下有多个同级 ID 或槽位子目录，并且这些子目录包含存档特征文件，推荐父目录。
- 如果只有一个 ID 或槽位子目录包含存档特征文件，并且父目录像存档目录，推荐这个子目录。
- 如果候选目录本身直接包含强存档文件，优先推荐该目录。

示例：

- `...\Hogwarts Legacy\Saved\SaveGames\1278235987\HL-00-11.sav` 推荐 `1278235987`。
- `...\Cyberpunk 2077\AutoSave-0\sav.dat` 和 `...\Cyberpunk 2077\AutoSave-1\sav.dat` 同时存在时，推荐 `Cyberpunk 2077`。
- `...\Red Dead Redemption 2\Profiles\1B315D22\SRDR30015` 推荐 `1B315D22`。

## 变化检测流程

变化检测用于用户不确定存档位置时。

界面提示流程：

1. 先扫描启动前文件状态。
2. 启动游戏。
3. 用户手动保存一次并关闭游戏。
4. 再扫描变化文件。
5. 将最像存档目录的候选排在前面。

扫描范围来自当前配置上下文：

- 当前游戏目录。
- 当前模拟器/游戏 EXE 所在目录。
- `Documents`
- `Saved Games`
- `%LOCALAPPDATA%`
- `%LOCALAPPDATA%\..\LocalLow`
- `%APPDATA%`

变化检测评分依据：

- 文件发生新增或修改。
- 修改文件包含强存档扩展名。
- 修改文件包含弱存档扩展名，并且目录命中游戏关键词或存档目录特征。
- 修改文件数量合理时加分，文件过多时降权。
- 文件名包含 `save`、`profile`、`slot` 时加分。
- 文件名包含 `config`、`setting` 时降权。
- 相对扫描根目录层级过深时降权。
- 变化发生在 ID 或槽位目录内时，套用 ID/槽位父目录推荐规则。

变化检测和未启动检测会合并候选，同一路径只保留分数更高的一项。

## 指定位置扫描和全盘扫描

检测窗口支持两种补充扫描：

- 选择位置扫描：用户手动选择一个目录或磁盘后扫描。
- 全盘扫描：扫描所有固定磁盘。

全盘扫描可能较慢，必须由用户主动触发，并会显示进度。扫描时会先找包含游戏关键词的目录，再在这些目录下继续寻找存档特征目录或存档特征文件。

默认不会主动进行全盘扫描。

## 过滤和降权

这些目录会跳过或降权：

- `cache`
- `shader`
- `logs`
- `log`
- `temp`
- `tmp`
- `crash`
- `dump`
- `screenshots`
- `screenshot`
- `video`
- `videos`
- `backup`
- `assetcache`
- `persistentdownloaddir`
- `.git`
- `__pycache__`
- `node_modules`
- `packages`
- `windows`

如果候选没有命中游戏关键词，参考候选最高分会封顶，避免 Adobe、DaVinci、浏览器配置等无关 `Profile` 目录排到前面。

## 评分优先级总结

总体优先级从高到低：

1. 路径命中游戏关键词。
2. ID 或槽位目录中存在强存档文件。
3. 多个同级 ID 或槽位目录存在时推荐父目录。
4. 末端目录名是 `save/saves/savedata/savegames`。
5. 位于常见存档根目录，例如 `Saved Games`、`Documents`、`AppData\Local`。
6. 变化检测中实际发生了新增或修改。
7. 文件数量像存档目录。
8. 目录过深、缓存日志、截图视频、配置目录等降权。

## 当前已验证示例

以下是当前规则在本机测试过的结果：

- `Hogwarts Legacy`：推荐 `%LOCALAPPDATA%\Hogwarts Legacy\Saved\SaveGames\1278235987`，判断合理。
- `Red Dead Redemption 2`：推荐 `Documents\Rockstar Games\Red Dead Redemption 2\Profiles\1B315D22`，判断合理。
- `Cyberpunk 2077`：推荐 `Saved Games\CD Projekt Red\Cyberpunk 2077`，判断合理。

## 维护建议

新增规则时优先修改权重和通用特征，不要硬编码某个具体用户或某个具体游戏。

需要调整时优先考虑：

- 是否会误伤游戏资源文件。
- 是否会把工具软件的 `Profile`、`Settings`、`Config` 排到前面。
- 是否会只推荐单个槽位，导致其他槽位没被备份。
- 是否会因为全盘扫描过深导致卡顿。

如果某个游戏规则不准，优先通过“关键词、存档特征文件、目录末端名称、槽位父目录规则”修正，而不是写特定游戏路径。
