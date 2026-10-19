"""Phase 62 Mandarin integration: LLM direct parts, MMS coarse labels and audio reading candidates.

Runs against the bundled pinyin table; native TIFA/g2pflow checks skip when the
downloaded runtime is absent. No provider calls: every transport is mocked.
"""
import base64
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

from timing import audio_readings as ar
from timing import chinese
from timing.llm_direct import DirectSession, coarse_units, compile_parts, write_pfml
from timing.llm_splitter import LlmConfig
from timing.schema import Line, Unit


def zh(text, reading):
    return {'text': text, 'lang': 'zh', 'reading': reading}


def en(text, phones):
    return {'text': text, 'lang': 'en', 'reading': phones.split()}


ZH_SYLLABLES = ('ni', 'hao', 'ai', 'shi', 'jie', 'er', 'qian', 'ling', 'si', 'nv', 'nian', 'ne')


@pytest.fixture
def zh_inventory(monkeypatch):
    """Native zh symbols derived from the bundled table, never guessed."""
    import timing.llm_direct as llm
    phones = set()
    for syllable in ZH_SYLLABLES:
        for group in chinese.pinyin_groups(syllable):
            phones.update(f"zh/{phone}" for phone in group['phones'])
    phones.update({'en/b', 'en/ey', 'en/iy', 'en/l', 'en/ah', 'en/v'})
    monkeypatch.setattr(llm, 'phone_inventory', lambda: frozenset(phones))
    return frozenset(phones)


def fixture_reply(items):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': items})}}]})


def attach(transport, handler):
    transport.client.close()
    transport.client = httpx.Client(transport=httpx.MockTransport(handler))


def payload_from(request):
    body = json.loads(request.content)
    content = body['messages'][1]['content']
    uri = content[0]['input_audio']['data']
    wave, rate = sf.read(io.BytesIO(base64.b64decode(uri.split(',', 1)[1])))
    assert rate == 16000 and wave.ndim == 1 and len(wave) > 0
    return body, json.loads(content[1]['text'])


def reply(items):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'items': items}), 'reasoning_content': 'private-not-for-audit'}}]})


def test_compile_parts_chinese_per_character_display_and_tones_normalized(zh_inventory):
    parts = compile_parts('你好', [zh('你', 'ni3'), zh('好', 'Hǎo')], 'zh')
    assert ''.join(p['text'] for p in parts) == '你好'
    assert [p['reading'] for p in parts] == ['ni', 'hao']
    assert parts[0]['groups'] == chinese.pinyin_groups('ni')
    assert parts[1]['groups'] == chinese.pinyin_groups('hao')
    assert all(set(p) == {'text', 'lang', 'reading', 'groups'} for p in parts)
    assert all(g['phones'] for p in parts for g in p['groups'])


def test_compile_parts_chinese_digits_keep_one_segment_with_multi_syllable_reading(zh_inventory):
    parts = compile_parts('2024年', [zh('2024', 'er qian ling er shi si'), zh('年', 'nian4')], 'zh')
    assert [p['reading'] for p in parts] == ['er qian ling er shi si', 'nian']
    assert len(parts[0]['groups']) == 6
    assert ''.join(p['text'] for p in parts) == '2024年'


def test_compile_parts_chinese_scope_allows_en_but_not_foreign_scripts(zh_inventory):
    parts = compile_parts('爱你 baby', [zh('爱', 'ai4'), zh('你', 'ni3'), en(' baby', 'b ey b iy')], 'zh-en')
    assert [p['lang'] for p in parts] == ['zh', 'zh', 'en']
    with pytest.raises(ValueError):
        compile_parts('あい', [{'text': 'あ', 'lang': 'ja', 'reading': 'ア'}], 'zh')
    with pytest.raises(ValueError):
        compile_parts('爱你', [zh('爱你', 'ai ni')], 'zh')


def test_compile_parts_chinese_readings_must_exist_in_native_dictionary(zh_inventory):
    parts = compile_parts('女', [zh('女', 'nü3')], 'zh')
    assert parts[0]['reading'] == 'nv'
    assert parts[0]['groups'] == chinese.pinyin_groups('nv')
    with pytest.raises(ValueError):
        compile_parts('你', [zh('你', 'xqz')], 'zh')
    with pytest.raises(ValueError):
        compile_parts('你', [zh('你', 'ni hao!')], 'zh')


