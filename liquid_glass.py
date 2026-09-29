"""Clear glass optics and a memory-only desktop sampler for the Qt overlay.

The window excludes itself from capture before sampling, preventing recursive
feedback. The bevel refracts desktop pixels; an optional faint Gaussian veil
softens the interior without obscuring its colors or structure.
"""
import ctypes
from ctypes import wintypes
import threading
import time

import numpy as np
from PIL import ImageGrab, Image, ImageFilter
from PySide6.QtGui import QImage

_user = ctypes.WinDLL('user32', use_last_error=True)
_user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user.GetWindowRect.restype = wintypes.BOOL
_user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user.SetWindowDisplayAffinity.restype = wintypes.BOOL
_user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user.GetWindowDisplayAffinity.restype = wintypes.BOOL
_user.GetSystemMetrics.argtypes = [ctypes.c_int]
_user.GetSystemMetrics.restype = ctypes.c_int


class DesktopSampler:
    """24 Hz capture off the UI thread, paused whenever the glass is hidden."""
    def __init__(self):
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._box = None
        self._frame = None
        self._frame_box = None
        self.error = None
        self.excluded = False
        self.frames = 0
        self._thread = threading.Thread(target=self._run, name='GlassDashBackdrop', daemon=True)
        self._thread.start()

    def activate(self, hwnd):
        # Do not start sampling if exclusion fails: that would photograph ourselves.
        self.excluded = bool(_user.SetWindowDisplayAffinity(hwnd, 0x11))
        value = wintypes.DWORD()
        self.excluded = self.excluded and bool(
            _user.GetWindowDisplayAffinity(hwnd, ctypes.byref(value))) and value.value == 0x11
        if not self.excluded:
            self.error = 'Capture exclusion unavailable (%d)' % ctypes.get_last_error()
        return self.excluded

    def request(self, hwnd):
        if not self.excluded:
            return None
        rect = wintypes.RECT()
        if not _user.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        box = (rect.left, rect.top, rect.right, rect.bottom)
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        with self._lock:
            self._box = box
        self._wake.set()
        return box

    def latest(self, box):
        with self._lock:
            # Never paint an old screen position after moving the window.
            return self._frame if box is not None and self._frame_box == box else None

    def pause(self):
        with self._lock:
            self._box = self._frame = self._frame_box = None
        self._wake.clear()

    def close(self):
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=1)

    def _probe_capture(self):
        """One-shot 8x8 probe: on this machine GDI screen capture is blocked by
        policy (BitBlt returns a flat frame). Single calls are harmless, but a
        24 Hz retry loop against a blocked path (plus per-layered-window
        PrintWindow storms) froze the whole machine. Probe once, then either
        sample normally or disable sampling forever."""
        try:
            w, h = _user.GetSystemMetrics(0), _user.GetSystemMetrics(1)
            if w <= 0 or h <= 0:
                return False
            cx, cy = w // 2, h // 2
            for box in ((cx, cy, cx + 8, cy + 8), (2, 2, 10, 10)):
                arr = np.asarray(ImageGrab.grab(bbox=box).convert('RGB'))
                if arr.size and float(arr.std()) > 0.5:
                    return True
            return False
        except Exception:
            return False

    def _run(self):
        if not self._probe_capture():
            self.error = 'GDI capture blocked by policy (probe returned flat frame); sampler disabled'
            self.excluded = False
            return
        while not self._stop.is_set():
            self._wake.wait(0.25)
            with self._lock:
                box = self._box
            if box is None:
                continue
            start = time.monotonic()
            try:
                frame = np.asarray(ImageGrab.grab(bbox=box, all_screens=True).convert('RGB')).copy()
                with self._lock:
                    if box == self._box:
                        self._frame, self._frame_box = frame, box
                        self.frames += 1
                self.error = None
            except Exception as exc:
                self.error = str(exc)
                with self._lock:
                    self._frame = self._frame_box = None
                self._stop.wait(0.5)
            self._stop.wait(max(0, 1 / 8 - (time.monotonic() - start)))


BRIGHTNESS_GAIN = 1.15   # 全局提亮（整体玻璃 UI 亮度 ×1.15）


