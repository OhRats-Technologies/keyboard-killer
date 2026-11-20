"""Run the virtual benchmark: current StereoGazeModel vs GeoGaze variants on several rigs.

Usage:  uv run python notes/gaze-calibration/bench/bench.py [rig ...] [--seeds N] [--duration S]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from gazesim import Event, Subject, World, angular_errors, make_rig, run_calibration, run_session  # noqa: E402

from keyboard_killer.config import AppConfig  # noqa: E402
from keyboard_killer.platform.base import DisplayLayout, MonitorInfo  # noqa: E402
from keyboard_killer.vision.calibrator import StereoGazeModel  # noqa: E402
from keyboard_killer.vision.geo_gaze import FixationSegmenter, GeoGazeModel, GeoGazeTracker  # noqa: E402
from keyboard_killer.vision.online_learner import OnlineGazeLearner  # noqa: E402

HEAD0 = {"triple": np.array([29.9, 12.0, -60.0])}


def layout_of(rig) -> tuple[MonitorInfo, DisplayLayout]:
    infos = tuple(MonitorInfo(s.connector, s.x, s.y, s.width, s.height, s.width, s.height, 1.0, s.connector == rig.ref) for s in rig.specs)
    tw = max(s.x + s.width for s in rig.specs)
    th = max(s.y + s.height for s in rig.specs)
    ref = next(i for i in infos if i.connector == rig.ref)
    return ref, DisplayLayout(infos, tw, th)


class BaselineController:
    def __init__(self, X, Y, G, rig, online: bool) -> None:
        mon, layout = layout_of(rig)
        self.model = StereoGazeModel.fit(X, Y, "cam1", "cam2", mon, layout, groups=G)
        self.learner = OnlineGazeLearner(self.model, AppConfig(), mon, layout, calib_path=Path("/nonexistent/x.json")) if online else None
        self.seg = FixationSegmenter(min_amp_px=25.0)

    def step(self, feat, t):
        if self.learner is not None:
            with contextlib.redirect_stdout(io.StringIO()):
                p = self.learner.predict(feat, now_ts=t)
        else:
            p = self.model.predict(feat)
        self.seg.push(t, np.asarray(p), feat)
        return self.seg.smoothed

    def click(self, x, y, t):
        if self.learner is not None:
            with contextlib.redirect_stdout(io.StringIO()):
                self.learner.record_verified_interaction(x, y, anchor_ts=t, source="mouse", confidence_weight=1.8)


class GeoController:
    def __init__(self, model: GeoGazeModel, chase: bool, clicks: bool) -> None:
        self.tracker = GeoGazeTracker(model, enable_chase=chase, enable_ekf=chase or clicks)
        self.clicks = clicks

    def step(self, feat, t):
        p = self.tracker.update(feat, t)
        s = self.tracker.smoothed
        return s if s is not None else p

    def click(self, x, y, t):
        if self.clicks:
            self.tracker.record_click(x, y, t)


def script_for(rig_name: str, duration: float) -> list[Event]:
    seg = duration / 5.0
    return [
        Event(0.0, "phase", "calm"),
        Event(seg, "phase", "lean+closer"),
        Event(seg, "head_offset", ((-10.0, 0.0, 9.0), (8.0, 0.0, 0.0))),
        Event(2 * seg, "phase", "slouch+back"),
        Event(2 * seg, "head_offset", ((4.0, 9.0, -8.0), (-4.0, 7.0, 3.0))),
        Event(3 * seg, "phase", "camera_bump"),
        Event(3 * seg, "camera_bump", (0, (2.5, -2.0, 1.0), (1.5, 0.8, 0.0))),
        Event(4 * seg, "phase", "recentred"),
        Event(4 * seg, "head_offset", ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))),
    ]


PHASES = ["calm", "lean+closer", "slouch+back", "camera_bump", "recentred"]
CONTROLLERS = [c for c in ["current", "current+clicks", "geo", "geo+clicks", "geo+clicks+chase"] if c in __import__("os").environ.get("CTL", "current,current+clicks,geo,geo+clicks,geo+clicks+chase").split(",")]


def _prepare_rig(job: tuple[str, int]) -> tuple[str, int, np.ndarray, np.ndarray, np.ndarray, str, float]:
    rig_name, seed = job
    stereo = rig_name.startswith("stereo")
    head0 = HEAD0.get(rig_name, np.array([26.5, 10.0, -62.0]))
    subj = Subject()
    cal_world = World(make_rig(rig_name), subj, np.random.default_rng(seed), head0)
    X, Y, G = run_calibration(cal_world, stereo)
    rig_for_specs = make_rig(rig_name)
    t0 = time.time()
    geo = GeoGazeModel.fit(X, Y, rig_for_specs.specs, rig_for_specs.ref)
    fit_s = round(time.time() - t0, 2)
    tmp = Path(f"/tmp/geo_{rig_name}_{seed}.json")
    geo.save(tmp)
    return rig_name, seed, X, Y, G, str(tmp), fit_s


def _run_controller_job(job: tuple[str, int, str, float, np.ndarray, np.ndarray, np.ndarray, str]) -> tuple[str, int, str, dict]:
    rig_name, seed, name, duration, X, Y, G, geo_path = job
    stereo = rig_name.startswith("stereo")
    head0 = HEAD0.get(rig_name, np.array([26.5, 10.0, -62.0]))
    subj = Subject()
    rig = make_rig(rig_name)
    world = World(rig, subj, np.random.default_rng(seed + 1000), head0)
    if name.startswith("current"):
        ctl = BaselineController(X, Y, G, rig, online=name.endswith("clicks"))
    else:
        ctl = GeoController(GeoGazeModel.load(Path(geo_path)), chase="chase" in name, clicks="clicks" in name)
    stats = run_session(world, ctl, stereo, script_for(rig_name, duration), duration, np.random.default_rng(seed + 7))
    errs = angular_errors(world, stats)
    res: dict = {ph: {"median": float(np.median(errs[ph])), "p90": float(np.percentile(errs[ph], 90))} for ph in PHASES if ph in errs}
    allv = np.concatenate([errs[ph] for ph in errs])
    res["all"] = {"median": float(np.median(allv)), "p90": float(np.percentile(allv, 90))}
    res["chase_steps_per_min"] = stats.chase_steps / (duration / 60.0)
    if isinstance(ctl, GeoController):
        log = ctl.tracker.label_log
        res["labels_accepted"] = {src: sum(1 for _, s, ok in log if s == src and ok) for src in {s for _, s, _ in log}}
        res["chains"] = ctl.tracker.chase.confirmed_chains
    return rig_name, seed, name, res


def run_rig(rig_name: str, seed: int, duration: float) -> dict:
    _, _, X, Y, G, geo_path, fit_s = _prepare_rig((rig_name, seed))
    out: dict = {"rig": rig_name, "seed": seed, "n_calib": int(len(X)), "geo_fit_s": fit_s, "results": {}}
    for name in CONTROLLERS:
        _, _, _, res = _run_controller_job((rig_name, seed, name, duration, X, Y, G, geo_path))
        out["results"][name] = res
    return out


def _print_rig_result(r: dict, seed_idx: int) -> None:
    print(f"\n== {r['rig']} seed={seed_idx} n_calib={r['n_calib']} geo_fit={r['geo_fit_s']}s")
    hdr = "controller".ljust(18) + "".join(p[:11].rjust(12) for p in PHASES) + "   all(med/p90)  chase/min"
    print(hdr)
    for name, res in r["results"].items():
        row = name.ljust(18) + "".join((f"{res[p]['median']:.2f}" if p in res else "-").rjust(12) for p in PHASES)
        row += f"   {res['all']['median']:.2f}/{res['all']['p90']:.2f}".ljust(16) + f"{res['chase_steps_per_min']:.1f}"
        if "labels_accepted" in res:
            row += f"  labels={res['labels_accepted']} chains={res['chains']}"
        print(row, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("rigs", nargs="*", default=["single", "offaxis", "dual", "triple", "stereo_dual"])
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--workers", type=int, default=0, help="Parallel worker processes (0 = auto)")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results.json")
    args = ap.parse_args()
    prep_jobs = [(rig, 11 + s) for rig in args.rigs for s in range(args.seeds)]
    total_ctl_jobs = len(prep_jobs) * len(CONTROLLERS)
    n_workers = args.workers if args.workers > 0 else min(total_ctl_jobs, os.cpu_count() or 1)
    all_out: list[dict] = []
    if n_workers <= 1:
        for idx, (rig, seed) in enumerate(prep_jobs):
            r = run_rig(rig, seed, args.duration)
            all_out.append(r)
            _print_rig_result(r, idx % args.seeds)
    else:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as pool:
            prepped = list(pool.map(_prepare_rig, prep_jobs))
            ctl_jobs = [
                (rig, seed, name, args.duration, X, Y, G, geo_path)
                for rig, seed, X, Y, G, geo_path, _ in prepped
                for name in CONTROLLERS
            ]
            ctl_results = list(pool.map(_run_controller_job, ctl_jobs))
        by_key = {(rig, seed, name): res for rig, seed, name, res in ctl_results}
        for idx, (rig, seed, X, _Y, _G, _gp, fit_s) in enumerate(prepped):
            r = {
                "rig": rig,
                "seed": seed,
                "n_calib": int(len(X)),
                "geo_fit_s": fit_s,
                "results": {name: by_key[(rig, seed, name)] for name in CONTROLLERS},
            }
            all_out.append(r)
            _print_rig_result(r, idx % args.seeds)
    args.out.write_text(json.dumps(all_out, indent=1))
    print(f"\nwrote {args.out}")
    _ = math


if __name__ == "__main__":
    main()
