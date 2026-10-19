"""Request planning, audio preparation and model dispatch for one alignment job."""
from pathlib import Path

from . import alignment
from .audio import decode
from .reporting import finish
from .request_plan import plan_request
from .sources import prepare_sources


def run(audio_path: Path, lyrics: str, out_dir: Path, language='mixed', syllables=True,
        vocals_path: Path | None = None, log=print, *, audio_mode='lead', aligner='auto',
        english_splitter='syllabify', llm_config=None, llm_audio_reading=False, llm_audio_split=False, llm_result=None,
        llm_prompt_profile='fine'):
    # Reject invalid/dormant settings before touching audio, models or a provider.
    options = dict(language=language, aligner=aligner, audio_mode=audio_mode, syllables=syllables,
                   english_splitter=english_splitter, llm_audio_reading=llm_audio_reading,
                   llm_audio_split=llm_audio_split)
    plan_request(lyrics, **options)
    if aligner == 'llm_direct':
        from .llm_direct import read_prompt
        read_prompt(llm_prompt_profile)
    imported = None
    if aligner == 'llm_import':
        from .llm_import import import_response
        imported = import_response(lyrics, llm_result)
    audio_path, out_dir = Path(audio_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log('解码音频…')
    audio, rate = decode(audio_path, out_dir / 'audio.wav')
    duration = len(audio) / rate
    plan = plan_request(lyrics, duration=duration, **options)
    from .llm_splitter import use_llm, LlmConfig
    config = (llm_config or LlmConfig.from_env()) if plan.needs_llm else None
    tracks = prepare_sources(audio_path, audio, rate, out_dir, vocals_path=vocals_path,
                             audio_mode=plan.audio_mode, has_background=plan.has_background, log=log)
    (out_dir / 'lyrics.txt').write_text(lyrics, encoding='utf-8')
    report = {'language': 'auto' if plan.aligner in ('llm_direct','llm_import') else language, 'duration': duration, 'source': tracks.source,
              'audio_mode': plan.audio_mode, 'separation': tracks.separation,
              'lead_separation': tracks.lead_separation, 'aligner': plan.aligner,
              'timestamp_grid_ms': None, 'warnings': [], 'lines': [],
              'background_source': ('provided_vocals' if vocals_path else 'full_vocals')
                                   if plan.has_background and plan.audio_mode != 'source'
                                   else tracks.source if plan.has_background else None}
    if plan.aligner == 'llm_direct':
        report['llm_prompt_profile'] = llm_prompt_profile
    if imported is not None:
        import json
        report['llm_import'] = imported
        (out_dir / 'llm_import.json').write_text(json.dumps(imported, ensure_ascii=False, indent=2), encoding='utf-8')

    from .english_syllables import use_splitter, current_method
    from .audio_readings import use_audio_readings
    english_llm = plan.syllables and plan.english_splitter == 'llm'
    with use_splitter(plan.english_splitter), use_llm(config if english_llm else None, log, out_dir) as session, \
            use_audio_readings(config if plan.llm_audio_reading else None, log, out_dir):
        if session is not None:
            session.audio_enabled = plan.llm_audio_split
        report['english_splitter'] = 'llm_direct' if plan.aligner == 'llm_direct' else current_method() if plan.syllables else 'word'
        route = {'tifa': alignment.align_tifa, 'ctc': alignment.align_ctc,
                 'sofa': alignment.align_sofa, 'llm_direct': alignment.align_llm_direct,
                 'llm_import': alignment.align_llm_import}[plan.aligner]
        route(plan.lines, tracks, out_dir, report, log, syllables=plan.syllables, direct_config=config)
        return finish(plan.lines, duration, report['language'], audio_path.stem, out_dir, report, log)
