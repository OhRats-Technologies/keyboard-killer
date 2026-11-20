import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from gazesim import Subject, World, make_rig, run_calibration, run_session
from bench import GeoController, script_for
from keyless.vision.geo_gaze import GeoGazeModel
rig_name = "single"; head0 = np.array([26.5, 10.0, -62.0])
cal = World(make_rig(rig_name), Subject(), np.random.default_rng(11), head0)
X, Y, G = run_calibration(cal, False)
m = GeoGazeModel.fit(X, Y, make_rig(rig_name).specs, make_rig(rig_name).ref)
w = World(make_rig(rig_name), Subject(), np.random.default_rng(1011), head0)
ctl = GeoController(m, chase=True, clicks=False); ctl.tracker.enable_ekf = False
fixes = []
orig = ctl.tracker.chase.push
def spy(f):
    fixes.append(f); return orig(f)
ctl.tracker.chase.push = spy
st = run_session(w, ctl, False, script_for(rig_name, 100), 100, np.random.default_rng(18))
ts = np.array([f.t for f in st.frames]); tk = np.array([f.task for f in st.frames]); tp = np.array([f.true_px for f in st.frames])
# chase episodes
ep = []; cur = None
for i, f in enumerate(st.frames):
    if f.task == "chase" and cur is None: cur = f.t
    if f.task != "chase" and cur is not None: ep.append((cur, f.t)); cur = None
ppd = ctl.tracker._ppd
print("ppd", round(ppd), "segmenter sigma", round(ctl.tracker.segmenter.sigma))
for a, b in ep[:8]:
    print(f"--- chase episode {a:.1f}-{b:.1f}")
    for f in fixes:
        if a - 2.0 <= f.t0 <= b + 1.5:
            sel = (ts >= f.t0) & (ts <= f.t1)
            print(f"  fix {f.t0:6.2f}-{f.t1:6.2f} dur={f.duration:.2f} c={f.center.round(0)} truth={np.nanmedian(tp[sel],axis=0).round(0)} tasks={sorted(set(tk[sel]))}")
