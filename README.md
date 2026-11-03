# 🎬 MoviePilot 实用插件合集 (MoviePilot-Plugins)

欢迎使用 **MoviePilot Plugins**！这是一个为 MoviePilot 打造的强大且实用的插件扩展包。本合集包含多个自动化运维、站点辅助和系统工具，旨在帮助您更高效、更稳定地管理您的媒体库和 PT 站点。

---

## 📦 包含插件及功能说明

### 1. 🖼️ 本地图片缓存代理 (ImageLocalCache)
- **简介**：将 TMDB 图片自动缓存到本地服务器，并通过插件 API 提供快速访问。
- **特色**：自动拦截并替换所有 TMDB 图片 URL 为本地缓存地址。首次访问时自动从 TMDB 下载缓存，后续直接从本地文件秒速返回，完美解决外网（如企业微信等）加载图片慢或失败的问题。
- **图标**：<img src="plugins.v2/imagelocalcache/icon.png" width="30">

### 2. 🏥 MP插件健康检测 (MpPluginHealthCheck)
- **简介**：全天候插件管家，定时检测已安装插件的状态变化。
- **特色**：支持定时巡检（默认每天 09:10），当检测到插件**丢失、停用、升级或新增**时，会自动发送详细的变更报告给您，让您对系统状态了如指掌。

### 3. 💰 财神PT喊话 (CaishenPTShout)
- **简介**：自动在财神 PT 站点群聊区发送喊话消息的交互助手。
- **特色**：定时自动读取站点 Cookie 并发送如「财神，求上传」等自定义喊话内容。内置容错机制，实时记录喊话成功与否的状态，并在详情页生成直观的历史统计。
- **图标**：<img src="plugins.v2/caishenptshout/caishen.png" width="50">

### 4. 🍪 青龙Cookie检测 (QlCookieCheck)
- **简介**：监控青龙面板中京东等 Cookie 环境状态的守护插件。
- **特色**：定时（默认每天 10:00）连接青龙面板 API，遍历检测 `JD_COOKIE` 的有效性。一旦发现 Cookie 失效或过期，会立即推送警报，避免错过任何收益。

### 5. ✍️ 站点自动签到 (AutoSignIn)
- **简介**：强大且兼容性极高的 PT 站点自动签到/模拟登录工具。
- **特色**：
  - 支持几十个主流站点的自动签到与模拟登录（包括 52pt, chdbits, m-team, u2 等）。
  - 内置 OCR 验证码识别和 Cloudflare 盾优选策略（配合特定插件使用）。
  - 详情页提供美观的矩阵式签到状态面板，近 7 天签到状态一目了然。

---

## 🚀 安装与使用

1. 下载本项目源码。
2. 将 `plugins.v2` 目录下的所需插件文件夹（例如 `autosignin`）复制到 MoviePilot 配置目录下的 `plugins` 文件夹中。
3. 重启 MoviePilot 服务。
4. 在 MoviePilot WebUI 的「设置」-「插件」中找到对应的插件，点击开启并进行相关配置即可。

> 👨‍💻 **Author**: wenzhanquan


## 媒体服务器通知本地版（MediaServerMsgLocal）

当前版本 **1.0.31**，配合 **ImageLocalCache 1.1.6** 使用。支持 Emby 单集事件和
`S01 E25, E29`、`S01 E26-E27` 等汇总事件；按真实季集去重计数，连续通知在最后一条之后等待配置的聚合时间再发送。

使用前启用本地图片缓存代理，并配置消息客户端可访问的对外域名；如同时启用魔改版，会收到两份通知。
通知本地版的图标使用 MoviePilot 内置资源，不再依赖外部 CDN。

图片代理索引 ID 已统一为 `ImageLocalCache`，与插件类名一致。已有安装如果仍包含小写
`imagelocalcache` 登记，应保留正确的 `ImageLocalCache`，清理重复登记，避免启动时反复安装。

详细说明：

- [媒体服务器通知本地版](plugins.v2/mediaservermsglocal/README.md)
- [本地图片缓存代理](plugins.v2/imagelocalcache/README.md)

### 回归测试

安装 pytest 后，在仓库根目录运行：

```sh
python -m pytest tests/test_mediaservermsglocal.py tests/test_imagelocalcache.py
```

测试隔离 MoviePilot 的数据库、事件注册和外部消息渠道，不访问真实媒体服务器或发送通知。


## 本地插件稳定快照

已集中归档 33 个本地插件，包含当前版本的源码、静态资源及随插件附带的依赖文件。
详见[版本清单与更新说明](LOCAL_PLUGIN_SNAPSHOT.md)及[机器可读清单](PLUGIN_SNAPSHOT.json)。
保持自动升级关闭，按插件逐个更新；集中镜像不代表未来兼容性保证。

## 2026-11-03 修复备份

MediaServerMsgLocal 1.0.33、ImageLocalCache 1.1.7、MpPluginHealthCheck 1.0.2。可选订阅整包记录和 Telegram TLS 核心修复见 [恢复说明](recovery/README.md)。插件安装不会自动应用核心补丁。
