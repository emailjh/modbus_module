# -*- coding: utf-8 -*-
"""
统一日志模块 (Logger)

提供全局日志记录器，支持：
- 文件日志（按天轮转，保留 30 天，实际业务数据保留 5 年由数据库实现）
- 控制台输出（开发调试用）
- 错误级别日志通过 EventBus 发送到 UI 状态栏
- 日志格式统一，包含时间、模块、级别、消息
- 线程安全，可在任意线程调用
"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

# EventBus 延迟导入，避免循环依赖（由主初始化时注入）
_event_bus = None

# 全局 logger 实例
_logger: Optional[logging.Logger] = None


def setup_logging(log_dir: str = "logs",
                  level: int = logging.DEBUG,
                  file_level: int = logging.DEBUG,
                  console_level: int = logging.INFO,
                  max_days: int = 30) -> logging.Logger:
    """
    初始化日志系统，配置日志处理器并返回全局 logger。
    :param log_dir: 日志文件存放目录
    :param level: logger 的整体级别（通常为 DEBUG，由各 handler 独立控制）
    :param file_level: 文件处理器的日志级别
    :param console_level: 控制台处理器的日志级别
    :param max_days: 日志文件保留天数
    """
    global _logger
    if _logger is not None:
        return _logger

    # 清除根 logger 的所有 handler，防止默认 StreamHandler 产生重复日志
    logging.getLogger().handlers.clear()

    _logger = logging.getLogger("modbus")
    _logger.propagate = False   # 新增：禁止日志向根 logger 传播
    _logger.setLevel(level)

    # 清除可能已存在的处理器（避免重复初始化）
    _logger.handlers.clear()

    # 创建日志目录
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # 日志格式
    formatter = logging.Formatter(  # %(filename)s
        '%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d %(funcName)s()] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 文件处理器（按天轮转，保留 max_days 天）
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=Path(log_dir) / "modbus.log",
        when="midnight",
        interval=1,
        backupCount=max_days,
        encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    _logger.addHandler(console_handler)

    # 添加自定义处理器：将 ERROR 及以上级别的日志发送到 EventBus
    try:
        from common.event_bus import EventBus
        event_bus = EventBus.instance()
        signal_handler = _SignalLogHandler(event_bus)
        signal_handler.setLevel(logging.ERROR)
        signal_handler.setFormatter(formatter)
        _logger.addHandler(signal_handler)
    except Exception:
        # EventBus 尚未初始化时忽略，后续可通过 set_event_bus 手动设置
        pass

    _logger.info("Logging system initialized.")
    return _logger


def set_event_bus(bus: object):
    """
    手动注入 EventBus，用于将错误日志转发到 UI。
    若在 setup_logging 时 EventBus 还未就绪，可在主程序启动后调用此方法。
    """
    global _logger
    if _logger is None:
        return
    from common.event_bus import EventBus
    # 查找已有的 SignalHandler 并更新 event_bus
    for handler in _logger.handlers:
        if isinstance(handler, _SignalLogHandler):
            handler.event_bus = bus
            break


class _SignalLogHandler(logging.Handler):
    """
    自定义日志处理器，将 ERROR 及以上日志通过 EventBus 发送到主界面状态栏。
    """
    def __init__(self, event_bus):
        super().__init__()
        self.event_bus = event_bus

    def emit(self, record: logging.LogRecord):
        if self.event_bus is None:
            return
        msg = self.format(record)
        # 使用 EventBus 的全局信号发送错误消息（信号名根据需要定义）
        # 注意：此信号可能触发 UI 更新，需确保槽函数在主线程执行（自动）
        try:
            self.event_bus.log_error.emit(msg)
        except Exception:
            pass  # 避免日志处理本身抛出异常


def get_logger(name: str = "modbus") -> logging.Logger:
    """
    获取指定名称的 logger 实例。
    若系统日志尚未初始化，返回默认 root logger。
    """
    global _logger
    if _logger is not None:
        return _logger.getChild(name)
    else:
        return logging.getLogger(name)