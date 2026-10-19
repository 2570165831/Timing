import json

import numpy as np
import pytest

from timing.english_syllables import use_splitter
from timing.llm_splitter import LlmConfig, use_llm
from timing.schema import Line
from timing import tifa
from test_llm_splitter import fake_client, envelope, PHONES


def whole(item):
    return {'id':item['id'],'parts':[{'text':item['word'],'phone_start':0,'phone_end':len(item['phones'])}]}


def test_26_words_from_many_lines_send_two_batches_and_share_context(monkeypatch):
    batches=[]
    def handler(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        batches.append(payload)
        contexts={c['id']:c['text'] for c in payload['contexts']}
        assert all(all(i in contexts for i in item['context_ids']) for item in payload['items'])
        return envelope([whole(item) for item in payload['items']])
    fake_client(monkeypatch,handler)
    entries=[('country'+chr(97+i),PHONES) for i in range(26)]
    groups=[(entries[i:i+2],f'lyric context {i//2}') for i in range(0,26,2)]
    groups += [([entries[0]],'another occurrence'),([entries[0]],'third occurrence')]
    with use_llm(LlmConfig('https://fixture.invalid/v1','fixture'),log=lambda _:None) as session:
        session.prepare_many(groups)
        for entry in entries:
            assert session.split(*entry)
        assert session.requests==2 and session.report()['repair_requests']==0
        assert session.report()['accepted_words']==26
    assert [len(p['items']) for p in batches]==[24,2]
    first=batches[0]['items'][0]
    assert len(first['context_ids'])==2
    assert len(batches[0]['contexts'])==13


def test_only_invalid_item_retried_with_its_error_and_context(monkeypatch):
    requests=[]
    def handler(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        requests.append(payload)
        if len(requests)==1:
            good=whole(payload['items'][0]);bad=whole(payload['items'][1]);bad['parts'][0]['text']='changed'
            return envelope([good,bad,{'id':'unrequested','parts':[]}])
        assert [item['id'] for item in payload['items']]==['w1']
        assert payload['validation_errors']==[{'id':'w1','reason':'原文或音素覆盖不完整'}]
        assert payload['contexts']==[{'id':'c0','text':'second sentence'}]
        return envelope([whole(payload['items'][0])])
    fake_client(monkeypatch,handler)
    with use_llm(LlmConfig('https://fixture.invalid/v1','fixture'),log=lambda _:None) as session:
        session.prepare_many([([('country',PHONES)],'first sentence'),([('countryb',PHONES)],'second sentence')])
        assert session.report()['accepted_words']==2 and session.report()['repair_requests']==1
        assert session.report()['fallback_words']==0


def test_tifa_complete_results_prefetches_before_mapping_each_line(tmp_path,monkeypatch):
    batches=[]
    def handler(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content'])
        batches.append(payload)
        return envelope([whole(item) for item in payload['items']])
    fake_client(monkeypatch,handler)
    lines=[];clips=[];rows=[]
    for i in range(26):
        word='country'+chr(97+i)
        lines.append(Line(word,start=i,end=i+1))
        clips.append({'id':str(i),'start':i,'end':i+1,'target_start':0,'target_end':len(word)})
        rows.append({'id':str(i),'texts':[word],'selected':[{'phonemes':list(PHONES)}],
                     'display_plans':[None],'owners':[1]*6,'phones':list(PHONES),'groups':[1]*6,
                     'spans':[[.1+j*.1,.2+j*.1] for j in range(6)],'skipped':[],'agreement':.9})
    with use_splitter('llm'),use_llm(LlmConfig('https://fixture.invalid/v1','fixture'),log=lambda _:None):
        checks=tifa.complete_results(np.ones(26000,dtype='float32'),1000,lines,tmp_path,clips,{'results':rows},log=lambda _:None)
    assert [len(b['items']) for b in batches]==[24,2]
    assert all(c['mode']=='initial' for c in checks)
    assert [(line.words[0].start,line.words[0].end) for line in lines]==[(i+.1,i+.7) for i in range(26)]


def test_invalid_native_anchor_never_sends_a_request(monkeypatch):
    fake_client(monkeypatch,lambda r:pytest.fail('invalid audio window must not be sent to LLM'))
    row={'texts':['country'],'selected':[{'phonemes':list(PHONES)}],'display_plans':[None],
         'owners':[1]*6,'phones':list(PHONES),'groups':[1]*6,
         'spans':[[1.1+j*.1,1.2+j*.1] for j in range(6)],'skipped':[]}
    with use_splitter('llm'),use_llm(LlmConfig('https://fixture.invalid/v1','fixture'),log=lambda _:None):
        with pytest.raises(ValueError,match='下一句'):
            tifa.map_clip(Line('country',start=0,end=1),row,{'start':0,'end':2,'target_start':0,'target_end':7},True)


def test_sofa_prefetches_all_lines_before_individual_alignment(monkeypatch):
    from timing.singing import prepare_lines_llm, phonemize
    dictionary={word:[p.lower().rstrip('01') for p in PHONES] for word in ['countrya','countryb']}
    monkeypatch.setattr('timing.singing.dictionary',lambda:dictionary)
    batches=[]
    def handler(request):
        payload=json.loads(json.loads(request.content)['messages'][1]['content']);batches.append(payload)
        return envelope([whole(item) for item in payload['items']])
    fake_client(monkeypatch,handler)
    lines=[Line('countrya'),Line('countryb')]
    with use_splitter('llm'),use_llm(LlmConfig('https://fixture.invalid/v1','fixture'),log=lambda _:None):
        prepare_lines_llm(lines)
        for line in lines:
            units,*_=phonemize([line])
            assert ''.join(u.text for u in units)==line.text
    assert len(batches)==1 and len(batches[0]['items'])==2
