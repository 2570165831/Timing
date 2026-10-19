"""Bounded lexical readings for letter/kana compounds, with original offsets.

Do not guess that every English word next to katakana is Japanese. Unknown
compounds still require a pronunciation override; extend this table explicitly.
"""
import re
import unicodedata

READINGS = {'tシャツ': 'ティーシャツ', 'yシャツ': 'ワイシャツ'}
PATTERN = re.compile(r'(?<![A-Za-zＡ-Ｚａ-ｚ])[TtYyＴｔＹｙ]シャツ')


def japanese_loanword_spans(text):
    for match in PATTERN.finditer(text):
        key = unicodedata.normalize('NFKC', match.group()).lower()
        yield match.start(), match.end(), READINGS[key]


def loanword_parts(surface, reading):
    key = unicodedata.normalize('NFKC', surface).lower()
    if READINGS.get(key) != reading:
        return None
    return [(surface[0], reading[:-3]), ('シャ', 'シャ'), ('ツ', 'ツ')]
