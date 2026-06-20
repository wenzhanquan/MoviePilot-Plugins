import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.utils.http import RequestUtils


class ImageLocalCache(_PluginBase):
    """
    TMDB 图片本地缓存代理插件。

    将 MediaInfo 中的 TMDB 图片 URL 替换为插件本地缓存代理地址，
    企业微信等外部服务通过外网域名访问插件 API 时直接从本地缓存返回图片，
    无需每次从 TMDB 远程加载，解决图片加载慢的问题。
    """

    plugin_name = "本地图片缓存代理"
    plugin_desc = (
        "将 TMDB 图片缓存到本地，通过插件 API 提供快速访问。"
        "自动替换所有 TMDB 图片 URL 为本地缓存地址，"
        "首次访问自动从 TMDB 下载缓存，后续直接返回本地文件。"
    )
    plugin_icon = "image.png"
    plugin_version = "1.0.0"
    plugin_label = "消息通知"
    plugin_author = "wenzhanquan"
    plugin_config_prefix = "imagelocalcache_"
    plugin_order = 100
    auth_level = 1

    # 运行时状态
    _enabled: bool = False
    _external_domain: str = ""
    _cache_dir: str = ""
    _original_tmdb_domain: str = ""
    _original_tmdb_scheme: str = ""
    _proxy_base_url: str = ""
    # 保存原始 get_message_image 方法引用
    _original_get_message_image = None

    def init_plugin(self, config: dict = None) -> None:
        """
        根据插件配置初始化运行状态。

        启动时保存当前 TMDB_IMAGE_DOMAIN，然后替换为本地缓存代理地址，
        使所有 TMDB 图片 URL 指向插件 API。

        :param config: 插件配置字典
        """
        self.stop_service()

        # 初始化状态
        self._enabled = False
        self._external_domain = ""
        self._original_tmdb_domain = ""
        self._proxy_base_url = ""

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        self._external_domain = str(config.get("external_domain") or "").rstrip("/")

        if not self._enabled or not self._external_domain:
            logger.warning("本地图片缓存代理插件已启用，但外网域名未配置")
            return

        # 设置缓存目录（在 MoviePilot 配置目录下）
        self._cache_dir = str(
            Path(settings.CONFIG_DIR) / "plugin_cache" / "imagelocalcache"
        )
        os.makedirs(self._cache_dir, exist_ok=True)

        # 保存原始 TMDB_IMAGE_DOMAIN 作为回源地址
        self._original_tmdb_domain = settings.TMDB_IMAGE_DOMAIN
        self._original_tmdb_scheme = "https://"
        logger.info(f"原始 TMDB 图片域名: {self._original_tmdb_domain}")

        # 构造本地缓存代理的基础 URL
        # 使用单级路径 /img?url= 避免 nginx 多级路径代理问题
        self._proxy_base_url = (
            f"{self._external_domain}/api/v1/plugin/ImageLocalCache/img?url="
        )

        # Monkey-patch MediaInfo.get_message_image
        # 将返回的 TMDB 图片 URL 替换为本地缓存代理地址
        self._patch_get_message_image()

        logger.info(f"本地图片缓存代理插件初始化完成，代理基础 URL: {self._proxy_base_url}")

    def _patch_get_message_image(self):
        """
        替换 MediaInfo.get_message_image 方法。

        将所有 TMDB 图片 URL 替换为本地缓存代理 URL，
        使通知中的图片直接从本地缓存加载。
        """
        if self._original_get_message_image is None:
            self._original_get_message_image = MediaInfo.get_message_image

        proxy_base = self._proxy_base_url

        def patched_get_message_image(self_obj, default=None):
            """
            替换后的 get_message_image 方法。

            调用原始方法获取 TMDB 图片 URL，然后替换为本地代理地址。
            """
            url = self._original_get_message_image(self_obj, default)
            if url:
                return f"{proxy_base}{quote(url, safe='')}"
            return url

        MediaInfo.get_message_image = patched_get_message_image
        logger.debug("已替换 MediaInfo.get_message_image 方法")

    def _unpatch_get_message_image(self):
        """
        恢复 MediaInfo.get_message_image 方法。
        """
        if self._original_get_message_image is not None:
            MediaInfo.get_message_image = self._original_get_message_image
            self._original_get_message_image = None
            logger.debug("已恢复 MediaInfo.get_message_image 方法")

    def get_state(self) -> bool:
        """
        获取插件启用状态。

        :return: 插件是否启用
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回插件远程命令列表。

        :return: 命令列表（当前插件无命令）
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件 API 列表。

        注册一个匿名可访问的图片缓存代理端点。

        :return: API 路由配置列表
        """
        return [
            {
                "path": "/img",
                "endpoint": self.serve_image,
                "methods": ["GET"],
                "summary": "TMDB 图片缓存代理",
                "description": "代理并缓存 TMDB 图片。首次请求从 TMDB 下载并缓存到本地，后续直接返回缓存文件。",
                "allow_anonymous": True,
            }
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """
        返回插件配置表单与默认配置。

        :return: (表单组件列表, 默认配置字典)
        """
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "title": "使用说明",
                                            "text": "启用后会自动将 TMDB 图片 URL 替换为本地缓存地址。"
                                                    "企业微信等外部渠道访问图片时直接从本地返回，无需远程加载。"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用图片缓存代理",
                                            "color": "primary",
                                            "hint": "开启后将拦截所有入库通知图片并替换为本地地址"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "external_domain",
                                            "label": "外网可访问域名",
                                            "placeholder": "http://frp4.ccszxc.xin:56362",
                                            "hint": "企业微信等外部服务能访问到的 MoviePilot 完整地址（含协议和端口）",
                                            "persistentHint": True,
                                            "clearable": True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "outlined",
                                            "text": "插件通过替换 get_message_image() 返回的图片 URL 为本地缓存地址来工作，"
                                                    "不影响原始 MediaInfo 数据和 WebUI 图片显示。"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "external_domain": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页面。

        展示运行状态、缓存统计和配置信息。

        :return: 页面组件列表，插件未启用时返回 None
        """
        if not self._enabled:
            return self._build_disabled_page()

        cache_size = self._get_cache_size()
        cache_count = self._get_cache_count()
        is_patched = self._original_get_message_image is not None
        status_color = "success" if is_patched else "warning"

        return [
            # 状态卡片
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {
                                        "component": "VCardItem",
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {"text": "运行状态"}
                                            },
                                            {
                                                "component": "VChip",
                                                "props": {
                                                    "color": status_color,
                                                    "text": "已启用 - 代理运行中" if is_patched else "已启用 - 待初始化",
                                                    "class": "mt-2"
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 8},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {
                                        "component": "VCardItem",
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {"text": "代理服务"}
                                            },
                                            {
                                                "component": "VCardText",
                                                "props": {
                                                    "text": f"基础地址：{self._proxy_base_url}"
                                                }
                                            },
                                            {
                                                "component": "VCardText",
                                                "props": {
                                                    "text": f"回源地址：{self._original_tmdb_domain}"
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            # 缓存统计卡片
            {
                "component": "VRow",
                "props": {"class": "mt-4"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {
                                        "component": "VCardItem",
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {"text": "缓存统计"}
                                            },
                                            {
                                                "component": "VList",
                                                "props": {"density": "compact"},
                                                "content": [
                                                    {
                                                        "component": "VListItem",
                                                        "props": {
                                                            "title": f"缓存文件数",
                                                            "subtitle": f"{cache_count} 个文件"
                                                        }
                                                    },
                                                    {
                                                        "component": "VListItem",
                                                        "props": {
                                                            "title": f"缓存总大小",
                                                            "subtitle": cache_size
                                                        }
                                                    },
                                                    {
                                                        "component": "VListItem",
                                                        "props": {
                                                            "title": f"缓存目录",
                                                            "subtitle": self._cache_dir
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {
                                        "component": "VCardItem",
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {"text": "配置信息"}
                                            },
                                            {
                                                "component": "VList",
                                                "props": {"density": "compact"},
                                                "content": [
                                                    {
                                                        "component": "VListItem",
                                                        "props": {
                                                            "title": "外网域名",
                                                            "subtitle": self._external_domain
                                                        }
                                                    },
                                                    {
                                                        "component": "VListItem",
                                                        "props": {
                                                            "title": "替换方式",
                                                            "subtitle": "MediaInfo.get_message_image() Monkey-Patch"
                                                        }
                                                    },
                                                    {
                                                        "component": "VListItem",
                                                        "props": {
                                                            "title": "缓存命中",
                                                            "subtitle": "首次请求自动回源下载，后续从本地返回"
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            # 使用说明
            {
                "component": "VRow",
                "props": {"class": "mt-4"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "title": "工作流程",
                                    "text": (
                                        "入库完成 → 通知消息中的图片URL自动替换为本地缓存地址 "
                                        "→ 企业微信请求本地缓存 → 插件从缓存目录返回图片 "
                                        "（首次从TMDB下载并缓存）"
                                    )
                                }
                            }
                        ]
                    }
                ]
            }
        ]

    def _build_disabled_page(self) -> List[dict]:
        """
        构建插件未启用时的页面。

        :return: 页面组件列表
        """
        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": "warning",
                                    "variant": "tonal",
                                    "title": "插件未启用",
                                    "text": "请在设置中启用插件并配置外网域名后保存，图片缓存代理功能将自动生效。"
                                }
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self) -> None:
        """
        停止插件服务。

        恢复原始的 MediaInfo.get_message_image 方法和 TMDB_IMAGE_DOMAIN。
        """
        self._unpatch_get_message_image()
        self._enabled = False

    @eventmanager.register(EventType.TransferComplete)
    def transfer_complete(self, event) -> None:
        """
        监听整理完成事件，预缓存媒体图片。

        当文件转移完成时，提前下载海报和背景图到本地缓存，
        确保后续通知发送时直接从本地返回。

        :param event: 整理完成事件对象
        """
        if not self._enabled:
            return
        try:
            mediainfo = event.event_data.get("mediainfo")
            if mediainfo:
                poster = getattr(mediainfo, "poster_path", None)
                backdrop = getattr(mediainfo, "backdrop_path", None)
                if poster:
                    self._precache_image_url(poster)
                if backdrop:
                    self._precache_image_url(backdrop)
                logger.info(f"已预缓存媒体图片：{getattr(mediainfo, 'title', '')}")
        except Exception as e:
            logger.error(f"预缓存图片失败：{e}")

    def serve_image(self, url: str = ""):
        """
        提供图片缓存代理服务（通过查询参数）。

        从本地缓存目录查找图片，如果未命中则从 TMDB 源下载并缓存。
        使用查询参数（?url=）传递图片地址，避免多级路径的 nginx 代理问题。

        :param url: TMDB 图片完整 URL（建议 URL 编码），格式如 http://.../t/p/w500/xxx.jpg
        :return: FastAPI Response 对象，包含图片数据
        """
        from fastapi.responses import Response
        from urllib.parse import unquote

        if not url:
            return Response(content=b"", status_code=400, media_type="text/plain")

        # 解码 URL
        decoded_url = unquote(url)

        # 从 URL 中提取 size 和 path
        # URL 格式: {scheme}://{domain}/t/p/{size}/{path}
        import re
        match = re.search(r"/t/p/([^/]+)/(.+)$", decoded_url)
        if not match:
            logger.warning(f"无法从 URL 中提取图片路径: {decoded_url}")
            return Response(content=b"", status_code=400, media_type="text/plain")

        size = match.group(1)
        safe_path = match.group(2).lstrip("/")
        cache_file = Path(self._cache_dir) / size / safe_path

        # 检查本地缓存
        if cache_file.exists():
            content = cache_file.read_bytes()
            content_type = self._guess_mime_type(content)
            logger.debug(f"缓存命中：{size}/{safe_path}")
            return Response(content=content, media_type=content_type)

        # 从原始 TMDB 代理域名下载（使用保存的原始回源地址）
        tmdb_source_url = f"{self._original_tmdb_scheme}{self._original_tmdb_domain}/t/p/{size}/{safe_path}"

        # 使用系统配置的代理访问 TMDB 源
        proxy = None
        if settings.PROXY_HOST:
            proxy = {
                "http": settings.PROXY_HOST,
                "https": settings.PROXY_HOST,
            }

        try:
            resp = RequestUtils(proxies=proxy).get_res(tmdb_source_url, timeout=30)
            if resp and resp.status_code == 200:
                content = resp.content
                # 保存到本地缓存
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(content)
                content_type = self._guess_mime_type(content)
                logger.info(f"已缓存图片：{size}/{safe_path}")
                return Response(content=content, media_type=content_type)
            else:
                logger.warning(f"下载图片失败，状态码：{resp.status_code if resp else '无响应'}")
        except Exception as e:
            logger.error(f"下载图片异常：{e}")

        return Response(content=b"", status_code=404, media_type="image/jpeg")

    def _precache_image_url(self, url: str) -> None:
        """
        预缓存单张图片。

        将完整 URL 传递给 serve_image 触发下载缓存。

        :param url: TMDB 图片完整 URL
        """
        if not url:
            return
        try:
            # 直接传入完整 URL，serve_image 内部会解析
            from fastapi.responses import Response
            result = self.serve_image(url=url)
            if result.status_code == 200:
                logger.debug(f"预缓存成功：{url[:80]}...")
        except Exception as e:
            logger.debug(f"预缓存单张图片失败：{e}")

    def _get_cache_size(self) -> str:
        """
        获取缓存目录总大小（人类可读格式）。

        :return: 缓存大小字符串
        """
        total = 0
        cache_dir = Path(self._cache_dir)
        if cache_dir.exists():
            for f in cache_dir.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        if total > 1024 * 1024 * 1024:
            return f"{total / 1024 / 1024 / 1024:.2f} GB"
        elif total > 1024 * 1024:
            return f"{total / 1024 / 1024:.2f} MB"
        elif total > 1024:
            return f"{total / 1024:.2f} KB"
        return f"{total} B"

    def _get_cache_count(self) -> int:
        """
        获取缓存文件数量。

        :return: 缓存文件总数
        """
        cache_dir = Path(self._cache_dir)
        if cache_dir.exists():
            return sum(1 for f in cache_dir.rglob("*") if f.is_file())
        return 0

    @staticmethod
    def _guess_mime_type(content: bytes) -> str:
        """
        根据文件头部字节推断图片 MIME 类型。

        :param content: 图片二进制数据
        :return: MIME 类型字符串
        """
        if content[:4] == b"\x89PNG":
            return "image/png"
        if content[:2] == b"\xff\xd8":
            return "image/jpeg"
        if content[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if content[:2] == b"BM":
            return "image/bmp"
        if content[:4] == b"RIFF" and b"WEBP" in content[:16]:
            return "image/webp"
        return "image/jpeg"
