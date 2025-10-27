import bpy
import threading
import time
import serial
import socket
import threading

# 测试服务器的 IP 地址和端口
HOST = '127.0.0.1'
PORT = 5555
PORT_COM = "COM5"

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

progress = 0.0
is_scanning = False
is_connected = False
ser = None  # 串口对象
data_thread = None  # 用于接收串口数据的线程
displaInfoStr = ""
dataBuffer = []  # 用于缓存接收到的数据,防止丢失数据（栈结构）
server_socket = None  # socket服务端对象
client_socket = None  # socket客户端对象

def init_socket():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((HOST, PORT))
    server_socket.listen()
    print(f"Server is listening on {HOST}:{PORT}")
    
def handle_client(client_socket):
    print("Client connected")
    
    while True:
        try:
            # 接收数据
            data = client_socket.recv(1024).decode('utf-8')
            
            if not data:
                break
            
            print(f"Received: {data}")
        except Exception as e:
            print(f"Error handling client: {e}")
            break
    
    print("Client disconnected")
    client_socket.close()

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

def send_command(command):
    commandEncoding = command.encode() + b"\n"
    if ser and ser.is_open:
        ser.write(commandEncoding)
    elif client_socket:
        client_socket.sendall(commandEncoding)
def scan_task():
    global progress, is_scanning
    
    send_command("start")
        
    for i in range(100):
        # 从栈顶处理数据（最后加入的数据优先处理）
        if dataBuffer:
            print(f"Postprocess: {dataBuffer[-1]}")
            dataBuffer.pop()  # 处理完成后从栈顶删除数据
            
        # 进度从0到100
        progress = i  
        
        # 用户主动停止,跳出循环
        if is_scanning == False:
            progress = 0.0
            break
        
        time.sleep(1)  # 模拟耗时操作
    
    is_scanning = False  
    progress = 100.0  # 完成后设为100%
    
    send_command("stop")
    
    bpy.app.timers.register(update_ui)

def receive_data():
    global ser, is_scanning, displaInfoStr, dataBuffer
    while is_scanning and ser.is_open:
        try:
            data = ser.read_until(expected=b'#')
            data = data.decode('utf-8').strip()
            if data:
                print(f"Received: {data}")
                displaInfoStr = data  
                dataBuffer.append(data)         
        except serial.SerialException as e:
            print(f"Error reading from serial port: {e}")
            is_scanning = False

# 添加socket接收数据函数
def receive_socket_data():
    global client_socket, is_scanning, displaInfoStr, dataBuffer
    while is_scanning and client_socket:
        try:
            data = client_socket.recv(1024).decode('utf-8').strip()
            if data:
                print(f"Received from socket: {data}")
                displaInfoStr = data
                dataBuffer.append(data)
        except Exception as e:
            print(f"Error reading from socket: {e}")
            is_scanning = False

class ButtonExplode(bpy.types.Operator):
    bl_idname = "button.explode"
    bl_label = "开始扫描"

    def execute(self, context):
        global is_scanning, data_thread
        if not is_scanning:
            is_scanning = True
            self.report({'INFO'}, "开始扫描...")
            my_thread = threading.Thread(target=scan_task)
            my_thread.start()
            if ser and ser.is_open:
                data_thread = threading.Thread(target=receive_data)
                data_thread.start()
            elif client_socket:
                data_thread = threading.Thread(target=receive_socket_data)
                data_thread.start()
            bpy.app.timers.register(update_ui)
            ButtonExplode.bl_label = "停止扫描"
        else:
            is_scanning = False
            self.report({'INFO'}, "停止扫描...")
            
            #停止扫描会下发命令而不是断开连接
            # if ser and ser.is_open:
            #     ser.close()
            ButtonExplode.bl_label = "开始扫描"
        return {'FINISHED'}
    
