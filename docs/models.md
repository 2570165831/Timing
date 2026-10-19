# 模型清单与安装策略

## 五组必需模型

安装固定下载下表五组，没有可选配置；任何模式都不会触发额外下载。SOFA 组内包含日语、英语与中文（普通话）资源，中文与日语、英语共用同一组，不单独安装；中文按普通话处理，不覆盖粤语。

| ID | 模型 / 本机目录 | 用途 | 本机现有约 GiB |
| --- | --- | --- | --- |
| `ctc` | MMS-300M / `ctc` | 句段定位；`ctc` 模式逐句对齐 | 1.18 |
| `tifa` | TIFA-1.0-ST / `tifa` | 中日英歌声精细对齐 | 0.21 |
| `singing` | SOFA 日中英 / `sofa` | 纯文本歌词对齐（含普通话） | 1.84 |
| `bs_lead` | BS-Roformer Karaoke / `bs-karaoke` | “只保留主唱”：从原曲或导入轨提取主唱 | 0.19 |
| `separator` | Kim MelBand vocals / `separator/melband-roformer-kim-vocals` | “保留全部人声”与背景歌词：去伴奏、保留和声 | 0.85 |

表中数字是整理时本机目录占用快照（合计约 4.27 GiB），包含配套资源与原始模型包，不是承诺下载量或显存需求；实际占用以 `--inventory` 为准。首次 Python / CUDA 依赖的空间另计。

## 安装与查看

```powershell
# 安装全部五组（setup.ps1 默认执行同样的安装）
.\.venv\Scripts\python.exe -X utf8 scripts\download_models.py

# 只查看，不下载
.\.venv\Scripts\python.exe -X utf8 scripts\download_models.py --list
.\.venv\Scripts\python.exe -X utf8 scripts\download_models.py --dry-run
.\.venv\Scripts\python.exe -X utf8 scripts\download_models.py --inventory
```

安装器逐组运行 `scripts/download_*.py`，重复运行可以补装、校验或修复缺失的运行依赖，不清空模型目录。固定来源哈希 / revision 保持不变；目录存在不等于完整性校验通过。

网络中断会保留 `.part` 临时文件，服务器支持时按字节续传；不支持时重新下载该文件。文件通过大小、哈希或压缩包校验后才替换正式文件，已校验的资源会复用。单个请求和模型组均有有限次数的重试，最终有任何组失败，安装命令都会返回失败。各组日志保存在 `.runtime/install/models/`，状态保存在 `.runtime/install/models-status.json`；状态记录只用于排错，重跑仍会检查实际文件。

## 缺项时

模型缺失时对应路线会报错并提示补装，不会自动下载其它模型，也不会在分离失败时静默换模型。五组都对功能有贡献：

- 缺 `ctc` 或 `tifa`：逐句时间路线不可用。
- 缺 `singing`：纯文本路线不可用。
- 缺 `bs_lead`：默认的“只保留主唱”不可用。
- 缺 `separator`：“保留全部人声”不可用；背景歌词需要导入完整人声替代。

`--inventory` 只读目录，不上传数据、不加载 GPU，也不提供自动删除操作。模型一律下载到本机，源码仓库排除整个 `models/`，无需为了提交代码而删除权重。

模型来源与许可限制见 [THIRD_PARTY.md](../THIRD_PARTY.md)。代码 MIT 不代表所有权重都能商用。
