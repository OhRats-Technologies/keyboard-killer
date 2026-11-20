"""Unit tests for the geometric 3-D gaze model, online tracker, chase detector, and EDID wiring."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from keyboard_killer.platform.base import DisplayLayout, MonitorInfo
from keyboard_killer.platform.linux.display import _attach_edid_mm, read_drm_edid_mm
from keyboard_killer.ui.gaze_region import calibration_region
from keyboard_killer.vision.calibrator import load_gaze_model
from keyboard_killer.vision.geo_gaze import (
    ChaseDetector,
    Fixation,
    FixationSegmenter,
    GeoGazeModel,
    GeoGazeTracker,
    MonitorSpec,
    fit_from_dataset,
    monitor_specs_from_layout,
    place_monitors,
)


def _synth_calib_data(
    n_anchors: int = 9,
    frames_per: int = 12,
    stereo: bool = False,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate clean synthetic 20-D stereo features from a known pinhole eye/head geometry."""
    rng = np.random.default_rng(seed)
    w_px, h_px = 1920.0, 1080.0
    w_cm, h_cm = 53.1, 29.9
    head_m0 = np.array([26.5, 12.0, -60.0])
    cam_pos = np.array([26.5, -1.5, 0.0])
    cam_rot = np.diag([-1.0, -1.0, 1.0])
    head_rot = np.diag([-1.0, -1.0, 1.0])

    xs = np.linspace(0.08, 0.92, 3)
    ys = np.linspace(0.10, 0.90, 3)
    pts = [(rx * w_px, ry * h_px) for ry in ys for rx in xs][:n_anchors]

    X_list, Y_list = [], []
    for px, py in pts:
        tgt_m0 = np.array([px / w_px * w_cm, py / h_px * h_cm, 0.0])
        for _ in range(frames_per):
            jitter = rng.normal(0.0, 0.2, 3)
            hpos = head_m0 + jitter
            eye_m0 = hpos + head_rot @ np.array([0.0, 2.6, 1.6])
            d_m0 = tgt_m0 - eye_m0
            d_m0 /= np.linalg.norm(d_m0)
            d_h = head_rot.T @ d_m0
            th = math.atan2(d_h[0], d_h[2])
            ph = math.asin(max(-1.0, min(1.0, d_h[1])))
            dx = th / 0.8 + rng.normal(0.0, 0.005)
            dy = -ph / 0.8 + rng.normal(0.0, 0.005)
            t_c = cam_rot.T @ (hpos - cam_pos)
            depth = max(15.0, abs(t_c[2]))
            yaw_los = -0.65 * math.atan2(t_c[0], depth)
            pitch_los = -0.65 * math.atan2(t_c[1], depth)
            blk1 = np.array([dx, dy, dx, dy, yaw_los, pitch_los, 0.0, t_c[0] / 100.0, t_c[1] / 100.0, t_c[2] / 100.0])
            if stereo:
                cam2_pos = np.array([50.0, 25.0, -8.0])
                t_c2 = cam_rot.T @ (hpos - cam2_pos)
                d2 = max(15.0, abs(t_c2[2]))
                blk2 = np.array([
                    dx + rng.normal(0.0, 0.005),
                    dy + rng.normal(0.0, 0.005),
                    dx + rng.normal(0.0, 0.005),
                    dy + rng.normal(0.0, 0.005),
                    -0.65 * math.atan2(t_c2[0], d2),
                    -0.65 * math.atan2(t_c2[1], d2),
                    0.0,
                    t_c2[0] / 100.0,
                    t_c2[1] / 100.0,
                    t_c2[2] / 100.0,
                ])
            else:
                blk2 = blk1.copy()
            X_list.append(np.concatenate([blk1, blk2]))
            Y_list.append((px, py))
    return np.asarray(X_list), np.asarray(Y_list)


def _dual_layout() -> DisplayLayout:
    return DisplayLayout(
        monitors=(
            MonitorInfo("DP-1", 0, 0, 1920, 1080, 1920, 1080, 1.0, True, width_mm=531.0, height_mm=299.0),
            MonitorInfo("HDMI-A-1", 1920, 0, 2560, 1440, 2560, 1440, 1.0, False, width_mm=597.0, height_mm=336.0),
        ),
        total_width=4480,
        total_height=1440,
    )


def test_geo_model_fit_predict_save_load_and_layout(tmp_path: Path) -> None:
    X, Y = _synth_calib_data(stereo=False)
    layout = _dual_layout()
    model = fit_from_dataset(X, Y, layout, ref_connector="DP-1", cam1_by_id="cam1", cam2_by_id="cam1")

    assert model.camera_mode == "mono"
    assert model.n_train_samples == len(X)
    assert len(model.monitors) == 2

    preds = model.predict_batch(X)
    med_err = float(np.median(np.linalg.norm(preds - Y, axis=1)))
    assert med_err < 30.0

    # Save & load via unified loader
    p = tmp_path / "calib.json"
    model.save(p)
    loaded = load_gaze_model(p, layout=layout)
    assert isinstance(loaded, GeoGazeModel)
    assert loaded.n_train_samples == len(X)
    assert np.allclose(loaded.predict_batch(X[:5]), preds[:5])

    # gaze_region covers full virtual desktop for geo-1 models
    region = calibration_region(p, layout)
    assert (region.width, region.height) == (layout.total_width, layout.total_height)

    # recenter_to returns (dx, dy) tuple
    dx, dy = loaded.recenter_to(960.0, 540.0, X[0])
    assert isinstance(dx, float) and isinstance(dy, float)
    rx, ry = loaded.predict(X[0])
    assert math.hypot(rx - 960.0, ry - 540.0) < 2.0


