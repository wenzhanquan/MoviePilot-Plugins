import ast
from pathlib import Path
from types import SimpleNamespace

def load(path,name,ns):
    tree=ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[]
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),path,'exec'),ns)
    return ns[name]

def test_selected_download_range_reserved_before_library_import():
    captured=[]
    ns={'SubscribeOper':lambda:SimpleNamespace(update=lambda uid,data:captured.append(data)), 'MediaType':SimpleNamespace(TV='tv',MOVIE='movie'),'resolve_media_identity':lambda media:('tmdb','275297')}
    f=load(str(Path(__file__).resolve().parents[1]/'recovery/subscribe_note_fix.py'),'__update_subscribe_note',ns)
    sub=SimpleNamespace(id=51,note=[5],tmdbid=275297,doubanid=None,bangumiid=None,anilistid=None)
    media=SimpleNamespace(tmdb_id=275297,douban_id=None,bangumi_id=None,anilist_id=None,type='tv')
    f(sub,[SimpleNamespace(media_info=media,meta_info=SimpleNamespace(episode_list=[1]),selected_episodes=[1,2,3,4])])
    assert set(captured[-1]['note'])=={1,2,3,4,5}

def test_episode_range_in_title():
    f=load(str(Path(__file__).resolve().parents[1]/'plugins.v2/mediaservermsglocal/__init__.py'),'_episode_title',{})
    assert f('📺 新入库剧集：走马上任','S01E01-E04',4)=='📺 新入库剧集：走马上任 S01E01-E04'
    assert f('📺 新入库剧集：走马上任','S01E03',1)=='📺 新入库剧集：走马上任：S01E03'

def test_manual_snapshot_really_replaces_baseline():
    f=load(str(Path(__file__).resolve().parents[1]/'plugins.v2/mppluginhealthcheck/__init__.py'),'_api_update_snapshot',{'logger':SimpleNamespace(info=lambda *a:None)})
    writes=[];current=[{'id':'ImageLocalCache','version':'1.1.7'}]
    obj=SimpleNamespace(__get_plugin_list=lambda:current,__save_snapshot=lambda data,source:writes.append((data,source)))
    assert f(obj)['success']
    assert writes==[(current,'手动快照')]
    obj.__get_plugin_list=lambda:[]
    assert not f(obj)['success']
    assert len(writes)==1

