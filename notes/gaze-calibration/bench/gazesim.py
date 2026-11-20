"""Closed-loop virtual gaze benchmark (no human in the loop).

World frame M0: origin at the reference monitor's top-left corner, x right, y down, z into the
screen, centimetres. A simulated subject sits in front of one or more monitors, moves head and
body, makes saccades/fixations with fixational noise and blinks, and reacts to the visible
cursor (it sometimes chases a mis-calibrated cursor). Cameras observe a 3-D eye/face model; the
landmark pixels go through the app's own feature code (``landmarks._iris_offset_high_snr``,
``_extract_los_euler_and_translation``, ``build_stereo_feature_vector``) so the gaze models see
the same 20-D vectors they see live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial.transform import Rotation

from keyboard_killer.vision.geo_gaze import MonitorGeom, MonitorSpec, _rotvec_to_mat
from keyboard_killer.vision.landmarks import (
    CameraObservation,
    _extract_los_euler_and_translation,
    _iris_offset_high_snr,
    build_stereo_feature_vector,
)

FPS = 30.0
DT = 1.0 / FPS
_DEG2RAD = math.pi / 180.0

# Camera axes expressed in M0 when a camera faces the user squarely (OpenGL camera frame).
CAM_FACING_USER = np.diag([-1.0, -1.0, 1.0])
# Head axes (x = subject's left, y = up, z = forward) in M0 when the subject faces the screen.
HEAD_FACING_SCREEN = np.diag([-1.0, -1.0, 1.0])


def _rot_yxz_deg(yaw: float, pitch: float, roll: float) -> np.ndarray:
    """Intrinsic YXZ rotation matrix: Ry(yaw) @ Rx(pitch) @ Rz(roll)."""
    y, p, r = yaw * _DEG2RAD, pitch * _DEG2RAD, roll * _DEG2RAD
    cy, sy = math.cos(y), math.sin(y)
    cp, sp = math.cos(p), math.sin(p)
    cr, sr = math.cos(r), math.sin(r)
    return np.array(
        [
            [cy * cr + sy * sp * sr, -cy * sr + sy * sp * cr, sy * cp],
            [cp * sr, cp * cr, -sp],
            [-sy * cr + cy * sp * sr, sy * sr + cy * sp * cr, cy * cp],
        ],
        dtype=np.float64,
    )


def _rot_yx_extrinsic_deg(a: float, b: float) -> np.ndarray:
    """Extrinsic yx rotation matrix: Rx(b) @ Ry(a)."""
    ra, rb = a * _DEG2RAD, b * _DEG2RAD
    ca, sa = math.cos(ra), math.sin(ra)
    cb, sb = math.cos(rb), math.sin(rb)
    return np.array(
        [
            [ca, 0.0, sa],
            [sb * sa, cb, -sb * ca],
            [-cb * sa, sb, cb * ca],
        ],
        dtype=np.float64,
    )


def _scale_yaw_zyx(r: np.ndarray, yaw_gain: float) -> np.ndarray:
    """Decompose intrinsic ZYX (roll, yaw, pitch), scale yaw by ``yaw_gain``, and reconstruct."""
    sy = math.hypot(float(r[0, 0]), float(r[1, 0]))
    if sy > 1e-6:
        pitch = math.atan2(float(r[2, 1]), float(r[2, 2]))
        yaw = math.atan2(float(-r[2, 0]), sy)
        roll = math.atan2(float(r[1, 0]), float(r[0, 0]))
    else:
        pitch = math.atan2(float(-r[1, 2]), float(r[1, 1]))
        yaw = math.atan2(float(-r[2, 0]), sy)
        roll = 0.0
    yaw *= yaw_gain
    cy, sy_ = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    return np.array(
        [
            [cr * cy, cr * sy_ * sp - sr * cp, cr * sy_ * cp + sr * sp],
            [sr * cy, sr * sy_ * sp + cr * cp, sr * sy_ * cp - cr * sp],
            [-sy_, cy * sp, cy * cp],
        ],
        dtype=np.float64,
    )


# --------------------------------------------------------------------------------------------
# Rig
# --------------------------------------------------------------------------------------------


@dataclass
class Camera:
    rot: np.ndarray  # camera -> M0
    pos: np.ndarray  # camera origin in M0 (cm)
    f_px: float = 900.0
    width: int = 1280
    height: int = 720


def camera_at(pos: tuple[float, float, float], look_at: np.ndarray, roll_deg: float = 0.0) -> Camera:
    """Camera at ``pos`` aimed at ``look_at`` with its up axis as close to M0 up (-y) as possible."""
    pos_v = np.asarray(pos, dtype=np.float64)
    view = look_at - pos_v
    view /= np.linalg.norm(view)
    z_cam = -view  # OpenGL: the camera looks along -z
    up = np.array([0.0, -1.0, 0.0])
    x_cam = np.cross(up, z_cam)
    x_cam /= np.linalg.norm(x_cam)
    y_cam = np.cross(z_cam, x_cam)
    rot = np.column_stack([x_cam, y_cam, z_cam])
    if roll_deg:
        rot = rot @ Rotation.from_rotvec([0, 0, math.radians(roll_deg)]).as_matrix()
    return Camera(rot=rot, pos=pos_v)


@dataclass
class Rig:
    name: str
    monitors: list[MonitorGeom]  # true physical poses
    specs: list[MonitorSpec]  # what the OS reports (layout px + EDID mm)
    ref: str
    cameras: list[Camera]

    def monitor_of_px(self, px: float, py: float) -> MonitorGeom | None:
        for m in self.monitors:
            if m.contains_px(px, py):
                return m
        return None

    def px_to_world(self, px: float, py: float) -> np.ndarray:
        m = self.monitor_of_px(px, py)
        if m is None:  # clamp to the nearest monitor
            m = min(self.monitors, key=lambda mm: math.hypot(np.clip(px, mm.x, mm.x + mm.width) - px, np.clip(py, mm.y, mm.y + mm.height) - py))
            px = float(np.clip(px, m.x, m.x + m.width - 1))
            py = float(np.clip(py, m.y, m.y + m.height - 1))
        return m.px_to_m0(np.array(px), np.array(py))

    def world_to_px(self, p: np.ndarray, eye: np.ndarray) -> np.ndarray:
        """Project a 3-D point seen from ``eye`` onto the monitor the ray hits first."""
        d = (p - eye)[None, :]
        o = eye[None, :]
        best, best_s = None, np.inf
        for m in self.monitors:
            px, py, s = m.intersect(o, d)
            if s[0] > 0 and m.contains_px(px[0], py[0]) and s[0] < best_s:
                best, best_s = np.array([px[0], py[0]]), s[0]
        return best if best is not None else np.array([np.nan, np.nan])


def _mon(spec: MonitorSpec, origin, u, v) -> MonitorGeom:
    w_cm, h_cm = spec.width_mm / 10.0, spec.height_mm / 10.0
    if (spec.width >= spec.height) != (spec.width_mm >= spec.height_mm):
        w_cm, h_cm = h_cm, w_cm
    return MonitorGeom(spec.connector, spec.x, spec.y, spec.width, spec.height, w_cm, h_cm, np.asarray(origin, float), np.asarray(u, float), np.asarray(v, float))


def _hinged(spec: MonitorSpec, hinge: np.ndarray, side: str, yaw_deg: float, tilt_deg: float, drop_cm: float = 0.0) -> MonitorGeom:
    """Monitor attached at ``hinge`` (top-left for 'right', top-right for 'left'), angled to the user."""
    w_cm, h_cm = spec.width_mm / 10.0, spec.height_mm / 10.0
    if (spec.width >= spec.height) != (spec.width_mm >= spec.height_mm):
        w_cm, h_cm = h_cm, w_cm
    sgn = -1.0 if side == "right" else 1.0
    r = Rotation.from_rotvec([0, sgn * math.radians(yaw_deg), 0]) * Rotation.from_rotvec([math.radians(tilt_deg), 0, 0])
    u = r.apply([1.0, 0.0, 0.0])
    v = r.apply([0.0, 1.0, 0.0])
    hinge = np.asarray(hinge, float) + np.array([0.0, drop_cm, 0.0])
    origin = hinge if side == "right" else hinge - u * w_cm
    return MonitorGeom(spec.connector, spec.x, spec.y, spec.width, spec.height, w_cm, h_cm, origin, u, v)


def make_rig(name: str) -> Rig:
    s24 = MonitorSpec("DP-1", 0, 0, 1920, 1080, 531, 299)
    tilt_back = Rotation.from_rotvec([math.radians(-8.0), 0, 0])  # top leans away from the user
    ref = _mon(s24, [0, 0, 0], [1, 0, 0], [0, 1, 0])
    head0 = np.array([26.5, 10.0, -62.0])
    if name == "single":
        cam = camera_at((26.5, -1.5, 0.5), head0)
        return Rig(name, [ref], [s24], "DP-1", [cam])
    if name == "offaxis":
        # Camera on a stand to the right of the screen: ~35 deg off the user's straight-ahead.
        cam = camera_at((60.0, 8.0, -14.0), head0)
        return Rig(name, [ref], [s24], "DP-1", [cam])
    if name == "dual":
        s27 = MonitorSpec("HDMI-A-1", 1920, 0, 2560, 1440, 597, 336)
        right = _hinged(s27, np.array([53.1, -2.0, 0.0]), "right", yaw_deg=32.0, tilt_deg=-5.0)
        cam = camera_at((26.5, -1.5, 0.5), head0)
        return Rig(name, [ref, right], [s24, s27], "DP-1", [cam])
    if name == "triple":
        # Portrait 24" on the left (layout 1080x1920), 27" centre (reference), 14" laptop right.
        sl = MonitorSpec("DP-2", 0, 0, 1080, 1920, 531, 299)
        sc = MonitorSpec("DP-1", 1080, 300, 2560, 1440, 597, 336)
        sr = MonitorSpec("eDP-1", 3640, 700, 1600, 1000, 302, 189)
        center = _mon(sc, [0, 0, 0], [1, 0, 0], [0, 1, 0])
        rot = tilt_back.as_matrix()
        center = MonitorGeom(sc.connector, sc.x, sc.y, sc.width, sc.height, 59.7, 33.6, np.zeros(3), rot @ [1, 0, 0], rot @ [0, 1, 0])
        left = _hinged(sl, center.origin + np.array([0.0, -14.0, 0.0]), "left", yaw_deg=38.0, tilt_deg=-3.0)
        right = _hinged(sr, center.origin + center.u * 59.7 + center.v * 12.0, "right", yaw_deg=45.0, tilt_deg=-20.0, drop_cm=6.0)
        head = np.array([29.9, 12.0, -60.0])
        cam = camera_at((29.9, -1.5, 0.0), head)
        return Rig(name, [left, center, right], [sl, sc, sr], "DP-1", [cam])
    if name == "stereo_dual":
        s27 = MonitorSpec("HDMI-A-1", 1920, 0, 2560, 1440, 597, 336)
        right = _hinged(s27, np.array([53.1, -2.0, 0.0]), "right", yaw_deg=32.0, tilt_deg=-5.0)
        cam1 = camera_at((26.5, -1.5, 0.5), head0)
        cam2 = camera_at((70.0, 36.0, -14.0), head0)  # under the right monitor
        return Rig(name, [ref, right], [s24, s27], "DP-1", [cam1, cam2])
    raise ValueError(name)


# --------------------------------------------------------------------------------------------
# Subject
# --------------------------------------------------------------------------------------------


@dataclass
class Subject:
    eye_a: np.ndarray = field(default_factory=lambda: np.array([-3.15, 2.9, 1.2]))  # subject's right eye centre (head frame)
    eye_b: np.ndarray = field(default_factory=lambda: np.array([3.15, 2.9, 1.2]))
    r_iris: float = 0.98
    kappa_h_deg: float = 5.0
    kappa_v_deg: float = 1.2
    face_scale: float = 1.05  # MediaPipe's metric scale error (true face vs canonical)
    yaw_gain: float = 0.92  # MediaPipe under-reports large yaw
    px_noise: float = 0.65  # iris landmark pixel noise (white)
    corner_noise: float = 0.45
    slow_noise: float = 0.35  # stationary std of the slow (AR) landmark bias
    pose_noise_deg: float = 0.3  # matches real within-dot yaw/pitch std (~0.45 deg)
    pose_slow_deg: float = 0.4


class _AR:
    def __init__(self, rng: np.random.Generator, shape, sigma: float, rho: float) -> None:
        self.rng, self.sigma, self.rho = rng, sigma, rho
        self.x = rng.normal(0, sigma, shape)

    def step(self) -> np.ndarray:
        self.x = self.rho * self.x + math.sqrt(1 - self.rho**2) * self.rng.normal(0, self.sigma, self.x.shape)
        return self.x


class FaceRenderer:
    """Produces the app's CameraObservation for one camera from the true 3-D state."""

    def __init__(self, cam: Camera, subj: Subject, rng: np.random.Generator) -> None:
        self.cam, self.subj, self.rng = cam, subj, rng
        self.slow = _AR(rng, (12, 2), subj.slow_noise, 0.97)
        self.pose_slow = _AR(rng, 3, math.radians(subj.pose_slow_deg), 0.985)

    def observe(self, head_pos: np.ndarray, head_rot: np.ndarray, eye_dirs_h: list[np.ndarray], lid_phi: list[float]) -> CameraObservation | None:
        cam, s, rng = self.cam, self.subj, self.rng
        r_h = cam.rot.T @ head_rot  # head -> camera
        t_c = cam.rot.T @ (head_pos - cam.pos)
        if t_c[2] > -10:
            return None
        fwd = r_h[:, 2]
        if -fwd[2] < -0.05 and False:
            return None
        to_cam = -t_c / np.linalg.norm(t_c)
        if float(fwd @ to_cam) < math.cos(math.radians(62)):
            return None  # face turned too far from this camera

        pts_h = []
        for eye_c, sgn, d_h, phi in ((s.eye_a, -1.0, eye_dirs_h[0], lid_phi[0]), (s.eye_b, 1.0, eye_dirs_h[1], lid_phi[1])):
            outer = eye_c + np.array([1.4 * sgn, -0.05, 0.7])
            inner = eye_c + np.array([-1.4 * sgn, -0.1, 1.0])
            upper = eye_c + np.array([0.0, 0.55 + 0.45 * math.sin(phi), 1.1])
            lower = eye_c + np.array([0.0, -0.55 + 0.25 * math.sin(phi), 1.05])
            iris = eye_c + s.r_iris * d_h
            pts_h += [outer, inner, upper, lower, iris]
        nose = np.array([0.0, -0.5, 6.0])
        pts_h.append(nose)
        pts_h += [np.zeros(3)] * (12 - len(pts_h))
        pts_c = (np.asarray(pts_h) @ r_h.T) + t_c
        z = -pts_c[:, 2]
        uv = np.column_stack([cam.width / 2 + cam.f_px * pts_c[:, 0] / z, cam.height / 2 - cam.f_px * pts_c[:, 1] / z])
        noise = rng.normal(0, s.corner_noise, uv.shape)
        noise[[4, 9]] = rng.normal(0, s.px_noise, (2, 2))
        uv = uv + noise + self.slow.step()

        a_out, a_in, a_up, a_lo, a_iris, b_out, b_in, b_up, b_lo, b_iris, nose_px = uv[:11]
        # In the image, eye A (subject's right) is on the left when the subject faces the camera.
        width_a = float(np.linalg.norm(a_in - a_out))
        width_b = float(np.linalg.norm(b_out - b_in))
        if width_a < 4 or width_b < 4:
            return None

        # Measured head pose: scale error on translation, yaw under-reporting, white + slow noise.
        r_meas = _scale_yaw_zyx(r_h, s.yaw_gain)
        r_noise = _rotvec_to_mat(rng.normal(0, math.radians(s.pose_noise_deg), 3) + self.pose_slow.step())
        mat = np.eye(4)
        mat[:3, :3] = r_meas @ r_noise
        mat[:3, 3] = t_c * s.face_scale + rng.normal(0, 0.3, 3)
        yaw, pitch, roll, tx, ty, tz = _extract_los_euler_and_translation(mat)

        # Blendshape-like neural gaze cues (noisy, saturating).
        th_a = math.atan2(eye_dirs_h[0][0], eye_dirs_h[0][2])
        th_b = math.atan2(eye_dirs_h[1][0], eye_dirs_h[1][2])
        ph_a, ph_b = math.asin(eye_dirs_h[0][1]), math.asin(eye_dirs_h[1][1])
        blend_a_dx = float(np.clip(th_a / 1.1 + rng.normal(0, 0.05), -1, 1))
        blend_b_dx = float(np.clip(th_b / 1.1 + rng.normal(0, 0.05), -1, 1))
        blend_a_dy = float(np.clip(-ph_a / 2.2 + rng.normal(0, 0.03), -1, 1))
        blend_b_dy = float(np.clip(-ph_b / 2.2 + rng.normal(0, 0.03), -1, 1))

        inter_canthal = float(np.linalg.norm(b_in - a_in))
        rms_w = math.sqrt(0.5 * (width_a**2 + width_b**2))
        rigid = 0.65 * (rms_w / max(0.72, math.cos(yaw))) + 0.35 * inter_canthal
        bino = 0.5 * (b_in + b_out) - 0.5 * (a_in + a_out)
        bu = bino / max(1e-5, np.linalg.norm(bino))
        bv = np.array([-bu[1], bu[0]])
        a_dx, a_dy, wa = _iris_offset_high_snr(a_out, a_in, a_iris, a_up, a_lo, bv, rigid, 1.0, blend_a_dx, blend_a_dy, 0.1)
        b_dx, b_dy, wb = _iris_offset_high_snr(b_out, b_in, b_iris, b_up, b_lo, bv, rigid, -1.0, blend_b_dx, blend_b_dy, 0.1)
        ratio = min(wa, wb) / max(wa, wb)
        return CameraObservation(
            iris_a_dx=a_dx, iris_a_dy=a_dy, iris_b_dx=b_dx, iris_b_dy=b_dy,
            yaw=yaw, pitch=pitch, roll=roll, tx=tx, ty=ty, tz=tz,
            nose_x=float(nose_px[0] / cam.width), nose_y=float(nose_px[1] / cam.height),
            blink_left=0.0, blink_right=0.0, jaw_open=0.0,
            confidence_a=1.0 if wa >= wb else ratio**1.5, confidence_b=1.0 if wb >= wa else ratio**1.5,
            iris_a_px=(0, 0), iris_b_px=(0, 0), nose_px=(0, 0), eye_corners_px=((0, 0), (0, 0), (0, 0), (0, 0)),
        )


