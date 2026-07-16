import time
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.schemas.types import NotificationType, EventType
from app.core.event import eventmanager
from app.helper.plugin import PluginHelper
from app.helper.server import MoviePilotServerHelper
from app.log import logger


class MpPluginHealthCheck(_PluginBase):
    """
    MP 插件健康检测插件
    """
    plugin_name = "MP插件健康检测"
    plugin_desc = "定时检测已安装插件状态变化"
    plugin_icon = "https://raw.githubusercontent.com/wenzhanquan/MoviePilot-Plugins/main/plugins.v2/mppluginhealthcheck/icon.png"
    plugin_version = "1.0.0"
    plugin_author = "wenzhanquan"
    author_url = "https://github.com/wenzhanquan"
    plugin_order = 31
    auth_level = 1

    _enabled = False
    _only_once = False
    _notify = True
    _auto_install = False
    _cron = "10 9 * * *"
    _snapshot_key = "plugin_snapshot"

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = config.get("enabled", False)
            self._only_once = config.get("onlyonce", False)
            self._notify = config.get("notify", True)
            self._auto_install = config.get("auto_install", False)
            self._cron = config.get("cron", "10 9 * * *") or "10 9 * * *"
            logger.info(f"MP插件健康检测配置已加载: enabled={self._enabled}, "
                        f"cron={self._cron}, onlyonce={self._only_once}, "
                        f"auto_install={self._auto_install}")
        # 立即运行一次
        if self._only_once:
            logger.info("MP插件健康检测: 立即运行一次")
            self._only_once = False
            self.update_config({
                "enabled": self._enabled,
                "onlyonce": False,
                "notify": self._notify,
                "auto_install": self._auto_install,
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
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "auto_install",
                                            "label": "自动安装丢失插件"
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
            "notify": True, "auto_install": False,
            "cron": "10 9 * * *"
        }

    @staticmethod
    def _fmt_detail(record: dict) -> str:
        """格式化插件变更详情文本。"""
        parts = []
        for name in record.get("lost_names", []) or []:
            parts.append("丢失:%s" % name)
        for name in record.get("stopped_names", []) or []:
            parts.append("停用:%s" % name)
        for name in record.get("started_names", []) or []:
            parts.append("启用:%s" % name)
        for item in record.get("upgraded_names", []) or []:
            parts.append("升级:%s" % item)
        for item in record.get("new_names", []) or []:
            parts.append("新增:%s" % item)
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
            event_data = getattr(event, "event_data", None) or getattr(event, "data", None)
            logger.info(f"MP插件健康检测: event_data={event_data}")
            if not event_data:
                logger.info("MP插件健康检测: 无事件数据，仍执行检查")
            else:
                action = event_data.get("action") if isinstance(event_data, dict) else None
                if action != "plugin_health_check":
                    logger.info(f"MP插件健康检测: action={action} 不匹配，忽略")
                    return
        self.check_plugins()

    def __get_plugin_list(self) -> List[dict]:
        try:
            from app.core.plugin import PluginManager
            from app.db.systemconfig_oper import SystemConfigOper
            from app.schemas.types import SystemConfigKey
            pm = PluginManager()
            running_plugins = pm.running_plugins
            all_plugins = pm.plugins
            config_oper = SystemConfigOper()
            # 获取真正已安装的插件 ID 列表
            installed_ids = config_oper.get(SystemConfigKey.UserInstalledPlugins) or []
            result = []
            for plugin_id, instance in running_plugins.items():
                if plugin_id not in installed_ids:
                    continue
                cls = all_plugins.get(plugin_id)
                plugin_cls = instance or cls
                # 读取插件的启用状态
                saved_config = config_oper.get(f"plugin.{plugin_id}")
                if saved_config:
                    enabled_setting = saved_config.get("enabled")
                    if enabled_setting is not None:
                        enabled = bool(enabled_setting)
                    elif hasattr(instance, "get_state"):
                        enabled = instance.get_state()
                    else:
                        enabled = False
                elif hasattr(instance, "get_state"):
                    enabled = instance.get_state()
                else:
                    enabled = False
                result.append({
                    "id": plugin_id,
                    "name": getattr(plugin_cls, "plugin_name", plugin_id),
                    "version": getattr(plugin_cls, "plugin_version", ""),
                    "state": enabled
                })
            logger.info(f"获取到 {len(result)} 个已安装插件")
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
        logger.info("MP插件健康检测: check_plugins 被调用")
        if not self._enabled and not self._only_once:
            logger.info("MP插件健康检测: 插件未启用，跳过")
            return
        logger.info("MP插件健康检测: 开始检查...")
        current = self.__get_plugin_list()
        auto_installed = []
        auto_failed = []
        now = datetime.now()
        day_key = f"{now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}"
        run_time = now.strftime("%H:%M:%S")
        date_label = f"{now.month}月{now.day}日"
        timestamp = now.timestamp()

        def _cleanup_expired_records():
            """清理超过7天的历史记录。"""
            history = self.get_data("_history") or []
            if not isinstance(history, list):
                return
            cutoff = now.timestamp() - 7 * 24 * 3600
            before = len(history)
            history = [r for r in history if r.get("timestamp", 0) >= cutoff]
            if len(history) != before:
                logger.info(f"已清理 {before - len(history)} 条过期记录")
                self.save_data("_history", history)

        def _append_history(record: dict):
            """追加记录到历史列表，自动清理7天前的记录。"""
            # 先清理过期记录
            _cleanup_expired_records()
            history = self.get_data("_history") or []
            if not isinstance(history, list):
                history = []
            history.insert(0, record)
            if len(history) > 100:
                history = history[:100]
            self.save_data("_history", history)

        if not current:
            logger.warning("MP插件健康检测: 无法获取插件列表")
            _append_history({
                "key": day_key, "plugin_count": 0, "lost": 0, "stopped": 0,
                "upgraded": 0, "new": 0, "has_changes": False,
                "lost_names": [], "stopped_names": [],
                "upgraded_names": [], "new_names": [],
                "status": "无法获取插件列表",
                "run_time": run_time, "date_label": date_label,
                "timestamp": timestamp
            })
            return
        baseline = self.__load_snapshot()
        if not baseline:
            self.__save_snapshot(current)
            logger.info(f"MP插件健康检测: 首次运行，已保存 {len(current)} 个插件作为基准")
            if self._notify:
                self.post_message(
                    title="MP插件健康检测 - 首次运行",
                    text=f"已保存 {len(current)} 个插件作为基准，下次将对比变化并通知",
                    mtype=NotificationType.Plugin
                )
            _append_history({
                "key": day_key, "plugin_count": len(current), "lost": 0, "stopped": 0,
                "upgraded": 0, "new": 0, "has_changes": False,
                "lost_names": [], "stopped_names": [],
                "upgraded_names": [], "new_names": [],
                "status": "首次运行 - 已建立基准",
                "run_time": run_time, "date_label": date_label,
                "timestamp": timestamp
            })
            return
        baseline_map = {p["id"]: p for p in baseline}
        current_map = {p["id"]: p for p in current}
        changes = []
        lost_names = []
        stopped_names = []
        started_names = []
        upgraded_names = []
        new_names = []
        lost_plugin_ids = []
        for pid, p in baseline_map.items():
            if pid not in current_map:
                # 彻底丢失（已卸载）
                changes.append(f"丢失: {p['name']} ({pid})")
                logger.warning(f"  插件丢失: {p['name']} ({pid})")
                lost_names.append(p['name'])
                lost_plugin_ids.append(pid)
            else:
                cp = current_map[pid]
                if p.get("state") and not cp.get("state"):
                    # 停用
                    changes.append(f"停用: {cp['name']} ({pid})")
                    logger.warning(f"  插件停用: {cp['name']} ({pid})")
                    stopped_names.append(cp['name'])
                elif not p.get("state") and cp.get("state"):
                    # 重新启用
                    changes.append(f"启用: {cp['name']} ({pid})")
                    logger.info(f"  插件启用: {cp['name']} ({pid})")
                    started_names.append(cp['name'])
                ov = p.get("version", "")
                nv = cp.get("version", "")
                if ov and nv and ov != nv:
                    changes.append(f"升级: {cp['name']} {ov}->{nv}")
                    logger.info(f"  插件升级: {cp['name']} {ov}->{nv}")
                    upgraded_names.append(f"{cp['name']} {ov}->{nv}")
        for pid, p in current_map.items():
            if pid not in baseline_map:
                changes.append(f"新增: {p['name']} v{p.get('version', '?')}")
                logger.info(f"  插件新增: {p['name']} v{p.get('version', '?')}")
                new_names.append(f"{p['name']} v{p.get('version', '?')}")

        # 自动安装丢失的插件
        if lost_plugin_ids and self._auto_install:
            logger.info(f"MP插件健康检测: 自动安装 {len(lost_plugin_ids)} 个丢失的插件...")
            online_plugins_map = self._get_online_plugins_map()
            for pid in lost_plugin_ids:
                success = self._install_lost_plugin(pid, online_plugins_map)
                if success:
                    auto_installed.append(pid)
                else:
                    auto_failed.append(pid)
            # 重载已安装的插件使其生效
            for pid in auto_installed:
                try:
                    self._reload_plugin(pid)
                    logger.info(f"  插件 {pid} 重载成功")
                except Exception as e:
                    logger.error(f"  插件 {pid} 重载失败: {str(e)}")

        # 安装后重新获取插件列表，更新对比结果
        if auto_installed:
            current = self.__get_plugin_list()
            current_map = {p["id"]: p for p in current}
            # 重新计算变更
            changes = []
            lost_names = []
            stopped_names = []
            started_names = []
            upgraded_names = []
            new_names = []
            for pid, p in baseline_map.items():
                if pid not in current_map:
                    if pid not in auto_failed:
                        changes.append("丢失: %s (%s)" % (p['name'], pid))
                        lost_names.append(p['name'])
                else:
                    cp = current_map[pid]
                    if p.get("state") and not cp.get("state"):
                        changes.append("停用: %s (%s)" % (cp['name'], pid))
                        stopped_names.append(cp['name'])
                    elif not p.get("state") and cp.get("state"):
                        changes.append("启用: %s (%s)" % (cp['name'], pid))
                        started_names.append(cp['name'])
            for pid, p in current_map.items():
                if pid in baseline_map:
                    old = baseline_map[pid]
                    ov = old.get("version", "")
                    nv = p.get("version", "")
                    if ov and nv and ov != nv:
                        changes.append("升级: %s %s->%s" % (p['name'], ov, nv))
                        upgraded_names.append("%s %s->%s" % (p['name'], ov, nv))
                else:
                    changes.append("新增: %s v%s" % (p['name'], p.get('version', '?')))
                    new_names.append("%s v%s" % (p['name'], p.get('version', '?')))
            # 保存新快照
            self.__save_snapshot(current)

        now_ts = datetime.now()
        run_time = now_ts.strftime("%H:%M:%S")
        day_key = f"{now_ts.month}月{now_ts.day}日 {now_ts.hour:02d}:{now_ts.minute:02d}"
        date_label = f"{now_ts.month}月{now_ts.day}日"
        now_str = now_ts.strftime("%m-%d %H:%M")
        timestamp = now_ts.timestamp()
        if not changes:
            logger.info("MP插件健康检测: 无变化")
            _append_history({
                "key": day_key, "plugin_count": len(current), "lost": 0, "stopped": 0,
                "upgraded": 0, "new": 0, "has_changes": False,
                "lost_names": [], "stopped_names": [],
                "upgraded_names": [], "new_names": [],
                "status": "正常",
                "run_time": run_time, "date_label": date_label,
                "timestamp": timestamp
            })
            if self._notify:
                msg_parts = ["✅ 所有插件状态正常（共 %d 个）" % len(current)]
                if auto_installed:
                    msg_parts.append("📥 自动安装结果:")
                    for pid in auto_installed:
                        name = baseline_map.get(pid, {}).get("name", pid)
                        msg_parts.append("  ✅ %s - 已完成" % name)
                msg_parts.append("⏰ 时间: %s" % now_str)
                self.post_message(
                    title="✅ MP插件健康检测",
                    text="\n".join(msg_parts),
                    mtype=NotificationType.Plugin
                )
            return
        # 有变更，保存执行记录
        _append_history({
            "key": day_key, "plugin_count": len(current), "lost": len(lost_names),
            "stopped": len(stopped_names), "upgraded": len(upgraded_names),
            "new": len(new_names), "has_changes": True,
            "lost_names": lost_names, "stopped_names": stopped_names,
            "started_names": started_names,
            "upgraded_names": upgraded_names, "new_names": new_names,
            "run_time": run_time, "date_label": date_label,
            "timestamp": timestamp, "status": "有变更"})
        if self._notify:
            parts = []
            # 丢失插件
            if lost_plugin_ids:
                lost_detail = []
                for pid in lost_plugin_ids:
                    name = baseline_map.get(pid, {}).get("name", pid)
                    if pid in auto_installed:
                        lost_detail.append("  ✅ %s - 已自动安装" % name)
                    elif pid in auto_failed:
                        lost_detail.append("  ❌ %s - 自动安装失败" % name)
                    else:
                        lost_detail.append("  🔴 %s - 未处理" % name)
                parts.append("🔴 丢失插件 (%d个):" % len(lost_plugin_ids))
                parts.extend(lost_detail)
            # 停用插件
            if stopped_names:
                parts.append("⚠️ 停用插件 (%d个):" % len(stopped_names))
                for name in stopped_names:
                    parts.append("  ⚠️ %s" % name)
            # 启用插件
            if started_names:
                parts.append("✅ 启用插件 (%d个):" % len(started_names))
                for name in started_names:
                    parts.append("  ✅ %s" % name)
            # 新增插件
            if new_names:
                parts.append("🆕 新增插件 (%d个):" % len(new_names))
                for name in new_names:
                    parts.append("  🆕 %s" % name)
            # 升级插件
            if upgraded_names:
                parts.append("⬆️ 升级插件 (%d个):" % len(upgraded_names))
                for name in upgraded_names:
                    parts.append("  ⬆️ %s" % name)
            parts.append("⏰ 时间: %s" % now_str)
            self.post_message(
                title="🔔 MP插件健康检测 - 变更报告",
                text="\n".join(parts),
                mtype=NotificationType.Plugin
            )

    def _get_online_plugins_map(self) -> Dict[str, Any]:
        """获取市场插件 ID 到插件信息的映射。"""
        try:
            from app.core.plugin import PluginManager
            pm = PluginManager()
            online_plugins = pm.get_online_plugins()
            result = {}
            for p in online_plugins:
                result[p.id] = p
            logger.info(f"获取到 {len(result)} 个市场插件")
            return result
        except Exception as e:
            logger.error(f"获取市场插件列表失败: {str(e)}")
            return {}

    def _install_lost_plugin(self, plugin_id: str, online_map: Dict[str, Any]) -> bool:
        """安装单个丢失的插件。"""
        try:
            plugin_info = online_map.get(plugin_id)
            if not plugin_info:
                logger.warning(f"  插件 {plugin_id} 未在市场找到，跳过安装")
                return False
            repo_url = plugin_info.repo_url
            logger.info(f"  开始安装插件: {plugin_info.plugin_name} ({plugin_id}), repo={repo_url}")
            helper = PluginHelper()
            state, msg = helper.install(pid=plugin_id, repo_url=repo_url, force_install=True)
            if state:
                MoviePilotServerHelper.install_plugin_reg(plugin_id=plugin_id, repo_url=repo_url)
                logger.info(f"  插件 {plugin_id} 安装成功")
                return True
            else:
                logger.error(f"  插件 {plugin_id} 安装失败: {msg}")
                return False
        except Exception as e:
            logger.error(f"  插件 {plugin_id} 安装异常: {str(e)}")
            return False

    def _reload_plugin(self, plugin_id: str):
        """重载指定插件使其生效。"""
        try:
            from app.core.plugin import PluginManager
            pm = PluginManager()
            pm.reload_plugin(plugin_id)
        except Exception as e:
            logger.error(f"  插件 {plugin_id} 重载失败: {str(e)}")
            raise
