"""QuotaRing data integration; no desktop capture and no network on the GUI thread."""
import json
import math
import threading
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer, Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPen, QFont, QConicalGradient
from PySide6.QtWidgets import QDialog, QFormLayout, QComboBox, QSpinBox, QPushButton, QLabel, QLineEdit
from oauth_config import google_client, save_google_client
import quota_fetchers as source
from app_paths import state_path, APP_DIR

BASE = Path(__file__).resolve().parent


class QuotaController(QObject):
    updated = Signal()
    result = Signal(object)
    login_result = Signal(bool, str)

    def __init__(self, parent=None, *, start=True, path=None):
        super().__init__(parent)
        self.path = Path(path) if path else state_path('quota_config.json')
        self.cfg = {'interval_sec': 60, 'agy_mode': 'auto'}
        origin = self.path if self.path.exists() else APP_DIR.parent / 'quota_ring' / 'config.json'
        try:
            old = json.loads(origin.read_text(encoding='utf-8'))
            self.cfg['interval_sec'] = max(30, min(3600, int(old.get('interval_sec', 60))))
            if old.get('agy_mode') in ('auto', 'local', 'cloud'):
                self.cfg['agy_mode'] = old['agy_mode']
        except (OSError, ValueError, TypeError):
            pass
        self.states = []
        self.busy = False
        self.closed = False
        self.login_busy = False
        self.result.connect(self._accept)
        self.login_result.connect(self._login_done)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.setInterval(self.cfg['interval_sec'] * 1000)
        if start:
            self.timer.start()
            QTimer.singleShot(0, self.refresh)

    def save(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
        self.timer.setInterval(self.cfg['interval_sec'] * 1000)

    def refresh(self):
        if self.busy or self.closed:
            return
        self.busy = True
        mode = self.cfg['agy_mode']
        def work():
            states = []
            for key, fetch in [('AGY', lambda: source.fetch_agy(mode)), ('Codex', source.fetch_codex)]:
                try:
                    states.append(fetch())
                except Exception:
                    states.append(source.ServiceState(key, error='读取失败，请稍后刷新'))
            if not self.closed:
                self.result.emit(states)
        threading.Thread(target=work, name='GlassDash-quota', daemon=True).start()

    def _accept(self, states):
        self.busy = False
        if not self.closed:
            self.states = states
            self.updated.emit()

    def close(self):
        self.closed = True
        self.timer.stop()

    def login(self):
        if self.login_busy or self.closed:
            return
        self.login_busy = True
        def work():
            try:
                ok, message = source.agy_cloud_login()
            except Exception:
                ok, message = False, '登录失败，请重试'
            if not self.closed:
                self.login_result.emit(ok, message)
        threading.Thread(target=work, name='GlassDash-login', daemon=True).start()

    def _login_done(self, ok, message):
        self.login_busy = False
        if ok:
            self.refresh()

    def tooltip(self):
        lines = ['剩余额度 · 滚轮 / ↑↓ 翻页 · 双击刷新']
        for s in self.states:
            lines.append(s.key + ('' if s.ok else '：' + (s.error or '未连接')))
            for w in s.windows:
                value = '未知' if w.remaining is None else f'{w.remaining:.0f}%'
                lines.append(f'  {w.label}：{value}' + (f' · {w.reset}' if w.reset else ''))
        return '\n'.join(lines)


class QuotaSettings(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle('额度设置')
        self.setMinimumWidth(310)
        form = QFormLayout(self)
        self.mode = QComboBox()
        for label, value in [('自动', 'auto'), ('本地 Antigravity', 'local'), ('Google 云端', 'cloud')]:
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(controller.cfg['agy_mode']))
        self.interval = QSpinBox()
        self.interval.setRange(30, 3600)
        self.interval.setSuffix(' 秒')
        self.interval.setValue(controller.cfg['interval_sec'])
        form.addRow('额度来源', self.mode)
        form.addRow('刷新间隔', self.interval)
        configured = google_client() or {}
        self.client_id = QLineEdit(configured.get('client_id', ''))
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.Password)
        self.client_secret.setPlaceholderText('已配置，留空保留' if configured else 'Google OAuth 客户端密钥')
        form.addRow('Google 客户端 ID', self.client_id)
        form.addRow('Google 客户端密钥', self.client_secret)
        login = QPushButton('登录 Google / Antigravity')
        login.clicked.connect(self.login)
        form.addRow(login)
        self.status = QLabel('复用现有登录；账号令牌不会复制到项目中。')
        self.status.setWordWrap(True)
        form.addRow(self.status)
        controller.login_result.connect(self.login_finished)
        save = QPushButton('保存并刷新')
        save.clicked.connect(self.save)
        form.addRow(save)

    def login_finished(self, ok, message):
        self.status.setText('登录成功' if ok else message)

    def save_client(self):
        existing = google_client() or {}
        client_id = self.client_id.text().strip()
        secret = self.client_secret.text().strip()
        if client_id == existing.get('client_id') and not secret:
            return True
        if not client_id and not secret:
            return True
        if not client_id or not secret:
            self.status.setText('更改客户端时请同时填写 ID 和密钥。')
            return False
        try:
            save_google_client(client_id, secret)
        except OSError:
            self.status.setText('无法保存本机 OAuth 配置。')
            return False
        self.client_secret.clear()
        self.client_secret.setPlaceholderText('已配置，留空保留')
        return True

    def login(self):
        if self.save_client():
            self.controller.login()

    def save(self):
        if not self.save_client():
            return
        self.controller.cfg.update(agy_mode=self.mode.currentData(), interval_sec=self.interval.value())
        try:
            self.controller.save()
        except OSError:
            self.status.setText('设置保存失败，请检查目录权限。')
            return
        self.controller.refresh()
        self.accept()