# --------------------------------------------------------------------------------------------
# Session: head/body motion, gaze behaviour, cursor chasing
# --------------------------------------------------------------------------------------------


@dataclass
class Event:
    t: float
    kind: str  # "head_offset" | "camera_bump" | "phase"
    value: object


@dataclass
class Frame:
    t: float
    true_px: np.ndarray
    shown_px: np.ndarray
    eye: np.ndarray
    true_world: np.ndarray
    in_fixation: bool
    chasing: bool
    phase: str
    task: str = ""


class World:
    """Truth state of the subject and the rig, advanced one frame at a time."""

    def __init__(self, rig: Rig, subj: Subject, rng: np.random.Generator, head0: np.ndarray) -> None:
        self.rig, self.subj, self.rng = rig, subj, rng
        self.head0 = head0.astype(float)
        self.offset = np.zeros(3)
        self.offset_target = np.zeros(3)
        self.rot_offset = np.zeros(3)  # yaw, pitch, roll (deg) of the body
        self.rot_offset_target = np.zeros(3)
        self.ou_pos = _AR(rng, 3, 0.8, math.exp(-DT / 3.0))
        self.ou_rot = _AR(rng, 3, 1.5, math.exp(-DT / 2.0))
        self.head_follow = np.zeros(2)  # yaw, pitch (deg) from eye-head coordination
        self.renderers = [FaceRenderer(c, subj, rng) for c in rig.cameras]
        self.gaze_world = rig.px_to_world(*self._ref_center())
        self.fixational = np.zeros(2)
        self.next_micro = 0.5

    def _ref_center(self) -> tuple[float, float]:
        m = next(mm for mm in self.rig.monitors if mm.connector == self.rig.ref)
        return m.x + m.width / 2, m.y + m.height / 2

    def head_pose(self) -> tuple[np.ndarray, np.ndarray]:
        pos = self.head0 + self.offset + self.ou_pos.x
        yaw, pitch, roll = self.rot_offset + self.ou_rot.x + np.array([self.head_follow[0], self.head_follow[1], 0.0])
        r = HEAD_FACING_SCREEN @ _rot_yxz_deg(float(yaw), float(pitch), float(roll))
        return pos, r

    def eye_center_world(self) -> np.ndarray:
        pos, r = self.head_pose()
        return pos + r @ (0.5 * (self.subj.eye_a + self.subj.eye_b))

    def step_body(self) -> None:
        a = 1 - math.exp(-DT / 1.2)
        self.offset += a * (self.offset_target - self.offset)
        self.rot_offset += a * (self.rot_offset_target - self.rot_offset)
        self.ou_pos.step()
        self.ou_rot.step()
        # Eye-head coordination: the head carries ~35% of gaze eccentricity beyond 8 deg.
        pos = self.head0 + self.offset + self.ou_pos.x
        v = self.gaze_world - pos
        ro = self.rot_offset
        v_h = (HEAD_FACING_SCREEN @ _rot_yxz_deg(float(ro[0]), float(ro[1]), float(ro[2]))).T @ v
        az = math.degrees(math.atan2(v_h[0], v_h[2]))
        el = math.degrees(math.atan2(v_h[1], math.hypot(v_h[0], v_h[2])))
        tgt = np.array([0.35 * np.sign(az) * max(0.0, abs(az) - 8.0), -0.25 * np.sign(el) * max(0.0, abs(el) - 8.0)])
        self.head_follow += (1 - math.exp(-DT / 0.35)) * (tgt - self.head_follow)

    def step_fixational(self, new_fixation: bool) -> None:
        if new_fixation:
            self.fixational = self.rng.normal(0, 0.25, 2)  # landing error (deg)
        self.fixational += self.rng.normal(0, 0.12 * math.sqrt(DT), 2)  # drift
        self.fixational *= 0.995
        self.next_micro -= DT
        if self.next_micro <= 0:
            self.fixational += self.rng.normal(0, 0.25, 2)
            self.next_micro = self.rng.exponential(0.7)

    def observe(self) -> list[CameraObservation | None]:
        pos, r = self.head_pose()
        dirs, phis = [], []
        perturb = _rot_yx_extrinsic_deg(float(self.fixational[0]), float(self.fixational[1]))
        for eye_c, sgn in ((self.subj.eye_a, -1.0), (self.subj.eye_b, 1.0)):
            e_w = pos + r @ eye_c
            v = self.gaze_world - e_w
            v_h = perturb @ (r.T @ v)
            v_h /= np.linalg.norm(v_h)
            th = math.atan2(v_h[0], v_h[2]) + sgn * math.radians(self.subj.kappa_h_deg)
            ph = math.asin(v_h[1]) + math.radians(self.subj.kappa_v_deg)
            dirs.append(np.array([math.sin(th) * math.cos(ph), math.sin(ph), math.cos(th) * math.cos(ph)]))
            phis.append(ph)
        return [rd.observe(pos, r, dirs, phis) for rd in self.renderers]

    def bump_camera(self, idx: int, rot_deg: tuple[float, float, float], shift_cm: tuple[float, float, float]) -> None:
        cam = self.rig.cameras[idx]
        cam.rot = Rotation.from_euler("xyz", rot_deg, degrees=True).as_matrix() @ cam.rot
        cam.pos = cam.pos + np.asarray(shift_cm, float)


