import re
import json
import threading
import time
from typing import Any, List, Dict, Tuple, Optional
from urllib.parse import quote
from pathlib import Path

from app.core.cache import cached
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.modules.themoviedb import CategoryHelper
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo, ServiceInfo, MediaServerItem
from app.schemas.types import EventType, MediaType, MediaImageType, NotificationType
from app.utils.web import WebUtils


class MediaServerMsgLocal(_PluginBase):
    """
    媒体服务器通知本地版

    基于 MediaServerMsgMod 魔改版修改，图片 URL 走本地缓存代理。

    功能：
    1. 监听Emby/Jellyfin/Plex等媒体服务器的Webhook事件
    2. 根据配置发送播放、入库等通知消息
    3. 对TV剧集入库事件进行智能聚合，避免消息轰炸
    4. 支持多种媒体服务器和丰富的消息类型配置
    """

    # 常量定义
    DEFAULT_EXPIRATION_TIME = 600                  # 默认过期时间（秒）
    DEFAULT_AGGREGATE_TIME = 15                    # 默认聚合时间（秒）

    # 插件基本信息
    plugin_name = "媒体服务器通知本地版"
    plugin_desc = "发送Emby/Jellyfin/Plex服务器的播放、入库等通知消息。图片已走本地缓存代理。"
    
    # 使用前端自带资源，避免 HTTP 图标被再次交给域名受限的图片代理。
    plugin_icon = "mediaplay.png"
    
    plugin_version = "1.0.31"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
    plugin_config_prefix = "mediaservermsglocal_"
    plugin_order = 14
    auth_level = 1

    # Webhook事件映射配置
    _webhook_actions = {
        "library.new": "新入库",
        "system.notificationtest": "测试",
        "system.webhooktest": "测试",
        "playback.start": "开始播放",
        "playback.stop": "停止播放",
        "user.authenticated": "登录成功",
        "user.authenticationfailed": "登录失败",
        "media.play": "开始播放",
        "media.stop": "停止播放",
        "PlaybackStart": "开始播放",
        "PlaybackStop": "停止播放",
        "item.rate": "标记了"
    }

    # 媒体服务器默认图标
    _webhook_images = {
        "emby": "https://emby.media/notificationicon.png",
        "plex": "https://www.plex.tv/wp-content/uploads/2022/04/new-logo-process-lines-gray.png",
        "jellyfin": "https://play-lh.googleusercontent.com/SCsUK3hCCRqkJbmLDctNYCfehLxsS4ggD1ZPHIFrrAN1Tn9yhjmGMPep2D9lMaaa9eQi"
    }

    def __init__(self):
        super().__init__()
        self.category = CategoryHelper()
        self._lock = threading.Lock()
        self._pending_messages = {}
        self._aggregate_timers = {}
        self._webhook_msg_keys = {}
        self._enabled = False
        self._add_play_link = False
        self._aggregate_enabled = True
        self._aggregate_time = self.DEFAULT_AGGREGATE_TIME
        self._types = []
        self._mediaservers = []
        logger.debug("媒体服务器消息本地代理插件初始化完成")

    def init_plugin(self, config: dict = None):
        """初始化插件配置"""
        if config:
            self._enabled = config.get("enabled", False)
            self._types = config.get("types") or []
            self._mediaservers = config.get("mediaservers") or []
            self._add_play_link = config.get("add_play_link", False)
            self._aggregate_enabled = config.get("aggregate_enabled", False)
            self._aggregate_time = int(config.get("aggregate_time", self.DEFAULT_AGGREGATE_TIME))

    def service_infos(self, type_filter: Optional[str] = None) -> Optional[Dict[str, ServiceInfo]]:
        if not self._mediaservers:
            return None

        services = MediaServerHelper().get_services(type_filter=type_filter, name_filters=self._mediaservers)
        if not services:
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if not service_info.instance.is_inactive():
                active_services[service_name] = service_info

        return active_services if active_services else None

    def service_info(self, name: str) -> Optional[ServiceInfo]:
        service_infos = self.service_infos() or {}
        return service_infos.get(name)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """注册本地图标接口"""
        return [{"path": "/icon", "endpoint": self.serve_icon, "methods": ["GET"],
                 "summary": "通知插件本地图标", "allow_anonymous": True}]

    def serve_icon(self) -> Any:
        """返回随插件保存的图标文件"""
        from fastapi.responses import FileResponse
        return FileResponse(Path(__file__).with_name("icon.png"), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    def _get_cached_image_url(self, image_url: Optional[str]) -> Optional[str]:
        """使用图片缓存插件的对外域名，避免重复嵌套代理地址。"""
        if not image_url or "/api/v1/plugin/ImageLocalCache/img?url=" in image_url:
            return image_url
        config = self.get_config("ImageLocalCache") or {}
        domain = str(config.get("external_domain") or settings.APP_DOMAIN or "").rstrip("/")
        if not domain:
            return image_url
        return f"{domain}/api/v1/plugin/ImageLocalCache/img?url={quote(image_url, safe='')}"

    def _extract_episode_pairs(self, event: WebhookEventInfo) -> List[Tuple[int, int]]:
        """从 Emby 汇总描述或单集元数据提取完整季集，保留特别篇的第零季。"""
        payload = getattr(event, "json_object", None)
        payload = payload if isinstance(payload, dict) else {}
        item = payload.get("Item") or payload.get("Metadata") or {}
        item = item if isinstance(item, dict) else {}
        pairs = set()
        description = str(payload.get("Description") or "")
        # Emby 把一次扫描的多集放在一条 Series Webhook 中，而非每集单独通知。
        pattern = r"S(\d{1,3})\s*(E\d{1,4}(?:\s*-\s*E?\d{1,4})?(?:\s*[,，、]\s*E?\d{1,4}(?:\s*-\s*E?\d{1,4})?)*)"
        for match in re.finditer(pattern, description, re.IGNORECASE):
            season = int(match.group(1))
            for token in re.split(r"\s*[,，、]\s*", match.group(2)):
                bounds = re.fullmatch(r"E?(\d{1,4})(?:\s*-\s*E?(\d{1,4}))?", token, re.IGNORECASE)
                if bounds:
                    start = int(bounds.group(1))
                    end = int(bounds.group(2) or start)
                    if start <= end:
                        pairs.update((season, episode) for episode in range(start, end + 1))
        if pairs:
            return sorted(pairs)
        if str(item.get("Type") or getattr(event, "media_type", "")).upper() in ("SERIES", "SEASON"):
            return []
        season, episode, _ = self._extract_season_episode(event)
        if episode is not None:
            season_value = item.get("ParentIndexNumber")
            if season_value is not None:
                season = int(season_value)
            end = item.get("IndexNumberEnd")
            try:
                end = int(end) if end is not None else episode
            except (ValueError, TypeError):
                end = episode
            if episode <= end <= 9999:
                pairs.update((season, number) for number in range(episode, end + 1))
        return sorted(pairs)

    def _get_episode_count(self, events: List[WebhookEventInfo]) -> int:
        """按去重后的季集计数，不将 Webhook 条数当成入库集数。"""
        return len({pair for event in events for pair in self._extract_episode_pairs(event)})

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        types_options = [
            {"title": "新入库", "value": "library.new"},
            {"title": "开始播放", "value": "playback.start|media.play|PlaybackStart"},
            {"title": "停止播放", "value": "playback.stop|media.stop|PlaybackStop"},
            {"title": "用户标记", "value": "item.rate"},
            {"title": "测试", "value": "system.webhooktest"},
            {"title": "登录成功", "value": "user.authenticated"},
            {"title": "登录失败", "value": "user.authenticationfailed"},
        ]
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'add_play_link',
                                            'label': '添加播放链接',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'mediaservers',
                                            'label': '媒体服务器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in MediaServerHelper().get_configs().values()]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'types',
                                            'label': '消息类型',
                                            'items': types_options
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'aggregate_enabled',
                                            'label': '启用TV剧集入库聚合',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'show': '{{aggregate_enabled}}'},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'aggregate_time',
                                            'label': 'TV剧集入库聚合时间（秒）',
                                            'placeholder': '15'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'props': {'show': '{{aggregate_enabled}}'},
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'warning',
                                            'variant': 'tonal',
                                            'text': '请在整理刮削设置中添加tmdbid以保证准确性。仅保证在Emby和整理刮削添加tmdbid后功能正常。'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '需要设置媒体服务器Webhook，回调相对路径为 /api/v1/webhook?token=API_TOKEN&source=媒体服务器名。'
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
            "types": [],
            "aggregate_enabled": False,
            "aggregate_time": 15
        }

    def _record_history(self, title: str, detail: str = ""):
        """记录通知发送历史，提供给 get_page 渲染展示"""
        try:
            history = self.get_data("msg_history") or []
            if not isinstance(history, list):
                history = []
            
            record = {
                "time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())),
                "title": title,
                "detail": detail
            }
            history.insert(0, record)
            if len(history) > 50:
                history = history[:50]
                
            self.save_data("msg_history", history)
        except Exception as e:
            logger.debug(f"保存通知历史记录失败: {e}")

    def get_page(self) -> List[dict]:
        """在 MP 网页端点击插件时渲染历史记录看板"""
        history = self.get_data("msg_history") or []
        
        if not history or not isinstance(history, list):
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "text": "暂无发送记录，当有新的剧集入库或播放通知发送后，将在这里展示明细。"
                    }
                }
            ]

        rows = []
        for item in history:
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "text": item.get("time", "")},
                    {"component": "td", "text": item.get("title", "")},
                    {"component": "td", "text": item.get("detail", "")},
                ]
            })

        return [
            {
                "component": "VCard",
                "props": {"title": "历史推送明细看板 (最近 50 条)", "variant": "outlined"},
                "content": [
                    {
                        "component": "VTable",
                        "props": {"density": "compact", "hover": True},
                        "content": [
                            {
                                "component": "thead",
                                "content": [
                                    {
                                        "component": "tr",
                                        "content": [
                                            {"component": "th", "props": {"class": "text-left", "width": "180px"}, "text": "时间戳"},
                                            {"component": "th", "props": {"class": "text-left", "width": "350px"}, "text": "触发事件"},
                                            {"component": "th", "props": {"class": "text-left"}, "text": "入库剧集明细 / 其他"}
                                        ]
                                    }
                                ]
                            },
                            {
                                "component": "tbody",
                                "content": rows
                            }
                        ]
                    }
                ]
            }
        ]

    def _get_subscriber_names(self, tmdb_id: str) -> str:
        """【性能核弹优化】带着 tmdb_id 潜入 MP 数据库反查订阅此媒体的用户，加入 limit 防内存溢出与死锁"""
        if not tmdb_id:
            return "未知"
        try:
            from app.db.models.subscribe import Subscribe
            from app.db.models.user import User
            from app.db import db
            
            # 清洗 tmdb_id 确保纯数字，防 SQL 报错
            match = re.search(r'\d+', str(tmdb_id))
            if not match:
                return "未知"
            clean_tmdbid = int(match.group())
            
            # 【优化防御】：强行只取前20条订阅记录，速度维持在毫秒级，防止脏数据无限膨胀导致的卡顿
            subs = db.session.query(Subscribe).filter(Subscribe.tmdbid == clean_tmdbid).limit(20).all()
            if not subs:
                return "未知"
            
            users = set()
            for sub in subs:
                uid = getattr(sub, 'userid', None) or getattr(sub, 'uid', None)
                if uid:
                    u = db.session.query(User).filter(User.id == uid).first()
                    name = getattr(u, 'name', None) or getattr(u, 'nickname', None)
                    if name:
                        users.add(str(name))
            
            return "、".join(users) if users else "未知"
        except Exception as e:
            logger.debug(f"反查订阅用户异常: {e}")
            return "未知"

    @eventmanager.register(EventType.WebhookMessage)
    def send(self, event: Event):
        """发送通知消息主入口函数"""
        try:
            if not self._enabled:
                return

            event_info: WebhookEventInfo = getattr(event, 'event_data', None)
            if not event_info:
                return

            event_type = getattr(event_info, 'event', None)
            
            if event_type == "library.new":
                item_n = getattr(event_info, 'item_name', '未知')
                item_t = getattr(event_info, 'item_type', '未知')
                logger.info(f"📡 [海关探针] 收到 Emby 入库 Webhook -> 名称: {item_n} | 类型: {item_t}")
                try:
                    raw_json = json.dumps(getattr(event_info, 'json_object', {}), ensure_ascii=False)
                    logger.info(f"📦 [海关探针-原始报文] {raw_json}")
                except Exception as e:
                    logger.debug(f"原始报文打印失败: {e}")

            self.__clean_expired_elements()

            if not event_type or not self._webhook_actions.get(event_type):
                return

            allowed_types = set()
            for _type in self._types:
                allowed_types.update(_type.split("|"))

            if event_type not in allowed_types:
                return

            if not self.service_infos():
                return

            server_name = getattr(event_info, 'server_name', None)
            if server_name and not self.service_info(name=server_name):
                return

            channel = getattr(event_info, 'channel', None)
            if channel and not self.service_infos(type_filter=channel):
                return

            def should_aggregate_tv() -> bool:
                try:
                    if not self._aggregate_enabled: return False
                    if event_type != "library.new": return False
                    
                    item_type = getattr(event_info, 'item_type', '')
                    if str(item_type).upper() not in ["TV", "SHOW", "EPISODE", "SERIES", "SEASON"]: return False
                    
                    json_object = getattr(event_info, 'json_object', None)
                    if json_object and not isinstance(json_object, dict): return False
                    return True
                except Exception:
                    return False

            if should_aggregate_tv():
                series_id = self._get_series_id(event_info)
                if series_id:
                    self._aggregate_tv_episodes(series_id, event_info)
                    return

            # ============== 以下是非聚合流程（电影、单集、播放等） ==============
            item_id = getattr(event_info, 'item_id', '')
            client = getattr(event_info, 'client', '')
            user_name = getattr(event_info, 'user_name', '')
            expiring_key = f"{item_id}-{client}-{user_name}"

            with self._lock:
                is_duplicate = expiring_key in self._webhook_msg_keys

            if str(event_type) == "playback.stop" and is_duplicate:
                self.__add_element(expiring_key)
                return

            item_type = str(getattr(event_info, 'item_type', '')).upper()
            item_name = getattr(event_info, 'item_name', '') or ''
            if event_type == "system.webhooktest" and not item_name:
                item_name = "Emby Webhook 连通性测试"

            message_title = ""
            event_action = self._webhook_actions.get(event_type, event_type)
            
            if item_type in ["TV", "SHOW", "EPISODE", "SERIES", "SEASON"]:
                message_title = f"📺 {event_action}剧集：{item_name}"
            elif item_type in ["MOV", "MOVIE"]:
                message_title = f"🎬 {event_action}电影：{item_name}"
            elif item_type in ["AUD", "AUDIO"]:
                message_title = f"🎧 {event_action}有声书：{item_name}"
            else:
                message_title = f"🔔 {event_action}：{item_name}"

            message_texts = []
            
            if event_type == "library.new":
                message_texts.append(f"⏰ 入库时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")
            else:
                message_texts.append(f"⏰ 发生时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

            if user_name: 
                message_texts.append(f"👤 用户：{user_name}")

            device_name = getattr(event_info, 'device_name', None)
            if device_name:
                message_texts.append(f"📱 设备：{client or ''} {device_name}")
            elif client:
                message_texts.append(f"📱 设备：{client}")

            ip = getattr(event_info, 'ip', None)
            if ip:
                try:
                    location = WebUtils.get_location(ip)
                    message_texts.append(f"📍 IP地址：{ip} {location}")
                except Exception:
                    message_texts.append(f"📍 IP地址：{ip}")

            percentage = getattr(event_info, 'percentage', None)
            if percentage:
                try:
                    percentage_val = round(float(percentage), 2)
                    message_texts.append(f"⏳ 进度：{percentage_val}%")
                except (ValueError, TypeError):
                    pass

            image_url = getattr(event_info, 'image_url', None)
            tmdb_id = getattr(event_info, 'tmdb_id', None)
            season_id, episode_id, _ = self._extract_season_episode(event_info)
            overview = getattr(event_info, 'overview', None)

            tmdb_info = None
            if tmdb_id:
                try:
                    if item_type in ["MOV", "MOVIE"]:
                        tmdb_info = self._get_tmdb_info(tmdb_id, MediaType.MOVIE)
                    else:
                        tmdb_info = self._get_tmdb_info(tmdb_id, MediaType.TV, season_id)
                except Exception:
                    pass

            # 追加订阅用户信息，并处理查不到的边界
            if event_type == "library.new":
                subscribers = self._get_subscriber_names(tmdb_id)
                message_texts.append(f"🙋‍♂️ 订阅用户：{subscribers}")
                if item_type in ["TV", "SHOW", "EPISODE", "SERIES", "SEASON"]:
                    episode_detail = self._merge_continuous_episodes([event_info])
                    episode_count = self._get_episode_count([event_info])
                    if episode_detail:
                        message_texts.append(f"📺 季集：{episode_detail}")
                    if episode_count > 1:
                        message_title += f" {episode_count}集"

            cat = None
            if tmdb_info:
                try:
                    if tmdb_info.get('media_type') == MediaType.TV or item_type in ["TV", "SHOW", "EPISODE", "SERIES", "SEASON"]:
                        cat = self.category.get_tv_category(tmdb_info)
                    else:
                        cat = self.category.get_movie_category(tmdb_info)
                except Exception:
                    pass
            if cat:
                message_texts.append(f"📚 分类：{cat}")

            if tmdb_info and tmdb_info.get('vote_average'):
                try:
                    rating = round(float(tmdb_info.get('vote_average')), 1)
                    message_texts.append(f"⭐ 评分：{rating}/10")
                except Exception:
                    pass
                
                genres = tmdb_info.get('genres', [])
                if genres:
                    genres_list = []
                    for g in genres[:3]:
                        if isinstance(g, dict) and g.get('name'):
                            genres_list.append(g.get('name'))
                        elif str(g):
                            genres_list.append(str(g))
                    if genres_list:
                        message_texts.append(f"🎭 类型：{'、'.join(genres_list)}")

            if not overview and tmdb_info:
                overview = tmdb_info.get('overview', '')
            
            if overview:
                if len(overview) > 100:
                    overview = overview[:100] + "..."
                message_texts.append(f"📖 剧情：{overview}")

            message_content = "\n".join(message_texts)

            if not image_url and tmdb_info:
                try:
                    tmdb_domain = settings.TMDB_IMAGE_DOMAIN or "image.tmdb.org"
                    if not tmdb_domain.startswith("http"):
                        tmdb_domain = f"https://{tmdb_domain}"
                    tmdb_domain = tmdb_domain.rstrip("/")
                    
                    if item_type in ["MOV", "MOVIE"] and tmdb_info.get('poster_path'):
                        original_url = f"{tmdb_domain}/t/p/original{tmdb_info.get('poster_path')}"
                        image_url = self._get_cached_image_url(original_url)
                    elif item_type in ["TV", "SHOW", "EPISODE", "SERIES", "SEASON"] and tmdb_info.get('backdrop_path'):
                        original_url = f"{tmdb_domain}/t/p/original{tmdb_info.get('backdrop_path')}"
                        image_url = self._get_cached_image_url(original_url)
                except Exception as e:
                    logger.debug(f"提取TMDB代理链接出错: {e}")

            if not image_url:
                image_url = self._webhook_images.get(getattr(event_info, 'channel', ''))
            image_url = self._get_cached_image_url(image_url)

            play_link = None
            if self._add_play_link:
                play_link = self._get_play_link(event_info)

            self.post_message(mtype=NotificationType.MediaServer,
                              title=message_title, text=message_content, image=image_url, link=play_link)
            
            logger.info(f"✅ 非聚合通知已送达企业微信: {message_title}")
            ui_detail = "单条入库事件" if event_type == "library.new" else f"事件: {event_action}"
            self._record_history(message_title, ui_detail)

        except Exception as e:
            logger.error(f"处理Webhook事件时发生错误: {str(e)}", exc_info=True)

    def _get_series_id(self, event_info: WebhookEventInfo) -> Optional[str]:
        """按服务器和剧集分组，Series 的父目录不能作为剧集标识。"""
        try:
            namespace = f"{getattr(event_info, 'channel', '')}:{getattr(event_info, 'server_name', '')}:"
            item_path = getattr(event_info, 'item_path') or ''
            tmdb_match = re.search(r'[\[{](?:tmdbid|tmdb)[=-](\d+)[\]}]', str(item_path), re.IGNORECASE)
            if tmdb_match:
                return f"{namespace}tmdb_{tmdb_match.group(1)}"

            json_object = getattr(event_info, 'json_object', None)
            if json_object and isinstance(json_object, dict):
                item = json_object.get("Item") or json_object.get("Metadata") or json_object
                if isinstance(item, dict):
                    series_id = item.get("SeriesId")
                    if not series_id and str(item.get("Type", "")).upper() == "SERIES":
                        series_id = item.get("Id")
                    series_id = series_id or item.get("SeriesName")
                    if series_id:
                        return namespace + str(series_id)

            item_name = getattr(event_info, 'item_name') or ''
            name_match = re.split(r'[Ss]\d+\s*[Ee]\d+|第\s*\d+\s*[季集]', str(item_name))
            if name_match and name_match[0].strip():
                return namespace + name_match[0].strip()

            fallback_id = getattr(event_info, "series_id", None)
            if fallback_id:
                return namespace + str(fallback_id)
                
        except Exception as e:
            logger.debug(f"提取 series_id 异常: {e}")
            
        return None

    def _extract_season_episode(self, event: WebhookEventInfo) -> Tuple[Optional[int], Optional[int], str]:
        try:
            if str(getattr(event, 'item_type', '')).upper() == "SERIES":
                return None, None, str(getattr(event, 'item_name', ''))

            season = next((getattr(event, key, None) for key in ("season_id", "item_season", "season")
                           if getattr(event, key, None) is not None), None)
            episode = next((getattr(event, key, None) for key in ("episode_id", "item_episode", "episode")
                            if getattr(event, key, None) is not None), None)
            episode_name = getattr(event, "item_name", "") or ""

            if str(season).strip() in ("", "None", "null"): season = None
            if str(episode).strip() in ("", "None", "null"): episode = None

            if hasattr(event, 'json_object') and isinstance(event.json_object, dict):
                item = event.json_object.get("Item") or event.json_object.get("Metadata") or event.json_object
                if isinstance(item, dict):
                    if season is None: 
                        season = next((item.get(key) for key in ("ParentIndexNumber", "SeasonNumber", "parentIndex")
                                       if item.get(key) is not None), None)
                    if episode is None: 
                        episode = next((item.get(key) for key in ("IndexNumber", "EpisodeNumber", "index")
                                        if item.get(key) is not None), None)
                    if not episode_name or episode_name == "未知剧集":
                        episode_name = item.get("Name") or item.get("EpisodeName") or item.get("title") or ""

            if season is None or episode is None:
                s_name = str(getattr(event, 'item_name', ''))
                s_path = str(getattr(event, 'item_path', ''))
                s_json = str(getattr(event, 'json_object', '')) if getattr(event, 'json_object', None) else ""
                
                text_to_search = f"{s_name} {s_path} {episode_name} {s_json}"
                
                match_se = re.search(r'[Ss]([0-9]{1,3})\s*[Ee]([0-9]{1,4})', text_to_search, re.IGNORECASE)
                if match_se:
                    if season is None: season = match_se.group(1)
                    if episode is None: episode = match_se.group(2)
                
                if season is None or episode is None:
                    match_zh = re.search(r'第\s*([0-9]{1,3})\s*季\s*第?\s*([0-9]{1,4})\s*集', text_to_search)
                    if match_zh:
                        if season is None: season = match_zh.group(1)
                        if episode is None: episode = match_zh.group(2)
                    
                if episode is None:
                    match_ep_only = re.search(r'第\s*([0-9]{1,4})\s*集', text_to_search)
                    if match_ep_only:
                        episode = match_ep_only.group(1)
                        if season is None: season = 1
                    
                if episode is None:
                    match_ep = re.search(r'[Ee][Pp]\.?\s*([0-9]{1,4})', text_to_search, re.IGNORECASE)
                    if match_ep:
                        episode = match_ep.group(1)
                        if season is None: season = 1

            def safe_extract_digits(val, default):
                if val is None or str(val).strip() in ("", "None", "null"): return default
                if isinstance(val, int): return val
                m = re.search(r'\d+', str(val))
                return int(m.group()) if m else default

            s_int = safe_extract_digits(season, 1)
            e_int = safe_extract_digits(episode, None)
            return s_int, e_int, str(episode_name)
        except Exception as e:
            logger.debug(f"提取剧集元数据异常: {e}")
            return 1, None, str(getattr(event, "item_name", ""))

    def _aggregate_tv_episodes(self, series_id: str, event_info: WebhookEventInfo):
        try:
            if not series_id: return
            item_name = getattr(event_info, 'item_name', '未知集数')

            with self._lock:
                if series_id not in self._pending_messages:
                    self._pending_messages[series_id] = []
                self._pending_messages[series_id].append(event_info)
                
                current_count = self._get_episode_count(self._pending_messages[series_id])
                current_detail = self._merge_continuous_episodes(self._pending_messages[series_id])

                previous_timer = self._aggregate_timers.get(series_id)
                if previous_timer:
                    previous_timer.cancel()
                # 与魔改版保持一致：连续到达的通知将等待时间延后至最后一条之后。
                timer = threading.Timer(
                    self._aggregate_time,
                    lambda: self._send_aggregated_message(series_id, expected_timer=timer)
                )
                timer.daemon = True
                self._aggregate_timers[series_id] = timer
                timer.start()
                logger.info(f"📦 剧集 [{series_id}] 将在最后一条通知后 {self._aggregate_time} 秒发送聚合消息")
                
                logger.info(f"📥 剧集 [{series_id}] 加入候车室: {item_name} (当前本车共 {current_count} 集, 明细: {current_detail})")

        except Exception as e:
            logger.error(f"聚合处理异常: {str(e)}", exc_info=True)

    def _send_aggregated_message(self, series_id: str, expected_timer: Optional[Any] = None):
        """发送聚合后的TV剧集消息"""
        try:
            with self._lock:
                if expected_timer is not None and self._aggregate_timers.get(series_id) is not expected_timer:
                    return
                events = self._pending_messages.pop(series_id, [])
                self._aggregate_timers.pop(series_id, None)

            if not events: return

            first_event = events[0]
            events_count = self._get_episode_count(events)
            is_multiple_episodes = events_count > 1
            episodes_detail = self._merge_continuous_episodes(events)
            
            logger.info(f"🚀 聚合窗口期满，开始打包发送 [{series_id}]。本批次包含 {events_count} 个文件，最终明细: {episodes_detail or '未知'}")

            payload_str = f"{getattr(first_event, 'json_object', '')} {getattr(first_event, 'item_path', '')} {getattr(first_event, 'item_name', '')}"
            tmdb_pattern = r'[\[{](?:tmdbid|tmdb)[=-](\d+)[\]}]'
            if match := re.search(tmdb_pattern, payload_str, re.IGNORECASE):
                first_event.tmdb_id = match.group(1)
            else:
                media_service = self.service_info(name=getattr(first_event, 'server_name', ''))
                if media_service:
                    service = media_service.instance
                    item_id = getattr(first_event, 'item_id', None)
                    if service and item_id:
                        info: MediaServerItem = service.get_iteminfo(item_id)
                        if info and info.tmdbid:
                            first_event.tmdb_id = info.tmdbid

            episode_pairs = self._extract_episode_pairs(first_event)
            s_id, e_id = episode_pairs[0] if episode_pairs else self._extract_season_episode(first_event)[:2]
            tmdb_info = None

            def safe_get_overview(tmdb_data, target_ep_id, multiple_eps):
                if getattr(first_event, 'overview', None):
                    return first_event.overview
                elif tmdb_data:
                    if multiple_eps:
                        return tmdb_data.get('overview', '')
                    else:
                        episodes = tmdb_data.get('episodes', [])
                        if episodes and target_ep_id is not None:
                            for ep_data in episodes:
                                if str(ep_data.get('episode_number')) == str(target_ep_id):
                                    return ep_data.get('overview', tmdb_data.get('overview', ''))
                        return tmdb_data.get('overview', '')
                return ''

            def send_fallback_msg():
                """========= 降级逻辑：未识别出完整元数据时执行 ========="""
                show_name = getattr(first_event, 'item_name', '未知剧集')
                message_title = f"📺 {self._webhook_actions.get(getattr(first_event, 'event', ''), '新入库')}剧集：{show_name}"
                if is_multiple_episodes:
                    message_title += f" {events_count}集"

                message_texts = []
                message_texts.append(f"⏰ 入库时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

                subscribers_fb = self._get_subscriber_names(getattr(first_event, 'tmdb_id', None))
                message_texts.append(f"🙋‍♂️ 订阅用户：{subscribers_fb}")

                if episodes_detail:
                    message_texts.append(f"📺 季集：{episodes_detail}")

                message_content = "\n".join(message_texts)
                fallback_img = getattr(first_event, 'image_url', None) or self._webhook_images.get(getattr(first_event, 'channel', ''))
                
                fallback_img = self._get_cached_image_url(fallback_img)

                play_link = None
                if self._add_play_link:
                    play_link = self._get_play_link(first_event)

                self.post_message(mtype=NotificationType.MediaServer,
                                  title=message_title, text=message_content, image=fallback_img, link=play_link)
                
                logger.info(f"✅ 聚合通知已降级发送: {message_title}")
                self._record_history(message_title, episodes_detail or "多集降级聚合")

            if not getattr(first_event, 'tmdb_id', None):
                send_fallback_msg()
                return

            # ========= 正常聚合逻辑 =========
            if str(getattr(first_event, 'item_type', '')).upper() in ["TV", "SHOW", "EPISODE", "SERIES", "SEASON"]:
                try:
                    tmdb_info = self._get_tmdb_info(
                        tmdb_id=first_event.tmdb_id,
                        mtype=MediaType.TV,
                        season=s_id
                    )
                except Exception:
                    pass

            overview = safe_get_overview(tmdb_info, e_id, is_multiple_episodes)

            show_name = getattr(first_event, 'item_name', '未知剧集')
            try:
                json_object = getattr(first_event, 'json_object', None)
                if json_object and isinstance(json_object, dict):
                    item = json_object.get("Item", {})
                    series_name = item.get("SeriesName")
                    if series_name:
                        show_name = series_name
            except Exception:
                pass

            message_title = f"📺 {self._webhook_actions.get(getattr(first_event, 'event', ''), '新入库')}剧集：{show_name}"
            if is_multiple_episodes:
                message_title += f" {events_count}集"

            message_texts = []
            message_texts.append(f"⏰ 入库时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

            subscribers = self._get_subscriber_names(first_event.tmdb_id)
            message_texts.append(f"🙋‍♂️ 订阅用户：{subscribers}")
            
            if episodes_detail:
                message_texts.append(f"📺 季集：{episodes_detail}")

            cat = None
            if tmdb_info:
                try:
                    if tmdb_info.get('media_type') == MediaType.TV:
                        cat = self.category.get_tv_category(tmdb_info)
                    else:
                        cat = self.category.get_movie_category(tmdb_info)
                except Exception:
                    pass

            if cat:
                message_texts.append(f"📚 分类：{cat}")

            if tmdb_info and tmdb_info.get('vote_average'):
                try:
                    rating = round(float(tmdb_info.get('vote_average')), 1)
                    message_texts.append(f"⭐ 评分：{rating}/10")
                except (ValueError, TypeError):
                    pass

                genres = tmdb_info.get('genres', [])
                if genres:
                    try:
                        genres_list = []
                        for genre in genres[:3]:
                            if isinstance(genre, dict):
                                genre_name = genre.get('name', '')
                                if genre_name: genres_list.append(genre_name)
                            else:
                                if str(genre): genres_list.append(str(genre))
                        if genres_list:
                            message_texts.append(f"🎭 类型：{'、'.join(genres_list)}")
                    except Exception:
                        pass

            if overview:
                if len(overview) > 100:
                    overview = overview[:100] + "..."
                message_texts.append(f"📖 剧情：{overview}")

            message_content = "\n".join(message_texts)

            image_url = None

            if tmdb_info:
                try:
                    tmdb_domain = settings.TMDB_IMAGE_DOMAIN or "image.tmdb.org"
                    if not tmdb_domain.startswith("http"):
                        tmdb_domain = f"https://{tmdb_domain}"
                    tmdb_domain = tmdb_domain.rstrip("/")
                    
                    if not is_multiple_episodes and tmdb_info.get('poster_path'):
                        original_url = f"{tmdb_domain}/t/p/original{tmdb_info.get('poster_path')}"
                        image_url = self._get_cached_image_url(original_url)
                    elif is_multiple_episodes and tmdb_info.get('backdrop_path'):
                        original_url = f"{tmdb_domain}/t/p/original{tmdb_info.get('backdrop_path')}"
                        image_url = self._get_cached_image_url(original_url)
                except Exception as e:
                    logger.debug(f"聚合消息提取TMDB代理链接出错: {e}")

            if not image_url:
                raw_image = getattr(first_event, 'image_url', None) or self._webhook_images.get(getattr(first_event, 'channel', ''))
                image_url = self._get_cached_image_url(raw_image)

            play_link = None
            if self._add_play_link:
                play_link = self._get_play_link(first_event)

            self.post_message(mtype=NotificationType.MediaServer,
                              title=message_title, text=message_content, image=image_url, link=play_link)
            
            logger.info(f"✅ 完美聚合通知已送达企业微信：{message_title}")
            self._record_history(message_title, episodes_detail or "已聚合")

        except Exception as e:
            logger.error(f"发送聚合消息异常: {str(e)}", exc_info=True)

    def _merge_continuous_episodes(self, events: List[WebhookEventInfo]) -> str:
        """合并真实季集范围，汇总型 Series 事件不再只显示第一集。"""
        seasons = {}
        full_seasons = set()
        has_summary = False
        for event in events:
            pairs = self._extract_episode_pairs(event)
            for season, episode in pairs:
                seasons.setdefault(season, set()).add(episode)
            if pairs:
                continue
            payload = getattr(event, "json_object", None) or {}
            item = (payload.get("Item") or payload.get("Metadata") or {}) if isinstance(payload, dict) else {}
            item_type = str(item.get("Type") or getattr(event, "media_type", "")
                            or getattr(event, "item_type", "")).upper()
            if item_type == "SEASON":
                season = item.get("IndexNumber")
                if season is None:
                    season, _, _ = self._extract_season_episode(event)
                if season is not None:
                    full_seasons.add(int(season))
            elif item_type == "SERIES":
                has_summary = True
        details = []
        for season in sorted(set(seasons).union(full_seasons)):
            if season in full_seasons:
                details.append(f"S{season:02d}全季")
                continue
            episodes = sorted(seasons[season])
            start = end = episodes[0]
            for episode in episodes[1:] + [None]:
                if episode is not None and episode == end + 1:
                    end = episode
                    continue
                detail = f"S{season:02d}E{start:02d}"
                if start != end:
                    detail += f"-E{end:02d}"
                details.append(detail)
                start = end = episode
        return ", ".join(details) or ("剧集更新（未提供集数）" if has_summary else "")

    def __add_element(self, key, duration=DEFAULT_EXPIRATION_TIME):
        with self._lock:
            self._webhook_msg_keys[key] = time.time() + duration

    def __remove_element(self, key):
        with self._lock:
            self._webhook_msg_keys.pop(key, None)

    def __clean_expired_elements(self):
        try:
            with self._lock:
                current_time = time.time()
                expired_keys = [k for k, v in self._webhook_msg_keys.items() if v <= current_time]
                for k in expired_keys:
                    self._webhook_msg_keys.pop(k, None)
        except Exception:
            pass

    def __get_elements(self):
        try:
            with self._lock:
                current_time = time.time()
                valid_keys = []
                expired_keys = []

                for key, expiration_time in self._webhook_msg_keys.items():
                    if expiration_time > current_time:
                        valid_keys.append(key)
                    else:
                        expired_keys.append(key)

                for key in expired_keys:
                    self._webhook_msg_keys.pop(key, None)

                return valid_keys
        except Exception:
            return []

    def _get_play_link(self, event_info: WebhookEventInfo) -> Optional[str]:
        try:
            server_name = getattr(event_info, 'server_name', None)
            item_id = getattr(event_info, 'item_id', None)

            if not item_id: return None

            if server_name:
                service = self.service_infos().get(server_name) if self.service_infos() else None
                if service:
                    try:
                        return service.instance.get_play_url(item_id)
                    except Exception:
                        pass

            channel = getattr(event_info, 'channel', None)
            if channel:
                try:
                    services = MediaServerHelper().get_services(type_filter=channel)
                    for service in services.values():
                        try:
                            play_link = service.instance.get_play_url(item_id)
                            if play_link: return play_link
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception:
            pass
        return None

    @cached(
        region="MediaServerMsg",
        maxsize=128,
        ttl=600,
        skip_none=True,
        skip_empty=False
    )
    def _get_tmdb_info(self, tmdb_id: str, mtype: MediaType, season: Optional[int] = None):
        try:
            if mtype == MediaType.MOVIE:
                result = self.chain.tmdb_info(tmdbid=tmdb_id, mtype=mtype)
                if not result or not isinstance(result, dict): return None
                return result
            else:
                tmdb_info = self.chain.tmdb_info(tmdbid=tmdb_id, mtype=mtype, season=season)
                tmdb_info2 = self.chain.tmdb_info(tmdbid=tmdb_id, mtype=mtype)
                
                res = {}
                if isinstance(tmdb_info, dict): res.update(tmdb_info)
                if isinstance(tmdb_info2, dict): res.update(tmdb_info2)
                
                return res if res else None
        except Exception as e:
            logger.debug(f"获取TMDB信息内部异常: {e}")
            return None

    def stop_service(self):
        try:
            with self._lock:
                pending_series_ids = list(self._pending_messages.keys())
            for series_id in pending_series_ids:
                try:
                    self._send_aggregated_message(series_id)
                except Exception:
                    pass

            with self._lock:
                for timer in self._aggregate_timers.values():
                    try:
                        timer.cancel()
                    except Exception:
                        pass
                self._aggregate_timers.clear()
                self._pending_messages.clear()

            try:
                self._get_tmdb_info.cache_clear()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"插件停止时发生错误: {str(e)}")
