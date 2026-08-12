# -*- coding: utf-8 -*-
"""
设备通信器 (DeviceCommunicator)

每个已启用的设备对应一个 DeviceCommunicator 实例，内部包含一个
_CommunicatorWorker 线程（直接继承 QThread），负责周期性采集数据、
数据转换、浓度换算、状态初判，并通过信号将数据发送至主线程。

特性：
- 所有耗时操作（连接、轮询、解析）均在 Worker 子线程中执行，绝不阻塞 GUI。
- Worker 直接继承 QThread，重写 run() 方法，简化线程管理。
- 重连失败后休眠 10 秒以上，避免疯狂重试导致 CPU 飙升。
- 支持通过 requestInterruption / wait 安全停止线程。
- 外壳 DeviceCommunicator 提供与旧接口兼容的 start/stop 方法，
  并维护连接状态，外部可通过 is_connected 属性或 connection_changed 信号获取。
"""

import time
import threading
from typing import Dict, List, Optional, Any

from PySide6.QtCore import QMetaObject, Q_ARG, Qt, Signal, QObject, QMutex, QThread, Slot

from modbus_module.buffers import RingBuffer
from modbus_module.interfaces import IModbusClient, ModbusException
from modbus_module.utils.cache_db import CacheDB
from modbus_module.utils.conversion import modbus_to_float, modbus_to_int32, modbus_to_int16, ppm_to_ugm3, ppb_to_ugm3
from modbus_module.utils.logger import get_logger


