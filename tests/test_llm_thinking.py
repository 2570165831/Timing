"""Verify thinking controls on the actual wire, without calling a remote model."""
import inspect
import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from timing.llm_splitter import LlmConfig, LlmSession, use_llm

PHONES = ('K','AH1','N','T','R','IY0')
PARTS = [{'text':'coun','phone_start':0,'phone_end':3},
         {'text':'try','phone_start':3,'phone_end':6}]


def reply(parts=PARTS):
    return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
        'content':json.dumps({'items':[{'id':'w0','parts':parts}]}),
        'reasoning_content':'private reasoning must not be persisted'}}]})


@pytest.mark.parametrize('mode,format,effort,expected',[
    ('default','reasoning_effort','high',{}),
    ('on','reasoning_effort','high',{'reasoning_effort':'high'}),
    ('on','reasoning_effort','ultra',{'reasoning_effort':'ultra'}),
    ('off','reasoning_effort','high',{'reasoning_effort':'none'}),
    ('on','enable_thinking','',{'enable_thinking':True}),
    ('on','enable_thinking','medium',{'enable_thinking':True,'reasoning_effort':'medium'}),
    ('off','enable_thinking','high',{'enable_thinking':False}),
    ('on','thinking','',{'thinking':{'type':'enabled'}}),
    ('on','thinking','xhigh',{'thinking':{'type':'enabled'},'reasoning_effort':'xhigh'}),
    ('off','thinking','high',{'thinking':{'type':'disabled'}}),
])
def test_thinking_parameters_reach_wire_and_audit_but_private_data_does_not(tmp_path,mode,format,effort,expected):
    config = LlmConfig('https://fixture.example/v1','fixture','secret-fixture',True,mode,format,effort)
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        actual = {k:v for k,v in body.items() if k not in ('model','messages','stream','response_format')}
        assert actual == expected
        assert body['response_format'] == {'type':'json_object'}
        return reply()
    with use_llm(config,log=lambda _:None,output=tmp_path) as session:
        session.client.close()
        session.client = httpx.Client(transport=httpx.MockTransport(handler))
        assert session.split('country',PHONES) == (('coun',0,3),('try',3,6))
        assert session.report()['thinking']['requested_parameters'] == expected
    assert len(calls) == 1
    audit = (tmp_path/'llm_splits.json').read_text(encoding='utf-8')
    assert all(secret not in audit for secret in ('secret-fixture','fixture.example','private reasoning'))


@pytest.mark.parametrize('changes',[
    {'thinking_mode':'invalid'}, {'thinking_format':'invalid'},
    {'reasoning_effort':'high\nmalformed'}, {'reasoning_effort':'x'*33},
    {'thinking_mode':'on','reasoning_effort':'none'},
    {'thinking_mode':'on','reasoning_effort':''},
])
def test_invalid_thinking_config_rejected_before_request(changes):
    with pytest.raises(ValueError):
        LlmConfig('https://fixture.example/v1','fixture',**changes)


def test_repair_retains_requested_effort_and_http_rejection_does_not_silently_disable_it():
    config = LlmConfig('https://fixture.example/v1','fixture',thinking_mode='on',reasoning_effort='max')
    with use_llm(config,log=lambda _:None) as session:
        session.client.close()
        calls = []
        def handler(request):
            body = json.loads(request.content); calls.append(body)
            assert body['reasoning_effort'] == 'max'
            return reply([{'text':'wrong','phone_start':0,'phone_end':6}]) if len(calls)==1 else reply()
        session.client = httpx.Client(transport=httpx.MockTransport(handler))
        assert session.split('country',PHONES)
        assert session.requests == 2
    with use_llm(config,log=lambda _:None) as session:
        session.client.close()
        session.client = httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(400,text='secret')))
        assert session.split('country',PHONES) is None
        assert session.requests == 1 and session.disabled
        assert '思考参数' in session.cache[('country',PHONES)]['reason']
        assert 'secret' not in json.dumps(session.report())


