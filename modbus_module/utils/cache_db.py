# -*- coding: utf-8 -*-
"""
本地缓存数据库 (CacheDB)

基于 SQLite 实现，用于在网络中断时暂存待上报的数据。
恢复网络后由 DeviceCommunicator 或上层服务调用 get_all() 获取并补发，
然后调用 clear() 清除。

国际化：内部不产生面向用户的字符串，所有错误信息通过 logging 记录。
"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from modbus_module.utils.logger import get_logger


class CacheDB:
    """本地 SQLite 缓存"""

    def __init__(self, db_path: str = "cache.db", logger = None):
        """
        :param db_path: SQLite 数据库文件路径
        """
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
        self._logger = logger or get_logger(__name__)

    def _init_db(self):
        """初始化数据库和表"""
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()
        self._logger.info("CacheDB initialized at %s", self._db_path)

    def insert(self, device_id: str, data: Dict[str, Any]) -> None:
        """
        插入一条缓存数据。
        :param device_id: 设备ID
        :param data: 数据字典
        """
        if not self._conn:
            return
        try:
            self._conn.execute(
                "INSERT INTO cache_data (device_id, data_json) VALUES (?, ?)",
                (device_id, json.dumps(data, ensure_ascii=False))
            )
            self._conn.commit()
        except Exception as e:
            self._logger.error("Failed to insert cache data: %s", e)

    def get_all(self, device_id: str) -> List[Dict[str, Any]]:
        """
        获取指定设备的所有缓存数据，按时间排序。
        :return: 列表，每项为 {"id": int, "data": dict, "timestamp": str}
        """
        if not self._conn:
            return []
        try:
            cursor = self._conn.execute(
                "SELECT id, data_json, timestamp FROM cache_data WHERE device_id = ? ORDER BY id",
                (device_id,)
            )
            results = []
            for row in cursor:
                data = json.loads(row[1])
                results.append({
                    "id": row[0],
                    "data": data,
                    "timestamp": row[2],
                })
            return results
        except Exception as e:
            self._logger.error("Failed to read cache data: %s", e)
            return []

    def clear(self, device_id: str) -> None:
        """删除指定设备的所有缓存数据"""
        if not self._conn:
            return
        try:
            self._conn.execute(
                "DELETE FROM cache_data WHERE device_id = ?",
                (device_id,)
            )
            self._conn.commit()
        except Exception as e:
            self._logger.error("Failed to clear cache data: %s", e)

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None