def test_write_pfml_chinese_keeps_language_and_pinyin_group_scripts(tmp_path, zh_inventory):
    parts = compile_parts('你好', [zh('你', 'ni3'), zh('好', 'hao3')], 'zh')
    audio = tmp_path / 'a.wav'
    write_pfml(audio, parts)
    root = ET.fromstring('<root>' + audio.with_suffix('.pfml').read_text(encoding='utf-8') + '</root>')
    words = list(root)
    assert [w.get('language') for w in words] == ['zh', 'zh']
    assert [w.get('text') for w in words] == ['你', '好']
    groups = [g for w in words for g in w]
    assert [g.get('script') for g in groups] == ['ni', 'hao']
    assert [g.get('phonemes') for g in groups] == [
        ' '.join(chinese.pinyin_groups('ni')[0]['phones']),
        ' '.join(chinese.pinyin_groups('hao')[0]['phones'])]


def test_coarse_units_chinese_use_roman_pinyin_labels_without_timing(zh_inventory):
    parts = compile_parts('你好 Love', [zh('你', 'ni3'), zh('好', 'hao3'), en(' Love', 'l ah v')], 'zh-en')
    units, labels = coarse_units(parts)
    assert [u.text for u in units] == ['你', '好', ' Love']
    assert labels == ['ni', 'hao', 'love']
    assert [u.reading for u in units] == ['ni', 'hao', 'l ah v']
    assert all(u.start is None and u.end is None for u in units)
    units, labels = coarse_units(compile_parts('女', [zh('女', 'nü3')], 'zh'))
    assert labels == ['nu']


def test_direct_session_chinese_parts_detected_language_and_audit(tmp_path, zh_inventory):
    lines = [Line('你好', 0, 2, language='zh'), Line('爱你 baby', 2, 4, language='zh-en')]
    session = DirectSession(LlmConfig('https://fixture.example/v1', 'mimo-v2.6-pro', 'private-key'), lambda _: None, tmp_path)
    calls = []
    def handler(request):
        body = json.loads(request.content)
        content = body['messages'][1]['content']
        payload = json.loads(content[1]['text'])
        calls.append(payload)
        assert all('language' not in item for item in payload['items'])
        assert payload['language_mode'] == 'audio_detected'
        return fixture_reply([
            {'id': 'line_001', 'parts': [zh('你', 'ni3'), zh('好', 'hao3')]},
            {'id': 'line_002', 'parts': [zh('爱', 'ai4'), zh('你', 'ni3'), en(' baby', 'b ey b iy')]}])
    session.transport.client.close()
    session.transport.client = httpx.Client(transport=httpx.MockTransport(handler))
    session.prepare(lines, np.zeros(4 * 16000), 16000)
    assert len(calls) == 1
    assert 'Mandarin parts' in session.prompt and 'pinyin' in session.prompt
    assert [r['status'] for r in session.records] == ['accepted', 'accepted']
    assert [p['reading'] for p in session.records[0]['parts']] == ['ni', 'hao']
    assert [p['lang'] for p in session.records[1]['parts']] == ['zh', 'zh', 'en']
    assert session.records[1]['parts'][1]['groups'] == chinese.pinyin_groups('ni')
    audit = json.loads((tmp_path / 'llm_direct.json').read_text(encoding='utf-8'))
    assert 'private-key' not in json.dumps(audit)
    for record in audit['lines']:
        for part in record['parts']:
            assert set(part) == {'text', 'lang', 'reading', 'groups'}
    assert lines[0].start == 0 and lines[0].end == 2


def test_direct_payload_ignores_selected_language_for_han_only_lines(tmp_path, zh_inventory):
    lines = [Line('你好', 0, 2, language='ja'), Line('今日', 2, 4, language='zh')]
    session = DirectSession(LlmConfig('https://fixture.example/v1', 'm'), lambda _: None, tmp_path)
    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'][1]['text'])
        assert all('language' not in item for item in payload['items'])
        return fixture_reply([{'id': 'line_001', 'parts': [zh('你', 'ni3'), zh('好', 'hao3')]},
                              {'id': 'line_002', 'parts': []}])
    attach(session.transport, handler)
    session.prepare(lines, np.zeros(4 * 16000), 16000)
    assert session.records[0]['status'] == 'accepted'
    assert session.records[1]['status'] == 'manual'
    assert '未确定' in session.records[1]['reason']


