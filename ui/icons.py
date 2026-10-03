"""Local monochrome vector icons, drawn without emoji fonts or asset downloads."""
from functools import lru_cache
import re
import weakref

from PySide6.QtCore import Qt, QRectF, QSize, QPointF
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget, QHBoxLayout


_PATHS = {
    'book': [[(5,4),(19,4),(19,20),(5,20),(5,4)],[(8,4),(8,20)],[(11,8),(16,8)]],
    'file': [[(6,3),(14,3),(19,8),(19,21),(6,21),(6,3)],[(14,3),(14,8),(19,8)],
             [(9,12),(16,12)],[(9,16),(14,16)]],
    'outline': [[(5,4),(5,18),(10,18)],[(5,8),(10,8)],[(10,5),(20,5),(20,11),(10,11),(10,5)],
                [(10,15),(20,15),(20,21),(10,21),(10,15)]],
    'pen': [[(5,15),(15,5),(19,9),(9,19),(4,20),(5,15)],[(13,7),(17,11)]],
    'play': [[(8,5),(19,12),(8,19),(8,5)]],
    'pause': [[(8,5),(8,19)],[(16,5),(16,19)]],
    'stop': [[(6,6),(18,6),(18,18),(6,18),(6,6)]],
    'skip': [[(7,5),(15,12),(7,19)],[(18,5),(18,19)]],
    'check': [[(5,12),(10,17),(19,7)]],
    'close': [[(6,6),(18,18)],[(18,6),(6,18)]],
    'plus': [[(12,5),(12,19)],[(5,12),(19,12)]],
    'send': [[(5,18),(19,4)],[(7,4),(19,4),(19,16)]],
    'chevron_down': [[(6,9),(12,15),(18,9)]],
    'chevron_up': [[(6,15),(12,9),(18,15)]],
    'pin': [[(8,4),(16,4),(15,10),(18,14),(6,14),(9,10),(8,4)],[(12,14),(12,21)]],
    'shield': [[(12,3),(20,6),(19,14),(16,18),(12,21),(8,18),(5,14),(4,6),(12,3)]],
    'folder': [[(3,6),(9,6),(11,9),(21,9),(21,20),(3,20),(3,6)]],
    'export': [[(5,13),(5,20),(19,20),(19,13)],[(12,16),(12,3)],[(7,8),(12,3),(17,8)]],
    'trash': [[(4,6),(20,6)],[(8,6),(8,3),(16,3),(16,6)],[(6,6),(7,21),(17,21),(18,6)],
              [(10,10),(10,17)],[(14,10),(14,17)]],
    'alert': [[(12,4),(21,20),(3,20),(12,4)],[(12,9),(12,14)],[(12,17),(12,17.1)]],
    'user': [[(5,21),(5,18),(8,14),(16,14),(19,18),(19,21)]],
    'search': [[(15,15),(21,21)]],
    'clock': [[(12,6),(12,12),(16,14)]],
    'retry': [[(4,5),(4,10),(9,10)]],
    'link': [], 'settings': [],
}
_CIRCLES = {'user': [(8,3,8,8)], 'search': [(3,3,14,14)], 'clock': [(3,3,18,18)]}


@lru_cache(maxsize=8)
def _theme_colors(stylesheet):
    match = re.search(r'\*\s*\{[^}]*\bcolor:\s*(#[0-9a-fA-F]{6})', stylesheet)
    normal = match.group(1) if match else '#35393F'
    primary = re.search(r'QPushButton#primaryAction\s*\{[^}]*\bcolor:\s*(#[0-9a-fA-F]{6})', stylesheet)
    inverse = primary.group(1) if primary else '#FFFFFF'
    return normal, inverse


