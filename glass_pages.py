"""Perspective page turns rendered by the existing, bounded animation timer."""
import math
import time
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QImage, QPainter, QFont, QColor, QPen, QPolygonF, QTransform, QPainterPath
from quota_dashboard import paint_quota


class GlassPages:
    PAGE_RECT = QRectF(18, 43, 202, 92)

    def init_pages(self, quota):
        self.quota = quota
        self.page = 0
        self._flip = None
        self._page_images = None
        self.setFocusPolicy(Qt.StrongFocus)
        if quota:
            quota.updated.connect(self._quota_changed)

    def _quota_changed(self):
        self.update()

    def flip_page(self, direction=1):
        if self._flip is not None:
            return
        self._page_images = [self._page_image(self.page), self._page_image(1-self.page)]
        self._flip = (time.monotonic(), 1 if direction > 0 else -1)
        self._interactive_until = time.monotonic() + .65
        self.update()

    def finish_flip(self):
        if self._flip is not None:
            self.page = 1-self.page
            self._flip = None
            self._page_images = None

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y() or ev.pixelDelta().y()
        if delta:
            self.flip_page(1 if delta < 0 else -1)
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        if self.page == 1 and self.quota and ev.button() == Qt.LeftButton:
            self.quota.refresh()
        ev.accept()

    def _page_image(self, page):
        dpr = self.devicePixelRatioF()
        im = QImage(round(250*dpr), round(150*dpr), QImage.Format_ARGB32_Premultiplied)
        im.setDevicePixelRatio(dpr)
        im.fill(Qt.transparent)
        painter = QPainter(im)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        if page == 0:
            self._paint_clear_content(painter, 250, 150)
        else:
            paint_quota(painter, self.quota.states if self.quota else [])
        painter.end()
        return im

    @classmethod
    def face_quad(cls, angle):
        r = cls.PAGE_RECT
        points = []
        for x,y in [(-r.width()/2,-r.height()/2), (r.width()/2,-r.height()/2),
                    (r.width()/2,r.height()/2), (-r.width()/2,r.height()/2)]:
            yy = y*math.cos(angle) + r.height()/2*math.sin(angle)
            z = -y*math.sin(angle) + r.height()/2*math.cos(angle)-r.height()/2
            perspective = 300/(300-z)
            points.append(QPointF(r.center().x()+x*perspective, r.center().y()+yy*perspective))
        return QPolygonF(points)

    def _paint_pages(self, p):
        p.setPen(QColor(248,252,255,190))
        p.setFont(QFont('Segoe UI',7,QFont.DemiBold))
        p.drawText(QRectF(24,18,153,17), Qt.AlignVCenter, 'G L A S S D A S H')
        if self._flip:
            progress = min(1, (time.monotonic()-self._flip[0])/.48)
            if progress >= 1:
                self.finish_flip()
        if self._flip:
            t = progress*progress*(3-2*progress)
            direction = self._flip[1]
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(self.PAGE_RECT.adjusted(-4,-3,4,3), 12,12)
            p.setClipPath(clip)
            r = self.PAGE_RECT
            src = QPolygonF([r.topLeft(),r.topRight(),r.bottomRight(),r.bottomLeft()])
            for im, angle in zip(self._page_images, [-direction*t*math.pi/2, direction*(1-t)*math.pi/2]):
                if abs(math.cos(angle)) < .002:
                    continue
                quad = self.face_quad(angle)
                transform = QTransform()
                if QTransform.quadToQuad(src, quad, transform):
                    p.save()
                    p.setTransform(transform, True)
                    p.setOpacity(.35+.65*abs(math.cos(angle)))
                    p.drawImage(QPointF(0,0), im)
                    p.restore()
                    # A moving, thin specular seam makes the turning edge visible.
                    p.setPen(QPen(QColor(240,252,255,round(60*math.sin(abs(angle)))), .7))
                    p.drawLine(quad[0],quad[1])
            p.restore()
        elif self.page == 0:
            self._paint_clear_content(p, self.width(),self.height())
        else:
            paint_quota(p, self.quota.states if self.quota else [])
        p.setPen(QPen(QColor(255,255,255,165),1.1,Qt.SolidLine,Qt.RoundCap))
        for y, sign in [(65,-1),(114,1)]:
            p.drawPolyline(QPolygonF([QPointF(228,y-sign*2),QPointF(231,y+sign*2),QPointF(234,y-sign*2)]))
        for i,y in enumerate((84,95)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255,255,255,210 if self.page==i else 55))
            p.drawEllipse(QPointF(231,y),1.8,1.8)
