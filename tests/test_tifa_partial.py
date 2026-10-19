import copy
import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from timing import tifa
from timing.pipeline import finish
from timing.schema import Line, Unit
from timing.ttml import TT, export


def prediction(identifier='line_001', bad=1):
    spans = [[.1,.3],[.3,.3],[.4,.8]]
    if bad is None:
        spans[1] = [.3,.4]
    else:
        spans = [[.1,.3],[.3,.4],[.4,.8]]
        spans[bad][1] = spans[bad][0]
    return {'id':identifier,'texts':['and','i','love'],
            'selected':[{'phonemes':[p]} for p in ['en/ae','en/ay','en/ah']],
            'phones':['en/ae','en/ay','en/ah'],'spans':spans,
            'owners':[1,2,3],'groups':[1,2,3],'display_plans':[None]*3,
            'skipped':[] if bad is None else [bad],'agreement':.8}


def clip():
    return {'id':'line_001','start':0,'end':1,'target_start':0,'target_end':8}


def test_retry_exhaustion_retains_other_units_and_only_failed_text_is_untimed(tmp_path,monkeypatch):
    row = prediction()
    saved = copy.deepcopy(row)
    retries = []
    def worker(folder,clips,*args,**kwargs):
        retries.extend(clips)
        return {'results':[prediction(c['id']) for c in clips]}
    monkeypatch.setattr(tifa,'run_worker',worker)
    line = Line('And I love',start=0,end=1)
    checks = tifa.complete_results(np.ones(1000),1000,[line],tmp_path,[clip()],
                                  {'results':[row]},log=lambda _:None,syllables=False)
    assert len(retries)==3 and row==saved
    assert checks[0]['mode']=='partial'
    assert checks[0]['unresolved_units'][0]['text']=='I '
    assert [(w.text,w.start,w.end) for w in line.words]==[
        ('And ',.1,.3),('I ',None,None),('love',.4,.8)]
    assert 'tifa_native_alignment' not in line.words[1].flags
    assert all('tifa_native_alignment' in w.flags for w in [line.words[0],line.words[2]])
    result = finish([line],1,'en','test',tmp_path,{'source':'original'},lambda _:None)
    assert result['manual_timing_lines']==[] and not result['sentence_only']
    assert result['partial_timing_lines']==[{'line':1,'units':[{'index':2,'text':'I '}]}]
    assert '其他字的时间已保留' in result['review_message']
    report=json.loads((tmp_path/'alignment.json').read_text(encoding='utf-8'))
    assert report['lines'][0]['words'][1]['start'] is None
    spans=ET.fromstring((tmp_path/'lyrics.ttml').read_bytes()).findall(f'.//{{{TT}}}p/{{{TT}}}span')
    assert ''.join(s.text for s in spans)==line.text
    assert spans[1].attrib=={'begin':'00:00:00.400','end':'00:00:00.400'} and spans[1].text=='I '
    assert report['export_placeholders'][0]['kind']=='zero_duration_edit_marker'
    assert spans[0].get('end')=='00:00:00.300' and spans[2].get('begin')=='00:00:00.400'


def test_successful_full_retry_wins_over_partial_initial_prediction(tmp_path,monkeypatch):
    monkeypatch.setattr(tifa,'run_worker',lambda folder,clips,*a,**kw:{
        'results':[prediction(c['id'],bad=None) for c in clips]})
    line=Line('And I love',start=0,end=1)
    checks=tifa.complete_results(np.ones(1000),1000,[line],tmp_path,[clip()],
                                {'results':[prediction()]},log=lambda _:None,syllables=False)
    assert checks[0]['mode']=='retry'
    assert all(w.start is not None for w in line.words)
    assert 'tifa_partial_alignment' not in line.flags


@pytest.mark.parametrize('bad',[0,2])
def test_unresolved_endpoints_use_anchor_bounds_without_changing_native_units(bad):
    line=Line('And I love',start=0,end=1)
    tifa.map_result(line,prediction(bad=bad),0,False,partial=True)
    tifa.validate_clip(line,Line(line.text,start=0,end=1),clip())
    root=ET.fromstring(export([line],1,'en'))
    p=root.find(f'.//{{{TT}}}p')
    assert p.get('begin')==('00:00:00.000' if bad==0 else '00:00:00.100')
    assert p.get('end')==('00:00:01.000' if bad==2 else '00:00:00.800')
    point='00:00:00.300' if bad==0 else '00:00:00.400'
    assert p.findall(f'{{{TT}}}span')[bad].attrib=={'begin':point,'end':point}


def test_japanese_plan_salvages_at_display_unit_not_whole_semantic_word():
    row={'texts':['あいう'],'selected':[{'phonemes':['ja/a','ja/i','ja/u']}],
         'display_plans':[[{'text':ch,'group_count':1,'flags':[]} for ch in 'あいう']],
         'owners':[1,1,1],'groups':[1,2,3],'phones':['ja/a','ja/i','ja/u'],
         'spans':[[.1,.3],[.3,.3],[.4,.8]],'skipped':[1]}
    line=Line('あいう',start=0,end=1)
    tifa.map_result(line,row,0,False,partial=True)
    assert [(w.start,w.end) for w in line.words]==[(.1,.3),(None,None),(.4,.8)]
    assert ''.join(w.text for w in line.words)=='あいう'


def test_partial_does_not_accept_reversed_other_word_boundaries():
    row=prediction()
    row['spans'][2]=[.15,.25]
    line=Line('And I love',start=0,end=1)
    tifa.map_result(line,row,0,False,partial=True)
    with pytest.raises(ValueError,match='次序冲突'):
        tifa.validate_clip(line,Line(line.text,start=0,end=1),clip())
    with pytest.raises(ValueError,match='顺序冲突'):
        export([line],1,'en')


def test_export_requires_explicit_unresolved_flag_and_valid_remaining_prediction():
    with pytest.raises(ValueError,match='未对齐'):
        export([Line('ab',start=0,end=1,words=[Unit('a','a'),Unit('b','b',start=.4,end=.8)])],1)
    with pytest.raises(ValueError,match='范围无效'):
        export([Line('a',start=0,end=1,words=[Unit('a','a',flags=['tifa_unresolved_unit'])])],1)


def test_nonfinite_phone_is_isolated_without_making_other_timestamps_nonfinite():
    row=prediction()
    row['spans'][1]=[float('nan'),float('nan')]
    line=Line('And I love',start=0,end=1)
    tifa.map_result(line,row,0,False,partial=True)
    assert [(w.start,w.end) for w in line.words]==[(.1,.3),(None,None),(.4,.8)]
    export([line],1,'en')
