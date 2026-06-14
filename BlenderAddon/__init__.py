bl_info = {
    "name": "Laser Scanner",
    "author": "Your Name",
    "description": "Receive images from rotating platform and display them as texture in Blender",
    "blender": (3, 0, 0),
    "version": (1, 4, 0),
    "category": "3D View",
}

import bpy
import socket
import threading
import time
import struct
import numpy as np
import os
import tempfile
import sys

# 确保用户站点包路径在sys.path中（解决Blender早期加载问题）
user_site = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'Python', 'Python311', 'site-packages')
if user_site not in sys.path:
    sys.path.insert(0, user_site)

from PIL import Image

# ================= 全局变量 =================
recv_running = False
scan_running = False
exit_flag = False

# 磁盘缓存
cache_dir = None
frame_counter = 0              # 接收帧计数器
process_counter = 0            # 处理帧计数器

# 统计信息
received_count = 0             # 接收到的帧数
processed_count = 0            # 已处理的帧数

# 参数
distance_max = 1000.0          # 最大量程（毫米）
update_interval = 0.1          # 图片更新间隔（秒）
max_frames = 0                 # 最大接收帧数（0=无限制）
auto_cleanup = True            # 自动清理缓存

# 网络配置
board_ip = "172.32.0.93"
control_port = 9000
data_port = 9001
control_sock = None
ctrl_connected = False
data_connected = False

# 目标贴图
target_image = None

# ================= 辅助函数 =================
def init_cache_directory():
    """初始化缓存目录"""
    global cache_dir
    if cache_dir is None:
        # 在临时目录中创建缓存文件夹
        cache_dir = os.path.join(tempfile.gettempdir(), "blender_laser_scan_cache")
        os.makedirs(cache_dir, exist_ok=True)
        print(f"[Cache] Cache directory: {cache_dir}")

def save_jpeg_to_disk(jpeg_bytes):
    """将JPEG数据保存到磁盘，返回文件路径"""
    global frame_counter
    init_cache_directory()
    
    frame_counter += 1
    filename = f"frame_{frame_counter:06d}.jpg"
    filepath = os.path.join(cache_dir, filename)
    
    try:
        with open(filepath, 'wb') as f:
            f.write(jpeg_bytes)
        return filepath
    except Exception as e:
        print(f"[Cache] Error saving frame: {e}")
        return None

def cleanup_cache_file(filepath):
    """清理单个缓存文件"""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"[Cache] Error: {e}")

def cleanup_all_cache():
    """清理所有缓存文件"""
    global cache_dir
    if cache_dir and os.path.exists(cache_dir):
        try:
            for filename in os.listdir(cache_dir):
                filepath = os.path.join(cache_dir, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)
            print(f"[Cache] Cleaned")
        except Exception as e:
            print(f"[Cache] Error: {e}")

def get_or_create_target_image(context, props):
    """获取用户指定的 Image，如果为空则创建默认图像"""
    img = props.target_image
    if img and img.size[0] > 0:
        return img
    # 创建默认贴图：使用配置的分辨率，RGB格式
    name = "LaserScanMap"
    if name in bpy.data.images:
        img = bpy.data.images[name]
        props.target_image = img
        return img
    
    # 使用配置的尺寸
    width = props.texture_width
    height = props.texture_height
    
    img = bpy.data.images.new(
        name=name,
        width=width,
        height=height,
        alpha=False,
        float_buffer=False  # 使用8位颜色，与原图一致
    )
    img.colorspace_settings.name = 'sRGB'
    img.pixels[:] = [0.0] * (width * height * 4)  # RGBA，全黑
    img.update()
    props.target_image = img
    return img

def update_texture_in_thread():
    """在独立线程中按频率读取并更新纹理"""
    global scan_running, exit_flag, process_counter, processed_count
    
    last_update_time = 0.0
    print("[Texture Thread] Started")
    
    while not exit_flag:
        if not scan_running:
            time.sleep(0.05)
            continue
        
        current_time = time.time()
        
        # 检查更新间隔
        if current_time - last_update_time < update_interval:
            time.sleep(0.01)
            continue
        
        # 查找下一个未处理的文件
        init_cache_directory()
        if cache_dir and os.path.exists(cache_dir):
            files = sorted([f for f in os.listdir(cache_dir) if f.endswith('.jpg')])
            
            if files:
                next_file = None
                for f in files:
                    try:
                        frame_num = int(f.replace('frame_', '').replace('.jpg', ''))
                        if frame_num > process_counter:
                            next_file = f
                            break
                    except:
                        continue
                
                if next_file:
                    filepath = os.path.join(cache_dir, next_file)
                    
                    # 直接从磁盘文件更新纹理
                    _do_update_texture(filepath)
                    processed_count += 1
                    process_counter += 1
                    
                    # 清理已处理的文件
                    if auto_cleanup:
                        cleanup_cache_file(filepath)
                    
                    last_update_time = time.time()
        
        time.sleep(0.01)