def test_validate_readings_chinese_normalizes_and_keeps_ja_default():
    assert ar.validate_readings({'surface': '你好'},
                                {'id': 'w0', 'surface': '你好', 'readings': ['ni3 hao3']}, 'zh') == ['ni hao']
    assert ar.validate_readings({'surface': '你好'},
                                {'id': 'w0', 'surface': '你好', 'readings': ['NI'], 'language': 'zh'}, 'zh') == ['ni']
    assert ar.validate_readings({'surface': '你好'},
                                {'id': 'w0', 'surface': '你好', 'readings': ['nǐ hǎo'], 'language': 'zh-en'}, 'zh-en') == ['ni hao']
    with pytest.raises(ValueError):
        ar.validate_readings({'surface': '你好'},
                             {'id': 'w0', 'surface': '你好', 'readings': ['ni3'], 'language': 'ja'}, 'zh')
    with pytest.raises(ValueError):
        ar.validate_readings({'surface': '2人'},
                             {'id': 'w0', 'surface': '2人', 'readings': ['フタリ'], 'language': 'zh'}, 'ja')
    assert ar.validate_readings({'surface': '2人'}, {'id': 'w0', 'surface': '2人', 'readings': ['フタリ']}) == ['フタリ']
    for bad in (['xqz'], ['ni hao!'], ['你好']):
        with pytest.raises(ValueError):
            ar.validate_readings({'surface': '你'}, {'id': 'w0', 'surface': '你', 'readings': bad, 'language': 'zh'})
    with pytest.raises(ValueError):
        ar.validate_readings({'surface': '你'}, {'id': 'w0', 'surface': '你', 'readings': ['ni3']})
    with pytest.raises(ValueError):
        ar.validate_readings({'surface': '你'}, {'id': 'w0', 'surface': '你', 'readings': ['ni3'], 'language': 'zh', 'start': 0})
    with pytest.raises(ValueError):
        ar.validate_readings({'surface': '你'}, {'id': 'w0', 'surface': '你', 'readings': ['ni3'], 'language': 'fr'})


def test_audio_session_chinese_targets_baselines_and_normalized_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr('timing.singing.pronunciation_overrides', lambda: {'ja': {}, 'en': {}, 'zh': {}})
    line = Line('你好世界', 0, 2, language='zh')
    calls = []
    def handler(request):
        _, payload = payload_from(request)
        calls.append(payload)
        return reply([{'id': item['id'], 'surface': item['surface'],
                       'readings': ['ne3' if item['surface'] == '你' else item['baseline'] + '3']}
                      for item in payload['items']])
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1', 'm'), lambda _: None, tmp_path) as session:
        attach(session.transport, handler)
        session.prepare([line], np.zeros(3 * 16000), 16000)
        assert [i['language'] for i in calls[0]['items']] == ['zh'] * 4
        assert [i['surface'] for i in calls[0]['items']] == ['你', '好', '世', '界']
        assert [i['baseline'] for i in calls[0]['items']] == ['ni', 'hao', 'shi', 'jie']
        assert ar.primary_readings(line) == {'你': 'ne', '好': 'hao', '世': 'shi', '界': 'jie'}
        assert ar.clip_candidates([line]) == {'你': ['ne', 'ni'], '好': ['hao'], '世': ['shi'], '界': ['jie']}
        assert session.records[0]['status'] == 'accepted'
        assert 'llm_audio_reading_candidate' in line.flags
    audit = (tmp_path / 'audio_readings.json').read_text(encoding='utf-8')
    assert 'private-not-for-audit' not in audit


def test_audio_session_zh_en_targets_mandarin_only_and_honours_manual_override(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides',
                        lambda: {'ja': {}, 'en': {}, 'zh': {'好': 'hao'}})
    lines = [Line('你好 baby', 0, 2, language='zh-en'), Line('In 40 years', 2, 4, language='en'),
             Line('ひだまり', 4, 6, language='ja')]
    calls = []
    def handler(request):
        _, payload = payload_from(request)
        calls.append(payload)
        return reply([{'id': item['id'], 'surface': item['surface'], 'readings': ['ni3']} for item in payload['items']])
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1', 'm'), lambda _: None) as session:
        attach(session.transport, handler)
        session.prepare(lines, np.zeros(6 * 16000), 16000)
        assert len(calls) == 1
        assert [i['surface'] for i in calls[0]['items']] == ['你']
        assert ar.clip_candidates([lines[0]]) == {'你': ['ni']}
        assert ar.clip_candidates([lines[0]], 'zh') == {'你': ['ni']}
        assert ar.clip_candidates([lines[0]], 'ja') == {}


