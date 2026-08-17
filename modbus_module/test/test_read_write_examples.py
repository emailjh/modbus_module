# -*- coding: utf-8 -*-
"""
Modbus 读写测试示例

演示如何使用 DeviceCommunicator 进行：
1. 读取单个地址的多种数据类型（分别建立通信器测试）
2. 读取多个地址的同种数据类型
3. 读取多个地址的不同数据类型
4. 写入单个地址的多种数据类型（分别发送命令）
5. 写入多个地址的同种数据类型（批量命令）
6. 写入多个地址的不同数据类型（批量命令）
7. 连续寄存器的一次性读写（演示单因子多寄存器类型如字符串、float/int32 等）

注意：本示例假设已有一个可用的 Modbus TCP 设备（地址、寄存器根据实际修改）。
"""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QTimer

from modbus_module.clients import PymodbusTcpClient
from modbus_module.device_communicator import DeviceCommunicator

# 字节序常量
BIG_ENDIAN = "big_endian"
LITTLE_ENDIAN = "little_endian"
BIG_ENDIAN_SWAP = "big_endian_swap"
LITTLE_ENDIAN_SWAP = "little_endian_swap"


# =============================================================================
# 辅助函数：构建读取因子配置
# =============================================================================

def build_single_factor_config(factor_name, address, data_type, byte_order=BIG_ENDIAN, register_count=None):
    """
    构建单个因子的配置，用于读取测试。
    :param factor_name: 因子名称（任意字符串）
    :param address: 寄存器起始地址
    :param data_type: 数据类型，如 'int16', 'uint16', 'int32', 'uint32', 'float', 'string'
    :param byte_order: 字节序
    :param register_count: 寄存器数量；若为 None，则自动推断（int16/uint16=1，int32/uint32/float=2）
                           字符串类型必须显式指定
    :return: 因子配置字典（单元素列表）
    """
    cfg = {
        "factor": factor_name,
        "register_address": address,
        "data_type": data_type,
        "byte_order": byte_order,
        "scale": 1.0,
        "offset": 0.0,
        "is_enabled": True
    }
    if register_count is not None:
        cfg["register_count"] = register_count
    return cfg


def build_multiple_same_type_configs(address_list, data_type, byte_order=BIG_ENDIAN, prefix="Factor"):
    """
    构建多个地址同种数据类型的因子配置列表。
    :param address_list: 地址列表
    :param data_type: 数据类型
    :param byte_order: 字节序
    :param prefix: 因子名称前缀，会加上地址
    :return: 因子配置列表
    """
    configs = []
    for addr in address_list:
        cfg = build_single_factor_config(f"{prefix}_{addr}", addr, data_type, byte_order)
        configs.append(cfg)
    return configs


def build_multiple_different_types_configs(items):
    """
    构建多个地址不同数据类型的因子配置列表。
    :param items: 列表，每个元素为字典，包含 factor, address, data_type, byte_order, register_count（可选）
    :return: 因子配置列表
    """
    configs = []
    for item in items:
        cfg = build_single_factor_config(
            item["factor"],
            item["address"],
            item["data_type"],
            item.get("byte_order", BIG_ENDIAN),
            item.get("register_count")
        )
        configs.append(cfg)
    return configs


# =============================================================================
# 辅助函数：构建写入命令
# =============================================================================

def build_write_single_command(address, value, data_type, byte_order=BIG_ENDIAN):
    """
    构建单个地址写入命令（兼容 write_data）。
    :param address: 寄存器起始地址
    :param value: 要写入的值
    :param data_type: 数据类型
    :param byte_order: 字节序
    :return: 命令字典
    """
    return {
        "type": "write_data",
        "address": address,
        "value": value,
        "data_type": data_type,
        "byte_order": byte_order
    }


def build_write_multiple_same_type_command(address_values, data_type, byte_order=BIG_ENDIAN):
    """
    构建多个地址同种数据类型的批量写入命令。
    :param address_values: 字典 {address: value}
    :param data_type: 数据类型
    :param byte_order: 字节序
    :return: write_multiple_data 命令字典
    """
    items = []
    for addr, val in address_values.items():
        items.append({
            "address": addr,
            "value": val,
            "data_type": data_type,
            "byte_order": byte_order
        })
    return {"type": "write_multiple_data", "items": items}


