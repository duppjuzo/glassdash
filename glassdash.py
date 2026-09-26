# -*- coding: utf-8 -*-
r"""
GlassDash — 骁龙 X1E (WOA) 功耗 / 亮度 仪表盘  (Qt6 / PySide6)

托盘常驻：图标实时显示整机功率，tooltip 全量信息。
点击托盘：清透玻璃仪表盘（实时桌面边缘折射、光学色散、响应式高光）。

数据源：
  整机功率  WMI BatteryStatus 充/放电率（电池端实测，放电时=整机消耗）
  SoC 功耗  Windows 系统能耗估算模型：注册表 EnergyEstimation\CPU\EfficiencyClass
            的 频率→功率 曲线 × PDH 实时频率/利用率（估算值，UI 标 ≈）
  亮  度    WMI WmiMonitorBrightness

参数 --shot 供自动化验收：显示窗口后抓屏保存 PNG 并退出。
"""

import sys, os, math, ctypes, time
from ctypes import wintypes
import winreg

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QObject, Signal
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QFont, QCursor, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu
from liquid_glass import DesktopSampler, LiquidGlassRenderer
from quota_dashboard import QuotaController, QuotaSettings
from glass_pages import GlassPages
from battery_stats import BatteryStats, duration
from app_paths import VERSION, data_dir, startup_command

BASE = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------------
# 1. 整机功率 + 亮度：WMI（comtypes / SWbem 自动化层 wbemdisp.tlb）
# ----------------------------------------------------------------------------

import comtypes.client


class WmiClient:
    """root\\WMI 单连接。所有失败静默返回空结果。"""

    def __init__(self):
        self.svc = None
        try:
            locator = comtypes.client.CreateObject("WbemScripting.SWbemLocator")
            self.svc = locator.ConnectServer(".", "root\\wmi")
        except Exception:
            self.svc = None

    def query_rows(self, wql, props):
        """返回 [{prop: value}]，失败 []"""
        rows = []
        if self.svc is None:
            return rows
        try:
            for o in self.svc.ExecQuery(wql):
                try:
                    rows.append({p: o.Properties_[p].Value for p in props})
                except Exception:
                    pass
        except Exception:
            pass
        return rows


# ----------------------------------------------------------------------------
# 2. SoC 估算模型：系统自己的 能耗估算 曲线 (HKLM\...\Power\EnergyEstimation)
#    X1E-78-100: 4× Prime (EfficiencyClass 1) + 8× 效率核 (class 0)
# ----------------------------------------------------------------------------

_REG = r"HKLM\SYSTEM\CurrentControlSet\Control\Power\EnergyEstimation"


def _load_power_curves():
    curves = {0: [], 1: []}
    try:
        for cls in (0, 1):
            base = r"SYSTEM\CurrentControlSet\Control\Power\EnergyEstimation\CPU\EfficiencyClass\%d\PowerCurve" % cls
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                n, _, _ = winreg.QueryInfoKey(k)
                pts = []
                for i in range(n):
                    try:
                        with winreg.OpenKey(k, str(i)) as sk:
                            f, _ = winreg.QueryValueEx(sk, "FrequencyPercent")
                            p, _ = winreg.QueryValueEx(sk, "PowerEnvelope")
                            pts.append((f, p))
                    except OSError:
                        pass
                pts.sort()
                if pts:
                    curves[cls] = pts
    except OSError:
        pass
    if not curves[0]:
        curves[0] = [(0, 30), (20, 53), (40, 81), (60, 111), (80, 182), (100, 348)]
    if not curves[1]:
        curves[1] = [(0, 70), (20, 126), (40, 225), (60, 395), (80, 710), (100, 1100)]
    return curves


def _interp(curve, pct):
    if not curve:
        return 0.0
    if pct <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if pct <= x1:
            return y0 + (y1 - y0) * (pct - x0) / max(x1 - x0, 1)
    return curve[-1][1]


CURVES = _load_power_curves()
P_CORES, E_CORES = 4, 8          # Snapdragon X1E-78-100: 4 Prime + 8 效率核
SOC_FLOOR_W = 0.25               # SoC 静态底噪（模型外修正，显式声明）
SOC_SCALE = 1.0                  # 校准常数：估算整体偏小时可上调


def _supported_soc_model():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            name, _ = winreg.QueryValueEx(key, 'ProcessorNameString')
        return 'X1E78100' in name.replace('-', '').replace(' ', '').upper()
    except OSError:
        return False


