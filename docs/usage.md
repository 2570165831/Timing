# 使用说明

## 歌词和音轨

歌词为必填：填写正确歌词，或导入 UTF-8 TXT / LRC / JSON。推荐 LRC，每行代表一次真实演唱；重复副歌要完整重复填写：

```text
[00:01.20] 青い箱をひらく
[00:05.80] Paper lanterns drift away
```

LRC 下一行起点决定上一行输入范围，最后一句以音频结束为上限。时间用于定位，不保证歌词恰好在该处开始或结束。纯文本每行一句，自动使用 SOFA。歌词文件导入后填入文本框，提交以文本框为准。

音频预处理三选一：

- 只保留主唱（默认）：用 BS-Roformer 去掉伴奏，并尽量去掉和声；输入可以是原曲，也可以是导入的主唱 / 人声轨。
- 保留全部人声：用 Kim 去掉伴奏、保留主唱和和声；已导入人声时直接使用导入轨。
- 直接使用所选音轨：不改动原曲或导入轨。

导入的音轨必须与原曲同起点、同时长。分离失败会报错，不会静默改用其他轨。所有模式共用同一套五组本地模型，安装后没有按功能补装步骤；缺项时页面会提示补装命令。

## 命令行

## 语言选择

网页歌词区的“歌曲语言”与 CLI `--language` 使用同一组取值：

| 取值 | 含义 |
| --- | --- |
| `mixed` | 默认；日语 / 英语 / 日英混唱 |
| `ja` / `en` | 日语 / 英语 |
| `zh` | 中文（普通话） |
| `zh-en` | 中英混唱 |
| `auto` | 仅用于 JSON 行级，表示继承整首选择 |

本地方案无法仅凭汉字区分中文与日语：只有整首或该行选择 `zh` / `zh-en` 时，汉字才按普通话处理；其他模式沿用日语读法。中文指普通话，不处理粤语。本地方案的行级 `language` 优先于整首选择。“LLM 整曲识别”和“网页 LLM（导入结果）”忽略这些语言设置，使用 LLM 根据完整音轨判断的分段语言；页面会隐藏语言选择。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m timing "song.flac" --lyrics "song.lrc" --output "song.ttml" --language mixed
```

| 参数 | 用途 |
| --- | --- |
| `--lyrics` | 必填；UTF-8 TXT / LRC / JSON |
| `--aligner auto` | 默认；完整句段时间用 TIFA，纯文本用 SOFA |
| `--aligner tifa` / `ctc` | 都需要逐句 LRC / JSON；`ctc` 为 MMS 逐句对齐 |
| `--aligner sofa` | SOFA 歌声对齐 |
| `--aligner llm_direct` | 一次发送完整音轨和歌词，LLM 自动判断语言、读音和拆分；需逐句时间与音频兼容接口 |
| `--aligner llm_import --llm-result result.json` | 使用网页 LLM 返回的 JSON，不请求接口，不需要 Key |
| `--export-llm-prompt prompt.txt` | 将全部歌词和输出规则写入提示词 TXT 后退出；只需 `--lyrics` |
| `--llm-prompt-profile fine` / `original` | LLM 日语拆分规则；默认 `fine` 细拆，`original` 保留适度合并的旧版；适用于整曲 API 与提示词导出 |
| `--audio-mode lead` | 默认；去掉伴奏、尽量去掉和声（BS） |
| `--audio-mode vocals` | 去掉伴奏、保留主唱和和声（Kim） |
| `--audio-mode source` | 直接使用原曲或导入轨 |
| `--vocals vocals.wav` | 导入同起点同时长的人声 / 原声轨 |
| `--word-level` | 英语保留整词，不细分候选音节 |
| `--english-splitter syllabify` / `pyphen` / `llm` | 英语候选音节方案 |
| `--language mixed` / `ja` / `en` / `zh` / `zh-en` | 整首语言；默认 `mixed` 覆盖日语 / 英语 / 日英混唱，`zh` 为普通话，`zh-en` 为中英混唱 |

本地方案的 `--language` 决定文字与读音处理：`zh` / `zh-en` 时汉字按普通话（拼音）处理，其余模式汉字按日语读法。`llm_direct` / `llm_import` 不使用此设置，导出语言来自 LLM 实际返回的分段。源文件、歌词和输出路径不能相互覆盖。CLI 中间输出保存在 `<输出文件名>.timing/`。

中英混唱示例：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m timing "song.flac" --lyrics "song.lrc" --output "song.ttml" --language zh-en
```

## 伴唱、对唱和重叠

用 JSON 明确每行范围及对应关系；以下均为演示文本：

