# -*- coding: utf-8 -*-
"""
线程安全环形缓冲区 (RingBuffer)

基于 collections.deque + QMutexLocker，用于生产者‑消费者模型。
支持最大长度限制，防止内存无限增长。
"""

from collections import deque
from typing import Any, Optional
from PySide6.QtCore import QMutex, QMutexLocker


class RingBuffer:
    """线程安全有界环形缓冲区"""

    EMPTY = object()  # 哨兵对象，表示缓冲区为空

    def __init__(self, maxlen: int = 5000):
        """
        :param maxlen: 缓冲区最大容量，超出时自动丢弃最早数据
        """
        self._buffer = deque(maxlen=maxlen)
        self._mutex = QMutex()

    def put(self, item: Any) -> None:
        """加入一个元素（线程安全）"""
        with QMutexLocker(self._mutex):
            self._buffer.append(item)

    def get(self) -> Optional[Any]:
        """取出并移除最早元素，若为空返回 None（线程安全）"""
        with QMutexLocker(self._mutex):
            if self._buffer:
                return self._buffer.popleft()
            return RingBuffer.EMPTY

    def size(self) -> int:
        """返回当前元素个数（线程安全）"""
        with QMutexLocker(self._mutex):
            return len(self._buffer)

    def clear(self) -> None:
        """清空缓冲区（线程安全）"""
        with QMutexLocker(self._mutex):
            self._buffer.clear()

    def peek(self) -> Optional[Any]:
        """查看最早元素但不移除（线程安全）"""
        with QMutexLocker(self._mutex):
            if self._buffer:
                return self._buffer[0]
            return None