import bpy
import threading
import time
import serial
import socket
import threading
import math
import time
import bmesh

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
host = '127.0.0.1' #远程地址
port = 5555     #端口号
port_com = "COM5"  #串口号
baud_rate = 115200  #波特率
maxVertexCount = 0 #估计顶点数量
maximumStroke = 4.5 #Y轴滑轨位移最大距离
scanFrequency = 120 #扫描频率 次/秒
displayUpdateInterval = 128 #视图顶点更新间隔
elapsedSeconds = 0  #已用时间
rotationSpeed = 100.0 #Y轴旋转速度
stepY = 0.05    #Y轴位移步进
vertexTotal = 0   #顶点数总计
progress = 0.0 #处理进度
is_scanning = False #是否正在扫描
is_connected = False #是否连接
serObject = None  #串口对象
data_thread = None  #用于接收串口数据的线程
scan_thread = None
displaInfoStr = ""  #Socket接收的原始信息
dataBuffer = []  #用于缓存接收到的数据,防止丢失数据（栈结构）
server_socket = None  #socket服务端对象
client_socket = None  #socket客户端对象

def time_display(elapsedSeconds):
    total_seconds_int = int(elapsedSeconds)

    # 2. 计算小时 (Hours)
    hours = total_seconds_int // 3600

    # 3. 计算剩余分钟 (Minutes)
    # 总秒数对 3600 取余，得到不满一小时的秒数；再除以 60 得到分钟数
    minutes = (total_seconds_int % 3600) // 60

    # 4. 计算剩余秒数 (Seconds)
    # 总秒数对 60 取模，得到不满一分钟的秒数
    seconds = total_seconds_int % 60

    # 5. 格式化输出 (HH:MM:SS)
    time_format = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return time_format
def init_socket():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen()
    print(f"Server is listening on {host}:{port}")
    
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
    
_commandSentinel = object()
def send_command(command,speed=_commandSentinel,step=_commandSentinel):
    global is_connected
    if not is_connected:
        return
    
    commandStr = f"{command};{speed};{step}"
    
    if speed is _commandSentinel:
        commandStr = f"{command}"
    if step is _commandSentinel:
        commandStr = f"{command}"
        
    
    commandEncoding = commandStr.encode() + b"\n"
    if serObject and serObject.is_open:
        serObject.write(commandEncoding)
    elif client_socket:
        client_socket.sendall(commandEncoding)

def scan_task():
    global progress, is_scanning, dataBuffer, vertexTotal
    global stepY, rotationSpeed, elapsedSeconds, displayUpdateInterval
    global scanFrequency, maxVertexCount
    
    startTime = time.time()
    vertexTotal = 0
    
    send_command("start",rotationSpeed,stepY)    
    
    try:       
        #创建 mesh
        mesh = bpy.data.meshes.new("ScanPoints")
        obj = bpy.data.objects.new("ScanPoints", mesh)
        obj.rotation_euler[0] = math.radians(90)
        bpy.context.collection.objects.link(obj)

        #verts = []
        bm = bmesh.new()
        
        #估算总数据点大小
        #按 60 FPS/秒 
        #y轴位移假设移动距离 0-4 每次步进 0.1 及 4/0.1 = 40
        #Speed 是旋转的速度，单位是 度/秒。
        # 360/100 = 3.6 秒/圈
        # 3.6*60 = 216点,每圈216点
        # 总计 216 * 40 =8640点
        total = int((360/rotationSpeed) * (maximumStroke/stepY) * scanFrequency)
        maxVertexCount = total
        countVer = 0
        # 从栈顶依次取出数据直到空
        for i in range(total):
            if is_scanning == False:
                progress = 0.0
                break
            if not dataBuffer:
                time.sleep(0.01)
                continue
            
            try:
                line = dataBuffer.pop(0)
            except IndexError:
                continue
            
            line = line.strip()
            if not line:
                continue
            try:
                distance, rotation, positionY = map(float, line.split(';'))
            except ValueError:
                print(f"跳过格式错误行: {line}")
                continue
            
            #处理射线没有命中情况
            if distance <=0: 
                pass
            else:
            #正常数据需要减去标定激光0点距离
                distance -= 8.2
                # 坐标转换
                x = distance * math.cos(math.radians(rotation))
                z = distance * math.sin(math.radians(rotation))
                y = positionY
                #verts.append((x, y, z))
                bm.verts.new((x, y, z))
                vertexTotal += 1
                
            # 更新进度
            progress = remap(i, 0, total, 0, 100) 
            
            elapsedSeconds = time.time() - startTime
            
            #每64个点重建一次Mesh,方便预览,(不能频繁重建会宕机)
            countVer += 1
            if countVer >= displayUpdateInterval:
                bm.to_mesh(mesh)
                mesh.update()
                countVer = 0
              
        #生成 mesh 点云   
        #mesh.from_pydata(verts, [], [])
        #mesh.update()
        bm.to_mesh(mesh)
        mesh.update()
        bm.free()
    except Exception as e:
        print(f"扫描任务出错: {e}")

    is_scanning = False  
    progress = 0.0
    dataBuffer.clear()
    bpy.app.timers.register(update_ui)

