# =============================================================================
# 通信参数
# =============================================================================
COMM_PARAM_HOST = "host"
COMM_PARAM_PORT = "port"
COMM_PARAM_TIMEOUT = "timeout"
COMM_PARAM_SERIAL_PORT = "serial_port"
COMM_PARAM_BAUDRATE = "baudrate"
COMM_PARAM_PARITY = "parity"
COMM_PARAM_BYTESIZE = "bytesize"
COMM_PARAM_DATA_BITS = "databits"
COMM_PARAM_STOP_BITS = "stopbits"

# =============================================================================
# 数据状态码 (HJ212-2025 附录 B)
# =============================================================================
DATA_STATUS_NORMAL = "N"          # 正常
DATA_STATUS_CALIBRATING = "C"     # 校准中
DATA_STATUS_OVERFLOW = "O"        # 超量程
DATA_STATUS_FAULT = "F"           # 故障
DATA_STATUS_DISCONNECTED = "D"    # 断线
DATA_STATUS_INVALID = "I"         # 无效
DATA_STATUS_MAP = {
	DATA_STATUS_NORMAL: 'Normal',
	DATA_STATUS_CALIBRATING: 'Calibrating',
	DATA_STATUS_FAULT: 'Fault',
	DATA_STATUS_DISCONNECTED: 'Offline',
	DATA_STATUS_INVALID: 'Invalid',
	DATA_STATUS_OVERFLOW: 'Overrange'
}

# =============================================================================
# 报警级别
# =============================================================================
ALARM_LEVEL_INFO = 1        # 提示
ALARM_LEVEL_WARNING = 2     # 警告
ALARM_LEVEL_CRITICAL = 3    # 严重
ALAEM_LEVEL_MAP = {
    ALARM_LEVEL_INFO: 'Info',
    ALARM_LEVEL_WARNING: 'Warning',
    ALARM_LEVEL_CRITICAL: 'Critical'
}