"""Deterministic, public test background for inspecting glass optics."""
import os
import json
import numpy as np
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget


def background_image(width, height, offset=0.0):
    y, x = np.mgrid[:height, :width].astype(np.float32)
    u, v = x / width, y / height
    ridge = u + 0.18 * np.sin(v * 4.5 + offset) + v * 0.38
    blend = np.clip((ridge - 0.1) / 1.15, 0, 1)[..., None]
    warm = np.array([240, 139, 93], np.float32)
    cool = np.array([24, 97, 119], np.float32)
    rgb = warm * (1 - blend) + cool * blend
    broad = np.exp(-((ridge - 0.86) / 0.095) ** 2)[..., None]
    rgb = rgb * (1 - broad * 0.68) + np.array([14, 44, 75]) * broad * 0.68
    highlight = np.exp(-((ridge - 0.62) / 0.012) ** 2)[..., None] * 0.78
    rgb += (np.array([240, 239, 204]) - rgb) * highlight
    highlight2 = np.exp(-((ridge - 0.24) / 0.021) ** 2)[..., None] * 0.40
    rgb += (255 - rgb) * highlight2
    rgba = np.empty((height, width, 4), np.uint8)
    rgba[:, :, :3] = np.clip(rgb, 0, 255)
    rgba[:, :, 3] = 255
    return QImage(rgba.data, width, height, width * 4, QImage.Format_RGBA8888).copy()


class ReferenceWindow(QWidget):
    def __init__(self, width, height):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.resize(width, height)
        self.setWindowTitle('GlassDash optical test background')
        self.texture = background_image(width, height)

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawImage(self.rect(), self.texture)
        p.end()


def save_snapshot(win, reference, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    layer = win.grab()
    layer.save(os.path.join(output_dir, 'shot_self.png'))
    composite = reference.grab()
    painter = QPainter(composite)
    painter.drawPixmap(win.pos() - reference.pos(), layer)
    painter.end()
    composite.save(os.path.join(output_dir, 'shot_glass.png'))
    report = {
        'capture_exclusion': win._sampler.excluded,
        'captured_frames': win._sampler.frames,
        'capture_error': win._sampler.error,
        'live_background_available': win._sampler.latest(win._capture_box) is not None,
        'max_edge_displacement_px': win._optics.last_displacement,
        'device_pixel_ratio': win.devicePixelRatioF(),
    }
    with open(os.path.join(output_dir, 'optics_report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    return report