```json
[
  {"text":"青い箱", "start":10.0, "end":15.0, "agent":"v1", "language":"ja"},
  {"text":"paper lantern", "start":14.0, "end":19.0, "agent":"v2", "language":"en"},
  {"text":"ah", "start":12.0, "end":15.0, "role":"bg", "parent":0}
]
```

`parent` 是主歌词行从 0 开始的下标；`agent` 为 `v1` / `v2`，`role` 为 `main` / `bg`。可附加 `translation` 和 `roman`。行级 `language` 接受 `auto` / `mixed` / `ja` / `en` / `zh` / `zh-en`，显式值优先于整首 `--language`；`zh` 为普通话。例如中文行写 `{"text":"今天很好","start":10.0,"end":13.0,"language":"zh"}`，英文行写 `"language":"en"`。显式结构不代表模型自动识别或分离不同歌手。

背景歌词（`role=bg`）在“只保留主唱”模式下对齐到 Kim 提取的完整人声，或导入的完整人声；没有原曲回退，也不能把“原曲减主唱”的剩余轨当作纯伴唱。“保留全部人声”和“直接使用所选音轨”模式使用该模式选定的音轨。

## 读音与候选音节

在 `dictionaries/pronunciations.json` 中填 `ja`（词到假名字符串）、`en`（词到音素数组）和 `zh`（汉字或词组到拼音）。例如 `"zh": {"重": "chong2", "重庆": "chong2 qing4"}`；词组需每字一个音节，单字设置优先。TIFA、MMS、SOFA 都会使用这些中文设置，听音辅助也会让它们优先；LLM 自主分段模式使用接口给出的读音。拼音可带声调数字或声调符号，字典匹配时会去掉声调。不修改原文或制造时间。表中整数提供读音候选，特殊年份 / 编号仍应校对。中文按普通话处理：TIFA 与 SOFA 各用自己的中文音素词典，MMS 使用无调拼音标签；不覆盖粤语。词典不支持的唱法会明确报错，可指定受支持的读音后重试。

`syllabify` 使用选中的音素，`pyphen` 使用拼写候选；无法安全映射时保留整词。连续元音或一个不可分读音可以作为整体，不按字数平均分配时间。方案记录在 `alignment.json`，结构正确不等于实际唱法正确。

## 网页 LLM 导入

两种 LLM 入口都有“日语拆分”选择：**细拆（默认）**要求日语全曲按能独立对应读音的汉字 / 假名单位拆分；**适度合并（旧版）**允许日语普通连贯段落适度合并，仍要求长音和每行末尾细拆。两份模板独立保留，英语统一要求逐词分开，词内可按演唱需要拆音节，禁止把半个词与相邻单词合并。网页模式更新或切换提示词后需重新生成并让模型返回新结果；已导入 JSON 始终采用它实际返回的分段，切换选项不会改写旧结果。CLI 选择旧版可加 `--llm-prompt-profile original`。

只有网页端的音频模型也能使用，应用不需要它的 API：

1. 填写逐句 LRC / 带完整时间的 JSON，选择“网页 LLM（导入结果）”。生成提示词，然后复制或下载。提示词已经包含全部歌词、每次演唱的行 ID、英语可用音素和输出格式。
2. 在模型网页上传同一首完整录音，粘贴提示词。保留前奏、间奏、尾奏及原始起点；有和声歌词时，上传的录音要包含和声。也可上传与原曲同起点、同时长的完整人声轨。
3. 将返回的完整 JSON 粘贴回来，或导入 UTF-8 JSON 文件。可以先点“检查结果”，再生成 TTML。所有检查和计时都在本机进行，不请求 LLM，也不会用另一个接口修正结果。

返回的每段包含 `text`、`lang`、`reading`，由网页模型决定中文 / 日语 / 英语语言、读音及显示分段。应用保留它的选择，只把假名 / 拼音 / 英语音素转为 TIFA 可用的音素结构；不再查词决定读法或重新分词。具体时间仍由本地 MMS / TIFA 计算。中日英混唱也不需手选语言。

结果必须包含 `request_id` 和全部行 ID；`parts` 拼接须与原歌词完全相同，不接受生成的时间、改写文字、漏行、重复 ID 或不支持的读音。某行听不清可明确返回 `parts:[]`，该行会保留整句 LRC 范围供人工补轴。格式错误会在分离和计时前说明行号，修好后再导入。歌词或逐句范围改变后需重新生成提示词；相同歌词结果可用于对比不同本地音轨，绑定标识不验证音频内容。JSON 文件上限 16 MiB。提示词和导入结果不保存到浏览器存储，任务报告会保存实际采用的拆分与读音。