def receive_data():
    global serObject, is_scanning, displaInfoStr, dataBuffer
    serObject.timeout = 1.0
    while is_scanning and serObject.is_open:
        try:
            data = serObject.read_until(expected=b'#')
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
    client_socket.settimeout(1.0)
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
        global is_scanning, data_thread, scan_thread, stepY, rotationSpeed
        global scanFrequency, is_connected, maximumStroke, displayUpdateInterval
        
        if not is_connected:
            self.report({'ERROR'}, "请先连接串口,或主机")
            return {'CANCELLED'}
        
        if not is_scanning:
            is_scanning = True
            try:
                stepY = float(context.scene.str_input_step)
                rotationSpeed = float(context.scene.str_input_rotation_speed)
                scanFrequency = float(context.scene.str_input_scan_frequency)
                maximumStroke = float(context.scene.str_input_maximum_stroke)
                displayUpdateInterval = int(context.scene.str_input_display_update_interval)
            except ValueError:
                self.report({'ERROR'}, "参数配置错误")
                return {'CANCELLED'}
            
            self.report({'INFO'}, "开始扫描...")
            
            if scan_thread is None or not scan_thread.is_alive():
                scan_thread = threading.Thread(target=scan_task)
                scan_thread.start()
            if serObject and serObject.is_open:
                if data_thread is None or not data_thread.is_alive():
                    data_thread = threading.Thread(target=receive_data)
                    data_thread.start()
            elif client_socket:
                if data_thread is None or not data_thread.is_alive():
                    data_thread = threading.Thread(target=receive_socket_data)
                    data_thread.start()
            bpy.app.timers.register(update_ui)
            ButtonExplode.bl_label = "停止扫描"
            
        else:
            send_command("stop")
            is_scanning = False
            #ButtonSerial.execute(self, context)
            self.report({'INFO'}, "停止扫描...")
            #停止扫描会下发命令而不是断开连接
            # if serObject and serObject.is_open:
            #     serObject.close()
            ButtonExplode.bl_label = "开始扫描"
        return {'FINISHED'}
    
class ButtonSerial(bpy.types.Operator):
    bl_idname = "button.serial"
    bl_label = "连接串口"

    def execute(self, context):
        global is_connected, serObject, client_socket, baud_rate, port_com, host, port
        scene = context.scene
        
        is_remote = scene.bool_isremote
        host = scene.str_inputaddress
        port = scene.str_inputport
        port_com = scene.str_inputport_com
        baud_rate = scene.str_input_baud_Rate

        if not is_connected:
            if is_remote:
                # 远程连接模式 - 连接到本地socket服务器
                try:                    
                    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    client_socket.connect((host, int(port)))
                    
                    self.report({'INFO'}, f"已连接到服务器 {host}:{port}")
                    is_connected = True
                    ButtonSerial.bl_label = "断开"
                except Exception as e:
                    self.report({'ERROR'}, f"连接失败: {e}")
            else:
                # 串口连接模式
                try:
                    serObject = serial.Serial(port_com, int(baud_rate))
                    self.report({'INFO'}, f"已连接到串口 {port_com}")
                    is_connected = True
                    ButtonSerial.bl_label = "断开"
                except serial.SerialException as e:
                    self.report({'ERROR'}, f"哎呀,连接串口 {port_com} 失败了: {e}")
        else:
            # 断开连接
            if is_remote:
                if client_socket:
                    client_socket.close()
                    client_socket = None
                self.report({'INFO'}, "远程连接已断开")
            else:
                if serObject and serObject.is_open:
                    serObject.close()
                    self.report({'INFO'}, "串口已断开")
            
            is_connected = False
            ButtonSerial.bl_label = "连接串口"
        
        bpy.app.timers.register(update_ui)
        return {'FINISHED'}

class ButtonTest(bpy.types.Operator):
    bl_idname = "button.button_test"
    bl_label = "Test"

    def execute(self, context):  
        import threading
        threads = threading.enumerate()
        print("当前活动线程数:", len(threads))
        for t in threads:
            print("线程名:", t.name, "是否守护线程:", t.daemon)
        return {'FINISHED'}
def prop_aligned(layout, label, data, prop):
    row = layout.row(align=True)
    split = row.split(factor=0.5)
    split.label(text=label)
    split.prop(data, prop, text="")
   
