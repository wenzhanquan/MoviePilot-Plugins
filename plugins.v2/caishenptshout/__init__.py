from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger

from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils
from app.db import Session, get_db
from app.db.models.site import Site
from app.core.event import Event as ManagerEvent, eventmanager
from app.schemas.types import EventType, NotificationType


class CaishenPTShout(_PluginBase):
    """
    财神PT喊话插件。
    定时在财神PT站点群聊区发送喊话消息，如"财神，求上传"等。
    """

    plugin_name = "财神PT喊话"
    plugin_desc = "定时在财神PT站点群聊区发送喊话消息。"
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/caishenptshout/caishen.png"
    plugin_version = "1.0.0"
    plugin_label = "站点工具"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
    plugin_config_prefix = "caishenptshout_"
    plugin_order = 99
    auth_level = 1

    # 配置项
    _enabled: bool = False
    _cron: str = ""
    _message: str = ""
    _onlyonce: bool = False
    _clearcron: bool = False

    # 站点记录
    _site_name: str = ""
    _site_domain: str = ""
    _site_cookie: str = ""

    def init_plugin(self, config: dict = None) -> None:
        """
        根据插件配置初始化运行状态。
        """
        self.stop_service()

        self._enabled = False
        self._message = ""
        self._cron = ""
        self._onlyonce = False
        self._clearcron = False

        if config:
            self._enabled = bool(config.get("enabled"))
            self._cron = str(config.get("cron") or "")
            self._message = str(config.get("message") or "财神，求上传")
            self._onlyonce = bool(config.get("onlyonce"))
            self._clearcron = bool(config.get("clearcron"))

        # 清理定时任务
        if self._clearcron:
            self._clearcron = False
            self._cron = ""
            self.__update_config()
            logger.info("财神PT喊话插件定时任务已清除")

        if not self._enabled:
            logger.info("财神PT喊话插件未启用")
            return

        # 获取站点信息
        self.__load_site_info()

        if not self._site_cookie:
            logger.error("未获取到财神PT站点Cookie，插件无法运行")
            self.post_message(
                title="财神PT喊话",
                text="未获取到财神PT站点Cookie，请检查站点配置是否有效"
            )
            return

        if not self._cron and not self._onlyonce:
            logger.warning("财神PT喊话插件未配置定时间隔")
            return

        # 一次性任务
        if self._onlyonce:
            logger.info("财神PT喊话插件一次性任务启动")
            self._onlyonce = False
            self.__update_config()
            self.__do_shout()

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件定时服务。
        """
        if not self._enabled or not self._cron:
            return []
        return [
            {
                "id": "caishenptshout",
                "name": "财神PT喊话",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__do_shout,
                "desc": "定时发送喊话消息",
            }
        ]

    def __load_site_info(self) -> None:
        """
        从数据库加载财神PT站点信息。
        """
        try:
            db_gen = get_db()
            session: Session = next(db_gen)
            try:
                site = session.query(Site).filter(
                    Site.name == "财神"
                ).first()
                if site:
                    self._site_name = site.name
                    self._site_domain = site.domain
                    self._site_cookie = site.cookie or ""
                    logger.info(f"已获取站点信息: {self._site_name} ({self._site_domain})")
                else:
                    logger.warning("数据库中未找到财神PT站点配置")
            finally:
                session.close()
                try:
                    next(db_gen)
                except StopIteration:
                    pass
        except Exception as e:
            logger.error(f"获取站点信息失败: {str(e)}")

    def __do_shout(self) -> None:
        """
        执行喊话操作：向财神PT群聊区发送消息。
        """
        if not self._site_cookie:
            logger.error("财神PT站点Cookie为空，无法发送喊话")
            self.__save_shout_record("Cookie为空", "失败")
            return

        # 每次喊话前重新加载获取最新Cookie
        self.__load_site_info()
        if not self._site_cookie:
            self.__save_shout_record("Cookie为空(重载后)", "失败")
            return

        message = self._message or "财神，求上传"
        logger.info(f"开始向财神PT群聊区喊话: {message}")

        try:
            # 解析Cookie字典
            cookies = {}
            for item in self._site_cookie.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    cookies[k] = v

            # 构造请求
            url = f"https://{self._site_domain}/shoutbox.php"
            data = {
                "shbox_text": message,
                "shout": "\u6211\u559d"
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                              " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://{self._site_domain}/index.php",
            }

            # 发送POST请求
            res = RequestUtils(cookies=cookies, headers=headers).post_res(url=url, data=data)

            if res and res.status_code == 200:
                logger.info(f"喊话成功: {message}")
                self.__save_shout_record(message, "成功")
                self.post_message(
                    title="财神PT喊话",
                    text=f"喊话成功: {message}",
                    mtype=NotificationType.SiteMessage
                )
            else:
                status = res.status_code if res else "无响应"
                logger.warning(f"喊话失败，HTTP状态码: {status}")
                self.__save_shout_record(message, f"失败-HTTP{status}")
                self.post_message(
                    title="财神PT喊话",
                    text=f"喊话失败，HTTP {status}",
                    mtype=NotificationType.SiteMessage
                )

        except Exception as e:
            logger.error(f"喊话异常: {str(e)}")
            self.__save_shout_record(message, f"异常:{str(e)[:30]}")
            self.post_message(
                title="财神PT喊话",
                text=f"喊话异常: {str(e)}",
                mtype=NotificationType.SiteMessage
            )

    def __save_shout_record(self, message: str, status: str) -> None:
        """
        保存喊话执行记录。
        """
        from datetime import datetime
        now = datetime.now()
        day_label = f"{now.month}月{now.day}日"
        record = {
            "message": message,
            "status": status,
            "success": "成功" in status
        }
        self.save_data(day_label, record)
        logger.info(f"已保存喊话记录: {day_label} - {status}")

    def get_state(self) -> bool:
        """
        获取插件启用状态。
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回插件远程命令列表。
        """
        return [
            {
                "cmd": "/caishen_shout",
                "event": EventType.PluginAction,
                "desc": "财神PT喊话",
                "category": "站点工具",
                "data": {"action": "caishen_shout"},
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件API列表。
        """
        return [
            {
                "path": "shout",
                "endpoint": self.api_shout,
                "methods": ["POST"],
                "summary": "手动触发喊话",
                "description": "立即向财神PT群聊区发送喊话消息",
            }
        ]

    def api_shout(self, **kwargs) -> Dict[str, Any]:
        """
        手动触发喊话的API接口。
        """
        self.__do_shout()
        return {"success": True, "message": "喊话已触发"}

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """
        返回插件配置表单与默认配置。
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "message",
                                            "label": "喊话内容",
                                            "placeholder": "财神，求上传"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VCronField",
                                        "props": {
                                            "model": "cron",
                                            "label": "定时间隔(Cron)",
                                            "placeholder": "每6小时: 0 */6 * * *"
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即执行一次"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "clearcron",
                                            "label": "清除定时任务"
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
                                            "variant": "tonal",
                                            "text": "插件会自动从数据库获取财神PT站点的Cookie进行喊话。如需手动触发喊话，可使用 /caishen_shout 命令。"
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
            "message": "财神，求上传",
            "cron": "0 9 * * *",
            "onlyonce": False,
            "clearcron": False,
        }

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页面，展示每日喊话执行记录和状态。
        """
        from datetime import timedelta
        date_list = [(datetime.now() - timedelta(days=i)).date() for i in range(14)]
        all_records = []
        for day in date_list:
            day_label = f"{day.month}月{day.day}日"
            record = self.get_data(day_label)
            if record and isinstance(record, dict):
                record["date"] = day_label
                all_records.append(record)
        if not all_records:
            # 无执行记录时显示当前配置状态
            site_status = f"{self._site_name} ({self._site_domain})" if self._site_name else "财神 (cspt.top)"
            cookie_status = "已获取" if self._site_cookie else "未获取"
            enabled_status = "已启用" if self._enabled else "已禁用"
            return [
                {
                    "component": "VRow",
                    "content": [{
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [{
                            "component": "VAlert",
                            "props": {
                                "type": "info",
                                "variant": "tonal",
                                "text": "暂无喊话记录，插件运行后将在此显示执行结果"
                            }
                        }]
                    }]
                },
                {
                    "component": "VRow",
                    "props": {"class": "mt-2"},
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 4},
                            "content": [{
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [{
                                    "component": "VCardText",
                                    "props": {"class": "pa-4"},
                                    "content": [
                                        {"component": "div", "props": {"class": "text-h6 font-weight-bold"}, "text": enabled_status},
                                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": "插件状态"}
                                    ]
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
                                    "content": [
                                        {"component": "div", "props": {"class": "text-h6 font-weight-bold"}, "text": site_status},
                                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": "目标站点"}
                                    ]
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
                                    "content": [
                                        {"component": "div", "props": {"class": "text-h6 font-weight-bold"}, "text": cookie_status},
                                        {"component": "div", "props": {"class": "text-caption text-medium-emphasis"}, "text": "Cookie状态"}
                                    ]
                                }]
                            }]
                        }
                    ]
                }
            ]

        all_records.sort(key=lambda r: r.get("date", ""), reverse=True)
        recent = all_records[:7]
        today = recent[0] if recent else {}
        total_shouts = sum(1 for r in recent if r.get("message"))
        success_today = 1 if today.get("success") else 0
        fail_today = 1 if today.get("success") is False else 0
        success_7d = sum(1 for r in recent if r.get("success"))
        fail_7d = sum(1 for r in recent if r.get("success") is False)

        return [
            # 历史记录表格 - 放在最上方
            {
                "component": "VRow",
                "content": [{
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [{
                        "component": "VTable",
                        "props": {"hover": True, "density": "compact"},
                        "content": [
                            {"component": "thead", "content": [{"component": "tr", "content": [
                                {"component": "th", "props": {"class": "text-start"}, "text": "日期"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "喊话内容"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "状态"}
                            ]}]},
                            {"component": "tbody", "content": [
                                {
                                    "component": "tr",
                                    "content": [
                                        {"component": "td", "text": r.get("date", "")},
                                        {"component": "td", "props": {"class": "text-center"},
                                         "text": r.get("message", "")},
                                        {"component": "td", "props": {"class": "text-center"},
                                         "text": r.get("status", "")}
                                    ]
                                } for r in recent
                            ]}
                        ]
                    }]
                }]
            },
            # 统计摘要卡片 - 放在下方
            {
                "component": "VRow",
                "props": {"class": "mt-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [{
                            "component": "VCard",
                            "props": {"variant": "outlined"},
                            "content": [{
                                "component": "VCardText",
                                "props": {"class": "pa-4"},
                                "content": [{
                                    "component": "div",
                                    "props": {"class": "text-h6 font-weight-bold"},
                                    "text": str(total_shouts)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "近7天执行次数"
                                }]
                            }]
                        }]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [{
                            "component": "VCard",
                            "props": {"variant": "outlined"},
                            "content": [{
                                "component": "VCardText",
                                "props": {"class": "pa-4"},
                                "content": [{
                                    "component": "div",
                                    "props": {"class": "text-h6 font-weight-bold text-success"},
                                    "text": str(success_today)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "今日成功"
                                }]
                            }]
                        }]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [{
                            "component": "VCard",
                            "props": {"variant": "outlined"},
                            "content": [{
                                "component": "VCardText",
                                "props": {"class": "pa-4"},
                                "content": [{
                                    "component": "div",
                                    "props": {"class": "text-h6 font-weight-bold text-error"},
                                    "text": str(fail_today)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "今日失败"
                                }]
                            }]
                        }]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [{
                            "component": "VCard",
                            "props": {"variant": "outlined"},
                            "content": [{
                                "component": "VCardText",
                                "props": {"class": "pa-4"},
                                "content": [{
                                    "component": "div",
                                    "props": {"class": "text-h6 font-weight-bold"},
                                    "text": f"{success_7d}/{total_shouts}"
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "近7天成功率"
                                }]
                            }]
                        }]
                    }
                ]
            }
        ]

    def stop_service(self) -> None:
        """
        停止插件后台服务并释放资源。
        """
        pass

    @eventmanager.register(EventType.PluginAction)
    def remote_shout(self, event: ManagerEvent = None):
        """
        远程喊话命令事件处理。
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "caishen_shout":
                return
        self.__do_shout()

    def __update_config(self) -> None:
        """
        更新插件配置到数据库。
        """
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "message": self._message,
            "onlyonce": self._onlyonce,
            "clearcron": self._clearcron,
        })
