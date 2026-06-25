from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.schemas.types import NotificationType, EventType
from app.core.event import eventmanager
from app.log import logger


class QlCookieCheck(_PluginBase):
    """
    青龙面板 Cookie 过期检测插件
    """
    plugin_name = "青龙Cookie检测"
    plugin_desc = "定时检测青龙面板中京东Cookie等环境变量是否过期"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/autosignin.png"
    plugin_version = "1.0"
    plugin_author = "wenzhanquan"
    plugin_order = 30
    auth_level = 1

    _enabled = False
    _only_once = False
    _ql_url = ""
    _ql_user = ""
    _ql_pass = ""
    _notify = True
    _cron = "0 10 * * *"
    _token = None
    _token_expire = 0

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = config.get("enabled", False)
            self._only_once = config.get("onlyonce", False)
            self._ql_url = config.get("ql_url", "") or ""
            self._ql_user = config.get("ql_user", "") or ""
            self._ql_pass = config.get("ql_pass", "") or ""
            self._notify = config.get("notify", True)
            self._cron = config.get("cron", "0 10 * * *") or "0 10 * * *"
            logger.info(f"青龙Cookie检测配置已加载: enabled={self._enabled}, "
                        f"cron={self._cron}, onlyonce={self._only_once}")
        # 立即运行一次
        if self._only_once:
            logger.info("青龙Cookie检测: 立即运行一次")
            self._only_once = False
            self.update_config({
                "enabled": self._enabled,
                "onlyonce": False,
                "ql_url": self._ql_url,
                "ql_user": self._ql_user,
                "ql_pass": self._ql_pass,
                "notify": self._notify,
                "cron": self._cron
            })
            self.check_cookies()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [{
            "cmd": "/ql_cookie_check",
            "event": EventType.PluginAction,
            "desc": "青龙Cookie检测",
            "category": "工具",
            "data": {"action": "ql_cookie_check"}
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._cron:
            return []
        try:
            return [{
                "id": "QlCookieCheck",
                "name": "青龙Cookie检测",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.check_cookies,
                "kwargs": {}
            }]
        except Exception as e:
            logger.error(f"青龙Cookie检测创建定时任务失败: {str(e)}")
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
                                "props": {"cols": 12, "md": 6},
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "ql_url",
                                            "label": "青龙面板地址",
                                            "placeholder": "http://192.168.31.10:5700"
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
                                            "model": "ql_user",
                                            "label": "青龙用户名",
                                            "placeholder": "974527510@qq.com"
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
                                            "model": "ql_pass",
                                            "label": "青龙密码",
                                            "type": "password",
                                            "placeholder": "输入青龙密码"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "检测结果通知"
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
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "0 10 * * *"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False, "onlyonce": False,
            "ql_url": "", "ql_user": "", "ql_pass": "",
            "notify": True, "cron": "0 10 * * *"
        }

    def get_page(self) -> Optional[List[dict]]:
        """
        返回插件详情页面，展示执行历史。
        """
        history = self.get_data("_history") or []
        if not history or not isinstance(history, list):
            return [{
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "暂无执行记录，插件运行后将在此显示检测结果"
                }
            }]
        recent = history[:20]
        today = recent[0] if recent else {}
        today_total = today.get("total", 0)
        today_valid = today.get("valid", 0)
        today_expired = today.get("expired", 0)
        today_status = today.get("status", "")
        today_color = "success" if "成功" in today_status else "error"
        total_ok = sum(1 for r in recent if "成功" in r.get("status", ""))
        total_fail = sum(1 for r in recent if "失败" in r.get("status", ""))
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
                                {"component": "th", "props": {"class": "text-start"}, "text": "时间"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "青龙状态"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "Cookie总数"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "有效"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "过期"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "状态"}
                            ]}]},
                            {"component": "tbody", "content": [
                                {
                                    "component": "tr",
                                    "content": [
                                        {"component": "td", "text": r.get("key", "")},
                                        {"component": "td", "props": {"class": "text-center"},
                                         "text": "正常" if r.get("ql_status") == "ok" else "失败"},
                                        {"component": "td", "props": {"class": "text-center"},
                                         "text": str(r.get("total", 0))},
                                        {"component": "td", "props": {"class": "text-center text-success"},
                                         "text": str(r.get("valid", 0))},
                                        {"component": "td", "props": {"class": "text-center text-error"},
                                         "text": str(r.get("expired", 0))},
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
                                    "text": str(today_valid)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": f"最新有效Cookie / 共{today_total}项"
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
                                    "text": str(today_expired)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "最新过期Cookie"
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
                                    "text": str(today_total)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "最新检查Cookie总数"
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
                                    "text": str(total_ok)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": f"近{len(recent)}次执行正常"
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
                                    "props": {"class": "text-h6 font-weight-bold text-warning"},
                                    "text": str(total_fail)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": f"近{len(recent)}次执行失败"
                                }]
                            }]
                        }]
                    }
                ]
            }
        ]

    def stop_service(self):
        pass

    @eventmanager.register(EventType.PluginAction)
    def remote_check(self, event=None):
        """
        远程命令事件处理。
        """
        if event:
            event_data = getattr(event, "event_data", None)
            if not event_data or event_data.get("action") != "ql_cookie_check":
                return
        self.check_cookies()

    def __ql_login(self) -> Optional[str]:
        import json, urllib.request, urllib.error
        if self._token and self._token_expire > datetime.now().timestamp():
            return self._token
        url = f"{self._ql_url}/api/user/login"
        data = json.dumps({"username": self._ql_user, "password": self._ql_pass}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read().decode())
            if result.get("code") == 200:
                self._token = result.get("data", {}).get("token")
                self._token_expire = datetime.now().timestamp() + 86400
                logger.info("青龙面板登录成功")
                return self._token
            else:
                logger.error(f"青龙登录失败: {result.get('message', '未知错误')}")
                return None
        except Exception as e:
            logger.error(f"青龙登录请求失败: {str(e)}")
            return None

    def __get_envs(self) -> List[dict]:
        import json, urllib.request
        token = self.__ql_login()
        if not token:
            return []
        url = f"{self._ql_url}/api/envs"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read().decode())
            if result.get("code") == 200:
                envs = result.get("data", [])
                logger.info(f"获取到 {len(envs)} 个环境变量")
                return envs
            else:
                logger.warning(f"获取环境变量失败: code={result.get('code')}")
                return []
        except Exception as e:
            logger.error(f"获取环境变量失败: {str(e)}")
            return []

    def __check_jd_cookie(self, cookie: str) -> dict:
        import json, urllib.request
        url = "https://me-api.jd.com/user_new/info/GetJDUserInfoUnion"
        req = urllib.request.Request(url, headers={
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read().decode())
            if result.get("retcode") == "0" or result.get("code") == "0":
                ui = result.get("data", {}).get("userInfo", {}).get("baseInfo", {})
                return {"valid": True, "nickname": ui.get("nickname", "未知用户")}
            else:
                return {"valid": False, "reason": "Cookie已过期"}
        except Exception as e:
            return {"valid": False, "reason": f"验证失败: {str(e)[:50]}"}

    def check_cookies(self):
        if not self._enabled and not self._only_once:
            logger.info("青龙Cookie检测: 插件未启用，跳过")
            return
        logger.info("青龙Cookie检测: 开始检查...")

        def _append_history(record: dict):
            """追加记录到历史列表，最多保留50条。"""
            history = self.get_data("_history") or []
            if not isinstance(history, list):
                history = []
            history.insert(0, record)
            if len(history) > 50:
                history = history[:50]
            self.save_data("_history", history)

        now = datetime.now()
        day_key = f"{now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}"

        envs = self.__get_envs()
        if not envs:
            logger.warning("青龙Cookie检测: 连接青龙面板失败，请检查配置")
            self.post_message(
                title="青龙Cookie检测",
                text="❌ 连接青龙面板失败，请检查配置",
                mtype=NotificationType.Plugin
            )
            _append_history({
                "key": day_key, "total": 0, "valid": 0, "expired": 0,
                "ql_status": "failed",
                "status": "青龙连接失败"
            })
            return
        jd_envs = [e for e in envs if e.get("name", "").upper() in ("JD_COOKIE", "JD_WSCK")]
        if not jd_envs:
            logger.warning("青龙Cookie检测: 未找到京东Cookie环境变量")
            self.post_message(
                title="青龙Cookie检测",
                text="⚠️ 未找到京东Cookie环境变量",
                mtype=NotificationType.Plugin
            )
            _append_history({
                "key": day_key, "total": 0, "valid": 0, "expired": 0,
                "ql_status": "ok",
                "status": "未找到京东Cookie"
            })
            return
        valid_count = 0
        expired_count = 0
        details = []
        for env in jd_envs:
            value = env.get("value", "")
            remarks = env.get("remarks", "") or env.get("name", "")
            result = self.__check_jd_cookie(value)
            if result["valid"]:
                valid_count += 1
                logger.info(f"  ✅ {remarks}: Cookie有效 ({result.get('nickname', '?')})")
                details.append(f"✅ {remarks} ({result.get('nickname', '?')})")
            else:
                expired_count += 1
                logger.warning(f"  ❌ {remarks}: {result.get('reason', '未知')}")
                details.append(f"❌ {remarks} — {result.get('reason', '未知')}")
        logger.info(f"青龙Cookie检测完成: 有效={valid_count}, 过期={expired_count}, 共{len(jd_envs)}项")
        if self._notify:
            title = "青龙Cookie检测"
            text = f"✅ 有效: {valid_count} | ❌ 过期: {expired_count}"
            if details:
                text += "\n" + "\n".join(details[:10])
                if len(details) > 10:
                    text += f"\n...等共{len(details)}项"
            self.post_message(
                title=title,
                text=text,
                mtype=NotificationType.Plugin
            )
        _append_history({
            "key": day_key, "total": len(jd_envs),
            "valid": valid_count, "expired": expired_count,
            "ql_status": "ok",
            "status": f"有效{valid_count} 过期{expired_count}"
        })
