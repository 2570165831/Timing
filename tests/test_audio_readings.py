import base64
import copy
import io
import json
from pathlib import Path
import sys

import httpx
import numpy as np
import pytest
import soundfile as sf

from timing import audio_readings as ar
from timing.llm_splitter import LlmConfig, use_llm
from timing.schema import Line


def reply(items):
    return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
        'content':json.dumps({'items':items}), 'reasoning_content':'private-not-for-audit'}}]})


def attach(session,handler):
    session.client.close()
    session.client=httpx.Client(transport=httpx.MockTransport(handler))


def payload_from(request):
    body=json.loads(request.content)
    content=body['messages'][1]['content']
    assert content[0]['type']=='input_audio'
    assert set(content[0]['input_audio'])=={'data'}
    uri=content[0]['input_audio']['data']
    assert uri.startswith('data:audio/wav;base64,')
    wave,rate=sf.read(io.BytesIO(base64.b64decode(uri.split(',',1)[1])))
    assert rate==16000 and wave.ndim==1 and 0<len(wave)<=30.25*rate
    return body,json.loads(content[1]['text'])


def test_audio_clip_is_real_pcm_with_original_time_window_and_resampling():
    wave=np.sin(np.arange(44100*3)/44100*440)*.3
    uri,bounds=ar.clip_audio(wave,44100,1,2)
    data,rate=sf.read(io.BytesIO(base64.b64decode(uri.split(',')[1])))
    assert rate==16000 and bounds==[.88,2.12]
    assert len(data)==19840 and np.max(np.abs(data))>.2


@pytest.mark.parametrize('start,end',[(None,2),(2,1),(-1,2),(0,31),(float('nan'),2)])
def test_bad_audio_windows_reject(start,end):
    with pytest.raises(ValueError):
        ar.clip_audio(np.zeros(40*16000),16000,start,end)


@pytest.mark.parametrize('row',[
    {'id':'w0','surface':'別','readings':['フタリ']},
    {'id':'w0','surface':'2人','readings':['futari']},
    {'id':'w0','surface':'2人','readings':['ふたり']},
    {'id':'w0','surface':'2人','readings':['フタリ'],'start':1},
    {'id':'w0','surface':'2人','readings':['フタリ']*4},
])
def test_invalid_readings_reject_rewritten_text_romaji_hiragana_times_and_unbounded_candidates(row):
    with pytest.raises(ValueError):
        ar.validate_readings({'surface':'2人'},row)