def test_audio_session_requests_candidates_for_units_without_local_pinyin(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides', lambda: {'ja': {}, 'en': {}, 'zh': {}})
    monkeypatch.setattr(chinese, 'chinese_units', lambda text: [Unit('嗯', '嗯', '', source_pronunciation='')])
    line = Line('嗯', 0, 1, language='zh')
    def handler(request):
        _, payload = payload_from(request)
        assert [(i['surface'], i['baseline']) for i in payload['items']] == [('嗯', '')]
        return reply([{'id': item['id'], 'surface': item['surface'], 'readings': ['en3']} for item in payload['items']])
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1', 'm'), lambda _: None) as session:
        attach(session.transport, handler)
        session.prepare([line], np.zeros(2 * 16000), 16000)
        assert ar.primary_readings(line) == {'嗯': 'en'}
        assert session.records[0]['status'] == 'accepted'


def test_audio_session_chinese_unsupported_text_is_flagged_not_dropped(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides', lambda: {'ja': {}, 'en': {}, 'zh': {}})
    line = Line('あい', 0, 1, language='zh')
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1', 'm'), lambda _: None) as session:
        attach(session.transport, lambda _: httpx.Response(500))
        session.prepare([line], np.zeros(2 * 16000), 16000)
        assert session.transport.requests == 0
        assert session.records[0]['status'] == 'fallback' and session.records[0]['reason']
        assert 'llm_audio_reading_fallback' in line.flags


def test_clip_candidates_language_filter_separates_families(monkeypatch):
    monkeypatch.setattr('timing.singing.pronunciation_overrides', lambda: {'ja': {}, 'en': {}, 'zh': {}})
    zh_line = Line('你好', 0, 1, language='zh')
    ja_line = Line('氷', 1, 2, language='ja')
    with ar.use_audio_readings(LlmConfig('https://fixture.example/v1', 'm'), lambda _: None) as session:
        session.by_line[ar.line_key(zh_line)] = {'candidates': {'你': ['ni']}}
        session.by_line[ar.line_key(ja_line)] = {'candidates': {'氷': ['コオリ']}}
        assert ar.clip_candidates([zh_line, ja_line]) == {'你': ['ni'], '氷': ['コオリ']}
        assert ar.clip_candidates([zh_line, ja_line], 'zh') == {'你': ['ni']}
        assert ar.clip_candidates([zh_line, ja_line], 'ja') == {'氷': ['コオリ']}


def test_audio_prompt_mentions_both_reading_scripts():
    prompt = ar.PROMPT_PATH.read_text(encoding='utf-8')
    assert 'Mandarin' in prompt and 'pinyin' in prompt and 'KATAKANA' in prompt


def test_chinese_compilation_with_installed_native_inventory():
    from timing.config import MODELS
    if not (MODELS / 'tifa/vocabulary.json').exists():
        pytest.skip('TIFA vocabulary not downloaded')
    from timing.llm_direct import phone_inventory
    assert any(symbol.startswith('zh/') for symbol in phone_inventory())
    parts = compile_parts('你好', [zh('你', 'ni'), zh('好', 'hao')], 'zh')
    assert [p['reading'] for p in parts] == ['ni', 'hao']


def test_pfml_chinese_parses_with_native_g2pflow(tmp_path):
    from timing.config import MODELS
    model = MODELS / 'tifa'
    if not (model / 'deps/g2pflow').exists() or not (model / 'vocabulary.json').exists():
        pytest.skip('TIFA runtime not downloaded')
    sys.path.insert(0, str(model / 'deps'))
    from g2pflow import G2PPipeline, G2PWord, parse_pfml
    parts = compile_parts('你好', [zh('你', 'ni3'), zh('好', 'hao3')], 'zh')
    audio = tmp_path / 'a.wav'
    write_pfml(audio, parts)
    source = audio.with_suffix('.pfml').read_text(encoding='utf-8')
    words = G2PPipeline(preprocessors=[], converters=[]).convert_pfml(source, languages=['zh', 'en'])
    assert [w.language for w in words] == ['zh', 'zh']
    assert all(w.readings[0].paths[0] for w in words)
    assert sum(isinstance(p, G2PWord) for p in parse_pfml(source).parts) == 2
