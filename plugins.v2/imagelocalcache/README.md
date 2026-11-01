# 本地图片缓存代理

版本：1.1.6。插件 ID 与类名为 ImageLocalCache，源码目录为 imagelocalcache。

启用后填写可供企业微信等外部客户端访问的域名；未填写时回退到 MoviePilot 的 APP_DOMAIN。
TMDB 图片通过插件接口读取，首次请求下载到 CONFIG_DIR/plugin_cache/imagelocalcache，后续返回本地缓存。
可以配置清理周期；使用方需要确保反向代理能够访问匿名图片路由。

1.1.6 修复缓存命中时的 MIME：TMDB 可能以 .jpg 地址返回 WebP，响应类型现在由文件头确定，避免首次请求和缓存命中时不一致。

本插件当前只缓存 TMDB /t/p/ 图片；非 TMDB URL 或插件停用时会重定向到原地址。
本地缓存不等于完全离线，首次回源仍需要访问 TMDB。

升级旧版本后，如安装清单同时存在 imagelocalcache 和 ImageLocalCache，只保留后者，避免同一插件重复登记。
