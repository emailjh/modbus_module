from setuptools import setup, find_packages

setup(
    name='modbus_module',                    # 包名（安装后 import 使用的名称）
    version='0.1.3',
    description='Reusable Modbus communication module based on PySide6 and pymodbus',
    author='emailjh',
    packages=find_packages(),                # 自动找到 modbus_module 包
    install_requires=[
        'pyside6',
        'pymodbus',
        # 如果有其他依赖，如 gmssl 等，也加入
    ],
    python_requires='>=3.8',
    include_package_data=True,
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
)