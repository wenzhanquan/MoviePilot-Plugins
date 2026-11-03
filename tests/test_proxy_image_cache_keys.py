import ast
import hashlib
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

SOURCE=Path(__file__).parents[1]/'plugins.v2/imagelocalcache/__init__.py'

def test_proxy_posters_are_cached_separately_and_other_urls_unchanged(monkeypatch):
    class ImageHelper:
        @staticmethod
        def _prepare_cache_path(url):
            return urlsplit(url).path.lstrip('/')+'.jpg'
    module=ModuleType('app.helper.image');module.ImageHelper=ImageHelper
    monkeypatch.setitem(sys.modules,'app.helper.image',module)
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_patch_image_helper_cache']
    assert functions, 'Proxy URLs must not share the MP image cache key'
    ns={'sha256':hashlib.sha256,'urlsplit':urlsplit}
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[])),str(SOURCE),'exec'),ns)
    ns['_patch_image_helper_cache']()
    poster='http://nas/api/v1/plugin/ImageLocalCache/img?url=https%3A%2F%2Fimage.tmdb.org%2Ft%2Fp%2Foriginal%2Fresident.jpg'
    other=poster.replace('resident.jpg','spiderman.jpg')
    keys=[ImageHelper._prepare_cache_path(x) for x in [poster,other]]
    assert keys[0]!=keys[1]
    cache={keys[0]:b'resident evil',keys[1]:b'spider man'}
    assert cache[ImageHelper._prepare_cache_path(poster)]==b'resident evil'
    normal='https://image.tmdb.org/t/p/w500/resident.jpg'
    assert ImageHelper._prepare_cache_path(normal)=='t/p/w500/resident.jpg.jpg'
    ns['_patch_image_helper_cache']()
    assert ImageHelper._prepare_cache_path(poster)==keys[0]
