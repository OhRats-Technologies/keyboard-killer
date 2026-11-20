"""Open-loop static accuracy per posture (no cursor feedback): per-fixation mean error in degrees.

Usage: uv run python posture_sweep.py [rig ...] [--seeds N]
"""
from __future__ import annotations
import argparse, math, multiprocessing, os, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from gazesim import DT, Subject, World, make_rig, run_calibration, features_from
from bench import layout_of, HEAD0
from keyboard_killer.vision.calibrator import StereoGazeModel
from keyboard_killer.vision.geo_gaze import GeoGazeModel

POSTURES = {
    "calib pose": ((0, 0, 0), (0, 0, 0)),
    "lean L 10cm": ((-10, 0, 0), (0, 0, 0)),
    "lean R 10cm": ((10, 0, 0), (0, 0, 0)),
    "closer 12cm": ((0, 0, 12), (0, 0, 0)),
    "back 12cm": ((0, 0, -12), (0, 0, 0)),
    "slouch 8cm": ((0, 8, -4), (0, 6, 0)),
    "body turn 12deg": ((3, 0, 0), (12, 0, 0)),
    "head roll 8deg": ((0, 0, 0), (0, 0, 8)),
}

def sweep(rig_name: str, seed: int, n_targets: int = 24) -> dict[str, dict[str, float]]:
    stereo = rig_name.startswith("stereo")
    head0 = HEAD0.get(rig_name, np.array([26.5, 10.0, -62.0]))
    cal = World(make_rig(rig_name), Subject(), np.random.default_rng(seed), head0)
    X, Y, G = run_calibration(cal, stereo)
    rig = make_rig(rig_name)
    mon, layout = layout_of(rig)
    models = {
        "current": StereoGazeModel.fit(X, Y, "a", "b", mon, layout, groups=G),
        "geo": GeoGazeModel.fit(X, Y, rig.specs, rig.ref),
    }
    rng = np.random.default_rng(seed + 99)
    out: dict[str, dict[str, float]] = {}
    for pname, (pos, rot) in POSTURES.items():
        w = World(make_rig(rig_name), Subject(), np.random.default_rng(seed + 5), head0)
        w.offset_target = w.offset = np.asarray(pos, float)
        w.rot_offset_target = w.rot_offset = np.asarray(rot, float)
        errs = {k: [] for k in models}
        for _ in range(n_targets):
            m = w.rig.monitors[int(rng.integers(len(w.rig.monitors)))]
            px = np.array([m.x + m.width * rng.uniform(0.08, 0.92), m.y + m.height * rng.uniform(0.08, 0.92)])
            w.gaze_world = w.rig.px_to_world(*px)
            w.step_fixational(True)
            feats = []
            for i in range(30):
                w.step_body(); w.step_fixational(False)
                f = features_from(w.observe(), stereo)
                if f is not None and i >= 12:
                    feats.append(f)
            if not feats:
                continue
            F = np.asarray(feats)
            eye = w.eye_center_world()
            truth = w.gaze_world - eye
            for k, mdl in models.items():
                p = mdl.predict_batch(F).mean(0) if hasattr(mdl, "predict_batch") else np.mean([mdl.predict(f) for f in F], axis=0)
                q = w.rig.px_to_world(float(p[0]), float(p[1])) - eye
                errs[k].append(math.degrees(math.acos(np.clip(q @ truth / np.linalg.norm(q) / np.linalg.norm(truth), -1, 1))))
        out[pname] = {k: float(np.median(v)) for k, v in errs.items()}
    return out

def _sweep_job(job: tuple[str, int]) -> dict[str, dict[str, float]]:
    return sweep(job[0], job[1])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("rigs", nargs="*", default=["single", "offaxis"])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args()
    jobs = [(rig, 21 + s) for rig in a.rigs for s in range(a.seeds)]
    n_workers = a.workers if a.workers > 0 else min(len(jobs), os.cpu_count() or 1)
    if n_workers <= 1 or len(jobs) <= 1:
        flat = [_sweep_job(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            flat = list(pool.map(_sweep_job, jobs))
    for r_idx, rig in enumerate(a.rigs):
        res = flat[r_idx * a.seeds : (r_idx + 1) * a.seeds]
        print(f"\n== {rig}  (median deg over targets, mean over {a.seeds} seeds)")
        print("posture".ljust(18) + "".join(k.rjust(10) for k in res[0]["calib pose"]))
        for p in POSTURES:
            print(p.ljust(18) + "".join(f"{np.mean([r[p][k] for r in res]):10.2f}" for k in res[0][p]))
