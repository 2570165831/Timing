// Timing upload form: pure request-state rules.
// Loaded as a classic script before app.js and reused verbatim by
// tests/test_web_ui.cjs. Keep this file free of DOM, network, storage and
// timer references so it can run in a bare node:vm context.
//
// The enums and normalization mirror the shared backend plan in
// timing/request_plan.py:
//   ALIGNERS      auto / tifa / ctc / sofa / llm_direct / llm_import
//   AUDIO_MODES   lead / vocals / source
//   LANGUAGES     mixed / ja / en / auto / zh / zh-en (zh is Mandarin)
//   SPLITTERS     syllabify / pyphen / llm
//   PROMPT_PROFILES fine / original (Japanese breakdown for direct-like LLM modes)
// and the audio-route naming mirrors timing/sources.py. Han text only counts
// as Chinese when the song or line language is zh / zh-en; everywhere else
// Han keeps the historical Japanese reading, so Han alone is never guessed
// into Chinese.
'use strict';

const ALIGNERS = ['auto', 'tifa', 'ctc', 'sofa', 'llm_direct', 'llm_import'];
const AUDIO_MODES = ['lead', 'vocals', 'source'];
const LANGUAGES = ['mixed', 'ja', 'en', 'auto', 'zh', 'zh-en'];
const REQUIRED_MODELS = ['ctc', 'tifa', 'singing', 'bs_lead', 'separator'];
const PROMPT_PROFILES = ['fine', 'original'];

const KANA = /[\u3040-\u30ff]/;
const JAPANESE_MARKS = /[々〆〤]/;
const HAN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const LATIN = /[A-Za-zÀ-ÖØ-öø-ÿ]/;