def features_from(obs: list[CameraObservation | None], stereo: bool) -> np.ndarray | None:
    if stereo:
        o1, o2 = obs[0], obs[1]
        if o1 is None and o2 is None:
            return None
        feat, _ = build_stereo_feature_vector(o1, o2)
        return feat
    if obs[0] is None:
        return None
    feat, _ = build_stereo_feature_vector(obs[0], obs[0])
    return feat


# --------------------------------------------------------------------------------------------
# Calibration protocol (mirrors the app: 9 anchors + 4 head-pose anchors + 2 pursuit sweeps)
# --------------------------------------------------------------------------------------------


ANCHORS = [(0.5, 0.5), (0.06, 0.08), (0.94, 0.92), (0.94, 0.08), (0.06, 0.92), (0.06, 0.5), (0.94, 0.5), (0.5, 0.08), (0.5, 0.92)]
POSE_ANCHORS = [(-14, 0), (14, 0), (0, -9), (0, 9)]


def run_calibration(world: World, stereo: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rig = world.rig
    ref = next(m for m in rig.monitors if m.connector == rig.ref)
    X, Y, G = [], [], []
    t = 0.0

    def hold(px: float, py: float, n_settle: int, n_take: int, grp: int) -> None:
        nonlocal t
        world.gaze_world = rig.px_to_world(px, py)
        world.step_fixational(True)
        for i in range(n_settle + n_take):
            world.step_body()
            world.step_fixational(False)
            t += DT
            if i < n_settle:
                continue
            f = features_from(world.observe(), stereo)
            if f is not None:
                X.append(f)
                Y.append((px, py))
                G.append(grp)

    for g, (rx, ry) in enumerate(ANCHORS):
        hold(ref.x + rx * ref.width, ref.y + ry * ref.height, 18, 22, g)
    for k, (yaw, pitch) in enumerate(POSE_ANCHORS):
        world.rot_offset_target = np.array([yaw, pitch, 0.0])
        for _ in range(45):
            world.step_body()
        hold(ref.x + 0.5 * ref.width, ref.y + 0.5 * ref.height, 6, 22, 9 + k)
    world.rot_offset_target = np.zeros(3)
    for _ in range(45):
        world.step_body()
    # Smooth pursuit: Lissajous sweep with an 80 ms pursuit lag between label and true gaze.
    for sweep in range(2):
        n = int(12.0 * FPS)
        for i in range(n):
            u = i / n
            fx = 0.5 + 0.44 * math.sin(2 * math.pi * (u * (1 + sweep) + 0.25 * sweep))
            fy = 0.5 + 0.42 * math.sin(2 * math.pi * u * (2 - sweep * 0.5))
            lag = 0.08 / 12.0
            fx_l = 0.5 + 0.44 * math.sin(2 * math.pi * ((u - lag) * (1 + sweep) + 0.25 * sweep))
            fy_l = 0.5 + 0.42 * math.sin(2 * math.pi * (u - lag) * (2 - sweep * 0.5))
            world.gaze_world = rig.px_to_world(ref.x + fx_l * ref.width, ref.y + fy_l * ref.height)
            world.step_body()
            world.step_fixational(False)
            f = features_from(world.observe(), stereo)
            if f is not None and i > 15:
                X.append(f)
                Y.append((ref.x + fx * ref.width, ref.y + fy * ref.height))
                G.append(50 + sweep)
    return np.asarray(X), np.asarray(Y, dtype=float), np.asarray(G)


# --------------------------------------------------------------------------------------------
# Closed-loop task session
# --------------------------------------------------------------------------------------------


@dataclass
class SessionStats:
    frames: list[Frame] = field(default_factory=list)
    chase_steps: int = 0
    clicks: int = 0


def run_session(
    world: World,
    controller: object,
    stereo: bool,
    script: list[Event],
    duration: float,
    rng: np.random.Generator,
    p_chase: float = 0.45,
    click_rate: float = 0.25,
    reading_share: float = 0.25,
) -> SessionStats:
    """Subject alternates target dwell tasks and reading, reacting to the shown cursor."""
    rig = world.rig
    stats = SessionStats()
    t = 0.0
    phase = "calm"
    events = sorted(script, key=lambda e: e.t)
    shown = np.array(world.rig.world_to_px(world.gaze_world, world.eye_center_world()))
    shown_hist: list[tuple[float, np.ndarray]] = []

    state = "idle"
    target_px = None
    state_until = 0.0
    chase_left = 0
    refractory_until = 0.0
    saccade_t = 0.0
    chasing = False
    pending_click = False
    reading: list[np.ndarray] = []

    def new_task() -> None:
        nonlocal state, target_px, state_until, pending_click, reading
        if rng.random() < reading_share:
            m = rig.monitors[int(rng.integers(len(rig.monitors)))]
            x0 = m.x + m.width * rng.uniform(0.1, 0.35)
            y0 = m.y + m.height * rng.uniform(0.15, 0.7)
            ppd = _ppd(world, m)
            reading = []
            for line in range(3):
                for w in range(int(rng.integers(7, 11))):
                    reading.append(np.array([x0 + w * 2.0 * ppd * rng.uniform(0.8, 1.2), y0 + line * 0.9 * ppd]))
            state = "read"
            look(reading.pop(0))
            state_until = t + rng.uniform(0.18, 0.3)
        else:
            m = rig.monitors[int(rng.integers(len(rig.monitors)))]
            target_px = np.array([m.x + m.width * rng.uniform(0.06, 0.94), m.y + m.height * rng.uniform(0.06, 0.94)])
            state = "dwell"
            look(target_px)
            state_until = t + rng.uniform(0.9, 2.2)
            pending_click = rng.random() < click_rate

    def look(px: np.ndarray) -> None:
        nonlocal saccade_t
        world.gaze_world = rig.px_to_world(float(px[0]), float(px[1]))
        world.step_fixational(True)
        saccade_t = t

    def seen_cursor(delay: float) -> np.ndarray:
        for ts, p in reversed(shown_hist):
            if ts <= t - delay:
                return p
        return shown

    new_task()
    while t < duration:
        while events and events[0].t <= t:
            ev = events.pop(0)
            if ev.kind == "phase":
                phase = str(ev.value)
            elif ev.kind == "head_offset":
                pos, rot = ev.value  # type: ignore[misc]
                world.offset_target = np.asarray(pos, float)
                world.rot_offset_target = np.asarray(rot, float)
            elif ev.kind == "camera_bump":
                idx, rot, shift = ev.value  # type: ignore[misc]
                world.bump_camera(idx, rot, shift)

        # ---- behaviour
        if state == "dwell":
            off_deg = _deg_between(world, shown, target_px)
            if (
                t - saccade_t > 0.35
                and t >= refractory_until
                and off_deg > 1.4
                and rng.random() < p_chase * DT / 0.3
            ):
                state, chase_left, chasing = "chase", int(rng.integers(1, 5)), True
                state_until = t + rng.uniform(0.18, 0.28)
            elif t >= state_until:
                if pending_click:
                    controller.click(float(target_px[0]), float(target_px[1]), t)
                    stats.clicks += 1
                new_task()
        elif state == "chase":
            if t >= state_until:
                if chase_left > 0:
                    look(seen_cursor(0.08))
                    stats.chase_steps += 1
                    chase_left -= 1
                    state_until = t + rng.uniform(0.3, 0.5)
                else:
                    look(target_px)
                    chasing = False
                    state = "dwell"
                    refractory_until = t + 1.2
                    state_until = max(state_until, t + rng.uniform(0.6, 1.2))
        elif state == "read":
            if t >= state_until:
                if reading:
                    look(reading.pop(0))
                    state_until = t + rng.uniform(0.18, 0.32)
                else:
                    new_task()

        world.step_body()
        world.step_fixational(False)
        blink = (t % 4.3) < 0.15
        feat = None if blink else features_from(world.observe(), stereo)
        if feat is not None:
            shown = np.asarray(controller.step(feat, t), dtype=float)
        shown_hist.append((t, shown.copy()))
        if len(shown_hist) > 30:
            shown_hist.pop(0)
        eye = world.eye_center_world()
        stats.frames.append(
            Frame(
                t=t,
                true_px=rig.world_to_px(world.gaze_world, eye),
                shown_px=shown.copy(),
                eye=eye,
                true_world=world.gaze_world.copy(),
                in_fixation=(t - saccade_t) > 0.25 and feat is not None,
                chasing=chasing,
                phase=phase,
                task=state,
            )
        )
        t += DT
    return stats


def _ppd(world: World, m: MonitorGeom) -> float:
    dist = float(np.linalg.norm(m.center - world.eye_center_world()))
    return dist * math.tan(math.radians(1.0)) / (m.width_cm / m.width)


def _deg_between(world: World, a_px: np.ndarray, b_px: np.ndarray) -> float:
    eye = world.eye_center_world()
    pa = world.rig.px_to_world(float(a_px[0]), float(a_px[1])) - eye
    pb = world.rig.px_to_world(float(b_px[0]), float(b_px[1])) - eye
    c = float(pa @ pb / (np.linalg.norm(pa) * np.linalg.norm(pb)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def angular_errors(world: World, stats: SessionStats) -> dict[str, np.ndarray]:
    """Per phase: angle (deg) between the true gaze ray and the ray to the shown cursor."""
    out: dict[str, list[float]] = {}
    for fr in stats.frames:
        if not fr.in_fixation or not np.all(np.isfinite(fr.shown_px)):
            continue
        p = world.rig.px_to_world(float(fr.shown_px[0]), float(fr.shown_px[1])) - fr.eye
        q = fr.true_world - fr.eye
        c = float(p @ q / (np.linalg.norm(p) * np.linalg.norm(q)))
        out.setdefault(fr.phase, []).append(math.degrees(math.acos(max(-1.0, min(1.0, c)))))
    return {k: np.asarray(v) for k, v in out.items()}