class ButtonSerial(bpy.types.Operator):
    bl_idname = "button.serial"
    bl_label = "连接串口"

    def execute(self, context):
        global is_connected, ser, client_socket
        scene = context.scene
        
        # 使用场景属性替代全局变量
        is_remote = scene.bool_isremote
        
        if not is_connected:
            if is_remote:
                # 远程连接模式 - 连接到本地socket服务器
                try:
                    address = scene.str_inputaddress if scene.str_inputaddress else HOST
                    port = int(scene.str_inputport) if scene.str_inputport.isdigit() else PORT
                    
                    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    client_socket.connect((address, port))
                    print(f"Connected to server at {address}:{port}")
                    
                    self.report({'INFO'}, f"已连接到服务器 {address}:{port}")
                    is_connected = True
                    ButtonSerial.bl_label = "断开"
                except Exception as e:
                    self.report({'ERROR'}, f"连接失败: {e}")
            else:
                # 串口连接模式
                # 修改: 使用 str_inputport_com 而不是 str_inputport
                port = context.scene.str_inputport_com
                try:
                    ser = serial.Serial(port, 115200)
                    self.report({'INFO'}, f"已连接到串口 {port}")
                    is_connected = True
                    ButtonSerial.bl_label = "断开"
                except serial.SerialException as e:
                    self.report({'ERROR'}, f"哎呀,连接串口 {port} 失败了: {e}")
        else:
            # 断开连接
            if is_remote:
                if client_socket:
                    client_socket.close()
                    client_socket = None
                self.report({'INFO'}, "远程连接已断开")
            else:
                if ser and ser.is_open:
                    ser.close()
                    self.report({'INFO'}, "串口已断开")
            
            is_connected = False
            ButtonSerial.bl_label = "连接串口"
        
        bpy.app.timers.register(update_ui)
        return {'FINISHED'}
    
class HelloWorldPanel(bpy.types.Panel):
    bl_idname = "TEST_SCRIPTS"
    bl_label = "LaserScanningTool"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'LaserScanningTool'

    def draw(self, context):
        global progress, is_scanning, is_connected, displaInfoStr
        layout = self.layout
        scene = context.scene
        
        layout.prop(scene, "bool_isremote", text="远程连接")
        
        is_remote = scene.bool_isremote
        if is_remote:           
            layout.label(text="Socket")
        else:
            layout.label(text="Serial")  
              
        if(is_connected):
            if is_remote:
                layout.label(text="Address: " + (scene.str_inputaddress if scene.str_inputaddress else HOST))
                layout.label(text="Port:    " + (scene.str_inputport if scene.str_inputport else str(PORT)))
            else:
                layout.label(text="Port:    "+ scene.str_inputport)
        else:
            if is_remote:
                layout.prop(scene, "str_inputaddress", text="Address")
                layout.prop(scene, "str_inputport", text="Port")
            else:
                layout.prop(scene, "str_inputport_com", text="COM")
                
        layout.operator("button.serial", text="断开" if is_connected else "连接")
        
        layout.label(text="Scanning")
        layout.operator("button.explode", text="停止" if is_scanning else "开始")

        layout.label(text=f"Progress: {progress:.1f}%")
        value = remap(progress, 0, 99, 0, 1)
        layout.progress(factor=value)
        
        layout.label(text = f"Info: {displaInfoStr}")
        layout.separator() 
        layout.label(text = "Debug")
        layout.label(text = "Github: ShaderFallback")

        
def register():
    bpy.utils.register_class(HelloWorldPanel)
    bpy.utils.register_class(ButtonExplode)
    bpy.utils.register_class(ButtonSerial)
    bpy.types.Scene.scan_progress = bpy.props.FloatProperty(name="Scan Progress", min=0, max=100, default=0.0)
    bpy.types.Scene.bool_isremote = bpy.props.BoolProperty(name="Remote Connection", default=False)
    bpy.types.Scene.str_inputaddress = bpy.props.StringProperty(name="Input Address", default=HOST)
    bpy.types.Scene.str_inputport = bpy.props.StringProperty(name="Input Port", default=str(PORT))
    bpy.types.Scene.str_inputport_com = bpy.props.StringProperty(name="COM Port", default=PORT_COM)

def unregister():
    bpy.utils.unregister_class(HelloWorldPanel)
    bpy.utils.unregister_class(ButtonExplode)
    bpy.utils.unregister_class(ButtonSerial)
    del bpy.types.Scene.scan_progress
    del bpy.types.Scene.bool_isremote
    del bpy.types.Scene.str_inputaddress
    del bpy.types.Scene.str_inputport
    del bpy.types.Scene.str_inputport_com

if __name__ == "__main__":
    register()