class HelloWorldPanel(bpy.types.Panel):
    bl_idname = "TEST_SCRIPTS"
    bl_label = "LaserScanningTool"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'LaserScanningTool'
    
    def draw(self, context):
        global progress, is_scanning, is_connected, displaInfoStr
        global dataBuffer, vertexTotal, elapsedSeconds, maxVertexCount, baud_rate, port_com, host, port
        layout = self.layout
        scene = context.scene
        
        if is_connected:
            layout.label(text="Socket Linking Ok")
        else:
            prop_aligned(layout, "Socket Link", scene, "bool_isremote")

        is_remote = scene.bool_isremote

              
        if(is_connected):
            if is_remote:
                layout.label(text="Address: " + str(host))
                layout.label(text="Port:    " + str(port))
            else:
                layout.label(text="Port:    "+ scene.str_inputport)
        else:
            if is_remote:
                prop_aligned(layout, "Address", scene, "str_inputaddress")
                prop_aligned(layout, "Port", scene, "str_inputport")

            else:
                prop_aligned(layout, "COM", scene, "str_inputport_com")
                prop_aligned(layout, "BaudRate", scene, "str_input_baud_Rate")

        
        
        layout.operator("button.serial", text="断开" if is_connected else "连接")
        layout.label(text="Scanning")
                
        prop_aligned(layout, "MaxStroke", scene, "str_input_maximum_stroke")
        prop_aligned(layout, "Y Step", scene, "str_input_step")
        prop_aligned(layout, "RotationSpeed", scene, "str_input_rotation_speed")
        prop_aligned(layout, "UpdateInterval", scene, "str_input_display_update_interval")
        prop_aligned(layout, "ScanFrequency", scene, "str_input_scan_frequency")
        
        
        layout.operator("button.explode", text="停止" if is_scanning else "开始")

        layout.label(text=f"Progress: {progress:.1f}%")
        value = remap(progress, 0, 99, 0, 1)
        layout.progress(factor=value)
        
        layout.label(text = f"ReceivedInfo: {displaInfoStr}")
        layout.label(text = f"BufferCount: {len(dataBuffer)}")
        layout.label(text = f"maxVertexCount: {maxVertexCount}")
        layout.label(text = f"VertexTotal: {vertexTotal}")
        layout.label(text = f"Time: {time_display(elapsedSeconds)}")
        layout.label(text = "Github ShaderFallback =^_^=")
        layout.label(text = "LaserScanningModeling")
        
        #layout.operator("button.button_test", text="DebugPrint")


def register():
    bpy.utils.register_class(HelloWorldPanel)
    bpy.utils.register_class(ButtonExplode)
    bpy.utils.register_class(ButtonSerial)
    bpy.utils.register_class(ButtonTest)
    bpy.types.Scene.scan_progress = bpy.props.FloatProperty(name="ScanProgress", min=0, max=100, default=0.0)
    bpy.types.Scene.bool_isremote = bpy.props.BoolProperty(name="RemoteConnection", default=False)
    bpy.types.Scene.str_inputaddress = bpy.props.StringProperty(name="InputAddress", default=host)
    bpy.types.Scene.str_inputport = bpy.props.StringProperty(name="InputPort", default=str(port))
    bpy.types.Scene.str_inputport_com = bpy.props.StringProperty(name="COMPort", default=port_com)
    bpy.types.Scene.str_input_step = bpy.props.StringProperty(name="OffsetStep", default="0.05")
    bpy.types.Scene.str_input_rotation_speed = bpy.props.StringProperty(name="RotationSpeed", default="100")
    bpy.types.Scene.str_input_display_update_interval = bpy.props.StringProperty(name="displayUpdateInterval", default="128")
    bpy.types.Scene.str_input_scan_frequency = bpy.props.StringProperty(name="scanFrequency", default="120")
    bpy.types.Scene.str_input_maximum_stroke = bpy.props.StringProperty(name="maximumStroke", default="4.5") 
    bpy.types.Scene.str_input_baud_Rate = bpy.props.StringProperty(name="BaudRate", default="115200")
    

def unregister():
    bpy.utils.unregister_class(HelloWorldPanel)
    bpy.utils.unregister_class(ButtonExplode)
    bpy.utils.unregister_class(ButtonSerial)
    bpy.utils.unregister_class(ButtonTest)
    del bpy.types.Scene.scan_progress
    del bpy.types.Scene.bool_isremote
    del bpy.types.Scene.str_inputaddress
    del bpy.types.Scene.str_inputport
    del bpy.types.Scene.str_inputport_com
    del bpy.types.Scene.str_input_step
    del bpy.types.Scene.str_input_rotation_speed
    del bpy.types.Scene.str_input_display_update_interval
    del bpy.types.Scene.str_input_scan_frequency
    del bpy.types.Scene.str_input_maximum_stroke
    del bpy.types.Scene.str_input_baud_Rate

if __name__ == "__main__":
    register()