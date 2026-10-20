"""操作日志与撤销/重做栈。

界面把"用户每一次操作"记成一份**完整状态快照**（控件取值 + 数据引用），
Ctrl+Z / Ctrl+Y 就是在这份快照序列上前后移动。快照只存引用、不复制像素，
所以记录成本可以忽略。
"""

from __future__ import annotations

DEFAULT_LABEL = "调整参数"


class ActionHistory:
    """Undo/redo stack over labelled state snapshots.

    第 0 项是基线状态；``push()`` 追加新记录并丢弃"重做尾巴"，与常见的
    文档历史语义一致——撤消后再做新操作，原来的重做分支就作废。
    """

    def __init__(self, limit: int = 200):
        self.limit = limit
        self._entries: list[tuple[str, object, object]] = []  # (label, state, signature)
        self._index = -1

    def __len__(self):
        return len(self._entries)

    @property
    def index(self):
        return self._index

    @property
    def can_undo(self):
        return self._index > 0

    @property
    def can_redo(self):
        return 0 <= self._index < len(self._entries) - 1

    def labels(self):
        return [label for label, _state, _sig in self._entries]

    def current(self):
        if self._index < 0:
            return None
        label, state, _sig = self._entries[self._index]
        return label, state

    def reset(self, label, state, signature=None):
        self._entries = [(label, state, signature)]
        self._index = 0

    def push(self, label, state, signature=None):
        """追加一条操作记录；签名与当前项相同则视为"状态没变"并跳过。"""
        if self._index >= 0 and signature is not None:
            if self._entries[self._index][2] == signature:
                return False
        del self._entries[self._index + 1:]
        self._entries.append((label, state, signature))
        if len(self._entries) > self.limit:
            del self._entries[: len(self._entries) - self.limit]
        self._index = len(self._entries) - 1
        return True

    def undo(self):
        if not self.can_undo:
            return None
        self._index -= 1
        label, state, _sig = self._entries[self._index]
        return label, state

    def redo(self):
        if not self.can_redo:
            return None
        self._index += 1
        label, state, _sig = self._entries[self._index]
        return label, state

    def goto(self, index):
        """直接跳到第 index 条（点日志列表用），返回该条 (label, state)。"""
        if not self._entries:
            return None
        self._index = max(0, min(int(index), len(self._entries) - 1))
        label, state, _sig = self._entries[self._index]
        return label, state
