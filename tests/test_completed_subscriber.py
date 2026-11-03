from pathlib import Path
import sys,re
from types import ModuleType,SimpleNamespace
from test_nov3_regressions import load

def test_completed_subscription_owner_survives_active_row_removal(monkeypatch):
    rows=[SimpleNamespace(tmdbid=123,season=1,username='alice'),SimpleNamespace(tmdbid=123,season=2,username='bob')]
    class Field:
        def __init__(self,name):self.name=name
        def __eq__(self,value):return self.name,value
    class Query:
        def __init__(self):self.rows=rows[:]
        def filter(self,*conditions):
            for name,value in conditions:self.rows=[r for r in self.rows if getattr(r,name)==value]
            return self
        def limit(self,n):return self
        def all(self):return self.rows
    class Session:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def query(self,*args):return Query()
    modules={
        'app.db':{'SessionFactory':Session},
        'app.db.models.subscribehistory':{'SubscribeHistory':SimpleNamespace(tmdbid=Field('tmdbid'),season=Field('season'))},
        'app.db.subscribe_oper':{'SubscribeOper':lambda:SimpleNamespace(list_by_tmdbid=lambda *a,**kw:[])},
        'app.db.user_oper':{'UserOper':lambda:SimpleNamespace(get_settings=lambda owner:{'wechat_userid':owner.upper()})},
    }
    for name,attrs in modules.items():
        m=ModuleType(name);m.__dict__.update(attrs);monkeypatch.setitem(sys.modules,name,m)
    fn=load(str(Path(__file__).resolve().parents[1]/'plugins.v2/mediaservermsglocal/__init__.py'),'_get_subscriber_names',{'re':re,'logger':SimpleNamespace(warning=lambda *a:None)})
    assert fn(None,'123',season=1)=='ALICE'
    assert fn(None,'123',season=2)=='BOB'
