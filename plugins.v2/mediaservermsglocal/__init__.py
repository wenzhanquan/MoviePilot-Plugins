import re
import threading
import time
from typing import Any, List, Dict, Tuple, Optional
from urllib.parse import quote

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
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/mediaservermsglocal/icon.png"
    plugin_version = "1.0.11"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
    plugin_config_prefix = "mediaservermsglocal_"
    plugin_order = 14
    auth_level = 1

    # Webhook事件映射配置
    _webhook_actions = {
        "library.new": "新入库",
        "system.notificationtest": "测试",
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
        # 初始化并发安全锁与独立内存字典，防止热重载引发的幽灵数据逃逸
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
        """获取媒体服务器信息服务信息"""
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
        """根据名称获取特定媒体服务器服务信息"""
        service_infos = self.service_infos() or {}
        return service_infos.get(name)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

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

    def get_page(self) -> List[dict]:
        return []

    @eventmanager.register(EventType.WebhookMessage)
    def send(self, event: Event):
        """发送通知消息主入口函数"""
        try:
            if not self._enabled:
                return

            event_info: WebhookEventInfo = getattr(event, 'event_data', None)
            if not event_info:
                return

            # 先行执行过期缓存池清理，防止挂机导致的内存泄露
            self.__clean_expired_elements()

            event_type = getattr(event_info, 'event', None)
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
                    # 严防大小写敏感和特殊命名漏网之鱼
                    if str(item_type).upper() not in ["TV", "SHOW", "EPISODE", "SERIES"]: return False
                    
                    json_object = getattr(event_info, 'json_object', None)
                    if json_object and not isinstance(json_object, dict): return False
                    return True
                except Exception:
                    return False

            # 遇到剧集入库走聚合流程
            if should_aggregate_tv():
                series_id = self._get_series_id(event_info)
                if series_id:
                    self._aggregate_tv_episodes(series_id, event_info)
                    return

            # 以下是非聚合流程（电影、有声书、单集播放等事件）
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
            item_name = getattr(event_info, 'item_name', '')

            message_title = ""
            event_action = self._webhook_actions.get(event_type, event_type)
            
            # 丰富消息标题 Emoji
            if item_type in ["TV", "SHOW", "EPISODE", "SERIES"]:
                message_title = f"📺 {event_action}剧集：{item_name}"
            elif item_type in ["MOV", "MOVIE"]:
                message_title = f"🎬 {event_action}电影：{item_name}"
            elif item_type in ["AUD", "AUDIO"]:
                message_title = f"🎧 {event_action}有声书：{item_name}"
            else:
                message_title = f"🔔 {event_action}：{item_name}"

            message_texts = []
            
            # 添加时间及区分 Emoji
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

            overview = getattr(event_info, 'overview', None)
            if overview:
                if len(overview) > 100:
                    overview = overview[:100] + "..."
                message_texts.append(f"📖 剧情：{overview}")

            message_content = "\n".join(message_texts)

            image_url = getattr(event_info, 'image_url', None)
            tmdb_id = getattr(event_info, 'tmdb_id', None)
            season_id, episode_id, _ = self._extract_season_episode(event_info)

            if item_type in ["MOV", "MOVIE"] and tmdb_id:
                try:
                    image_url = self.chain.obtain_specific_image(
                        mediaid=tmdb_id,
                        mtype=MediaType.MOVIE,
                        image_type=MediaImageType.Poster
                    )
                except Exception:
                    pass
            elif tmdb_id:
                try:
                    specific_image = self.chain.obtain_specific_image(
                        mediaid=tmdb_id,
                        mtype=MediaType.TV,
                        image_type=MediaImageType.Backdrop,
                        season=season_id,
                        episode=episode_id
                    )
                    if specific_image:
                        image_url = specific_image
                except Exception:
                    pass

            # ========================
            # 核心代理：套接 ImageLocalCache 专属链接
            # ========================
            proxy_base = f"{settings.APP_DOMAIN.rstrip('/')}/api/v1/plugin/ImageLocalCache/img?url=" if settings.APP_DOMAIN else ""
            if image_url and proxy_base:
                try:
                    if "/api/v1/plugin/ImageLocalCache/img?url=" not in image_url:
                        image_url = f"{proxy_base}{quote(image_url, safe='')}"
                except Exception as e:
                    logger.debug(f"图片本地缓存代理处理失败: {str(e)}")

            if not image_url:
                channel = getattr(event_info, 'channel', '')
                image_url = self._webhook_images.get(channel)

            play_link = None
            if self._add_play_link:
                play_link = self._get_play_link(event_info)

            if str(event_type) == "playback.stop":
                self.__add_element(expiring_key)
            if str(event_type) == "playback.start":
                self.__remove_element(expiring_key)

            self.post_message(mtype=NotificationType.MediaServer,
                              title=message_title, text=message_content, image=image_url, link=play_link)

        except Exception as e:
            logger.error(f"处理Webhook事件时发生错误: {str(e)}", exc_info=True)

    def _get_series_id(self, event_info: WebhookEventInfo) -> Optional[str]:
        """获取剧集ID，强制剥离 TMDB ID 以防假性不聚合"""
        try:
            # 1. 优先尝试从路径强制剥离 TMDB ID，这是最稳固的同剧集特征！
            item_path = getattr(event_info, 'item_path') or ''
            tmdb_match = re.search(r'[\[{](?:tmdbid|tmdb)[=-](\d+)[\]}]', str(item_path), re.IGNORECASE)
            if tmdb_match:
                return f"tmdb_{tmdb_match.group(1)}"

            # 2. 尝试提取标准的 SeriesId
            json_object = getattr(event_info, 'json_object', None)
            if json_object and isinstance(json_object, dict):
                item = json_object.get("Item") or json_object.get("Metadata") or json_object
                if isinstance(item, dict):
                    series_id = item.get("SeriesId") or item.get("SeriesName") or item.get("ParentId")
                    if series_id:
                        return str(series_id)

            # 3. 剥离掉 S01E01 后的纯名字，强制把散落的单集聚类
            item_name = getattr(event_info, 'item_name') or ''
            name_match = re.split(r'[Ss]\d+\s*[Ee]\d+|第\s*\d+\s*季', str(item_name))
            if name_match and name_match[0].strip():
                return name_match[0].strip()

            # 4. 万无一失的最后兜底
            fallback_id = getattr(event_info, "series_id", None)
            if fallback_id:
                return str(fallback_id)
                
        except Exception:
            pass
        return "unknown_series_fallback"

    def _extract_season_episode(self, event: WebhookEventInfo) -> Tuple[Optional[int], Optional[int], str]:
        """超级提取引擎：从 Webhook 对象中安全剥离季号、集号和单集名称"""
        try:
            season = getattr(event, "season_id", None) or getattr(event, "item_season", None) or getattr(event, "season", None)
            episode = getattr(event, "episode_id", None) or getattr(event, "item_episode", None) or getattr(event, "episode", None)
            episode_name = getattr(event, "item_name", "") or ""

            if str(season).strip() in ("", "None", "null"): season = None
            if str(episode).strip() in ("", "None", "null"): episode = None

            if hasattr(event, 'json_object') and isinstance(event.json_object, dict):
                item = event.json_object.get("Item") or event.json_object.get("Metadata") or event.json_object
                if isinstance(item, dict):
                    if season is None: 
                        season = item.get("ParentIndexNumber") or item.get("SeasonNumber") or item.get("parentIndex")
                    if episode is None: 
                        episode = item.get("IndexNumber") or item.get("EpisodeNumber") or item.get("index")
                    if not episode_name or episode_name == "未知剧集":
                        episode_name = item.get("Name") or item.get("EpisodeName") or item.get("title") or ""

            # 深层降维正则强刷
            if season is None or episode is None:
                # 安全获取属性并净化 None 值
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

            # 安全的提取纯数字函数，防御 int("S01") 引发崩溃
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
        """聚合TV剧集结入库消息（带读写锁隔离保护）"""
        try:
            if not series_id: return

            with self._lock:
                if series_id not in self._pending_messages:
                    self._pending_messages[series_id] = []
                self._pending_messages[series_id].append(event_info)

                if series_id in self._aggregate_timers:
                    try:
                        self._aggregate_timers[series_id].cancel()
                    except Exception:
                        pass

                timer = threading.Timer(self._aggregate_time, self._send_aggregated_message, [series_id])
                self._aggregate_timers[series_id] = timer
                timer.start()
        except Exception as e:
            logger.error(f"聚合处理异常: {str(e)}", exc_info=True)

    def _send_aggregated_message(self, series_id: str):
        """发送聚合后的TV剧集消息"""
        try:
            # 加锁从内存中安全弹出数据池
            with self._lock:
                events = self._pending_messages.pop(series_id, [])
                self._aggregate_timers.pop(series_id, None)

            if not events: return

            first_event = events[0]
            events_count = len(events)
            is_multiple_episodes = events_count > 1

            # 智能提取 tmdb_id
            item_path = getattr(first_event, 'item_path', None)
            tmdb_pattern = r'[\[{](?:tmdbid|tmdb)[=-](\d+)[\]}]'
            if item_path and (match := re.search(tmdb_pattern, str(item_path))):
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

            s_id, e_id, _ = self._extract_season_episode(first_event)
            tmdb_info = None

            def safe_get_overview(tmdb_data, target_ep_id, multiple_eps):
                """安全提取剧情简述"""
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
                    message_title += f" {events_count}个文件"

                message_texts = []
                message_texts.append(f"⏰ 入库时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

                episodes_detail = self._merge_continuous_episodes(events)
                if episodes_detail:
                    message_texts.append(f"📺 季集：{episodes_detail}")

                message_content = "\n".join(message_texts)
                fallback_img = getattr(first_event, 'image_url', None) or self._webhook_images.get(getattr(first_event, 'channel', ''))
                
                proxy_b = f"{settings.APP_DOMAIN.rstrip('/')}/api/v1/plugin/ImageLocalCache/img?url=" if settings.APP_DOMAIN else ""
                if fallback_img and proxy_b and "/api/v1/plugin/ImageLocalCache/img?url=" not in fallback_img:
                    fallback_img = f"{proxy_b}{quote(fallback_img, safe='')}"

                play_link = None
                if self._add_play_link:
                    play_link = self._get_play_link(first_event)

                self.post_message(mtype=NotificationType.MediaServer,
                                  title=message_title, text=message_content, image=fallback_img, link=play_link)

            # 如果没有提取到 TMDB_ID，直接进降级
            if not getattr(first_event, 'tmdb_id', None):
                send_fallback_msg()
                return

            # ========= 正常聚合逻辑 =========
            if str(getattr(first_event, 'item_type', '')).upper() in ["TV", "SHOW", "EPISODE", "SERIES"]:
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
                message_title += f" {events_count}个文件"

            message_texts = []
            message_texts.append(f"⏰ 入库时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")
            
            episodes_detail = self._merge_continuous_episodes(events)
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

            # 通过TMDB提取并清洗原生图片，同时注入ImageLocalCache地址
            if tmdb_info and settings.APP_DOMAIN:
                try:
                    tmdb_domain = settings.TMDB_IMAGE_DOMAIN or "image.tmdb.org"
                    if not tmdb_domain.startswith("http"):
                        tmdb_domain = f"https://{tmdb_domain}"
                    tmdb_domain = tmdb_domain.rstrip("/")
                    
                    if not is_multiple_episodes and tmdb_info.get('poster_path'):
                        original_url = f"{tmdb_domain}/t/p/original{tmdb_info.get('poster_path')}"
                        image_url = f"{settings.APP_DOMAIN.rstrip('/')}/api/v1/plugin/ImageLocalCache/img?url={quote(original_url, safe='')}"
                    elif is_multiple_episodes and tmdb_info.get('backdrop_path'):
                        original_url = f"{tmdb_domain}/t/p/original{tmdb_info.get('backdrop_path')}"
                        image_url = f"{settings.APP_DOMAIN.rstrip('/')}/api/v1/plugin/ImageLocalCache/img?url={quote(original_url, safe='')}"
                except Exception as e:
                    logger.debug(f"聚合消息提取TMDB代理链接出错: {e}")

            # 若未从 TMDB 拿到图，回退取 Webhook 原生附带的 image_url 并套一层代理
            if not image_url:
                raw_image = getattr(first_event, 'image_url', None) or self._webhook_images.get(getattr(first_event, 'channel', ''))
                proxy_b = f"{settings.APP_DOMAIN.rstrip('/')}/api/v1/plugin/ImageLocalCache/img?url=" if settings.APP_DOMAIN else ""
                if raw_image and proxy_b and "/api/v1/plugin/ImageLocalCache/img?url=" not in raw_image:
                    image_url = f"{proxy_b}{quote(raw_image, safe='')}"
                else:
                    image_url = raw_image

            play_link = None
            if self._add_play_link:
                play_link = self._get_play_link(first_event)

            self.post_message(mtype=NotificationType.MediaServer,
                              title=message_title, text=message_content, image=image_url, link=play_link)

        except Exception as e:
            logger.error(f"发送聚合消息异常: {str(e)}", exc_info=True)

    def _merge_continuous_episodes(self, events: List[WebhookEventInfo]) -> str:
        """合并连续的集数信息，并使用统一引擎彻底去重和补漏"""
        season_episodes = {}

        for event in events:
            try:
                season_key, episode_key, episode_name = self._extract_season_episode(event)
                if episode_key is None:
                    continue

                if season_key not in season_episodes:
                    season_episodes[season_key] = {}
                    
                if episode_key not in season_episodes[season_key]:
                    season_episodes[season_key][episode_key] = episode_name or ""
            except Exception as e:
                logger.debug(f"处理集数合并单条记录异常: {e}")
                continue

        merged_details = []
        try:
            for season in sorted(season_episodes.keys()):
                ep_dict = season_episodes[season]
                if not ep_dict:
                    continue
                
                sorted_eps = sorted(ep_dict.keys())
                start = sorted_eps[0]
                end = sorted_eps[0]

                for current in sorted_eps[1:]:
                    if current == end + 1:
                        end = current
                    else:
                        if start == end:
                            name = ep_dict.get(start, "")
                            name_str = f" {name}" if name and not str(start) in name else ""
                            merged_details.append(f"S{season:02d}E{start:02d}{name_str}")
                        else:
                            merged_details.append(f"S{season:02d}E{start:02d}-E{end:02d}")
                        start = current
                        end = current

                if start == end:
                    name = ep_dict.get(start, "")
                    name_str = f" {name}" if name and not str(start) in name else ""
                    merged_details.append(f"S{season:02d}E{start:02d}{name_str}")
                else:
                    merged_details.append(f"S{season:02d}E{start:02d}-E{end:02d}")
                    
        except Exception as err:
            logger.error(f"合并集数信息时出错: {str(err)}")
            fallback = []
            for evt_backup in events:
                try:
                    s_int, e_int, _ = self._extract_season_episode(evt_backup)
                    if e_int is not None:
                        fallback.append(f"S{s_int:02d}E{e_int:02d}")
                except Exception:
                    pass
            return ", ".join(fallback)

        return ", ".join(merged_details)

    def __add_element(self, key, duration=DEFAULT_EXPIRATION_TIME):
        with self._lock:
            self._webhook_msg_keys[key] = time.time() + duration

    def __remove_element(self, key):
        with self._lock:
            self._webhook_msg_keys.pop(key, None)

    def __clean_expired_elements(self):
        """【防御机制】：清理过期播放标记，防止运行长达数月后内存字典无限膨胀导致 OOM"""
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
        """【核心修复】：强制字典对象合并，绝对防御 TMDB 宕机返回 None 引发的致命 TypeError 崩溃"""
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
            # 清空缓存并安全加锁弹出剩余作业
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


# ============================================================
# 模块级 patch（彻底解决直接读属性的搜索盲区）
# 该代码块不能删除，这是保证通过本地网络拉取图片的“命根子”！
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
        
        if isinstance(getattr(self_obj, "poster_path", None), str):
            orig = self_obj.poster_path
            self_obj.poster_path = _wrap_url(orig)
            if orig != self_obj.poster_path:
                replaced = True
                
        if isinstance(getattr(self_obj, "backdrop_path", None), str):
            orig = self_obj.backdrop_path
            self_obj.backdrop_path = _wrap_url(orig)
            if orig != self_obj.backdrop_path:
                replaced = True
                
        if replaced:
            title = getattr(self_obj, "title", "") or getattr(self_obj, "original_title", "未知媒体")
            # 静默后台注入，不再频繁打印影响主日志流
            pass

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
                    pass
                
        Notification.__init__ = _patched_notification_init
    except Exception as e:
        logger.debug(f"拦截 Notification 失败: {e}")

# 模块导入时立即安全执行 patch
_module_patch()