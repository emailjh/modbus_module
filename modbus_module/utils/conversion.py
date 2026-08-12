from modbus_module.constants import *

# -*- coding: utf-8 -*-
"""
AQMS 2.0 - 浓度与数据转换服务 (ConversionService)
==================================================
提供 Modbus 寄存器解析、浓度单位换算（不考虑温压补偿）、
数据修约、状态码国际化等功能。

浓度换算基于 25℃、101.325 kPa 标准状态，
摩尔体积固定为 24.5 L/mol，无需温度、气压参数。

"""

import struct
from typing import List
from PySide6.QtCore import QCoreApplication

# 翻译上下文，对应 .ts 文件中的 <context>ConversionService</context>
_TR = QCoreApplication.translate
_CONTEXT = "ConversionService"


class ConversionService:
    """
    通用数据转换服务（无状态，所有方法为静态）。
    """

    # ===================== Modbus 寄存器解析 =====================

    @staticmethod
    def registers_to_float(
            registers: List[int],
            byte_order: str = "big_endian"
    ) -> float:
        """
        将两个 16 位 Modbus 寄存器转换为 32 位 IEEE 754 浮点数。

        :param registers: 长度为 2 的整数列表，每个元素范围 0~65535。
        :param byte_order: 字节序，可选值：
            - 'big_endian'         : ABCD (大端字序 + 大端字节序)
            - 'little_endian'      : DCBA (小端字序 + 小端字节序)
            - 'big_endian_swap'    : BADC (大端字序 + 小端字节序)
            - 'little_endian_swap' : CDAB (小端字序 + 大端字节序)
        :return: 解析出的浮点数。
        :raises ValueError: 寄存器数量不为 2 或字节序非法。
        """
        if len(registers) != 2:
            raise ValueError(_TR(_CONTEXT, "registers must contain exactly 2 elements"))
        if not (0 <= registers[0] <= 0xFFFF and 0 <= registers[1] <= 0xFFFF):
            raise ValueError(_TR(_CONTEXT, "Register values must be between 0 and 65535"))

        if byte_order == "big_endian":
            # 大端字序 + 大端字节序：reg[0]高字，reg[1]低字，字内大端
            raw = struct.pack('>HH', registers[0], registers[1])
            return struct.unpack('>f', raw)[0]

        elif byte_order == "little_endian":
            # 小端字序 + 小端字节序：reg[0]低字，reg[1]高字，字内小端
            raw = struct.pack('<HH', registers[1], registers[0])
            return struct.unpack('>f', raw)[0]

        elif byte_order == "big_endian_swap":
            # 大端字序 + 小端字节序：reg[0]高字，reg[1]低字，但字内小端
            # 交换寄存器后按小端处理
            raw = struct.pack('>HH', registers[1], registers[0])
            return struct.unpack('<f', raw)[0]

        elif byte_order == "little_endian_swap":
            # 小端字序 + 大端字节序：reg[0]低字，reg[1]高字，字内大端
            # 交换寄存器后按大端处理
            raw = struct.pack('>HH', registers[1], registers[0])
            return struct.unpack('>f', raw)[0]

        else:
            err_msg = _TR(_CONTEXT, "Unknown byte_order: %1").arg(byte_order)
            raise ValueError(err_msg)

    @staticmethod
    def registers_to_int32(
            registers: List[int],
            signed: bool = True,
            byte_order: str = "big_endian"
    ) -> int:
        """
        将两个 16 位寄存器解析为 32 位整数。

        :param registers: 长度为 2 的寄存器列表。
        :param signed: 是否为有符号整数。
        :param byte_order: 字节序，同 registers_to_float。
        :return: 解析出的整数。
        :raises ValueError: 寄存器数量不为 2 或字节序非法。
        """
        if len(registers) != 2:
            raise ValueError(_TR(_CONTEXT, "registers must contain exactly 2 elements"))
        if not (0 <= registers[0] <= 0xFFFF and 0 <= registers[1] <= 0xFFFF):
            raise ValueError(_TR(_CONTEXT, "Register values must be between 0 and 65535"))

        # 根据字节序选择寄存器顺序和打包格式
        if byte_order == "big_endian":
            raw = struct.pack('>HH', registers[0], registers[1])
            fmt = '>i' if signed else '>I'
        elif byte_order == "little_endian":
            raw = struct.pack('>HH', registers[0], registers[1])
            fmt = '<i' if signed else '<I'
        elif byte_order == "big_endian_swap":
            # 交换寄存器，然后小端打包、小端解包
            raw = struct.pack('>HH', registers[1], registers[0])
            fmt = '<i' if signed else '<I'
        elif byte_order == "little_endian_swap":
            # 交换寄存器，然后大端打包、大端解包
            raw = struct.pack('>HH', registers[1], registers[0])
            fmt = '>i' if signed else '>I'
        else:
            err_msg = _TR(_CONTEXT, "Unknown byte_order: %1").arg(byte_order)
            raise ValueError(err_msg)

        return struct.unpack(fmt, raw)[0]

    @staticmethod
    def register_to_int16(register: int, signed: bool = True) -> int:
        """单个 16 位寄存器转整数（保留原有实现）"""
        if not (0 <= register <= 0xFFFF):
            raise ValueError(_TR(_CONTEXT, "Register value must be between 0 and 65535"))

        if signed and register >= 0x8000:
            return register - 0x10000
        return register

    # ===================== 浓度单位换算（无温压补偿） =====================

    # 标准摩尔体积 (25°C, 101.325 kPa)，单位 L/mol
    STD_MOLAR_VOLUME = 24.5

    @staticmethod
    def ppm_to_mgm3(ppm_value: float, molecular_weight: float) -> float:
        """
        体积浓度 ppm → 质量浓度 mg/m³（不考虑温压补偿）。

        公式: mg/m³ = ppm × (M / 24.5)

        :param ppm_value: ppm 浓度值
        :param molecular_weight: 分子量 g/mol (如 SO₂ = 64.07，NO	= 30.01，O₃	= 48，NO₂ = 46.01，NO = 30.01)
        :return: 质量浓度 mg/m³
        """
        return ppm_value * (molecular_weight / ConversionService.STD_MOLAR_VOLUME)

    @staticmethod
    def ppb_to_mgm3(ppb_value: float, molecular_weight: float) -> float:
        """
        ppb → mg/m³ (1 ppb = 0.001 ppm)
        """
        return ConversionService.ppm_to_mgm3(ppb_value * 0.001, molecular_weight)

    @staticmethod
    def mgm3_to_ppm(mgm3_value: float, molecular_weight: float) -> float:
        """
        质量浓度 mg/m³ → 体积浓度 ppm（不考虑温压补偿）。
        """
        factor = molecular_weight / ConversionService.STD_MOLAR_VOLUME
        if factor == 0:
            return 0.0
        return mgm3_value / factor

    # ---------- 辅助单位（μg/m³ 兼容） ----------

    @staticmethod
    def ppm_to_ugm3(ppm_value: float, molecular_weight: float) -> float:
        """
        ppm → μg/m³ (1 mg/m³ = 1000 μg/m³)
        """
        return ConversionService.ppm_to_mgm3(ppm_value, molecular_weight) * 1000.0

    @staticmethod
    def ppb_to_ugm3(ppb_value: float, molecular_weight: float) -> float:
        """
        ppb → μg/m³
        """
        return ConversionService.ppb_to_mgm3(ppb_value, molecular_weight) * 1000.0

    @staticmethod
    def ugm3_to_ppm(ugm3_value: float, molecular_weight: float) -> float:
        """
        μg/m³ → ppm
        """
        return ConversionService.mgm3_to_ppm(ugm3_value / 1000.0, molecular_weight)

    # ===================== 统一转换入口 =====================

    @staticmethod
    def convert(value: float, convert_type: str, molecular_weight: float) -> float:
        """
        根据转换类型自动选择转换函数（无温压参数）。

        :param value: 原始值
        :param convert_type: 转换类型，可选:
            'ppm_to_mgm3', 'ppb_to_mgm3', 'mgm3_to_ppm',
            'ppm_to_ugm3', 'ppb_to_ugm3', 'ugm3_to_ppm',
            'none' (不转换)
        :param molecular_weight: 分子量 g/mol
        :return: 转换后的浓度值
        """
        if convert_type == 'ppm_to_mgm3':
            return ConversionService.ppm_to_mgm3(value, molecular_weight)
        elif convert_type == 'ppb_to_mgm3':
            return ConversionService.ppb_to_mgm3(value, molecular_weight)
        elif convert_type == 'mgm3_to_ppm':
            return ConversionService.mgm3_to_ppm(value, molecular_weight)
        elif convert_type == 'ppm_to_ugm3':
            return ConversionService.ppm_to_ugm3(value, molecular_weight)
        elif convert_type == 'ppb_to_ugm3':
            return ConversionService.ppb_to_ugm3(value, molecular_weight)
        elif convert_type == 'ugm3_to_ppm':
            return ConversionService.ugm3_to_ppm(value, molecular_weight)
        else:
            return value

    # ===================== 数据修约 =====================

    @staticmethod
    def round_half_even(value: float, decimals: int = 1) -> float:
        """
        四舍六入五成双（银行家舍入）修约规则。
        用于 HJ212-2025 要求的统计值修约。

        :param value: 待修约数值
        :param decimals: 保留小数位数
        :return: 修约后的浮点数
        """
        multiplier = 10 ** decimals
        scaled = value * multiplier
        int_part = int(scaled)
        frac = scaled - int_part

        if abs(frac) < 0.5 - 1e-12:
            return int_part / multiplier
        if abs(frac) > 0.5 + 1e-12:
            return (int_part + (1 if value >= 0 else -1)) / multiplier

        # 恰好 .5
        if int_part % 2 == 0:
            return int_part / multiplier
        else:
            return (int_part + (1 if value >= 0 else -1)) / multiplier

    # ===================== 国际化工具 =====================

    @staticmethod
    def translate_unit(unit_key: str) -> str:
        """
        返回单位的国际化名称。
        :param unit_key: 单位标识符，如 'mg/m³', 'μg/m³', 'ppm', 'ppb'
        :return: 当前语言的单位字符串
        """
        return _TR(_CONTEXT, unit_key)

    @staticmethod
    def translate_status(status_code: str) -> str:
        """
        将 HJ212 数据状态码翻译为当前语言的描述。
        :param status_code: 'N','C','F','D','I','O'
        """
        status_map = DATA_STATUS_MAP
        key = status_map.get(status_code, 'Unknown')
        return _TR(_CONTEXT, key)

    @staticmethod
    def translate_alarm_level(level: int) -> str:
        """
        报警级别国际化。
        :param level: 1-信息，2-警告，3-严重
        """
        level_map = ALAEM_LEVEL_MAP
        key = level_map.get(level, 'Unknown')
        return _TR(_CONTEXT, key)


# 向后兼容的函数别名（便于直接调用，无需加类前缀）
modbus_to_float = ConversionService.registers_to_float
modbus_to_int32 = ConversionService.registers_to_int32
modbus_to_int16 = ConversionService.register_to_int16
ppm_to_mgm3 = ConversionService.ppm_to_mgm3
ppb_to_mgm3 = ConversionService.ppb_to_mgm3
mgm3_to_ppm = ConversionService.mgm3_to_ppm
ppm_to_ugm3 = ConversionService.ppm_to_ugm3
ppb_to_ugm3 = ConversionService.ppb_to_ugm3
ugm3_to_ppm = ConversionService.ugm3_to_ppm