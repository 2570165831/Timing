"""Shared request validation for the API, CLI and alignment pipeline."""
from dataclasses import dataclass
import re

from .schema import Line
from .text import parse_lyrics

ALIGNERS = ('auto', 'tifa', 'ctc', 'sofa', 'llm_direct', 'llm_import')
AUDIO_MODES = ('lead', 'vocals', 'source')
SPLITTERS = ('syllabify', 'pyphen', 'llm')
LANGUAGES = ('mixed', 'ja', 'en', 'auto', 'zh', 'zh-en')
MAX_LYRICS_BYTES = 2 * 1024**2


@dataclass(frozen=True)
class RequestPlan:
    lines: list[Line]
    aligner: str
    audio_mode: str
    syllables: bool
    english_splitter: str
    llm_audio_reading: bool
    llm_audio_split: bool
    needs_llm: bool
    has_background: bool


def plan_request(lyrics, *, duration=None, language='mixed', aligner='auto',
                 audio_mode='lead', syllables=True, english_splitter='syllabify',
                 llm_audio_reading=False, llm_audio_split=False):
    if language not in LANGUAGES:
        raise ValueError('不支持的语言选项。')
    if aligner not in ALIGNERS:
        raise ValueError('不支持的对齐模型。')
    if audio_mode not in AUDIO_MODES:
        raise ValueError('请选择只保留主唱、保留全部人声，或直接使用音轨。')
    if english_splitter not in SPLITTERS:
        raise ValueError('不支持的英语音节划分方案。')
    if not isinstance(lyrics, str) or not lyrics.strip('\ufeff \t\r\n'):
        raise ValueError('请粘贴歌词，或导入 TXT、LRC、JSON 歌词文件。')
    if len(lyrics.encode('utf-8')) > MAX_LYRICS_BYTES:
        raise ValueError('歌词最大 2 MB。')
    lines = parse_lyrics(lyrics, duration)
    for line in lines:
        if line.language == 'auto':
            line.language = language
    anchored = [line.start is not None for line in lines]
    mains = [line for line in lines if line.role == 'main']
    if not mains:
        raise ValueError('至少需要一行主歌词。')
    main_anchors = [line.start is not None for line in mains]
    if any(main_anchors) and not all(main_anchors):
        raise ValueError('主唱歌词需全部有逐句时间，或全部使用纯文本。')
    if aligner == 'auto':
        aligner = 'tifa' if all(anchored) else 'sofa'
    if aligner in ('tifa', 'ctc', 'llm_direct', 'llm_import') and not all(anchored):
        raise ValueError('TIFA、MMS 和 LLM 整曲识别需要逐句 LRC／带完整时间的 JSON；纯文本请使用自动或 SOFA。')
    if aligner in ('llm_direct', 'llm_import'):
        # Language is supplied by the whole-song response, even for JSON rows.
        for line in lines:
            line.language = 'auto'
    if aligner == 'ctc' and any(line.end-line.start > 30 for line in lines):
        raise ValueError('MMS 每句最长30秒，请拆分逐句范围。')

    english = any(re.search(r'[A-Za-z]', line.text) for line in lines)
    reading_language = any(re.search(r'[\u3040-\u30ff\u3400-\u9fff]', line.text) for line in lines)
    direct = aligner in ('llm_direct', 'llm_import')
    effective_syllables = bool(syllables and english and not direct)
    splitter = english_splitter if effective_syllables else 'syllabify'
    # MMS uses spelling labels, so a dormant phoneme splitter never requires API credentials.
    if aligner == 'ctc' and splitter == 'llm':
        splitter = 'syllabify'
    audio_reading = bool(llm_audio_reading and reading_language and not direct)
    audio_split = bool(llm_audio_split and effective_syllables and splitter == 'llm' and not direct)
    if llm_audio_split and not direct and english and not (effective_syllables and splitter == 'llm'):
        raise ValueError('英语听音拆分需要开启英语细分并选择 LLM。')
    if (audio_reading or audio_split) and (aligner != 'tifa' or not all(anchored)):
        raise ValueError('多模态听音需要逐句 LRC，并使用 TIFA 或自动方案。')
    needs_llm = aligner == 'llm_direct' or (effective_syllables and splitter == 'llm') or audio_reading
    return RequestPlan(lines, aligner, audio_mode, effective_syllables, splitter,
                       audio_reading, audio_split, bool(needs_llm), any(line.role == 'bg' for line in lines))