SOC_MODEL_SUPPORTED = _supported_soc_model()


def soc_estimate_w(freq_pct, util_pct):
    """按系统曲线估算 SoC(CPU) 功耗，瓦。"""
    busy = max(0.0, min(1.0, util_pct / 100.0))
    f = max(0.0, min(100.0, freq_pct))
    w = (P_CORES * _interp(CURVES[1], f) + E_CORES * _interp(CURVES[0], f)) * busy / 1000.0
    return (w + SOC_FLOOR_W) * SOC_SCALE


# ----------------------------------------------------------------------------
# 3. PDH 性能计数器：频率% / 利用率%
# ----------------------------------------------------------------------------

_pd = ctypes.WinDLL("pdh.dll")


class PDHValue(ctypes.Structure):
    class U(ctypes.Union):
        _fields_ = [("d", ctypes.c_double), ("l", ctypes.c_long), ("ll", ctypes.c_longlong)]
    _fields_ = [("status", ctypes.c_long), ("u", U)]


class PdhCounters:
    def __init__(self, paths):
        self.ok = False
        self.q = wintypes.HANDLE()
        if _pd.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) != 0:
            return
        self.handles = {}
        for p in paths:
            h = wintypes.HANDLE()
            if _pd.PdhAddEnglishCounterW(self.q, p, 0, ctypes.byref(h)) != 0:
                return
            self.handles[p] = h
        _pd.PdhCollectQueryData(self.q)
        self.ok = True

    def read(self, path):
        if not self.ok or path not in self.handles:
            return None
        _pd.PdhCollectQueryData(self.q)
        v = PDHValue()
        if _pd.PdhGetFormattedCounterValue(self.handles[path], 0x200, None, ctypes.byref(v)) != 0:
            return None
        return v.u.d


# ----------------------------------------------------------------------------
# 4. 数据聚合
# ----------------------------------------------------------------------------

class PowerData(QObject):
    updated = Signal(dict)

    FREQ = r"\Processor Information(_Total)\% Processor Performance"
    UTIL = r"\Processor Information(_Total)\% Processor Utility"

    def __init__(self):
        super().__init__()
        self.wmi = WmiClient()
        self.battery_stats = BatteryStats()
        QApplication.instance().aboutToQuit.connect(self.battery_stats.save)
        capacity_rows = self.wmi.query_rows(
            "SELECT FullChargedCapacity FROM BatteryFullChargedCapacity", ["FullChargedCapacity"])
        self.full_capacity = capacity_rows[0].get('FullChargedCapacity') if capacity_rows else None
        if not isinstance(self.full_capacity, (int, float)) or not 0 < self.full_capacity < 0xFFFFFFFF:
            self.full_capacity = None
        self.pdh = PdhCounters([self.FREQ, self.UTIL])
        self.ema = None          # SoC 平滑
        self.hist = []           # 整机功率 sparkline（60 点）
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self):
        d = {"freq": None, "util": None, "soc": None,
             "total": None, "other": None, "charging": None, "ac": None,
             "brightness": None, "hist": self.hist}
        try:
            freq = self.pdh.read(self.FREQ)
            util = self.pdh.read(self.UTIL)
        except Exception:
            freq = util = None
        d["freq"], d["util"] = freq, util
        if SOC_MODEL_SUPPORTED and freq is not None and util is not None:
            soc = soc_estimate_w(freq, util)
            self.ema = soc if self.ema is None else self.ema * 0.6 + soc * 0.4
            d["soc"] = self.ema

        try:
            rows = self.wmi.query_rows(
                "SELECT PowerOnline, Charging, Discharging, DischargeRate, ChargeRate, RemainingCapacity FROM BatteryStatus",
                ["PowerOnline", "Charging", "Discharging", "DischargeRate", "ChargeRate", "RemainingCapacity"])
        except Exception:
            rows = []
        if rows:
            r = rows[0]
            remaining = r.get('RemainingCapacity')
            d['remaining_mwh'] = remaining if isinstance(remaining, (int, float)) and 0 <= remaining < 0xFFFFFFFF else None
            online = bool(r.get("PowerOnline"))
            discharging = bool(r.get("Discharging"))
            dis, chg = r.get("DischargeRate"), r.get("ChargeRate")
            d["ac"] = online
            d["charging"] = bool(r.get("Charging"))
            if discharging and dis is not None and 0 < dis < 0xFFFFFFFF:
                d["total"] = max(dis, 0) / 1000.0        # 放电：整机消耗（实测）
                d["other"] = d["total"] - d["soc"] if d["soc"] is not None else None
            else:
                d["total"] = None                        # 接电时电池端无整机消耗读数
                if chg is not None and 0 <= chg < 0xFFFFFFFF:
                    d["charge_rate"] = chg / 1000.0
            # 曲线记录主显示值：放电=整机实测，接电=SoC 估算
            main_v = d["total"] if d["total"] is not None else d["soc"]
            if main_v is not None:
                self.hist = (self.hist + [main_v])[-60:]
        try:
            rows = self.wmi.query_rows(
                "SELECT CurrentBrightness FROM WmiMonitorBrightness", ["CurrentBrightness"])
            if rows and rows[0].get("CurrentBrightness") is not None:
                d["brightness"] = int(rows[0]["CurrentBrightness"])
        except Exception:
            pass
        d["hist"] = self.hist
        self.battery_stats.update(d, self.full_capacity)
        self.updated.emit(d)


