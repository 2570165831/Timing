// Timing upload form: DOM wiring, uploads, polling and result links.
// Pure request-state rules live in ui-logic.js, which loads first.
'use strict';
const token = (document.querySelector('meta[name="timing-token"]') || {}).content || '';
const $ = id => document.getElementById(id);
const storage = { get(key) { try { return localStorage.getItem('timing-' + key); } catch { return null; } }, set(key, value) { try { localStorage.setItem('timing-' + key, String(value)); } catch {} }, remove(key) { try { localStorage.removeItem('timing-' + key); } catch {} } };
const show = (id, visible) => { $(id).classList.toggle('hidden', !visible); };
const ICON_EYE = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiM0ZTU5NjciIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTcuOTQgMTcuOTRBMTAuMDcgMTAuMDcgMCAwIDEgMTIgMjBjLTcgMC0xMS04LTExLThhMTguNDUgMTguNDUgMCAwIDEgNS4wNi01Ljk0TTkuOSA0LjI0QTkuMTIgOS4xMiAwIDAgMSAxMiA0YzcgMCAxMSA4IDExIDhhMTguNSAxOC41IDAgMCAxLTIuMTYgMy4xOW0tNi43Mi0xLjA3YTMgMyAwIDEgMS00LjI0LTQuMjQiLz48bGluZSB4MT0iMSIgeTE9IjEiIHgyPSIyMyIgeTI9IjIzIi8+PC9zdmc+';
const ICON_EYE_OFF = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiM0ZTU5NjciIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMSAxMnM0LTggMTEtOCAxMSA4IDExIDgtNCA4LTExIDgtMTEtOC0xMS04eiIvPjxjaXJjbGUgY3g9IjEyIiBjeT0iMTIiIHI9IjMiLz48L3N2Zz4=';
let current, parsed, busy = false, testBusy = false, importing = false, service = null, serviceError = '', timer = null, active = null, lyricsFileName = '', lyricsImportNote = '', fileError = '', audioError = '', pollFailures = 0;
let importSequence = 0, ttmlTimer = null, ttmlConverting = false;
const MAX_LLM_RESULT_BYTES = 16 * 1024 ** 2;
let promptText = '', promptRequestId = '', promptLines = 0, promptBusy = false, promptMessage = '', promptWarning = false;
let checkBusy = false, checkMessage = '', checkWarning = false, resultImporting = false;
let lyricsRevision = 0, resultRevision = 0, promptSequence = 0, checkSequence = 0, resultFileSequence = 0;
const persisted = ['llm-base-url', 'llm-model', 'llm-api-key-header', 'llm-api-key-format', 'llm-thinking-mode', 'llm-thinking-format', 'llm-reasoning-effort'];
for (const name of persisted) { const value = storage.get(name); if (value !== null && ($(name).tagName !== 'SELECT' || [...$(name).options].some(option => option.value === value))) $(name).value = value; }
for (const name of ['syllables', 'llm-audio-reading', 'llm-audio-split', 'llm-json-mode']) { const saved = storage.get(name); if (saved !== null) $(name).checked = saved === 'true'; }
let preferredSplitter = storage.get('english-splitter') || 'syllabify';
if (!['syllabify', 'pyphen', 'llm'].includes(preferredSplitter)) preferredSplitter = 'syllabify';
let preferredPromptProfile = storage.get('llm-prompt-profile') || 'fine';
if (!['fine', 'original'].includes(preferredPromptProfile)) preferredPromptProfile = 'fine';
$('llm-prompt-profile').value = preferredPromptProfile;
const allowedAudio = new Set(['wav', 'mp3', 'flac', 'm4a', 'ogg', 'opus', 'aac', 'wma', 'mp4', 'aiff']);
function audioFileError(file) { if (!file) return ''; if (!file.size) return '音频文件为空，请重新选择。'; if (file.size > 1024 ** 3) return '音频最大 1 GB，请选择较小的文件。'; if (!allowedAudio.has(file.name.split('.').pop().toLowerCase())) return '不支持此音频格式，请选择 WAV、FLAC、MP3 或 M4A 等音频。'; return ''; }
function sizeText(bytes) { return bytes >= 1024 ** 2 ? (bytes / 1024 ** 2).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB'; }
async function requestTtmlConversion(xml) {
  const data = new FormData(); data.set('lyrics', xml);
  const response = await fetch('/api/lyrics/ttml', { method: 'POST', headers: { 'X-Timing-Token': token }, body: data });
  const result = await responseData(response, '无法转换 TTML 歌词');
  if (!response.ok) throw new Error(messageFrom(result, '无法转换 TTML 歌词。'));
  const lines = Number(result.lines);
  if (typeof result.lyrics !== 'string' || !result.lyrics.trim()) throw new Error('TTML 转换结果没有返回逐句歌词。');
  if (!Number.isInteger(lines) || lines < 1) throw new Error('TTML 转换结果缺少有效行数。');
  return { lyrics: result.lyrics, lines };
}
function llmResultText() { return $('llm-result').value.trim(); }
function invalidateCheck(reason) { ++checkSequence; checkBusy = false; checkMessage = reason; checkWarning = true; }
function invalidateImportBinding(reason) {
  if (!current?.showImport && !promptText && !promptBusy && !checkBusy) return;
  ++promptSequence; ++checkSequence; promptText = ''; promptRequestId = ''; promptLines = 0; promptBusy = false; checkBusy = false;
  promptMessage = reason; promptWarning = true; checkMessage = reason; checkWarning = true;
}
function renderImportPanel() {
  show('llm-import-panel', Boolean(current?.showImport));
  if (!current?.showImport) return;
  const audio = $('audio').files[0], result = llmResultText();
  $('llm-import-audio-name').textContent = audio ? '当前音频：' + audio.name : '尚未选择音频；可以先准备提示词，最终生成时必须选择完整歌曲。';
  $('generate-prompt').disabled = busy || promptBusy || importing || ttmlConverting || !current.promptReady;
  $('copy-prompt').classList.toggle('hidden', !promptText); $('download-prompt').classList.toggle('hidden', !promptText);
  $('copy-prompt').disabled = !promptText; $('download-prompt').disabled = !promptText;
  $('llm-prompt').textContent = promptText; $('llm-prompt-box').classList.toggle('hidden', !promptText);
  $('llm-prompt-status').textContent = promptMessage || (current.promptReady ? '生成提示词后，与完整音频一起发送给你的网页 LLM。' : '需要带逐句时间的完整歌词后才能生成提示词。');
  $('llm-prompt-status').classList.toggle('warning', promptWarning || !current.promptReady);
  $('import-llm-result').disabled = busy || resultImporting;
  $('validate-llm-result').disabled = busy || checkBusy || ttmlConverting || !current.promptReady || !result;
  $('llm-result-status').textContent = checkMessage || (!result ? '把返回的 JSON 粘贴到上方，或导入 .json 文件。提交时以当前文本为准。' : '当前文本会随提交上传；可以先用“检查结果”核对歌词绑定和完整度。');
  $('llm-result-status').classList.toggle('warning', checkWarning);
}
function readState() { const hasVocals = Boolean($('vocals').files.length); const language = $('language').value, aligner = $('aligner').value, directLike = ['llm_direct', 'llm_import'].includes(aligner); parsed = analyzeLyrics($('lyrics').value, directLike ? 'auto' : language, directLike); return resolveOptions({ lyrics: parsed, language, aligner, syllables: $('syllables').checked, splitter: preferredSplitter, audioReading: $('llm-audio-reading').checked, audioSplit: $('llm-audio-split').checked, promptProfile: $('llm-prompt-profile').value, hasVocals, audioMode: $('audio-mode').value }); }
function updateUI() {
  current = readState();
  const audio = $('audio').files[0], vocals = $('vocals').files[0];
  $('task-inputs').disabled = busy;
  for (const option of $('aligner').options) option.disabled = ['tifa', 'ctc', 'llm_direct', 'llm_import'].includes(option.value) && !parsed.anchored;
  show('language-settings', current.showLanguage);
  show('prompt-profile-settings', current.showPromptProfile); $('llm-prompt-profile').disabled = !current.showPromptProfile;
  show('english-settings', current.showEnglish); $('syllables').disabled = !current.showEnglish;
  show('splitter-settings', current.showSplitter); $('english-splitter').disabled = !current.showSplitter;
  $('english-splitter').querySelector('option[value="llm"]').disabled = !current.allowLlmSplitter; $('english-splitter').value = current.splitter;
  show('audio-reading-field', current.showAudioReading); $('llm-audio-reading').disabled = !current.showAudioReading;
  $('audio-reading-text').textContent = audioReadingLabel(parsed);
  show('audio-split-field', current.showAudioSplit); $('llm-audio-split').disabled = !current.showAudioSplit;
  show('audio-helpers', current.showAudioReading || current.showAudioSplit);
  show('llm-settings', current.llm); $('llm-settings').disabled = busy || !current.llm;
  $('llm-base-url').required = current.llm; $('llm-model').required = current.llm;
  $('audio-name').textContent = audio ? audio.name : '选择要对齐的歌曲';
  $('audio-meta').textContent = audio ? audio.name.split('.').pop().toUpperCase() + ' · ' + sizeText(audio.size) : 'WAV、FLAC、MP3、M4A 等 · 最大 1 GB';
  $('choose-audio').textContent = audio ? '更换文件' : '选择文件';
  show('vocal-note', Boolean(vocals)); $('vocal-name').textContent = vocals ? '已导入：' + vocals.name + ' · 需与原曲同起点、同时长' : '';
  const audioOptions = $('audio-mode').options;
  audioOptions[0].textContent = vocals ? '从导入人声提取主唱' : '只保留主唱（去伴奏、尽量去和声）';
  audioOptions[1].textContent = vocals ? '直接使用导入的人声' : '保留全部人声（去伴奏、保留和声）';
  audioOptions[2].textContent = '直接使用原曲（不处理）';
  audioOptions[2].disabled = Boolean(vocals); audioOptions[2].hidden = Boolean(vocals);
  const route = vocals ? (current.audioMode === 'lead' ? '从导入的人声提取主唱，去掉伴奏，并尽量去掉和声。' : '直接使用导入的人声，不处理。')
    : current.audioMode === 'lead' ? '从原曲提取主唱：去掉伴奏，并尽量去掉和声。'
    : current.audioMode === 'vocals' ? '分离完整人声：去掉伴奏，保留主唱和和声。'
    : '直接使用原曲，不处理。';
  $('audio-route').textContent = route;
  const bgNote = parsed.hasBackground && current.audioMode === 'lead';
  show('bg-note', bgNote);
  $('bg-note').textContent = bgNote ? (vocals ? '背景歌词使用导入的人声（含和声）对齐。' : '背景歌词使用分离出的完整人声（含和声）对齐。') : '';
  $('audio-description').textContent = audio ? route : '选择歌曲文件，并按需要指定人声处理方式。';
  const lang = describeLanguages(parsed);
  $('lyrics-status').textContent = ttmlConverting ? '正在转换 TTML 歌词…' : importing ? '正在导入歌词…' : fileError || (parsed.empty ? '请粘贴歌词，或导入歌词文件。' : parsed.error) || (parsed.anchored ? '已识别逐句时间' : '已识别纯文本') + ' · ' + parsed.count + ' 行' + (lang ? ' · ' + lang : '');
  $('lyrics-status').title = lyricsImportNote;
  $('lyrics-status').classList.toggle('warning', Boolean(fileError || (!ttmlConverting && parsed.error && !parsed.empty)));
  show('lyrics-status-icon', !ttmlConverting && !fileError && !parsed.error);
  show('clear-lyrics', Boolean($('lyrics').value || fileError || ttmlConverting));
  $('clear-lyrics').textContent = !$('lyrics').value && fileError ? '清除导入错误' : '清空歌词';
  $('lyrics-file-note').textContent = lyricsImportNote;
  const visualLines = $('lyrics').value.split('\n').length;
  $('line-numbers').textContent = Array.from({ length: Math.min(10000, visualLines) }, (_, i) => i + 1).join('\n');
  $('line-numbers').scrollTop = $('lyrics').scrollTop;
  $('mode-description').textContent = current.engine === 'llm_direct' ? '整首歌只请求一次，由接口识别语言、读音和分段。' : current.engine === 'llm_import' ? '把提示词和音频发给网页 LLM，再导入它返回的语言、读音和分词。' : '根据歌词是否带逐句时间选择处理方式。';
  const auto = $('aligner').value === 'auto';
  $('mode-hint').textContent = parsed.empty ? '填写歌词后，会按歌词格式选择对齐方案。'
    : current.engine === 'llm_direct' ? '时间由本机 TIFA 计算；个别行无法识别或结果无效时标记为手工补时，不会自动重试。'
    : current.engine === 'llm_import' ? '本应用不调用网页 LLM，也不需要接口 Key；导入结果通过本地检查后交给 TIFA 计时。'
    : current.engine === 'tifa' ? (auto ? '歌词带逐句时间，用 TIFA 细化每一句。' : '用 TIFA 细化每一句的时间。') + ' 个别句子可能保留整句时间，需手工补充。'
    : current.engine === 'ctc' ? '用 MMS 按句对齐；不支持听音辅助。'
    : current.engine === 'sofa' ? (auto ? '纯文本歌词，使用 SOFA 对齐。' : '使用 SOFA 对齐。') + ' 下载后请试听校对。'
    : '';
  $('splitter-hint').textContent = !current.syllables ? '英文单词按整词给时间。'
    : current.engine === 'ctc' ? (preferredSplitter === 'llm' ? 'MMS 使用本地拆分；切回其他方案后恢复接口选择。' : 'MMS 使用本地拆分，不调用接口。')
    : current.splitter === 'llm' ? '接口只给出拆分建议，时间仍来自本机计算。'
    : current.splitter === 'pyphen' ? '按拼写规则拆分，供对比；时间来自本机计算。'
    : '按发音拆分；拆不准时保留整词。';
  show('alignment-error', Boolean(current.error && current.error !== parsed.error)); $('alignment-error').textContent = current.error;
  audioError = audioFileError(audio) || audioFileError(vocals); show('audio-error', Boolean(audioError)); $('audio-error').textContent = audioError;
  $('run-summary').textContent = ttmlConverting ? '正在转换 TTML 歌词；完成后才能提交' : !audio ? '选择歌曲和歌词，生成 TTML' : !current.valid ? '填写或修正歌词后即可提交' : '将用 ' + audio.name + ' 和 ' + parsed.count + ' 行歌词生成 TTML';
  $('run-disclosure').textContent = current.engine === 'llm_import'
    ? '只生成本地提示词并接收你带回的 JSON；不发送音频、歌词或凭据到网页 LLM。'
    : current.engine === 'llm_direct'
    ? '整首歌和全部歌词只发送一次' + (current.hasBackground ? '，并附带含主唱与背景的完整人声' : '') + '；下载后仍需试听校对。'
    : current.sendsAudio ? '音频片段和歌词会发送到所选接口；下载后先试听校对。' : current.llm ? '只发送英文词和所在句，不发送音频；下载后先试听校对。' : '音频和歌词都在本机处理；下载后先试听校对。';
  $('submit-label').textContent = busy ? '正在处理…' : '生成 TTML';
  updateThinking(); renderImportPanel(); renderService();
}
function updateThinking() {
  const mode = $('llm-thinking-mode').value, format = $('llm-thinking-format').value, effort = $('llm-reasoning-effort'), inactive = !current?.llm || busy;
  show('thinking-format-field', mode !== 'default'); show('thinking-effort-field', mode === 'on');
  $('llm-thinking-format').disabled = inactive || mode === 'default';
  effort.disabled = inactive || mode !== 'on'; effort.required = !inactive && mode === 'on' && format === 'reasoning_effort';
  effort.setCustomValidity(!inactive && mode === 'on' && effort.value.toLowerCase() === 'none' ? '关闭思考请使用“关闭思考”模式。' : '');
  let params = {};
  if (mode !== 'default') { if (format === 'reasoning_effort') params.reasoning_effort = mode === 'on' ? effort.value : 'none'; else { if (format === 'thinking') params.thinking = { type: mode === 'on' ? 'enabled' : 'disabled' }; else params.enable_thinking = mode === 'on'; if (mode === 'on' && effort.value) params.reasoning_effort = effort.value; } }
  $('llm-thinking-preview').textContent = '将发送：' + JSON.stringify(params);
  $('llm-thinking-hint').textContent = mode === 'default' ? '跟随平台默认，不发送思考参数。' : '格式与强度需要接口支持；开关格式可留空强度。测试通过不能证明平台执行了思考。';
  const custom = $('llm-api-key-header').value.toLowerCase() !== 'authorization' || $('llm-api-key-format').value !== 'auto';
  $('advanced-summary').textContent = [custom ? '自定义鉴权' : '标准鉴权', $('llm-json-mode').checked ? 'JSON 模式' : null, mode === 'on' ? '开启思考' : mode === 'off' ? '关闭思考' : '思考跟随平台'].filter(Boolean).join(' · ');
  $('llm-test').disabled = inactive || testBusy;
}
function renderService() {
  const modelNames = { ctc: 'MMS', tifa: 'TIFA', singing: 'SOFA', bs_lead: '主唱提取', separator: '人声分离' };
  const missing = service ? current.models.filter(name => !service.models?.[name]) : [];
  $('model-status').textContent = serviceError || (!service ? '正在检查本地服务…' : missing.length ? '缺少 ' + missing.map(name => modelNames[name] || name).join('、') + '。请运行 setup.ps1 安装全部 5 个模型，完成后点“重新检查”。' : '本地模型已就绪' + (service.version ? ' · v' + service.version : '') + (service.queued > 0 ? ' · ' + service.queued + ' 个任务处理中' : ''));
  $('model-status').classList.toggle('warning', Boolean(serviceError || missing.length));
  show('retry-models', Boolean(serviceError || missing.length));
  const importMissing = current.showImport && (!llmResultText() || llmResultText().length > MAX_LLM_RESULT_BYTES);
  $('submit').disabled = busy || importing || ttmlConverting || (current.showImport && resultImporting) || !service || Boolean(serviceError) || missing.length > 0 || !current.valid || !$('audio').files.length || Boolean(fileError || audioError) || importMissing;
}
async function checkModels() {
  serviceError = ''; service = null; renderService();
  try { const response = await fetch('/api/status'); if (!response.ok) throw new Error(); const status = await response.json(); if (status.app !== 'Timing' || status.api_version !== 7) throw new Error('version'); service = status; }
  catch (error) { serviceError = error.message === 'version' ? '服务版本不匹配，请重新启动应用后刷新。' : '连接本机服务失败，请运行 启动.cmd 后重试。'; }
  renderService();
}
function llmData(data) {
  for (const name of persisted.concat('llm-api-key')) if (!$(name).disabled) data.set($(name).name, $(name).value);
  data.set('llm_json_mode', String($('llm-json-mode').checked));
}
function jobData() {
  const data = new FormData();
  data.set('audio', $('audio').files[0]);
  data.set('lyrics', $('lyrics').value);
  for (const [name, value] of Object.entries(submissionFields(current))) data.set(name, value);
  if ($('vocals').files[0]) data.set('vocals', $('vocals').files[0]);
  if (current.showImport) {
    const text = $('llm-result').value;
    if (!text.trim()) throw new Error('请先粘贴或导入网页 LLM 返回的 JSON。');
    if (new TextEncoder().encode(text).byteLength > MAX_LLM_RESULT_BYTES) throw new Error('网页 LLM 结果最大 16 MB，请精简或重新获取结果。');
    data.set('llm_result_file', new Blob([text], { type: 'application/json' }), 'llm-result.json');
  }
  if (current.llm) llmData(data);
  return data;
}
function validateLlm() { if (!current.llm) return true; for (const name of persisted) { if (!$(name).checkValidity()) { if ($(name).closest('details')) $('api-advanced').open = true; $(name).reportValidity(); return false; } } return true; }
function messageFrom(result, fallback) { if (typeof result.detail === 'string') return result.detail; if (Array.isArray(result.detail)) return result.detail.map(item => item.msg).join('\n'); return result.message || fallback; }
async function responseData(response, fallback) { try { return await response.json(); } catch { throw new Error(fallback + '（HTTP ' + response.status + '），请稍后重试。'); } }
async function generatePrompt() {
  updateUI();
  if ($('generate-prompt').disabled) return;
  const sequence = ++promptSequence, revision = lyricsRevision, profile = current.promptProfile, lyrics = $('lyrics').value, audio = $('audio').files[0];
  ++checkSequence; checkBusy = false; promptBusy = true; promptText = ''; promptRequestId = ''; promptLines = 0;
  promptMessage = '正在生成本地提示词…'; promptWarning = false; checkMessage = ''; checkWarning = false; renderImportPanel();
  const data = new FormData(); data.set('lyrics', lyrics); data.set('llm_prompt_profile', profile); if (audio) data.set('audio_name', audio.name);
  try {
    const response = await fetch('/api/llm/prompt', { method: 'POST', headers: { 'X-Timing-Token': token }, body: data });
    const result = await responseData(response, '无法生成提示词');
    if (sequence !== promptSequence || revision !== lyricsRevision || profile !== $('llm-prompt-profile').value) return;
    if (!response.ok) throw new Error(messageFrom(result, '无法生成提示词'));
    if (typeof result.prompt !== 'string' || !result.prompt.trim()) throw new Error('服务没有返回提示词，请检查本机版本后重试。');
    promptText = result.prompt; promptRequestId = typeof result.request_id === 'string' ? result.request_id : '';
    promptLines = Number.isFinite(Number(result.lines)) ? Number(result.lines) : 0;
    promptMessage = '提示词已生成' + (promptLines ? ' · ' + promptLines + ' 行' : '') + '。请与当前完整音频一起发送给网页 LLM。'; promptWarning = false;
    checkMessage = '提示词已就绪；粘贴或导入返回 JSON 后可以检查。'; checkWarning = false;
  } catch (error) {
    if (sequence === promptSequence && revision === lyricsRevision && profile === $('llm-prompt-profile').value) { promptMessage = error.message || '无法生成提示词。'; promptWarning = true; }
  } finally {
    if (sequence === promptSequence) { promptBusy = false; renderImportPanel(); }
  }
}
async function copyPrompt() {
  if (!promptText) return;
  let copied = false;
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(promptText); copied = true; } } catch {}
  if (!copied) {
    const selection = window.getSelection(), range = document.createRange();
    range.selectNodeContents($('llm-prompt')); selection.removeAllRanges(); selection.addRange(range);
    try { copied = document.execCommand('copy'); } catch {}
  }
  promptMessage = copied ? '提示词已复制。请连同上方完整音频一起发送。' : '浏览器未允许自动复制；已在提示词框内选中文字，请按 Ctrl+C。';
  promptWarning = !copied; renderImportPanel();
}
function downloadPrompt() {
  if (!promptText) return;
  const url = URL.createObjectURL(new Blob([promptText], { type: 'text/plain;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'timing-web-llm-prompt.txt'; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
async function checkImportResult() {
  updateUI();
  if ($('validate-llm-result').disabled) return;
  const sequence = ++checkSequence, revision = lyricsRevision, resultRevisionAtStart = resultRevision, text = $('llm-result').value;
  checkBusy = true; checkMessage = '正在核对结果与当前歌词的绑定…'; checkWarning = false; renderImportPanel();
  const data = new FormData(); data.set('lyrics', $('lyrics').value); data.set('llm_result_file', new Blob([text], { type: 'application/json' }), 'llm-result.json');
  try {
    const response = await fetch('/api/llm/import/validate', { method: 'POST', headers: { 'X-Timing-Token': token }, body: data });
    const result = await responseData(response, '无法检查导入结果');
    if (sequence !== checkSequence || revision !== lyricsRevision || resultRevisionAtStart !== resultRevision) return;
    if (!response.ok || result.ok !== true) throw new Error(messageFrom(result, '导入结果未通过检查。'));
    const accepted = Array.isArray(result.accepted) ? result.accepted.length : Number(result.accepted) || 0;
    const manual = Array.isArray(result.manual_lines) ? result.manual_lines.length : Number(result.manual_lines) || 0;
    checkMessage = '检查通过 · ' + (Number(result.lines) || 0) + ' 行 · 可用 ' + accepted + ' 行' + (manual ? ' · 待手工 ' + manual + ' 行' : '') + '。'; checkWarning = manual > 0;
  } catch (error) {
    if (sequence === checkSequence && revision === lyricsRevision && resultRevisionAtStart === resultRevision) { checkMessage = error.message || '导入结果未通过检查。'; checkWarning = true; }
  } finally {
    if (sequence === checkSequence) { checkBusy = false; renderImportPanel(); }
  }
}
function link(label, url, primary = false) { const element = document.createElement('a'); element.textContent = label; element.href = url; element.className = 'download' + (primary ? ' primary' : ''); element.download = ''; $('links').append(element); }
function stopPolling() { clearTimeout(timer); timer = null; active = null; busy = false; storage.remove('job'); updateUI(); }
function acceptTtmlConversion(converted, note) {
  $('lyrics').value = converted.lyrics;
  ++lyricsRevision; fileError = ''; lyricsImportNote = note;
  invalidateImportBinding('TTML 歌词已转换，请重新生成提示词并重新检查结果。');
}
function schedulePastedTtmlConversion(sequence, revision, xml) {
  ttmlConverting = true;
  ttmlTimer = setTimeout(async () => {
    ttmlTimer = null;
    try {
      const converted = await requestTtmlConversion(xml);
      if (sequence !== importSequence || revision !== lyricsRevision) return;
      acceptTtmlConversion(converted, '已将粘贴的 TTML 转换为逐句歌词（' + converted.lines + ' 行）；提交以当前文本框为准。');
    } catch (error) {
      if (sequence === importSequence && revision === lyricsRevision) fileError = error.message || '无法转换 TTML 歌词。';
    } finally {
      if (sequence === importSequence) { ttmlConverting = false; updateUI(); }
    }
  }, 300);
}
function completeJob(id, job) {
  const result = job.result || {}; stopPolling(); $('links').replaceChildren(); $('error').textContent = '';
  if (job.state === 'complete') {
    const sentence = result.sentence_only || (result.lines > 0 && result.manual_timing_lines?.length === result.lines);
    $('state').textContent = sentence ? '仅生成整句时间 · 需手工细分' : result.review_lines ? 'TTML 已生成 · 请试听校对' : 'TTML 已生成';
    link(sentence ? '下载整句时间 TTML' : '下载 TTML', '/api/jobs/' + id + '/files/ttml', true);
    link('对齐记录 JSON', '/api/jobs/' + id + '/files/report');
    $('result-message').textContent = result.review_message || '下载后先试听校对，少数字可能需要手工补时间。';
  } else if (job.state === 'draft') {
    $('state').textContent = '旧版歌词草稿';
    link('下载旧版歌词草稿', '/api/jobs/' + id + '/files/draft', true);
    $('result-message').textContent = '这是旧版本生成的草稿；自动生成功能已移除，可下载后手工使用，或直接重新提交当前歌词。';
  } else {
    $('state').textContent = '处理失败'; $('result-message').textContent = '可以调整输入或设置后重新提交。'; $('error').textContent = job.error || '处理失败，请查看日志。'; $('log-details').open = true;
  }
  if (result.lead_separated) {
    link('下载主唱 WAV', '/api/jobs/' + id + '/files/lead');
    link(result.residual_includes_accompaniment ? '下载伴奏及和声 WAV' : '下载和声／剩余人声 WAV', '/api/jobs/' + id + '/files/' + (result.residual_includes_accompaniment ? 'residual' : 'backing'));
  } else if (['separated_vocals', 'provided_vocals'].includes(result.source)) {
    link('下载对齐用人声 WAV', '/api/jobs/' + id + '/files/vocals');
  }
}
async function poll(id) {
  try {
    const response = await fetch('/api/jobs/' + id); if (id !== active) return;
    if (response.status === 404 || response.status === 410) { stopPolling(); $('links').replaceChildren(); $('state').textContent = '未找到任务'; $('error').textContent = '保存的任务已不存在。请重新选择音频并提交。'; return; }
    const job = await responseData(response, '暂时无法读取任务进度');
    if (!response.ok) throw new Error(messageFrom(job, '暂时无法读取进度'));
    if (id !== active) return;
    pollFailures = 0; $('error').textContent = '';
    $('state').textContent = ({ uploading: '正在导入…', queued: '已排队', running: '正在处理…' })[job.state] || '正在处理…';
    $('job-info').textContent = (job.name || '') + ' · ' + id.slice(0, 8);
    $('log').textContent = (job.log || []).join('\n'); $('log').scrollTop = $('log').scrollHeight;
    if (['complete', 'failed', 'draft'].includes(job.state)) { completeJob(id, job); return; }
    timer = setTimeout(() => poll(id), 1800);
  } catch (error) {
    if (id !== active) return;
    pollFailures++;
    $('error').textContent = (error.message || '进度连接中断') + '，正在重试。任务编号：' + id + '；请保持本机服务运行。';
    timer = setTimeout(() => poll(id), Math.min(15000, 2000 + pollFailures * 2000));
  }
}
$('form').addEventListener('submit', async event => {
  event.preventDefault(); updateUI(); if ($('submit').disabled || !validateLlm()) return;
  let data;
  try { data = jobData(); } catch (error) {
    if (current.showImport) { checkMessage = error.message; checkWarning = true; renderImportPanel(); }
    else { show('progress', true); $('state').textContent = '无法提交'; $('error').textContent = error.message; }
    return;
  }
  busy = true; $('progress').classList.remove('hidden'); $('state').textContent = '正在导入…'; $('job-info').textContent = ''; $('error').textContent = ''; $('result-message').textContent = ''; $('links').replaceChildren(); $('log').textContent = ''; $('progress').setAttribute('aria-busy', 'true'); updateUI(); $('progress').focus();
  try {
    const response = await fetch('/api/jobs', { method: 'POST', headers: { 'X-Timing-Token': token }, body: data });
    const result = await responseData(response, '提交失败');
    if (!response.ok) throw new Error(messageFrom(result, '提交失败'));
    active = result.id; storage.set('job', active); pollFailures = 0; poll(active);
  } catch (error) { busy = false; updateUI(); $('state').textContent = '提交失败'; $('error').textContent = error.message; }
  finally { $('progress').removeAttribute('aria-busy'); }
});
$('choose-audio').addEventListener('click', () => $('audio').click());
$('choose-vocals').addEventListener('click', () => $('vocals').click());
$('import-lyrics').addEventListener('click', () => $('lyrics_file').click());
$('audio').addEventListener('change', () => {
  if (promptBusy) { ++promptSequence; promptBusy = false; promptMessage = '音频已更改，请重新生成提示词。'; promptWarning = true; }
  invalidateCheck('音频已更换，请确认网页模型使用了同一录音，再检查结果。'); updateUI();
});
$('vocals').addEventListener('change', () => { if ($('vocals').files.length && $('audio-mode').value === 'source') $('audio-mode').value = 'vocals'; updateUI(); });
$('clear-vocals').addEventListener('click', () => { $('vocals').value = ''; updateUI(); });
$('audio-mode').addEventListener('change', updateUI);
$('lyrics_file').addEventListener('change', async () => {
  const file = $('lyrics_file').files[0]; if (!file) return;
  const sequence = ++importSequence, revision = lyricsRevision;
  if (ttmlTimer !== null) { clearTimeout(ttmlTimer); ttmlTimer = null; }
  ttmlConverting = false; importing = true; fileError = ''; lyricsFileName = ''; lyricsImportNote = ''; updateUI();
  try {
    if (file.size > 2 * 1024 ** 2) throw new Error('歌词文件最大 2 MB。');
    if (!file.size) throw new Error('歌词文件为空。');
    const bytes = await file.arrayBuffer();
    let text;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes).replace(/^\uFEFF/, ''); }
    catch { throw new Error('歌词文件请保存为 UTF-8 编码，再重新导入。'); }
    if (sequence !== importSequence || revision !== lyricsRevision) return;
    let lyrics = text, converted = null;
    if (file.name.toLowerCase().endsWith('.ttml') || isTtmlXml(text)) {
      ttmlConverting = true; updateUI();
      converted = await requestTtmlConversion(text);
      if (sequence !== importSequence || revision !== lyricsRevision) return;
      lyrics = converted.lyrics;
    }
    $('lyrics').value = lyrics; lyricsFileName = file.name;
    lyricsImportNote = converted
      ? '已从 ' + file.name + ' 导入 TTML 并转换为逐句歌词（' + converted.lines + ' 行）；提交以当前文本框为准。'
      : '已导入 ' + file.name + '；提交以当前文本框为准。';
    ++lyricsRevision; invalidateImportBinding('歌词已更改，请重新生成提示词并重新检查结果。');
  } catch (error) {
    if (sequence !== importSequence || revision !== lyricsRevision) return;
    fileError = error.message;
  } finally { if (sequence === importSequence) { importing = false; ttmlConverting = false; $('lyrics_file').value = ''; updateUI(); } }
});
$('lyrics').addEventListener('input', () => {
  const sequence = ++importSequence, revision = ++lyricsRevision, text = $('lyrics').value;
  if (ttmlTimer !== null) { clearTimeout(ttmlTimer); ttmlTimer = null; }
  ttmlConverting = false; importing = false; fileError = '';
  if (isTtmlXml(text)) { lyricsFileName = ''; lyricsImportNote = ''; schedulePastedTtmlConversion(sequence, revision, text); }
  invalidateImportBinding('歌词已更改，请重新生成提示词并重新检查结果。'); updateUI();
});
$('lyrics').addEventListener('scroll', () => { $('line-numbers').scrollTop = $('lyrics').scrollTop; });
$('clear-lyrics').addEventListener('click', () => { ++importSequence; if (ttmlTimer !== null) { clearTimeout(ttmlTimer); ttmlTimer = null; } ++lyricsRevision; ttmlConverting = false; importing = false; $('lyrics').value = ''; $('lyrics_file').value = ''; lyricsFileName = ''; lyricsImportNote = ''; fileError = ''; invalidateImportBinding('歌词已更改，请重新生成提示词并重新检查结果。'); updateUI(); });
$('generate-prompt').addEventListener('click', generatePrompt);
$('copy-prompt').addEventListener('click', copyPrompt);
$('download-prompt').addEventListener('click', downloadPrompt);
$('import-llm-result').addEventListener('click', () => $('llm-result-file').click());
$('validate-llm-result').addEventListener('click', checkImportResult);
$('llm-result').addEventListener('input', () => { ++resultFileSequence; resultImporting = false; ++resultRevision; invalidateCheck('结果已修改，请重新检查绑定和完整度。'); renderImportPanel(); renderService(); });
$('llm-result-file').addEventListener('change', async () => {
  const file = $('llm-result-file').files[0]; if (!file) return;
  const sequence = ++resultFileSequence;
  resultImporting = true; ++checkSequence; checkBusy = false; checkMessage = '正在读取导入文件…'; checkWarning = false; renderImportPanel(); renderService();
  try {
    if (!file.size) throw new Error('结果文件为空。');
    if (file.size > MAX_LLM_RESULT_BYTES) throw new Error('网页 LLM 结果最大 16 MB，请精简或重新获取结果。');
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    if (sequence !== resultFileSequence) return;
    $('llm-result').value = text; ++resultRevision;
    invalidateCheck('已导入 ' + file.name + '；提交以当前文本框为准，请重新检查。');
  } catch (error) {
    if (sequence !== resultFileSequence) return;
    checkMessage = error instanceof TypeError ? '结果文件请保存为 UTF-8 编码后再导入。' : error.message;
    checkWarning = true;
  } finally { if (sequence === resultFileSequence) { resultImporting = false; $('llm-result-file').value = ''; renderImportPanel(); renderService(); } }
});
$('aligner').addEventListener('change', updateUI); $('language').addEventListener('change', updateUI);
$('llm-prompt-profile').addEventListener('change', () => {
  preferredPromptProfile = $('llm-prompt-profile').value;
  storage.set('llm-prompt-profile', preferredPromptProfile);
  const hasResult = Boolean(llmResultText());
  ++promptSequence; ++checkSequence; promptBusy = false; checkBusy = false;
  promptText = ''; promptRequestId = ''; promptLines = 0;
  promptMessage = '日语拆分设置已更改；只影响之后生成的提示词，请重新生成。'; promptWarning = true;
  checkMessage = hasResult
    ? '日语拆分设置已更改；已保留当前结果文本，实际分词仍以导入内容为准。可重新生成提示词后按需再检查。'
    : '日语拆分设置已更改；请重新生成提示词。';
  checkWarning = true;
  updateUI();
});
$('english-splitter').addEventListener('change', () => { preferredSplitter = $('english-splitter').value; storage.set('english-splitter', preferredSplitter); updateUI(); });
for (const name of ['syllables', 'llm-audio-reading', 'llm-audio-split', 'llm-json-mode']) $(name).addEventListener('change', () => { storage.set(name, $(name).checked); updateUI(); });
for (const name of persisted) for (const event of ['input', 'change']) $(name).addEventListener(event, () => { storage.set(name, $(name).value); updateThinking(); });
$('toggle-key').addEventListener('click', () => { const visible = $('llm-api-key').type === 'password'; $('llm-api-key').type = visible ? 'text' : 'password'; $('toggle-key').setAttribute('aria-label', visible ? '隐藏 API Key' : '显示 API Key'); $('toggle-key').setAttribute('aria-pressed', String(visible)); $('key-eye').src = visible ? ICON_EYE_OFF : ICON_EYE; });
$('mimo-preset').addEventListener('click', () => { if (!$('llm-base-url').value) $('llm-base-url').value = 'https://api.xiaomimimo.com/v1'; $('llm-model').value = 'mimo-v2.6-pro'; $('llm-thinking-mode').value = 'on'; $('llm-thinking-format').value = 'thinking'; $('llm-reasoning-effort').value = ''; $('llm-json-mode').checked = false; for (const name of persisted) storage.set(name, $(name).value); storage.set('llm-json-mode', false); updateThinking(); $('llm-test-result').textContent = '已填入 MiMo Pro 预设，保留原 Base URL 与鉴权设置。请按接口要求填写 Key 和模型 ID。'; });
// Native validation must reveal errors inside folded advanced settings.
$('form').addEventListener('invalid', event => { if (event.target.closest('#api-advanced')) $('api-advanced').open = true; }, true);
$('llm-test').addEventListener('click', async () => {
  if (testBusy || !validateLlm()) return;
  const data = new FormData(); llmData(data); testBusy = true; updateThinking();
  $('llm-test-result').textContent = '正在测试文字接口，最多等待 5 分钟。此测试不发送音频。';
  try { const response = await fetch('/api/llm/test', { method: 'POST', headers: { 'X-Timing-Token': token }, body: data }); const result = await responseData(response, '接口测试失败'); $('llm-test-result').textContent = messageFrom(result, '测试失败'); if (result.ok) $('llm-test-result').textContent += ' 示例：' + (result.example?.parts || []).join(' · ') + '。未验证音频支持与平台思考执行。'; }
  catch (error) { $('llm-test-result').textContent = error.message || '无法连接本机服务，请检查服务后重试。'; }
  finally { testBusy = false; updateThinking(); }
});
$('retry-models').addEventListener('click', checkModels);
updateUI(); checkModels();
const pending = storage.get('job');
if (pending && /^[a-f0-9]{32}$/.test(pending)) { active = pending; busy = true; show('progress', true); $('state').textContent = '正在恢复任务进度…'; updateUI(); poll(active); } else if (pending) storage.remove('job');
