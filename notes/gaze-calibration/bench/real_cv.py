"""Leave-groups-out CV on the saved real calibration dataset: StereoGazeModel vs GeoGazeModel."""
from __future__ import annotations
import sys, time
from pathlib import Path
import numpy as np
from keyboard_killer.platform.base import DisplayLayout, MonitorInfo
from keyboard_killer.vision.calibrator import StereoGazeModel
from keyboard_killer.vision.geo_gaze import GeoGazeModel, MonitorSpec

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".config/keyboard-killer/calibration/calib_dataset.npz")
d = np.load(path, allow_pickle=True)
X, Y, G = d["X"], d["Y"], d["groups"]
specs = [MonitorSpec("DP-2", 0, 0, 1920, 1080, 521, 293), MonitorSpec("HDMI-A-1", 1920, 0, 1920, 1080, 598, 336)]
mon = MonitorInfo("DP-2", 0, 0, 1920, 1080, 1920, 1080, 1.0, True)
layout = DisplayLayout((mon,), 1920, 1080)
cm_per_px = 52.1 / 1920
groups = np.unique(G)
rng = np.random.default_rng(0)
folds = np.array_split(rng.permutation(groups), 5)
res = {"stereo": [], "geo": []}
for k, fg in enumerate(folds):
    te = np.isin(G, fg); tr = ~te
    t0 = time.time(); m1 = StereoGazeModel.fit(X[tr], Y[tr], "a", "", mon, layout, groups=G[tr]); t1 = time.time()
    m2 = GeoGazeModel.fit(X[tr], Y[tr], specs, "DP-2"); t2 = time.time()
    p1 = np.array([m1.predict(x) for x in X[te]]); p2 = m2.predict_batch(X[te])
    p2 = np.clip(p2, 0, [1919, 1079])
    e1 = np.linalg.norm(p1 - Y[te], axis=1); e2 = np.linalg.norm(p2 - Y[te], axis=1)
    res["stereo"].append(e1); res["geo"].append(e2)
    print(f"fold {k}: groups={sorted(fg.tolist())} n={te.sum()} stereo med={np.median(e1):.0f}px geo med={np.median(e2):.0f}px  (fit {t1-t0:.1f}s / {t2-t1:.1f}s)")
for name, e in res.items():
    e = np.concatenate(e)
    print(f"{name:7s} median={np.median(e):.0f}px mean={e.mean():.0f}px p90={np.percentile(e,90):.0f}px  (~{np.median(e)*cm_per_px:.1f} cm)")
m = GeoGazeModel.fit(X, Y, specs, "DP-2")
bf = m.blocks[0]
from scipy.spatial.transform import Rotation
print("camera pos in M0 (cm):", np.round(bf.params[3:6], 1), "rot (deg):", np.round(np.degrees(Rotation.from_rotvec(bf.params[:3]).as_euler('xyz')), 1))
print("head gains:", np.round(bf.params[6:8], 2), "viewer M0:", np.round(m.viewer_m0, 1), "sigma_px:", round(bf.sigma_px))
for mg in m.monitors: print(mg.connector, np.round(mg.origin,1), np.round(mg.normal,2))
