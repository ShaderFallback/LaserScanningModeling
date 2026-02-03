import os
import socket
import threading
import time
import queue
import subprocess
from periphery import GPIO

# 配置
HOST = "127.0.0.1"
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
    camera.stdin.write(f"snap {name}\n")
    camera.stdin.flush()


# Socket 接收线程
def socket_server():
    print("Server Listen....")
    global running, paused, exit_flag

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((HOST, PORT))
    srv.listen(1)

    conn, addr = srv.accept()
    print("Connected from", addr)

    while not exit_flag:
        data = conn.recv(1024)
        if not data:
            break

        cmd = data.decode().strip().lower()
        print("recv cmd:", cmd)

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

    conn.close()
    srv.close()


# 拍照生产线程
def photo_producer():
    idx = 1

    while not exit_flag:
        if not running or paused:
            time.sleep(1)
            continue

        rotate_forward(ROTATE_STEPS)

        photo_name = f"photo_{idx}.jpg"
        snap_photo(photo_name)

        # 队列满会阻塞 → 自动暂停拍照
        photo_queue.put(photo_name)

        idx += 1
        if idx > PHOTO_QUEUE_SIZE:
            idx = 1

# Socket 发送线程
def socket_sender():
    while not running:
        try:
            cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            cli.connect(("127.32.0.100", PORT))

            while not exit_flag:
                try:
                    photo = photo_queue.get(timeout=1)
                except queue.Empty:
                    continue

                cli.sendall((photo + "\n").encode())
                photo_queue.task_done()
            cli.close()
        except Exception:
            pass
        time.sleep(1)
    
# 释放资源,重启系统
def system_cleanup_and_reboot():
    print("cleanup before reboot")

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


# 主入口
if __name__ == "__main__":
    try:
        t1 = threading.Thread(target=socket_server, daemon=True)
        t2 = threading.Thread(target=photo_producer, daemon=True)
        t3 = threading.Thread(target=socket_sender, daemon=True)

        t1.start()
        t2.start()
        t3.start()

        while not exit_flag:
            time.sleep(1)

    finally:
        system_cleanup_and_reboot()

