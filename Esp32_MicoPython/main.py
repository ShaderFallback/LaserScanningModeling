from machine import Pin, I2C
import time
import vl53l0x
import _thread
# 定义引脚
IN1 = Pin(18, Pin.OUT)
IN2 = Pin(19, Pin.OUT)
IN3 = Pin(5, Pin.OUT)
IN4 = Pin(17, Pin.OUT)

INY1 = Pin(23, Pin.OUT)
INY2 = Pin(16, Pin.OUT)
INY3 = Pin(4, Pin.OUT)
INY4 = Pin(0, Pin.OUT)

IN1.value(0)
IN2.value(0)
IN3.value(0)
IN4.value(0)
INY1.value(0)
INY2.value(0)
INY3.value(0)
INY4.value(0)

i2c = I2C(scl=Pin(13), sda=Pin(15))
# 初始化VL53L0X传感器
tof = vl53l0x.VL53L0X(i2c)
# 启动传感器
tof.start()

# 半步模式相位顺序
half_step_sequence = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1]
]

def DistanceThread():
    #获取距离测量值
    distance = tof.read() - 95.0
    return distance
    
    
def step_motor_Y(steps, direction=1, delay=1):
    sequence = half_step_sequence if direction == 1 else list(reversed(half_step_sequence))
    for _ in range(steps):
        for step in sequence:
            INY1.value(step[0])
            INY2.value(step[1])
            INY3.value(step[2])
            INY4.value(step[3])
            time.sleep_ms(delay)
        
def step_motor_X(steps, direction=1, delay=1):
    sequence = half_step_sequence if direction == 1 else list(reversed(half_step_sequence))
    for _ in range(steps):
        for step in sequence:
            IN1.value(step[0])
            IN2.value(step[1])
            IN3.value(step[2])
            IN4.value(step[3])
            time.sleep_ms(delay)
        #512步,每走一步输出距离
        #DistanceThread()
                   
def MotorThread(name):
    #try:
        #半步模式下512步每圈
        #y轴滑台旋转一圈约为 120 mm
        #stepDis = (512.0/12.0)#约走1mm
        #512/360 = 每度要走的不仅
    while(True):
        for i in range(360):
            step_motor_X(0.703125, direction=1, delay=1)
            distance = DistanceThread()
            print("angle:"+str(i) + "  distance:" + str(distance) + "#")
            time.sleep(0.5)
        step_motor_Y(42.6666667,direction=1, delay=1)
            
#     except Exception as e:
#         print("Error: ", e)
#         IN1.value(0)
#         IN2.value(0)
#         IN3.value(0)
#         IN4.value(0)
#         INY1.value(0)
#         INY2.value(0)
#         INY3.value(0)
#         INY4.value(0)
#         print("Motor stopped")       
_thread.start_new_thread(MotorThread, ("",))