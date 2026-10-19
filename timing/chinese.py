"""Mandarin display units and phonemes, shared by every alignment route.

The small pinyin/phone table is from the pinned MIT TIFA release. Hanzi are
never changed to simplified characters for display. Contextual readings come
from pypinyin; custom readings and audio candidates take precedence.
"""
from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from .schema import Unit
from .text import kept

HAN = r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]'
HAN_RE = re.compile(HAN)
TOKENS = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’][A-Za-z]+)*|[0-9]+|" + HAN)
CHINESE_LANGUAGES = ('zh', 'zh-en')


def is_chinese(language):
    return language in CHINESE_LANGUAGES


@lru_cache(maxsize=1)
def pinyin_dictionary():
    source = Path(__file__).with_name('resources') / 'mandarin_pinyin.txt'
    return {key: phones.split() for key, phones in
            (line.split('\t', 1) for line in source.read_text(encoding='utf-8').splitlines() if '\t' in line)}


def pinyin_groups(reading, *, require_phones=True):
    """Validate explicit syllables, stripping tones only in the acoustic labels."""
    if isinstance(reading, list) and all(isinstance(item, str) for item in reading):
        reading = ' '.join(reading)
    if not isinstance(reading, str) or not reading.strip() or len(reading) > 500:
        raise ValueError('中文读音请填写用空格分隔的拼音。')
    # Preserve the diaeresis before removing tone accents.
    text = unicodedata.normalize('NFC', reading).lower().replace('u:', 'v')
    text = re.sub('[üǖǘǚǜ]', 'v', text)
    text = ''.join(char for char in unicodedata.normalize('NFD', text) if not unicodedata.combining(char))
    text = text.replace('’', "'").replace("'", ' ')
    groups = []
    for value in text.split():
        if not re.fullmatch(r'[a-z]+[0-5]?', value):
            raise ValueError('中文读音只支持拼音音节，可带声调数字，音节之间用空格分隔。')
        script = value.rstrip('012345')
        # j/q/x/y spell ü as u in standard pinyin; accept explicit v too.
        if re.match(r'^[jqxy]v', script):
            script = script[0] + 'u' + script[2:]
        phones = pinyin_dictionary().get(script)
        if not phones and require_phones:
            raise ValueError(f'中文声学词典没有拼音「{value}」，请在自定义读音中指定受支持的唱法。')
        groups.append({'script': script, 'phones': list(phones or [])})
    if not groups:
        raise ValueError('中文读音不能为空。')
    return groups


def pinyin_for_text(text, *, require_phones=True):
    """One contextual pinyin per Han character; unsupported text is explicit."""
    from pypinyin import lazy_pinyin, Style
    from hanziconv import HanziConv
    # Phrase dictionaries mainly contain simplified spellings. Convert only
    # the lookup input so 重慶 and 重庆 share context without changing lyrics.
    lookup = HanziConv.toSimplified(text)
    if len(lookup) != len(text):
        raise ValueError('繁体读音转换无法逐字对应，请补充自定义读音。')
    readings = lazy_pinyin(lookup, style=Style.NORMAL, errors=lambda value:list(value), strict=False)
    result = []
    for reading in readings:
        groups = pinyin_groups(reading, require_phones=require_phones)
        if len(groups) != 1:
            raise ValueError('每个汉字需对应一个拼音音节。')
        result.append(groups[0]['script'])
    if len(result) != len(text):
        raise ValueError('中文读音数量与原文不一致，请补充自定义读音。')
    return result


