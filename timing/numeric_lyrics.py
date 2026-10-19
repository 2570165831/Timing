"""Pronunciation candidates for written integers; display text stays unchanged."""
import re
import unicodedata

SMALL = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()


def english_integer(text):
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"不支持的整数读音：{text}")
    if len(text) > 1 and text.startswith("0"):
        return " ".join(SMALL[int(c)] for c in text)
    value = int(text)
    if value < 20:
        return SMALL[value]
    if value < 100:
        tens, rest = divmod(value, 10)
        return TENS[tens] + (" " + SMALL[rest] if rest else "")
    for scale, name in ((10**12,"trillion"),(10**9,"billion"),(10**6,"million"),(1000,"thousand"),(100,"hundred")):
        if scale <= value < 1000*scale:
            left, rest = divmod(value, scale)
            return english_integer(str(left)) + " " + name + (" " + english_integer(str(rest)) if rest else "")
    return " ".join(SMALL[int(c)] for c in text)


def english_numbers(text):
    """Infer English only when the line contains Latin text and no Japanese."""
    return bool(re.search(r"[A-Za-z]", text)) and not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text)


JA_DIGITS = ('ゼロ','イチ','ニ','サン','ヨン','ゴ','ロク','ナナ','ハチ','キュウ')


def japanese_integer(text):
    """A cardinal reading candidate, never a replacement for display text."""
    text = unicodedata.normalize('NFKC',text)
    if not re.fullmatch(r'[0-9]+',text):
        raise ValueError(f'不支持的日语整数读音：{text}')
    if len(text)>16 or (len(text)>1 and text.startswith('0')):
        return ''.join(JA_DIGITS[int(c)] for c in text)
    value = int(text)
    if value < 10:
        return JA_DIGITS[value]
    for scale,name in ((10**12,'チョウ'),(10**8,'オク'),(10000,'マン'),
                       (1000,'セン'),(100,'ヒャク'),(10,'ジュウ')):
        if value >= scale:
            left,rest = divmod(value,scale)
            irregular = {(100,3):'サンビャク',(100,6):'ロッピャク',(100,8):'ハッピャク',
                         (1000,3):'サンゼン',(1000,8):'ハッセン'}
            head = irregular.get((scale,left)) or (('' if left==1 and scale<10000 else japanese_integer(str(left)))+name)
            return head+(japanese_integer(str(rest)) if rest else '')


def japanese_numeric_spans(text):
    """Original offsets plus kana; preserve person-counter readings as one unit.

    English-only lines keep their existing English numeric converter. Numeric
    surfaces in Japanese/mixed lines remain explicitly reviewable candidates.
    """
    if english_numbers(text):
        return
    for match in re.finditer(r'[0-9０-９]+(?:人)?',text):
        surface = match.group()
        people = surface.endswith('人')
        digits = unicodedata.normalize('NFKC',surface[:-1] if people else surface)
        reading = japanese_integer(digits)
        if people:
            regular = text[match.end():match.end()+1] in ('称','前')
            if not regular and digits in ('1','2'):
                reading = {'1':'ヒトリ','2':'フタリ'}[digits]
            else:
                reading = (reading[:-2]+'ヨ' if digits.endswith('4') and reading.endswith('ヨン') else reading)+'ニン'
        yield match.start(),match.end(),reading
