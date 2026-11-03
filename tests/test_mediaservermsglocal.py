"""隔离 MoviePilot 的数据库和通知边界，回放实际入库报文。"""
import ast
import re
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from unittest.mock import Mock

import pytest


@pytest.fixture
def plugin_class():
    """加载原插件类，隔离框架导入及事件注册。"""
    source = Path(__file__).parents[1] / 'plugins.v2/mediaservermsglocal/__init__.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = [d for d in node.decorator_list
                                   if isinstance(d, ast.Name) and d.id == 'staticmethod']
    namespace = dict(re=re, json=json, threading=threading, time=time, Path=Path,
                     Any=Any, Dict=Dict, List=List, Optional=Optional, Tuple=Tuple,
                     quote=quote, _PluginBase=object, WebhookEventInfo=object,
                     ServiceInfo=object, Event=object, MediaType=SimpleNamespace(TV='tv', MOVIE='movie'),
                     MediaImageType=Mock(), NotificationType=Mock(), MediaServerItem=object,
                     settings=SimpleNamespace(APP_DOMAIN='http://mp.local', TMDB_IMAGE_DOMAIN='image.tmdb.org'),
                     logger=Mock(), CategoryHelper=Mock(), MediaServerHelper=Mock(), WebUtils=Mock())
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['MediaServerMsgLocal']


def summary(description):
    """构造 Emby 将多集汇总成一个 Series 通知的脱敏样本。"""
    return SimpleNamespace(item_type='TV', media_type='Series', item_name='Demo',
                           item_path='/tv/Demo {tmdbid=123}', season_id=None,
                           episode_id=None, channel='emby', server_name='home',
                           json_object={'Item': {'Type': 'Series', 'Id': '100', 'Name': 'Demo'},
                                        'Description': description})


@pytest.mark.parametrize('description, expected', [
    ('S01 E25, E29\n\nTmdbId: 123', 'S01E25, S01E29'),
    ('S01 E26-E27\n\nTmdbId: 123', 'S01E26-E27'),
    ('S01 E01-E03, E05\nS02 E01-E02', 'S01E01-E03, S01E05, S02E01-E02'),
    ('S00 E01-E02', 'S00E01-E02'),
])
def test_emby_summary_keeps_every_episode(plugin_class, description, expected):
    plugin = plugin_class()
    assert plugin._merge_continuous_episodes([summary(description)]) == expected


def test_episode_count_uses_unique_episodes_not_webhook_count(plugin_class):
    plugin = plugin_class()
    assert plugin._get_episode_count([summary('S01 E25, E29'), summary('S01 E29')]) == 2


def test_episode_metadata_preserves_special_season_and_end_index(plugin_class):
    plugin = plugin_class()
    event = summary('')
    event.json_object['Item'] = {'Type': 'Episode', 'ParentIndexNumber': 0,
                                 'IndexNumber': 1, 'IndexNumberEnd': 3}
    assert plugin._merge_continuous_episodes([event]) == 'S00E01-E03'


def test_unparsed_series_does_not_invent_first_episode(plugin_class):
    plugin = plugin_class()
    assert plugin._merge_continuous_episodes([summary('New episodes')]) == '剧集更新（未提供集数）'


def test_single_episode_and_duplicates(plugin_class):
    plugin = plugin_class()
    event = summary('')
    event.item_name = 'Demo S01E07'
    event.json_object['Item'] = {'Type': 'Episode', 'ParentIndexNumber': 1, 'IndexNumber': 7}
    detail = plugin._merge_continuous_episodes([event, event])
    assert detail.startswith('S01E07')
    assert detail.count('S01E07') == 1


def test_image_proxy_wraps_once_and_uses_configured_external_domain(plugin_class):
    plugin = plugin_class()
    plugin.get_config = Mock(return_value={'enabled': True, 'external_domain': 'https://images.local/'})
    raw = 'https://image.tmdb.org/t/p/w500/test.jpg'
    result = plugin._get_cached_image_url(raw)
    assert result == 'https://images.local/api/v1/plugin/ImageLocalCache/img?url=' + quote(raw, safe='')
    assert plugin._get_cached_image_url(result) == result


def test_icon_uses_frontend_builtin_asset(plugin_class):
    assert plugin_class.plugin_icon == 'mediaplay.png'
    plugin = plugin_class()
    route = next(api for api in plugin.get_api() if api['path'] == '/icon')
    assert route['allow_anonymous'] is True


