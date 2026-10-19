import re
import sys
from pathlib import Path

import pytest

from timing.numeric_lyrics import japanese_integer, japanese_numeric_spans
from timing.ctc import text_units
from timing.schema import Line
from timing.singing import phonemize
from timing.text import tokenize


@pytest.mark.parametrize('number,kana',[
    ('0','ゼロ'),('40','ヨンジュウ'),('300','サンビャク'),('600','ロッピャク'),
    ('800','ハッピャク'),('3000','サンゼン'),('8000','ハッセン'),
    ('2026','ニセンニジュウロク'),('007','ゼロゼロナナ'),('４０','ヨンジュウ'),
])
def test_japanese_cardinal_candidates(number,kana):
    assert japanese_integer(number)==kana


@pytest.mark.parametrize("text,expected",[
    ("2人で箱を運び1人で数える",[("2人","フタリ"),("1人","ヒトリ")]),
    ("２人と１人が箱を運ぶ",[("２人","フタリ"),("１人","ヒトリ")]),
    ("14人と3人",[("14人","ジュウヨニン"),("3人","サンニン")]),
    ("1人称で2人前",[("1人","イチニン"),("2人","ニニン")]),
    ("記録は40年分",[("40","ヨンジュウ")]),
    ("In 40 years",[]),
])
def test_context_readings_preserve_offsets(text,expected):
    assert [(text[a:b],kana) for a,b,kana in japanese_numeric_spans(text)]==expected


@pytest.mark.parametrize("text",["2人で箱を運び1人で数える","２人で箱を運び１人で数える","机の横で2人が待つ","記録は40年分"])
def test_mms_sofa_preserve_numeric_surfaces_and_use_japanese_phones(text):
    units,labels=text_units(text)
    assert ''.join(u.text for u in units)==text
    numeric=[(u,label) for u,label in zip(units,labels) if 'number_reading_candidate' in u.flags]
    assert numeric and all(re.fullmatch("[a-z']+",label) for _,label in numeric)
    if '箱を運び' in text:
        assert [label for _,label in numeric]==['futari','hitori']
    for fine in (False,True):
        result,_,phones,_=phonemize([Line(text)],fine=fine)
        assert ''.join(u.text for u in result)==text
        assert set(lang for lang,_ in phones)=={'sp','ja'}
        assert [u.reading for u in result if 'number_reading_candidate' in u.flags]==[u.reading for u,_ in numeric]
    for fine in (False,True):
        assert ''.join(u.text for u in tokenize(text,fine=fine))==text


def test_native_tifa_converter_uses_same_readings_and_does_not_claim_english():
    from timing.tifa_pronunciation import ensure_numbers_registered
    from timing.tifa_worker import display_plans
    from timing.tifa import map_result
    model=Path(__file__).resolve().parents[1]/'models/tifa'
    if not (model/'deps/g2pflow').exists():
        pytest.skip('TIFA runtime not downloaded')
    sys.path.insert(0,str(model/'deps'))
    converter=ensure_numbers_registered()(dict_path=str(model/'dictionaries/japanese_dict_full.txt'))
    assert converter.find("2人で箱を運び1人で数える")==(0,2)
    assert converter.find('In 2 years') is None
    for text,scripts in [('2人',['fu','ta','ri']),('1人',['hi','to','ri']),('２人',['fu','ta','ri'])]:
        word=converter.convert(text)[0]
        assert word.text==text
        groups=word.readings[0].paths[0]
        assert [g.script for g in groups]==scripts
        phones=['ja/'+p for g in groups for p in g.phonemes]
        choice={'phonemes':phones,'scripts':scripts}
        plans=display_plans([text],[choice],model)
        # Whole numeric counter retains actual native edges; no invented split.
        row={'texts':[text],'selected':[choice],'display_plans':plans,
             'phones':phones,'spans':[[i*.1,(i+1)*.1] for i in range(len(phones))],
             'owners':[1]*len(phones),'groups':[j for j,g in enumerate(groups) for _ in g.phonemes], 'skipped':[]}
        line=Line(text); map_result(line,row,10)
        assert [(u.text,u.start,u.end) for u in line.words]==[(text,10,round(10+.1*len(phones),3))]
        assert 'number_reading_candidate' in line.words[0].flags