function analyzeLyrics(raw, language, llmDirect = false) {
  const mode = llmDirect ? 'auto' : (LANGUAGES.includes(language) ? language : 'mixed');
  const text = String(raw || '').replace(/^\uFEFF/, '').trim();
  const result = { empty: !text, error: '', kind: 'text', count: 0, anchored: false, partial: false, hasEnglish: false, hasJapanese: false, hasChinese: false, hasBackground: false, maxKnownLineSeconds: 0, language: mode, effectiveLanguages: [] };
  if (!text) { result.error = '未填写歌词：请粘贴或导入歌词。'; return result; }
  const lrc = /^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/; let rows = []; let json = false;
  const first = text.split(/\r?\n/)[0];
  const metadata = /^\[(?:ar|ti|al|by|offset|length):/i.test(first);
  const section = /^\[[A-Za-z\u3040-\u30ff\u3400-\u9fff][A-Za-z\u3040-\u30ff\u3400-\u9fff 0-9_-]*\]$/.test(first);
  if (text.startsWith('{') || (text.startsWith('[') && !lrc.test(first) && !metadata && !section)) {
    try {
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) { result.error = 'JSON 歌词应为数组，每一项是一行歌词。'; return result; }
      json = true; rows = parsed; result.kind = 'json';
    } catch { result.error = 'JSON 歌词格式不完整，请检查括号、引号和逗号。'; return result; }
  }
  if (!json) {
    for (const source of text.split(/\r?\n/)) {
      const line = source.trim();
      if (!line || /^\[(?:ar|ti|al|by|offset|length):/i.test(line)) continue;
      const m = line.match(lrc);
      if (m) {
        if (lrc.test(m[3])) { result.error = '一行包含多个时间标签，请展开为独立歌词行。'; return result; }
        if (m[3].trim()) rows.push({ text: m[3].trim(), start: Number(m[1]) * 60 + Number(m[2]), role: 'main' });
      } else if (!(line.startsWith('[') && line.endsWith(']'))) rows.push({ text: line, role: 'main' });
    }
    if (rows.some(row => row.start != null)) result.kind = 'lrc';
  }
  result.count = rows.length;
  if (!rows.length) { result.error = '没有可对齐的歌词，请填写歌词。'; return result; }
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (!row || typeof row !== 'object' || typeof row.text !== 'string' || !/[\p{L}\p{N}]/u.test(row.text)) { result.error = `第 ${i + 1} 行没有可演唱文字。`; return result; }
    if (!['main', 'bg'].includes(row.role === undefined ? 'main' : row.role) || !['v1', 'v2'].includes(row.agent === undefined ? 'v1' : row.agent) || !LANGUAGES.includes(row.language === undefined ? 'auto' : row.language)) { result.error = `第 ${i + 1} 行的角色、歌手或语言标记无效。`; return result; }
    if (['translation', 'roman'].some(key => key in row && typeof row[key] !== 'string')) { result.error = `第 ${i + 1} 行的 translation 和 roman 必须是文字。`; return result; }
    const start = row.start != null, end = row.end != null;
    if (json && (start !== end || (start && (!Number.isFinite(row.start) || !Number.isFinite(row.end) || row.start < 0 || row.end <= row.start)))) { result.error = `第 ${i + 1} 行需提供有效的 start / end 起止时间。`; return result; }
    if (json && start) result.maxKnownLineSeconds = Math.max(result.maxKnownLineSeconds, row.end - row.start);
    if (!json && start) { const next = rows.slice(i + 1).find(item => item.start != null); if (next && next.start <= row.start) { result.error = `第 ${i + 1} 行之后的 LRC 时间必须递增。`; return result; } if (next) result.maxKnownLineSeconds = Math.max(result.maxKnownLineSeconds, next.start - row.start); }
    if ((row.role ?? 'main') === 'bg') { result.hasBackground = true; if (!start || !Number.isInteger(row.parent) || row.parent < 0 || row.parent >= rows.length || (rows[row.parent]?.role ?? 'main') !== 'main') { result.error = `第 ${i + 1} 行背景歌词需带起止时间，并通过 parent 指向主唱行。`; return result; } }
  }
  const mains = rows.filter(row => (row.role ?? 'main') === 'main');
  if (!mains.length) { result.error = '至少需要一行主歌词。'; return result; }
  let previousMainStart = -1;
  for (const row of mains) { if (row.start != null) { if (row.start < previousMainStart) { result.error = '主唱歌词请按开始时间排序；重叠演唱可以保留。'; return result; } previousMainStart = row.start; } }
  const starts = mains.filter(row => row.start != null).length;
  result.partial = starts > 0 && starts < mains.length;
  if (result.partial) result.error = '逐句时间不完整：请给每句补全时间，或统一使用纯文本歌词。';
  result.anchored = rows.every(row => row.start != null && (json ? row.end != null : true));
  for (const row of rows) {
    if (llmDirect) { result.effectiveLanguages.push('auto'); continue; }
    const declared = row.language === undefined ? 'auto' : row.language;
    const effective = declared === 'auto' ? (mode === 'auto' ? 'mixed' : mode) : declared;
    result.effectiveLanguages.push(effective);
    const singing = String(row.text).normalize('NFKC');
    const chinese = effective === 'zh' || effective === 'zh-en';
    if (KANA.test(singing) || JAPANESE_MARKS.test(singing)) result.hasJapanese = true;
    if (HAN.test(singing)) { if (chinese) result.hasChinese = true; else result.hasJapanese = true; }
    if (LATIN.test(singing)) result.hasEnglish = true;
    if (effective === 'ja') result.hasJapanese = true;
    if (effective === 'en') result.hasEnglish = true;
    if (chinese) result.hasChinese = true;
  }
  return result;
}

