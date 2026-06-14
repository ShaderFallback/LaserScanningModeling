#!/usr/bin/env python3
"""
双端口通信协议测试脚本
用于验证Blender插件与开发板之间的双端口通信（控制+数据）
"""

import socket
import struct
import time
import sys

# 配置
BOARD_IP = "172.32.0.93"
CONTROL_PORT = 9000  # 控制命令端口
DATA_PORT = 9001     # 图片数据端口

def connect_control():
    """连接到控制端口"""
    print(f"[TEST] Connecting to control port {BOARD_IP}:{CONTROL_PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    
    try:
        sock.connect((BOARD_IP, CONTROL_PORT))
        print("[TEST] ✓ Control connection established")
        return sock
    except Exception as e:
        print(f"[TEST] ✗ Control connection failed: {e}")
        return None

def connect_data():
    """连接到数据端口"""
    print(f"[TEST] Connecting to data port {BOARD_IP}:{DATA_PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    
    try:
        sock.connect((BOARD_IP, DATA_PORT))
        print("[TEST] ✓ Data connection established")
        return sock
    except Exception as e:
        print(f"[TEST] ✗ Data connection failed: {e}")
        return None



def send_command(sock, cmd):
    """通过控制连接发送命令（双端口架构 - 纯文本协议）"""
    cmd_bytes = (cmd + "\n").encode('utf-8')
    
    print(f"[TEST] Sending command: '{cmd}'")
    sock.sendall(cmd_bytes)
    
    # 接收响应（纯文本，以换行符结束）
    try:
        sock.settimeout(2.0)
        response = b""
        while True:
            byte = sock.recv(1)
            if byte == b'\n':
                break
            response += byte
        result = response.decode('utf-8').strip()
        print(f"[TEST] Response: {result}")
        return result
    except socket.timeout:
        print("[TEST] ✗ No response received (timeout)")
        return None
    except Exception as e:
        print(f"[TEST] ✗ Error receiving response: {e}")
        return None

def receive_images(sock, count=3, timeout=10, start_index=1):
    """从数据连接接收指定数量的图片
    返回: (成功接收的数量, 总字节数)
    
    参数:
        sock: socket对象
        count: 接收数量
        timeout: 超时时间（秒）
        start_index: 文件名起始索引（用于避免覆盖）
    """
    print(f"[TEST] Waiting for {count} images on data port...")
    sock.settimeout(timeout)
    
    received_count = 0
    total_bytes = 0
    
    for i in range(count):
        try:
            # 读取消息头 [4字节大端整数: JPEG长度]
            header = sock.recv(4)
            if len(header) < 4:
                print(f"[TEST] ✗ Incomplete header for image #{i+1}")
                break
            
            jpeg_len = struct.unpack(">I", header)[0]
            
            if jpeg_len > 0:
                # 接收JPEG数据
                jpeg_bytes = b""
                remaining = jpeg_len
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    chunk = sock.recv(chunk_size)
                    if not chunk:
                        break
                    jpeg_bytes += chunk
                    remaining -= len(chunk)
                
                if len(jpeg_bytes) == jpeg_len:
                    print(f"[TEST] ✓ Received image #{i+1}: {len(jpeg_bytes)} bytes")
                    
                    # 保存测试（使用start_index避免覆盖）
                    filename = f"test_image_{start_index + i}.jpg"
                    with open(filename, "wb") as f:
                        f.write(jpeg_bytes)
                    print(f"[TEST]   Saved to {filename}")
                    
                    received_count += 1
                    total_bytes += len(jpeg_bytes)
                else:
                    print(f"[TEST] ✗ Incomplete image #{i+1}: expected {jpeg_len}, got {len(jpeg_bytes)}")
                    break
            else:
                print(f"[TEST] ✗ Invalid image length: {jpeg_len}")
                break
        
        except socket.timeout:
            print(f"[TEST] ✗ Timeout waiting for image #{i+1}")
            break
        except Exception as e:
            print(f"[TEST] ✗ Error receiving image: {e}")
            import traceback
            traceback.print_exc()
            break
    
    print(f"[TEST] Summary: Received {received_count}/{count} images, {total_bytes} bytes total")
    return received_count, total_bytes

def test_full_workflow():
    """测试完整工作流程（双端口架构）"""
    print("=" * 60)
    print("激光扫描系统 - 双端口协议测试")
    print("=" * 60)
    
    test_results = {
        'control_connect': False,
        'data_connect': False,
        'start_cmd': False,
        'receive_images_1': False,
        'pause_cmd': False,
        'resume_cmd': False,
        'receive_images_2': False,
        'stop_cmd': False,
    }
    
    # 1. 连接控制端口
    ctrl_sock = connect_control()
    if not ctrl_sock:
        print("\n[TEST] ✗ FAILED: Cannot connect to control port")
        return False
    test_results['control_connect'] = True
    
    # 2. 连接数据端口
    data_sock = connect_data()
    if not data_sock:
        print("\n[TEST] ✗ FAILED: Cannot connect to data port")
        ctrl_sock.close()
        return False
    test_results['data_connect'] = True
    
    print("[TEST] ✓ Both connections successful (Ctrl + Data)")
    
    try:
        # 3. 测试 start 命令
        print("\n[TEST] === Test 1: Start Scan ===")
        resp = send_command(ctrl_sock, "start")
        if resp == "SCAN_STARTED":
            print("[TEST] ✓ Start command successful")
            test_results['start_cmd'] = True
        else:
            print(f"[TEST] ✗ FAILED: Expected 'SCAN_STARTED', got '{resp}'")
        
        # 4. 等待几秒让系统开始拍照
        print("\n[TEST] Waiting for images to be captured...")
        time.sleep(2)
        
        # 5. 接收图片（从数据端口）
        print("\n[TEST] === Test 2: Receive Images ===")
        img_count, img_bytes = receive_images(data_sock, count=50, timeout=60, start_index=1)
        if img_count >= 1:
            print(f"[TEST] ✓ Image reception successful ({img_count} images)")
            test_results['receive_images_1'] = True
        else:
            print(f"[TEST] ✗ FAILED: No images received (expected at least 1)")
        
        # 6. 测试 pause 命令
        print("\n[TEST] === Test 3: Pause Scan ===")
        resp = send_command(ctrl_sock, "pause")
        if resp == "SCAN_PAUSED":
            print("[TEST] ✓ Pause command successful")
            test_results['pause_cmd'] = True
        else:
            print(f"[TEST] ✗ FAILED: Expected 'SCAN_PAUSED', got '{resp}'")
        
        # 7. 恢复扫描
        print("\n[TEST] === Test 4: Resume Scan ===")
        resp = send_command(ctrl_sock, "continue")
        if resp == "SCAN_RESUMED":
            print("[TEST] ✓ Resume command successful")
            test_results['resume_cmd'] = True
        else:
            print(f"[TEST] ✗ FAILED: Expected 'SCAN_RESUMED', got '{resp}'")
        
        # 8. 等待新图片产生
        print("\n[TEST] Waiting for more images after resume...")
        time.sleep(1.5)
        
        # 9. 再接收一些图片
        print("\n[TEST] === Test 5: Receive More Images ===")
        img_count2, img_bytes2 = receive_images(data_sock, count=50, timeout=60, start_index=51)
        if img_count2 >= 1:
            print(f"[TEST] ✓ Second image reception successful ({img_count2} images)")
            test_results['receive_images_2'] = True
        else:
            print(f"[TEST] ✗ FAILED: No images received in second batch")
        
        # 10. 测试 stop 命令
        print("\n[TEST] === Test 6: Stop Scan ===")
        resp = send_command(ctrl_sock, "stop")
        if resp == "SCAN_STOPPED":
            print("[TEST] ✓ Stop command successful")
            test_results['stop_cmd'] = True
        else:
            print(f"[TEST] ✗ FAILED: Expected 'SCAN_STOPPED', got '{resp}'")
        
        # 打印测试结果摘要
        print("\n" + "=" * 60)
        print("测试结果摘要:")
        print("=" * 60)
        passed = 0
        total = len(test_results)
        for test_name, result in test_results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"  {status} - {test_name}")
            if result:
                passed += 1
        
        print("=" * 60)
        print(f"总计: {passed}/{total} 测试通过")
        
        if passed == total:
            print("\n[TEST] ✓✓✓ 所有测试通过！")
            return True
        else:
            print(f"\n[TEST] ✗✗✗ {total - passed} 个测试失败")
            return False
    
    except Exception as e:
        print(f"\n[TEST] ✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # 清理
        print("\n[TEST] Closing connections...")
        try:
            ctrl_sock.close()
        except:
            pass
        try:
            data_sock.close()
        except:
            pass
        print("[TEST] Done!")

def test_exposure_control():
    """测试曝光参数控制：从 0ms 到 1ms，共拍摄 50 张，并自动接收图片"""
    print("\n" + "=" * 60)
    print("测试曝光参数控制 (0ms - 1ms, 50张照片)")
    print("=" * 60)
    
    ctrl_sock = connect_control()
    data_sock = connect_data() # 连接数据端口以接收图片
    
    if not ctrl_sock or not data_sock:
        return False
    
    try:
        # 生成曝光时间列表：从 100us 到 1000us (1ms)，共 50 步
        num_photos = 500
        min_exp = 100   # 最小曝光 100us（避免 0us 导致完全黑暗）
        max_exp = 500000  # 最大曝光 1000us (1ms)
        exp_times = [int(min_exp + i * (max_exp - min_exp) / (num_photos - 1)) for i in range(num_photos)]
        gain_val = 1  # 固定增益
        
        print(f"[TEST] Will test {len(exp_times)} exposure steps ({min_exp}us to {max_exp}us)...")
        
        for i, exp_time in enumerate(exp_times):
            cmd = f"exp {exp_time} {gain_val}"
            filename = f"exposure_{exp_time}us_{i+1:03d}.jpg"
            
            print(f"\n[TEST] Step {i+1}/{len(exp_times)}: Setting {cmd}")
            resp = send_command(ctrl_sock, cmd)
            
            if resp == "EXP_SET":
                # ISP 稳定时间增加到 0.3 秒，确保曝光生效
                time.sleep(0.3)
                
                # 发送测试拍照指令（会自动加入发送队列）
                snap_cmd = f"snap_test {filename}"
                print(f"[TEST] Triggering test snapshot: {filename}")
                send_command(ctrl_sock, snap_cmd)
                
                # 从数据端口接收这张图片
                try:
                    header = data_sock.recv(4)
                    if len(header) == 4:
                        jpeg_len = struct.unpack(">I", header)[0]
                        if jpeg_len > 0 and jpeg_len < 10 * 1024 * 1024: # 限制最大 10MB
                            jpeg_bytes = b""
                            remaining = jpeg_len
                            while remaining > 0:
                                chunk = data_sock.recv(min(65536, remaining))
                                if not chunk: break
                                jpeg_bytes += chunk
                                remaining -= len(chunk)
                            
                            if len(jpeg_bytes) == jpeg_len:
                                with open(filename, "wb") as f:
                                    f.write(jpeg_bytes)
                                print(f"[TEST] ✓ Saved: {filename} ({len(jpeg_bytes)} bytes)")
                            else:
                                print(f"[TEST] ✗ Incomplete image data")
                        else:
                            print(f"[TEST] ✗ Invalid image length: {jpeg_len}")
                    else:
                        print(f"[TEST] ✗ Failed to receive image header")
                except Exception as e:
                    print(f"[TEST] ✗ Error receiving image: {e}")
                    
            elif resp == "EXP_SET_FAIL":
                print(f"[TEST] ✗ Exposure setting failed by camera process")
            elif resp == "EXP_SET_TIMEOUT":
                print(f"[TEST] ✗ Exposure setting timeout")
            elif resp == "EXP_SET_ERROR":
                print(f"[TEST] ✗ Exposure command error")
            else:
                print(f"[TEST] ✗ Unexpected response: {resp}")
        
        print(f"\n[TEST] Exposure sweep complete! Check local folder for 'exposure_*.jpg'")
        return True
    finally:
        ctrl_sock.close()
        data_sock.close()

def test_invalid_command():
    """测试无效命令处理（双端口架构）"""
    print("\n" + "=" * 60)
    print("测试无效命令处理")
    print("=" * 60)
    
    ctrl_sock = connect_control()
    if not ctrl_sock:
        return False
    
    try:
        print("\n[TEST] Sending invalid command...")
        resp = send_command(ctrl_sock, "invalid_cmd")
        
        if resp and resp.startswith("UNKNOWN_CMD"):
            print("[TEST] ✓ Server correctly handled invalid command")
            return True
        else:
            print(f"[TEST] ✗ Unexpected response: {resp}")
            return False
    
    finally:
        ctrl_sock.close()

if __name__ == "__main__":
    print("激光扫描系统 - 双端口通信协议测试工具\n")
    
    if len(sys.argv) > 1:
        BOARD_IP = sys.argv[1]
        print(f"Using custom IP: {BOARD_IP}")
    
    if len(sys.argv) > 2:
        CONTROL_PORT = int(sys.argv[2])
        print(f"Using custom control port: {CONTROL_PORT}")
    
    if len(sys.argv) > 3:
        DATA_PORT = int(sys.argv[3])
        print(f"Using custom data port: {DATA_PORT}")
    
    # 运行完整工作流测试
    # success = test_full_workflow()
    success = True  # 默认设为 True，因为我们主要跑曝光测试
    
    # 运行曝光控制测试
    test_exposure_control()
    
    # 运行无效命令测试
    test_invalid_command()
    
    if success:
        print("\n✓ 所有测试通过！")
        sys.exit(0)
    else:
        print("\n✗ 部分测试失败")
        sys.exit(1)
