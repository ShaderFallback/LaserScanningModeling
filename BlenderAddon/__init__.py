import bpy
import threading
import time
import serial

bl_info = {
    "name": "LaserScanningTool",
    "author": "ShaderFallback",
    "description": "",
    "blender": (2, 80, 0),
    "version": (0, 0, 1),
    "location": "",
    "warning": "",
    "category": "Generic"
}

# 全局变量来保存进度和连接状态
progress = 0.0
is_scanning = False
stop_requested = False
is_connected = False
ser = None  # 串口对象
data_thread = None  # 用于接收串口数据的线程

def remap(value, from_min, from_max, to_min, to_max):
    from_span = from_max - from_min
    to_span = to_max - to_min
    value_scaled = float(value - from_min) / float(from_span)
    return to_min + (value_scaled * to_span)

def update_ui():
    # 强制UI更新
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    if is_scanning:
        return 0.1  # 每0.1秒调用一次
    else:
        return None  # 停止定时器

def scan_task():
    global progress, is_scanning, stop_requested
    for i in range(100):
        if stop_requested:
            progress = 0.0
            break
        progress = i  # 进度从0到100
        time.sleep(1)  # 模拟耗时操作
    if not stop_requested:
        progress = 100.0  # 完成后设为100%
    is_scanning = False
    stop_requested = False
    bpy.app.timers.register(update_ui)

def receive_data():
    global ser, is_scanning, stop_requested
    while is_scanning and ser.is_open:
        try:
            data = ser.read_until(expected=b'#')
            data = data.decode('utf-8').strip()
            if data:
                print(f"Received: {data}")
        except serial.SerialException as e:
            print(f"Error reading from serial port: {e}")
            is_scanning = False
            stop_requested = True

class ButtonExplode(bpy.types.Operator):
    bl_idname = "button.explode"
    bl_label = "开始扫描"

    def execute(self, context):
        global is_scanning, stop_requested, data_thread
        if not is_scanning:
            is_scanning = True
            self.report({'INFO'}, "开始扫描...")
            my_thread = threading.Thread(target=scan_task)
            my_thread.start()
            if ser and ser.is_open:
                data_thread = threading.Thread(target=receive_data)
                data_thread.start()
            bpy.app.timers.register(update_ui)
            ButtonExplode.bl_label = "停止扫描"
        else:
            self.report({'INFO'}, "停止扫描...")
            stop_requested = True
            if ser and ser.is_open:
                ser.close()
            ButtonExplode.bl_label = "开始扫描"
        return {'FINISHED'}
    
class ButtonSerial(bpy.types.Operator):
    bl_idname = "button.serial"
    bl_label = "连接串口"

    def execute(self, context):
        global is_connected, ser
        if not is_connected:
            port = context.scene.my_string
            try:
                ser = serial.Serial(port,115200)
                self.report({'INFO'}, f"已连接到串口 {port}")
                is_connected = True
                ButtonSerial.bl_label = "断开"
            except serial.SerialException as e:
                self.report({'ERROR'}, f"连接串口 {port} 失败: {e}")
        else:
            if ser and ser.is_open:
                ser.close()
                self.report({'INFO'}, "串口已断开")
            is_connected = False
            ButtonSerial.bl_label = "连接串口"
        bpy.app.timers.register(update_ui)
        return {'FINISHED'}

class InputPortOperator(bpy.types.Operator):
    bl_idname = "inport.port"
    bl_label = "PrintInput"

    def execute(self, context):
        print("Input Text: ", context.scene.my_string)
        return {'FINISHED'}
    
class HelloWorldPanel(bpy.types.Panel):
    bl_idname = "TEST_SCRIPTS"
    bl_label = "LaserScanningTool"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'LaserScanningTool'

    def draw(self, context):
        global progress, is_scanning, is_connected
        layout = self.layout
        scene = context.scene
        
        layout.label(text="Serial")
        layout.prop(scene, "my_string", text="Port")
        layout.operator("button.serial", text="断开" if is_connected else "连接")
        
        layout.label(text="Scanning")
        layout.operator("button.explode", text="停止" if is_scanning else "开始")

        layout.label(text=f"Progress: {progress:.1f}%")
        value = remap(progress, 0, 100, 0, 1)
        layout.prop(scene, "scan_progress", text="")
        layout.progress(factor=value)
        
def register():
    bpy.utils.register_class(HelloWorldPanel)
    bpy.utils.register_class(ButtonExplode)
    bpy.utils.register_class(ButtonSerial)
    bpy.utils.register_class(InputPortOperator)
    bpy.types.Scene.scan_progress = bpy.props.FloatProperty(name="Scan Progress", min=0, max=100, default=0.0)
    bpy.types.Scene.my_string = bpy.props.StringProperty(name="Input Text", default="COM3")  # 定义字符串属性并设置默认值

def unregister():
    bpy.utils.unregister_class(HelloWorldPanel)
    bpy.utils.unregister_class(ButtonExplode)
    bpy.utils.unregister_class(ButtonSerial)
    bpy.utils.unregister_class(InputPortOperator)
    del bpy.types.Scene.scan_progress
    del bpy.types.Scene.my_string  # 删除字符串属性

if __name__ == "__main__":
    register()