def _do_update_texture(filepath):
    """直接从磁盘文件更新纹理（后台线程）"""
    global target_image
    
    # 优先使用UI中的最新图像引用
    try:
        props = bpy.context.scene.laser_scanner_props
        img = props.target_image
    except:
        img = target_image
    
    if img is None or not os.path.exists(filepath):
        print(f"[Texture] Warning: No target image or file not found: {filepath}")
        return
    
    # 检查图像尺寸有效性
    if img.size[0] <= 0 or img.size[1] <= 0:
        print(f"[Texture] Error: Invalid image size {img.size}")
        return
    
    try:
        # 使用PIL从文件加载
        pil_img = Image.open(filepath)
        
        # 获取目标尺寸
        target_w, target_h = img.size[0], img.size[1]
        w, h = pil_img.size
        
        # 检查源图像尺寸合理性
        if w < 64 or h < 64:
            print(f"[Texture] Warning: Source image too small ({w}x{h}), skipping")
            return
        
        print(f"[Texture] Processing: {os.path.basename(filepath)} source={w}x{h}, target={target_w}x{target_h}")
        
        # 统一使用PIL进行缩放处理
        if w != target_w or h != target_h:
            pil_img = pil_img.resize((target_w, target_h), Image.BILINEAR)
            print(f"[Texture] Resized from {w}x{h} to {target_w}x{target_h}")
        
        # 转换为RGB并归一化到[0,1]
        rgb_array = np.array(pil_img.convert('RGB')).astype(np.float32) / 255.0
        
        # 转换为RGBA
        rgba = np.zeros((target_h, target_w, 4), dtype=np.float32)
        rgba[:, :, :3] = rgb_array
        rgba[:, :, 3] = 1.0
        
        # 在后台线程中直接更新像素
        img.pixels[:] = rgba.flatten()
        img.update()
        
        # 刷新视图
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        
        print(f"[Texture] Updated successfully")
        
    except Exception as e:
        import traceback
        print(f"[Texture] Error: {e}")
        traceback.print_exc()

def send_control_cmd(cmd):
    """发送控制命令"""
    global control_sock, ctrl_connected
    if not ctrl_connected or control_sock is None:
        return False
    try:
        control_sock.sendall((cmd + "\n").encode())
        return True
    except Exception as e:
        print(f"[CMD] Error: {e}")
        ctrl_connected = False
        control_sock = None
        return False

