"""Safe regression check: synthetic backdrop, no desktop capture or real login."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time
import tempfile
import threading
import numpy as np
from PySide6.QtCore import QObject, Signal, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QFontDatabase, QFont
from glassdash import GlassWindow
from glass_preview import background_image
from quota_dashboard import QuotaController, source

class Data(QObject):
    updated = Signal(dict)

class Sampler:
    def activate(self, hwnd): pass
    def request(self, hwnd): return None
    def pause(self): pass
    def close(self): pass
    def latest(self, box): return self.bg

app = QApplication([])
for font in ('segoeui.ttf', 'segoeuil.ttf', 'seguisb.ttf', 'msyh.ttc'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + font)
app.setFont(QFont('Segoe UI', 9))
app.setQuitOnLastWindowClosed(False)
data = Data()
quota = QuotaController(start=False)
quota.states = [source.ServiceState('agy', True, 76), source.ServiceState('codex', True, 42)]
sampler = Sampler()
win = GlassWindow(data, quota, sampler)
dpr = win.devicePixelRatioF()
bg = background_image(round(250*dpr), round(150*dpr))
sampler.bg = np.asarray(bg.bits()).reshape(bg.height(),bg.width(),4)[:,:,:3].copy()
win.show()
win.breath_timer.stop()
win._scale = 1
win.d.update(total=12.8, brightness=65, hist=[8,9,7,12,10,13,12])
win.d.update(remaining_seconds=19800, battery_use_seconds=7320, average_w=10.6,
             since_full=14400, stats_partial=True)
out = Path(__file__).parent / 'artifacts'
out.mkdir(exist_ok=True)
def render(name):
    composite = bg.copy()
    composite.setDevicePixelRatio(dpr)
    p = QPainter(composite)
    p.drawPixmap(0,0,win.grab())
    p.end()
    composite.save(str(out / (name+'.png')))
render('power')
assert win._close_rect().x() == 194
win.flip_page(1)
assert win._flip
first = win._flip
win.flip_page(-1)
assert win._flip == first
win._flip = (time.monotonic()-.24, 1)
render('turn')
win._flip = (time.monotonic()-.5, 1)
render('quota')
assert win.page == 1 and win._page_images is None
QTest.keyClick(win, Qt.Key_Up)
assert win._flip[1] == -1
win.hide()
assert win._flip is None and not win.breath_timer.isActive()
win.show()
win.breath_timer.stop()
QTest.mouseClick(win, Qt.LeftButton, pos=QPoint(231,114))
assert win._flip is not None
win.finish_flip()
QTest.mouseClick(win, Qt.LeftButton, pos=QPoint(208,28))
assert not win.isVisible()

# A burst of refresh requests must start just one worker; a failed source
# must not discard the other service or turn into a zero quota.
gate = threading.Event()
calls = []
def agy(mode):
    calls.append('agy')
    gate.wait(2)
    raise RuntimeError('fixture')
source.fetch_agy = agy
source.fetch_codex = lambda: source.ServiceState('codex', True, 42)
for _ in range(30): quota.refresh()
assert quota.busy
gate.set()
deadline = time.monotonic()+3
while quota.busy and time.monotonic()<deadline:
    app.processEvents()
    time.sleep(.01)
assert calls == ['agy'] and not quota.busy
assert not quota.states[0].ok and quota.states[0].ring is None
assert quota.states[1].ring == 42
quota.close()
quota.refresh()
assert not quota.busy
print('PASS: close position/hit, page projection, repeated input, hide cleanup, worker gating, partial failure')
print('Synthetic preview:', out)
