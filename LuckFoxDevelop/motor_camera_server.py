import os
import sys
import socket
import threading
import time
import queue
import subprocess
from periphery import GPIO

#sys.stdout.flush()

# 配置
HOST = "0.0.0.0"
PORT = 9000

PHOTO_QUEUE_SIZE = 20
STEP_DELAY = 0.005
ROTATE_STEPS = 1

PIN_IN1 = 55
PIN_IN2 = 56
PIN_IN3 = 57
PIN_IN4 = 58

STEP_SEQUENCE = [
    [1, 0, 0, 0],
    [0, 1, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1],
]

# 全局状态
running = False     # 是否允许拍照
paused = False      # 是否暂停
exit_flag = False    # 仅 reboot 使用

photo_queue = queue.Queue(maxsize=PHOTO_QUEUE_SIZE)

# 步进电机
pins = [
    GPIO(PIN_IN1, "out"),
    GPIO(PIN_IN2, "out"),
    GPIO(PIN_IN3, "out"),
    GPIO(PIN_IN4, "out"),
]

def set_step(w1, w2, w3, w4):
    pins[0].write(bool(w1))
    pins[1].write(bool(w2))
    pins[2].write(bool(w3))
    pins[3].write(bool(w4))

def rotate_forward(steps):
    for _ in range(steps):
        for seq in STEP_SEQUENCE:
            set_step(*seq)
            time.sleep(STEP_DELAY)

def motor_cleanup():
    set_step(0, 0, 0, 0)
    for p in pins:
        p.close()


# 停止RTSP流,防止占用相机资源
subprocess.run(
    ["/etc/init.d/S21appinit", "stop"],
    cwd="/etc/init.d",
    check=True
)

time.sleep(3)

# 拍照进程
camera = subprocess.Popen(
    ["./simple_vi_bind_venc_jpeg"],
    cwd="/root",
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

def snap_photo(name):
    try:
        camera.stdin.write(f"snap {name}\n")
        camera.stdin.flush()
    except Exception as e:
        print("[PythonCameraServer] Camera write error:", e, flush=True)

# Socket 接收线程
def socket_server():
    global running, paused, exit_flag

    print("[PythonCameraServer] RecvSocket Thread start!", flush=True)

    while not exit_flag:

        srv = None
        conn = None

        try:
            # =========================
            # 创建监听
            # =========================
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((HOST, PORT))
            srv.listen(1)

            print("[PythonCameraServer] Server Listen....", flush=True)

            # =========================
            # 等待客户端
            # =========================
            conn, addr = srv.accept()
            print("[PythonCameraServer] Connected from", addr, flush=True)

            # =========================
            # 接收循环
            # =========================
            while not exit_flag:

                data = conn.recv(1024)

                if not data:
                    print("[PythonCameraServer] Client disconnected", flush=True)
                    break

                cmd = data.decode(errors="ignore").strip().lower()
                print("[PythonCameraServer] recv cmd:", cmd, flush=True)

                if cmd == "start":
                    running = True
                    paused = False

                elif cmd == "pause":
                    paused = True

                elif cmd == "continue":
                    paused = False
                    running = True

                elif cmd == "reboot":
                    exit_flag = True
                    break

                else:
                    print(f"[PythonCameraServer] Cmd Error:{cmd}", flush=True)

        except Exception as e:
            print("[PythonCameraServer] Socket error:{e}",flush=True)

        finally:
            # =========================
            # 清理资源
            # =========================
            try:
                if conn:
                    conn.close()
            except:
                pass

            try:
                if srv:
                    srv.close()
            except:
                pass

            if not exit_flag:
                print("[PythonCameraServer] Restart listening...", flush=True)
                time.sleep(1)


# 拍照生产线程
def photo_producer():
    idx = 1
    print("[PythonCameraServer] PhotoProducer thread tart!")

    while not exit_flag:
        if not running or paused:
            time.sleep(1)
            continue

        rotate_forward(ROTATE_STEPS)

        photo_name = f"photo_{idx}.jpg"
        snap_photo(photo_name)
        print(f"[PythonCameraServer] SnapPhoto: {photo_name}", flush=True)
        # 队列满会阻塞 → 自动暂停拍照
        photo_queue.put(photo_name)

        idx += 1
        if idx > PHOTO_QUEUE_SIZE:
            idx = 1

# Socket 发送线程
def socket_sender():
    print(f"[PythonCameraServer] socketSender thread tart!", flush=True)

    while not exit_flag and running:
        try:
            cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            cli.connect(("127.32.0.100", PORT))
            
            print(f"[PythonCameraServer] Send Socket Connect!", flush=True)
            
            while not exit_flag:
                try:
                    photo = photo_queue.get(timeout=1)
                except queue.Empty:
                    continue

                cli.sendall((photo + "\n").encode())
                photo_queue.task_done()
                print("[PythonCameraServer] SendDone!", flush=True)

            cli.close()
        except Exception:
            pass
        time.sleep(1)
    
# 释放资源,重启系统
def system_cleanup_and_reboot():
    print("[PythonCameraServer] CleanupBeforeReboot")

    try:
        camera.stdin.write("quit\n")
        camera.stdin.flush()
        camera.terminate()
    except Exception:
        pass

    try:
        motor_cleanup()
    except Exception:
        pass

    time.sleep(1)
    #os.system("reboot")
    
def PrintTest():
    count = 0
    while True:
        print(f"[PythonCameraServer]{count}",flush=True)
        time.sleep(1)
        count += 1

# 主入口
if __name__ == "__main__":
    try:
        t1 = threading.Thread(target=socket_server)
        t2 = threading.Thread(target=photo_producer)
        t3 = threading.Thread(target=socket_sender)
        #t4 = threading.Thread(target=PrintTest)
        
        t1.start()
        t2.start()
        t3.start()
        #t4.start()
        
        while not exit_flag:
            time.sleep(1)
    finally:
        system_cleanup_and_reboot()

