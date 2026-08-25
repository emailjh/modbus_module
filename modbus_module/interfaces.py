# -*- coding: utf-8 -*-
"""
通信层抽象接口 (interfaces.py)

定义设备通信的抽象基类，确保上层业务逻辑与具体通信协议解耦。
所有设备驱动必须实现这些接口，以支持统一调用。
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any


class IModbusClient(ABC):
    """Modbus 客户端抽象接口，支持 TCP 和 RTU"""

    @abstractmethod
    # def connect(self, host: str, port: int) -> bool:
    #     """
    #     建立与设备的连接。
    #     :param host: 主机 IP 或串口名（RTU 时可能为 "COM1" 等）
    #     :param port: TCP 端口或串口波特率（根据实现）
    #     :return: 连接成功返回 True，否则 False
    #     """
    #     ...
    def connect(self, params: dict) -> bool:
        """
        params 自动适配两种模式
        TCP: {"host":"192.168.1.10","port":502,"timeout":1.0}
        RTU: {"serial_name":"COM3","baudrate":9600,"parity":"N","stopbits":1,"timeout":0.8}
        """

    @abstractmethod
    def disconnect(self):
        """断开连接，释放资源"""
        ...

    @abstractmethod
    def read_holding_registers(self, device_id: int, address: int, count: int) -> List[int]:
        """
        读取保持寄存器。
        :param device_id: Modbus 从站地址
        :param address: 起始寄存器地址（0-based 或 1-based 由实现决定）
        :param count: 读取的寄存器数量
        :return: 寄存器值列表（每个元素为 0~65535 的整数）
        :raises ModbusException: 通信失败时抛出
        """
        ...

    @abstractmethod
    def read_input_registers(self, device_id: int, address: int, count: int) -> List[int]:
        """
        读取输入寄存器。
        :param device_id: 从站地址
        :param address: 起始地址
        :param count: 数量
        :return: 寄存器值列表
        :raises ModbusException:
        """
        ...

    @abstractmethod
    def write_register(self, device_id: int, address: int, value: int) -> bool:
        """
        写单个保持寄存器。
        :param device_id: 从站地址
        :param address: 寄存器地址
        :param value: 写入值
        :return: 成功返回 True
        :raises ModbusException:
        """
        ...

    def write_registers(self, unit: int, address: int, values: List[int]) -> bool:
        """
        批量写入多个保持寄存器（默认实现：循环调用 write_register）。
        子类可根据底层客户端特性重写此方法以提升效率。

        :param unit: Modbus 从站地址
        :param address: 起始寄存器地址
        :param values: 要写入的寄存器值列表（每个元素 0~65535）
        :return: 全部写入成功返回 True，否则返回 False
        :raises ModbusException: 通信失败时抛出
        """
        for i, val in enumerate(values):
            if not self.write_register(unit, address + i, val):
                return False
        return True

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """返回当前连接状态"""
        ...

    @staticmethod
    def _compute_crc(data: bytes) -> bytes:
        """计算 Modbus RTU CRC16，返回小端序两字节"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc.to_bytes(2, 'little')

class IReportProtocol(ABC):
    """数据上报协议抽象接口"""
    @abstractmethod
    def build_message(self, mn: str, pw: str, st: int, cn: int,
                      data: Dict[str, Any], **kwargs) -> str:
        """构建完整报文字符串"""
        ...

    @abstractmethod
    def parse_response(self, message: str) -> Dict[str, Any]:
        """解析平台返回的应答报文"""
        ...


class ModbusException(Exception):
    """Modbus 通信异常，用于统一包装底层错误"""
    pass


# 可选：定义通用的通信结果封装，便于上层处理
class CommunicationResult:
    """通信操作结果"""
    def __init__(self, success: bool, data: Optional[object] = None, error: str = ""):
        self.success = success
        self.data = data
        self.error = error