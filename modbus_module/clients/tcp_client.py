# -*- coding: utf-8 -*-
"""
基于 pymodbus 的 Modbus TCP 客户端实现。
支持自动重连、超时设置，异常统一封装。
"""

from typing import List, Optional, Dict, Any
from pymodbus.client import ModbusTcpClient as _ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

from modbus_module.interfaces import IModbusClient, ModbusException
from modbus_module.constants import *
from modbus_module.utils.logger import get_logger


class PymodbusTcpClient(IModbusClient):
    """Modbus TCP 客户端"""

    def __init__(self, timeout: float = 3.0, logger=None):
        """
        :param timeout: 通信超时时间（秒）
        """
        self._host: str = ""
        self._port: int = 502
        self._timeout = timeout
        self._client: Optional[_ModbusTcpClient] = None
        self._logger = logger or get_logger(__name__)

    # ---------- 连接管理 ----------
    def connect(self, params: dict) -> bool:                      # 修改签名
        """
        建立 TCP 连接。
        :param params: 连接参数字典，必须包含 'host' 和 'port' 键，
                       可选 'timeout' 覆盖默认超时。
        :return: 成功返回 True
        """
        host = params.get(COMM_PARAM_HOST, '')
        port = params.get(COMM_PARAM_PORT, 502)
        timeout = params.get(COMM_PARAM_TIMEOUT, self._timeout)

        if not host:
            self._logger.error("Modbus TCP connect: missing 'host' in params")
            return False

        self._host = host
        self._port = port
        self._timeout = timeout

        try:
            self._client = _ModbusTcpClient(
                host=host,
                port=port,
                timeout=timeout
            )
            connected = self._client.connect()
            if connected:
                self._logger.info(f"Modbus TCP connected to {host}:{port}")
            else:
                self._logger.error(f"Modbus TCP connection failed: {host}:{port}")
            return connected
        except Exception as e:
            self._logger.error(f"Exception during Modbus TCP connection: {e}")
            self._client = None
            return False

    def disconnect(self):
        """断开连接"""
        if self._client:
            self._client.close()
            self._client = None
            self._logger.info(f"Modbus TCP disconnected from {self._host}:{self._port}")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.connected

    def _ensure_connected(self):
        """检查连接状态，若断开则尝试重连一次"""
        if not self.is_connected:
            self._logger.warning("Modbus TCP not connected, attempting reconnect...")
            if self._host and self._port:
                # 构造参数字典调用 connect（使用保存的参数）
                self.connect({COMM_PARAM_HOST: self._host, COMM_PARAM_PORT: self._port, COMM_PARAM_TIMEOUT: self._timeout})
            if not self.is_connected:
                raise ModbusException("Modbus TCP client is not connected")

    # ---------- 数据读写（以下方法未改动） ----------
    def read_holding_registers(self, unit: int, address: int, count: int) -> List[int]:
        self._ensure_connected()
        try:
            result = self._client.read_holding_registers(address=address, count=count, device_id=unit)
            if result.isError():
                error_msg = f"Modbus read holding registers error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return result.registers
        except ModbusIOException as e:
            self._logger.error(f"Modbus I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during read holding registers: {e}")
            raise ModbusException(f"Modbus read error: {e}") from e

    def read_input_registers(self, unit: int, address: int, count: int) -> List[int]:
        self._ensure_connected()
        try:
            result = self._client.read_input_registers(address=address, count=count, device_id=unit)
            if result.isError():
                error_msg = f"Modbus read input registers error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return result.registers
        except ModbusIOException as e:
            self._logger.error(f"Modbus I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during read input registers: {e}")
            raise ModbusException(f"Modbus read error: {e}") from e

    def write_register(self, unit: int, address: int, value: int) -> bool:
        self._ensure_connected()
        try:
            result = self._client.write_register(address=address, value=value, device_id=unit)
            if result.isError():
                error_msg = f"Modbus write register error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return True
        except ModbusIOException as e:
            self._logger.error(f"Modbus I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during write register: {e}")
            raise ModbusException(f"Modbus write error: {e}") from e

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
                error_msg = f"Modbus write registers error: {result}"
                self._logger.error(error_msg)
                raise ModbusException(error_msg)
            return True
        except ModbusIOException as e:
            self._logger.error(f"Modbus I/O exception: {e}")
            self._client.close()
            raise ModbusException(f"Modbus I/O error: {e}") from e
        except Exception as e:
            self._logger.error(f"Unexpected error during write registers: {e}")
            raise ModbusException(f"Modbus write error: {e}") from e