def test_geo_model_stereo_single_camera_fallback() -> None:
    X, Y = _synth_calib_data(stereo=True)
    layout = _dual_layout()
    model = fit_from_dataset(X, Y, layout, ref_connector="DP-1", cam1_by_id="c1", cam2_by_id="c2")
    assert model.camera_mode == "stereo"
    assert len(model.blocks) == 2

    # Zero out cam2 block (simulating head turned away from cam2)
    x_cam1_only = X[0].copy()
    x_cam1_only[10:] = 0.0
    p_both = np.asarray(model.predict(X[0]))
    p_one = np.asarray(model.predict(x_cam1_only))
    assert np.all(np.isfinite(p_one))
    assert np.linalg.norm(p_both - p_one) < 60.0


def test_place_monitors_handles_portrait_rotation_and_edid_mm() -> None:
    layout = DisplayLayout(
        monitors=(
            MonitorInfo("DP-1", 1080, 0, 1920, 1080, 1920, 1080, 1.0, True, width_mm=531.0, height_mm=299.0),
            MonitorInfo("DP-2", 0, 0, 1080, 1920, 1080, 1920, 1.0, False, width_mm=531.0, height_mm=299.0),
        ),
        total_width=3000,
        total_height=1920,
    )
    specs = monitor_specs_from_layout(layout)
    mons = place_monitors(specs, "DP-1", np.array([26.5, 12.0, -60.0]))
    portrait = next(m for m in mons if m.connector == "DP-2")
    # Portrait output swaps landscape EDID mm -> width_cm < height_cm
    assert portrait.width_cm == pytest.approx(29.9)
    assert portrait.height_cm == pytest.approx(53.1)


def test_read_drm_edid_mm_parses_dtd_and_skips_disconnected(tmp_path: Path) -> None:
    # Build minimal valid 128-byte EDID with DTD = 598x336mm
    edid = bytearray(128)
    edid[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    edid[21], edid[22] = 60, 34  # basic cm fallback
    w_mm, h_mm = 598, 336
    edid[66] = w_mm & 0xFF
    edid[67] = h_mm & 0xFF
    edid[68] = ((w_mm >> 8) << 4) | (h_mm >> 8)

    d_conn = tmp_path / "card0-HDMI-A-1"
    d_conn.mkdir()
    (d_conn / "status").write_text("connected\n")
    (d_conn / "edid").write_bytes(bytes(edid))

    d_disc = tmp_path / "card0-DP-3"
    d_disc.mkdir()
    (d_disc / "status").write_text("disconnected\n")
    (d_disc / "edid").write_bytes(bytes(edid))

    mm = read_drm_edid_mm(tmp_path)
    assert mm == {"HDMI-A-1": (598.0, 336.0)}

    layout = DisplayLayout(
        monitors=(MonitorInfo("HDMI-A-1", 0, 0, 1920, 1080, 1920, 1080, 1.0, True),),
        total_width=1920,
        total_height=1080,
    )
    attached = _attach_edid_mm(layout, mm)
    assert (attached.monitors[0].width_mm, attached.monitors[0].height_mm) == (598.0, 336.0)


def test_chase_detector_catches_loop_and_rejects_reading() -> None:
    feat = np.zeros(20)
    # 1. Closed upward-left chase loop: A -> B -> C -> A
    det = ChaseDetector(px_per_deg=40.0)
    b = np.array([-80.0, -60.0])  # 2.5 deg bias
    p0 = np.array([900.0, 500.0])
    seq = [
        Fixation(0.0, 0.5, p0, feat, 15, p0),
        Fixation(0.55, 0.85, p0 + b, feat, 10, p0 + b),
        Fixation(0.90, 1.20, p0 + 2 * b, feat, 10, p0 + 2 * b),
        Fixation(1.25, 1.75, p0 + np.array([4.0, -3.0]), feat, 15, p0),
    ]
    labels = []
    for f in seq:
        labels.extend(det.push(f))
    assert det.confirmed_chains == 1
    assert any(lab.source == "chase" for lab in labels)

    # 2. Left-to-right text reading should NOT trigger chase
    det_read = ChaseDetector(px_per_deg=40.0)
    step = np.array([90.0, 4.0])
    for i in range(5):
        c = np.array([300.0, 400.0]) + i * step
        assert det_read.push(Fixation(i * 0.35, i * 0.35 + 0.3, c, feat, 9, c)) == []
    assert det_read.confirmed_chains == 0


def test_tracker_ekf_adapts_to_click_bias() -> None:
    X, Y = _synth_calib_data(stereo=False)
    layout = _dual_layout()
    model = fit_from_dataset(X, Y, layout, ref_connector="DP-1")
    tracker = GeoGazeTracker(model, enable_chase=False, enable_ekf=True)

    # Simulate a +120px X bias (camera bump) and feed 4 clicks
    bias = np.array([120.0, -60.0])
    before = np.linalg.norm(tracker.predict_raw(X[0]) - (Y[0] + bias))
    for i in range(4):
        idx = i * 12
        tracker.add_label(X[idx], Y[idx] + bias, ts=float(i), source="click", weight=1.0)
    after = np.linalg.norm(tracker.predict_raw(X[0]) - (Y[0] + bias))
    assert after < before * 0.45