命令行两步使用，无需接口配置：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m timing --lyrics song.lrc --export-llm-prompt prompt.txt
.\.venv\Scripts\python.exe -X utf8 -m timing song.flac --lyrics song.lrc --aligner llm_import --llm-result result.json --output song.ttml
```

## 可选 LLM 接口

在页面填写 Base URL、模型 ID 和 Key。接口采用 Chat Completions；默认不强制 JSON 模式或采样参数。可自定义 Key 头名称和 `auto` / `bearer` / `raw` 发送格式。`Authorization` 在自动格式下加 Bearer，其他头默认原样发送。

- 英语 LLM 拆分：支持 TIFA / SOFA，保持原字母与已选音素的连续对应，不产生时间。文字模式按任务去重批量请求。
- 日语 / 中文听音读音（中文结果为拼音）、英语听音拆分：需完整 LRC / JSON 与 TIFA；英语拆分还需英语细分和 `llm` 方案。实际歌词和所用音轨的片段发送到远端。
- `--aligner llm_direct`：一次发送完整音轨和全部歌词，LLM 判断每段中日英语言、实际读音及拆分；同一句中也能混用三种语言。不使用手选语言、本地词语读音和英语拆分开关。仍需完整逐句 LRC / JSON，歌词不改写；具体时间由本地 MMS + TIFA 预测。

整曲识别不再按 8 行 / 45 秒分批，也不自动修正请求。接口超时、输出截断或格式错误不会自动重发；可用行继续对齐，缺漏或无效的行保留整句 LRC 范围，标为待补轴。一次请求读取超时为 600 秒；完整音轨编码成 16 kHz 单声道 PCM WAV，不删前奏、间奏和尾奏，保留原曲时间线。仅主歌词时发送所选处理轨；有背景歌词时发送同时含主唱和和声的完整人声轨（直接使用音轨模式则沿用所选轨），本地计时仍分别使用对应音轨。

普通 5 分钟音轨的 WAV 约 9.2 MiB，Base64 约 12.2 MiB。应用对编码音频设有 64 MiB 的本地上限，整曲响应上限为 16 MiB；音频超出本地上限时不发送请求，保留整句时间供手工补轴。接口自己的大小、时长、输入格式和输出长度限制可能更严格。请选能接受整曲输入、足够长 JSON 输出的音频模型；应用不会因接口拒绝而偷偷改成逐句多次请求。

音频使用 `messages[].content[].input_audio.data` 中的 WAV data URL。只有文字接口兼容不代表支持此音频格式；固定词测试也不能验证音频能力。远端服务的可用性和质量需自行确认。

API Key 通过页面或 `TIMING_LLM_API_KEY` 环境变量提供；不要写入源码或命令参数。CLI 连接参数示例：

```text
--english-splitter llm --llm-base-url https://provider.example/v1 --llm-model MODEL_ID
--llm-api-key-header x-api-key --llm-api-key-format raw
```

环境变量还支持 `TIMING_LLM_BASE_URL`、`TIMING_LLM_MODEL`、`TIMING_LLM_JSON_MODE`、`TIMING_LLM_API_KEY_HEADER`、`TIMING_LLM_API_KEY_FORMAT`。没有自动加载 `.env` 的功能；请在当前终端中设置环境变量。

思考参数可选跟随平台 / 开启 / 关闭。跟随平台不发额外参数；开启时选择 `reasoning_effort`、`enable_thinking` 或 `thinking` 格式，按接口文档填写强度。后两种格式需要纯开关时留空强度。CLI 支持 `--llm-thinking-mode`、`--llm-thinking-format`、`--llm-reasoning-effort`，对应环境变量前缀 `TIMING_LLM_`。参数不匹配会报错，不会静默删掉重试。

提示词在 `timing/prompts/`。对齐记录保存实际映射、请求数、参数和回退原因，不保存 Key、音频 Base64 或思考正文。读音和拆分校验只说明映射结构合格，不证明时间更准确。

## 结果和恢复

下载 TTML、JSON 报告及本次分离产生的音轨。主唱提取的剩余轨包含伴奏和和声，只供试听；背景歌词使用 Kim 完整人声或导入的完整人声，不使用剩余轨。分离后的采样点和重建检查只验证时间线完整性。

`partial_timing_lines` 列出需补轴的单位，`manual_timing_lines` 列出整句占位，`export_placeholders` 记录 TTML 的零时长编辑标记。保留的有效单位仍使用模型原生边界；标记不能视作已测量的字时长。

页面刷新可恢复最近任务的查询；应用重启会将尚未结束的任务标为中断，需重新提交。进度持久化对短暂文件占用有重试和恢复副本；若整个目录不可写，结果写入仍可能失败。数据在 `data/`，不会自动上传或清理。
