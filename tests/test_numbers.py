import re
import sys
from pathlib import Path
import pytest

from timing.numeric_lyrics import english_integer, english_numbers
from timing.ctc import text_units
from timing.schema import Line
from timing.singing import phonemize


@pytest.mark.parametrize("number,reading", [("40","forty"),("21","twenty one"),("105","one hundred five"),
                                           ("2026","two thousand twenty six"),("007","zero zero seven"),("0","zero")])
def test_english_integer_reading(number,reading):
    assert english_integer(number)==reading


def test_ctc_numeric_lyrics_keep_original_text_and_use_english_labels():
    text="The paper map will last for maybe 40 years"
    units, labels=text_units(text)
    assert "".join(u.text for u in units)==text
    assert [(u.text,label) for u,label in zip(units,labels) if u.spoken=="40"]==[("40 ","forty")]
    assert not english_numbers("40年後")


def test_sofa_numeric_display_retained_and_phones_are_english():
    units,_,phones,_=phonemize([Line("In 40 years",language="en")])
    assert "".join(u.text for u in units)=="In 40 years"
    assert set(lang for lang,_ in phones)=={"sp","en"}
    assert next(u for u in units if u.spoken=="40").flags==["number_reading_candidate"]


def test_tifa_digit_dictionary_uses_verified_lexicon_and_preserves_token(tmp_path,monkeypatch):
    from timing.config import MODELS
    from timing.tifa_worker import number_dictionary
    if not all((MODELS/'tifa'/name).exists() for name in ('deps/g2pflow','dictionaries/ds_cmudict-07b.txt')):
        pytest.skip('TIFA pronunciation assets not downloaded')
    monkeypatch.syspath_prepend(str(MODELS/"tifa/deps"))
    text=tmp_path/"probe.txt"; text.write_text("maybe 40 years",encoding="utf-8")
    path=number_dictionary([{"audio":str(text.with_suffix(".wav"))}],MODELS/"tifa",tmp_path)
    assert path.read_text(encoding="utf-8").split("\t")[0]=="40"
    assert re.fullmatch(r"40\t[a-z_ ]+\n",path.read_text(encoding="utf-8"))
    from g2pflow.converters.dictionary import DictionaryConverter
    converter=DictionaryConverter(str(path))
    assert converter.accepts_word("40")
    assert converter._convert("40")[0].text=="40"


def test_worker_directory_does_not_shadow_standard_library_numbers():
    import subprocess
    result=subprocess.run([sys.executable,"-c","from numbers import Number; assert isinstance(40, Number)"],
                          cwd=Path(__file__).resolve().parents[1]/"timing",capture_output=True,text=True)
    assert result.returncode==0,result.stderr
