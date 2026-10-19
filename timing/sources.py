"""Prepare one explicit audio route for every alignment model."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import decode, match_timeline
from .bs_lead import separate as separate_bs_lead
from .separation import separate_vocals


@dataclass
class AudioSources:
    working: np.ndarray
    rate: int
    main_path: Path
    background: np.ndarray
    background_path: Path
    source: str
    separation: dict | None = None
    lead_separation: dict | None = None


def prepare_sources(audio_path, audio, rate, out_dir, *, vocals_path=None,
                    audio_mode='lead', has_background=False, log=print):
    selected_path, selected = audio_path, audio
    source = 'original'
    if vocals_path is not None:
        log('使用导入的人声轨，检查起点与总时长…')
        selected, _ = decode(vocals_path, out_dir / 'imported.wav')
        selected = match_timeline(selected, len(audio))
        selected_path, source = vocals_path, 'provided_vocals'

    full_path, full, separation = selected_path, selected, None
    # Kim removes accompaniment and retains all voices. It is also the bg source
    # when the main lyrics use a BS lead stem from the original mix.
    if vocals_path is None and (audio_mode == 'vocals' or (audio_mode == 'lead' and has_background)):
        full_path, separation = separate_vocals(audio_path, out_dir, log)
        full, _ = decode(full_path, out_dir / 'full_vocals.wav')
        full = match_timeline(full, len(audio))

    lead_info = None
    if audio_mode == 'lead':
        main_path, lead_info = separate_bs_lead(selected_path, out_dir, log)
        working, _ = decode(main_path, out_dir / 'vocals.wav')
        working = match_timeline(working, len(audio))
        source = 'separated_vocals'
    elif audio_mode == 'vocals':
        main_path, working = full_path, full
        if vocals_path is None:
            source = 'separated_vocals'
        # Keep a consistent downloadable alignment waveform for processed/imported tracks.
        working, _ = decode(main_path, out_dir / 'vocals.wav')
        working = match_timeline(working, len(audio))
    else:
        main_path, working = selected_path, selected
        if vocals_path is not None:
            working, _ = decode(main_path, out_dir / 'vocals.wav')
            working = match_timeline(working, len(audio))
    return AudioSources(working, rate, main_path, full, full_path, source, separation, lead_info)