class _LineIcon(QIconEngine):
    def __init__(self, name, owner=None):
        super().__init__()
        self.name = name
        self.owner = weakref.ref(owner) if owner is not None else None

    def clone(self):
        return _LineIcon(self.name, self.owner() if self.owner else None)

    def key(self):
        return 'XiedaoLineIcon'

    def paint(self, painter, rect, mode, state):
        app = QApplication.instance()
        normal, inverse = _theme_colors(app.styleSheet() if app else '')
        owner = self.owner() if self.owner else None
        try:
            primary = owner is not None and owner.objectName() in ('primaryAction', 'sendBtn')
        except RuntimeError:
            primary = False
        color = QColor(inverse if primary else normal)
        if mode == QIcon.Mode.Disabled:
            color.setAlpha(90)
        painter.save()
        side = min(rect.width(), rect.height())
        painter.translate(rect.x() + (rect.width()-side)/2, rect.y() + (rect.height()-side)/2)
        painter.scale(side/24, side/24)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for points in _PATHS[self.name]:
            for first, second in zip(points, points[1:]):
                painter.drawLine(QPointF(*first), QPointF(*second))
        for ellipse in _CIRCLES.get(self.name, []):
            painter.drawEllipse(QRectF(*ellipse))
        if self.name == 'retry':
            painter.drawArc(QRectF(4,4,16,16), 170*16, -290*16)
        elif self.name == 'settings':
            for y, x in ((6,9),(12,15),(18,9)):
                painter.drawLine(4,y,x-2,y)
                painter.drawLine(x+2,y,20,y)
                painter.drawEllipse(QRectF(x-2,y-2,4,4))
        elif self.name == 'link':
            painter.translate(12,12)
            painter.rotate(-35)
            painter.drawRoundedRect(QRectF(-10,-3,12,6),3,3)
            painter.drawRoundedRect(QRectF(-2,-3,12,6),3,3)
        painter.restore()

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = QPixmap(QSize(round(size.width()*scale), round(size.height()*scale)))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRectF(0,0,size.width(),size.height()), mode, state)
        painter.end()
        return pixmap


def line_icon(name, owner=None):
    if name not in _PATHS:
        raise ValueError(f'Unknown UI icon: {name}')
    return QIcon(_LineIcon(name, owner))


def icon_button(text, name, parent=None):
    button = QPushButton(text, parent)
    button.setIcon(line_icon(name, button))
    button.setIconSize(QSize(18,18))
    button.setAccessibleName(text)
    return button


class IconLabel(QLabel):
    def __init__(self, name, parent=None, size=18):
        super().__init__(parent)
        self.name = name
        self.setFixedSize(size,size)

    def paintEvent(self, event):
        painter = QPainter(self)
        line_icon(self.name, self).paint(painter, self.rect())
        painter.end()


def icon_text(text, name, parent=None):
    widget = QWidget(parent)
    row = QHBoxLayout(widget)
    row.setContentsMargins(0,0,0,0)
    row.setSpacing(8)
    row.addWidget(IconLabel(name, widget))
    label = QLabel(text, widget)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    row.addWidget(label, 1)
    return widget


_STATUS_MARKERS = ('❌','⚠','✅','⏹','⏭','⏸','⏳','⏱','📖','📄','📋','💾','📤','🔍','🔎','🔀','🔧','🚀','🤖','↷','↶','✓','⏩')


def legacy_ui_text(message):
    """Clean known historical interface notifications, preserving authored text."""
    message = message.replace('「⚙ 配置模型」', '「配置模型」')
    if re.match(r'^(?:❌|⚠|📤)\ufe0f?\s*(?:调用|任务|书目|已导出|已有|有 |未配置|还没有)', message):
        return status_text(message)
    return message


def status_text(message):
    """Display legacy/generated task status markers as words; never edit source data."""
    result = []
    for line in message.split('\n'):
        indent = line[:len(line)-len(line.lstrip())]
        value = line.lstrip()
        for marker in _STATUS_MARKERS:
            if value.startswith(marker):
                value = value[len(marker):].lstrip('\ufe0f ')
                if marker == '❌':
                    value = '错误：' + value
                elif marker == '⚠':
                    value = '注意：' + value
                break
        result.append(indent + value)
    return '\n'.join(result)