# ----------------------------------------------------------------------------
# 5. 液态玻璃窗口
# ----------------------------------------------------------------------------

class GlassWindow(GlassPages, QWidget):
    GLASS_W, GLASS_H = 250, 150
    RADIUS = 44
    updated = Signal(dict)

    def __init__(self, data: PowerData, quota=None, sampler=None):
        super().__init__()
        self.data = data
        self.init_pages(quota)
        self.d = {"total": None, "soc": None, "other": None, "brightness": None,
                  "charging": None, "ac": None, "hist": [], "freq": None, "util": None}
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('GlassDash')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.GLASS_W, self.GLASS_H)
        self._drag = None
        self._breath = 0.0
        self._pressed = False
        self._scale, self._scale_velocity = 1.0, 0.0
        self._stretch = 0.0
        self._light = (0.0, 0.0)
        self._last_frame = time.monotonic()
        self._interactive_until = self._last_frame + 0.4
        self._last_drag_pos = None
        self._optics = None
        self._button_optics = None
        self._close_pressed = False
        self._close_hover = 0.0
        self._sampler = sampler if sampler is not None else DesktopSampler()
        self._capture_box = None
        self.setMouseTracking(True)
        QApplication.instance().aboutToQuit.connect(self._sampler.close)
        self.updated.connect(self._on_data)
        data.updated.connect(self.updated)

        self.breath_timer = QTimer(self)
        self.breath_timer.setInterval(33)
        self.breath_timer.timeout.connect(self._breathe)

    # ---- 透明窗口和背景采样 ----
    def showEvent(self, ev):
        super().showEvent(ev)
        self._place_bottom_right()
        self._sampler.activate(int(self.winId()))
        self._capture_box = self._sampler.request(int(self.winId()))
        self._scale, self._scale_velocity = 0.965, 0.0
        self._last_frame = time.monotonic()
        self._interactive_until = self._last_frame + 0.4
        self.breath_timer.start()

    def hideEvent(self, ev):
        self.breath_timer.stop()
        self.finish_flip()
        self._sampler.pause()
        self._capture_box = None
        self._pressed = False
        self._close_pressed = False
        self._drag = None
        super().hideEvent(ev)

    def _place_bottom_right(self):
        s = self.screen() or QApplication.primaryScreen()
        g = s.availableGeometry()
        self.move(g.right() - self.width() - 20, g.bottom() - self.height() - 14)

    # ---- 数据 ----
    def _on_data(self, d):
        self.d = d
        self.update()

    def _breathe(self):
        now = time.monotonic()
        dt = min(max(now - self._last_frame, 0.001), 0.032)
        self._last_frame = now
        interval = 33 if now < self._interactive_until or self._pressed else 50
        if self.breath_timer.interval() != interval:
            self.breath_timer.setInterval(interval)
        self._breath = now / 4.0
        target = 0.984 if self._pressed else 1.0
        self._scale_velocity += ((target - self._scale) * 320 - self._scale_velocity * 24) * dt
        self._scale += self._scale_velocity * dt
        self._stretch *= math.exp(-dt * 11)
        cursor = self.mapFromGlobal(QCursor.pos())
        if self.rect().adjusted(-90, -90, 90, 90).contains(cursor):
            tx = max(-1, min(1, (cursor.x() - self.width() / 2) / (self.width() / 2)))
            ty = max(-1, min(1, (cursor.y() - self.height() / 2) / (self.height() / 2)))
        else:
            tx = ty = 0.0
        ease = 1 - math.exp(-dt * 5)
        hover = 1.0 if self._close_contains(QPointF(cursor)) else 0.0
        self._close_hover += (hover - self._close_hover) * (1 - math.exp(-dt * 12))
        self._light = (self._light[0] + (tx - self._light[0]) * ease,
                       self._light[1] + (ty - self._light[1]) * ease)
        self._capture_box = self._sampler.request(int(self.winId()))
        self.update()

    # ---- 交互 ----
    def _close_rect(self):
        return QRectF(self.width() - 56, 14, 28, 28)

    def _close_contains(self, position):
        center = self._close_rect().center()
        return (position.x() - center.x()) ** 2 + (position.y() - center.y()) ** 2 <= 14 ** 2

    def mousePressEvent(self, ev):
        if ev.button() == Qt.RightButton:
            self.hide()
            return
        if ev.button() != Qt.LeftButton:
            return
        if self._close_contains(ev.position()):
            self._close_pressed = True
            self._interactive_until = time.monotonic() + 0.5
            self.update()
            return
        if ev.position().x() >= 221 and 48 <= ev.position().y() <= 130:
            self.flip_page(-1 if ev.position().y() < 89 else 1)
            return
        self.setFocus()
        self._pressed = True
        self._interactive_until = time.monotonic() + 0.5
        self._last_drag_pos = ev.globalPosition().toPoint()
        self._drag = (ev.globalPosition().toPoint() - self.pos())

    def mouseMoveEvent(self, ev):
        self._interactive_until = time.monotonic() + 0.25
        if self._drag is not None:
            point = ev.globalPosition().toPoint()
            if self._last_drag_pos is not None:
                delta = point - self._last_drag_pos
                self._stretch = max(-0.010, min(0.010, (abs(delta.x()) - abs(delta.y())) * 0.0006))
            self._last_drag_pos = point
            self.move(point - self._drag)
            self._capture_box = self._sampler.request(int(self.winId()))

    def mouseReleaseEvent(self, ev):
        if self._close_pressed:
            self._close_pressed = False
            if ev.button() == Qt.LeftButton and self._close_contains(ev.position()):
                self.hide()
            else:
                self.update()
            return
        self._pressed = False
        self._interactive_until = time.monotonic() + 0.5
        self._drag = None

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Up, Qt.Key_PageUp):
            self.flip_page(-1)
        elif ev.key() in (Qt.Key_Down, Qt.Key_PageDown):
            self.flip_page(1)
        elif ev.key() == Qt.Key_Escape:
            self.hide()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        dpr = self.devicePixelRatioF()
        size = (round(self.width() * dpr), round(self.height() * dpr))
        if self._optics is None or (self._optics.width, self._optics.height) != size:
            self._optics = LiquidGlassRenderer(*size, dpr=dpr, radius=self.RADIUS,
                                               optical_scale=0.5, blur_radius=0.7)
        background = self._sampler.latest(self._capture_box)
        sx = self._scale * (1 + self._stretch)
        sy = self._scale * (1 - self._stretch * 0.65)
        frame = self._optics.render(background, phase=self._breath,
                                   pointer=self._light, scales=(sx, sy),
                                   energy=1.0 if self._pressed else 0.0)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(sx, sy)
        p.translate(-self.width() / 2, -self.height() / 2)
        p.drawImage(QPointF(0, 0), frame)
        self._paint_pages(p)
        self._paint_close_button(p, background, dpr)
        p.end()

    def _paint_close_button(self, p, background, dpr):
        rect = self._close_rect()
        size = round(rect.width() * dpr)
        if self._button_optics is None or self._button_optics.width != size:
            self._button_optics = LiquidGlassRenderer(size, size, dpr=dpr, radius=12,
                                                      optical_scale=0.14, blur_radius=0.8)
        crop = None
        if background is not None:
            x, y = round(rect.x() * dpr), round(rect.y() * dpr)
            crop = background[y:y + size, x:x + size]
        scale = 0.92 if self._close_pressed else 1.0 + 0.025 * self._close_hover
        image = self._button_optics.render(crop, phase=self._breath, pointer=self._light,
                                          scales=(scale, scale), energy=self._close_hover)
        p.save()
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.width() / 2, -rect.height() / 2)
        p.drawImage(QPointF(0, 0), image)
        p.setPen(QPen(QColor(255, 255, 255, 80 + round(self._close_hover * 40)), 0.65))
        p.setBrush(QColor(255, 255, 255, 12 + round(self._close_hover * 12)))
        p.drawEllipse(QRectF(3, 3, 22, 22))
        p.setPen(QPen(QColor(255, 255, 255, 206), 1.25, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(10.5, 10.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 10.5), QPointF(10.5, 17.5))
        p.restore()

    def _paint_clear_content(self, p, w, h):
        d = self.d
        total = d.get('total')
        charging = bool(d.get('charging'))
        main = d.get('charge_rate') if charging else total if total is not None else d.get('soc')
        number = f'{main:.1f}' if main is not None else '—'
        p.setFont(QFont('Segoe UI', 23, QFont.Light))
        p.setPen(QColor(255, 255, 255, 237))
        p.drawText(QRectF(23, 41, 112, 37), Qt.AlignVCenter, number)
        advance = p.fontMetrics().horizontalAdvance(number)
        p.setFont(QFont('Segoe UI', 8))
        p.drawText(QRectF(27+advance, 59, 22, 16), 'W')
        p.setFont(QFont('Microsoft YaHei UI', 6))
        p.setPen(QColor(248, 252, 255, 170))
        label = ('充电功率 · 电池净输入' if charging else '整机功耗 · 电池' if total is not None
                 else 'SoC 估算 · 接电' if d.get('soc') is not None and d.get('ac')
                 else 'SoC 估算' if d.get('soc') is not None else '暂无功耗读数')
        p.drawText(QRectF(25, 76, 118, 14), label)
        p.drawText(QRectF(134, 44, 83, 15), Qt.AlignRight, '预计剩余续航')
        p.setFont(QFont('Microsoft YaHei UI', 10, QFont.Light))
        p.setPen(QColor(255, 255, 255, 237))
        eta = '充电中' if d.get('charging') else '已接电' if d.get('ac') else duration(d.get('remaining_seconds'))
        p.drawText(QRectF(123, 61, 94, 22), Qt.AlignRight, eta)
        p.setPen(QPen(QColor(255,255,255,45), .6))
        p.drawLine(QPointF(25,91), QPointF(216,91))
        p.setFont(QFont('Microsoft YaHei UI', 7))
        p.setPen(QColor(250,253,255,215))
        used = duration(d.get('battery_use_seconds'))
        avg = d.get('average_w')
        p.drawText(QRectF(25,94,106,17), '已用 ' + used)
        p.drawText(QRectF(129,94,88,17), Qt.AlignRight, '均耗 ' + (f'{avg:.1f} W' if avg is not None else '—'))
        p.setFont(QFont('Microsoft YaHei UI', 6))
        p.setPen(QColor(248,252,255,174))
        full = '距充满 ' + duration(d['since_full']) if d.get('since_full') is not None else '上次充满：未记录'
        p.drawText(QRectF(25,113,140,16), full)
        bri = d.get('brightness')
        p.drawText(QRectF(161,113,56,16), Qt.AlignRight, f'亮度 {bri}%' if bri is not None else '')
        if d.get('stats_partial'):
            p.setFont(QFont('Microsoft YaHei UI', 5))
            p.setPen(QColor(248,252,255,145))
            p.drawText(QRectF(25,129,180,10), '已用 / 均耗按已记录的电池运行计算')


# ----------------------------------------------------------------------------
# 6. 托盘图标（数字显示整机功率）
# ----------------------------------------------------------------------------

class TrayIcon(QSystemTrayIcon):
    def __init__(self, win: GlassWindow, app):
        super().__init__()
        self.win = win
        self.app = app
        self.d = {}
        win.data.updated.connect(self._on_data)
        self.setIcon(self._icon(None))
        self.setToolTip("GlassDash 启动中…")
        menu = QMenu()
        act = menu.addAction("显示 / 隐藏 仪表盘")
        act.triggered.connect(self.toggle)
        menu.addAction("功耗页", lambda: self.show_page(0))
        menu.addAction("额度页", lambda: self.show_page(1))
        if win.quota:
            menu.addAction("刷新额度", win.quota.refresh)
            menu.addAction("额度设置 / 登录", self.quota_settings)
        self.autorun = menu.addAction("开机自启")
        self.autorun.setCheckable(True)
        self.autorun.setChecked(self._autorun_on())
        self.autorun.triggered.connect(self._toggle_autorun)
        menu.addSeparator()
        act_q = menu.addAction("退出")
        act_q.triggered.connect(self.app.quit)
        self.setContextMenu(menu)
        self.activated.connect(self._activated)

    def show_page(self, page):
        self.win.show()
        self.win.finish_flip()
        if self.win.page != page:
            self.win.flip_page(1 if page else -1)

    def quota_settings(self):
        if not hasattr(self, '_settings'):
            self._settings = QuotaSettings(self.win.quota)
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle()

    def toggle(self):
        if self.win.isVisible():
            self.win.hide()
        else:
            self.win.show()

    # 自启：HKCU\...\Run
    RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _autorun_on(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN) as k:
                v, _ = winreg.QueryValueEx(k, "GlassDash")
                return True
        except OSError:
            return False

    def _toggle_autorun(self):
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.RUN) as k:
                if self.autorun.isChecked():
                    cmd = startup_command()
                    winreg.SetValueEx(k, "GlassDash", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(k, "GlassDash")
                    except FileNotFoundError:
                        pass
        except OSError:
            pass

    def _on_data(self, d):
        self.d = d
        total = d.get("total")
        charging = bool(d.get('charging'))
        self.setIcon(self._icon(d.get('charge_rate') if charging else total))
        if charging:
            rate = d.get('charge_rate')
            head = ("充电中 · 电池净输入 +%.1f W" % rate) if rate is not None else "充电中 · 等待充电功率"
        elif total is not None:
            head = "整机 %.1f W（使用电池）" % total
        elif d.get("ac"):
            head = "已接电 · 未充电"
        else:
            head = "等待电池数据…"
        soc = d.get("soc")
        self.setToolTip("%s\nSoC ≈ %s\n亮度 %s%%" %
                        (head, ("%.1f W（估算）" % soc) if soc is not None else "–",
                         d.get("brightness") if d.get("brightness") is not None else "–"))

    def _icon(self, total):
        pm = QPixmap(96, 96)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        text = "%.0f" % total if total is not None else "·"
        f = QFont("Segoe UI", 30 if len(text) <= 2 else 25, QFont.Bold)
        p.setFont(f)
        p.setPen(QPen(QColor(0, 0, 0, 200), 4))
        p.drawText(QRectF(2, 4, 92, 78), Qt.AlignCenter, text)
        p.setPen(QPen(QColor(255, 255, 255, 245), 2))
        p.drawText(QRectF(2, 4, 92, 78), Qt.AlignCenter, text)
        p.setFont(QFont("Segoe UI", 12, QFont.DemiBold))
        p.setPen(QPen(QColor(0, 0, 0, 180), 3))
        p.drawText(QRectF(2, 62, 92, 30), Qt.AlignCenter, "W")
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.6))
        p.drawText(QRectF(2, 62, 92, 30), Qt.AlignCenter, "W")
        p.end()
        return QIcon(pm)


