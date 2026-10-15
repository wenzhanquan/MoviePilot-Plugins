import os
import re
import uuid
import time
import threading
from datetime import datetime
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
        "内置周期性自动清空任务与动态追踪，防止硬盘被无限占用。"
    )
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/imagelocalcache/icon.png"
    plugin_version = "1.1.5"
    plugin_label = "消息通知"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
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
    _auto_clear_schedule: str = "monthly"
    
    # 内存追踪字典：用于在搜索阶段将图片文件名映射回电影名称，实现明细追踪
    _filename_to_title: Dict[str, str] = {}

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        if not config:
            config = {}

        self._enabled = bool(config.get("enabled", False))
        
        if not self._enabled:
            self.stop_service()
            return

        domain = str(config.get("external_domain") or "").rstrip("/")
        if not domain:
            domain = str(settings.APP_DOMAIN or "").rstrip("/")

        if not domain:
            logger.warning("图片缓存代理插件已启用，但插件未配置外网域名，且系统也未配置 APP_DOMAIN，无法代理！")
            self.stop_service()
            return

        self._external_domain = domain
        self._auto_clear_schedule = config.get("auto_clear_schedule", "monthly")

        self._cache_dir = str(
            Path(settings.CONFIG_DIR) / "plugin_cache" / "imagelocalcache"
        )
        os.makedirs(self._cache_dir, exist_ok=True)

        tmdb_domain = (settings.TMDB_IMAGE_DOMAIN or "image.tmdb.org").rstrip("/")
        if tmdb_domain.startswith("http://") or tmdb_domain.startswith("https://"):
            self._original_tmdb_domain = tmdb_domain
            self._original_tmdb_scheme = ""
        else:
            self._original_tmdb_domain = tmdb_domain
            self._original_tmdb_scheme = "https://"
            
        logger.info(f"原始 TMDB 图片回源基址: {self._original_tmdb_scheme}{self._original_tmdb_domain}")

        self._proxy_base_url = (
            f"{self._external_domain}/api/v1/plugin/ImageLocalCache/img?url="
        )

        global _module_proxy_base
        _module_proxy_base = self._proxy_base_url

        # 启动后台守护线程进行周期检查
        self._stop_event = threading.Event()
        threading.Thread(target=self._scheduler_loop, daemon=True).start()

        logger.info(f"本地图片缓存代理插件初始化完成，代理 URL: {self._proxy_base_url}，清理策略: {self._auto_clear_schedule}")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        # 在 MP 插件卡片的右上角“执行命令”处展示
        return [{
            "cmd": "clear_all_cache",
            "desc": "一键物理清空所有图片缓存",
            "icon": "mdi-delete-sweep"
        }]

    def execute(self, in_cmd: str, in_data: dict = None) -> Tuple[bool, str]:
        if in_cmd == "clear_all_cache":
            if not self._cache_dir or not os.path.exists(self._cache_dir):
                return False, "缓存目录不存在，无需清理。"
            try:
                count = 0
                for root, dirs, files in os.walk(self._cache_dir):
                    for name in files:
                        try:
                            os.remove(os.path.join(root, name))
                            count += 1
                        except Exception:
                            pass
                logger.info(f"图片物理缓存清空完成，共清理 {count} 个文件。")
                return True, f"成功清空 {count} 张图片缓存！"
            except Exception as e:
                logger.error(f"清理图片缓存异常：{e}")
                return False, f"清理失败：{str(e)}"
        return False, "未知命令"

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/img",
                "endpoint": self.serve_image,
                "methods": ["GET"],
                "summary": "TMDB 图片缓存代理",
                "description": "代理并缓存 TMDB 图片。首次请求从 TMDB 下载缓存到本地，后续直接返回本地文件。",
                "allow_anonymous": True,
            }
        ]

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
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
                                                    "企业微信等外部渠道访问图片时直接从本地返回，彻底解决加载慢问题。"
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用图片缓存代理",
                                            "color": "primary"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "external_domain",
                                            "label": "外网可访问域名",
                                            "placeholder": "若留空则自动读取系统 APP_DOMAIN",
                                            "clearable": True
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "auto_clear_schedule",
                                            "label": "自动清空周期",
                                            "items": [
                                                {"title": "每月1号自动清空 (默认)", "value": "monthly"},
                                                {"title": "每周一自动清空", "value": "weekly"},
                                                {"title": "每日自动清空", "value": "daily"},
                                                {"title": "从不自动清空", "value": "never"}
                                            ],
                                            "hint": "定期自动清空硬盘中的图片缓存，释放空间"
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
            "auto_clear_schedule": "monthly"
        }

    def get_page(self) -> Optional[List[dict]]:
        if not self._enabled:
            return self._build_disabled_page()

        cache_size = self._get_cache_size()
        cache_count = self._get_cache_count()
        history = self.get_data("_history") or []
        if not isinstance(history, list):
            history = []
        recent = history[:10]

        cards = [
            {
                "component": "VCol",
                "props": {"cols": 12, "md": 4},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "outlined"},
                    "content": [{
                        "component": "VCardText",
                        "props": {"class": "pa-4"},
                        "content": [{
                            "component": "div",
                            "props": {"class": "text-h6 font-weight-bold text-success"},
                            "text": "运行中"
                        }, {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis"},
                            "text": "代理状态"
                        }]
                    }]
                }]
            },
            {
                "component": "VCol",
                "props": {"cols": 12, "md": 4},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "outlined"},
                    "content": [{
                        "component": "VCardText",
                        "props": {"class": "pa-4"},
                        "content": [{
                            "component": "div",
                            "props": {"class": "text-h6 font-weight-bold"},
                            "text": str(cache_count)
                        }, {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis"},
                            "text": "缓存文件数"
                        }]
                    }]
                }]
            },
            {
                "component": "VCol",
                "props": {"cols": 12, "md": 4},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "outlined"},
                    "content": [{
                        "component": "VCardText",
                        "props": {"class": "pa-4"},
                        "content": [{
                            "component": "div",
                            "props": {"class": "text-h6 font-weight-bold"},
                            "text": cache_size
                        }, {
                            "component": "div",
                            "props": {"class": "text-caption text-medium-emphasis"},
                            "text": "硬盘总占用空间"
                        }]
                    }]
                }]
            }
        ]

        rows = []
        for r in recent:
            title = r.get("title", "")
            img_type = r.get("type", "")
            time = r.get("time", "")
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "text": title},
                    {"component": "td", "props": {"class": "text-center"}, "text": img_type},
                    {"component": "td", "text": time}
                ]
            })

        if not rows:
            table_alert = {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "暂无缓存明细记录，发起搜索或完成入库后将自动记录"
                }
            }
        else:
            table_alert = {
                "component": "VTable",
                "props": {"hover": True, "density": "compact"},
                "content": [
                    {"component": "thead", "content": [{"component": "tr", "content": [
                        {"component": "th", "props": {"class": "text-start"}, "text": "媒体名称"},
                        {"component": "th", "props": {"class": "text-center"}, "text": "缓存方式"},
                        {"component": "th", "text": "更新时间"}
                    ]}]},
                    {"component": "tbody", "content": rows}
                ]
            }

        return [
            {"component": "VRow", "content": cards},
            {
                "component": "VRow",
                "props": {"class": "mt-2"},
                "content": [{
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [{
                        "component": "VCard",
                        "props": {"title": "最新 10 条缓存明细追踪", "variant": "outlined"},
                        "content": [{
                            "component": "VCardText",
                            "props": {"class": "pa-4"},
                            "content": [table_alert]
                        }]
                    }]
                }]
            }
        ]

    def _build_disabled_page(self) -> List[dict]:
        return [{
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 12},
                "content": [{
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "variant": "tonal",
                        "title": "插件未启用",
                        "text": "请在设置中启用插件并配置外网域名后保存，图片缓存代理功能将自动生效。"
                    }
                }]
            }]
        }]

    def stop_service(self) -> None:
        global _module_proxy_base
        _module_proxy_base = ""
        self._enabled = False
        if hasattr(self, "_stop_event"):
            self._stop_event.set()

    def _scheduler_loop(self) -> None:
        """后台低频校验循环，实现抗重启的定时清空功能。"""
        while self._enabled and not self._stop_event.is_set():
            try:
                self._check_and_clear_cache()
            except Exception as e:
                logger.error(f"图片缓存后台任务异常: {e}")
                
            for _ in range(3600):
                if not self._enabled or self._stop_event.is_set():
                    return
                time.sleep(1)

    def _check_and_clear_cache(self) -> None:
        """检查并执行周期性的缓存清空。"""
        schedule = self._auto_clear_schedule
        if schedule == "never":
            return
            
        now = datetime.now()
        
        if schedule == "monthly":
            current_period = now.strftime("%Y-%m")
        elif schedule == "weekly":
            current_period = now.strftime("%Y-%W")
        elif schedule == "daily":
            current_period = now.strftime("%Y-%m-%d")
        else:
            return
            
        last_period = self.get_data("last_auto_clear_period")
        
        if last_period and last_period != current_period:
            logger.info(f"达到设定的缓存清理周期 ({schedule})，开始自动清空图片...")
            success, msg = self.execute("clear_all_cache")
            if success:
                self.save_data("last_auto_clear_period", current_period)
        elif not last_period:
            self.save_data("last_auto_clear_period", current_period)

    @eventmanager.register(EventType.TransferComplete)
    def transfer_complete(self, event) -> None:
        """监听整理完成事件，预缓存入库的媒体图片。"""
        if not self._enabled:
            return
        try:
            mediainfo = event.event_data.get("mediainfo")
            if mediainfo:
                if hasattr(mediainfo, "_original_get_poster_image"):
                    poster = mediainfo._original_get_poster_image()
                else:
                    poster = mediainfo.get_poster_image()
                    
                if hasattr(mediainfo, "_original_get_backdrop_image"):
                    backdrop = mediainfo._original_get_backdrop_image()
                else:
                    backdrop = mediainfo.get_backdrop_image()

                title = getattr(mediainfo, "title", "") or getattr(mediainfo, "original_title", "")
                if not title:
                    title = "未知"
                    
                now_str = datetime.now().strftime("%m-%d %H:%M")
                types = []
                
                if poster:
                    self._precache_image_url(poster)
                    types.append("海报")
                if backdrop:
                    self._precache_image_url(backdrop)
                    types.append("背景图")
                    
                if types:
                    record = {
                        "title": title,
                        "type": f"入库预缓存({'+'.join(types)})",
                        "time": now_str
                    }
                    self._append_history(record)
        except Exception as e:
            logger.error(f"预缓存入库图片失败：{e}")

    def serve_image(self, url: str = ""):
        """提供图片缓存代理服务。"""
        from fastapi.responses import Response, RedirectResponse, FileResponse

        if not url:
            return Response(content=b"", status_code=400, media_type="text/plain")

        if not self._enabled or not self._cache_dir:
            return RedirectResponse(url=url)

        match = re.search(r"/t/p/([^/]+)/(.+)$", url)
        if not match:
            return RedirectResponse(url=url)

        size = match.group(1)
        if not size.isalnum():
            logger.warning(f"非法图片尺寸参数: {size}")
            return RedirectResponse(url=url)

        filename = Path(match.group(2).split("?")[0]).name
        if not filename:
            return RedirectResponse(url=url)
            
        cache_file = Path(self._cache_dir) / size / filename
        cache_headers = {"Cache-Control": "public, max-age=31536000"}

        if cache_file.exists() and cache_file.stat().st_size > 0:
            logger.info(f"命中本地图片缓存：{size}/{filename}")
            return FileResponse(path=str(cache_file), headers=cache_headers)

        tmdb_source_url = f"{self._original_tmdb_scheme}{self._original_tmdb_domain}/t/p/{size}/{filename}"

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
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                
                tmp_file = cache_file.parent / f"{cache_file.name}.{uuid.uuid4().hex}.tmp"
                try:
                    tmp_file.write_bytes(content)
                    tmp_file.replace(cache_file)
                    
                    # 动态追踪记录：当真实发生下载时，尝试在字典中找回电影名称写入明细表
                    title = ImageLocalCache._filename_to_title.get(filename, "未知搜刮项")
                    if title != "未知搜刮项":
                        self._append_history({
                            "title": title,
                            "type": "动态代理下载",
                            "time": datetime.now().strftime("%m-%d %H:%M")
                        })
                        
                except Exception as write_err:
                    logger.error(f"保存缓存文件失败: {write_err}")
                finally:
                    try:
                        if tmp_file.exists():
                            tmp_file.unlink()
                    except Exception:
                        pass

                content_type = self._guess_mime_type(content)
                logger.info(f"已缓存新图片至本地：{size}/{filename}")
                return Response(content=content, media_type=content_type, headers=cache_headers)
            else:
                logger.warning(f"下载图片失败，状态码：{resp.status_code if resp else '无响应'}，URL: {tmdb_source_url}")
        except Exception as e:
            logger.error(f"下载图片异常：{e}")

        return RedirectResponse(url=url)

    def _append_history(self, record: dict) -> None:
        try:
            history = self.get_data("_history") or []
            if not isinstance(history, list):
                history = []
            history.insert(0, record)
            if len(history) > 10:
                history = history[:10]
            self.save_data("_history", history)
        except Exception as e:
            logger.debug(f"保存图片缓存明细失败（可忽略）: {e}")

    def _precache_image_url(self, url: str) -> None:
        if not url:
            return
        try:
            result = self.serve_image(url=url)
            if getattr(result, 'status_code', None) == 200:
                logger.debug(f"预缓存完成：{url[:80]}...")
        except Exception as e:
            logger.debug(f"预缓存单张图片失败：{e}")

    def _get_cache_size(self) -> str:
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
        cache_dir = Path(self._cache_dir)
        if cache_dir.exists():
            return sum(1 for f in cache_dir.rglob("*") if f.is_file())
        return 0

    @staticmethod
    def _guess_mime_type(content: bytes) -> str:
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


