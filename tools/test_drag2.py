# -*- coding: utf-8 -*-
"""验证拖动规则（第二版，补齐 show/processEvents，让几何真正算出来）。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
_sp = os.path.join(HERE, "overlay_qt.json")
if os.path.exists(_sp):
    os.remove(_sp)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint, Qt
app = QApplication([])
import overlay_qt as M

o = M.QtOverlay()
o.w.show()
app.processEvents()

ok = fail = 0
def chk(n, c):
    global ok, fail
    if c:
        ok += 1; print(f"  ok   {n}")
    else:
        fail += 1; print(f"  FAIL {n}")

class E:
    def __init__(self, gp, btn=None):
        self._gp = gp; self._btn = btn
    def button(self): return self._btn
    def buttons(self): return self._btn
    def globalPos(self): return self._gp
    def pos(self): return QPoint(self._gp.x() - o.w.x(), self._gp.y() - o.w.y())

def at(lx, ly):
    return QPoint(o.w.x() + lx, o.w.y() + ly)

press, move, release = o.w.mousePressEvent, o.w.mouseMoveEvent, o.w.mouseReleaseEvent

print("=== 默认：无框 ===")
chk("bg_alpha 默认 0（没有框）", float(o.st["bg_alpha"]) == 0.0)
chk("edit_mode 默认关", o.st["edit_mode"] is False)

print("\n=== 默认：整块可拖（空白处）===")
p0 = o.w.pos()
start = at(8, o.w.height() - 8)
press(E(start, Qt.LeftButton))
move(E(QPoint(start.x() + 70, start.y() - 25), Qt.LeftButton))
moved = o.w.pos()
chk("空白处拖动生效", moved != p0)
release(E(QPoint(start.x() + 70, start.y() - 25), Qt.LeftButton))
chk("抬起后保存位置", o.st["x"] == moved.x())

print("\n=== 默认：整块可拖（含文字上）===")
o.st["hold_seconds"] = 9999
o.clear()
o.add("the quick brown fox jumps over the lazy dog",
      "敏捷的棕色狐狸跳过了懒狗")
o._relayout()
app.processEvents()
lbl = o.entries[0][3]
lp = lbl.mapTo(o.w, QPoint(lbl.width() // 2, lbl.height() // 2))
print(f"     label 几何 {lbl.geometry()}  中心 ({lp.x()},{lp.y()})")
before = o.w.pos()
start = at(lp.x(), lp.y())
press(E(start, Qt.LeftButton))
move(E(QPoint(start.x() + 90, start.y() + 30), Qt.LeftButton))
chk("按在文字上也能拖（成熟方案：整块 grab）", o.w.pos() != before)
release(E(QPoint(start.x() + 90, start.y() + 30), Qt.LeftButton))

print("\n=== 调整模式：只认把手 ===")
o.toggle_edit()
chk("M 打开调整模式", o.st["edit_mode"] is True)
before = o.w.pos()
start = at(8, o.w.height() - 8)
press(E(start, Qt.LeftButton))
move(E(QPoint(start.x() + 70, start.y() - 25), Qt.LeftButton))
chk("调整模式下空白处不拖", o.w.pos() == before)
release(E(QPoint(start.x() + 70, start.y() - 25), Qt.LeftButton))

g = o._grip_rect()
start = at(g.center().x(), g.center().y())
press(E(start, Qt.LeftButton))
move(E(QPoint(start.x() + 55, start.y() + 20), Qt.LeftButton))
chk("调整模式下把手可拖", o.w.pos() != before)
release(E(QPoint(start.x() + 55, start.y() + 20), Qt.LeftButton))

print("\n=== 阈值 ===")
o.toggle_edit()
before = o.w.pos()
start = at(8, o.w.height() - 8)
press(E(start, Qt.LeftButton))
move(E(QPoint(start.x() + 2, start.y() - 2), Qt.LeftButton))
chk("微动 2px 仍不触发", o.w.pos() == before)
release(E(QPoint(start.x() + 2, start.y() - 2), Qt.LeftButton))

print(f"\n合计 {ok} 通过 / {fail} 失败")
o.close()
sys.exit(0 if fail == 0 else 1)