# ----------------------------------------------------------------------------
# 7. main
# ----------------------------------------------------------------------------

def _single_instance():
    k32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    k32.CreateMutexW.argtypes = [wintypes.HANDLE, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW(None, False, "GlassDash_SingleInstance_Mutex")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main():
    if '--self-test' in sys.argv:
        from diagnostics import run_self_test
        index = sys.argv.index('--self-test')
        report = sys.argv[index + 1] if len(sys.argv) > index + 1 else str(data_dir() / 'self-test.json')
        return run_self_test(report)
    if "--shot" not in sys.argv and not _single_instance():
        return 0
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("GlassDash")
    app.setApplicationVersion(VERSION)
    app.setApplicationDisplayName("GlassDash")

    data = PowerData()
    quota = QuotaController()
    app.aboutToQuit.connect(quota.close)
    win = GlassWindow(data, quota)
    if "--quota" in sys.argv:
        win.page = 1
    tray = TrayIcon(win, app)
    tray.show()

    if "--shot" in sys.argv:
        from glass_preview import ReferenceWindow, save_snapshot
        ref = ReferenceWindow(win.width() + 70, win.height() + 70)
        win._place_bottom_right()
        ref.move(win.x() - 35, win.y() - 35)
        ref.show()
        win.show()
        win.raise_()

        def snap():
            report = save_snapshot(win, ref, os.path.join(BASE, 'probe'))
            print(report)
            ref.hide()
            app.exit(0 if report['live_background_available'] else 2)

        QTimer.singleShot(3200, snap)
    elif "--show" in sys.argv or (getattr(sys, 'frozen', False) and '--tray' not in sys.argv):
        win.show()
    else:
        win.hide()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