def test_new_episode_restarts_aggregation_wait(plugin_class, monkeypatch):
    """同魔改版一样从最后一条通知重新计算等待时间。"""
    timers = []
    def create_timer(interval, function, args=None, kwargs=None):
        timer = SimpleNamespace(interval=interval, function=function, args=args or [],
                                kwargs=kwargs or {}, start=Mock(), cancel=Mock())
        timers.append(timer)
        return timer
    monkeypatch.setattr(threading, 'Timer', create_timer)
    plugin = plugin_class()
    plugin._aggregate_time = 60
    plugin._aggregate_tv_episodes('demo', summary('S01 E01'))
    plugin._aggregate_tv_episodes('demo', summary('S01 E02'))
    assert len(timers) == 2
    timers[0].cancel.assert_called_once()
    assert plugin._aggregate_timers['demo'] is timers[1]
    # 即使取消旧定时器时它刚好已经触发，旧回调也不能提前取走新队列。
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    assert len(plugin._pending_messages['demo']) == 2


@pytest.mark.parametrize('has_tmdb', [True, False])
def test_aggregated_notification_title_detail_and_cached_image(plugin_class, has_tmdb):
    plugin = plugin_class()
    event = summary('S01 E26-E27')
    event.event = 'library.new'
    event.image_url = 'https://image.tmdb.org/t/p/original/test.jpg'
    event.item_path = '/tv/Demo' if not has_tmdb else event.item_path
    plugin._pending_messages['demo'] = [event]
    plugin.service_info = Mock(return_value=None)
    plugin.get_config = Mock(return_value={'external_domain': 'http://cache.local'})
    plugin._get_tmdb_info = Mock(return_value=None)
    plugin._get_subscriber_names = Mock(return_value='test')
    plugin.post_message = Mock()
    plugin._record_history = Mock()
    plugin._send_aggregated_message('demo')
    plugin.post_message.assert_called_once()
    message = plugin.post_message.call_args.kwargs
    assert message['title'].endswith('S01E26-E27')
    assert 'S01E26-E27' in message['title']
    assert '📺 季集：' not in message['text']
    assert message['image'].startswith('http://cache.local/api/v1/plugin/ImageLocalCache/img?url=')


def test_no_aggregation_notification_retains_all_episodes(plugin_class):
    plugin = plugin_class()
    plugin._enabled = True
    plugin._types = ['library.new']
    plugin._aggregate_enabled = False
    plugin.service_infos = Mock(return_value={'home': object()})
    plugin.service_info = Mock(return_value=object())
    plugin.get_config = Mock(return_value={})
    plugin._get_subscriber_names = Mock(return_value='test')
    plugin.post_message = Mock()
    plugin._record_history = Mock()
    event = summary('S01 E25, E29')
    event.event = 'library.new'
    plugin.send(SimpleNamespace(event_data=event))
    message = plugin.post_message.call_args.kwargs
    assert 'S01E25, S01E29' in message['title']
    assert '📺 季集：' not in message['text']
    assert message['title'].endswith('S01E25, S01E29')


def test_normalized_special_season_without_raw_item(plugin_class):
    event = summary('')
    event.json_object = {}
    event.media_type = 'Episode'
    event.season_id = 0
    event.episode_id = 1
    assert plugin_class()._merge_continuous_episodes([event]) == 'S00E01'


def test_same_show_on_different_servers_has_separate_group(plugin_class):
    plugin = plugin_class()
    first, second = summary('S01 E01'), summary('S01 E02')
    second.server_name = 'other'
    assert plugin._get_series_id(first) != plugin._get_series_id(second)


def test_different_series_sharing_library_have_separate_group(plugin_class):
    plugin = plugin_class()
    first, second = summary('S01 E01'), summary('S01 E02')
    for event, identity in [(first, '100'), (second, '200')]:
        event.item_path = '/tv/' + identity
        event.json_object['Item'].update(Id=identity, ParentId='library')
    assert plugin._get_series_id(first) != plugin._get_series_id(second)


def test_emby_native_webhook_test_is_supported(plugin_class):
    plugin = plugin_class()
    plugin._enabled = True
    plugin._types = ['system.webhooktest']
    plugin.service_infos = Mock(return_value={'home': object()})
    plugin.service_info = Mock(return_value=object())
    plugin.get_config = Mock(return_value={})
    plugin.post_message = Mock()
    plugin._record_history = Mock()
    event = SimpleNamespace(event='system.webhooktest', channel='emby', server_name='home',
                            item_type=None, item_name=None, json_object={'Event':'system.webhooktest'})
    plugin.send(SimpleNamespace(event_data=event))
    plugin.post_message.assert_called_once()
    assert 'Emby Webhook' in plugin.post_message.call_args.kwargs['title']
