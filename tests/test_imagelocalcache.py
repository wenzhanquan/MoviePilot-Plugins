"""验证缓存命中时保留真实图片类型，不访问网络。"""
import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import mimetypes
import re
import sys


def test_cached_webp_with_jpg_filename_keeps_actual_mime(tmp_path, monkeypatch):
    """TMDB 可用 jpg 路径返回 WebP，缓存命中也应返回 WebP。"""
    source = Path(__file__).parents[1] / 'plugins.v2/imagelocalcache/__init__.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ImageLocalCache')
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ['serve_image','_guess_mime_type']]
    isolated = ast.ClassDef(name='ImageLocalCache', bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = dict(Path=Path, re=re, logger=Mock())
    exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[])), str(source), 'exec'), namespace)

    class FileResponse:
        """替代 FastAPI 文件响应边界，仅记录 MIME。"""
        def __init__(self, path, headers=None, media_type=None):
            self.media_type = media_type or mimetypes.guess_type(str(path))[0]

    responses = ModuleType('fastapi.responses')
    responses.FileResponse = FileResponse
    responses.RedirectResponse = Mock()
    responses.Response = Mock()
    monkeypatch.setitem(sys.modules, 'fastapi', ModuleType('fastapi'))
    monkeypatch.setitem(sys.modules, 'fastapi.responses', responses)
    cache = tmp_path / 'w500' / 'sample.jpg'
    cache.parent.mkdir()
    cache.write_bytes(b'RIFF1234WEBP1234567890')
    plugin = namespace['ImageLocalCache']()
    plugin._enabled = True
    plugin._cache_dir = str(tmp_path)
    result = plugin.serve_image('https://image.tmdb.org/t/p/w500/sample.jpg')
    assert result.media_type == 'image/webp'
