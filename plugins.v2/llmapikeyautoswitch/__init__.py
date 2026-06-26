import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import urllib.request
import urllib.error

from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import NotificationType


class LLMApiKeyAutoSwitch(_PluginBase):
    """
    智能助手 API Key 自动切换插件。
    当检测到当前 API Key 额度耗尽时，自动切换到备用 Key。
    """

    plugin_name = "API Key 自动切换"
    plugin_desc = "检测 API Key 额度，耗尽时自动切换到备用 Key。"
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/llmapikeyautoswitch/icon.png"
    plugin_version = "1.0.0"
    plugin_label = "系统工具"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
    plugin_config_prefix = "llmapikeyautoswitch_"
    plugin_order = 10
    auth_level = 1

    _enabled: bool = False
    _primary_key: str = ""
    _backup_key: str = ""
    _notify: bool = True
    _cron: str = "0 */6 * * *"

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        self.stop_service()

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        self._primary_key = str(config.get("primary_key") or "")
        self._backup_key = str(config.get("backup_key") or "")
        self._notify = bool(config.get("notify", True))
        self._cron = str(config.get("cron") or "0 */6 * * *")

        if not self._enabled:
            logger.info("API Key 自动切换插件未启用")
            return

        if not self._primary_key or not self._backup_key:
            logger.warning("API Key 自动切换插件：主 Key 和备用 Key 都必须配置")
            return

        # 注册定时检测任务
        self._register_scheduler()

        logger.info(f"API Key 自动切换插件已启动，检测间隔: {self._cron}")

    def _register_scheduler(self) -> None:
        """注册定时检测任务。"""
        try:
            trigger = CronTrigger.from_cron(self._cron)
            self._scheduler.add_job(
                func=self._check_and_switch,
                trigger=trigger,
                name="API Key 额度检测",
            )
        except Exception as e:
            logger.error(f"定时任务注册失败: {str(e)}")

    def get_service(self) -> List[Dict[str, Any]]:
        """返回插件定时服务配置。"""
        if not self._enabled:
            return []
        return [
            {
                "id": "api_key_check",
                "name": "API Key 额度检测",
                "trigger": CronTrigger.from_cron(self._cron),
                "func": self._check_and_switch,
                "kwargs": {},
            }
        ]

    def _get_current_key_label(self) -> str:
        """获取当前使用的 Key 是主还是备用。"""
        current_key = settings.LLM_API_KEY or ""
        if current_key == self._primary_key:
            return "主 Key"
        elif current_key == self._backup_key:
            return "备用 Key"
        return "未识别"

    def _check_and_switch(self) -> None:
        """检测当前 API Key 额度并在耗尽时切换。"""
        if not self._enabled:
            return

        current_key = settings.LLM_API_KEY or ""
        if not current_key:
            logger.warning("当前 LLM_API_KEY 为空，尝试切换到主 Key")
            self._do_switch(self._primary_key, "系统 Key 为空")
            return

        # 判断当前用的是哪个 Key
        is_primary = current_key == self._primary_key
        is_backup = current_key == self._backup_key

        if not is_primary and not is_backup:
            logger.warning(f"当前 LLM_API_KEY 不在插件管理的 Key 中，尝试切换回主 Key")
            self._do_switch(self._primary_key, "当前 Key 不在管理范围")
            return

        # 检测当前 Key 是否有额度
        exhausted, error_msg = self._check_quota(current_key)
        if exhausted:
            logger.warning(f"当前 Key 额度已耗尽: {error_msg}")
            target_key = self._backup_key if is_primary else self._primary_key
            label = "备用 Key" if is_primary else "主 Key"
            self._do_switch(target_key, f"当前 {label} 额度耗尽: {error_msg}")
        else:
            label = "主 Key" if is_primary else "备用 Key"
            logger.info(f"当前 {label} 额度正常")

    def _check_quota(self, api_key: str) -> Tuple[bool, str]:
        """
        检测指定 API Key 是否还有额度。

        :param api_key: 待检测的 API Key
        :return: (是否已耗尽, 错误信息)
        """
        base_url = str(settings.LLM_BASE_URL or "").rstrip("/")
        model = str(settings.LLM_MODEL or "gpt-3.5-turbo")

        if not base_url:
            return False, ""

        # 构造 chat completions 请求，只请求 1 个 token 来测试
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
        }
        req_data = json.dumps(payload).encode()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        url = f"{base_url}/chat/completions"

        try:
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=15)
            # 200 表示正常返回，Key 有额度
            return False, ""
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            status = e.code

            # 额度耗尽相关状态码/错误
            if status in (401, 402, 403, 429):
                return True, f"HTTP {status}: {body[:200]}"

            # 检查 body 中是否包含额度耗尽关键词
            exhausted_keywords = [
                "insufficient_quota",
                "quota_exceeded",
                "rate_limit_exceeded",
                "exhausted",
                "insufficient balance",
                "billing",
                "credit limit",
                "payment required",
                "out of credits",
                "account suspended",
                "no available quota",
                "token quota",
                "current quota",
                "429",
            ]
            body_lower = body.lower()
            for kw in exhausted_keywords:
                if kw in body_lower:
                    return True, f"额度异常: {body[:200]}"

            # 其他非额度错误（如模型不存在等）不触发切换
            logger.warning(f"API 请求返回 {status}，非额度错误，不触发切换: {body[:200]}")
            return False, ""
        except Exception as e:
            # 网络超时等临时错误不触发切换
            logger.warning(f"API 检测请求异常: {str(e)}")
            return False, ""

    def _do_switch(self, new_key: str, reason: str) -> None:
        """
        执行 Key 切换并记录历史。

        :param new_key: 新的 API Key
        :param reason: 切换原因
        """
        old_key = settings.LLM_API_KEY or ""
        old_label = self._get_current_key_label()

        # 判断新 key 是主还是备用
        if new_key == self._primary_key:
            new_label = "主 Key"
        elif new_key == self._backup_key:
            new_label = "备用 Key"
        else:
            new_label = "未知"

        # 更新运行时的 LLM_API_KEY
        settings.LLM_API_KEY = new_key

        # 记录切换历史
        history = self.get_data("switch_history") or []
        record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "old_label": old_label,
            "new_label": new_label,
            "reason": reason,
        }
        history.append(record)
        # 最多保留 100 条
        if len(history) > 100:
            history = history[-100:]
        self.save_data("switch_history", history)

        # 记录当前状态
        self.save_data("current_status", {
            "current_label": new_label,
            "last_switch_time": record["time"],
            "last_switch_reason": reason,
        })

        msg = f"API Key 已自动切换：{old_label} → {new_label}\n原因：{reason}"
        logger.info(msg)

        if self._notify:
            self.post_message(
                title="API Key 自动切换",
                text=msg,
                mtype=NotificationType.SiteMessage,
            )

    def get_state(self) -> bool:
        """获取插件启用状态。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """返回插件远程命令列表。"""
        return [
            {
                "cmd": "/switch_api_key",
                "event": "switch_api_key",
                "desc": "手动切换 API Key",
                "data": {},
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。"""
        return [
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "获取当前 Key 状态",
            },
            {
                "path": "/history",
                "endpoint": self.api_history,
                "methods": ["GET"],
                "summary": "获取切换历史记录",
            },
        ]

    def api_status(self) -> Dict[str, Any]:
        """获取当前 Key 状态 API。"""
        return {
            "current_label": self._get_current_key_label(),
            "last_switch_time": (self.get_data("current_status") or {}).get("last_switch_time", "无"),
            "last_switch_reason": (self.get_data("current_status") or {}).get("last_switch_reason", "无"),
        }

    def api_history(self) -> List[Dict[str, Any]]:
        """获取切换历史记录 API。"""
        return self.get_data("switch_history") or []

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回插件配置表单与默认配置。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "enabled",
                            "label": "启用插件",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "notify",
                            "label": "启用通知",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "primary_key",
                            "label": "主 API Key",
                            "placeholder": "sk-xxx...",
                            "type": "password",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "backup_key",
                            "label": "备用 API Key",
                            "placeholder": "sk-xxx...",
                            "type": "password",
                        },
                    },
                    {
                        "component": "VCronField",
                        "props": {
                            "model": "cron",
                            "label": "检测周期 (Cron 表达式)",
                            "placeholder": "默认每6小时",
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "notify": True,
            "primary_key": "",
            "backup_key": "",
            "cron": "0 */6 * * *",
        }

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页面。"""
        if not self._enabled:
            return None

        current_status = self.get_data("current_status") or {}
        history = self.get_data("switch_history") or []
        last_records = history[-5:] if history else []

        rows = []
        # 当前状态卡片
        rows.append({
            "component": "VCard",
            "content": [
                {
                    "component": "VCardText",
                    "props": {"class": "pa-4"},
                    "content": [
                        {
                            "component": "VAlert",
                            "props": {
                                "type": "success" if current_status.get("current_label") else "info",
                                "title": "当前使用",
                                "text": f'当前: {current_status.get("current_label", "未检测")} | 上次切换: {current_status.get("last_switch_time", "无")}',
                            },
                        },
                        {
                            "component": "VAlert",
                            "props": {
                                "type": "info",
                                "title": "上次切换原因",
                                "text": current_status.get("last_switch_reason", "无"),
                            },
                        },
                    ],
                },
            ],
        })

        # 切换历史表格
        if last_records:
            headers = [
                {"title": "时间", "key": "time", "align": "start", "sortable": True},
                {"title": "切换前", "key": "old_label"},
                {"title": "切换后", "key": "new_label"},
                {"title": "原因", "key": "reason"},
            ]
            rows.append({
                "component": "VCard",
                "content": [
                    {
                        "component": "VCardText",
                        "props": {"class": "pa-0"},
                        "content": [
                            {
                                "component": "VDataTable",
                                "props": {
                                    "headers": headers,
                                    "items": last_records,
                                    "items-per-page": -1,
                                    "hide-default-footer": True,
                                    "density": "compact",
                                },
                            },
                        ],
                    },
                ],
            })

        return [
            {
                "component": "div",
                "props": {"class": "pa-4"},
                "content": rows,
            },
        ]

    def stop_service(self) -> None:
        """停止插件后台服务并释放资源。"""
        try:
            self._scheduler.remove_job("api_key_check")
        except Exception:
            pass