# ============================================================
# 模块级 patch（彻底解决直接读属性的搜索盲区）
# ============================================================

_module_proxy_base = ""

def _module_patch():
    """
    保存原始方法并动态替换。
    核心逻辑：不仅拦截调用方法，还拦截对象的实例化(__init__)，并记录日志，实现可视化代理。
    """
    global _module_proxy_base

    if not _module_proxy_base and settings.APP_DOMAIN:
        _module_proxy_base = f"{settings.APP_DOMAIN.rstrip('/')}/api/v1/plugin/ImageLocalCache/img?url="

    def _wrap_url(url):
        if not url or not isinstance(url, str):
            return url
        if "/plugin/ImageLocalCache/img?url=" in url:
            return url
        if _module_proxy_base and ("/t/p/" in url or "tmdb.org" in url):
            return f"{_module_proxy_base}{quote(url, safe='')}"
        return url

    # 提取文件特征以供跨层级追踪使用
    def _extract_and_map(orig_url, title):
        try:
            fname = Path(orig_url.split("?")[0]).name
            if fname:
                # 保持字典在合理范围内防内存溢出
                if len(ImageLocalCache._filename_to_title) > 500:
                    ImageLocalCache._filename_to_title.clear()
                ImageLocalCache._filename_to_title[fname] = title
        except Exception:
            pass

    # --------------------------------------------------------
    # 1. 拦截 MediaInfo 的方法 (防动态获取调用)
    # --------------------------------------------------------
    if not hasattr(MediaInfo, "_original_get_message_image"):
        MediaInfo._original_get_message_image = MediaInfo.get_message_image
        MediaInfo._original_get_poster_image = MediaInfo.get_poster_image
        MediaInfo._original_get_backdrop_image = MediaInfo.get_backdrop_image

    def _patched_get_message_image(self_obj, *args, **kwargs):
        url = MediaInfo._original_get_message_image(self_obj, *args, **kwargs)
        return _wrap_url(url)

    def _patched_get_poster_image(self_obj, *args, **kwargs):
        url = MediaInfo._original_get_poster_image(self_obj, *args, **kwargs)
        return _wrap_url(url)

    def _patched_get_backdrop_image(self_obj, *args, **kwargs):
        url = MediaInfo._original_get_backdrop_image(self_obj, *args, **kwargs)
        return _wrap_url(url)

    MediaInfo.get_message_image = _patched_get_message_image
    MediaInfo.get_poster_image = _patched_get_poster_image
    MediaInfo.get_backdrop_image = _patched_get_backdrop_image

    # --------------------------------------------------------
    # 2. 拦截 MediaInfo 实例化 (解决企业微信搜索直接读属性的盲区)
    # --------------------------------------------------------
    if not hasattr(MediaInfo, "_original_init"):
        MediaInfo._original_init = MediaInfo.__init__

    def _patched_mediainfo_init(self_obj, *args, **kwargs):
        MediaInfo._original_init(self_obj, *args, **kwargs)
        replaced = False
        title = getattr(self_obj, "title", "") or getattr(self_obj, "original_title", "未知媒体")
        
        # 拦截对象的诞生，直接修改其底层属性，同时记录映射追踪特征
        if isinstance(getattr(self_obj, "poster_path", None), str):
            orig = self_obj.poster_path
            self_obj.poster_path = _wrap_url(orig)
            if orig != self_obj.poster_path:
                _extract_and_map(orig, title)
                replaced = True
                
        if isinstance(getattr(self_obj, "backdrop_path", None), str):
            orig = self_obj.backdrop_path
            self_obj.backdrop_path = _wrap_url(orig)
            if orig != self_obj.backdrop_path:
                _extract_and_map(orig, title)
                replaced = True
                
        if replaced:
            logger.info(f"已注入图片代理链接: {title}")

    MediaInfo.__init__ = _patched_mediainfo_init

    # --------------------------------------------------------
    # 3. 拦截 Notification 实例化 (兜底包含 image 字段的普通通知)
    # --------------------------------------------------------
    try:
        from app.schemas.types import Notification
        if not hasattr(Notification, "_original_init"):
            Notification._original_init = Notification.__init__
        
        def _patched_notification_init(self_obj, *args, **kwargs):
            Notification._original_init(self_obj, *args, **kwargs)
            if isinstance(getattr(self_obj, "image", None), str):
                orig = self_obj.image
                self_obj.image = _wrap_url(orig)
                if orig != self_obj.image:
                    title = getattr(self_obj, "title", "未知通知")
                    logger.info(f"已注入通知消息图片代理: {title}")
                
        Notification.__init__ = _patched_notification_init
    except Exception as e:
        logger.debug(f"拦截 Notification 失败: {e}")

# 模块导入时立即安全执行 patch
_module_patch()