function resolveOptions(input) {
  const lyrics = input.lyrics || {};
  const requestedLanguage = LANGUAGES.includes(input.language) ? input.language : 'mixed';
  const aligner = ALIGNERS.includes(input.aligner) ? input.aligner : 'auto';
  const engine = aligner === 'auto' ? (lyrics.anchored ? 'tifa' : 'sofa') : aligner;
  const direct = engine === 'llm_direct';
  const directLike = direct || engine === 'llm_import';
  const showImport = engine === 'llm_import';
  const language = directLike ? 'auto' : requestedLanguage;
  let error = lyrics.error || (lyrics.empty ? '未填写歌词：请粘贴或导入歌词。' : '') || (lyrics.partial ? '逐句时间不完整：请给每句补全时间，或统一使用纯文本歌词。' : '');
  if (['tifa', 'ctc', 'llm_direct', 'llm_import'].includes(engine) && !lyrics.anchored && !error) error = '当前方案需要逐句时间：请补全每句时间，或改用“自动 / SOFA”。';
  if (engine === 'ctc' && lyrics.maxKnownLineSeconds > 30 && !error) error = 'MMS 每句最长 30 秒，请拆分逐句范围。';
  const valid = !error;
  const showEnglish = !directLike && Boolean(lyrics.hasEnglish);
  const syllables = showEnglish && Boolean(input.syllables);
  const allowLlmSplitter = !['ctc', 'llm_direct'].includes(engine);
  const requested = typeof input.splitter === 'string' ? input.splitter : 'syllabify';
  const splitter = syllables && !(requested === 'llm' && !allowLlmSplitter) ? requested : 'syllabify';
  const showSplitter = showEnglish && syllables;
  const showAudioReading = !directLike && engine === 'tifa' && Boolean(lyrics.anchored) && (Boolean(lyrics.hasJapanese) || Boolean(lyrics.hasChinese));
  const showAudioSplit = !directLike && engine === 'tifa' && Boolean(lyrics.anchored) && syllables && splitter === 'llm';
  const audioReading = valid && showAudioReading && Boolean(input.audioReading);
  const audioSplit = valid && showAudioSplit && Boolean(input.audioSplit);
  const llm = valid && (direct || (syllables && splitter === 'llm') || audioReading);
  const promptProfile = PROMPT_PROFILES.includes(input.promptProfile) ? input.promptProfile : 'fine';
  const audioMode = AUDIO_MODES.includes(input.audioMode) ? input.audioMode : 'lead';
  const hasVocals = Boolean(input.hasVocals);
  const source = audioMode === 'lead' ? 'separated_vocals' : audioMode === 'vocals' ? (hasVocals ? 'provided_vocals' : 'separated_vocals') : (hasVocals ? 'provided_vocals' : 'original');
  return { aligner, engine, language, showLanguage: !directLike, valid, error, showEnglish, showSplitter, allowLlmSplitter, showAudioReading, showAudioSplit, syllables, splitter, audioReading, audioSplit, llm, sendsAudio: llm && (direct || audioReading || audioSplit), showImport, showPromptProfile: directLike, promptReady: showImport && valid, promptProfile, audioMode, source, models: [...REQUIRED_MODELS], hasBackground: Boolean(lyrics.hasBackground) };
}

// Keep submission policy beside option resolution so direct mode cannot send a
// stale song-language selection or helper flags from hidden controls.
function submissionFields(plan) {
  const fields = {
    language: plan.language,
    aligner: plan.aligner,
    audio_mode: plan.audioMode,
    syllables: String(Boolean(plan.syllables)),
    english_splitter: plan.splitter,
    llm_audio_reading: String(Boolean(plan.audioReading)),
    llm_audio_split: String(Boolean(plan.audioSplit)),
  };
  if (['llm_direct', 'llm_import'].includes(plan.engine)) {
    fields.llm_prompt_profile = PROMPT_PROFILES.includes(plan.promptProfile) ? plan.promptProfile : 'fine';
  }
  return fields;
}

// Composite song-language name for the lyrics status line, e.g. 中英混唱.
function describeLanguages(plan) {
  if (plan.language === 'auto' && plan.effectiveLanguages?.length && plan.effectiveLanguages.every(language => language === 'auto')) return '待 LLM 识别语言';
  const code = (plan.hasChinese ? 'z' : '') + (plan.hasJapanese ? 'j' : '') + (plan.hasEnglish ? 'e' : '');
  return { z: '中文', j: '日语', e: '英语', zj: '中日混唱', ze: '中英混唱', je: '日英混唱', zje: '中日英混唱' }[code] || '';
}

// llm_audio_reading is one shared flag; the label follows the lyrics language.
function audioReadingLabel(plan) {
  if (plan.hasChinese && plan.hasJapanese) return 'LLM 听音校对中文／日语读音';
  return plan.hasChinese ? 'LLM 听音校对中文读音' : 'LLM 听音校对日语读音';
}
