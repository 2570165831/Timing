from pathlib import Path
import sys

import pytest

from timing import tifa
from timing.text import canonical_apostrophes, kept


@pytest.mark.parametrize('original,labels',[
    ('I’m still your girl',["i'm",'still','your','girl']),
    ('Wait ’til I hear our song',['wait',"'til",'i','hear','our','song']),
    ('Tell myself I’m alright',['tell','myself',"i'm",'alright']),
    ('Baby, you’re just harder to see than most',['baby',"you're",'just','harder','to','see','than','most']),
    ('Every night I’m dancing with your ghost',['every','night',"i'm",'dancing','with','your','ghost']),
])
def test_curly_apostrophe_reconstruction_preserves_original_and_next_line_offsets(original,labels):
    restored=tifa.restore_display(original,labels)
    assert ''.join(restored)==original
    assert sum(len(kept(label)) for label in labels)==len(kept(original))
    assert canonical_apostrophes(original)!=original
    # Punctuation normalization cannot excuse missing actual letters.
    with pytest.raises(ValueError,match='原文映射'):
        tifa.restore_display(original,labels[:-1])


def test_real_native_preprocessors_keep_contractions_and_leading_elision():
    model=Path(__file__).resolve().parents[1]/'models/tifa'
    if not (model/'deps/g2pflow').exists():
        pytest.skip('TIFA runtime not downloaded')
    sys.path.insert(0,str(model/'deps'))
    from timing.tifa_pronunciation import ensure_apostrophes_registered
    from g2pflow.preprocessors.simple import FilterPunctuation, LowercasePreprocessor
    normalize=ensure_apostrophes_registered()()
    source=['I’m still your girl','Wait ’til I hear our song','Baby, you’re just harder']
    actual=LowercasePreprocessor().process(FilterPunctuation().process(normalize.process(source)))
    expected=LowercasePreprocessor().process(FilterPunctuation().process([canonical_apostrophes(x) for x in source]))
    assert actual==expected
    assert actual==["i'm still your girl","wait 'til i hear our song",'baby'," you're just harder"]


def test_long_mark_restoration_also_accepts_native_ascii_apostrophes():
    from timing.tifa_worker import restore_silent_marks
    words,marks=restore_silent_marks('I’m ナー',["i'm",'ナ'])
    assert words==["i'm",'ナー'] and marks=={1}
