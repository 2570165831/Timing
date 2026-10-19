"""Optional MiMo-compatible audio-to-reading candidates (Japanese kana, Mandarin pinyin).

Never accepts time output.
"""
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import io
import json
import math
from pathlib import Path
import re

import numpy as np
import soundfile as sf

_SESSION = ContextVar('japanese_audio_readings',default=None)
_NATIVE = ContextVar('native_audio_candidates',default={})
_PRIMARY_DISABLED = ContextVar('audio_primary_disabled',default=False)
PROMPT_PATH = Path(__file__).with_name('prompts')/'japanese_audio_readings.txt'


def clip_audio(samples,rate,start,end,*,max_duration=30):
    if start is None or end is None or not all(math.isfinite(v) for v in (start,end)):
        raise ValueError('音频读音校对需要有效逐句时间。')
    wave = np.asarray(samples,dtype=np.float32)
    if wave.ndim!=1 or rate<=0 or not 0<=start<end<=len(wave)/rate+.001 or end-start>max_duration:
        raise ValueError(f'音频读音校对片段需在0到{max_duration}秒之间。')
    a,b = max(0,round((start-.12)*rate)),min(len(wave),round((end+.12)*rate))
    chunk = wave[a:b]
    if not len(chunk) or not np.isfinite(chunk).all():
        raise ValueError('音频片段无效。')
    if rate!=16000:
        from scipy.signal import resample_poly
        factor=math.gcd(int(rate),16000)
        chunk=resample_poly(chunk,16000//factor,int(rate)//factor).astype(np.float32)
    buffer=io.BytesIO()
    sf.write(buffer,chunk,16000,format='WAV',subtype='PCM_16')
    return 'data:audio/wav;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii'),[round(a/rate,4),round(b/rate,4)]


def line_key(line):
    return (line.text,line.start,line.end,line.role,line.agent)


def _pinyin_readings(readings):
    """Normalize Mandarin candidates to spaced, toneless native pinyin syllables."""
    from . import chinese
    result=[]
    for reading in readings:
        if not isinstance(reading,str) or not 1<=len(reading)<=80:
            raise ValueError('中文读音仅允许1到80个拼音字符。')
        try:
            groups=chinese.pinyin_groups(reading)
        except ValueError as exc:
            raise ValueError(f'中文读音需为本地词典支持的拼音音节：{exc}') from exc
        result.append(' '.join(group['script'] for group in groups))
    return list(dict.fromkeys(result))


def _reading_family(language):
    return 'zh' if language in ('zh','zh-en') else language


def validate_readings(item,row,language='ja'):
    if not isinstance(row,dict) or not {'id','surface','readings'}<=set(row)<={'id','surface','readings','language'} or row['surface']!=item['surface']:
        raise ValueError('原词或字段发生变化；仅允许 id/surface/readings 和可选 language。')
    family=_reading_family(language)
    if family not in ('ja','zh'):
        raise ValueError('读音语言需为 ja、zh 或 zh-en。')
    claimed=row.get('language') or language
    if claimed not in ('ja','zh','zh-en'):
        raise ValueError('读音语言需为 ja、zh 或 zh-en。')
    if _reading_family(claimed)!=family:
        raise ValueError('读音语言与目标词语言不一致，不接受跨语言读音。')
    readings=row['readings']
    if not isinstance(readings,list) or len(readings)>3:
        raise ValueError('readings 必须为0到3个读音候选。')
    if family=='zh':
        return _pinyin_readings(readings)
    from .singing import kana_phones
    for reading in readings:
        if not isinstance(reading,str) or not 1<=len(reading)<=80 or not re.fullmatch(r'[ァ-ヺー]+',reading):
            raise ValueError('读音仅允许1到80个片假名或长音符。')
        if not kana_phones(reading):
            raise ValueError('读音没有有效音素。')
    return list(dict.fromkeys(readings))


