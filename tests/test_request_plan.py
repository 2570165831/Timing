"""Reject unusable requests before model work and keep entry points consistent."""
import inspect
import json

import pytest
from fastapi.testclient import TestClient

from timing.request_plan import plan_request


@pytest.mark.parametrize('text', ['', ' \ufeff\n', '[]', '[{"text":', '{"text":"Hello"}', '[null]',
    '[{"text":12}]', '[{"text":"Hello","start":false,"end":2}]',
    '[{"text":"Hello","start":0,"end":NaN}]',
    '[{"text":"Hello","translation":null}]',
    '[{"text":"Hello","start":1}]'])
def test_invalid_lyrics_fail_before_models(text):
    with pytest.raises(ValueError):
        plan_request(text)


def test_automatic_model_selection_and_unanchored_lyrics_requirements():
    assert plan_request('Hello\nWorld').aligner == 'sofa'
    assert plan_request('[00:01]Hello\n[00:03]World').aligner == 'tifa'
    for name in ('tifa', 'ctc', 'llm_direct'):
        with pytest.raises(ValueError, match='逐句'):
            plan_request('Hello', aligner=name)
    with pytest.raises(ValueError, match='全部'):
        plan_request('Hello\n[00:03]World')


@pytest.mark.parametrize('options', [{'aligner':'qwen'}, {'audio_mode':'old-lead'}, {'english_splitter':'unknown'}])
def test_removed_and_unknown_options_are_rejected(options):
    with pytest.raises(ValueError):
        plan_request('Hello', **options)


@pytest.mark.parametrize('text', ['[{"text":"Long","start":0,"end":31}]', '[00:00]Long\n[00:31]Next'])
def test_mms_known_long_ranges_are_rejected_during_preflight(text):
    with pytest.raises(ValueError, match='30秒'):
        plan_request(text, aligner='ctc')


def test_lrc_final_range_is_checked_once_actual_audio_duration_is_known():
    assert plan_request('[00:01]Hello', aligner='ctc').aligner == 'ctc'
    with pytest.raises(ValueError, match='30秒'):
        plan_request('[00:01]Hello', aligner='ctc', duration=35)
    assert plan_request('[70:01]Hello').aligner == 'tifa'


def test_inactive_provider_preferences_do_not_require_credentials():
    plan = plan_request('[00:01]Hello', aligner='ctc', english_splitter='llm')
    assert plan.english_splitter == 'syllabify' and not plan.needs_llm
    japanese = plan_request('[00:01]光の中へ', english_splitter='llm', llm_audio_split=True)
    assert not japanese.syllables and not japanese.llm_audio_split and not japanese.needs_llm
    direct = plan_request('[00:01]Hello', aligner='llm_direct', llm_audio_split=True, llm_audio_reading=True)
    assert direct.needs_llm and not direct.syllables and not direct.llm_audio_reading and not direct.llm_audio_split


def test_explicit_unusable_audio_assist_is_rejected():
    with pytest.raises(ValueError, match='英语听音拆分'):
        plan_request('[00:01]Hello', aligner='ctc', english_splitter='llm', llm_audio_split=True)
    with pytest.raises(ValueError, match='多模态听音'):
        plan_request('[00:01]光の中へ', aligner='sofa', llm_audio_reading=True)


def test_ordered_main_overlaps_remain_supported_but_reversed_input_is_rejected():
    rows = [{'text':'First','start':0,'end':5}, {'text':'Overlap','start':2,'end':4,'agent':'v2'}]
    assert len(plan_request(json.dumps(rows)).lines) == 2
    with pytest.raises(ValueError, match='排序'):
        plan_request(json.dumps(rows[::-1]))


@pytest.mark.parametrize('fields', [
    {'lyrics':''}, {'lyrics':'[{"text":'}, {'lyrics':'Hello','aligner':'qwen'},
    {'lyrics':'[00:00]Hello','separate':'false'},
    {'lyrics':'[00:00]Hello','lead':'false'},
    {'lyrics':'[{"text":"Hello","start":0,"end":31}]','aligner':'ctc'},
])
def test_api_refuses_invalid_requests_before_queue_or_upload(tmp_path, monkeypatch, fields):
    from timing import server
    monkeypatch.setattr(server, 'DATA', tmp_path)
    monkeypatch.setattr(server, 'jobs', {})
    monkeypatch.setattr(server.executor, 'submit', lambda *args: pytest.fail('invalid request was queued'))
    with TestClient(server.app) as client:
        response = client.post('/api/jobs', data=fields, files={'audio':('song.wav',b'fixture')},
                               headers={'X-Timing-Token':server.TOKEN})
    assert response.status_code == 400 and server.jobs == {}
    assert not list(tmp_path.iterdir())


def test_api_edited_text_wins_over_the_imported_file(tmp_path, monkeypatch):
    from timing import server
    monkeypatch.setattr(server, 'DATA', tmp_path)
    monkeypatch.setattr(server, 'jobs', {})
    submitted = []
    monkeypatch.setattr(server.executor, 'submit', lambda *args: submitted.append(args))
    with TestClient(server.app) as client:
        response = client.post('/api/jobs', data={'lyrics':'Edited words','audio_mode':'vocals'},
            files={'audio':('song.wav',b'fixture'), 'lyrics_file':('lyrics.txt',b'Original words')},
            headers={'X-Timing-Token':server.TOKEN})
    assert response.status_code == 200
    args = inspect.signature(server.process).bind(*submitted[0][1:]).arguments
    assert args['lyrics'] == 'Edited words' and args['audio_mode'] == 'vocals' and args['aligner'] == 'sofa'


def test_static_ui_resources_and_five_model_status_contract():
    from timing import server
    with TestClient(server.app) as client:
        root = client.get('/')
        assert '__TIMING_TOKEN__' not in root.text and 'name="timing-token"' in root.text
        for asset in ('styles.css','ui-logic.js','app.js'):
            assert client.get('/static/'+asset+'?v=0.8.0').status_code == 200
        status = client.get('/api/status').json()
    assert status['api_version'] == 7
    assert set(status['models']) == set(status['required_models']) == {'ctc','tifa','singing','bs_lead','separator'}
