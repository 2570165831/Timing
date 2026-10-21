'use strict';

/*
 * Timing upload-form state tests.
 *
 * Scope: web/ui-logic.js (the pure request-state module, loaded here as an
 * external file) plus the static wiring in web/index.html, web/styles.css and
 * web/app.js. Everything runs offline: no browser, no server, no models and no
 * network access.
 *
 * Run from the repository root:
 *   node --test tests/test_web_ui.cjs
 * (the project shell wrapper is fine too: rtk node --test tests/test_web_ui.cjs)
 *
 * Contract under test
 *   analyzeLyrics(raw, language, llmDirect) -> {empty,error,kind,count,anchored,partial,
 *                          hasEnglish,hasJapanese,hasChinese,hasBackground,
 *                          language,effectiveLanguages}
 *   resolveOptions({lyrics,language,aligner,syllables,splitter,audioReading,
 *                   audioSplit,promptProfile,hasVocals,audioMode}) -> {aligner,engine,language,showLanguage,
 *                   valid,error,showEnglish,showSplitter,allowLlmSplitter,
 *                   showAudioReading,showAudioSplit,syllables,splitter,
 *                   audioReading,audioSplit,llm,sendsAudio,showImport,showPromptProfile,promptReady,
 *                   promptProfile,audioMode,source,models,hasBackground}
 *   submissionFields(plan) -> the browser payload fields owned by the UI.
 *
 * Expected values mirror the shared backend rules:
 *   timing/request_plan.py  ALIGNERS / AUDIO_MODES / SPLITTERS / LANGUAGES
 *                           (mixed / ja / en / auto / zh / zh-en), required
 *                           lyrics, auto -> tifa|sofa, MMS without the LLM
 *                           splitter, text-only LLM splitter. zh is Mandarin;
 *                           Han text only reads as Chinese under zh / zh-en.
 *   timing/sources.py       source naming for the lead / vocals / source
 *                           routes, including imported vocal stems.
 *   timing/text.py          TXT/LRC/JSON recognition, LRC metadata tags and
 *                           JSON role:"bg" background lines.
 */

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const WEB = path.join(ROOT, 'web');
const UI_LOGIC = path.join(WEB, 'ui-logic.js');
const APP_JS = path.join(WEB, 'app.js');
const INDEX_HTML = path.join(WEB, 'index.html');
const STYLES_CSS = path.join(WEB, 'styles.css');

const REQUIRED_MODELS = ['ctc', 'tifa', 'singing', 'bs_lead', 'separator'];

/* ------------------------------------------------------------------ *
 * Harness: evaluate the external pure module in a bare node:vm context.
 * ------------------------------------------------------------------ */

function loadPureSource(source, filename) {
  const context = vm.createContext({});
  vm.runInContext(source, context, { filename: filename });
  return (name) => vm.runInContext(
    'typeof ' + name + ' === "undefined" ? undefined : ' + name,
    context,
    { filename: filename + '.lookup.js' }
  );
}

function loadPureFile(file) {
  return loadPureSource(fs.readFileSync(file, 'utf8'), path.basename(file));
}

function loadFromFile() {
  const lookup = loadPureFile(UI_LOGIC);
  const missing = ['analyzeLyrics', 'resolveOptions', 'submissionFields', 'describeLanguages']
    .filter((name) => typeof lookup(name) !== 'function');
  if (missing.length) throw new Error(path.basename(UI_LOGIC) + ' does not define ' + missing.join(', ') + '.');
  return {
    analyzeLyrics: lookup('analyzeLyrics'),
    resolveOptions: lookup('resolveOptions'),
    submissionFields: lookup('submissionFields'),
    describeLanguages: lookup('describeLanguages'),
  };
}

const html = fs.readFileSync(INDEX_HTML, 'utf8');
const appSource = fs.readFileSync(APP_JS, 'utf8');
const uiSource = fs.readFileSync(UI_LOGIC, 'utf8');

