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
from modbus_module.utils.conversion import (
    modbus_to_float, modbus_to_int32, modbus_to_int16,
    ppm_to_ugm3, ppb_to_ugm3, float_to_registers,
    int32_to_registers, string_to_registers, modbus_to_string
)
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

    @property
    def device_id(self) -> str:
        """设备唯一标识（只读）"""
        return self._device_id

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
        """
        new_state = self._connected

        # 直接检查物理连接状态
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
                if cmd is not RingBuffer.EMPTY:   # 使用哨兵对象判断是否为空
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

    def _collect_data(self) -> Optional[Dict[str, Any]]:
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
                # 根据数据类型自动确定所需寄存器数量（仅当用户未显式指定 register_count 时）
                data_type = cfg.get("data_type", "int16")
                if "register_count" in cfg and cfg.get("register_count") is not None:
                    reg_count = cfg.get("register_count")
                else:
                    # 32 位数据类型需要 2 个寄存器，其他默认 1 个
                    if data_type in ("float", "int32", "uint32"):
                        reg_count = 2
                    elif data_type == "string":
                        # 字符串类型必须显式指定 register_count，否则默认读 1 个寄存器（2个字符）
                        self._logger.warning("String factor %s missing register_count, default to 1", factor)
                        reg_count = 1
                    else:
                        reg_count = 1

                registers = self._client.read_holding_registers(
                    self._unit_id, address, reg_count
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

    def _parse_registers(self, registers: List[int], cfg: Dict[str, Any]) -> Any:
        """
        将原始寄存器值转换为物理量（未进行单位转换的原始值）。
        支持 int16, uint16, int32, uint32, float, string 等类型及各种字节序。
        """
        data_type = cfg.get("data_type", "int16")
        byte_order = cfg.get("byte_order", "big_endian")
        scale = cfg.get("scale", 1.0)
        offset = cfg.get("offset", 0.0)

        # 字符串类型不需要数量校验
        if data_type != "string":
            expected_count = 2 if data_type in ("float", "int32", "uint32") else 1
            if len(registers) != expected_count:
                self._logger.warning(
                    "Register count mismatch for factor %s: expected %d, got %d. Data type %s",
                    cfg.get("factor", "unknown"), expected_count, len(registers), data_type
                )
                # 对于 32 位类型，若寄存器不足，抛出异常，由上层处理（单因子失败不影响其他因子）
                if len(registers) < expected_count:
                    raise ModbusException(f"Insufficient registers for {data_type}")

        if data_type == "float":
            val = modbus_to_float(registers, byte_order)
        elif data_type in ("int32", "uint32"):
            signed = (data_type == "int32")
            val = modbus_to_int32(registers, signed, byte_order)
        elif data_type in ("int16", "uint16"):
            signed = (data_type == "int16")
            val = modbus_to_int16(registers[0], signed)
        elif data_type == "string":
            val = modbus_to_string(registers, byte_order)
            return val
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
        """
        处理即时命令，如对时写入、校准控制、带数据类型的写入等。
        支持的命令类型：
            - "write_register": 写单个 16 位寄存器（兼容原有方式）
            - "write_data": 写入任意数据类型的值（自动编码为寄存器列表并批量写入）
            - "write_multiple_data": 批量写入多个不同数据类型的数据点
        """
        cmd_type = command.get("type")
        if cmd_type == "write_register":
            address = command.get("address")
            value = command.get("value")
            if address is not None and value is not None:
                try:
                    self._client.write_register(self._unit_id, address, value)
                    self._logger.info(
                        "Write register command executed: device=%s, address=%s, value=%s",
                        self._device_id, address, value
                    )
                except ModbusException as e:
                    self._logger.error("Write register command failed for device=%s: %s", self._device_id, e)
                    self.comm_error.emit(self._device_id, f"Write register failed: {e}")
                except Exception as e:
                    self._logger.exception("Unexpected error during write register command for device=%s",
                                           self._device_id)
                    self.comm_error.emit(self._device_id, f"Unexpected write register error: {e}")

        elif cmd_type == "write_data":
            address = command.get("address")
            value = command.get("value")
            data_type = command.get("data_type", "int16")  # 默认 int16
            byte_order = command.get("byte_order", "big_endian")
            if address is None or value is None:
                self._logger.warning("Write data command missing address or value: %s", command)
                return
            try:
                # 将原始值编码为寄存器列表
                registers = self._encode_value_to_registers(value, data_type, byte_order)
                # 使用批量写入方法
                self._client.write_registers(self._unit_id, address, registers)
                self._logger.info(
                    "Write data command executed: device=%s, address=%s, type=%s, byte_order=%s, registers=%s",
                    self._device_id, address, data_type, byte_order, registers
                )
            except ModbusException as e:
                self._logger.error("Write data command failed for device=%s: %s", self._device_id, e)
                self.comm_error.emit(self._device_id, f"Write data failed: {e}")
            except Exception as e:
                self._logger.exception("Unexpected error during write data command for device=%s", self._device_id)
                self.comm_error.emit(self._device_id, f"Unexpected write data error: {e}")

        elif cmd_type == "write_multiple_data":
            # 新增：批量写入多个不同数据类型的数据点
            items = command.get("items", [])
            if not isinstance(items, list) or not items:
                self._logger.warning("Write multiple data command missing items or empty: %s", command)
                return
            # 遍历每个数据项，逐个编码并写入
            for idx, item in enumerate(items):
                # 每个 item 应包含 address, value, data_type, byte_order
                address = item.get("address")
                value = item.get("value")
                data_type = item.get("data_type", "int16")
                byte_order = item.get("byte_order", "big_endian")
                if address is None or value is None:
                    self._logger.warning("Item %d missing address or value, skipped", idx)
                    continue
                try:
                    # 将原始值编码为寄存器列表
                    registers = self._encode_value_to_registers(value, data_type, byte_order)
                    # 执行批量写入（此处为单个数据点，但底层支持连续寄存器批量写）
                    self._client.write_registers(self._unit_id, address, registers)
                    self._logger.info(
                        "Write multiple data item %d executed: device=%s, address=%s, type=%s, byte_order=%s, registers=%s",
                        idx, self._device_id, address, data_type, byte_order, registers
                    )
                except ModbusException as e:
                    self._logger.error("Write multiple data item %d failed: %s", idx, e)
                    self.comm_error.emit(self._device_id, f"Write multiple data item {idx} failed: {e}")
                except Exception as e:
                    self._logger.exception("Unexpected error during write multiple data item %d", idx)
                    self.comm_error.emit(self._device_id, f"Unexpected write multiple data error: {e}")
        else:
            self._logger.warning("Unknown command type: %s", cmd_type)

    def _encode_value_to_registers(self, value: float, data_type: str, byte_order: str) -> List[int]:
        """
        根据数据类型和字节序，将原始值（int/float）编码为 Modbus 寄存器列表。

        :param value: 待编码的值
        :param data_type: 数据类型，支持 'int16', 'uint16', 'int32', 'uint32', 'float', 'string'
        :param byte_order: 字节序，与 _parse_registers 中使用的字节序定义一致
        :return: 编码后的寄存器值列表
        :raises ValueError: 数据类型不支持或字节序非法
        """
        if data_type == "float":
            return float_to_registers(value, byte_order)
        elif data_type in ("int32", "uint32"):
            signed = (data_type == "int32")
            return int32_to_registers(int(value), signed, byte_order)
        elif data_type in ("int16", "uint16"):
            signed = (data_type == "int16")
            # 单个寄存器，需确保数值在 16 位范围内
            if signed and not (-32768 <= int(value) <= 32767):
                raise ValueError("Int16 value out of range")
            if not signed and not (0 <= int(value) <= 65535):
                raise ValueError("Uint16 value out of range")
            return [int(value)]
        elif data_type == "string":
            return string_to_registers(str(value), byte_order)
        else:
            raise ValueError(f"Unsupported data_type: {data_type}")


class DeviceCommunicator(QObject):
    """
    设备通信器（外壳）

    提供与之前相同的接口，内部管理 _CommunicatorWorker 线程。
    外部通过 start() 启动、stop() 停止、send_command() 发送命令。
    连接状态可通过 is_connected 属性或 connection_changed 信号获取。
    支持多次 start/stop 安全重启。
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

        # 保存构造参数，以便重新创建 Worker
        self._device_id = device_id
        self._client = client
        self._factor_configs = factor_configs
        self._connection_params = connection_params
        self._poll_interval = poll_interval
        self._cache_db = cache_db
        self._unit_id = unit_id

        self._worker: Optional[_CommunicatorWorker] = None
        self._connected = False

        # 初始创建 Worker
        self._create_worker()

    def _create_worker(self):
        """
        创建 _CommunicatorWorker 实例并连接信号。
        此方法通常在 __init__ 或 start() 中调用。
        """
        if self._worker is not None:
            self._logger.warning("Worker already exists, skipping creation.")
            return

        self._worker = _CommunicatorWorker(
            self._device_id,
            self._client,
            self._factor_configs,
            self._connection_params,
            self._poll_interval,
            self._cache_db,
            self._unit_id,
            self._logger
        )

        # 转发信号
        self._worker.data_ready.connect(self.data_ready)
        self._worker.comm_error.connect(self.comm_error)
        # 连接 connection_changed 信号：先经过内部槽更新状态，再转发
        self._worker.connection_changed.connect(self._on_worker_connection_changed)

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
        """启动通信线程。若 Worker 已销毁，则重新创建后再启动。"""
        if self._worker is None:
            self._logger.info("Worker is None, recreating worker before start.")
            self._create_worker()
        self._worker.start()

    def stop(self):
        """安全停止通信线程，并清理资源"""
        if self._worker is not None:
            # 请求中断并等待线程完全退出
            self._worker.requestInterruption()
            self._worker.wait()

            # 断开所有信号连接，防止悬空引用
            try:
                self._worker.data_ready.disconnect(self.data_ready)
                self._worker.comm_error.disconnect(self.comm_error)
                self._worker.connection_changed.disconnect(self._on_worker_connection_changed)
            except Exception as e:
                self._logger.debug("Error disconnecting signals: %s", e)

            # 删除底层 C++ 对象，并清除 Python 引用
            self._worker.deleteLater()
            self._worker = None
            self._connected = False

            self._logger.info("Communicator (Device no - %s) stopped and cleaned up.", self._device_id)
        else:
            self._logger.info("Communicator already stopped or not started.")

    def send_command(self, command: Dict[str, Any]):
        """向工作线程提交命令（跨线程安全）"""
        if self._worker is None:
            self._logger.warning("Cannot send command: worker is not running.")
            return
        self._worker.send_command(command)

    def flush_cache(self):
        """请求工作线程补发缓存数据"""
        if self._worker is None:
            self._logger.warning("Cannot flush cache: worker is not running.")
            return
        self._worker.flush_cache()