class AudioReadingSession:
    def __init__(self,config,log):
        from .llm_splitter import LlmSession
        self.transport=LlmSession(config,log)
        self.log=log
        self.prompt=PROMPT_PATH.read_text(encoding='utf-8')
        self.records=[]
        self.by_line={}
        self.disabled=False
        self.failures=0

    def prepare(self,lines,samples,rate,background=None):
        from .text import tokenize
        from .singing import pronunciation_overrides
        from . import chinese
        overrides=pronunciation_overrides()
        manual_ja=overrides.get('ja',{})
        manual_zh=overrides.get('zh') or {}
        for index,line in enumerate(lines):
            from .numeric_lyrics import english_numbers
            if line.language=='en':
                continue
            targets={}
            if chinese.is_chinese(line.language):
                language='zh'
                try:
                    units=chinese.chinese_units(line.text)
                except ValueError as exc:
                    record={'line':index+1,'text':line.text,'status':'fallback','candidates':{},'fallback_words':[],'reason':str(exc)}
                    self.records.append(record); self.by_line[line_key(line)]=record
                    line.flags.append('llm_audio_reading_fallback')
                    self.log(f'第 {index+1} 行中文读音候选未建立：{exc}；保留本地拼音。')
                    continue
                phrases = chinese.phrase_readings(units, manual_zh)
                for unit_index, unit in enumerate(units):
                    if unit.spoken in manual_zh or unit_index in phrases:
                        continue
                    if unit.reading or chinese.HAN_RE.search(unit.spoken) or unit.spoken.isdigit():
                        targets.setdefault(unit.spoken,unit.reading)
            else:
                language='ja'
                if english_numbers(line.text):
                    continue
                for word in tokenize(line.text,fine=False):
                    mixed = re.search(r'[A-Za-z]',word.spoken) and re.search(r'[ァ-ヺ]',word.spoken)
                    if (re.search(r'[\u3400-\u9fff々0-9]',word.spoken) or mixed) and word.spoken not in manual_ja:
                        targets.setdefault(word.spoken,word.source_pronunciation or word.reading)
            if not targets:
                continue
            record={'line':index+1,'text':line.text,'status':'fallback','candidates':{},'fallback_words':[]}
            self.records.append(record); self.by_line[line_key(line)]=record
            if self.disabled:
                record['reason']='此前请求失败，停止本任务后续音频请求'
                record['fallback_words']=list(targets)
                line.flags.append('llm_audio_reading_fallback')
                continue
            try:
                audio,bounds=clip_audio(background if line.role=='bg' and background is not None else samples,rate,line.start,line.end)
            except ValueError as exc:
                record['reason']=str(exc)
                record['fallback_words']=list(targets)
                line.flags.append('llm_audio_reading_fallback')
                continue
            record['audio_range']=bounds
            items=[{'id':f'w{i}','surface':surface,'baseline':reading,'language':language}
                   for i,(surface,reading) in enumerate(targets.items())]
            pending={item['id']:item for item in items}
            errors={}
            reason='读音候选校验未通过'
            words_label='中文词' if language=='zh' else '日语词'
            self.log(f'MiMo 音频读音 {index+1}/{len(lines)}：{len(items)} 个{words_label}，发送 {bounds[1]-bounds[0]:.2f} 秒片段…')
            for attempt in range(2):
                payload={'lyrics':line.text,'context':{'before':lines[index-1].text if index else '',
                         'after':lines[index+1].text if index+1<len(lines) else ''},'items':list(pending.values())}
                if attempt:
                    payload['validation_errors']=[{'id':key,'reason':errors.get(key,reason)} for key in pending]
                rows,error=self.transport._chat(payload,repair=bool(attempt),audio=audio,prompt=self.prompt)
                if error:
                    reason=error
                    if error.startswith('HTTP ') or error in ('连接或响应超时','接口连接失败'):
                        self.disabled=True
                        break
                    continue
                for key,item in list(pending.items()):
                    matches=[row for row in rows if isinstance(row,dict) and row.get('id')==key]
                    try:
                        if len(matches)!=1:
                            raise ValueError('词 ID 缺失或重复')
                        readings=validate_readings(item,matches[0],language)
                    except ValueError as exc:
                        errors[key]=str(exc)
                        continue
                    if readings:
                        # Keep the dictionary baseline as an acoustic alternative.
                        baseline=item['baseline']
                        if language=='zh':
                            if baseline and baseline not in readings:
                                readings.append(baseline)
                        elif baseline and re.fullmatch(r'[ァ-ヺー]+',baseline) and baseline not in readings:
                            readings.append(baseline)
                        record['candidates'][item['surface']]=readings
                    else:
                        record['fallback_words'].append(item['surface'])
                    del pending[key]
                if not pending:
                    break
            record['fallback_words'].extend(item['surface'] for item in pending.values())
            if pending:
                record['reason']=reason
                self.failures+=1
                if self.failures>=2:
                    self.disabled=True
            else:
                self.failures=0
            if record['candidates']:
                record['status']='partial' if record['fallback_words'] else 'accepted'
                line.flags.append('llm_audio_reading_candidate')
            if record['fallback_words']:
                line.flags.append('llm_audio_reading_fallback')
            if record['status']=='fallback':
                self.log(f'第 {index+1} 行音频读音未采用：{record.get("reason","模型无法确定读音")}；保留本地词典。')

    def candidates(self,line):
        return self.by_line.get(line_key(line),{}).get('candidates',{})

    def report(self):
        return {'model':self.transport.config.model,'protocol':'chat_completions/input_audio/data-url',
                'requests':self.transport.requests,'repair_requests':self.transport.repair_requests,
                'thinking':self.transport.report()['thinking'],
                'prompt_sha256':hashlib.sha256(self.prompt.encode()).hexdigest(),'lines':self.records,
                'note':'音频辅助读音候选；精细边界来自本地声学模型，需人工听音复核。'}


def current_session():
    return _SESSION.get()


def primary_readings(line):
    if _PRIMARY_DISABLED.get():
        return {}
    session=current_session()
    return {word:readings[0] for word,readings in session.candidates(line).items()} if session else {}


@contextmanager
def without_primary():
    token=_PRIMARY_DISABLED.set(True)
    try:
        yield
    finally:
        _PRIMARY_DISABLED.reset(token)


def clip_candidates(lines,language=None):
    """Candidates for these lines; language='zh'/'ja' keeps only that language family."""
    from . import chinese
    session=current_session()
    result={}
    if session:
        for line in lines:
            if language is not None and chinese.is_chinese(line.language) != (language in ('zh','zh-en')):
                continue
            for word,readings in session.candidates(line).items():
                result[word]=list(dict.fromkeys(result.get(word,[])+readings))
    return result


@contextmanager
def native_candidates(candidates):
    token=_NATIVE.set(candidates)
    try:
        yield
    finally:
        _NATIVE.reset(token)


def current_native_candidates():
    return _NATIVE.get()


@contextmanager
def use_audio_readings(config=None,log=print,output=None):
    session=AudioReadingSession(config,log) if config else None
    token=_SESSION.set(session)
    try:
        yield session
    finally:
        _SESSION.reset(token)
        if session:
            session.transport.close()
            if output is not None:
                output=Path(output); output.mkdir(parents=True,exist_ok=True)
                (output/'audio_readings.json').write_text(json.dumps(session.report(),ensure_ascii=False,indent=2),encoding='utf-8')