function scriptSrcs(source) {
  return Array.from(source.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']/gi), (match) => match[1].split('?')[0]);
}

function optionValues(source, id) {
  const select = new RegExp('<select\\b[^>]*\\bid\\s*=\\s*["\']' + id + '["\'][^>]*>([\\s\\S]*?)</select>', 'i').exec(source);
  return select ? Array.from(select[1].matchAll(/<option\b[^>]*\bvalue\s*=\s*["']([^"']*)["']/gi), (match) => match[1]) : null;
}

function selectBlock(source, id) {
  const start = source.indexOf('id="' + id + '"');
  if (start < 0) return null;
  const open = source.lastIndexOf('<select', start);
  const close = source.indexOf('</select>', start);
  return open < 0 || close < 0 ? null : source.slice(open, close);
}

function optionLabels(source, id) {
  const block = selectBlock(source, id);
  return block === null ? null : block.split('<option').slice(1)
    .map((part) => part.slice(part.indexOf('>') + 1, part.indexOf('</option>')).trim());
}

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

const JA_TEXT = '光の中へ\n走り出す\n夜が明ける';
const EN_TEXT = 'Run into the light\nWe are moving on';
const MIXED_TEXT = '光の中へ\nRun into the light';
const ZH_TEXT = '今天很好\n明天更好';
const ZH_EN_TEXT = '今天很好\nWe are moving on';
const FULL_LRC = '[00:12.34]光の中へ\n[00:18.00]Run into the light';
const PARTIAL_LRC = '光の中へ\n[00:18.00]Run into the light';
const META_ONLY_LRC = '[ar:测试歌手]\n[ti:歌词]';
const META_LRC = '[ar:测试歌手]\n[ti:歌词]\n[al:专辑]\n[by:上传者]\n[offset:120]\n[length:03:20]\n' +
  '[00:12.34]光の中へ\n[00:18.00]走り出す';
const BROKEN_JSON = '[{"text":"光の中へ","start":12.34,"end":18.0},';
const JSON_WITHOUT_TIMES = '[{"text":"光の中へ"},{"text":"Run into the light"}]';
const JSON_MISSING_END = '[{"text":"光の中へ","start":12.34,"end":18.0},{"text":"走り出す","start":18.0}]';
const JSON_REVERSED_TIMES = '[{"text":"光の中へ","start":18.0,"end":12.34}]';
const DOUBLE_STAMP_LRC = '[00:10.00][00:20.00]同じ行に二つの時間タグ';
const JSON_LYRICS = '[{"text":"光の中へ","start":12.34,"end":18.0},' +
  '{"text":"Run into the light","start":18.0,"end":24.5}]';
const JSON_BACKGROUND = '[{"text":"光の中へ","start":12.34,"end":18.0,"role":"main"},' +
  '{"text":"oh oh","start":13.0,"end":17.0,"role":"bg","parent":0}]';
const TTML_XML = '<?xml version="1.0" encoding="UTF-8"?>' +
  '<tt xmlns="http://www.w3.org/ns/ttml"><body><div begin="12.34s" end="18s">' +
  '<p begin="12.34s" end="18s">光の中へ</p></div></body></tt>';
const TTML_NORMALIZED = '[{"text":"光の中へ","start":12.34,"end":18.0}]';

// A lyrics descriptor shaped like the analyzeLyrics() result.
function lyrics(overrides) {
  return Object.assign({
    empty: false,
    error: '',
    kind: 'lrc',
    count: 4,
    anchored: true,
    partial: false,
    hasEnglish: true,
    hasJapanese: true,
    hasBackground: false,
  }, overrides);
}

// A saved-preferences object shaped like the form state.
function preferences(overrides) {
  return Object.assign({
    lyrics: lyrics(),
    language: 'mixed',
    aligner: 'auto',
    syllables: true,
    splitter: 'syllabify',
    audioReading: false,
    audioSplit: false,
    promptProfile: 'fine',
    hasVocals: false,
    audioMode: 'lead',
  }, overrides);
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function fakeResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function fakeClassList() {
  const values = new Set();
  return {
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    contains: (name) => values.has(name),
    toggle(name, force) {
      const visible = force === undefined ? !values.has(name) : Boolean(force);
      if (visible) values.add(name); else values.delete(name);
      return visible;
    },
  };
}

function fakeElement(id) {
  const listeners = new Map();
  const element = {
    id, tagName: 'DIV', value: '', checked: false, disabled: false, required: false,
    hidden: false, files: [], options: [], textContent: '', title: '', className: '',
    scrollTop: 0, scrollHeight: 0, open: false, type: 'text', classList: fakeClassList(),
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    async dispatch(type, extra = {}) {
      for (const handler of listeners.get(type) || []) {
        await handler(Object.assign({ type, target: element, preventDefault() {} }, extra));
      }
    },
    querySelector(selector) {
      const match = /^option\[value=["'](.+)["']\]$/.exec(selector);
      return match ? element.options.find((option) => option.value === match[1]) || null : null;
    },
    closest: () => null,
    append(child) { if (!element.children) element.children = []; element.children.push(child); },
    replaceChildren(...children) { element.children = children; },
    setAttribute(name, value) { if (!element.attributes) element.attributes = {}; element.attributes[name] = String(value); },
    removeAttribute(name) { if (element.attributes) delete element.attributes[name]; },
    setCustomValidity() {},
    checkValidity: () => true,
    reportValidity: () => true,
    focus() {},
    click() {},
  };
  return element;
}

async function makeAppHarness() {
  const elements = new Map();
  const timers = new Map();
  const ttmlRequests = [], statusRequests = [];
  let nextTimer = 1;
  let ttmlHandler = async () => fakeResponse(500, { detail: 'unexpected TTML request' });

  const element = (id) => {
    if (!elements.has(id)) elements.set(id, fakeElement(id));
    return elements.get(id);
  };
  const setOptions = (id, values) => {
    const target = element(id);
    target.tagName = 'SELECT';
    target.options = values.map((value) => ({ value, textContent: value, disabled: false, hidden: false }));
  };
  setOptions('aligner', optionValues(html, 'aligner'));
  setOptions('audio-mode', optionValues(html, 'audio-mode'));
  setOptions('language', optionValues(html, 'language'));
  setOptions('english-splitter', ['syllabify', 'pyphen', 'llm']);
  setOptions('llm-prompt-profile', ['fine', 'original']);
  element('aligner').value = 'auto';
  element('audio-mode').value = 'lead';
  element('language').value = 'mixed';
  element('english-splitter').value = 'syllabify';
  element('llm-prompt-profile').value = 'fine';
  element('syllables').checked = true;
  element('llm-thinking-mode').value = 'default';
  element('llm-thinking-format').value = 'reasoning_effort';
  element('llm-api-key-header').value = 'Authorization';
  element('llm-api-key-format').value = 'auto';

  const document = {
    getElementById: element,
    querySelector: (selector) => selector === 'meta[name="timing-token"]' ? { content: 'test-token' } : null,
    createElement: (tag) => fakeElement('created-' + tag),
    createRange: () => ({ selectNodeContents() {} }),
    execCommand: () => true,
  };
  const storageValues = new Map();
  const context = vm.createContext({
    document,
    window: { getSelection: () => ({ removeAllRanges() {}, addRange() {} }) },
    localStorage: {
      getItem: (key) => storageValues.has(key) ? storageValues.get(key) : null,
      setItem: (key, value) => storageValues.set(key, String(value)),
      removeItem: (key) => storageValues.delete(key),
    },
    navigator: {},
    fetch: async (url, init = {}) => {
      if (url === '/api/status') {
        statusRequests.push(init);
        return fakeResponse(200, { app: 'Timing', api_version: 7, version: 'test', queued: 0,
          models: { ctc: true, tifa: true, singing: true, bs_lead: true, separator: true } });
      }
      if (url === '/api/lyrics/ttml') {
        ttmlRequests.push({ init, body: init.body });
        return ttmlHandler(init, ttmlRequests.length);
      }
      throw new Error('unexpected request: ' + url);
    },
    setTimeout: (handler, delay) => {
      const id = nextTimer++;
      timers.set(id, { handler, delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    FormData, Blob, TextEncoder, TextDecoder,
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
  });

  vm.runInContext(uiSource, context, { filename: 'ui-logic.js' });
  vm.runInContext(appSource, context, { filename: 'app.js' });
  const settle = async (rounds = 8) => { for (let i = 0; i < rounds; i++) await Promise.resolve(); };
  await settle();
  return {
    setTtmlHandler(handler) { ttmlHandler = handler; },
    element,
    dispatch: (id, type) => element(id).dispatch(type),
    runNextTimer() {
      const next = timers.entries().next();
      assert.equal(next.done, false, 'expected a pending lyric timer');
      const [id, timer] = next.value;
      timers.delete(id);
      return Promise.resolve(timer.handler());
    },
    timerCount: () => timers.size,
    settle,
    ttmlRequests,
    statusRequests,
  };
}

/* ------------------------------------------------------------------ *
 * Boot
 * ------------------------------------------------------------------ */

let loadedApi = null;
let loadError = null;
try {
  loadedApi = loadFromFile();
} catch (error) {
  loadError = error;
}

describe('shipped files', () => {
  test('web/ui-logic.js loads in a bare sandbox and is required by this harness', () => {
    const lookup = loadPureSource('function sampleFunction() { return 42; }', 'sample.js');
    assert.equal(lookup('sampleFunction')(), 42);
    assert.equal(loadError, null, 'web/ui-logic.js must load: ' + (loadError && loadError.message));
    assert.equal(typeof loadedApi.analyzeLyrics, 'function');
    assert.equal(typeof loadedApi.resolveOptions, 'function');
  });

  test('web/ui-logic.js stays free of DOM, storage, network and timer references', () => {
    assert.ok(
      !/\b(document|window|localStorage|fetch|setTimeout|setInterval|FormData)\b/.test(uiSource),
      'ui-logic.js must stay a pure module so it can be tested without a browser'
    );
  });

  test('web/ui-logic.js exports the shipped language contract', () => {
    const lookup = loadPureFile(UI_LOGIC);
    assert.deepStrictEqual([...lookup('LANGUAGES')], ['mixed', 'ja', 'en', 'auto', 'zh', 'zh-en']);
  });

  test('web/index.html contains no inline script blocks', () => {
    assert.ok(
      !/<script\b(?![^>]*\bsrc\s*=)[^>]*>[\s\S]*?<\/script>/i.test(html),
      'form logic must live in external files, not inline <script> blocks'
    );
  });

  test('web/index.html loads the external ui-logic and app scripts in order', () => {
    const srcs = scriptSrcs(html);
    assert.ok(srcs.includes('/static/ui-logic.js'), 'missing ui-logic.js script tag: ' + srcs.join(', '));
    assert.ok(srcs.includes('/static/app.js'), 'missing app.js script tag: ' + srcs.join(', '));
    assert.ok(srcs.indexOf('/static/ui-logic.js') < srcs.indexOf('/static/app.js'), 'ui-logic.js must load before app.js');
  });

  test('web/index.html links styles.css and the stylesheet carries the form styles', () => {
    assert.ok(html.includes('/static/styles.css'), 'stylesheet link missing');
    const css = fs.readFileSync(STYLES_CSS, 'utf8');
    assert.ok(css.length > 4000, 'styles.css looks truncated: ' + css.length + ' bytes');
    assert.ok(css.includes('.lyrics-box'), 'styles.css is missing the form styles');
  });

  test('the request token ships exactly once, through the timing-token meta tag', () => {
    assert.ok(/<meta\s+name="timing-token"\s+content="__TIMING_TOKEN__">/i.test(html), 'timing-token meta tag missing');
    assert.equal(html.split('__TIMING_TOKEN__').length - 1, 1, 'the token placeholder must appear exactly once');
  });

  test('web/app.js compiles as a classic script', () => {
    assert.doesNotThrow(() => new vm.Script(appSource, { filename: 'app.js' }));
  });

  test('web/app.js reads the token meta and submits audio_mode without the removed fields', () => {
    assert.ok(appSource.includes('timing-token'), 'app.js must read the token meta tag');
    assert.match(appSource, /Object\.entries\(submissionFields\(current\)\)/,
      'payload policy must come from the tested ui-logic helper');
    assert.ok(appSource.includes("data.set('lyrics', $('lyrics').value);"),
      'lyrics must be submitted from the textarea verbatim');
    assert.ok(!/data\.set\('(?:separate|lead)'/.test(appSource), 'the removed separate/lead fields must not be sent');
  });

  test('web/app.js pins the status guard to api_version 7', () => {
    assert.match(appSource, /api_version\s*!==\s*7/);
  });

  test('the page exposes the song-language selector as a visible lyrics setting', () => {
    assert.deepStrictEqual(optionValues(html, 'language'), ['mixed', 'ja', 'en', 'zh', 'zh-en']);
    assert.ok(html.includes('for="language"'), 'the language selector needs a visible label');
    assert.ok(!html.includes('语言提示'), 'the language selector must not fold back into a details hint');
    const before = html.slice(0, html.indexOf('id="language"'));
    assert.equal(before.split('<details').length, before.split('</details>').length, 'the language selector must sit outside any details element');
    assert.ok(!selectBlock(html, 'language').includes('selected'), 'mixed stays the default first option');
  });

  test('the language options carry the shipped mixed and Mandarin labels', () => {
    assert.deepStrictEqual(optionLabels(html, 'language'), ['日英混唱', '日语', '英语', '中文（普通话）', '中英混唱']);
  });

  test('the language hint explains Mandarin selection instead of an export-only tag', () => {
    assert.ok(html.includes('汉字不会自动判定为中文'), 'the hint must explain the Han ambiguity');
    assert.ok(html.includes('中文（普通话）'), 'Chinese must be named as Mandarin');
    assert.ok(!html.includes('此选项用于导出语言标记'), 'the old export-only language hint must be gone');
  });

  test('the shared audio-reading flag ships a language-following label target', () => {
    assert.ok(html.includes('id="audio-reading-text"'), 'the audio-reading label span is missing');
    assert.ok(html.includes('中文读音结果以拼音给出'), 'the helper note must say Mandarin readings come back as pinyin');
  });

  test('the shipped assets carry the 0.13.0 version stamp', () => {
    for (const asset of ['/static/styles.css?v=0.13.0', '/static/ui-logic.js?v=0.13.0', '/static/app.js?v=0.13.0']) {
      assert.ok(html.includes(asset), 'missing versioned asset ' + asset);
    }
  });

  test('the removed ASR model group and draft copy are gone from the shipped files', () => {
    const combined = html + appSource + uiSource;
    assert.ok(!/['"]asr['"]/.test(appSource + uiSource), 'no asr model group may remain');
    assert.ok(!/听写/.test(combined), 'no ASR dictation copy may remain');
    assert.ok(!/生成歌词草稿/.test(combined), 'no draft-generation copy may remain');
    assert.ok(!/Qwen/i.test(combined), 'the retired Qwen references are gone');
  });

  test('the audio-mode selector exposes exactly lead, vocals and source', () => {
    assert.deepStrictEqual(optionValues(html, 'audio-mode'), ['lead', 'vocals', 'source']);
  });

  test('the aligner selector keeps local/API modes and adds the web import mode', () => {
    assert.deepStrictEqual(optionValues(html, 'aligner'), ['auto', 'llm_direct', 'llm_import', 'tifa', 'ctc', 'sofa']);
    assert.equal(optionValues(html, 'aligner')[0], 'auto');
  });

  test('the Japanese prompt profile is a visible direct-like selector with two labels', () => {
    assert.ok(html.includes('id="prompt-profile-settings" class="local-settings hidden"'));
    assert.ok(html.includes('for="llm-prompt-profile">日语拆分</label>'));
    assert.deepStrictEqual(optionValues(html, 'llm-prompt-profile'), ['fine', 'original']);
    assert.deepStrictEqual(optionLabels(html, 'llm-prompt-profile'), ['细拆（默认）', '适度合并（旧版）']);
    assert.match(appSource, /show\('prompt-profile-settings', current\.showPromptProfile\)/);
  });

  test('LLM direct is presented as one full-track request with manual fallback', () => {
    assert.match(optionLabels(html, 'aligner')[1], /^LLM 整曲识别/);
    assert.ok(html.includes('id="language-settings"'), 'the language settings need a hideable container');
    assert.ok(appSource.includes('整首歌和全部歌词只发送一次'), 'the disclosure must name the single full-track request');
    assert.ok(appSource.includes('不会自动重试'), 'the hint must explain the manual fallback after an invalid response');
  });

  test('the web import flow is a visible three-step local prompt and result handoff', () => {
    assert.match(optionLabels(html, 'aligner')[2], /^网页 LLM（导入结果）/);
    for (const id of ['llm-import-panel', 'llm-import-audio-name', 'generate-prompt', 'copy-prompt',
      'download-prompt', 'llm-prompt', 'llm-result', 'llm-result-file', 'validate-llm-result']) {
      assert.ok(html.includes('id="' + id + '"'), 'missing web import control #' + id);
    }
    const panel = html.slice(html.indexOf('id="llm-import-panel"'), html.indexOf('id="english-settings"'));
    assert.equal((panel.match(/<li>/g) || []).length, 3, 'the handoff must stay at three steps');
    assert.ok(panel.includes('完整音频'), 'step one must name the full recording');
    assert.ok(panel.includes('同起点、同时长'), 'step one must preserve the optional-vocal alignment rule');
    assert.ok(panel.includes('检查结果'), 'the optional local check must be visible');
    assert.ok(!panel.includes('llm-api-key'), 'the import panel must not expose provider credentials');
  });

  test('web import requests use only the local prompt/check endpoints and current textarea', () => {
    assert.ok(appSource.includes("fetch('/api/llm/prompt'"));
    assert.ok(appSource.includes("fetch('/api/llm/import/validate'"));
    assert.match(appSource, /data\.set\('llm_result_file',\s*new Blob\(\[text\]/);
    assert.match(appSource, /MAX_LLM_RESULT_BYTES\s*=\s*16 \* 1024 \*\* 2/);
    assert.ok(appSource.includes("$('llm-result').value"), 'the current textarea value must win');
    assert.ok(!/storage\.(?:get|set)\([^\n]*(?:llm-result|llm-prompt(?!-profile))/.test(appSource), 'prompt and result must stay out of storage');
    assert.ok(!/llm-result[^\n]*innerHTML|innerHTML[^\n]*llm-result/.test(appSource), 'LLM result must never use HTML rendering');
  });

  test('web import prompt/check requests discard stale responses and send no audio or credentials', () => {
    const generate = appSource.slice(appSource.indexOf('async function generatePrompt()'), appSource.indexOf('async function copyPrompt()'));
    const check = appSource.slice(appSource.indexOf('async function checkImportResult()'), appSource.indexOf('function link('));
    assert.ok(generate.includes("data.set('lyrics', lyrics)"));
    assert.ok(generate.includes("data.set('audio_name', audio.name)"));
    assert.ok(generate.includes("data.set('llm_prompt_profile', profile)"));
    assert.ok(!generate.includes("data.set('audio'"));
    assert.ok(!generate.includes('llmData('));
    assert.ok(!check.includes('llmData('));
    assert.match(generate, /sequence !== promptSequence \|\| revision !== lyricsRevision \|\| profile !== \$\('llm-prompt-profile'\)\.value/);
    assert.match(check, /sequence !== checkSequence \|\| revision !== lyricsRevision \|\| resultRevisionAtStart !== resultRevision/);
    assert.ok(appSource.includes("invalidateImportBinding('歌词已更改"), 'lyrics edits must invalidate the prompt and check state');
  });

  test('profile changes persist, discard stale request state and keep the pasted result', () => {
    const handler = appSource.slice(
      appSource.indexOf("$('llm-prompt-profile').addEventListener('change'"),
      appSource.indexOf("$('english-splitter').addEventListener('change'")
    );
    assert.ok(appSource.includes("storage.get('llm-prompt-profile')"));
    assert.ok(handler.includes("storage.set('llm-prompt-profile', preferredPromptProfile)"));
    assert.match(handler, /\+\+promptSequence; \+\+checkSequence/);
    assert.match(handler, /promptText = ''/);
    assert.ok(!handler.includes("$('llm-result').value ="));
    assert.ok(handler.includes('已保留当前结果文本'));
    assert.ok(handler.includes('只影响之后生成的提示词'));
  });

  test('the page carries the background-lyrics note for the lead route', () => {
    assert.ok(html.includes('id="bg-note"'), 'bg-note element missing');
  });

  test('the page keeps the optional vocals import and its explicit harmonies copy', () => {
    assert.ok(html.includes('id="vocals"'), 'optional vocals input missing');
    assert.ok(html.includes('保留主唱和和声'), 'the full-vocals option must spell out that harmonies are kept');
  });

  test('the lyrics picker accepts TTML and describes its local conversion', () => {
    assert.match(html, /id="lyrics_file"[^>]*accept="[^"]*\.ttml/);
    assert.ok(html.includes('TTML 会先在本机转换为逐句歌词'));
    assert.ok(appSource.includes("fetch('/api/lyrics/ttml'"));
    assert.ok(appSource.includes("data.set('lyrics', xml)"));
    assert.ok(appSource.includes("headers: { 'X-Timing-Token': token }"));
    assert.match(appSource, /ttmlConverting \? '正在转换 TTML 歌词…'/);
    assert.match(appSource, /\$\('generate-prompt'\)\.disabled = busy \|\| promptBusy \|\| importing \|\| ttmlConverting/);
  });
});

describe('TTML conversion wiring', () => {
  test('a pasted TTML conversion is requested once and a late reply cannot replace newer text', async () => {
    const app = await makeAppHarness();
    const response = deferred();
    app.setTtmlHandler(() => response.promise);
    const lyrics = app.element('lyrics');
    lyrics.value = TTML_XML;
    await app.dispatch('lyrics', 'input');

    assert.equal(app.element('lyrics-status').textContent, '正在转换 TTML 歌词…');
    assert.equal(app.timerCount(), 1);
    const conversion = app.runNextTimer();
    await app.settle();
    assert.equal(app.ttmlRequests.length, 1);
    assert.equal(app.ttmlRequests[0].body.get('lyrics'), TTML_XML);
    assert.equal(app.ttmlRequests[0].init.headers['X-Timing-Token'], 'test-token');

    await app.dispatch('syllables', 'change');
    assert.equal(app.ttmlRequests.length, 1, 'unrelated UI renders must not repeat the conversion');
    lyrics.value = '手动输入的新歌词';
    await app.dispatch('lyrics', 'input');
    response.resolve(fakeResponse(200, { lyrics: TTML_NORMALIZED, lines: 1 }));
    await conversion;
    await app.settle();

    assert.equal(lyrics.value, '手动输入的新歌词');
    assert.match(app.element('lyrics-status').textContent, /已识别纯文本 · 1 行/);
    assert.equal(app.element('lyrics-status').title, '');
  });

  test('clearing lyrics cancels a pending pasted TTML conversion', async () => {
    const app = await makeAppHarness();
    const response = deferred();
    app.setTtmlHandler(() => response.promise);
    app.element('lyrics').value = TTML_XML;
    await app.dispatch('lyrics', 'input');
    const conversion = app.runNextTimer();
    await app.settle();
    await app.element('clear-lyrics').dispatch('click');
    response.resolve(fakeResponse(200, { lyrics: TTML_NORMALIZED, lines: 1 }));
    await conversion;
    await app.settle();

    assert.equal(app.element('lyrics').value, '');
    assert.equal(app.element('lyrics-file-note').textContent, '');
  });

  test('a .ttml file is converted to normalized JSON before entering the textarea', async () => {
    const app = await makeAppHarness();
    app.setTtmlHandler(async () => fakeResponse(200, { lyrics: TTML_NORMALIZED, lines: 1 }));
    const bytes = new TextEncoder().encode(TTML_XML);
    app.element('lyrics_file').files = [{ name: 'Song.TTML', size: bytes.byteLength, arrayBuffer: async () => bytes.buffer }];
    app.element('lyrics').value = '旧的纯歌词';

    await app.dispatch('lyrics_file', 'change');

    assert.equal(app.ttmlRequests.length, 1);
    assert.equal(app.ttmlRequests[0].body.get('lyrics'), TTML_XML);
    assert.equal(app.element('lyrics').value, TTML_NORMALIZED);
    assert.equal(app.element('lyrics-file-note').textContent,
      '已从 Song.TTML 导入 TTML 并转换为逐句歌词（1 行）；提交以当前文本框为准。');
    assert.match(app.element('lyrics-status').textContent, /已识别逐句时间 · 1 行/);
  });

  test('editing during a file read prevents the older file result from taking over', async () => {
    const app = await makeAppHarness();
    const bytes = deferred();
    app.setTtmlHandler(async () => fakeResponse(200, { lyrics: TTML_NORMALIZED, lines: 1 }));
    app.element('lyrics_file').files = [{ name: 'raced.ttml', size: 100, arrayBuffer: () => bytes.promise }];
    const importing = app.dispatch('lyrics_file', 'change');
    await app.settle();
    app.element('lyrics').value = '用户先写入的新歌词';
    await app.dispatch('lyrics', 'input');
    bytes.resolve(new TextEncoder().encode(TTML_XML).buffer);
    await importing;
    await app.settle();

    assert.equal(app.element('lyrics').value, '用户先写入的新歌词');
    assert.equal(app.ttmlRequests.length, 0, 'a stale file read must not call the conversion endpoint');
  });

  test('an HTTP 400 TTML error keeps prior text and surfaces the backend detail', async () => {
    const app = await makeAppHarness();
    app.setTtmlHandler(async () => fakeResponse(400, { detail: '不支持的 TTML 时间表达式' }));
    const bytes = new TextEncoder().encode('<tt><body/></tt>');
    app.element('lyrics_file').files = [{ name: 'bad.ttml', size: bytes.byteLength, arrayBuffer: async () => bytes.buffer }];
    app.element('lyrics').value = '保留的旧歌词';

    await app.dispatch('lyrics_file', 'change');

    assert.equal(app.element('lyrics').value, '保留的旧歌词');
    assert.equal(app.element('lyrics-status').textContent, '不支持的 TTML 时间表达式');
    assert.equal(app.element('lyrics-file-note').textContent, '');
  });
});

function defineContractTests(api) {
  const analyze = (raw, language) => language === undefined ? api.analyzeLyrics(raw) : api.analyzeLyrics(raw, language);
  const analyzeDirect = (raw) => api.analyzeLyrics(raw, 'zh-en', true);
  const resolve = (overrides) => api.resolveOptions(preferences(overrides));

  describe('analyzeLyrics', () => {
    test('empty input is a blocking error, not a parse failure', () => {
      const info = analyze('');
      assert.equal(info.empty, true);
      assert.equal(info.error, '未填写歌词：请粘贴或导入歌词。');
      assert.equal(info.count, 0);
      assert.equal(info.anchored, false);
      assert.equal(info.partial, false);
      assert.equal(info.hasEnglish, false);
      assert.equal(info.hasJapanese, false);
      assert.equal(info.hasChinese, false);
      assert.equal(info.hasBackground, false);
    });

    test('any leading angle-bracket input is delegated instead of counted as lyric tags', () => {
      for (const source of [TTML_XML, '<!-- timing -->\n<tt xmlns="http://www.w3.org/ns/ttml"><body/></tt>',
        '<tt/>', '<tt', '<html>', '\uFEFF\n  <tt']) {
        const info = analyze(source);
        assert.equal(info.empty, false);
        assert.equal(info.kind, 'ttml');
        assert.equal(info.needsTtmlConversion, true);
        assert.equal(info.count, 0);
        assert.equal(info.anchored, false);
        assert.equal(info.hasJapanese, false);
        assert.match(info.error, /TTML.*转换/);
      }
      assert.equal(api.resolveOptions(preferences({ lyrics: analyze(TTML_XML), aligner: 'auto' })).valid, false);
    });

    test('plain Japanese text is text and unanchored', () => {
      const info = analyze(JA_TEXT);
      assert.equal(info.empty, false);
      assert.equal(info.error, '');
      assert.equal(info.kind, 'text');
      assert.equal(info.count, 3);
      assert.equal(info.anchored, false);
      assert.equal(info.partial, false);
      assert.equal(info.hasJapanese, true);
      assert.equal(info.hasEnglish, false);
    });

    test('plain English text is text and unanchored', () => {
      const info = analyze(EN_TEXT);
      assert.equal(info.kind, 'text');
      assert.equal(info.count, 2);
      assert.equal(info.hasEnglish, true);
      assert.equal(info.hasJapanese, false);
    });

    test('mixed Japanese and English lines report both languages', () => {
      const info = analyze(MIXED_TEXT);
      assert.equal(info.hasJapanese, true);
      assert.equal(info.hasEnglish, true);
      assert.equal(info.count, 2);
    });

    test('a full LRC is anchored with both languages detected', () => {
      const info = analyze(FULL_LRC);
      assert.equal(info.error, '');
      assert.equal(info.kind, 'lrc');
      assert.equal(info.count, 2);
      assert.equal(info.anchored, true);
      assert.equal(info.partial, false);
      assert.equal(info.hasJapanese, true);
      assert.equal(info.hasEnglish, true);
    });

    test('a partly stamped LRC is partial', () => {
      const info = analyze(PARTIAL_LRC);
      assert.equal(info.count, 2);
      assert.equal(info.partial, true);
    });

    test('LRC metadata tags are not lyric lines', () => {
      const info = analyze(META_LRC);
      assert.equal(info.kind, 'lrc');
      assert.equal(info.count, 2);
      assert.equal(info.anchored, true);
      assert.equal(info.partial, false);
    });

    test('a metadata-only LRC reports no alignable lyrics', () => {
      const info = analyze(META_ONLY_LRC);
      assert.equal(info.count, 0);
      assert.equal(info.error, '没有可对齐的歌词，请填写歌词。');
    });

    test('malformed JSON is reported instead of read as lyrics', () => {
      const info = analyze(BROKEN_JSON);
      assert.ok(info.error, 'expected a JSON format error');
      assert.equal(info.count, 0);
    });

    test('JSON objects and invalid optional metadata are rejected before upload', () => {
      for (const raw of ['{"text":"Hello"}', '{broken', '[{"text":"Hello","role":null}]', '[{"text":"Hello","translation":false}]']) {
        assert.ok(analyze(raw).error, raw);
      }
    });

    test('JSON singer IDs accept v3, v1000 and the nine-digit boundary', () => {
      const raw = JSON.stringify([
        { text: 'First', start: 0, end: 1, agent: 'v3' },
        { text: 'Second', start: 1, end: 2, agent: 'v1000' },
        { text: 'Third', start: 2, end: 3, agent: 'v999999999' },
      ]);
      const info = analyze(raw);
      assert.equal(info.error, '');
      assert.equal(info.anchored, true);
      assert.equal(info.count, 3);
    });

    test('JSON singer IDs reject zero-leading, malformed and overlong values with the allowed format', () => {
      for (const agent of ['v0', 'v01', 'v', 'V3', 'v3x', 'v1000000000', 3, null]) {
        const info = analyze(JSON.stringify([{ text: 'Line', start: 0, end: 1, agent }]));
        assert.match(info.error, /歌手 ID 无效.*v1.*v3.*v1000/);
      }
    });

    test('main JSON rows must be chronological while overlapping singing stays supported', () => {
      assert.ok(analyze('[{"text":"Later","start":5,"end":7},{"text":"Earlier","start":1,"end":3}]').error);
      assert.equal(analyze('[{"text":"First","start":0,"end":5},{"text":"Overlap","start":2,"end":4}]').error, '');
    });

    test('MMS rejects known sentence ranges over 30 seconds before upload', () => {
      for (const raw of ['[{"text":"Long line","start":0,"end":31}]', '[00:00]Long line\n[00:31]Next']) {
        const info = analyze(raw);
        assert.ok(resolve({ lyrics: info, aligner: 'ctc' }).error);
        assert.equal(resolve({ lyrics: info, aligner: 'tifa' }).valid, true);
      }
    });

    test('JSON rows need a valid start and end pair', () => {
      assert.ok(analyze(JSON_MISSING_END).error, 'a start without an end is incomplete');
      assert.ok(analyze(JSON_REVERSED_TIMES).error, 'end must come after start');
    });

    test('JSON without times is valid but unanchored', () => {
      const info = analyze(JSON_WITHOUT_TIMES);
      assert.equal(info.error, '');
      assert.equal(info.kind, 'json');
      assert.equal(info.count, 2);
      assert.equal(info.anchored, false);
      assert.equal(info.partial, false);
    });

    test('a JSON line array is json and carries its own times', () => {
      const info = analyze(JSON_LYRICS);
      assert.equal(info.error, '');
      assert.equal(info.kind, 'json');
      assert.equal(info.count, 2);
      assert.equal(info.anchored, true);
      assert.equal(info.partial, false);
      assert.equal(info.hasJapanese, true);
      assert.equal(info.hasEnglish, true);
    });

    test('JSON role bg lines set hasBackground', () => {
      const info = analyze(JSON_BACKGROUND);
      assert.equal(info.kind, 'json');
      assert.equal(info.hasBackground, true);
      assert.equal(info.count, 2);
    });

    test('text and JSON without background lines do not set hasBackground', () => {
      assert.equal(analyze(JA_TEXT).hasBackground, false);
      assert.equal(analyze(FULL_LRC).hasBackground, false);
      assert.equal(analyze(JSON_LYRICS).hasBackground, false);
    });

    test('two time tags on one LRC line is an error, not a lyric', () => {
      const info = analyze(DOUBLE_STAMP_LRC);
      assert.ok(info.error, 'expected a non-empty error for an unexpandable LRC line');
    });

    test('the default language keeps the historical JA/EN reading, Han included', () => {
      const japanese = analyze(JA_TEXT);
      assert.equal(japanese.language, 'mixed');
      assert.equal(japanese.hasJapanese, true);
      assert.equal(japanese.hasChinese, false);
      const hanOnly = analyze(ZH_TEXT);
      assert.equal(hanOnly.hasJapanese, true, 'Han alone still reads as Japanese under mixed');
      assert.equal(hanOnly.hasChinese, false);
      assert.deepStrictEqual([...hanOnly.effectiveLanguages], ['mixed', 'mixed']);
    });

    test('zh mode reads Han text as Mandarin Chinese', () => {
      const info = analyze(ZH_TEXT, 'zh');
      assert.equal(info.language, 'zh');
      assert.equal(info.error, '');
      assert.equal(info.hasChinese, true);
      assert.equal(info.hasJapanese, false);
      assert.equal(info.hasEnglish, false);
      assert.deepStrictEqual([...info.effectiveLanguages], ['zh', 'zh']);
    });

    test('zh-en separates Chinese from English and keeps kana Japanese', () => {
      const info = analyze(ZH_EN_TEXT, 'zh-en');
      assert.equal(info.hasChinese, true);
      assert.equal(info.hasEnglish, true);
      assert.equal(info.hasJapanese, false);
      const kana = analyze('今天很好\nまた明日', 'zh-en');
      assert.equal(kana.hasChinese, true);
      assert.equal(kana.hasJapanese, true);
    });

    test('explicit JSON row languages override the song language', () => {
      const info = analyze('[{"text":"今天很好","language":"zh"},{"text":"We are moving on","language":"en"}]', 'mixed');
      assert.equal(info.hasChinese, true);
      assert.equal(info.hasEnglish, true);
      assert.equal(info.hasJapanese, false);
      assert.deepStrictEqual([...info.effectiveLanguages], ['zh', 'en']);
    });

    test('auto rows inherit the song language and unknown row values are rejected', () => {
      const info = analyze('[{"text":"今天很好"},{"text":"We are moving on"}]', 'zh-en');
      assert.deepStrictEqual([...info.effectiveLanguages], ['zh-en', 'zh-en']);
      assert.equal(info.hasChinese, true);
      assert.equal(info.hasEnglish, true);
      assert.equal(analyze('[{"text":"Hello","language":"auto"}]').error, '');
      assert.ok(analyze('[{"text":"Hello","language":"ko"}]').error, 'unknown row languages must be rejected');
    });

    test('an unknown language argument falls back to mixed and auto follows mixed', () => {
      assert.equal(analyze(JA_TEXT, 'garbage').language, 'mixed');
      assert.equal(analyze(JA_TEXT, 'garbage').hasJapanese, true);
      assert.equal(analyze(JA_TEXT, 'auto').language, 'auto');
      assert.equal(analyze(JA_TEXT, 'auto').hasJapanese, true);
      assert.equal(analyze(JA_TEXT, 'auto').hasChinese, false);
    });

    test('LLM direct leaves language pending and does not infer Han before the model responds', () => {
      const info = analyzeDirect('今天很好\nまた明日\nHello');
      assert.equal(info.language, 'auto');
      assert.equal(info.hasChinese, false);
      assert.equal(info.hasJapanese, false);
      assert.equal(info.hasEnglish, false);
      assert.deepStrictEqual([...info.effectiveLanguages], ['auto', 'auto', 'auto']);
      const rows = analyzeDirect('[{"text":"今天很好","language":"zh","start":0,"end":1}]');
      assert.equal(rows.hasChinese, false);
      assert.deepStrictEqual([...rows.effectiveLanguages], ['auto']);
    });
  });

  describe('resolveOptions: empty lyrics', () => {
    test('empty lyrics block submission and normalize every helper off', () => {
      const result = resolve({
        lyrics: lyrics({ empty: true, kind: '', count: 0, anchored: false, hasJapanese: false, hasEnglish: false }),
        aligner: 'tifa',
        audioReading: true,
        audioSplit: true,
      });
      assert.equal(result.valid, false);
      assert.ok(result.error);
      assert.equal(result.engine, 'tifa');
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
      assert.equal(result.showAudioReading, false);
      assert.equal(result.showAudioSplit, false);
      assert.deepStrictEqual([...result.models], REQUIRED_MODELS);
    });

    test('empty input with a saved llm_direct is refused end to end', () => {
      const result = api.resolveOptions(preferences({
        lyrics: analyze(''),
        aligner: 'llm_direct',
        splitter: 'llm',
        audioReading: true,
        audioSplit: true,
      }));
      assert.equal(result.valid, false);
      assert.ok(result.error);
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
    });
  });

  describe('resolveOptions: engine choice', () => {
    test('auto with a full LRC resolves to tifa', () => {
      const result = resolve({ aligner: 'auto', lyrics: lyrics({ anchored: true, partial: false }) });
      assert.equal(result.valid, true);
      assert.equal(result.engine, 'tifa');
    });

    test('auto with plain text resolves to sofa', () => {
      const result = resolve({ aligner: 'auto', lyrics: lyrics({ kind: 'txt', anchored: false, partial: false }) });
      assert.equal(result.engine, 'sofa');
    });

    test('explicit aligners keep their own engine', () => {
      const anchored = lyrics({ anchored: true, partial: false });
      assert.equal(resolve({ aligner: 'tifa', lyrics: anchored }).engine, 'tifa');
      assert.equal(resolve({ aligner: 'ctc', lyrics: anchored }).engine, 'ctc');
      assert.equal(resolve({ aligner: 'sofa', lyrics: anchored }).engine, 'sofa');
      assert.equal(resolve({ aligner: 'llm_direct', lyrics: anchored }).engine, 'llm_direct');
      assert.equal(resolve({ aligner: 'llm_import', lyrics: anchored }).engine, 'llm_import');
    });

    test('tifa, ctc, llm_direct and llm_import need sentence anchors', () => {
      const plain = lyrics({ kind: 'txt', anchored: false, partial: false });
      for (const aligner of ['tifa', 'ctc', 'llm_direct', 'llm_import']) {
        const result = resolve({ aligner: aligner, lyrics: plain });
        assert.equal(result.valid, false, aligner + ' must not be submitted without anchors');
        assert.ok(result.error, aligner + ' must explain why it is unavailable');
      }
    });

    test('sofa still runs on plain text', () => {
      const result = resolve({ aligner: 'sofa', lyrics: lyrics({ kind: 'txt', anchored: false, partial: false }) });
      assert.equal(result.valid, true);
      assert.equal(result.engine, 'sofa');
      assert.ok(!result.error);
    });

    test('partial anchors are rejected before the job is submitted', () => {
      for (const aligner of ['auto', 'tifa', 'llm_import']) {
        const result = resolve({ aligner: aligner, lyrics: lyrics({ anchored: false, partial: true }) });
        assert.equal(result.valid, false, aligner + ' must reject partly anchored lyrics');
        assert.ok(result.error);
      }
    });
  });

  describe('resolveOptions: audio assist', () => {
    test('MMS and SOFA never offer audio assist', () => {
      for (const aligner of ['ctc', 'sofa']) {
        const result = resolve({
          aligner: aligner,
          lyrics: lyrics({ anchored: true, partial: false }),
          audioReading: true,
          audioSplit: true,
        });
        assert.equal(result.showAudioReading, false, aligner + ' cannot read audio');
        assert.equal(result.showAudioSplit, false, aligner + ' cannot split from audio');
        assert.equal(result.audioReading, false);
        assert.equal(result.audioSplit, false);
        assert.equal(result.sendsAudio, false);
      }
    });

    test('Japanese-only plus a full LRC on TIFA offers audio reading', () => {
      const result = resolve({
        aligner: 'tifa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: false, hasJapanese: true }),
        audioReading: true,
      });
      assert.equal(result.showAudioReading, true);
      assert.equal(result.audioReading, true);
      assert.equal(result.llm, true);
      assert.equal(result.sendsAudio, true);
      assert.equal(result.valid, true);
    });

    test('auto resolving to tifa also offers audio reading', () => {
      const result = resolve({
        aligner: 'auto',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: false, hasJapanese: true }),
        audioReading: true,
      });
      assert.equal(result.engine, 'tifa');
      assert.equal(result.showAudioReading, true);
      assert.equal(result.audioReading, true);
    });

    test('audio reading is unavailable without sentence anchors', () => {
      const result = resolve({
        aligner: 'auto',
        lyrics: lyrics({ kind: 'txt', anchored: false, partial: false, hasEnglish: false }),
        audioReading: true,
      });
      assert.equal(result.engine, 'sofa');
      assert.equal(result.showAudioReading, false);
      assert.equal(result.audioReading, false);
      assert.equal(result.sendsAudio, false);
      assert.equal(result.llm, false);
    });

    test('audio split needs English subdivision plus the llm splitter on TIFA', () => {
      const anchored = lyrics({ anchored: true, partial: false, hasEnglish: true });
      const allowed = resolve({ aligner: 'tifa', lyrics: anchored, syllables: true, splitter: 'llm', audioSplit: true });
      assert.equal(allowed.showAudioSplit, true);
      assert.equal(allowed.audioSplit, true);
      assert.equal(allowed.sendsAudio, true);
      assert.equal(allowed.llm, true);

      const noSplitter = resolve({ aligner: 'tifa', lyrics: anchored, syllables: true, splitter: 'syllabify', audioSplit: true });
      assert.equal(noSplitter.showAudioSplit, false);
      assert.equal(noSplitter.audioSplit, false);

      const noSyllables = resolve({ aligner: 'tifa', lyrics: anchored, syllables: false, splitter: 'llm', audioSplit: true });
      assert.equal(noSyllables.showAudioSplit, false);
      assert.equal(noSyllables.audioSplit, false);
    });

    test('audio split needs English lyrics even on a fully anchored TIFA job', () => {
      const result = resolve({
        aligner: 'tifa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: false, hasJapanese: true }),
        syllables: true,
        splitter: 'llm',
        audioSplit: true,
      });
      assert.equal(result.showAudioSplit, false);
      assert.equal(result.audioSplit, false);
    });

    test('llm_direct hides the local splitter options and its own audio toggles', () => {
      const result = resolve({
        aligner: 'llm_direct',
        lyrics: lyrics({ anchored: true, partial: false }),
        syllables: true,
        splitter: 'llm',
        audioReading: true,
        audioSplit: true,
      });
      assert.equal(result.engine, 'llm_direct');
      assert.equal(result.showEnglish, false);
      assert.equal(result.showSplitter, false);
      assert.equal(result.showAudioReading, false);
      assert.equal(result.showAudioSplit, false);
      assert.equal(result.audioReading, false);
      assert.equal(result.audioSplit, false);
      assert.equal(result.llm, true);
      assert.equal(result.sendsAudio, true);
      assert.equal(result.language, 'auto');
      assert.equal(result.showLanguage, false);
      assert.equal(result.showPromptProfile, true);
      assert.equal(result.promptProfile, 'fine');
      assert.deepStrictEqual({ ...api.submissionFields(result) }, {
        language: 'auto',
        aligner: 'llm_direct',
        audio_mode: 'lead',
        syllables: 'false',
        english_splitter: 'syllabify',
        llm_audio_reading: 'false',
        llm_audio_split: 'false',
        llm_prompt_profile: 'fine',
      });
    });

    test('web import is direct-like but never requires or sends API settings', () => {
      const result = resolve({
        aligner: 'llm_import',
        lyrics: lyrics({ anchored: true, partial: false }),
        syllables: true,
        splitter: 'llm',
        audioReading: true,
        audioSplit: true,
      });
      assert.equal(result.engine, 'llm_import');
      assert.equal(result.valid, true);
      assert.equal(result.showImport, true);
      assert.equal(result.showPromptProfile, true);
      assert.equal(result.promptProfile, 'fine');
      assert.equal(result.promptReady, true);
      assert.equal(result.language, 'auto');
      assert.equal(result.showLanguage, false);
      assert.equal(result.showEnglish, false);
      assert.equal(result.showSplitter, false);
      assert.equal(result.showAudioReading, false);
      assert.equal(result.showAudioSplit, false);
      assert.equal(result.audioReading, false);
      assert.equal(result.audioSplit, false);
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
      assert.deepStrictEqual({ ...api.submissionFields(result) }, {
        language: 'auto',
        aligner: 'llm_import',
        audio_mode: 'lead',
        syllables: 'false',
        english_splitter: 'syllabify',
        llm_audio_reading: 'false',
        llm_audio_split: 'false',
        llm_prompt_profile: 'fine',
      });
      assert.equal(resolve({ aligner: 'llm_import', lyrics: lyrics({ anchored: false, kind: 'txt' }) }).promptReady, false);
    });

    test('prompt profiles are visible only for direct-like routes and normalize to fine', () => {
      const direct = resolve({ aligner: 'llm_direct' });
      const imported = resolve({ aligner: 'llm_import', promptProfile: 'original' });
      assert.equal(direct.showPromptProfile, true);
      assert.equal(direct.promptProfile, 'fine');
      assert.equal(imported.showPromptProfile, true);
      assert.equal(imported.promptProfile, 'original');
      assert.equal(api.submissionFields(imported).llm_prompt_profile, 'original');
      assert.equal(resolve({ aligner: 'llm_direct', promptProfile: 'unknown' }).promptProfile, 'fine');
      for (const aligner of ['auto', 'tifa', 'ctc', 'sofa']) {
        const result = resolve({ aligner, promptProfile: 'original' });
        assert.equal(result.showPromptProfile, false, aligner + ' must hide the prompt profile');
        assert.ok(!('llm_prompt_profile' in api.submissionFields(result)), aligner + ' must not submit a prompt profile');
      }
    });
  });

  describe('resolveOptions: Mandarin and language plumbing', () => {
    const lookup = loadPureFile(UI_LOGIC);

    test('the resolved plan echoes the provided song language', () => {
      assert.equal(resolve({ language: 'zh' }).language, 'zh');
      assert.equal(resolve({ language: 'zh-en' }).language, 'zh-en');
      assert.equal(resolve({}).language, 'mixed');
      assert.equal(resolve({ language: 'ko' }).language, 'mixed');
    });

    test('Mandarin with sentence anchors opens the same TIFA audio reading', () => {
      const result = resolve({
        language: 'zh',
        aligner: 'tifa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: false, hasJapanese: false, hasChinese: true }),
        audioReading: true,
      });
      assert.equal(result.valid, true);
      assert.equal(result.showAudioReading, true);
      assert.equal(result.audioReading, true);
      assert.equal(result.llm, true);
      assert.equal(result.sendsAudio, true);
    });

    test('Mandarin without anchors stays on SOFA without the audio assist', () => {
      const result = resolve({
        language: 'zh',
        aligner: 'auto',
        lyrics: lyrics({ kind: 'txt', anchored: false, partial: false, hasEnglish: false, hasJapanese: false, hasChinese: true }),
        audioReading: true,
      });
      assert.equal(result.engine, 'sofa');
      assert.equal(result.showAudioReading, false);
      assert.equal(result.sendsAudio, false);
    });

    test('MMS and SOFA still refuse audio assist for Mandarin', () => {
      for (const aligner of ['ctc', 'sofa']) {
        const result = resolve({
          language: 'zh',
          aligner,
          lyrics: lyrics({ anchored: true, partial: false, hasChinese: true, hasJapanese: false }),
          audioReading: true,
          audioSplit: true,
        });
        assert.equal(result.showAudioReading, false, aligner + ' cannot reread Mandarin');
        assert.equal(result.audioReading, false);
        assert.equal(result.sendsAudio, false);
      }
    });

    test('Chinese rows do not switch on the English syllable settings', () => {
      const result = resolve({
        language: 'zh',
        aligner: 'tifa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: false, hasJapanese: false, hasChinese: true }),
        syllables: true,
        splitter: 'llm',
      });
      assert.equal(result.showEnglish, false);
      assert.equal(result.showSplitter, false);
      assert.equal(result.llm, false);
    });

    test('describeLanguages names the shipped combinations', () => {
      const describeLanguages = lookup('describeLanguages');
      assert.equal(describeLanguages({ hasChinese: true, hasJapanese: false, hasEnglish: false }), '中文');
      assert.equal(describeLanguages({ hasChinese: false, hasJapanese: true, hasEnglish: false }), '日语');
      assert.equal(describeLanguages({ hasChinese: false, hasJapanese: false, hasEnglish: true }), '英语');
      assert.equal(describeLanguages({ hasChinese: true, hasJapanese: false, hasEnglish: true }), '中英混唱');
      assert.equal(describeLanguages({ hasChinese: false, hasJapanese: true, hasEnglish: true }), '日英混唱');
      assert.equal(describeLanguages({ hasChinese: true, hasJapanese: true, hasEnglish: false }), '中日混唱');
      assert.equal(describeLanguages({ hasChinese: true, hasJapanese: true, hasEnglish: true }), '中日英混唱');
      assert.equal(describeLanguages({ language: 'auto', effectiveLanguages: ['auto'] }), '待 LLM 识别语言');
      assert.equal(describeLanguages({}), '');
    });

    test('submission fields preserve normal routing and force direct jobs to auto', () => {
      const normal = resolve({ language: 'zh-en', aligner: 'auto' });
      assert.equal(normal.language, 'zh-en');
      assert.equal(normal.showLanguage, true);
      assert.equal(api.submissionFields(normal).aligner, 'auto');
      assert.equal(api.submissionFields(normal).language, 'zh-en');

      const direct = resolve({ language: 'zh', aligner: 'llm_direct' });
      assert.equal(direct.language, 'auto');
      assert.equal(direct.showLanguage, false);
      assert.equal(api.submissionFields(direct).language, 'auto');
    });

    test('audioReadingLabel switches between Mandarin and Japanese', () => {
      const label = lookup('audioReadingLabel');
      assert.equal(label({ hasChinese: false, hasJapanese: true }), 'LLM 听音校对日语读音');
      assert.equal(label({ hasChinese: true, hasJapanese: false }), 'LLM 听音校对中文读音');
      assert.equal(label({ hasChinese: true, hasJapanese: true }), 'LLM 听音校对中文／日语读音');
    });
  });

  describe('resolveOptions: English syllable settings', () => {
    test('no English means no English settings and no API requirement', () => {
      const result = resolve({
        aligner: 'auto',
        lyrics: lyrics({ kind: 'txt', anchored: false, partial: false, hasEnglish: false, hasJapanese: true }),
        syllables: true,
        splitter: 'llm',
      });
      assert.equal(result.showEnglish, false);
      assert.equal(result.showSplitter, false);
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
      assert.equal(result.valid, true);
    });

    test('English lyrics with the llm splitter require the text API but no audio', () => {
      const result = resolve({
        aligner: 'tifa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: true }),
        syllables: true,
        splitter: 'llm',
      });
      assert.equal(result.showEnglish, true);
      assert.equal(result.showSplitter, true);
      assert.equal(result.allowLlmSplitter, true);
      assert.equal(result.llm, true);
      assert.equal(result.sendsAudio, false);
    });

    test('turning syllables off drops the splitter without needing the API', () => {
      const result = resolve({
        aligner: 'tifa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: true }),
        syllables: false,
        splitter: 'llm',
      });
      assert.equal(result.showEnglish, true);
      assert.equal(result.showSplitter, false);
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
    });

    test('MMS cannot use the llm splitter, so it falls back to syllabify', () => {
      const result = resolve({
        aligner: 'ctc',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: true }),
        syllables: true,
        splitter: 'llm',
      });
      assert.equal(result.valid, true, 'the fallback must still be a submittable combination');
      assert.equal(result.splitter, 'syllabify');
      assert.equal(result.allowLlmSplitter, false);
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
    });

    test('SOFA keeps the llm splitter available', () => {
      const result = resolve({
        aligner: 'sofa',
        lyrics: lyrics({ anchored: true, partial: false, hasEnglish: true }),
        syllables: true,
        splitter: 'llm',
      });
      assert.equal(result.allowLlmSplitter, true);
      assert.equal(result.splitter, 'llm');
      assert.equal(result.llm, true);
    });
  });

  describe('resolveOptions: audio route and required models', () => {
    test('audioMode defaults to lead and unknown values normalize to lead', () => {
      assert.equal(resolve({}).audioMode, 'lead');
      assert.equal(resolve({ audioMode: 'garbage' }).audioMode, 'lead');
      assert.equal(resolve({ audioMode: 'vocals' }).audioMode, 'vocals');
      assert.equal(resolve({ audioMode: 'source' }).audioMode, 'source');
    });

    test('source mirrors timing/sources.py for every mode and stem combination', () => {
      assert.equal(resolve({ audioMode: 'lead', hasVocals: false }).source, 'separated_vocals');
      assert.equal(resolve({ audioMode: 'lead', hasVocals: true }).source, 'separated_vocals');
      assert.equal(resolve({ audioMode: 'vocals', hasVocals: false }).source, 'separated_vocals');
      assert.equal(resolve({ audioMode: 'vocals', hasVocals: true }).source, 'provided_vocals');
      assert.equal(resolve({ audioMode: 'source', hasVocals: false }).source, 'original');
      assert.equal(resolve({ audioMode: 'source', hasVocals: true }).source, 'provided_vocals');
    });

    test('every state lists exactly the five required model groups in order', () => {
      const states = [
        {},
        { aligner: 'tifa' },
        { aligner: 'ctc', syllables: false },
        { aligner: 'sofa', audioMode: 'source' },
        { aligner: 'llm_direct' },
        { aligner: 'llm_import' },
        { hasVocals: true, audioMode: 'vocals' },
      ];
      for (const overrides of states) {
        const result = resolve(overrides);
        assert.deepStrictEqual([...result.models], REQUIRED_MODELS, JSON.stringify(overrides) + ' must require all five groups');
      }
      assert.notEqual(resolve({}).models, resolve({}).models, 'each result must carry a fresh models array');
    });

    test('the background flag passes through for the lead-route note', () => {
      assert.equal(resolve({ lyrics: lyrics({ hasBackground: true }) }).hasBackground, true);
      assert.equal(resolve({}).hasBackground, false);
    });
  });

  describe('resolveOptions: result plumbing', () => {
    test('every documented field is present with the right type', () => {
      const result = resolve({});
      const flags = ['showLanguage', 'valid', 'showEnglish', 'showSplitter', 'allowLlmSplitter', 'showAudioReading',
        'showAudioSplit', 'syllables', 'audioReading', 'audioSplit', 'llm', 'sendsAudio', 'showImport',
        'showPromptProfile', 'promptReady', 'hasBackground'];
      for (const key of flags) {
        assert.equal(typeof result[key], 'boolean', key + ' must be a boolean');
      }
      assert.equal(typeof result.engine, 'string');
      assert.ok(result.engine.length > 0, 'engine must name the resolved aligner');
      assert.equal(typeof result.language, 'string');
      assert.equal(typeof result.splitter, 'string');
      assert.equal(typeof result.promptProfile, 'string');
      assert.equal(typeof result.source, 'string');
      assert.equal(typeof result.audioMode, 'string');
      assert.equal(typeof result.error, 'string');
      assert.ok(Array.isArray(result.models), 'models must be an array');
      for (const model of result.models) {
        assert.equal(typeof model, 'string');
      }
    });

    test('draft-era fields are gone from the result', () => {
      const result = resolve({});
      for (const key of ['draft', 'direct', 'separate', 'lead', 'directLead']) {
        assert.ok(!(key in result), key + ' must not exist any more');
      }
    });

    test('the saved preferences object is never modified', () => {
      const input = preferences({ aligner: 'ctc', syllables: true, splitter: 'llm', audioReading: true, audioSplit: true });
      const snapshot = structuredClone(input);
      const result = api.resolveOptions(input);
      assert.deepStrictEqual(input, snapshot, 'resolveOptions must not write back into the form state');
      assert.equal(input.splitter, 'llm', 'the user preference is kept even when it is not usable');
      assert.notEqual(result, input);
    });

    test('a frozen preferences object can be resolved without throwing', () => {
      const input = Object.freeze(preferences({ aligner: 'ctc', splitter: 'llm', hasVocals: true }));
      const result = api.resolveOptions(input);
      assert.equal(result.valid, true);
    });
  });

  describe('resolveOptions with real analyzeLyrics output', () => {
    test('mixed anchored LRC on auto runs on tifa with the text API', () => {
      const info = analyze(FULL_LRC);
      const result = api.resolveOptions(preferences({
        lyrics: info,
        aligner: 'auto',
        syllables: true,
        splitter: 'llm',
        hasVocals: false,
        audioMode: 'lead',
      }));
      assert.equal(result.valid, true);
      assert.equal(result.engine, 'tifa');
      assert.equal(result.llm, true);
      assert.equal(result.sendsAudio, false);
      assert.equal(result.source, 'separated_vocals');
      assert.deepStrictEqual([...result.models], REQUIRED_MODELS);
    });

    test('the same text pasted without times runs on sofa with the text API only', () => {
      const info = analyze(MIXED_TEXT);
      const result = api.resolveOptions(preferences({ lyrics: info, aligner: 'auto', splitter: 'llm' }));
      assert.equal(result.valid, true);
      assert.equal(result.engine, 'sofa');
      assert.equal(result.llm, true);
      assert.equal(result.sendsAudio, false);
    });

    test('a partly anchored LRC is refused before submission', () => {
      const info = analyze(PARTIAL_LRC);
      const result = api.resolveOptions(preferences({ lyrics: info, aligner: 'auto' }));
      assert.equal(result.valid, false);
      assert.ok(result.error);
    });

    test('a JSON background track keeps the five groups and reports hasBackground', () => {
      const info = analyze(JSON_BACKGROUND);
      assert.equal(info.hasBackground, true);
      const result = api.resolveOptions(preferences({ lyrics: info, aligner: 'tifa' }));
      assert.equal(result.hasBackground, true);
      assert.deepStrictEqual([...result.models], REQUIRED_MODELS);
    });

    test('empty input with a saved llm_direct is blocked instead of drafted', () => {
      const info = analyze('');
      const result = api.resolveOptions(preferences({
        lyrics: info,
        aligner: 'llm_direct',
        splitter: 'llm',
        audioReading: true,
        audioSplit: true,
      }));
      assert.equal(result.valid, false);
      assert.ok(result.error);
      assert.equal(result.llm, false);
      assert.equal(result.sendsAudio, false);
      assert.deepStrictEqual([...result.models], REQUIRED_MODELS);
    });
  });
}

if (loadError) {
  test('web/ui-logic.js exposes analyzeLyrics and resolveOptions', () => {
    throw loadError;
  });
} else {
  defineContractTests(loadedApi);
}
