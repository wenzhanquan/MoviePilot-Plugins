import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from functools import wraps

from app.agent.llm import helper as llm_helper_module
from app.core.config import settings
from app.core.event import Event as ManagerEvent, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType


class LLMApiKeyAutoSwitch(_PluginBase):
    """
    免费 token API Key 自动切换插件。
    支持主 Key + 多个备用 Key 轮询切换。
    检测到连续多次 API 返回 401/402/429 时，自动切换到下一个备用 Key。
    """

    plugin_name = "免费token Api Key自动切换"
    plugin_desc = "支持主 Key + 多组备用 Key 轮询切换，检测到额度耗尽时自动依次切换。"
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
    _backup_count: int = 1
    _backup_keys: List[str] = []
    _notify: bool = True
    _retry_401: int = 3
    _retry_402: int = 3
    _retry_429: int = 3

    _original_build_func = None

    def init_plugin(self, config: dict = None) -> None:
        """根据插件配置初始化运行状态。"""
        self._restore_build()

        if not config:
            return

        self._enabled = bool(config.get("enabled", False))
        self._primary_key = str(config.get("primary_key") or "")
        self._backup_count = max(1, int(config.get("backup_count") or 1))
        self._retry_401 = int(config.get("retry_401") or 3)
        self._retry_402 = int(config.get("retry_402") or 3)
        self._retry_429 = int(config.get("retry_429") or 3)
        self._notify = bool(config.get("notify", True))

        # 读取所有备用 Key
        self._backup_keys = []
        for i in range(1, self._backup_count + 1):
            k = str(config.get(f"backup_key_{i}", "") or "")
            self._backup_keys.append(k)

        if not self._enabled:
            logger.info("免费token Api Key自动切换插件未启用")
            return

        if not self._primary_key:
            logger.warning("主 API Key 未配置")
            return

        # 确保当前 LLM_API_KEY 在管理范围内
        current = settings.LLM_API_KEY or ""
        all_keys = [self._primary_key] + self._get_backup_keys()
        if current not in all_keys:
            logger.info("当前 LLM_API_KEY 不在管理范围内，切换至主 Key")
            settings.LLM_API_KEY = self._primary_key

        # 初始化计数器
        for code in ("401", "402", "429"):
            key = f"fail_count_{code}"
            if not self.get_data(key):
                self.save_data(key, 0)

        if not self.get_data("current_status"):
            self.save_data("current_status", {
                "current_label": self._get_current_key_label(),
                "last_switch_time": "无",
                "last_switch_reason": "无",
                "last_switch_type": "-",
            })

        self._apply_patch()
        key_count = 1 + self._backup_count
        logger.info(
            f"免费token Api Key自动切换已启动，共 {key_count} 个 Key 轮询，"
            f"401={self._retry_401}次 402={self._retry_402}次 429={self._retry_429}次"
        )

    def _get_backup_keys(self) -> List[str]:
        """获取所有备用 Key 列表（按顺序）。"""
        return self._backup_keys

    def _get_backup_key(self, index: int) -> str:
        """获取第 N 个备用 Key（从 1 开始）。"""
        if 1 <= index <= len(self._backup_keys):
            return self._backup_keys[index - 1]
        return ""

    def _get_all_keys(self) -> List[Tuple[str, str]]:
        """获取所有 Key 列表，返回 [(label, key), ...]。"""
        result = [("主 API Key", self._primary_key)]
        for i in range(1, self._backup_count + 1):
            k = self._get_backup_key(i)
            if k:
                result.append((f"备用 Key {i}", k))
        return result

    def _get_current_key_index(self) -> int:
        """获取当前 Key 在列表中的索引（0=主，1=备1，2=备2...），不在管理范围返回 -1。"""
        current = settings.LLM_API_KEY or ""
        for idx, (label, key) in enumerate(self._get_all_keys()):
            if key == current:
                return idx
        return -1

    def _get_current_key_label(self) -> str:
        """获取当前 Key 的名称。"""
        idx = self._get_current_key_index()
        if idx < 0:
            return "未识别"
        return self._get_all_keys()[idx][0]

    def _apply_patch(self) -> None:
        """修补 _build_httpx_client，拦截 401/402/429 响应。"""
        original = llm_helper_module._build_httpx_client
        self._original_build_func = original
        plugin_ref = self

        @wraps(original)
        def patched_build(proxy_url, *, async_client=False, timeout=None):
            client = original(proxy_url, async_client=async_client, timeout=timeout)

            if async_client:
                _orig_send = client.send
                async def _patched_send(request, **kwargs):
                    response = await _orig_send(request, **kwargs)
                    plugin_ref._handle_status(response.status_code)
                    return response
                client.send = _patched_send
            else:
                _orig_send = client.send
                def _patched_send(request, **kwargs):
                    response = _orig_send(request, **kwargs)
                    plugin_ref._handle_status(response.status_code)
                    return response
                client.send = _patched_send
            return client

        llm_helper_module._build_httpx_client = patched_build

    def _restore_build(self) -> None:
        """恢复原始的 _build_httpx_client。"""
        if self._original_build_func is not None:
            llm_helper_module._build_httpx_client = self._original_build_func
            self._original_build_func = None

    def _handle_status(self, status_code: int) -> None:
        """处理 HTTP 响应状态码，按状态码分别统计连续失败次数。"""
        if not self._enabled:
            return

        if status_code in (401, 402, 429):
            code = str(status_code)
            count_key = f"fail_count_{code}"
            threshold = getattr(self, f"_retry_{code}", 3)

            count = (self.get_data(count_key) or 0) + 1
            self.save_data(count_key, count)

            if count >= threshold:
                self._do_switch(f"连续 {count} 次 API 返回 {status_code}")
        else:
            for code in ("401", "402", "429"):
                cur = self.get_data(f"fail_count_{code}") or 0
                if cur > 0:
                    self.save_data(f"fail_count_{code}", 0)

    def _get_next_key(self) -> Tuple[str, str]:
        """获取轮询中的下一个 Key，返回 (label, key)。"""
        all_keys = self._get_all_keys()
        idx = self._get_current_key_index()
        if idx < 0:
            return all_keys[0] if all_keys else ("", "")
        next_idx = (idx + 1) % len(all_keys)
        return all_keys[next_idx]

    def _do_switch(self, reason: str, trigger_type: str = "自动") -> None:
        """执行 Key 切换并记录历史。"""
        old_label = self._get_current_key_label()

        new_label, new_key = self._get_next_key()
        if not new_key:
            return

        settings.LLM_API_KEY = new_key

        for code in ("401", "402", "429"):
            self.save_data(f"fail_count_{code}", 0)

        history = self.get_data("switch_history") or []
        record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "old_label": old_label,
            "new_label": new_label,
            "reason": reason,
            "type": trigger_type,
        }
        history.append(record)
        if len(history) > 100:
            history = history[-100:]
        self.save_data("switch_history", history)

        self.save_data("current_status", {
            "current_label": new_label,
            "last_switch_time": record["time"],
            "last_switch_reason": reason,
            "last_switch_type": trigger_type,
        })

        emoji = "🔄" if "主" in new_label else "⚠️"
        msg = f"{emoji} {old_label} → {new_label}\n触发: {trigger_type}\n原因: {reason}"
        logger.warning(msg)

        if self._notify:
            self.post_message(
                title="🔑 API Key 已切换",
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
                "cmd": "/sw-api",
                "event": "switch_api_key",
                "desc": "手动切换到下一个备用 API Key",
                "data": {},
            },
        ]

    @eventmanager.register(EventType.CommandExcute)
    def handle_command(self, event: ManagerEvent = None) -> None:
        """处理 /sw-api 命令。"""
        if not self._enabled:
            return
        event_data = event.event_data
        if not event_data:
            return
        command = event_data.get("cmd")
        if command != "/sw-api":
            return
        self._do_switch("手动触发切换", trigger_type="手动")

    def get_api(self) -> List[Dict[str, Any]]:
        """返回插件 API 列表。"""
        return [
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "获取当前 Key 状态和失败计数",
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
        status = self.get_data("current_status") or {}
        all_keys = self._get_all_keys()
        key_list = []
        for label, key in all_keys:
            masked = key[:10] + "..." if len(key) > 15 else key
            key_list.append({"label": label, "masked": masked})

        return {
            "current_label": self._get_current_key_label(),
            "total_keys": len(all_keys),
            "key_list": key_list,
            "fail_401": self.get_data("fail_count_401") or 0,
            "fail_402": self.get_data("fail_count_402") or 0,
            "fail_429": self.get_data("fail_count_429") or 0,
            "retry_401": self._retry_401,
            "retry_402": self._retry_402,
            "retry_429": self._retry_429,
            "last_switch_time": status.get("last_switch_time", "无"),
            "last_switch_reason": status.get("last_switch_reason", "无"),
            "last_switch_type": status.get("last_switch_type", "-"),
        }

    def api_history(self) -> List[Dict[str, Any]]:
        """获取切换历史记录 API。"""
        return self.get_data("switch_history") or []

    def get_form(self) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
        """返回插件配置表单与默认配置。"""
        fields = [
            {
                "component": "VRow",
                "props": {"class": "mb-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}],
                    },
                ],
            },
            {
                "component": "VRow",
                "props": {"class": "mb-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [{"component": "VSwitch", "props": {"model": "notify", "label": "切换时发送通知"}}],
                    },
                ],
            },
            {
                "component": "VRow",
                "props": {"class": "mb-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [{
                            "component": "VTextField",
                            "props": {
                                "model": "primary_key",
                                "label": "主 API Key",
                                "placeholder": "sk-xxx...",
                                "append-inner-icon": "mdi-content-paste",
                                "onClick:append-inner": "event => { navigator.clipboard.readText().then(t => { model.primary_key = t; }).catch(e => {}); }",
                            },
                        }],
                    },
                ],
            },
            {
                "component": "VRow",
                "props": {"class": "mb-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [{
                            "component": "VTextField",
                            "props": {
                                "model": "backup_count",
                                "label": "备用 Key 数量",
                                "placeholder": "1",
                                "type": "number",
                                "hint": "修改后保存重新打开配置页以显示对应数量的输入框",
                                "persistent-hint": True,
                            },
                        }],
                    },
                ],
            },
        ]

        # 动态生成备用 Key 输入框
        count = self._backup_count
        for i in range(1, count + 1):
            model_key = f"backup_key_{i}"
            fields.append({
                "component": "VRow",
                "props": {"class": "mb-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [{
                            "component": "VTextField",
                            "props": {
                                "model": model_key,
                                "label": f"备用 Key {i}",
                                "placeholder": "sk-xxx...",
                                "append-inner-icon": "mdi-content-paste",
                                "onClick:append-inner": f"event => {{ navigator.clipboard.readText().then(t => {{ model.{model_key} = t; }}).catch(e => {{}}); }}",
                            },
                        }],
                    },
                ],
            })

        # 触发次数设置
        fields.append({
            "component": "VRow",
            "props": {"class": "mb-2"},
            "content": [
                {
                    "component": "VCol",
                    "props": {"cols": 4},
                    "content": [{"component": "VTextField", "props": {"model": "retry_401", "label": "401 触发次数", "placeholder": "3", "type": "number"}}],
                },
                {
                    "component": "VCol",
                    "props": {"cols": 4},
                    "content": [{"component": "VTextField", "props": {"model": "retry_402", "label": "402 触发次数", "placeholder": "3", "type": "number"}}],
                },
                {
                    "component": "VCol",
                    "props": {"cols": 4},
                    "content": [{"component": "VTextField", "props": {"model": "retry_429", "label": "429 触发次数", "placeholder": "3", "type": "number"}}],
                },
            ],
        })

        # 默认配置
        defaults = {
            "enabled": False,
            "notify": True,
            "primary_key": "",
            "backup_count": 1,
            "retry_401": 3,
            "retry_402": 3,
            "retry_429": 3,
        }
        for i in range(1, count + 1):
            defaults[f"backup_key_{i}"] = ""

        return [{"component": "VForm", "content": fields}], defaults

    def get_page(self) -> Optional[List[dict]]:
        """返回插件详情页面。"""
        if not self._enabled:
            return None

        status = self.get_data("current_status") or {}
        history = self.get_data("switch_history") or []
        current_label = self._get_current_key_label()
        all_keys = self._get_all_keys()

        f401 = self.get_data("fail_count_401") or 0
        f402 = self.get_data("fail_count_402") or 0
        f429 = self.get_data("fail_count_429") or 0
        has_fail = f401 > 0 or f402 > 0 or f429 > 0

        alert_type = "success"
        if has_fail:
            alert_type = "warning"
        if "备用" in current_label and has_fail:
            alert_type = "error"

        # Key 列表展示
        key_chips = []
        for idx, (label, key) in enumerate(all_keys):
            is_current = label == current_label
            masked = key[:12] + "..." if len(key) > 16 else key
            color = "primary" if idx == 0 else ("warning" if is_current else "default")
            variant = "tonal" if not is_current else "flat"
            key_chips.append({
                "component": "VChip",
                "props": {
                    "color": color,
                    "variant": variant,
                    "size": "small",
                    "class": "mr-1 mb-1",
                    "label": True,
                },
                "content": f"{'👉' if is_current else ''} {label}",
            })

        fail_text = f"401: {f401}/{self._retry_401} | 402: {f402}/{self._retry_402} | 429: {f429}/{self._retry_429}"

        rows = [
            # Key 列表 + 状态
            {
                "component": "VCard",
                "props": {"class": "mb-4"},
                "content": [{
                    "component": "VCardText",
                    "props": {"class": "pa-4"},
                    "content": [
                        {"component": "div", "props": {"class": "mb-2"}, "content": key_chips},
                        {
                            "component": "VAlert",
                            "props": {
                                "type": alert_type,
                                "density": "compact",
                                "variant": "tonal",
                                "title": f"当前: {current_label}",
                                "text": (
                                    f"连续失败: {fail_text}\n"
                                    f"上次切换: {status.get('last_switch_time', '无')} "
                                    f"({status.get('last_switch_type', '-')}) | "
                                    f"{status.get('last_switch_reason', '无')}"
                                ),
                            },
                        },
                    ],
                }],
            },
        ]

        # 切换历史列表
        if history:
            items = list(reversed(history[-10:]))

            rows.append({
                "component": "VRow",
                "content": [{
                    "component": "VCol",
                    "props": {"cols": 12},
                    "content": [{
                        "component": "VCard",
                        "props": {"variant": "outlined"},
                        "content": [{
                            "component": "VCardText",
                            "props": {"class": "pa-4"},
                            "content": [
                                {
                                    "component": "div",
                                    "props": {"class": "text-h6 font-weight-bold mb-2"},
                                    "text": f"切换历史（最近 {min(len(history), 10)} 条）",
                                },
                                {
                                    "component": "VTable",
                                    "props": {"hover": True, "density": "compact"},
                                    "content": [
                                        {"component": "thead", "content": [{"component": "tr", "content": [
                                            {"component": "th", "props": {"class": "text-start"}, "text": "时间"},
                                            {"component": "th", "props": {"class": "text-center"}, "text": "触发"},
                                            {"component": "th", "props": {"class": "text-center"}, "text": "切换方向"},
                                            {"component": "th", "props": {"class": "text-start"}, "text": "原因"},
                                        ]}]},
                                        {"component": "tbody", "content": [
                                            {
                                                "component": "tr",
                                                "content": [
                                                    {"component": "td", "text": r["time"]},
                                                    {"component": "td", "props": {"class": "text-center"}, "text": r.get("type", "自动") if r.get("type") else ("手动" if "手动" in r.get("reason", "") else "自动")},
                                                    {"component": "td", "props": {"class": "text-center"}, "text": f'{r["old_label"]} → {r["new_label"]}'},
                                                    {"component": "td", "text": r["reason"]},
                                                ]
                                            } for r in items
                                        ]}
                                    ]
                                }
                            ]
                        }]
                    }]
                }]
            })

        return [{"component": "div", "props": {"class": "pa-4"}, "content": rows}]

    def stop_service(self) -> None:
        """停止插件并恢复原始修补。"""
        self._restore_build()