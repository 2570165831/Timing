"""Offline vocal separation with an explicit model lifetime and unchanged timeline."""
import gc
import json
import subprocess
from pathlib import Path

import soundfile as sf

from .config import SEPARATOR_DIR, SEPARATOR_NAME


def separator_ready():
    folder = SEPARATOR_DIR / SEPARATOR_NAME
    checkpoint = folder / "MelBandRoformer.ckpt"
    return (SEPARATOR_DIR / "assets.json").is_file() and checkpoint.is_file() and \
        checkpoint.stat().st_size == 913106900 and (folder / "config_vocals_mel_band_roformer.yaml").is_file()


def separate_vocals(source: Path, output_dir: Path, log=print):
    import torch
    from mel_band_roformer import MelBandRoformerSession, ensure_model_assets

    if not torch.cuda.is_available():
        raise RuntimeError("自动人声分离需要 CUDA GPU，请先运行 setup.ps1。")
    if not separator_ready():
        raise RuntimeError("人声分离模型尚未下载完整，请运行 scripts/download_separator.py。")
    # download_missing=False is deliberate: user audio never triggers downloads.
    checkpoint, config = ensure_model_assets(SEPARATOR_NAME, models_dir=SEPARATOR_DIR, download_missing=False)
    inputs = output_dir / "separation-input"
    outputs = output_dir / "stems"
    inputs.mkdir(parents=True, exist_ok=True)
    mix = inputs / "mix.wav"
    log("准备立体声音频，保留原曲起点与完整时长…")
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_f32le", str(mix),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise ValueError("分离音频准备失败：" + proc.stderr[-1500:])
    input_info = sf.info(mix)
    log("加载 MelBand RoFormer Kim 人声模型…")
    session = MelBandRoformerSession(model_name=SEPARATOR_NAME, model_path=checkpoint,
                                     config_path=config, device="cuda:0", progress=False)
    try:
        session.load()
        log("正在 GPU 分离人声与伴奏…")
        manifest = session.infer(inputs, store_dir=outputs, output_format="wav_float32")
        vocal_outputs = [Path(entry["output_path"]) for entry in manifest if entry["output_id"] == "vocals"]
        if len(vocal_outputs) != 1:
            raise RuntimeError("分离器未返回唯一的人声轨。")
        result = vocal_outputs[0]
        output_info = sf.info(result)
        if (input_info.frames, input_info.samplerate, input_info.channels) != \
                (output_info.frames, output_info.samplerate, output_info.channels):
            raise RuntimeError("分离器改变了时间轴或采样格式，已停止打轴。")
        assets = json.loads((SEPARATOR_DIR / "assets.json").read_text(encoding="utf-8"))
        info = {"model": SEPARATOR_NAME, "checkpoint_sha256": assets["sha256"],
                "sample_rate": output_info.samplerate, "frames": output_info.frames,
                "timeline_preserved": True}
        (output_dir / "separation.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        log("人声分离完成，释放分离模型显存，开始打轴。")
        return result, info
    except torch.cuda.OutOfMemoryError as exc:
        raise RuntimeError("人声分离显存不足，请关闭占用显存的程序，或选择“直接使用音轨”后重试。") from exc
    finally:
        session.close()
        gc.collect()
        torch.cuda.empty_cache()