def build_write_multiple_different_types_command(item_list):
    """
    构建多个地址不同数据类型的批量写入命令。
    :param item_list: 列表，每个元素为字典，包含 address, value, data_type, byte_order
    :return: write_multiple_data 命令字典
    """
    return {"type": "write_multiple_data", "items": item_list}


# =============================================================================
# 测试类：演示各场景
# =============================================================================

class ReadWriteTestApp(QObject):
    def __init__(self):
        super().__init__()
        # 连接参数（根据实际设备修改）
        self.conn_params = {'host': '192.168.100.247', 'port': 502, 'timeout': 3.0}
        self.client = None
        self.comm = None          # 主通信器，用于周期写入测试
        self.pi = 3.1415926
        self.temp_communicators = []   # 保存临时通信器，便于管理

    def create_communicator(self, factor_configs, device_id="test_device"):
        """创建并返回一个新的 DeviceCommunicator"""
        client = PymodbusTcpClient()
        comm = DeviceCommunicator(
            device_id=device_id,
            client=client,
            factor_configs=factor_configs,
            connection_params=self.conn_params,
            poll_interval=5
        )
        # 连接信号
        comm.data_ready.connect(self.on_data_ready)
        comm.comm_error.connect(self.on_error)
        return comm

    def on_data_ready(self, device_id, data):
        print(f"[数据] 设备 {device_id}: {data}")

    def on_error(self, device_id, msg):
        print(f"[错误] 设备 {device_id}: {msg}")

    # -------------------------------------------------------------------------
    # 读取测试场景
    # -------------------------------------------------------------------------

    def test_read_single_address_various_types(self):
        """测试读取单个地址的多种数据类型（分别创建通信器）"""
        print("\n=== 测试：读取单个地址的多种数据类型 ===")
        test_cases = [
            ("int16", 100, BIG_ENDIAN),
            ("uint16", 100, BIG_ENDIAN),
            ("int32", 100, BIG_ENDIAN),
            ("uint32", 100, BIG_ENDIAN),
            ("float", 100, BIG_ENDIAN),
            ("float", 100, LITTLE_ENDIAN),  # 不同字节序
        ]
        for data_type, addr, byte_order in test_cases:
            print(f"--- 数据类型: {data_type}, 字节序: {byte_order} ---")
            cfg = build_single_factor_config(f"Test_{data_type}", addr, data_type, byte_order)
            comm = self.create_communicator([cfg], device_id=f"temp_{data_type}")
            self.temp_communicators.append(comm)
            comm.start()
            # 运行 2 秒后自动停止
            QTimer.singleShot(2000, comm.stop)

    def test_read_multiple_same_type(self):
        """测试读取多个地址的同种数据类型"""
        print("\n=== 测试：读取多个地址的同种数据类型 ===")
        addresses = [200, 202, 204]
        configs = build_multiple_same_type_configs(
            addresses, data_type="float", byte_order=BIG_ENDIAN, prefix="Float"
        )
        comm = self.create_communicator(configs, device_id="temp_same_type")
        self.temp_communicators.append(comm)
        comm.start()
        QTimer.singleShot(3000, comm.stop)

    def test_read_multiple_different_types(self):
        """测试读取多个地址的不同数据类型"""
        print("\n=== 测试：读取多个地址的不同数据类型 ===")
        items = [
            {"factor": "湿度", "address": 0, "data_type": "int16", "byte_order": LITTLE_ENDIAN_SWAP},
            {"factor": "温度", "address": 2, "data_type": "int32", "byte_order": LITTLE_ENDIAN_SWAP},
            {"factor": "压力", "address": 4, "data_type": "float", "byte_order": LITTLE_ENDIAN_SWAP},
            {"factor": "风速", "address": 6, "data_type": "uint32", "byte_order": LITTLE_ENDIAN_SWAP},
            {"factor": "设备型号", "address": 8, "data_type": "string", "byte_order": LITTLE_ENDIAN_SWAP, "register_count": 3}
        ]
        configs = build_multiple_different_types_configs(items)
        comm = self.create_communicator(configs, device_id="temp_multi_type")
        self.temp_communicators.append(comm)
        comm.start()
        QTimer.singleShot(5000, comm.stop)

    def test_read_continuous_registers(self):
        """
        测试连续寄存器的一次性读取。
        本例读取一个字符串因子，指定读取 3 个寄存器（6 个字符），
        避免读取到设备填充字符。
        """
        print("\n=== 测试：连续寄存器一次性读取（字符串，3 个寄存器） ===")
        cfg = build_single_factor_config(
            "设备型号_连续", 8, "string", byte_order=LITTLE_ENDIAN, register_count=3
        )
        comm = self.create_communicator([cfg], device_id="temp_cont_read")
        self.temp_communicators.append(comm)
        comm.start()
        QTimer.singleShot(5000, comm.stop)

    # -------------------------------------------------------------------------
    # 写入测试场景
    # -------------------------------------------------------------------------
    def test_write_multiple_same_type(self):
        """测试写入多个地址的同种数据类型（批量）"""
        print("\n=== 测试：写入多个地址的同种数据类型 ===")
        if not self.comm or not self.comm.is_connected:
            print("主通信器未连接，稍后重试")
            return
        address_values = {
            12: 1.23,
            14: 45.67,
            16: 89.10,
        }
        cmd = build_write_multiple_same_type_command(address_values, "float", BIG_ENDIAN)
        self.comm.send_command(cmd)
        print(f"发送命令: {cmd}")

    def test_write_multiple_different_types(self):
        """测试写入多个地址的不同数据类型（批量）"""
        print("\n=== 测试：写入多个地址的不同数据类型 ===")
        if not self.comm or not self.comm.is_connected:
            print("主通信器未连接，稍后重试")
            return
        self.pi = self.pi + 1 if self.pi else 3.14
        items = [
            {"address": 0, "value": self.pi, "data_type": "float", "byte_order": LITTLE_ENDIAN_SWAP},
            {"address": 2, "value": 250, "data_type": "uint16", "byte_order": LITTLE_ENDIAN_SWAP},
            {"address": 4, "value": 2501, "data_type": "int16", "byte_order": LITTLE_ENDIAN_SWAP},
            {"address": 6, "value": -123456, "data_type": "int32", "byte_order": LITTLE_ENDIAN_SWAP},
            {"address": 8, "value": "A1B2C3", "data_type": "string", "byte_order": LITTLE_ENDIAN_SWAP},
        ]
        cmd = build_write_multiple_different_types_command(items)
        self.comm.send_command(cmd)
        print(f"发送命令: {cmd}")

    def test_write_continuous_registers(self):
        """
        测试连续寄存器的一次性写入。
        本例写入一个字符串（长度 6 个字符，使用 3 个寄存器）。
        """
        print("\n=== 测试：连续寄存器一次性写入（字符串，6 个字符） ===")
        if not self.comm or not self.comm.is_connected:
            print("主通信器未连接，稍后重试")
            return
        cmd = build_write_single_command(8, "ABCD12", "string", LITTLE_ENDIAN_SWAP)
        self.comm.send_command(cmd)
        print(f"发送命令: {cmd}")


