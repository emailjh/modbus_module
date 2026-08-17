# -*- coding: utf-8 -*-
"""
基于 pymodbus 的 Modbus RTU 客户端实现。
支持自动重连、超时设置，异常统一封装。
"""

from typing import List, Optional, Dict, Any
from pymodbus.client import ModbusSerialClient as _ModbusSerialClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from modbus_module.constants import *
from modbus_module.interfaces import IModbusClient, ModbusException
from modbus_module.utils.logger import get_logger


class PymodbusRtuClient(IModbusClient):
    """Modbus RTU 客户端（串口通信）"""

    def __init__(self, timeout: float = 3.0, logger=None):
        """
        :param timeout: 通信超时（秒）
        """
        self._serial_port: str = ""             # 串口号，如 "COM1"
        self._baudrate: int = 9600
        self._parity: str = 'N'
        self._stopbits: int = 1
        self._bytesize: int = 8
        self._timeout = timeout
        self._client: Optional[_ModbusSerialClient] = None
        self._logger = logger or get_logger(__name__)

    # ---------- 连接管理 ----------
    def connect(self, params: dict) -> bool:                      # 修改签名
        """
        建立 RTU 连接。
        :param params: 连接参数字典，必须包含 'serial_port' 和 'baudrate' 键，
                       可选 'parity', 'stopbits', 'bytesize', 'timeout'。
        :return: 成功返回 True
        """
        port = params.get(COMM_PARAM_SERIAL_PORT, '')      # 使用 'serial_port' 键获取串口号
        baudrate = params.get(COMM_PARAM_BAUDRATE, 9600)
        parity = params.get(COMM_PARAM_PARITY, 'N')
        stopbits = params.get(COMM_PARAM_STOP_BITS, 1)
        bytesize = params.get(COMM_PARAM_BYTESIZE, 8)
        timeout = params.get(COMM_PARAM_TIMEOUT, self._timeout)

        if not port:
            self._logger.error("Modbus RTU connect: missing 'serial_port' in params")
            return False

        self._serial_port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._bytesize = bytesize
        self._timeout = timeout

        try:
            self._client = _ModbusSerialClient(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
                parity=parity,
                stopbits=stopbits,
                bytesize=bytesize
            )
            connected = self._client.connect()
            if connected:
                self._logger.info(f"Modbus RTU connected on {port}, baudrate={baudrate}")
            else:
                self._logger.error(f"Modbus RTU connection failed: {port}")
            return connected
        except Exception as e:
            self._logger.error(f"Exception during Modbus RTU connection: {e}")
            self._client = None
            return False

    def disconnect(self):
        """断开连接"""
        if self._client:
            self._client.close()
            self._client = None
            self._logger.info(f"Modbus RTU disconnected from {self._serial_port}")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.connected

    def _ensure_connected(self):
        """检查连接，若断开则尝试重连一次"""
        if not self.is_connected:
            self._logger.warning("Modbus RTU not connected, attempting reconnect...")
            if self._serial_port and self._baudrate:
                # 构造参数字典调用 connect（使用保存的参数）
                params = {
                    COMM_PARAM_SERIAL_PORT: self._serial_port,
                    COMM_PARAM_BAUDRATE: self._baudrate,
                    COMM_PARAM_PARITY: self._parity,
                    COMM_PARAM_STOP_BITS: self._stopbits,
                    COMM_PARAM_BYTESIZE: self._bytesize,
                    COMM_PARAM_TIMEOUT: self._timeout
                }
                self.connect(params)
            if not self.is_connected:
                raise ModbusException("Modbus RTU client is not connected")

    # ---------- 数据读写（以下方法未改动） ----------
    def read_holding_registers(self, unit: int, address: int, count: int) -> List[int]:
        self._ensure_connected()
        try:
            result = self._client.read_holding_registers(address=address, count=count, device_id=unit)
            if result.isError():
                error_msg = f"Modbus RTU read holding registers error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return result.registers
        except ModbusIOException as e:
            self._logger.error(f"Modbus RTU I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus RTU I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during RTU read holding registers: {e}")
            raise ModbusException(f"Modbus RTU read error: {e}") from e

    def read_input_registers(self, unit: int, address: int, count: int) -> List[int]:
        self._ensure_connected()
        try:
            result = self._client.read_input_registers(address=address, count=count, device_id=unit)
            if result.isError():
                error_msg = f"Modbus RTU read input registers error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return result.registers
        except ModbusIOException as e:
            self._logger.error(f"Modbus RTU I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus RTU I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during RTU read input registers: {e}")
            raise ModbusException(f"Modbus RTU read error: {e}") from e

    def write_register(self, unit: int, address: int, value: int) -> bool:
        self._ensure_connected()
        try:
            result = self._client.write_register(address=address, value=value, device_id=unit)
            if result.isError():
                error_msg = f"Modbus RTU write register error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return True
        except ModbusIOException as e:
            self._logger.error(f"Modbus RTU I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus RTU I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during RTU write register: {e}")
            raise ModbusException(f"Modbus RTU write error: {e}") from e

    def write_registers(self, unit: int, address: int, values: List[int]) -> bool:
        """
        批量写入多个保持寄存器（高效实现，使用 pymodbus 的 write_registers）。

        :param unit: 从站地址
        :param address: 起始寄存器地址
        :param values: 寄存器值列表
        :return: 成功返回 True
        :raises ModbusException: 通信失败或 pymodbus 返回错误时抛出
        """
        self._ensure_connected()
        try:
            result = self._client.write_registers(address=address, values=values, device_id=unit)
            if result.isError():
                error_msg = f"Modbus RTU write registers error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return True
        except ModbusIOException as e:
            self._logger.error(f"Modbus RTU I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus RTU I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during RTU write registers: {e}")
            raise ModbusException(f"Modbus RTU write error: {e}") from e