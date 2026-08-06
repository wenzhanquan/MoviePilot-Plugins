from app.plugins import _PluginBase
from app.log import logger
from typing import Any, List, Dict, Tuple, Optional
from app.schemas.types import NotificationType
from datetime import datetime
from fastapi import Body


class HermesNotificationForwarder(_PluginBase):
    plugin_name = "Hermes通知转发"
    plugin_desc = "接收Hermes推送的消息，通过MP通知渠道（企微/微信）发送给管理员"
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/hermesnotificationforwarder/icon.png"
    plugin_version = "2.2"
    plugin_author = "wenzhanquan"
    plugin_order = 20
    auth_level = 1

    _enabled: bool = True
    _notify: bool = True
    _onlyonce: bool = False
    _api_key: str = ""
    _title_template: str = "🦞 Hermes通知"

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = config.get("enabled", True)
            self._notify = config.get("notify", True)
            self._onlyonce = config.get("onlyonce", False)
            self._api_key = config.get("api_key", "")
            self._title_template = config.get("title_template", "🦞 Hermes通知")
        if self._onlyonce:
            logger.info("Hermes通知转发：立即运行一次 - 发送测试通知")
            self._onlyonce = False
            self.__update_config()
            self.post_message(
                mtype=NotificationType.Plugin,
                title=self._title_template or "🦞 Hermes通知",
                text="🔔 Hermes通知转发插件已启动\n\n插件运行正常，等待接收通知..."
            )

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "api_key": self._api_key,
            "title_template": self._title_template,
        })

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/send",
                "endpoint": self.api_send,
                "methods": ["POST", "PUT"],
                "summary": "发送消息通知",
                "description": "接收消息内容并通过MP通知渠道发送给管理员"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
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
                                            "label": "启用插件"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "发送通知",
                                            "subtitle": "关闭后只记录不发送"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                            "subtitle": "保存后立即发送测试通知"
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
                                            "model": "api_key",
                                            "label": "API密钥",
                                            "placeholder": "设置密钥防止滥用",
                                            "subtitle": "留空则不验证身份"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "title_template",
                                            "label": "通知标题",
                                            "placeholder": "默认为 🦞 Hermes通知",
                                            "subtitle": "发送通知时显示的标题"
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
                                            "text": "此插件由Hermes主动调用API推送消息，无需配置定时任务。"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": False,
            "api_key": self._api_key,
            "title_template": self._title_template,
        }

    def get_page(self) -> List[dict]:
        history = self.get_data("_history") or []
        if not history or not isinstance(history, list):
            return [
                {
                    "component": "VAlert",
                    "props": {
                        "type": "info",
                        "variant": "tonal",
                        "text": "暂无发送记录，Hermes调用API后将在此显示"
                    }
                }
            ]
        recent = history[:20]
        total_success = sum(1 for r in recent if r.get("success"))
        total_fail = sum(1 for r in recent if not r.get("success"))
        rows = []
        for record in recent:
            status_icon = "✅" if record.get("success") else "❌"
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "text": status_icon},
                    {"component": "td", "text": record.get("time", "")},
                    {"component": "td", "text": record.get("source", "Hermes")},
                    {"component": "td", "props": {"class": "text-truncate", "style": "max-width:300px"},
                     "text": record.get("preview", "")[:50]},
                ]
            })
        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal", "color": "primary"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "text-center pa-4"},
                                        "content": [
                                            {"component": "div", "props": {"class": "text-h5"}, "text": str(len(recent))},
                                            {"component": "div", "props": {"class": "text-body-2"}, "text": "最近发送"}
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal", "color": "success"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "text-center pa-4"},
                                        "content": [
                                            {"component": "div", "props": {"class": "text-h5"}, "text": str(total_success)},
                                            {"component": "div", "props": {"class": "text-body-2"}, "text": "发送成功"}
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal", "color": "error"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "props": {"class": "text-center pa-4"},
                                        "content": [
                                            {"component": "div", "props": {"class": "text-h5"}, "text": str(total_fail)},
                                            {"component": "div", "props": {"class": "text-body-2"}, "text": "发送失败"}
                                        ]
                                    }
                                ]
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
                                "component": "VTable",
                                "props": {"hover": True, "density": "compact"},
                                "content": [
                                    {
                                        "component": "thead",
                                        "content": [{
                                            "component": "tr",
                                            "content": [
                                                {"component": "th", "props": {"class": "text-start"}, "text": "状态"},
                                                {"component": "th", "props": {"class": "text-start"}, "text": "时间"},
                                                {"component": "th", "props": {"class": "text-start"}, "text": "来源"},
                                                {"component": "th", "props": {"class": "text-start"}, "text": "内容预览"},
                                            ]
                                        }]
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
            }
        ]

    def stop_service(self):
        pass

    def api_send(self,
                message: str = Body(..., description="通知消息内容"),
                api_key: str = Body("", description="API密钥"),
                source: str = Body("Hermes", description="消息来源")):
        """
        API端点：接收消息并通过MP通知渠道发送给管理员
        """
        try:
            if self._api_key and api_key != self._api_key:
                logger.warning(f"Hermes通知转发：API密钥验证失败（来自 {source}）")
                return {"success": False, "message": "API密钥验证失败"}

            if not message:
                return {"success": False, "message": "消息内容不能为空"}

            result = {"success": True, "message": "通知已记录"}

            if self._notify:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title=self._title_template or "🦞 Hermes通知",
                    text=message
                )
                logger.info(f"Hermes通知转发成功: {message[:80]}...")
                result = {"success": True, "message": "通知已发送"}

            self.__add_history(source, message, result.get("success"))
            return result

        except Exception as e:
            logger.error(f"Hermes通知转发失败: {str(e)}")
            return {"success": False, "message": f"处理失败: {str(e)}"}

    def __add_history(self, source: str, message: str, success: bool):
        history = self.get_data("_history") or []
        if not isinstance(history, list):
            history = []
        history.insert(0, {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "preview": message.strip()[:100],
            "success": success
        })
        if len(history) > 200:
            history = history[:200]
        self.save_data("_history", history)