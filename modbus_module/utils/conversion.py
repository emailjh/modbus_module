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

    @staticmethod
    def float_to_registers(value: float, byte_order: str = "big_endian") -> List[int]:
        """
        将 32 位 IEEE 754 浮点数转换为两个 16 位 Modbus 寄存器值。

        该函数是 registers_to_float 的逆操作，确保各种字节序下转换可逆。

        :param value: 待转换的浮点数
        :param byte_order: 字节序，与 registers_to_float 对应。
        :return: 长度为 2 的寄存器值列表
        :raises ValueError: 字节序非法
        """
        if byte_order == "big_endian":
            # 大端字序 + 大端字节序：直接使用大端打包，拆分为两个大端寄存器
            raw = struct.pack('>f', value)
            return [int.from_bytes(raw[0:2], 'big'), int.from_bytes(raw[2:4], 'big')]

        elif byte_order == "little_endian":
            # 小端字序 + 小端字节序：使用小端打包，拆分为两个小端寄存器
            raw = struct.pack('<f', value)
            # 小端打包后的字节顺序为 [低字节, 高字节]，寄存器应按顺序取，但寄存器内字节也是小端
            # 实际寄存器值 = 两个字节按小端解释
            reg0 = raw[0] | (raw[1] << 8)
            reg1 = raw[2] | (raw[3] << 8)
            return [reg0, reg1]

        elif byte_order == "big_endian_swap":
            # 大端字序 + 小端字节序：先按小端打包浮点数，然后交换寄存器顺序
            # 解码时使用 raw = pack('>HH', reg[1], reg[0]) -> unpack('<f')
            # 因此编码时应：将浮点数按小端打包，拆分为两个寄存器，然后将它们交换顺序
            raw_le = struct.pack('<f', value)
            reg0 = raw_le[0] | (raw_le[1] << 8)  # 低字（小端）
            reg1 = raw_le[2] | (raw_le[3] << 8)  # 高字（小端）
            return [reg1, reg0]

        elif byte_order == "little_endian_swap":
            # 小端字序 + 大端字节序：先按大端打包浮点数，然后交换寄存器顺序
            # 解码时使用 raw = pack('>HH', reg[1], reg[0]) -> unpack('>f')
            raw_be = struct.pack('>f', value)
            reg0 = raw_be[0] << 8 | raw_be[1]  # 高字（大端）
            reg1 = raw_be[2] << 8 | raw_be[3]  # 低字（大端）
            return [reg1, reg0]

        else:
            raise ValueError(_TR(_CONTEXT, "Unknown byte_order: %1").arg(byte_order))

    @staticmethod
    def int32_to_registers(value: int, signed: bool = True, byte_order: str = "big_endian") -> List[int]:
        """
        将 32 位整数转换为两个 16 位 Modbus 寄存器值。

        :param value: 待转换的整数（若 signed=True，范围为 -2^31 ~ 2^31-1；否则 0 ~ 2^32-1）
        :param signed: 是否为有符号整数
        :param byte_order: 字节序，与 registers_to_int32 对应。
        :return: 长度为 2 的寄存器值列表
        :raises ValueError: 数值超出范围或字节序非法
        """
        # 检查范围
        if signed and not (-2**31 <= value <= 2**31 - 1):
            raise ValueError(_TR(_CONTEXT, "Signed int32 value out of range"))
        if not signed and not (0 <= value <= 2**32 - 1):
            raise ValueError(_TR(_CONTEXT, "Unsigned int32 value out of range"))

        if byte_order == "big_endian":
            raw = struct.pack('>i', value) if signed else struct.pack('>I', value)
            return [int.from_bytes(raw[0:2], 'big'), int.from_bytes(raw[2:4], 'big')]

        elif byte_order == "little_endian":
            raw = struct.pack('<i', value) if signed else struct.pack('<I', value)
            reg0 = raw[0] | (raw[1] << 8)
            reg1 = raw[2] | (raw[3] << 8)
            return [reg0, reg1]

        elif byte_order == "big_endian_swap":
            # 对应解码: pack('>HH', reg[1], reg[0]) -> unpack('<i')
            raw_le = struct.pack('<i', value) if signed else struct.pack('<I', value)
            reg0 = raw_le[0] | (raw_le[1] << 8)
            reg1 = raw_le[2] | (raw_le[3] << 8)
            return [reg1, reg0]

        elif byte_order == "little_endian_swap":
            # 对应解码: pack('>HH', reg[1], reg[0]) -> unpack('>i')
            raw_be = struct.pack('>i', value) if signed else struct.pack('>I', value)
            reg0 = raw_be[0] << 8 | raw_be[1]
            reg1 = raw_be[2] << 8 | raw_be[3]
            return [reg1, reg0]

        else:
            raise ValueError(_TR(_CONTEXT, "Unknown byte_order: %1").arg(byte_order))

    @staticmethod
    def string_to_registers(text: str, byte_order: str) -> List[int]:
        """
        将 ASCII 字符串转换为 Modbus 寄存器列表。
        每个寄存器存放两个字符，字符串长度不足偶数时填充 0x00。

        :param text: 待转换的字符串（仅支持 ASCII 字符）
        :param byte_order: 'big_endian'、'little_endian'、'big_endian_swap' 或 'little_endian_swap'。
                           对于字符串，swap 变体与对应基础字节序行为相同。
        :return: 寄存器值列表
        """
        # 确保字符串为 ASCII 编码（每个字符一个字节）
        try:
            encoded = text.encode('ascii')
        except UnicodeEncodeError:
            raise ValueError("Only ASCII strings are supported")

        # 若长度为奇数，补一个空字节
        if len(encoded) % 2 != 0:
            encoded += b'\x00'

        # 对于字符串，swap 变体等同于对应的基础字节序
        if byte_order in ("big_endian_swap",):
            byte_order = "big_endian"
        elif byte_order in ("little_endian_swap",):
            byte_order = "little_endian"

        registers = []
        for i in range(0, len(encoded), 2):
            high_byte = encoded[i]
            low_byte = encoded[i + 1]
            if byte_order == "big_endian":
                # 高字节在前（第一个字符在高字节）
                reg_value = (high_byte << 8) | low_byte
            elif byte_order == "little_endian":
                # 低字节在前（第一个字符在低字节）
                reg_value = (low_byte << 8) | high_byte
            else:
                raise ValueError(f"Unsupported byte_order for string: {byte_order}")
            registers.append(reg_value)
        return registers

    @staticmethod
    def registers_to_string(registers: List[int], byte_order: str = "big_endian") -> str:
        """
        将 Modbus 寄存器列表转换为 ASCII 字符串。
        每个寄存器包含两个字符，顺序由 byte_order 决定。

        :param registers: 寄存器值列表（每个元素 0~65535）
        :param byte_order: 'big_endian' 或 'little_endian'，swap 变体自动映射为基础字节序
        :return: 解码后的字符串（自动去除尾部填充的空字符和空格）
        """
        # 将 swap 变体映射为基础字节序（字符串不涉及多寄存器顺序交换）
        if byte_order in ("big_endian_swap",):
            byte_order = "big_endian"
        elif byte_order in ("little_endian_swap",):
            byte_order = "little_endian"

        chars = []
        for reg in registers:
            if not (0 <= reg <= 0xFFFF):
                raise ValueError("Register value must be between 0 and 65535")
            if byte_order == "big_endian":
                # 高字节在前（第一个字符在高字节）
                high_byte = (reg >> 8) & 0xFF
                low_byte = reg & 0xFF
            elif byte_order == "little_endian":
                # 低字节在前（第一个字符在低字节）
                high_byte = reg & 0xFF
                low_byte = (reg >> 8) & 0xFF
            else:
                raise ValueError(f"Unsupported byte_order for string: {byte_order}")
            # 将字节转换为字符，0x00 和 0x20 视为填充，但保留中间可能出现的有效空格
            # 这里先收集所有字符，最后统一去除尾部填充
            chars.append(chr(high_byte))
            chars.append(chr(low_byte))

        # 去掉字符串末尾的填充字符（空字符或空格），保留中间内容
        result = ''.join(chars).rstrip('\x00 ')
        return result
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
modbus_to_string = ConversionService.registers_to_string
ppm_to_mgm3 = ConversionService.ppm_to_mgm3
ppb_to_mgm3 = ConversionService.ppb_to_mgm3
mgm3_to_ppm = ConversionService.mgm3_to_ppm
ppm_to_ugm3 = ConversionService.ppm_to_ugm3
ppb_to_ugm3 = ConversionService.ppb_to_ugm3
ugm3_to_ppm = ConversionService.ugm3_to_ppm
float_to_registers = ConversionService.float_to_registers
int32_to_registers = ConversionService.int32_to_registers
string_to_registers = ConversionService.string_to_registers
