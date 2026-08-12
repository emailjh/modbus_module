import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal

from modbus_module.clients import PymodbusTcpClient
from modbus_module.device_communicator import DeviceCommunicator


# 导入独立模块

class MyApp(QObject):
    data_signal = Signal(str, dict)   # 接收采集数据

    def __init__(self):
        super().__init__()
        # 配置连接参数（字典格式，符合新接口）
        conn_params = {'host': '192.168.100.247', 'port': 503, 'timeout': 3.0}
        # 因子配置列表（示例）
        factor_configs = [
            {
                "factor": "PM2.5-环境湿度",
                "register_address": 0,
                "register_count": 2,
                "data_type": "float32",
                "byte_order": "little_endian_swap",
                "scale": 1.0,
                "offset": 0.0,
                "is_enabled": True
            }
        ]
        # 创建 Modbus TCP 客户端
        client = PymodbusTcpClient()
        # 创建通信器（外壳）
        self.comm = DeviceCommunicator(
            device_id="1",
            client=client,
            factor_configs=factor_configs,
            connection_params=conn_params,
            poll_interval=5
        )
        self.comm.data_ready.connect(self.on_data_ready)
        self.comm.comm_error.connect(self.on_error)

    def start(self):
        self.comm.start()

    def stop(self):
        self.comm.stop()

    def on_data_ready(self, device_id, data):
        print(f"Data from {device_id}: {data}")

    def on_error(self, device_id, msg):
        print(f"Error on {device_id}: {msg}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    worker = MyApp()
    worker.start()
    sys.exit(app.exec())