def test_japanese_audio_partial_repair_keeps_valid_entries_and_records_no_credentials(tmp_path,monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides',lambda:{'ja':{},'en':{}})
    calls=[]
    def handler(request):
        body,payload=payload_from(request); calls.append(payload)
        assert body['thinking']=={'type':'enabled'} and 'reasoning_effort' not in body
        items=payload['items']
        if len(calls)==2:
            assert [item['surface'] for item in items]==['1人']
            assert 'validation_errors' in payload
        return reply([{'id':item['id'],'surface':item['surface'],
                       'readings':['フタリ'] if item['surface']=='2人' else (['wrong'] if len(calls)==1 else ['ヒトリ'])}
                      for item in items])
    line=Line('2人と1人',1,3)
    config=LlmConfig('https://fixture.example/v1','mimo-v2.6-pro','secret-fixture',False,'on','thinking','')
    with ar.use_audio_readings(config,lambda _:None,tmp_path) as session:
        attach(session.transport,handler)
        session.prepare([line],np.zeros(4*16000),16000)
        assert ar.primary_readings(copy.deepcopy(line))=={'2人':'フタリ','1人':'ヒトリ'}
        assert session.report()['requests']==2
        assert session.records[0]['status']=='accepted'
        assert ar.clip_candidates([line])=={'2人':['フタリ'],'1人':['ヒトリ']}
    assert ar.current_session() is None
    audit=(tmp_path/'audio_readings.json').read_text(encoding='utf-8')
    assert all(v not in audit for v in ('secret-fixture','fixture.example','base64','private-not-for-audit'))


def test_repeated_word_keeps_per_occurrence_audio_readings_and_english_is_not_sent(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides',lambda:{'ja':{},'en':{}})
    lines=[Line('今日',0,1),Line('今日',1,2),Line('In 40 years',2,3)]
    count=[]
    def handler(request):
        _,p=payload_from(request);count.append(p)
        return reply([{'id':x['id'],'surface':x['surface'],'readings':['キョー' if len(count)==1 else 'コンニチ']} for x in p['items']])
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1','m'),lambda _:None) as s:
        attach(s.transport,handler); s.prepare(lines,np.zeros(3*16000),16000)
        assert ar.primary_readings(lines[0])['今日']=='キョー'
        assert ar.primary_readings(lines[1])['今日']=='コンニチ'
        assert len(count)==2
        assert ar.clip_candidates(lines)['今日']==['キョー','コンニチ']


def test_http_failure_stops_later_audio_requests_and_explicitly_falls_back(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides',lambda:{'ja':{},'en':{}})
    lines=[Line('今日',0,1),Line('明日',1,2)]
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1','m'),lambda _:None) as s:
        attach(s.transport,lambda _:httpx.Response(401,text='secret-private-body'))
        s.prepare(lines,np.zeros(2*16000),16000)
        assert s.transport.requests==1 and s.disabled
        assert all('llm_audio_reading_fallback' in l.flags for l in lines)
        assert 'secret-private-body' not in json.dumps(s.report())


def test_ctc_uses_audio_primary_and_manual_override_still_wins(monkeypatch):
    from timing.ctc import text_units
    monkeypatch.setattr('timing.ctc.pronunciation_overrides',lambda:{'ja':{},'en':{}})
    units,labels=text_units('今日',japanese_readings={'今日':'コンニチ'})
    assert labels==['konnichi'] and units[0].text=='今日'
    assert 'llm_audio_reading_candidate' in units[0].flags
    monkeypatch.setattr('timing.ctc.pronunciation_overrides',lambda:{'ja':{'今日':'キョー'},'en':{}})
    units,labels=text_units('今日',japanese_readings={'今日':'コンニチ'})
    assert labels==['kyoo'] and 'custom_pronunciation' in units[0].flags


def test_native_converter_has_context_scoped_candidates_without_leaking_between_clips():
    from timing.tifa_pronunciation import ensure_audio_registered
    model=Path(__file__).resolve().parents[1]/'models/tifa'
    if not (model/'deps/g2pflow').exists():
        pytest.skip('TIFA runtime not downloaded')
    sys.path.insert(0,str(model/'deps'))
    converter=ensure_audio_registered()(dict_path=str(model/'dictionaries/japanese_dict_full.txt'))
    assert converter.find('今日も') is None
    with ar.native_candidates({'今日':['キョー','コンニチ']}):
        assert converter.find('今日も')==(0,2)
        words=converter.convert('今日')
        assert len(words[0].readings)==2 and words[0].text=='今日'
    assert converter.find('今日も') is None


def test_english_audio_scoped_cache_and_native_edges_are_preserved():
    from timing.english_syllables import use_splitter
    from timing.tifa import map_clip,prepare_clips_llm
    phones=('K','AH1','N','T','R','IY0')
    row={'texts':['country'],'selected':[{'phonemes':['en/'+p.lower() for p in phones]}],
         'display_plans':[None],'owners':[1]*6,'phones':list(phones),'groups':[1]*6,
         'spans':[[.1,.13],[.13,.4],[.4,.45],[.45,.48],[.48,.5],[.5,1.2]],'skipped':[]}
    original=copy.deepcopy(row)
    calls=[]
    def handler(request):
        body,payload=payload_from(request);calls.append(body)
        assert 'You receive the actual sung audio' in body['messages'][0]['content']
        return reply([{'id':x['id'],'parts':[{'text':'coun','phone_start':0,'phone_end':3},
                                          {'text':'try','phone_start':3,'phone_end':6}]} for x in payload['items']])
    with use_splitter('llm'),use_llm(LlmConfig('https://fixture.example/v1','m'),lambda _:None) as s:
        s.audio_enabled=True; s.audio_sources=(np.zeros(4*16000),16000,None)
        attach(s,handler)
        for i in (0,2):
            line=Line('country',i,i+2)
            clip={'id':'x','start':i,'end':i+2,'target_start':0,'target_end':7}
            prepare_clips_llm([line],[clip],{'x':row})
            probe,_=map_clip(line,row,clip,True)
            assert [(u.text,u.start,u.end) for u in probe.words]==[('coun',i+.1,i+.45),('try',i+.45,i+1.2)]
        assert len(calls)==2 and len(s.records)==2
        assert s.records[0]['audio_scope']!=s.records[1]['audio_scope']
    assert row==original


def test_audio_requires_lrc_before_separation_and_validates_english_selection(tmp_path,monkeypatch):
    from timing import pipeline
    config=LlmConfig('https://fixture.example/v1','m')
    monkeypatch.setattr(pipeline,'decode',lambda *a,**kw:pytest.fail('must validate before decoding'))
    monkeypatch.setattr(pipeline,'prepare_sources',lambda *a,**kw:pytest.fail('must validate before separation'))
    with pytest.raises(ValueError,match='逐句 LRC'):
        pipeline.run(tmp_path/'a.wav','今日',tmp_path,llm_config=config,llm_audio_reading=True,log=lambda _:None)
    with pytest.raises(ValueError,match='选择 LLM'):
        pipeline.run(tmp_path/'a.wav','hello',tmp_path,llm_config=config,llm_audio_split=True,log=lambda _:None)


def test_api_accepts_japanese_audio_independent_of_english_and_rejects_invalid_modes(monkeypatch,tmp_path):
    import inspect
    from fastapi.testclient import TestClient
    from timing import server
    monkeypatch.setattr(server,'DATA',tmp_path); monkeypatch.setattr(server,'jobs',{})
    monkeypatch.setattr(server,'initialize',lambda:None)
    queued=[];monkeypatch.setattr(server.executor,'submit',lambda *a:queued.append(a))
    data={'lyrics':'[00:00.00]今日','english_splitter':'syllabify','syllables':'false','llm_audio_reading':'true',
          'llm_base_url':'https://fixture.example/v1','llm_model':'mimo-v2.6-pro'}
    with TestClient(server.app) as client:
        headers={'X-Timing-Token':server.TOKEN}
        response=client.post('/api/jobs',data=data,files={'audio':('a.wav',b'audio')},headers=headers)
        assert response.status_code==200
        args=inspect.signature(server.process).bind(*queued[0][1:]).arguments
        assert args['llm_audio_reading'] is True and args['llm_config'].model=='mimo-v2.6-pro'
        english={'lyrics':'[00:00.00]hello','llm_audio_split':'true'}
        assert client.post('/api/jobs',data={**data,**english},files={'audio':('a.wav',b'audio')},headers=headers).status_code==400
        assert client.post('/api/jobs',data={**data,'aligner':'sofa'},files={'audio':('a.wav',b'audio')},headers=headers).status_code==400


def test_failed_audio_primary_coarse_path_retries_dictionary_and_retains_native_candidates(monkeypatch,tmp_path):
    from timing import pipeline, alignment
    from timing.schema import Unit
    monkeypatch.setattr(pipeline,'decode',lambda *a,**kw:(np.zeros(32000),16000))
    monkeypatch.setattr(alignment,'decode',lambda *a,**kw:(np.zeros(2*44100),44100))
    monkeypatch.setattr('timing.singing.pronunciation_overrides',lambda:{'ja':{},'en':{}})
    attempts=[]
    class Ctc:
        def __init__(self,*a):pass
        def release(self):pass
        def align(self,samples,line,*a):
            candidates=ar.primary_readings(line); attempts.append(candidates)
            if candidates:
                raise ValueError('no acoustic path')
            line.start,line.end=.1,.5
    def prepare(self,lines,*a):
        self.by_line[ar.line_key(lines[0])]={'candidates':{'今日':['コンニチ','キョー']}}
    def align(samples,rate,lines,*a,**kw):
        assert ar.clip_candidates(lines)=={'今日':['コンニチ','キョー']}
        lines[0].words=[Unit('今日','今日',start=.2,end=.8)]
        return {'frame_ms':10}
    monkeypatch.setattr(alignment,'CtcAligner',Ctc)
    monkeypatch.setattr(ar.AudioReadingSession,'prepare',prepare)
    monkeypatch.setattr('timing.tifa.align',align)
    result=pipeline.run(tmp_path/'a.wav','[00:00.00]今日',tmp_path,audio_mode='source',
                        llm_audio_reading=True,llm_config=LlmConfig('https://fixture.example/v1','m'),log=lambda _:None)
    assert result['status']=='complete' and attempts==[{'今日':'コンニチ'},{}]
    report=json.loads((tmp_path/'alignment.json').read_text(encoding='utf-8'))
    assert 'llm_audio_coarse_fallback' in report['lines'][0]['flags']


def test_chinese_readings_normalize_pinyin_and_keep_ja_default():
    assert ar.validate_readings({'surface':'你好'},{'id':'w0','surface':'你好','readings':['ni3 hao3']},'zh')==['ni hao']
    assert ar.validate_readings({'surface':'你好'},{'id':'w0','surface':'你好','readings':['NI'],'language':'zh'},'zh')==['ni']
    assert ar.validate_readings({'surface':'你好'},{'id':'w0','surface':'你好','readings':['ni3'],'language':'zh-en'},'zh')==['ni']
    assert ar.validate_readings({'surface':'2人'},{'id':'w0','surface':'2人','readings':['フタリ']})==['フタリ']
    with pytest.raises(ValueError):
        ar.validate_readings({'surface':'你好'},{'id':'w0','surface':'你好','readings':['xqz'],'language':'zh'})
    with pytest.raises(ValueError):
        ar.validate_readings({'surface':'你好'},{'id':'w0','surface':'你好','readings':['ni']})
    with pytest.raises(ValueError):
        ar.validate_readings({'surface':'你好'},{'id':'w0','surface':'你好','readings':['ni3'],'language':'ja'},'zh')
    with pytest.raises(ValueError):
        ar.validate_readings({'surface':'2人'},{'id':'w0','surface':'2人','readings':['フタリ'],'language':'zh'},'ja')


def test_chinese_audio_session_uses_local_pinyin_baseline_and_language_filter(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides',lambda:{'ja':{},'en':{},'zh':{}})
    line=Line('你好',1,3,language='zh')
    seen=[]
    def handler(request):
        _,p=payload_from(request);seen.append(p)
        return reply([{'id':x['id'],'surface':x['surface'],'readings':[x['baseline']+'3']} for x in p['items']])
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1','m'),lambda _:None) as s:
        attach(s.transport,handler);s.prepare([line],np.zeros(4*16000),16000)
        assert [i['language'] for i in seen[0]['items']]==['zh','zh']
        assert [i['surface'] for i in seen[0]['items']]==['你','好']
        assert [i['baseline'] for i in seen[0]['items']]==['ni','hao']
        assert ar.primary_readings(line)=={'你':'ni','好':'hao'}
        assert ar.clip_candidates([line],'zh')=={'你':['ni'],'好':['hao']}
        assert ar.clip_candidates([line],'ja')=={}
        assert s.records[0]['status']=='accepted'
