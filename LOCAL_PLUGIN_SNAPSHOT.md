# 本地插件稳定快照

日期：2026-11-01。收录 NAS 本地仓库中已登记的 33 个插件，不代表全部均已启用。
保留原作者与作者主页；这是版本镜像，不将第三方插件改署名为本仓库作者。

| 插件 ID | 当前快照版本 | 原作者 |
|---|---|---|
| AutoBackup | 2.1.4 | thsrite |
| AutoSignIn | 1.0.0 | wenzhanquan |
| CFIPSelector | 1.3.0 | xijin285 |
| CaishenPTShout | 1.0.0 | wenzhanquan |
| CategoryEditor | 1.3 | jxxghp |
| CleanInvalidSeed | 2.1 | DzAvril |
| CloudflareSpeedTest | 1.5.1 | thsrite |
| Commands | 1.0 | InfinityPacer |
| CureTMDbAnime | 2.4.1 | Attente |
| CustomHosts | 1.2.2 | thsrite |
| FileCopy | 1.1 | thsrite |
| GroupChatZone | 2.3.3 | KoWming,madrays |
| HermesNotificationForwarder | 2.2 | wenzhanquan |
| IdentifierHelper | 1.0.2 | hankun |
| ImageLocalCache | 1.1.6 | wenzhanquan |
| LLMApiKeyAutoSwitch | 1.0.0 | wenzhanquan |
| LibraryScraper | 2.1.4 | jxxghp |
| LogsClean | 2.1 | madrays |
| MediaCoverGenerator | 0.9.6 | 呀哈哈 |
| MediaServerMsgLocal | 1.0.31 | wenzhanquan |
| MediaServerMsgMod | 1.8.2.1 | jxxghp,Seed680 |
| MediaServerRefresh | 1.3.3 | jxxghp |
| MpPluginHealthCheck | 1.0.1 | wenzhanquan |
| P115StrmHelper | 2.8.79 | DDSRem |
| PluginAutoUpgrade | 2.4.2 | hotlcc |
| PluginMarketsAutoUpdate | 2.1 | Aqr-K |
| QlCookieCheck | 1.0.0 | wenzhanquan |
| RemoteIdentifiers | 2.4.1 | honue |
| RemoveLink | 2.18 | DzAvril |
| RssSubscribe | 2.1 | jxxghp |
| SaMediaSyncDel | 1.0.8 | DDSRem,thsrite |
| SiteStatistic | 1.9.7 | lightolly,jxxghp |
| WeatherWidget | 2.0 | InfinityPacer |

## 使用与更新原则

当前配套运行环境为 MoviePilot 2.15.6 / Python 3.12。建议保留本地仓库副本，关闭插件自动升级，
将本仓库作为集中备份和按需更新来源。一次只替换选定插件目录及其 package.v2.json 索引项，
更新前备份旧目录和该插件配置；验证后再更新其他插件。不需要启用已归档的自动升级插件。

“只有一个仓库”不能消除兼容性风险：MoviePilot API、Python、第三方服务及共享依赖仍会变化。
仓库中包含 115 助手自带的 wheels、锁文件及前端资源，但这不代表全部插件可以完全离线运行，
也不代表这些依赖可在任意平台或未来 Python 版本上安装。

当前 NAS 保持单一本地仓库、禁止自动联网安装新版本的模式。暂不将在线市场重新打开，
因为本机 MP 启动时仍可能将市场中更高的版本作为待安装项；仅关闭“插件自动升级”不足以锁住版本。
若以后需要恢复在线市场，应只填本仓库，并单独验证当前 MP 的启动更新行为。

## 本次校验

- 33 个插件的索引、类名、目录及版本相互校对。
- 297 个 Python 文件通过语法解析，104 个随插件附带的 wheel 通过 ZIP CRC 校验。
- 通知本地版与图片缓存代理的独立回归测试覆盖 19 个案例，另有实际通知接收验证。
- 其余插件只做源码、资源与索引完整性校验，没有逐个执行真实业务测试。
- 五个本地 PNG 曾被错误的文本编码破坏：四个使用 GitHub 上已有的有效原图；Hermes 通知转发图标改用 MP 内置 forward.png 资源。
- 不包含 NAS 配置、账号密码、Cookie、数据库、日志或 Python 编译缓存。

天气插件内置的服务密钥与原作者公开源码一致，未从 NAS 运行配置提取；原有归属与授权仍适用。
