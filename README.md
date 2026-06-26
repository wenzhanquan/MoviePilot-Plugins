# 🎬 MoviePilot 实用插件合集 (MoviePilot-Plugins)

欢迎使用 **MoviePilot Plugins**！这是一个为 MoviePilot 打造的强大且实用的插件扩展包。本合集包含多个自动化运维、站点辅助和系统工具，旨在帮助您更高效、更稳定地管理您的媒体库和 PT 站点。

---

## 📦 包含插件及功能说明

### 1. 🖼️ 本地图片缓存代理 (ImageLocalCache)
- **简介**：将 TMDB 图片自动缓存到本地服务器，并通过插件 API 提供快速访问。
- **特色**：自动拦截并替换所有 TMDB 图片 URL 为本地缓存地址。首次访问时自动从 TMDB 下载缓存，后续直接从本地文件秒速返回，完美解决外网（如企业微信等）加载图片慢或失败的问题。
- **图标**：<img src="plugins.v2/imagelocalcache/image.png" width="30">

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
