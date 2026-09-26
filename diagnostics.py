"""Packaged runtime check: synthetic glass, native WMI, no auth or desktop capture."""
import json
import struct
import sys
import time
import traceback
from pathlib import Path


def pe_arch(path):
    with open(path, 'rb') as f:
        f.seek(0x3c)
        offset = struct.unpack('<I', f.read(4))[0]
        f.seek(offset + 4)
        machine = struct.unpack('<H', f.read(2))[0]
    return {0xAA64: 'arm64', 0x8664: 'x64'}.get(machine, hex(machine))


def run_self_test(output):
    report = {'ok': False, 'architecture': pe_arch(sys.executable), 'frozen': bool(getattr(sys, 'frozen', False))}
    path = Path(output).resolve()
    try:
        import tempfile
        import numpy as np
        import requests
        import comtypes.client
        import quota_fetchers
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QObject, Signal, Qt
        from PySide6.QtGui import QImage, QPainter
        from glassdash import GlassWindow, WmiClient, SOC_MODEL_SUPPORTED
        from quota_dashboard import QuotaController
        from glass_preview import background_image
        from battery_stats import BatteryStats
        from app_paths import VERSION, startup_command
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        class Data(QObject):
            updated = Signal(dict)
        class Sampler:
            def activate(self, hwnd): pass
            def request(self, hwnd): return None
            def latest(self, box): return self.frame
            def pause(self): pass
            def close(self): pass
        with tempfile.TemporaryDirectory(prefix='GlassDash-check-') as folder:
            q = QuotaController(start=False, path=Path(folder)/'quota.json')
            q.states = [quota_fetchers.ServiceState('agy',True,76), quota_fetchers.ServiceState('codex',True,42)]
            q.save()
            assert q.path.is_file()
            stats = BatteryStats(Path(folder)/'battery.json')
            stats.save()
            assert stats.path.is_file()
            data = Data()
            sampler = Sampler()
            win = GlassWindow(data, q, sampler)
            dpr = win.devicePixelRatioF()
            bg = background_image(round(250*dpr), round(150*dpr))
            sampler.frame = np.frombuffer(bg.bits(), np.uint8).reshape(bg.height(),bg.width(),4)[:,:,:3].copy()
            win.d.update(total=12.8, brightness=65, remaining_seconds=18000, battery_use_seconds=7200, average_w=10.6)
            win.show()
            win.breath_timer.stop()
            app.processEvents()
            for page in (0,1):
                win.page = page
                assert not win.grab().isNull()
            win.d.update(charging=True, charge_rate=45.6, ac=True, total=None)
            win.page=0
            assert not win.grab().isNull()
            win.flip_page(1)
            win._flip = (time.monotonic()-.24,1)
            assert not win.grab().isNull()
            win.hide()
            assert win._flip is None and win._page_images is None
            q.close()
        wmi = WmiClient()
        rows = wmi.query_rows('SELECT RemainingCapacity FROM BatteryStatus', ['RemainingCapacity'])
        report.update(ok=True, version=VERSION, rendered_pages=2, charging_render=True,
                      perspective_render=True, wmi_connected=wmi.svc is not None,
                      battery_rows=len(rows), soc_model_supported=SOC_MODEL_SUPPORTED,
                      startup_targets_executable=sys.executable in startup_command(),
                      requests_ca_present=Path(requests.certs.where()).is_file())
    except Exception:
        report['error'] = traceback.format_exc()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return 0 if report['ok'] else 1
