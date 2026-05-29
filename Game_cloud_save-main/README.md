# Games Cloud Save

`Games Cloud Save` 是一个基于 `PyQt5` 的多游戏本地存档同步工具。

它的目标是把“不同游戏、不同电脑上的本地存档”统一备份到 GitHub，并提供简单明确的上传、下载流程。

## 当前版本特点

- 支持多游戏独立配置
- 每个游戏独立设置本地存档目录
- 每个游戏独立设置云端备份路径
- 下载覆盖前可先备份本地存档
- 支持覆盖后确认或回退
- 使用“最近一次上传记录”作为对比

## 运行环境

- Windows
- 建议 Python 3.11 及以上
- 依赖见 `requirements.txt`

安装依赖：

```bash
pip install -r requirements.txt
```

启动方式：

```bash
python main.py
```

或者：

```bash
python github_save_sync_gui.py
```

## 首次使用

1. 填写 GitHub Token
2. 填写仓库名，格式为 `用户名/仓库名`
3. 填写分支名，通常是 `main`
4. 新增一个游戏
5. 选择该游戏的本地存档文件夹
6. 为该游戏填写独立的云端路径

示例：

- `save_sync/Games/save_backup_latest.zip`

## 配置文件

程序运行后会在当前目录生成：

- `games_cloud_save_config.json`

这个文件包含：

- GitHub 仓库信息
- 本地游戏路径
- 设备名
- 最近上传记录
- GitHub Token

## 使用建议

- 每个游戏使用独立的 `remote_zip_path`
- 不同游戏不要共用同一个云端 zip 路径
- 上传前确认当前选择的是正确的存档文件夹
