"""Phonetic syllables over the selected phones, with conservative display mapping.

syllabify decides phone ownership. Pyphen supplies spelling candidates only;
it never decides acoustic cuts. A spelling/phone count mismatch stays a word.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

VOWELS = frozenset("AA AE AH AO AW AY EH ER EY IH IY OW OY UH UW".split())
CONSONANTS = frozenset("B CH D DH F G HH JH K L M N NG P R S SH T TH V W Y Z ZH".split())
METHODS = ("syllabify", "pyphen", "llm")
_METHOD = ContextVar("english_splitter", default="syllabify")


def current_method():
    return _METHOD.get()


@contextmanager
def use_splitter(method):
    if method not in METHODS:
        raise ValueError("不支持的英语音节划分方案。")
    token = _METHOD.set(method)
    try:
        yield
    finally:
        _METHOD.reset(token)


def split_flags():
    if current_method() == 'llm':
        return ['english_syllable_candidate','english_llm_candidate']
    return ["english_syllable_candidate", "english_spelling_candidate",
            "english_phonetic_syllables" if current_method() == "syllabify" else "english_pyphen_legacy"]


def base_phone(phone):
    return re.sub(r"[012]$", "", phone.rsplit("/", 1)[-1].upper()).replace("AX", "AH")


@lru_cache(maxsize=1)
def pronunciations():
    import cmudict
    return cmudict.dict()


@lru_cache(maxsize=1)
def spelling_dictionary():
    import pyphen
    return pyphen.Pyphen(lang="en_US")


def split_english(word, phones=None, *, method=None):
    method = current_method() if method is None else method
    if method not in METHODS:
        raise ValueError("不支持的英语音节划分方案。")
    if method == 'llm':
        if phones is None:
            return None
        from .llm_splitter import current_session
        session = current_session()
        if session is None:
            raise ValueError('LLM 拆分尚未配置接口。')
        return session.split(word,tuple(phones))
    return _split_english(word, tuple(phones) if phones is not None else None, method)


def word_flags(word,phones):
    if current_method() != 'llm':
        return []
    from .llm_splitter import current_session
    session = current_session()
    return ['english_llm_fallback'] if session and session.fallback(word,phones) else []


def prepare_llm(entries,context):
    prepare_llm_many([(entries,context)])


def prepare_llm_many(groups):
    if current_method() == 'llm':
        from .llm_splitter import current_session
        session = current_session()
        if session is None:
            raise ValueError('LLM 拆分尚未配置接口。')
        session.prepare_many(groups)


@lru_cache(maxsize=4096)
def _split_english(word, phones, method):
    """Return (display piece, first phone, exclusive last phone), or None.

    Never substitute a dictionary pronunciation for the selected model input.
    Stress is recovered only from an exactly matching CMU phone sequence.
    Phone order/content and one vowel per syllable are checked independently
    because an external syllabifier must not drop or duplicate model phones.
    """
    if method == "pyphen" and phones is not None:
        pieces = spelling_dictionary().inserted(word, hyphen="|").split("|")
        nuclei = [i for i,p in enumerate(phones) if base_phone(p) in VOWELS]
        if len(pieces) != len(nuclei) or len(pieces) < 2:
            return None
        cuts = [0] + [max(nuclei[i-1]+1, nuclei[i]-1) for i in range(1,len(nuclei))] + [len(phones)]
        return tuple((part,a,b) for part,a,b in zip(pieces,cuts,cuts[1:]))
    if not re.fullmatch(r"[A-Za-z]+(?:['’][A-Za-z]+)*", word):
        return None
    variants = pronunciations().get(word.lower().replace("’", "'"), [])
    if phones is None:
        if not variants:
            return None
        phones = tuple(variants[0])
    canonical = [base_phone(p) for p in phones]
    if not canonical or any(p not in VOWELS | CONSONANTS for p in canonical):
        return None
    if sum(p in VOWELS for p in canonical) == 1:
        return ((word, 0, len(phones)),)
    # Only use lexical stress when its entire pronunciation matches.
    stressed = next((v for v in variants if [base_phone(p) for p in v] == canonical), None)
    supplied = list(stressed) if stressed else [p.rsplit("/", 1)[-1].upper() for p in phones]
    supplied = [p.replace("AX", "AH") for p in supplied]
    from syllabify.core import factory
    try:
        syllables = factory(" ".join(supplied))
        groups = [[base_phone(str(p).strip()) for p in s.phonemes] for s in syllables]
    except (ValueError, IndexError, AttributeError):
        return None
    if ([p for group in groups for p in group] != canonical or
            any(sum(p in VOWELS for p in group) != 1 for group in groups)):
        return None
    pieces = spelling_dictionary().inserted(word, hyphen="|").split("|")
    if len(pieces) != len(groups) or len(groups) < 2 or "".join(pieces) != word:
        return None
    cursor, result = 0, []
    for piece, group in zip(pieces, groups):
        result.append((piece, cursor, cursor + len(group)))
        cursor += len(group)
    return tuple(result)