class _CommunicatorWorker(QThread):
    """
    设备通信工作线程（直接继承 QThread）。

    重写 run() 方法，包含采集主循环，通过信号与主线程交互。
    使用 QThread 自带的 requestInterruption() / isInterruptionRequested()
    机制来优雅地停止线程。
    """

    # 信号定义（参数与 DeviceCommunicator 外壳一致）
    data_ready = Signal(str, dict)          # (device_id, {factor: value})
    comm_error = Signal(str, str)          # (device_id, error_message)
    connection_changed = Signal(str, bool)  # (device_id, connected)

    def __init__(
        self,
        device_id: str,
        client: IModbusClient,
        factor_configs: List[Dict[str, Any]],
        connection_params: Dict[str, Any],
        poll_interval: int = 10,
        cache_db: Optional[CacheDB] = None,
        unit_id: int = 1,
        logger = None
    ):
        """
        :param device_id: 设备唯一标识
        :param client: Modbus 客户端实例（未连接）
        :param factor_configs: 因子配置列表（字典形式，已与 ORM 解耦）
        :param connection_params: 连接参数字典，如 {"host": "192.168.1.10", "port": 502}
        :param poll_interval: 轮询间隔（秒）
        :param cache_db: 断网缓存数据库实例（可选）
        :param unit_id: Modbus 从站地址
        """
        super().__init__()
        self._device_id = device_id
        self._client = client
        self._factor_configs = factor_configs
        self._connection_params = connection_params
        self._poll_interval = poll_interval
        self._cache_db = cache_db
        self._unit_id = unit_id

        self._connected = False
        self._request_queue = RingBuffer(maxlen=100)
        self._mutex = QMutex()
        self._logger = logger or get_logger(__name__)

    # ---------- 公共槽（可跨线程安全调用） ----------
    @Slot(dict)
    def send_command(self, command: Dict[str, Any]):
        """添加即时命令到请求队列（对时、校准等）"""
        self._request_queue.put(command)

    @Slot()
    def flush_cache(self):
        """网络恢复后补发缓存数据（在工作线程中执行）"""
        if self._cache_db:
            cached = self._cache_db.get_all(self._device_id)
            for item in cached:
                self.data_ready.emit(self._device_id, item["data"])
            self._cache_db.clear(self._device_id)

    def _manage_connection(self) -> bool:
        """
        管理设备连接状态，封装了连接状态检查、重连、标志同步与信号发射的全部逻辑，返回 bool 表示当前是否可以开始采集。
        同时负责更新内部标志并发射 connection_changed 信号。

        返回值：
        True 表示连接就绪（可采集）
        False 表示需等待后重试。
        """
        # 期望状态临时变量
        new_state = self._connected

        # 如果内部标记未连接，先主动关闭旧连接
        if not self._connected:
            try:
                self._client.disconnect()
            except Exception as e:
                self._logger.debug("Device no - %s disconnect cleanup exception: %s", self._device_id, e)

        # 物理连接正常
        if self._client.is_connected:
            new_state = True
        else:
            # 物理链路断开，尝试重连
            new_state = self._reconnect()

        # ========== 仅当状态发生变化时触发信号 ==========
        if new_state != self._connected:
            self._connected = new_state
            if new_state:
                self._logger.info("[Device no - %s] Connection established/recovered", self._device_id)
            else:
                self._logger.warning("[Device no - %s] Connection lost", self._device_id)
            self.connection_changed.emit(self._device_id, self._connected)

        if self._connected:
            return True
        else:
            # 连接失败，休眠等待下一轮
            self.sleep(10)
            return False

    def run(self):
        """
        采集主循环，运行在子线程中。
        使用 isInterruptionRequested() 检查退出条件，sleep() 可被中断。
        """
        self._logger.info(
            "Communicator worker (Device no - %s) started in thread: %s",
            self._device_id, threading.current_thread().name
        )

        while not self.isInterruptionRequested():   # 相当于一个“退出标记”，让线程内部的循环能够感知到外部请求停止的信号，并安全地退出循环，结束线程。
            try:
                # 1. 处理命令队列中的即时命令
                cmd = self._request_queue.get()
                if cmd:
                    self._handle_command(cmd)

                # 2. 连接管理（False - 连接未就绪时跳过采集并等待下一轮循环）
                if not self._manage_connection():
                    continue    # 连接未就绪，跳过本次采集

                # 3. 采集数据
                data = self._collect_data()
                if data:
                    self.data_ready.emit(self._device_id, data)
                    # 过滤掉值为 None 的失败因子，只记录成功数量
                    valid_count = sum(1 for v in data.values() if v is not None)
                    self._logger.info("[Device no - %s] Collected %d/%d factors", self._device_id, valid_count, len(data))

            except ModbusException as e:
                err_msg = str(e)
                self._logger.warning("Device no - %s communication error: %s", self._device_id, err_msg)
                self.comm_error.emit(self._device_id, self.tr("Communication error: {}").format(err_msg))

                # 主动断开，确保状态同步
                try:
                    self._client.disconnect()
                except:
                    pass

                # self._connected = False
                # self.connection_changed.emit(self._device_id, False)
                self.sleep(3)       # 短暂等待后立即重连
                continue            # 跳过正常轮询间隔，直接进入下一次循环尝试重连

            except Exception as e:
                self._logger.exception("Unexpected error in communicator for device no - %s", self._device_id)
                self.comm_error.emit(self._device_id, self.tr("Unexpected error: {}").format(str(e)))

                # 主动断开，确保状态同步
                try:
                    self._client.disconnect()
                except:
                    pass
                self._connected = False
                self.connection_changed.emit(self._device_id, False)
                self.sleep(10)
                continue            # 跳过正常轮询间隔，直接进入下一次循环尝试重连

            # 正常轮询间隔等待
            self.sleep(self._poll_interval)

        self._logger.info("Communicator worker (Device no - %s) exited.", self._device_id)

    # ---------- 内部方法 ----------
    def _reconnect(self) -> bool:
        """尝试一次 TCP/RTU 连接，成功返回 True，失败返回 False"""
        if not self._connection_params:
            self._logger.warning("Device no - %s missing connection_params", self._device_id)
            return False
        try:
            return self._client.connect(self._connection_params)
        except Exception as e:
            self._logger.error("Reconnect failed for device no - %s: %s", self._device_id, e)
            return False

    def _collect_data(self) -> Optional[Dict[str, float]]:
        """
        根据因子配置采集所有启用的因子。
        单个因子失败时将值设为 None 并记录警告；
        仅当所有因子都失败时，才抛出 ModbusException，由上层触发重连。
        """
        result = {}
        failed_count = 0
        total_count = 0

        for cfg in self._factor_configs:
            factor = cfg.get("factor")
            if not factor or not cfg.get("is_enabled", True):
                continue
            address = cfg.get("register_address")
            if address is None:
                continue

            total_count += 1
            try:
                registers = self._client.read_holding_registers(
                    self._unit_id, address, cfg.get("register_count", 1)
                )
                raw_value = self._parse_registers(registers, cfg)
                standard_value = self._apply_conversion(raw_value, cfg)
                result[factor] = standard_value
            except ModbusException as e:
                self._logger.warning("Failed to read factor %s: %s", factor, e)
                result[factor] = None
                failed_count += 1
            except Exception:
                self._logger.exception("Unexpected error reading factor %s", factor)
                result[factor] = None
                failed_count += 1

        # 如果所有因子均失败（且确实有需要采集的因子），则抛出异常触发重连
        if total_count > 0 and failed_count == total_count:
            raise ModbusException(
                self.tr("All factors (%d) failed to read for device %s") % (total_count, self._device_id)
            )

        return result

    def _parse_registers(self, registers: List[int], cfg: Dict[str, Any]) -> float:
        """
        将原始寄存器值转换为物理量（未进行单位转换的原始值）。
        支持 int16, uint16, int32, uint32, float32 等类型及各种字节序。
        """
        data_type = cfg.get("data_type", "int16")
        byte_order = cfg.get("byte_order", "big_endian")
        scale = cfg.get("scale", 1.0)
        offset = cfg.get("offset", 0.0)

        if data_type == "float32":
            val = modbus_to_float(registers, byte_order)
        elif data_type in ("int32", "uint32"):
            signed = (data_type == "int32")
            val = modbus_to_int32(registers, signed, byte_order)
        elif data_type in ("int16", "uint16"):
            signed = (data_type == "int16")
            val = modbus_to_int16(registers[0], signed)
        else:
            # 默认按有符号16位处理
            val = modbus_to_int16(registers[0], True)

        return val * scale + offset

    def _apply_conversion(self, raw_value: float, cfg: Dict[str, Any]) -> float:
        """
        应用浓度转换（ppm → μg/m³ 等），使用默认环境条件（25°C, 101325 Pa）。
        实际应从气象因子实时获取温度、气压，此处使用固定值。
        """
        convert_type = cfg.get("convert_type", "none")
        if convert_type == "none":
            return raw_value

        mol_weight = cfg.get("molecular_weight", 0.0)
        if convert_type == "ppm_to_ugm3":
            return ppm_to_ugm3(raw_value, mol_weight)
        elif convert_type == "ppb_to_ugm3":
            return ppb_to_ugm3(raw_value, mol_weight)
        return raw_value

    def _handle_command(self, command: Dict[str, Any]):
        """处理即时命令，如对时写入、校准控制等"""
        cmd_type = command.get("type")
        if cmd_type == "write_register":
            address = command.get("address")
            value = command.get("value")
            if address is not None and value is not None:
                self._client.write_register(self._unit_id, address, value)


