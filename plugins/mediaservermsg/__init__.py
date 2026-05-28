import time
import threading
from typing import Any, List, Dict, Tuple

from app.core.event import eventmanager, Event
from app.log import logger
from app.modules.emby import Emby
from app.modules.jellyfin import Jellyfin
from app.modules.plex import Plex
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType, MediaType, MediaImageType, NotificationType
from app.utils.web import WebUtils


class MediaServerMsg(_PluginBase):
    # 插件名称
    plugin_name = "媒体库服务器通知"
    # 插件描述
    plugin_desc = "发送Emby/Jellyfin/Plex服务器的播放、入库等通知消息。"
    # 插件图标
    plugin_icon = "mediaplay.png"
    # 插件版本
    plugin_version = "1.3"
    # 插件作者
    plugin_author = "wenzhanquan"
    # 作者主页
    author_url = "https://github.com/wenzhanquan"
    # 插件配置项ID前缀
    plugin_config_prefix = "mediaservermsg_"
    # 加载顺序
    plugin_order = 14
    # 可使用的用户级别
    auth_level = 1

    # 对像
    plex = None
    emby = None
    jellyfin = None

    # 私有属性
    _enabled = False
    _types = []
    _webhook_msg_keys = {}

    # ==========================
    # 自定义新增：入库消息聚合缓存与定时器
    # ==========================
    _library_cache = {}
    _library_timer = None

    # 拼装消息内容
    _webhook_actions = {
        "library.new": "新入库",
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
    _webhook_images = {
        "emby": "https://emby.media/notificationicon.png",
        "plex": "https://www.plex.tv/wp-content/uploads/2022/04/new-logo-process-lines-gray.png",
        "jellyfin": "https://play-lh.googleusercontent.com/SCsUK3hCCRqkJbmLDctNYCfehLxsS4ggD1ZPHIFrrAN1Tn9yhjmGMPep2D9lMaaa9eQi"
    }

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._types = config.get("types") or []
            if self._enabled:
                self.emby = Emby()
                self.plex = Plex()
                self.jellyfin = Jellyfin()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
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
                                'props': {
                                    'cols': 12,
                                },
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
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '需要设置媒体服务器Webhook，回调相对路径为 /api/v1/webhook?token=moviepilot（3001端口），其中 moviepilot 为设置的 API_TOKEN。'
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
            "types": []
        }

    def get_page(self) -> List[dict]:
        pass

    # ==========================
    # 自定义新增：定时发送聚合后的入库通知
    # ==========================
    def _send_library_msg(self):
        """定时器触发发送聚合入库消息"""
        for key, data in self._library_cache.items():
            ep_list = data.get("episodes", [])
            msg_texts = data.get("texts", [])
            
            if ep_list:
                # 对收集到的集数去重并排序，格式化为 E01, E02 的形式
                try:
                    ep_str = ", ".join([f"E{str(e).zfill(2)}" for e in sorted(set(int(e) for e in ep_list))])
                except Exception:
                    ep_str = ", ".join([f"E{str(e).zfill(2)}" for e in sorted(set(ep_list))])
                    
                # 插入到通知内容的最上方
                msg_texts.insert(0, f"入库集数：{ep_str}")
                
            message_content = "\n".join(msg_texts)
            
            # 使用系统的 MediaServer 类型发信，因为我们已经延迟处理并合并成了单条消息，
            # 系统核心模块此时不会再触发多集防刷屏折叠机制。
            self.post_message(mtype=NotificationType.MediaServer,
                              title=data["title"], 
                              text=message_content, 
                              image=data["image"], 
                              link=data["link"])
        
        # 发送完毕后清空缓存
        self._library_cache = {}

    @eventmanager.register(EventType.WebhookMessage)
    def send(self, event: Event):
        """
        发送通知消息
        """
        if not self._enabled:
            return

        event_info: WebhookEventInfo = event.event_data
        if not event_info:
            return

        # 不在支持范围不处理
        if not self._webhook_actions.get(event_info.event):
            return

        # 不在选中范围不处理
        msgflag = False
        for _type in self._types:
            if event_info.event in _type.split("|"):
                msgflag = True
                break
        if not msgflag:
            logger.info(f"未开启 {event_info.event} 类型的消息通知")
            return

        expiring_key = f"{event_info.item_id}-{event_info.client}-{event_info.user_name}"
        # 过滤停止播放重复消息
        if str(event_info.event) == "playback.stop" and expiring_key in self._webhook_msg_keys.keys():
            # 刷新过期时间
            self.__add_element(expiring_key)
            return

        # 消息标题
        if event_info.item_type in ["TV", "SHOW"]:
            message_title = f"{self._webhook_actions.get(event_info.event)}剧集 {event_info.item_name}"
        elif event_info.item_type == "MOV":
            message_title = f"{self._webhook_actions.get(event_info.event)}电影 {event_info.item_name}"
        elif event_info.item_type == "AUD":
            message_title = f"{self._webhook_actions.get(event_info.event)}有声书 {event_info.item_name}"
        else:
            message_title = f"{self._webhook_actions.get(event_info.event)}"

        # 消息内容
        message_texts = []
        if event_info.user_name:
            message_texts.append(f"用户：{event_info.user_name}")
        if event_info.device_name:
            message_texts.append(f"设备：{event_info.client} {event_info.device_name}")
        if event_info.ip:
            message_texts.append(f"IP地址：{event_info.ip} {WebUtils.get_location(event_info.ip)}")
        if event_info.percentage:
            percentage = round(float(event_info.percentage), 2)
            message_texts.append(f"进度：{percentage}%")
        if event_info.overview:
            message_texts.append(f"剧情：{event_info.overview}")
        message_texts.append(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))}")

        # 常规消息内容（不含集数的普通文本拼接）
        message_content = "\n".join(message_texts)

        # 消息图片
        image_url = event_info.image_url
        # 查询剧集图片
        if (event_info.tmdb_id
                and event_info.season_id
                and event_info.episode_id):
            specific_image = self.chain.obtain_specific_image(
                mediaid=event_info.tmdb_id,
                mtype=MediaType.TV,
                image_type=MediaImageType.Backdrop,
                season=event_info.season_id,
                episode=event_info.episode_id
            )
            if specific_image:
                image_url = specific_image
        # 使用默认图片
        if not image_url:
            image_url = self._webhook_images.get(event_info.channel)

        # 获取链接地址
        if event_info.channel == "emby":
            play_link = self.emby.get_play_url(event_info.item_id)
        elif event_info.channel == "plex":
            play_link = self.plex.get_play_url(event_info.item_id)
        elif event_info.channel == "jellyfin":
            play_link = self.jellyfin.get_play_url(event_info.item_id)
        else:
            play_link = None

        if str(event_info.event) == "playback.stop":
            # 停止播放消息，添加到过期字典
            self.__add_element(expiring_key)
        if str(event_info.event) == "playback.start":
            # 开始播放消息，删除过期字典
            self.__remove_element(expiring_key)

        # ==========================
        # 自定义新增：拦截入库消息并进行缓存聚合
        # ==========================
        if str(event_info.event) == "library.new":
            season = event_info.season_id
            episode = event_info.episode_id
            
            # 如果这是一个剧集（有确定的集数）
            if episode is not None and str(episode).isdigit():
                # 按照媒体渠道、剧集名称、季数做 Key，这样 S01 和 S02 的更新不会混在一起
                cache_key = f"{event_info.channel}_{event_info.item_name}_S{season}"
                
                if cache_key not in self._library_cache:
                    # 标题中带上第几季，比如“新入库剧集 权力的游戏 S01”
                    season_str = f" S{str(season).zfill(2)}" if season is not None else ""
                    self._library_cache[cache_key] = {
                        "title": message_title + season_str,
                        "texts": message_texts,  # 保存基础内容字典
                        "image": image_url,
                        "link": play_link,
                        "episodes": []
                    }
                
                # 记录这一集
                ep_int = int(episode)
                if ep_int not in self._library_cache[cache_key]["episodes"]:
                    self._library_cache[cache_key]["episodes"].append(ep_int)
                
                # 重新启动 10秒 定时器，这意味着：
                # 如果系统在 10 秒内连续推入了第 1、2、3 集，定时器会被一直重置，
                # 直到最后一次推送 10 秒后，才统一打包把 E01,E02,E03 一起发出去。
                if self._library_timer:
                    self._library_timer.cancel()
                self._library_timer = threading.Timer(10.0, self._send_library_msg)
                self._library_timer.start()
                return  # 拦截掉原本默认的单条发送
        # ==========================

        # 发送常规消息 (播放、停止等正常走这里)
        self.post_message(mtype=NotificationType.MediaServer,
                          title=message_title, text=message_content, image=image_url, link=play_link)

    def __add_element(self, key, duration=600):
        expiration_time = time.time() + duration
        # 如果元素已经存在，更新其过期时间
        self._webhook_msg_keys[key] = expiration_time

    def __remove_element(self, key):
        self._webhook_msg_keys = {k: v for k, v in self._webhook_msg_keys.items() if k != key}

    def __get_elements(self):
        current_time = time.time()
        # 过滤掉过期的元素
        self._webhook_msg_keys = {k: v for k, v in self._webhook_msg_keys.items() if v > current_time}
        return list(self._webhook_msg_keys.keys())

    def stop_service(self):
        """
        退出插件
        """
        if self._library_timer:
            self._library_timer.cancel()
