"""Model-specific alignment routes; shared audio and request planning live elsewhere."""
import copy
from .audio import decode, slice_audio
from .ctc import CtcAligner
from .singing import SingingAligner
from . import tifa


def align_llm_direct(lines, tracks, out_dir, report, log, *, syllables=True, direct_config=None):
    from .llm_direct import DirectSession
    session = DirectSession(direct_config, log, out_dir,
                            prompt_profile=report.get('llm_prompt_profile','fine'))
    source = (report.get('background_source') if any(line.role == 'bg' for line in lines) else
              'separated_lead' if report.get('audio_mode') == 'lead' else tracks.source)
    source = 'original_mix' if source == 'original' else source
    session.prepare(lines, tracks.working, tracks.rate, tracks.background, audio_source=source)
    report['llm_direct'] = session.report()
    align_llm_records(lines, tracks, out_dir, report, log, session.records)


def align_llm_import(lines, tracks, out_dir, report, log, *, syllables=True, direct_config=None):
    audit = report['llm_import']
    log(f'使用网页 LLM 导入结果：{len(audit["lines"])} 行；本次不请求任何 LLM。')
    align_llm_records(lines, tracks, out_dir, report, log, audit['lines'])


def align_llm_records(lines, tracks, out_dir, report, log, records):
    from .llm_direct import detected_language, align as direct_align, locate as direct_locate
    working, sr = tracks.working, tracks.rate
    background_working, background_source = tracks.background, tracks.background_path
    singing_source = tracks.main_path
    for line, record in zip(lines, records):
        line.language = detected_language(record['parts'])
    report['language'] = detected_language([part for record in records for part in record['parts']])
    coarse = direct_locate(working, sr, lines, records, log, background_working)
    singing_audio, singing_rate = decode(singing_source, out_dir / 'singing.wav', rate=44100, floating=True)
    background_audio = None
    if any(line.role == 'bg' for line in lines):
        background_audio, _ = decode(background_source, out_dir / 'background_alignment.wav', rate=44100, floating=True)
    report['english_splitter'] = 'llm_import' if 'llm_import' in report else 'llm_direct'
    report['coarse_localization'] = coarse
    report['tifa'] = direct_align(singing_audio, singing_rate, lines, out_dir, records, log, background_audio, coarse)
    report['timestamp_grid_ms'] = 10


def align_tifa(lines, tracks, out_dir, report, log, *, syllables=True, direct_config=None):
    working, sr = tracks.working, tracks.rate
    singing_source = tracks.main_path
    background_working, background_source = tracks.background, tracks.background_path
    singer = None
    try:
        if not all(line.start is not None and line.end is not None for line in lines):
            raise ValueError("TIFA 精细对齐目前需要逐句 LRC／JSON 定位；纯文本可使用自动方案。")
        from .audio_readings import current_session as audio_session
        from .llm_splitter import current_session as english_session
        if audio_session() is not None:
            audio_session().prepare(lines,working,sr,background_working)
        if english_session() is not None and english_session().audio_enabled:
            english_session().audio_sources=(working,sr,background_working)
            log('英语听音拆分已开启：按实际句段发送音频，不跨不同演唱片段复用拆分。')
        coarse_rows = []
        singer = CtcAligner(log)
        try:
            for index,line in enumerate(lines):
                left,right = line.start,line.end
                selected = background_working if line.role == "bg" else working
                # Keep the supplied anchors: CTC only suggests where to
                # search for a following pause, never a fine-unit duration.
                probe = copy.deepcopy(line)
                log(f"定位句段 {index+1}/{len(lines)}：{line.text[:32]}")
                try:
                    singer.align(slice_audio(selected,left,right),probe,False)
                except ValueError:
                    from .audio_readings import primary_readings,without_primary
                    if not primary_readings(line):
                        raise
                    log(f'第 {index+1} 行听音首选无法完成粗定位，改用本地词典重试；TIFA 保留读音候选。')
                    probe=copy.deepcopy(line)
                    with without_primary():
                        singer.align(slice_audio(selected,left,right),probe,False)
                    line.flags.append('llm_audio_coarse_fallback')
                coarse_rows.append({"start":left+probe.start,"end":left+probe.end})
        finally:
            singer.release()
            singer = None
        singing_audio,singing_rate = decode(singing_source,out_dir/"singing.wav",rate=44100,floating=True)
        background_audio = None
        if any(line.role == "bg" for line in lines):
            background_audio,_ = decode(background_source,out_dir/"background_alignment.wav",rate=44100,floating=True)
        report["coarse_localization"] = coarse_rows
        report["tifa"] = tifa.align(singing_audio,singing_rate,lines,out_dir,coarse_rows,log,
                                    syllables=syllables,background=background_audio)
        report["timestamp_grid_ms"] = 10
        return

    finally:
        if singer is not None and hasattr(singer, "release"):
            singer.release()