def paint_quota(p, states):
    colors = [QColor('#62caff'), QColor('#88f4cf')]
    valid = [s for s in states if s.ok and s.ring is not None and math.isfinite(s.ring)]
    center = QPointF(65, 86)
    for i, s in enumerate(states[:2]):
        if s not in valid:
            continue  # Missing data never becomes an empty track or zero.
        radius = 34 - i * 9
        rect = QRectF(center.x()-radius, center.y()-radius, radius*2, radius*2)
        color = QColor('#ff8d90') if s.ring < 20 else colors[i]
        p.setPen(QPen(QColor(255, 255, 255, 28), 5))
        p.drawArc(rect, 90*16, -360*16)
        gradient = QConicalGradient(center, 90)
        gradient.setColorAt(0, color)
        gradient.setColorAt(.25, QColor(245, 255, 255, 240))
        gradient.setColorAt(.55, color)
        gradient.setColorAt(.8, QColor(255, 255, 255, 185))
        gradient.setColorAt(1, color)
        span = 100 if s.ring <= 0 else max(0, min(100, s.ring))
        p.setPen(QPen(gradient, 5, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 90*16, -round(span*3.6*16))
        p.setPen(QPen(QColor(255, 255, 255, 170), .65))
        p.drawArc(rect.adjusted(-2, -2, 2, 2), 90*16, -round(span*3.6*16))
    lowest = min(valid, key=lambda s: s.ring) if valid else None
    p.setPen(QColor(255, 255, 255, 242))
    p.setFont(QFont('Segoe UI', 15, QFont.Light))
    p.drawText(QRectF(37, 70, 56, 25), Qt.AlignCenter, f'{lowest.ring:.0f}%' if lowest else '—')
    p.setFont(QFont('Segoe UI', 6))
    p.drawText(QRectF(38, 95, 54, 12), Qt.AlignCenter, lowest.key if lowest else '等待数据')
    for i, name in enumerate(('AGY', 'Codex')):
        s = states[i] if i < len(states) else None
        y = 57 + i*33
        p.setPen(colors[i])
        p.setFont(QFont('Segoe UI', 8, QFont.DemiBold))
        p.drawText(QRectF(113, y, 56, 17), name)
        p.setPen(QColor(255, 255, 255, 230))
        p.setFont(QFont('Segoe UI', 10, QFont.Light))
        p.drawText(QRectF(164, y-1, 43, 19), Qt.AlignRight, f'{s.ring:.0f}%' if s in valid else '—')
        p.setFont(QFont('Microsoft YaHei UI', 6))
        p.setPen(QColor(240, 250, 255, 155))
        label = '剩余额度' if s in valid else ('未连接' if s else '正在读取')
        p.drawText(QRectF(113, y+17, 96, 13), label)
