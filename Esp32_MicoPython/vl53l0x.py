import time
from machine import I2C

VL53L0X_I2C_ADDRESS = 0x29
VL53L0X_REG_IDENTIFICATION_MODEL_ID = 0xC0
VL53L0X_REG_VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV = 0x89
VL53L0X_REG_SYSRANGE_START = 0x00
VL53L0X_REG_SYSTEM_INTERMEASUREMENT_PERIOD = 0x04

class VL53L0X:
    def __init__(self, i2c, address=VL53L0X_I2C_ADDRESS):
        self.i2c = i2c
        self.address = address

        # 确认传感器型号ID是否正确
        model_id = self.i2c.readfrom_mem(self.address, VL53L0X_REG_IDENTIFICATION_MODEL_ID, 1)
        if model_id[0] != 0xEE:
            raise RuntimeError("Failed to find VL53L0X - check your wiring!")
        
        self.i2c.writeto_mem(self.address, VL53L0X_REG_VHV_CONFIG_PAD_SCL_SDA__EXTSUP_HV, b'\x01')

        # 初始化传感器
        self._init_sensor()

    def _init_sensor(self):
        # 启动测距
        self.i2c.writeto_mem(self.address, VL53L0X_REG_SYSRANGE_START, b'\x01')

    def read(self):
        # 读取距离
        self.i2c.writeto_mem(self.address, 0x00, b'\x01')
        time.sleep(0.01)
        result = self.i2c.readfrom_mem(self.address, 0x14, 12)
        distance = (result[10] << 8) | result[11]
        return distance

    def start(self):
        self._init_sensor()

    def stop(self):
        self.i2c.writeto_mem(self.address, VL53L0X_REG_SYSRANGE_START, b'\x00')

    def set_measurement_timing_budget(self, budget_us):
        # 设置测量时间预算
        # budget_us 以微秒为单位的时间预算
        # 典型值：20000us, 33000us, 50000us, 66000us, 100000us, 200000us
        if budget_us < 20000:
            budget_us = 20000
        elif budget_us > 200000:
            budget_us = 200000
        self.i2c.writeto_mem(self.address, VL53L0X_REG_SYSTEM_INTERMEASUREMENT_PERIOD, bytes([budget_us // 1000]))