def chinese_integer(text):
    """Read integer lyrics in Mandarin without changing their displayed digits."""
    if not re.fullmatch('[0-9]+', text) or len(text) > 16:
        raise ValueError('中文数字只支持最多16位整数，请在读音表中注明其他读法。')
    digits = '零一二三四五六七八九'
    if len(text) > 1 and text.startswith('0'):
        return ''.join(digits[int(c)] for c in text)
    number = int(text)
    if number == 0:
        return '零'
    def block(value):
        output, zero = '', False
        for scale, marker in ((1000,'千'),(100,'百'),(10,'十'),(1,'')):
            amount, value = divmod(value, scale)
            if amount:
                if zero: output += '零'
                output += digits[amount] + marker
                zero = False
            elif output and value:
                zero = True
        return output
    pieces, position = [], 0
    while number:
        number, chunk = divmod(number, 10000)
        pieces.append((chunk, ('','万','亿','兆')[position])); position += 1
    output, zero = '', False
    for chunk, marker in reversed(pieces):
        if not chunk:
            if output: zero = True
            continue
        if output and (zero or chunk < 1000): output += '零'
        output += block(chunk) + marker; zero = False
    return output[1:] if output.startswith('一十') else output


def chinese_units(text):
    """Per-character Han units and English words, with exact original text."""
    normalized = unicodedata.normalize('NFKC', text)
    if normalized != text:
        # NFKC may expand a single display character: match by original offsets.
        pattern = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿＡ-Ｚａ-ｚ]+(?:['’][A-Za-z]+)*|[0-9０-９]+|" + HAN)
    else:
        pattern = TOKENS
    readings = {}
    for run in re.finditer(HAN + '+', text):
        readings.update({run.start()+i:pinyin for i,pinyin in enumerate(pinyin_for_text(run.group(), require_phones=False))})
    units, cursor, prefix = [], 0, ''
    for match in pattern.finditer(text):
        gap = text[cursor:match.start()]
        if kept(gap):
            raise ValueError(f'中文歌词包含不支持的发音文字「{gap}」，请检查语言选项。')
        if units: units[-1].text += gap
        else: prefix += gap
        surface = match.group()
        spoken = kept(surface)
        reading = readings.get(match.start(), '')
        if spoken.isascii() and spoken.isdigit():
            reading = ' '.join(pinyin_for_text(chinese_integer(spoken)))
        units.append(Unit(prefix + surface, spoken, reading, source_pronunciation=reading,
                          flags=['chinese_character'] if reading else []))
        prefix = ''; cursor = match.end()
    tail = text[cursor:]
    if kept(tail):
        raise ValueError(f'中文歌词包含不支持的发音文字「{tail}」，请检查语言选项。')
    if units: units[-1].text += tail
    if not units or ''.join(unit.text for unit in units) != text:
        raise ValueError('中文分词未能完整保留歌词。')
    return units


def mandarin_parts(unit, overrides=None, *, require_phones=True):
    custom = (overrides or {}).get(unit.spoken)
    reading = custom or unit.reading or ' '.join(pinyin_for_text(unit.spoken))
    groups = pinyin_groups(reading, require_phones=require_phones)
    phones = [phone for group in groups for phone in group['phones']]
    flags = ['chinese_character']
    if custom: flags.append('custom_pronunciation')
    if unit.spoken.isdigit(): flags.append('number_reading_candidate')
    return [(unit.text, ' '.join(group['script'] for group in groups), phones, flags)]


def phrase_readings(units, overrides):
    """Resolve exact Han phrases by occurrence; single-character keys win later."""
    table = {key:value for key,value in (overrides or {}).items()
             if isinstance(key,str) and len(key)>1 and re.fullmatch(HAN+'+',key)}
    result = {}
    for start in range(len(units)):
        if start in result:
            continue
        for phrase in sorted(table, key=len, reverse=True):
            count = len(phrase)
            if ''.join(unit.spoken for unit in units[start:start+count]) != phrase:
                continue
            groups = pinyin_groups(table[phrase], require_phones=False)
            if len(groups) != count:
                raise ValueError(f'自定义读音「{phrase}」需逐字提供 {count} 个拼音音节，当前为 {len(groups)} 个。')
            result.update({start+offset:group['script'] for offset,group in enumerate(groups)})
            break
    return result


def roman_label(reading):
    """MMS uses unaccented Latin labels, with ü represented by u."""
    return ''.join(group['script'].replace('v','u') for group in pinyin_groups(reading, require_phones=False))
