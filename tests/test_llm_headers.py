import inspect
import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from timing.llm_splitter import LlmConfig, LlmSession


@pytest.mark.parametrize('name,format,expected',[
    ('Authorization','auto','Bearer secret-fixture'),
    ('authorization','auto','Bearer secret-fixture'),
    ('x-api-key','auto','secret-fixture'),
    ('api-key','raw','secret-fixture'),
    ('Authorization','raw','secret-fixture'),
    ('x-custom-key','bearer','Bearer secret-fixture'),
])
@pytest.mark.parametrize('audio',[None,'data:audio/wav;base64,Zml4dHVyZQ=='])
def test_auth_header_reaches_text_and_audio_wire_without_leaking(name,format,expected,audio):
    config=LlmConfig('https://fixture.example/v1','m','secret-fixture',api_key_header=name,api_key_format=format)
    session=LlmSession(config,lambda _:None)
    session.client.close()
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.headers[name]==expected
        if name.lower()!='authorization':
            assert 'authorization' not in request.headers
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
    session.client=httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert session._chat({'items':[]},audio=audio)==([],None)
        assert session._chat({'items':[]},repair=True,audio=audio)==([],None)
        assert len(calls)==2
        assert 'secret-fixture' not in repr(config)+json.dumps(session.report())
    finally:
        session.close()


@pytest.mark.parametrize('name',['bad header','x-key:','x-key\r\nother','中文','host','Content-Length','transfer-encoding'])
def test_invalid_header_name_rejected_before_network(name):
    with pytest.raises(ValueError,match='请求头名称'):
        LlmConfig('https://fixture.example/v1','m',api_key_header=name)


def test_environment_and_empty_key(monkeypatch):
    for key,value in {'BASE_URL':'https://fixture.example/v1','MODEL':'m','API_KEY':'',
                      'API_KEY_HEADER':'x-api-key','API_KEY_FORMAT':'raw'}.items():
        monkeypatch.setenv('TIMING_LLM_'+key,value)
    config=LlmConfig.from_env()
    assert config.api_key_header=='x-api-key' and config.api_key_format=='raw'
    assert config.auth_headers()=={}


def test_api_test_and_direct_job_share_custom_auth_config(monkeypatch,tmp_path):
    from timing import server
    monkeypatch.setattr(server,'DATA',tmp_path)
    monkeypatch.setattr(server,'jobs',{})
    monkeypatch.setattr(server,'initialize',lambda:None)
    queued=[]
    monkeypatch.setattr(server.executor,'submit',lambda *args:queued.append(args))
    original=LlmSession.__init__
    calls=[]
    def init(self,*args,**kwargs):
        original(self,*args,**kwargs);self.client.close()
        def handler(request):
            assert request.headers['x-api-key']=='secret-fixture'
            assert 'authorization' not in request.headers
            calls.append(request)
            content={'items':[{'id':'w0','parts':[{'text':'country','phone_start':0,'phone_end':6}]}]}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(content)}}]})
        self.client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(LlmSession,'__init__',init)
    data={'aligner':'llm_direct','llm_model':'m','llm_base_url':'https://fixture.example/v1',
          'llm_api_key':'secret-fixture','llm_api_key_header':'x-api-key','llm_api_key_format':'raw',
          'lyrics':'[00:00.00]hi'}
    with TestClient(server.app) as client:
        headers={'X-Timing-Token':server.TOKEN}
        assert client.post('/api/llm/test',data=data,headers=headers).json()['ok']
        response=client.post('/api/jobs',data=data,headers=headers,files={'audio':('a.wav',b'audio')})
        assert response.status_code==200
        config=inspect.signature(server.process).bind(*queued[0][1:]).arguments['llm_config']
        assert config.auth_headers()=={'x-api-key':'secret-fixture'}
        assert 'secret-fixture' not in client.get('/api/jobs/'+response.json()['id']).text
        assert 'secret-fixture' not in next(tmp_path.glob('*/job.json')).read_text()
        for url in ('/api/llm/test','/api/jobs'):
            response=client.post(url,data={**data,'llm_api_key_header':'bad header'},headers=headers,
                                 files={'audio':('a.wav',b'audio')})
            assert response.status_code==400
    assert len(calls)==1


def test_cli_custom_headers_override_environment(monkeypatch,tmp_path):
    from timing import __main__,pipeline
    audio=tmp_path/'a.wav';audio.write_bytes(b'audio')
    lyrics=tmp_path/'lyrics.txt';lyrics.write_text('[00:00.10]hello',encoding='utf-8')
    monkeypatch.setenv('TIMING_LLM_API_KEY_HEADER','Authorization')
    monkeypatch.setenv('TIMING_LLM_API_KEY_FORMAT','bearer')
    monkeypatch.setenv('TIMING_LLM_API_KEY','secret-fixture')
    monkeypatch.setattr(__main__,'initialize',lambda:None)
    configs=[]
    def run(*args,**kwargs):
        configs.append(kwargs['llm_config'])
        args[2].mkdir();(args[2]/'lyrics.ttml').write_bytes(b'<tt/>')
        return {'status':'complete'}
    monkeypatch.setattr(pipeline,'run',run)
    monkeypatch.setattr(sys,'argv',['timing',str(audio),'--lyrics',str(lyrics),'--aligner','llm_direct',
        '--llm-base-url','https://fixture.example/v1','--llm-model','m',
        '--llm-api-key-header','x-api-key','--llm-api-key-format','raw'])
    __main__.main()
    assert configs[0].auth_headers()=={'x-api-key':'secret-fixture'}