class LiquidGlassRenderer:
    """Signed-distance bevel with refraction, RGB dispersion and surface lighting.

    Geometry and normals are cached. The narrow bevel is sampled each frame;
    optional Gaussian softening is cached for each new background capture.
    """
    def __init__(self, width, height, dpr=1.0, radius=88, optical_scale=1.0, blur_radius=0.0):
        self.width, self.height, self.dpr = width, height, dpr
        self.blur_radius = blur_radius
        self._background_key = None
        self._soft_background = None
        optical_pixel = dpr * optical_scale
        self.cx, self.cy = (width - 1) / 2, (height - 1) / 2
        yy, xx = np.mgrid[:height, :width].astype(np.float32)
        px, py = xx - self.cx, yy - self.cy
        radius *= dpr
        margin = 14 * optical_pixel
        hx, hy = width / 2 - margin, height / 2 - margin
        radius = min(radius, hx, hy)
        qx, qy = np.abs(px) - (hx - radius), np.abs(py) - (hy - radius)
        ox, oy = np.maximum(qx, 0), np.maximum(qy, 0)
        length = np.sqrt(ox * ox + oy * oy)
        sdf = length + np.minimum(np.maximum(qx, qy), 0) - radius
        self.distance = -sdf / optical_pixel
        # Keep the optical bevel clear; diffusion settles on the flat face.
        frost = np.clip((self.distance - 10) / 18, 0, 1)
        self.frost_mask = frost * frost * (3 - 2 * frost)
        nx = np.where(length > 1e-5, ox / np.maximum(length, 1e-5), (qx > qy).astype(np.float32)) * np.sign(px)
        ny = np.where(length > 1e-5, oy / np.maximum(length, 1e-5), (qy >= qx).astype(np.float32)) * np.sign(py)
        coverage = np.clip(0.5 - sdf, 0, 1)
        self.coverage = coverage
        self.indices = np.where((self.distance > -0.6 / optical_pixel) & (self.distance < 20))
        iy, ix = self.indices
        self.x, self.y = ix.astype(np.float32), iy.astype(np.float32)
        self.nx, self.ny = nx[self.indices], ny[self.indices]
        self.d = np.maximum(self.distance[self.indices], 0)
        self.cover = coverage[self.indices]
        # Curved face transitions to a completely flat center over 17 logical px.
        u = np.clip(self.d / 17, 0, 1)
        self.tilt = 0.98 * np.power(1 - u, 2.0)
        self.nz = np.sqrt(1 - self.tilt * self.tilt)
        self.displacement = 19 * np.power(1 - u, 2.2) * optical_pixel
        self.bevel_alpha = np.clip((18.5 - self.d) / 5, 0, 1)
        self.bevel_alpha = self.bevel_alpha * self.bevel_alpha * (3 - 2 * self.bevel_alpha)
        self.bevel_alpha *= self.cover
        self.crest = np.exp(-((self.d - 0.8) / 0.72) ** 2)
        self.inner_caustic = np.exp(-((self.d - 8.8) / 2.9) ** 2)
        # Cast shadow is soft and mainly below/right, rather than a black outline.
        sqx = np.abs(px - 1.5 * optical_pixel) - (hx - radius)
        sqy = np.abs(py - 3 * optical_pixel) - (hy - radius)
        shadow_sdf = np.sqrt(np.maximum(sqx, 0) ** 2 + np.maximum(sqy, 0) ** 2)
        shadow_sdf += np.minimum(np.maximum(sqx, sqy), 0) - radius
        shadow = 0.13 * np.exp(-np.maximum(shadow_sdf, 0) / (3.6 * optical_pixel)) * (1 - coverage)
        self.base = np.zeros((height, width, 4), dtype=np.uint8)
        self.base[:, :, :3] = (15, 22, 32)
        self.base[:, :, 3] = (shadow * 255).astype(np.uint8)
        inside = coverage > 0.99
        self.base[inside, :3] = (255, 255, 255)
        self.base[inside, 3] = 3
        self.last_displacement = float(self.displacement.max())

    @staticmethod
    def _sample_channel(background, x, y, channel):
        h, w = background.shape[:2]
        x = np.clip(x, 0, w - 1.001)
        y = np.clip(y, 0, h - 1.001)
        x0, y0 = x.astype(np.int32), y.astype(np.int32)
        fx, fy = x - x0, y - y0
        a = background[y0, x0, channel] * (1 - fx) + background[y0, x0 + 1, channel] * fx
        b = background[y0 + 1, x0, channel] * (1 - fx) + background[y0 + 1, x0 + 1, channel] * fx
        return a * (1 - fy) + b * fy

    def render(self, background=None, phase=0.0, pointer=(0, 0), scales=(1, 1), energy=0.0):
        # A small moving light, supplemented by cursor position, avoids a looping
        # neon border. Highlights emerge only where the surface normal faces it.
        lx = -0.43 + 0.16 * pointer[0] + 0.035 * np.sin(phase)
        ly = -0.52 + 0.12 * pointer[1] + 0.025 * np.cos(phase * 0.71)
        light = np.array([lx, ly, 1.0], dtype=np.float32)
        light /= np.linalg.norm(light)
        normal_x, normal_y = self.nx * self.tilt, self.ny * self.tilt
        ndh = np.clip(normal_x * light[0] + normal_y * light[1] + self.nz * light[2], 0, 1)
        specular = np.power(ndh, 92) * (1.42 + energy * 0.28)
        back = np.clip(-normal_x * light[0] - normal_y * light[1] + self.nz * light[2], 0, 1)
        specular += np.power(back, 115) * 0.42
        facing = self.nx * light[0] + self.ny * light[1]
        crest = self.crest * (0.43 + np.maximum(facing, 0) * 0.56)
        highlight = np.clip(specular + crest, 0, 0.985)
        # A soft, displaced inner shade and opposite caustic supply the lens depth.
        shade = np.exp(-((self.d - 4.4) / 2.5) ** 2) * (0.06 + np.maximum(-facing, 0) * 0.14)
        caustic = self.inner_caustic * (0.025 + np.maximum(-facing, 0) * 0.045)
        rgba = self.base.copy()
        if background is not None and background.shape[:2] == (self.height, self.width):
            sx, sy = scales
            if self.blur_radius > 0:
                if self._background_key is not background:
                    softened = np.asarray(Image.fromarray(background).filter(
                        ImageFilter.GaussianBlur(self.blur_radius * self.dpr)), dtype=np.float32)
                    # Gentle diffusion, with enough clear transmission to retain
                    # the movement and detail behind the glass.
                    self._soft_background = np.clip(
                        (softened * 0.88 + background * 0.12) * 0.978 + 255 * 0.022,
                        0, 255).astype(np.uint8)
                    self._background_key = background
                background = self._soft_background
                body = Image.fromarray(background)
                if abs(sx - 1) + abs(sy - 1) > 0.0001:
                    body = body.transform((self.width, self.height), Image.Transform.AFFINE,
                        (sx, 0, self.cx * (1 - sx), 0, sy, self.cy * (1 - sy)),
                        Image.Resampling.BILINEAR)
                visible = self.coverage > 0
                rgba[visible, :3] = np.asarray(body)[visible]
                rgba[visible, 3] = (self.coverage[visible] * 255).astype(np.uint8)
            actual_x = self.cx + (self.x - self.cx) * sx
            actual_y = self.cy + (self.y - self.cy) * sy
            rgb = np.empty((len(self.x), 3), dtype=np.float32)
            # Dispersion depends on the captured content, rather than painted rings.
            for c, ratio in enumerate((1.052, 1.0, 0.948)):
                dx = self.nx * self.displacement * ratio * sx
                dy = self.ny * self.displacement * ratio * sy
                rgb[:, c] = self._sample_channel(background, actual_x - dx, actual_y - dy, c)
            rgb *= (1 - shade[:, None])
            rgb += (255 - rgb) * caustic[:, None]
            rgb += (255 - rgb) * highlight[:, None]
            if self.blur_radius > 0:
                blend = (self.bevel_alpha / np.maximum(self.cover, 0.001))[:, None]
                under = rgba[self.indices[0], self.indices[1], :3].astype(np.float32)
                rgb = rgb * blend + under * (1 - blend)
                rgba[self.indices[0], self.indices[1], :3] = np.clip(rgb, 0, 255).astype(np.uint8)
            else:
                rgba[self.indices[0], self.indices[1], :3] = np.clip(rgb, 0, 255).astype(np.uint8)
                rgba[self.indices[0], self.indices[1], 3] = np.maximum(3, self.bevel_alpha * 255).astype(np.uint8)
        else:
            # Safe clear fallback: highlights only if desktop capture is unavailable.
            opacity = np.clip(highlight + caustic + shade, 0, 0.96) * self.cover
            value = 255 * np.clip((highlight + caustic) / np.maximum(highlight + caustic + shade, 0.001), 0, 1)
            rgba[self.indices[0], self.indices[1], :3] = value[:, None].astype(np.uint8)
            rgba[self.indices[0], self.indices[1], 3] = np.maximum(3, opacity * 255).astype(np.uint8)
        if BRIGHTNESS_GAIN != 1.0:
            rgba[..., :3] = np.clip(
                rgba[..., :3].astype(np.float32) * BRIGHTNESS_GAIN, 0, 255).astype(np.uint8)
        if background is not None and background.shape[:2] == (self.height, self.width):
            # Bright desktops need a little neutral density behind white labels.
            # Apply it after the optical highlights so their clear rim stays bright.
            brightness = float(background[::8, ::8, :3].mean())
            density = np.clip((brightness - 145) / 90, 0, 1) * 0.14
            if density > 0:
                attenuation = 1 - self.frost_mask * density
                rgba[..., :3] = np.clip(
                    rgba[..., :3].astype(np.float32) * attenuation[..., None],
                    0, 255).astype(np.uint8)
        image = QImage(rgba.data, self.width, self.height, self.width * 4, QImage.Format_RGBA8888).copy()
        image.setDevicePixelRatio(self.dpr)
        return image
