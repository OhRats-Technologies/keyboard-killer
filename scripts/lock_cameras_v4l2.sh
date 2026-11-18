#!/usr/bin/env bash
set -euo pipefail

echo "Locking V4L2 camera exposure & gain via Python ioctl..."
uv run python -c "
from keyboard_killer.config import AppConfig
from keyboard_killer.vision.v4l2_config import lock_v4l2_controls

cfg = AppConfig.load()
for name, path, exp, gain in [
    ('cam1', cfg.cam1_by_id, cfg.cam1_exposure, cfg.cam1_gain),
    ('cam2', cfg.cam2_by_id, cfg.cam2_exposure, cfg.cam2_gain),
]:
    applied, unsupported = lock_v4l2_controls(path, exposure_absolute=exp, gain=gain, manual_exposure=True)
    print(f'{name} ({path}): applied={applied}, unsupported={unsupported}')
"
