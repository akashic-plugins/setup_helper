# Setup Helper

`/chatid`（别名 `/myid`）显示当前渠道的接收人和 Session，并生成 Wake 的 `[delivery]` 配置。将片段合并到 `<workspace>/plugin-data/wake-builtin/config.local.toml`；插件本身不修改配置或发起推送。

QQBot 仍显示 `allow_from` 提示。其配置目录按 `QQBOT_DATA_DIR`、插件 `qqbot_data_dir` 的顺序读取；缺少目录时明确提示，不猜安装路径。

命令通过当前 Command registry 注册和清理，适用于 Message runtime。测试覆盖真实 PluginManager 的候选发布、别名执行，以及生成配置被 Wake 接受。
