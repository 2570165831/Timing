import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def decode(source: Path, target: Path, rate=16000, *, floating=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_f32le" if floating else "pcm_s16le", str(target)
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise ValueError("音频解码失败：" + proc.stderr[-1500:])
    samples, sr = sf.read(target, dtype="float32")
    if len(samples) < sr / 4:
        raise ValueError("音频过短。")
    if len(samples) > 60 * 60 * sr:
        raise ValueError("单次支持最长 60 分钟音频，请先分段。")
    if not np.isfinite(samples).all() or np.max(np.abs(samples)) < 1e-5:
        raise ValueError("音频无有效声音。")
    return samples, sr


def slice_audio(samples, start, end, sr=16000):
    return samples[max(0, int(start * sr)):min(len(samples), int(end * sr))]


def match_timeline(samples, expected_frames):
    """Allow only the rounding of a resampler endpoint, never shift a stem."""
    delta = expected_frames - len(samples)
    if abs(delta) > 2:
        raise ValueError('人声轨与原曲长度不一致；请提供同起点、同时长的音轨。')
    if delta > 0:
        return np.pad(samples, (0, delta))
    return samples[:expected_frames] if delta < 0 else samples


def validate_stems(source, lead, residual):
    """Validate timeline and reconstruction, not separation quality."""
    reference = sf.info(source)
    signature = (reference.frames, reference.samplerate, reference.channels)
    for path in (lead, residual):
        info = sf.info(path)
        if (info.frames, info.samplerate, info.channels) != signature:
            raise RuntimeError('主唱分离改变了音频时间轴，已停止打轴。')
    energies = np.zeros(3, dtype=np.float64)
    reconstruction_error = 0.
    with sf.SoundFile(source) as original, sf.SoundFile(lead) as main, sf.SoundFile(residual) as rest:
        while original.tell() < len(original):
            blocks = [f.read(262144, dtype='float32', always_2d=True) for f in (original, main, rest)]
            if any(not np.isfinite(block).all() for block in blocks):
                raise RuntimeError('主唱分离产生非有限采样，已停止打轴。')
            energies += [np.sum(block.astype(np.float64)**2) for block in blocks]
            reconstruction_error = max(reconstruction_error, float(np.max(np.abs(blocks[0]-blocks[1]-blocks[2]))))
    if energies[0] <= 0 or energies[1] <= 0:
        raise RuntimeError('主唱分离返回空信号，未继续生成时间轴。')
    if reconstruction_error > 2e-5:
        raise RuntimeError('主唱与剩余轨未能重建输入音频，已停止打轴。')
    return {'frames': reference.frames, 'sample_rate': reference.samplerate,
            'channels': reference.channels, 'timeline_preserved': True,
            'reconstruction_max_error': reconstruction_error,
            'lead_energy_ratio': float(energies[1]/energies[0]),
            'residual_energy_ratio': float(energies[2]/energies[0])}
