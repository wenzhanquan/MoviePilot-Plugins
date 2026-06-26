from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.schemas.types import NotificationType, EventType
from app.core.event import eventmanager
from app.log import logger


class MpPluginHealthCheck(_PluginBase):
    """
    MP 插件健康检测插件
    """
    plugin_name = "MP插件健康检测"
    plugin_desc = "定时检测已安装插件状态变化"
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/mppluginhealthcheck/icon.png"
    plugin_version = "1.0"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
    plugin_order = 31
    auth_level = 1

    _enabled = False
    _only_once = False
    _notify = True
    _cron = "10 9 * * *"
    _snapshot_key = "plugin_snapshot"

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = config.get("enabled", False)
            self._only_once = config.get("onlyonce", False)
            self._notify = config.get("notify", True)
            self._cron = config.get("cron", "10 9 * * *") or "10 9 * * *"
            logger.info(f"MP插件健康检测配置已加载: enabled={self._enabled}, "
                        f"cron={self._cron}, onlyonce={self._only_once}")
        # 立即运行一次
        if self._only_once:
            logger.info("MP插件健康检测: 立即运行一次")
            self._only_once = False
            self.update_config({
                "enabled": self._enabled,
                "onlyonce": False,
                "notify": self._notify,
                "cron": self._cron
            })
            self.check_plugins()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [{
            "cmd": "/plugin_health_check",
            "event": EventType.PluginAction,
            "desc": "MP插件健康检测",
            "category": "工具",
            "data": {"action": "plugin_health_check"}
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._cron:
            return []
        try:
            return [{
                "id": "MpPluginHealthCheck",
                "name": "MP插件健康检测",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.check_plugins,
                "kwargs": {}
            }]
        except Exception as e:
            logger.error(f"MP插件健康检测创建定时任务失败: {str(e)}")
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
                                            "placeholder": "10 9 * * *"
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
            "notify": True, "cron": "10 9 * * *"
        }

    @staticmethod
    def _fmt_detail(record: dict) -> str:
        """格式化插件变更详情文本。"""
        parts = []
        for name in record.get("lost_names", []) or []:
            parts.append(f"丢失:{name}")
        for name in record.get("stopped_names", []) or []:
            parts.append(f"停用:{name}")
        for item in record.get("upgraded_names", []) or []:
            parts.append(f"升级:{item}")
        for item in record.get("new_names", []) or []:
            parts.append(f"新增:{item}")
        return "; ".join(parts) if parts else record.get("status", "")

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
        plugin_count = today.get("plugin_count", 0)
        total_changes = sum(1 for r in recent if r.get("has_changes"))
        total_ok = len(recent) - total_changes
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
                                {"component": "th", "props": {"class": "text-center"}, "text": "插件数"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "丢失"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "停用"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "升级"},
                                {"component": "th", "props": {"class": "text-center"}, "text": "新增"},
                                {"component": "th", "props": {"class": "text-start"}, "text": "详细"}
                            ]}]},
                            {"component": "tbody", "content": [
                                {
                                    "component": "tr",
                                    "content": [
                                        {"component": "td", "text": r.get("key", r.get("date_label", ""))},
                                        {"component": "td", "props": {"class": "text-center"},
                                         "text": str(r.get("plugin_count", 0))},
                                        {"component": "td", "props": {"class": "text-center text-error"},
                                         "text": str(r.get("lost", 0))},
                                        {"component": "td", "props": {"class": "text-center text-warning"},
                                         "text": str(r.get("stopped", 0))},
                                        {"component": "td", "props": {"class": "text-center text-info"},
                                         "text": str(r.get("upgraded", 0))},
                                        {"component": "td", "props": {"class": "text-center text-success"},
                                         "text": str(r.get("new", 0))},
                                        {"component": "td", "props": {"style": "max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"},
                                         "text": self._fmt_detail(r)}
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
                                    "text": str(plugin_count)
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "最新检查插件数"
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
                                    "text": str(today.get("lost", 0))
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "最新丢失"
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
                                    "text": str(today.get("stopped", 0))
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": "最新停用"
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
                                    "text": f"{total_ok}/{len(recent)}"
                                }, {
                                    "component": "div",
                                    "props": {"class": "text-caption text-medium-emphasis"},
                                    "text": f"近{len(recent)}次正常"
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
            if not event_data or event_data.get("action") != "plugin_health_check":
                return
        self.check_plugins()

    def __get_plugin_list(self) -> List[dict]:
        try:
            from app.core.plugin import PluginManager
            from app.db.systemconfig_oper import SystemConfigOper
            pm = PluginManager()
            running_plugins = pm.running_plugins
            all_plugins = pm.plugins
            config_oper = SystemConfigOper()
            result = []
            all_ids = set(list(running_plugins.keys()) + list(all_plugins.keys()))
            for plugin_id in all_ids:
                instance = running_plugins.get(plugin_id)
                cls = all_plugins.get(plugin_id)
                plugin_cls = instance or cls
                # 优先从数据库中读取已保存的配置来判断启用状态
                # 避免插件重载时序导致 running_plugins 中状态不准确
                saved_config = config_oper.get(f"plugin.{plugin_id}")
                if saved_config:
                    enabled = bool(saved_config.get("enabled", False))
                elif instance and hasattr(instance, "get_state"):
                    enabled = instance.get_state()
                else:
                    enabled = False
                result.append({
                    "id": plugin_id,
                    "name": getattr(plugin_cls, "plugin_name", plugin_id),
                    "version": getattr(plugin_cls, "plugin_version", ""),
                    "state": enabled
                })
            logger.info(f"获取到 {len(result)} 个插件")
            return result
        except Exception as e:
            logger.error(f"获取插件列表失败: {str(e)}")
            return []

    def __save_snapshot(self, plugins: List[dict]):
        try:
            self.save_data(self._snapshot_key, plugins)
            logger.info(f"已保存 {len(plugins)} 个插件快照")
        except Exception as e:
            logger.error(f"保存插件快照失败: {str(e)}")

    def __load_snapshot(self) -> List[dict]:
        try:
            data = self.get_data(self._snapshot_key)
            result = data if isinstance(data, list) else []
            logger.info(f"加载到 {len(result)} 个插件快照记录")
            return result
        except Exception as e:
            logger.error(f"加载插件快照失败: {str(e)}")
            return []

    def check_plugins(self):
        if not self._enabled and not self._only_once:
            logger.info("MP插件健康检测: 插件未启用，跳过")
            return
        logger.info("MP插件健康检测: 开始检查...")
        current = self.__get_plugin_list()
        now = datetime.now()
        day_key = f"{now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}"
        run_time = now.strftime("%H:%M:%S")
        date_label = f"{now.month}月{now.day}日"

        def _append_history(record: dict):
            """追加记录到历史列表，最多保留50条。"""
            history = self.get_data("_history") or []
            if not isinstance(history, list):
                history = []
            history.insert(0, record)
            if len(history) > 50:
                history = history[:50]
            self.save_data("_history", history)

        if not current:
            logger.warning("MP插件健康检测: 无法获取插件列表")
            _append_history({
                "key": day_key, "plugin_count": 0, "lost": 0, "stopped": 0,
                "upgraded": 0, "new": 0, "has_changes": False,
                "lost_names": [], "stopped_names": [],
                "upgraded_names": [], "new_names": [],
                "status": "无法获取插件列表",
                "run_time": run_time, "date_label": date_label
            })
            return
        baseline = self.__load_snapshot()
        if not baseline:
            self.__save_snapshot(current)
            logger.info(f"MP插件健康检测: 首次运行，已保存 {len(current)} 个插件作为基准")
            if self._notify:
                self.post_message(
                    title="MP插件健康检测 - 首次运行",
                    text=f"📋 已保存 {len(current)} 个插件作为基准，下次将对比变化并通知",
                    mtype=NotificationType.Plugin
                )
            _append_history({
                "key": day_key, "plugin_count": len(current), "lost": 0, "stopped": 0,
                "upgraded": 0, "new": 0, "has_changes": False,
                "lost_names": [], "stopped_names": [],
                "upgraded_names": [], "new_names": [],
                "status": "首次运行 - 已建立基准",
                "run_time": run_time, "date_label": date_label
            })
            return
        baseline_map = {p["id"]: p for p in baseline}
        current_map = {p["id"]: p for p in current}
        critical = []
        info = []
        lost_names = []
        stopped_names = []
        upgraded_names = []
        new_names = []
        for pid, p in baseline_map.items():
            if pid not in current_map:
                critical.append(f"🔴 丢失: {p['name']} ({pid})")
                logger.warning(f"  插件丢失: {p['name']} ({pid})")
                lost_names.append(p['name'])
        for pid, p in current_map.items():
            if pid in baseline_map:
                old = baseline_map[pid]
                if old.get("state") and not p.get("state"):
                    critical.append(f"🟡 停用: {p['name']} ({pid})")
                    logger.warning(f"  插件停用: {p['name']} ({pid})")
                    stopped_names.append(p['name'])
                ov = old.get("version", "")
                nv = p.get("version", "")
                if ov and nv and ov != nv:
                    info.append(f"🔵 升级: {p['name']} {ov}->{nv}")
                    logger.info(f"  插件升级: {p['name']} {ov}->{nv}")
                    upgraded_names.append(f"{p['name']} {ov}->{nv}")
            else:
                info.append(f"🟢 新增: {p['name']} v{p.get('version', '?')}")
                logger.info(f"  插件新增: {p['name']} v{p.get('version', '?')}")
                new_names.append(f"{p['name']} v{p.get('version', '?')}")
        self.__save_snapshot(current)
        now_ts = datetime.now()
        run_time = now_ts.strftime("%H:%M:%S")
        day_key = f"{now_ts.month}月{now_ts.day}日 {now_ts.hour:02d}:{now_ts.minute:02d}"
        date_label = f"{now_ts.month}月{now_ts.day}日"
        now_str = now_ts.strftime("%m-%d %H:%M")
        if not critical and not info:
            logger.info("MP插件健康检测: 无变化")
            _append_history({
                "key": day_key, "plugin_count": len(current), "lost": 0, "stopped": 0,
                "upgraded": 0, "new": 0, "has_changes": False,
                "lost_names": [], "stopped_names": [],
                "upgraded_names": [], "new_names": [],
                "status": "正常",
                "run_time": run_time, "date_label": date_label
            })
            if self._notify:
                self.post_message(
                    title="MP插件健康检测",
                    text=f"✅ 所有插件状态正常（共 {len(current)} 个）\n🕐 {now_str}",
                    mtype=NotificationType.Plugin
                )
            return
        # 有变更，保存执行记录
        _append_history({
            "key": day_key, "plugin_count": len(current), "lost": len(critical),
            "stopped": sum(1 for c in critical if "停用" in c),
            "upgraded": sum(1 for i in info if "升级" in i),
            "new": sum(1 for i in info if "新增" in i),
            "has_changes": True,
            "lost_names": lost_names, "stopped_names": stopped_names,
            "upgraded_names": upgraded_names, "new_names": new_names,
            "run_time": run_time, "date_label": date_label,
            "status": "有变更"})
        if self._notify:
            parts = []
            if critical:
                parts.append("⚠️ 插件异常")
                parts.extend(critical)
            if info:
                parts.append("📌 变更信息")
                parts.extend(info)
            parts.append(f"🕐 {now_str}")
            self.post_message(
                title="MP插件健康检测 - 变更报告",
                text="\n".join(parts),
                mtype=NotificationType.Plugin
            )