def test_environment_controls(monkeypatch):
    for key,value in {'BASE_URL':'https://fixture.example/v1','MODEL':'fixture',
                      'THINKING_MODE':'on','THINKING_FORMAT':'thinking','REASONING_EFFORT':'xhigh'}.items():
        monkeypatch.setenv('TIMING_LLM_'+key,value)
    assert LlmConfig.from_env().thinking_parameters() == {'thinking':{'type':'enabled'},'reasoning_effort':'xhigh'}


def test_both_api_routes_forward_same_controls_and_reject_invalid_values(monkeypatch,tmp_path):
    from timing import server
    monkeypatch.setattr(server,'DATA',tmp_path)
    monkeypatch.setattr(server,'jobs',{})
    monkeypatch.setattr(server,'initialize',lambda:None)
    queued = []
    monkeypatch.setattr(server.executor,'submit',lambda *args:queued.append(args))
    original = LlmSession.__init__
    outgoing = []
    def init(self,*args,**kwargs):
        original(self,*args,**kwargs); self.client.close()
        def handler(request):
            outgoing.append(json.loads(request.content))
            return reply()
        self.client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(LlmSession,'__init__',init)
    data = {'english_splitter':'llm','llm_base_url':'https://fixture.example/v1','llm_model':'fixture',
            'llm_thinking_mode':'on','llm_thinking_format':'enable_thinking','llm_reasoning_effort':'xhigh',
            'lyrics':'[00:00.00]country'}
    with TestClient(server.app) as client:
        headers = {'X-Timing-Token':server.TOKEN}
        response = client.post('/api/llm/test',data=data,headers=headers)
        assert response.json()['ok']
        assert outgoing[0]['enable_thinking'] is True and outgoing[0]['reasoning_effort']=='xhigh'
        response = client.post('/api/jobs',data=data,files={'audio':('a.wav',b'audio')},headers=headers)
        assert response.status_code == 200
        config = inspect.signature(server.process).bind(*queued[0][1:]).arguments['llm_config']
        assert config.thinking_parameters() == response_for_test(outgoing[0])
        # An empty effort is allowed for switch-only formats through FastAPI too.
        data['llm_reasoning_effort'] = ''
        assert client.post('/api/llm/test',data=data,headers=headers).json()['thinking']['requested_parameters']=={'enable_thinking':True}
        for url in ('/api/llm/test','/api/jobs'):
            bad = {**data,'llm_thinking_mode':'invalid'}
            response = client.post(url,data=bad,headers=headers,files={'audio':('a.wav',b'audio')})
            assert response.status_code == 400


def response_for_test(body):
    return {k:body[k] for k in ('enable_thinking','reasoning_effort')}


def test_cli_thinking_flags_override_environment_without_needing_env_address(monkeypatch,tmp_path):
    from timing import __main__, pipeline
    audio = tmp_path/'a.wav'; audio.write_bytes(b'audio')
    lyrics = tmp_path/'lyrics.txt'; lyrics.write_text('[00:00.10]hello',encoding='utf-8')
    monkeypatch.delenv('TIMING_LLM_BASE_URL',raising=False)
    monkeypatch.delenv('TIMING_LLM_MODEL',raising=False)
    monkeypatch.setenv('TIMING_LLM_REASONING_EFFORT','low')
    monkeypatch.setattr(__main__,'initialize',lambda:None)
    calls = []
    def run(*args,**kwargs):
        calls.append(kwargs['llm_config'])
        args[2].mkdir(); (args[2]/'lyrics.ttml').write_bytes(b'<tt/>')
        return {'status':'complete'}
    monkeypatch.setattr(pipeline,'run',run)
    monkeypatch.setattr(sys,'argv',['timing',str(audio),'--lyrics',str(lyrics),'--english-splitter','llm',
        '--llm-base-url','https://fixture.example/v1','--llm-model','fixture',
        '--llm-thinking-mode','on','--llm-thinking-format','thinking','--llm-reasoning-effort','max'])
    __main__.main()
    assert calls[0].thinking_parameters() == {'thinking':{'type':'enabled'},'reasoning_effort':'max'}