def align_ctc(lines, tracks, out_dir, report, log, *, syllables=True, direct_config=None):
    working, sr = tracks.working, tracks.rate
    background_working = tracks.background
    singer = None
    try:
        if not all(line.start is not None and line.end is not None for line in lines):
            raise ValueError("MMS 逐句对齐需要带时间的 LRC 或 JSON；纯文本可选择 SOFA。")
        singer = CtcAligner(log)
        report["timestamp_grid_ms"] = 20
        report["ctc_note"] = "CTC 非空白状态定位发音证据，不等于完整持续音的起止；20ms 网格不代表20ms准确度。"
        for index, line in enumerate(lines):
            left, right = line.start, line.end
            if right-left > 30:
                raise ValueError(f"第 {index+1} 行超过30秒，请拆分 LRC 句段。")
            log(f"MMS 逐句对齐 {index+1}/{len(lines)} [{left:.2f}–{right:.2f}]：{line.text[:38]}")
            selected = background_working if line.role == "bg" else working
            info = singer.align(slice_audio(selected, left, right), line, syllables)
            for unit in line.words:
                unit.start = round(unit.start+left, 3)
                unit.end = round(unit.end+left, 3)
                if unit.start < left-.001 or unit.end > right+.001:
                    raise ValueError(f"第 {index+1} 行模型边界越出 LRC 范围，未导出。")
            line.start, line.end = line.words[0].start, line.words[-1].end
            report.setdefault("anchored_alignment", []).append({"line": index+1, "range_start": left, "range_end": right, **info})
        return

    finally:
        if singer is not None and hasattr(singer, "release"):
            singer.release()


def align_sofa(lines, tracks, out_dir, report, log, *, syllables=True, direct_config=None):
    singing_source = tracks.main_path
    background_source = tracks.background_path
    lead_info = tracks.lead_separation
    singer = None
    try:
        if syllables:
            from .singing import prepare_lines_llm
            prepare_lines_llm(lines)
        singer = SingingAligner(log)
        # Decode the source at the native SOFA sample rate.
        singing_audio, singing_rate = decode(singing_source, out_dir / "singing.wav", rate=44100, floating=True)
        background_audio = singing_audio
        if lead_info and any(line.role == "bg" for line in lines):
            background_audio, _ = decode(background_source, out_dir / "background_alignment.wav", rate=44100, floating=True)
        mains = [line for line in lines if line.role == "main"]
        anchored = [line for line in mains if line.start is not None]
        if anchored and len(anchored) != len(mains):
            raise ValueError("主唱歌词需全部有逐句时间，或全部使用纯文本。")
        if not anchored:
            report["singing_alignment"] = singer.align(singing_audio, mains, syllables, out_dir, sample_rate=singing_rate)
            report["timestamp_grid_ms"] = report["singing_alignment"]["frame_ms"]
        for index, line in enumerate(lines):
            if not (line.role == "bg" or anchored):
                continue
            left, right = line.start, line.end
            log(f"逐句对齐 {index+1}/{len(lines)} [{left:.2f}–{right:.2f}]：{line.text[:38]}")
            selected_audio = background_audio if line.role == "bg" else singing_audio
            info = singer.align(slice_audio(selected_audio, left, right, singing_rate), [line], syllables, out_dir, sample_rate=singing_rate, native=True)
            for unit in line.words:
                unit.start = round(unit.start+left, 3)
                unit.end = round(unit.end+left, 3)
                if unit.start < left-.001 or unit.end > right+.001:
                    raise ValueError(f"第 {index+1} 行的模型边界越出提供的逐句范围，未导出。")
            for phone in info.get("phonemes", []):
                phone["start"] += left
                phone["end"] += left
            report.setdefault("anchored_alignment", []).append({"line": index+1, "range_start": left, "range_end": right, **info})
            line.start, line.end = line.words[0].start, line.words[-1].end
            line.flags.append("anchored_singing_alignment_check")
            if info.get("method") == "experimental_bilingual":
                line.flags.append("experimental_bilingual_alignment_check")
            report["timestamp_grid_ms"] = info["frame_ms"]
        return

    finally:
        if singer is not None and hasattr(singer, "release"):
            singer.release()
