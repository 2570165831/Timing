import copy
import numpy as np
import pytest

from timing.schema import Line
from timing.tifa import restore_display, map_result, quiet_crop_end, target_result


def test_display_mapping_preserves_japanese_quotes_case_and_spaces():
    assert restore_display('Beep 「起きてる？今から」',['beep','起き','てる','今','から']) == ['Beep 「','起き','てる？','今','から」']
    with pytest.raises(ValueError,match='不完整'):
        restore_display('君も歌う',['君','歌う'])


def test_long_vowel_has_one_indivisible_acoustic_group_not_fabricated_split():
    line=Line('どうせ ')
    row={'texts':['どうせ'],'selected':[{'phonemes':['ja/d','ja/o','ja/s','ja/e']}],
         'display_plans':[[{'text':'どう','group_count':1,'flags':['shared_vowel_indivisible']},
                           {'text':'せ','group_count':1,'flags':[]}]],
         'owners':[1,1,1,1],'groups':[1,1,2,2], 'phones':['ja/d','ja/o','ja/s','ja/e'],
         'spans':[[.1,.2],[.2,.6],[.6,.7],[.7,.9]],'skipped':[]}
    map_result(line,row,10)
    assert [(w.text,w.start,w.end) for w in line.words] == [('どう',10.1,10.6),('せ ',10.6,10.9)]
    row['spans'][2]=[.6,.6]
    with pytest.raises(ValueError,match='未补造'):
        map_result(Line('どうせ '),row,10)


def test_coarse_spike_does_not_truncate_a_held_vowel():
    rate=1000
    wave=np.zeros(6000,dtype='float32')
    wave[100:3500]=.2  # CTC stops at2s, actual sustain continues to3.5s.
    wave[4200:5500]=.2 # A later nonlexical phrase.
    end,gate=quiet_crop_end(wave,rate,2,6,.1)
    assert 3.6<end<4.2 and gate is not None
    # A gap too short for an utterance boundary cannot trim a sustain.
    wave[2800:2850]=0
    end,_=quiet_crop_end(wave,rate,2,6,.1)
    assert end>3.5


def test_context_extraction_keeps_only_target_phones_and_rejects_cross_line_word():
    row={'texts':['君','hello','世界'], 'selected':[1,2,3],'display_plans':[None]*3,
         'owners':[1,2,2,3], 'phones':['k','h','e','s'],'groups':[1,2,2,3],
         'spans':[[.1,.2],[.3,.4],[.4,.5],[.6,.7]],'skipped':[]}
    target=target_result(row,1,6)
    assert target['texts']==['hello'] and target['owners']==[1,1]
    assert target['spans']==[[.3,.4],[.4,.5]]
    with pytest.raises(ValueError,match='跨句'):
        target_result(row,2,6)


def test_tifa_choice_uses_direct_lead_and_releases_coarse_before_worker(tmp_path,monkeypatch):
    from timing import pipeline, alignment
    from timing.schema import Unit
    calls=[]
    def decode(path,target,rate=16000,*,floating=False,**kw):
        return np.ones(2*rate,dtype='float32'),rate
    monkeypatch.setattr(pipeline,'decode',decode)
    monkeypatch.setattr('timing.sources.decode',decode)
    monkeypatch.setattr(alignment,'decode',decode)
    monkeypatch.setattr('timing.sources.separate_bs_lead',lambda *a:(calls.append('direct') or tmp_path/'lead.wav',{'route':'direct_original'}))
    monkeypatch.setattr('timing.sources.separate_vocals',lambda *a:pytest.fail('unneeded vocals-first cascade'))
    class Coarse:
        def __init__(self,*a):pass
        def align(self,a,line,*args):line.start,line.end=.1,.7
        def release(self):calls.append('coarse_release')
    def align(audio,rate,lines,*args,**kw):
        assert calls[-1]=='coarse_release'
        assert (lines[0].start,lines[0].end)==(.2,2)
        lines[0].words=[Unit('hello','hello',start=.3,end=.9)]
        return {'frame_ms':10}
    monkeypatch.setattr(alignment,'CtcAligner',Coarse)
    monkeypatch.setattr('timing.tifa.align',align)
    result=pipeline.run(tmp_path/'song.wav','[00:00.20]hello',tmp_path,log=lambda _:None)
    assert result['aligner']=='tifa' and calls==['direct','coarse_release']
