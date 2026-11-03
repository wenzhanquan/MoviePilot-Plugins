# 可选核心修复备份

这两个修复不属于插件安装内容，不会因为安装插件而自动生效。未包含任何运行配置、凭据或数据库。

## 订阅整包集数记录

`subscribe-selected-episodes.patch` 只修改已下载集数记录：优先使用下载任务的 `selected_episodes`，仅其为 None 时退回识别元数据，防止整包尚未入库时漏记集数、重复下载单集。基于当时 NAS MoviePilot v2.15.6 文件制作；升级后先检查对应逻辑是否已修复，勿覆盖整个新版核心文件。

在 MoviePilot 源码根目录先执行 `git apply --check subscribe-selected-episodes.patch`，通过后备份目标文件，再应用。`subscribe_note_fix.py` 是该方法的独立回归测试副本，不是独立运行脚本。

## Telegram TLS 兼容

`telegram/compat.py` 保留原 header formatter 兼容，并针对 Telegram 官方 API 在每线程独立 Requests 会话上使用校验证书的 Python 标准 TLS 上下文，解决该镜像 HTTP 库默认配置访问 Telegram 失败的问题。

恢复前备份 `/app/app/modules/telegram/compat.py`；如适用于当前版本，可将本文件存入 NAS 持久化目录，再以只读卷映射到该路径，重启验证。订阅核心补丁也需保存到持久目录并按对应路径挂载，容器内部直接修改不能抵御重建。

镜像更新后检查接口与依赖兼容性。账号绑定、订阅归属、网盘 Cookie、外网地址和代理配置应从各自的私有备份恢复，均不在本仓库。