class DeviceCommunicator(QObject):
    """
    设备通信器（外壳）

    提供与之前相同的接口，内部管理 _CommunicatorWorker 线程。
    外部通过 start() 启动、stop() 停止、send_command() 发送命令。
    连接状态可通过 is_connected 属性或 connection_changed 信号获取。
    """

    # 外壳信号的参数与 Worker 完全一致，直接转发
    data_ready = Signal(str, dict)
    comm_error = Signal(str, str)
    connection_changed = Signal(str, bool)

    def __init__(
        self,
        device_id: str,
        client: IModbusClient,
        factor_configs: List[Dict[str, Any]],
        connection_params: Dict[str, Any],
        poll_interval: int = 10,
        cache_db: Optional[CacheDB] = None,
        unit_id: int = 1,
        logger = None
    ):
        super().__init__()
        self._logger = logger or get_logger(__name__)
        # 创建工作线程
        self._worker = _CommunicatorWorker(
            device_id, client, factor_configs, connection_params,
            poll_interval, cache_db, unit_id, self._logger
        )

        # 转发信号
        self._worker.data_ready.connect(self.data_ready)
        self._worker.comm_error.connect(self.comm_error)
        # 连接 connection_changed 信号：先经过内部槽更新状态，再转发
        self._worker.connection_changed.connect(self._on_worker_connection_changed)

        self._connected = False

    @Slot(str, bool)
    def _on_worker_connection_changed(self, device_id: str, connected: bool):
        """更新内部状态并转发信号"""
        self._connected = connected
        self.connection_changed.emit(device_id, connected)   # 转发给外部（DeviceService）
        self._logger.info("[Device no - %s] Connection status forwarded: %s", device_id, connected)

    @property
    def is_connected(self) -> bool:
        """返回当前连接状态（线程安全：仅读取内部标志）"""
        return self._connected

    def start(self):
        """启动通信线程（QThread.start）"""
        self._worker.start()

    def stop(self):
        """安全停止通信线程"""
        self._worker.requestInterruption()  # 设置中断标志，run() 中的循环会退出
        self._worker.wait()                 # 等待线程完全退出
        self._logger.info("Communicator (Device no - %s) stopped.", self._worker._device_id)

    def send_command(self, command: Dict[str, Any]):
        """向工作线程提交命令（跨线程安全）"""
        QMetaObject.invokeMethod(
            self._worker, "send_command",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(dict, command)
        )

    def flush_cache(self):
        """请求工作线程补发缓存数据"""
        QMetaObject.invokeMethod(
            self._worker, "flush_cache",
            Qt.ConnectionType.QueuedConnection
        )