# ================= 网络接收线程 =================
def net_recv_thread():
    """接收图片并保存到磁盘"""
    global recv_running, exit_flag, data_connected, received_count
    
    print(f"[Net] Starting receiver thread for {board_ip}:{data_port}")
    
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    while recv_running and not exit_flag:
        try:
            print(f"[Net] Connecting to {board_ip}:{data_port}...")
            client_sock.connect((board_ip, data_port))
            client_sock.settimeout(1.0)
            data_connected = True
            print(f"[Net] Connected successfully")
            
            buf = b""
            while recv_running and not exit_flag:
                # 检查是否达到最大帧数限制
                if max_frames > 0 and received_count >= max_frames:
                    print(f"[Net] Reached max frames ({max_frames}), stopping")
                    recv_running = False
                    break
                
                try:
                    data = client_sock.recv(65536)
                    if not data:
                        print("[Net] Connection closed by server")
                        break
                    buf += data
                    
                    # 解析图片数据包
                    while len(buf) >= 4:
                        jpeg_len = struct.unpack(">I", buf[:4])[0]
                        
                        if jpeg_len > 0:
                            if len(buf) >= 4 + jpeg_len:
                                jpeg_bytes = buf[4:4+jpeg_len]
                                buf = buf[4+jpeg_len:]
                                
                                # 保存到磁盘
                                filepath = save_jpeg_to_disk(jpeg_bytes)
                                if filepath:
                                    received_count += 1
                                    if received_count % 10 == 0:  # 每10帧打印一次
                                        print(f"[Net] Received frame #{received_count}")
                            else:
                                break
                        else:
                            buf = buf[4:]
                            
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[Net] Error receiving data: {e}")
                    break
            
            data_connected = False
            client_sock.close()
            print("[Net] Disconnected, will retry in 2 seconds...")
            time.sleep(2)
            
        except socket.error as e:
            print(f"[Net] Connection error: {e}")
            data_connected = False
            time.sleep(2)
        except Exception as e:
            print(f"[Net] Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(2)
    
    try:
        client_sock.close()
    except:
        pass
    print("[Net] Receiver thread stopped")

# ================= Blender 定时器：保持界面刷新 =================
def status_update_timer():
    """简单定时器，用于刷新状态显示（可选）"""
    return 0.5

def sync_params_timer():
    """同步UI参数"""
    global distance_max, update_interval, max_frames, auto_cleanup, target_image
    props = bpy.context.scene.laser_scanner_props
    distance_max = props.distance_max
    update_interval = props.update_interval
    max_frames = props.max_frames
    auto_cleanup = props.auto_cleanup
    target_image = props.target_image
    return 1.0

# ================= 操作符 =================
# （保持之前的 SCAN_OT_connect_ctrl, disconnect_ctrl, send_cmd, start_scan, pause_scan, resume_scan, stop_scan, clear_texture 等）
# 注意：clear_cloud 改为 clear_texture，将贴图所有像素清零

class SCAN_OT_clear_texture(bpy.types.Operator):
    bl_idname = "scan.clear_texture"
    bl_label = "Clear Texture"

    def execute(self, context):
        props = context.scene.laser_scanner_props
        img = props.target_image
        if img:
            pixels = [0.0] * (img.size[0] * img.size[1] * 4)
            img.pixels = pixels
            img.update()
            self.report({'INFO'}, "Texture cleared")
        else:
            self.report({'WARNING'}, "No target texture selected")
        return {'FINISHED'}

class SCAN_OT_create_texture(bpy.types.Operator):
    bl_idname = "scan.create_texture"
    bl_label = "Create/Update Texture"
    bl_description = "Create a new texture or update existing one with configured dimensions"

    def execute(self, context):
        props = context.scene.laser_scanner_props
        
        # 如果已有贴图，检查尺寸是否需要更新
        if props.target_image:
            current_w, current_h = props.target_image.size[0], props.target_image.size[1]
            if current_w == props.texture_width and current_h == props.texture_height:
                self.report({'INFO'}, f"Texture already exists with correct size: {current_w}x{current_h}")
                return {'FINISHED'}
            else:
                # 删除旧贴图，创建新的
                old_img = props.target_image
                bpy.data.images.remove(old_img)
                props.target_image = None
        
        # 创建新贴图
        img = get_or_create_target_image(context, props)
        self.report({'INFO'}, f"Texture created: {img.size[0]}x{img.size[1]}")
        return {'FINISHED'}

# 以下是之前操作符，稍作调整（将 Clear Point Cloud 改为 Clear Texture）
class SCAN_OT_connect_ctrl(bpy.types.Operator):
    bl_idname = "scan.connect_ctrl"
    bl_label = "Connect to Board"
    
    def execute(self, context):
        global recv_running, exit_flag, ctrl_connected, control_sock
        props = context.scene.laser_scanner_props
        
        # 关闭旧连接
        if recv_running or ctrl_connected:
            recv_running = False
            exit_flag = True
            ctrl_connected = False
            if control_sock:
                try:
                    control_sock.close()
                except:
                    pass
                control_sock = None
            time.sleep(0.3)
        
        # 建立控制连接
        try:
            control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            control_sock.settimeout(5.0)
            control_sock.connect((props.board_ip, props.control_port))
            ctrl_connected = True
        except Exception as e:
            self.report({'ERROR'}, f"Connection failed: {e}")
            return {'CANCELLED'}
        
        # 启动接收和更新线程
        exit_flag = False
        recv_running = True
        threading.Thread(target=net_recv_thread, daemon=True).start()
        threading.Thread(target=update_texture_in_thread, daemon=True).start()
        
        self.report({'INFO'}, f"Connected! Ctrl:{props.control_port} Data:{props.data_port}")
        return {'FINISHED'}

class SCAN_OT_disconnect_ctrl(bpy.types.Operator):
    bl_idname = "scan.disconnect_ctrl"
    bl_label = "Disconnect"
    
    def execute(self, context):
        global recv_running, scan_running, exit_flag, ctrl_connected, control_sock
        global process_counter, frame_counter
        global received_count, processed_count
        
        scan_running = False
        recv_running = False
        exit_flag = True
        ctrl_connected = False
        
        if control_sock:
            try:
                control_sock.close()
            except:
                pass
            control_sock = None
        
        cleanup_all_cache()
        
        # 重置计数器
        process_counter = 0
        frame_counter = 0
        received_count = 0
        processed_count = 0
        
        self.report({'INFO'}, "Disconnected")
        return {'FINISHED'}

class SCAN_OT_send_cmd(bpy.types.Operator):
    bl_idname = "scan.send_cmd"
    bl_label = "Send Command"
    command: bpy.props.StringProperty(default="start")
    
    def execute(self, context):
        if send_control_cmd(self.command):
            self.report({'INFO'}, f"Command '{self.command}' sent")
        else:
            self.report({'ERROR'}, "Failed to send command")
        return {'FINISHED'}

class SCAN_OT_start_scan(bpy.types.Operator):
    bl_idname = "scan.start_scan"
    bl_label = "Start Scan"
    
    def execute(self, context):
        global recv_running, scan_running, exit_flag
        global received_count, processed_count, process_counter, frame_counter
        global target_image
        
        if not ctrl_connected:
            self.report({'ERROR'}, "Please connect first")
            return {'CANCELLED'}
        
        props = context.scene.laser_scanner_props
        
        # 确保目标图像存在且有效
        img = get_or_create_target_image(context, props)
        if img is None or img.size[0] <= 0 or img.size[1] <= 0:
            self.report({'ERROR'}, "Failed to create valid target image")
            return {'CANCELLED'}
        
        # 同步全局变量
        target_image = img
        
        print(f"[Scan] Starting with image: {img.name} ({img.size[0]}x{img.size[1]})")
        
        # 重置计数器
        received_count = 0
        processed_count = 0
        process_counter = 0
        frame_counter = 0
        
        # 清空缓存
        cleanup_all_cache()
        
        # 启动线程
        if not recv_running:
            exit_flag = False
            recv_running = True
            threading.Thread(target=net_recv_thread, daemon=True).start()
            threading.Thread(target=update_texture_in_thread, daemon=True).start()
        
        scan_running = True
        
        # 清空贴图
        SCAN_OT_clear_texture.execute(self, context)
        send_control_cmd("start")
        
        self.report({'INFO'}, "Scan started")
        return {'FINISHED'}

class SCAN_OT_stop_scan(bpy.types.Operator):
    bl_idname = "scan.stop_scan"
    bl_label = "Stop Scan"
    
    def execute(self, context):
        global scan_running, recv_running, exit_flag, process_counter
        
        scan_running = False
        recv_running = False
        exit_flag = True
        send_control_cmd("stop")
        
        process_counter = 0
        
        self.report({'INFO'}, "Scan stopped")
        return {'FINISHED'}

class SCAN_OT_set_exposure(bpy.types.Operator):
    bl_idname = "scan.set_exposure"
    bl_label = "Set Exposure"
    bl_description = "Set camera exposure time and gain"
    
    def execute(self, context):
        props = context.scene.laser_scanner_props
        
        if not ctrl_connected:
            self.report({'ERROR'}, "Please connect first")
            return {'CANCELLED'}
        
        exp_time = int(props.exposure_time)
        gain = int(props.exposure_gain)
        
        cmd = f"exp {exp_time} {gain}"
        print(f"[Exposure] Sending command: {cmd}")
        
        if send_control_cmd(cmd):
            self.report({'INFO'}, f"Exposure set: {exp_time}us, Gain: {gain}")
        else:
            self.report({'ERROR'}, "Failed to set exposure")
        
        return {'FINISHED'}

# ================= UI 面板 =================
def prop_aligned(layout, label, data, prop):
    row = layout.row(align=True)
    split = row.split(factor=0.5)
    split.label(text=label)
    split.prop(data, prop, text="")

class SCAN_PT_panel(bpy.types.Panel):
    bl_label = "Laser Scanner"
    bl_idname = "SCAN_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Laser Scanner"

    def draw(self, context):
        layout = self.layout
        props = context.scene.laser_scanner_props

        # 控制连接
        box = layout.box()
        box.label(text="Control Connection (Dual Port)")
        if ctrl_connected:
            box.label(text=f"Connected to {props.board_ip}")
            box.label(text=f"Ctrl:{props.control_port} Data:{props.data_port}")
            row = box.row(align=True)
            row.operator("scan.disconnect_ctrl", text="Disconnect")
            row.operator("scan.send_cmd", text="Reboot").command = "reboot"
        else:
            prop_aligned(box, "Board IP", props, "board_ip")
            prop_aligned(box, "Ctrl Port", props, "control_port")
            prop_aligned(box, "Data Port", props, "data_port")
            box.operator("scan.connect_ctrl", text="Connect")

        # 扫描控制
        box = layout.box()
        box.label(text="Scan Control")
        row = box.row(align=True)
        row.operator("scan.start_scan", text="Start Scan")
        row.operator("scan.stop_scan", text="Stop Scan")

        # 曝光控制
        box = layout.box()
        box.label(text="Exposure Control")
        prop_aligned(box, "Exposure Time (us)", props, "exposure_time")
        prop_aligned(box, "Gain", props, "exposure_gain")
        box.operator("scan.set_exposure", text="Set Exposure")

        # 目标贴图
        box = layout.box()
        box.label(text="Target Texture")
        prop_aligned(box, "Width", props, "texture_width")
        prop_aligned(box, "Height", props, "texture_height")
        prop_aligned(box, "Update Interval (s)", props, "update_interval")
        prop_aligned(box, "Max Frames", props, "max_frames")
        box.prop(props, "auto_cleanup", text="Auto Cleanup")
        box.prop(props, "target_image", text="")
        row = box.row(align=True)
        row.operator("scan.clear_texture", text="Clear Texture")
        row.operator("scan.create_texture", text="Create/Update Texture")
        if props.target_image:
            box.label(text=f"Size: {props.target_image.size[0]} x {props.target_image.size[1]}")

        # 状态信息
        box = layout.box()
        box.label(text="Status")
        box.label(text=f"Receiver: {'Running' if recv_running else 'Stopped'}")
        box.label(text=f"Connected: {'Yes' if ctrl_connected else 'No'}")
        box.label(text=f"Scanning: {'Active' if scan_running else 'Inactive'}")
        box.separator()
        box.label(text="Frame Statistics:")
        box.label(text=f"  Received: {received_count}")
        box.label(text=f"  Processed: {processed_count}")
        if received_count > 0:
            pending = received_count - processed_count
            box.label(text=f"  Pending: {pending}")

# ================= 属性组 =================
class LaserScannerProperties(bpy.types.PropertyGroup):
    board_ip: bpy.props.StringProperty(name="Board IP", default="172.32.0.93")
    control_port: bpy.props.IntProperty(name="Control Port", default=9000, min=1, max=65535)
    data_port: bpy.props.IntProperty(name="Data Port", default=9001, min=1, max=65535)
    target_image: bpy.props.PointerProperty(name="Output Texture", type=bpy.types.Image)
    texture_width: bpy.props.IntProperty(name="Width", default=640, min=64, max=4096)
    texture_height: bpy.props.IntProperty(name="Height", default=480, min=64, max=4096)
    update_interval: bpy.props.FloatProperty(name="Update Interval (s)", default=0.1, min=0.01, max=2.0, precision=2)
    max_frames: bpy.props.IntProperty(name="Max Frames (0=unlimited)", default=0, min=0, max=100000, description="Maximum frames to receive (0 = no limit)")
    auto_cleanup: bpy.props.BoolProperty(name="Auto Cleanup", default=True, description="Automatically delete processed images from disk")
    distance_max: bpy.props.FloatProperty(name="Max Distance (mm)", default=1000.0, min=1.0, max=5000.0)
    exposure_time: bpy.props.IntProperty(name="Exposure Time (us)", default=10000, min=100, max=1000000, description="Camera exposure time in microseconds")
    exposure_gain: bpy.props.IntProperty(name="Gain", default=1, min=1, max=100, description="Camera gain value")

# ================= 注册与注销 =================
classes = [
    LaserScannerProperties,
    SCAN_OT_connect_ctrl,
    SCAN_OT_disconnect_ctrl,
    SCAN_OT_send_cmd,
    SCAN_OT_start_scan,
    SCAN_OT_stop_scan,
    SCAN_OT_clear_texture,
    SCAN_OT_create_texture,
    SCAN_OT_set_exposure,
    SCAN_PT_panel,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.laser_scanner_props = bpy.props.PointerProperty(type=LaserScannerProperties)
    if not bpy.app.timers.is_registered(sync_params_timer):
        bpy.app.timers.register(sync_params_timer)

def unregister():
    global recv_running, exit_flag, control_sock
    recv_running = False
    exit_flag = True
    if control_sock:
        try:
            control_sock.close()
        except:
            pass
    # 清理所有缓存文件
    cleanup_all_cache()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.laser_scanner_props
    if bpy.app.timers.is_registered(sync_params_timer):
        bpy.app.timers.unregister(sync_params_timer)

if __name__ == "__main__":
    register()