# =============================================================================
# 主程序：演示完整流程
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    tester = ReadWriteTestApp()

    # 创建主通信器（用于周期写入测试），配置为读取一些基础因子
    read_items = [
        {"factor": "PM2.5", "address": 0, "data_type": "float", "byte_order": LITTLE_ENDIAN_SWAP},
        {"factor": "温度", "address": 2, "data_type": "uint32", "byte_order": LITTLE_ENDIAN_SWAP},
        {"factor": "终值", "address": 6, "data_type": "int32", "byte_order": LITTLE_ENDIAN_SWAP},
    ]
    read_configs = build_multiple_different_types_configs(read_items)
    tester.comm = tester.create_communicator(read_configs, device_id="main_device")
    tester.comm.start()

    # 定时执行读取测试（独立通信器）
    QTimer.singleShot(5000, tester.test_read_multiple_different_types)
    QTimer.singleShot(10000, tester.test_read_continuous_registers)

    # 周期定时器：每 7 秒尝试执行一次写入测试（自动检查连接）
    timer = QTimer()
    timer.setInterval(7000)
    timer.timeout.connect(tester.test_write_multiple_different_types)
    timer.start()

    # 延迟执行单次写入测试
    QTimer.singleShot(14000, tester.test_write_multiple_same_type)
    QTimer.singleShot(16000, tester.test_write_continuous_registers)

    # 30 秒后停止主通信器并退出
    def cleanup():
        tester.comm.stop()
        app.quit()
    QTimer.singleShot(50000, cleanup)

    sys.exit(app.exec())