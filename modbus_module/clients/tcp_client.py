# -*- coding: utf-8 -*-
"""
基于 pymodbus 的 Modbus TCP 客户端实现。
支持自动重连、超时设置，异常统一封装。
"""
import struct
import time
from typing import List, Optional, Dict, Any
from pymodbus.client import ModbusTcpClient as _ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu.register_message import ReadInputRegistersRequest, ReadHoldingRegistersRequest

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

    # ---------- 数据读写 ----------
    def _read_broadcast(self, request_class, address: int, count: int, unit: int):
        """
        处理广播读（unit=0）：手动构造 Modbus‑TCP 请求帧，发送并解析响应，
        绕过 pymodbus 的设备 ID 匹配检查。

        加固点：
        1. 循环 recv，依据 MBAP 的 Length 字段接收完整报文，解决 TCP 半包/粘包；
        2. 校验 MBAP 头：ProtocolID、TransactionID、Length 合法性；
        3. 解析 Modbus 异常应答，获取真实异常码；
        4. 读取应答 UnitID，输出日志告警；
        5. 报文长度边界校验，防止切片越界。

        注意：此为非标 Modbus‑TCP 行为，标准协议 unit=0 广播不应返回应答；
        仅适配厂商扩展支持广播读回复的设备。
        """
        self._ensure_connected()
        try:
            # 确定功能码
            if request_class == ReadHoldingRegistersRequest:
                func_code = 0x03
            elif request_class == ReadInputRegistersRequest:
                func_code = 0x04
            else:
                raise ModbusException("Unsupported request class for broadcast read")

            # 手动构造 PDU：功能码 + 起始地址(2字节) + 数量(2字节)
            pdu = bytes([func_code]) + struct.pack('>HH', address, count)

            # 构造 MBAP 头：事务标识=0，协议标识=0，长度=单元标识(1)+PDU长度，单元标识=0
            trans_id = 0
            mbap = struct.pack('>HHHB', trans_id, 0, len(pdu) + 1, 0)
            packet = mbap + pdu

            # 发送请求帧
            self._client.transport.send(packet)

            # ========== 循环接收完整应答报文，处理TCP流半包粘包 ==========
            buffer = bytearray()
            mbap_header_len = 7
            total_expected = None
            start_time = time.time()

            while True:
                if (time.time() - start_time) > self._timeout:
                    raise ModbusException("Timeout waiting for full broadcast response")

                chunk = self._client.transport.recv(self._timeout)
                if not chunk:
                    raise ModbusException("Socket closed while receiving broadcast response")
                buffer.extend(chunk)

                # 先收到MBAP头之后解析总长度
                if total_expected is None and len(buffer) >= mbap_header_len:
                    # MBAP: >HHHB TransID(2),ProtoID(2),Length(2),UnitId(1)
                    resp_trans_id = int.from_bytes(buffer[0:2], "big")
                    resp_proto_id = int.from_bytes(buffer[2:4], "big")
                    resp_mbap_length = int.from_bytes(buffer[4:6], "big")
                    # MBAP头7字节 + mbap_length 为后续全部字节(UnitId+PDU)
                    total_expected = mbap_header_len + resp_mbap_length

                    # MBAP合法性校验
                    if resp_proto_id != 0:
                        raise ModbusException(f"Invalid Modbus‑TCP protocol id: {resp_proto_id}")
                    if resp_trans_id != trans_id:
                        self._logger.warning(
                            f"Broadcast response transaction id mismatch, req:{trans_id}, resp:{resp_trans_id}")

                # 已经解析出总长度，并且缓冲区已经收齐全部数据
                if total_expected is not None and len(buffer) >= total_expected:
                    break

            response_packet = bytes(buffer[:total_expected])
            # ==============================================================

            # 取出应答MBAP字段
            resp_mbap_length = int.from_bytes(response_packet[4:6], "big")
            resp_unit_id = response_packet[6]
            self._logger.debug(f"Broadcast response unit id = {resp_unit_id}")

            # 最小报文校验：MBAP7 + Func(1) + ByteCount(1) =9
            if len(response_packet) < 9:
                raise ModbusException(f"Response packet too short, len={len(response_packet)}")

            resp_func = response_packet[7]
            # PDU部分：response_packet[7:]
            pdu_resp = response_packet[7:]

            if resp_func >= 0x80:
                # Modbus异常应答：PDU[0]=异常功能码，PDU[1]=异常码
                if len(pdu_resp) < 2:
                    raise ModbusException("Malformed modbus exception response")
                exception_code = pdu_resp[1]
                raise ModbusException(
                    f"Broadcast read exception, func={resp_func:#04x}, exception_code={exception_code:#02x}")

            # 正常响应：03/04功能码，PDU[0]=func，PDU[1]=byte_count，后续为数据
            if len(pdu_resp) < 2:
                raise ModbusException("Malformed normal response PDU")
            byte_count = pdu_resp[1]
            if len(pdu_resp) < (2 + byte_count):
                raise ModbusException(
                    f"Response PDU length mismatch, expect at least {2 + byte_count}, got {len(pdu_resp)}")

            data_bytes = pdu_resp[2: 2 + byte_count]

            # 转换为寄存器列表（大端）
            registers = [int.from_bytes(data_bytes[i:i + 2], 'big') for i in range(0, len(data_bytes), 2)]
            return registers

        except ModbusException:
            raise
        except Exception as e:
            self._logger.error(f"Broadcast read exception: {e}")
            raise ModbusException(f"Broadcast read failed: {e}") from e

    def read_holding_registers(self, unit: int, address: int, count: int) -> List[int]:
        # 广播读特殊处理
        if unit == 0:
            return self._read_broadcast(ReadHoldingRegistersRequest, address, count, unit)
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
        # 广播读特殊处理
        if unit == 0:
            return self._read_broadcast(ReadInputRegistersRequest, address, count, unit)
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