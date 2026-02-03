import socket
import threading
import queue
import time
import struct

import bpy
import numpy as np
import OpenImageIO as oiio

# =========================
# 网络配置
# =========================
SERVER_IP = "172.32.0.93"
SERVER_PORT = 9000

# =========================
# 队列配置
# =========================
PHOTO_QUEUE_MAX = 20
RESUME_THRESHOLD = PHOTO_QUEUE_MAX // 2

photo_queue = queue.Queue(maxsize=PHOTO_QUEUE_MAX)

paused_remote = False
lock = threading.Lock()

OUT_IMAGE_NAME = "EdgeResult"

# =========================
# Socket 接收线程（接收 JPEG 字节）
# =========================
def socket_recv_thread(sock):
    global paused_remote

    buf = b""

    while True:
        data = sock.recv(65536)
        if not data:
            print("[NET] connection closed")
            break

        buf += data

        while True:
            if len(buf) < 4:
                break

            jpeg_size = struct.unpack(">I", buf[:4])[0]
            if len(buf) < 4 + jpeg_size:
                break

            jpeg_bytes = buf[4:4 + jpeg_size]
            buf = buf[4 + jpeg_size:]

            # 队列满 -> pause
            with lock:
                if photo_queue.full() and not paused_remote:
                    sock.sendall(b"pause\n")
                    paused_remote = True
                    print("[CTRL] send pause")

            photo_queue.put(jpeg_bytes)

            # 队列回落 -> continue
            with lock:
                if paused_remote and photo_queue.qsize() <= RESUME_THRESHOLD:
                    sock.sendall(b"continue\n")
                    paused_remote = False
                    print("[CTRL] send continue")

# =========================
# 图像处理线程（JPEG → 像素）
# =========================
def image_process_thread():
    while True:
        jpeg_bytes = photo_queue.get()

        t0 = time.perf_counter()

        # =========================
        # JPEG 内存解码
        # =========================
        buf = oiio.ImageBuf(jpeg_bytes)
        pixels = buf.get_pixels(oiio.FLOAT)

        pixels = pixels[..., :3]

        # =========================
        # 去色
        # =========================
        gray = (
            pixels[..., 0] * 0.587 +
            pixels[..., 1] * 0.299 +
            pixels[..., 2] * 0.114
        )
        pixels = np.stack((gray, gray, gray), axis=-1)

        # =========================
        # 色阶
        # =========================
        IN_BLACK  = 0.03
        IN_WHITE  = 0.97
        GAMMA     = 0.1

        pixels = np.clip(pixels, IN_BLACK, IN_WHITE)
        pixels = (pixels - IN_BLACK) / (IN_WHITE - IN_BLACK)
        pixels = pixels ** (1.0 / GAMMA)
        pixels = np.clip(pixels, 0.0, 1.0)

        # =========================
        # 简单去色
        # =========================
        gray = pixels.mean(axis=-1)
        pixels = np.stack((gray, gray, gray), axis=-1)

        t1 = time.perf_counter()
        print(f"[PROC] cost {(t1 - t0) * 1000:.1f} ms")

        # =========================
        # 写回 Blender
        # =========================
        h, w = pixels.shape[:2]
        out_np = np.zeros((h, w, 4), dtype=np.float32)
        out_np[..., :3] = pixels
        out_np[..., 3] = 1.0

        if OUT_IMAGE_NAME in bpy.data.images:
            img = bpy.data.images[OUT_IMAGE_NAME]
            if img.size[0] != w or img.size[1] != h:
                bpy.data.images.remove(img)
                img = None

        if OUT_IMAGE_NAME not in bpy.data.images:
            img = bpy.data.images.new(
                OUT_IMAGE_NAME,
                width=w,
                height=h,
                alpha=True,
                float_buffer=True
            )

        img.pixels.foreach_set(out_np.ravel())
        img.update()

        photo_queue.task_done()

# =========================
# 主入口
# =========================
def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_IP, SERVER_PORT))

    sock.sendall(b"start\n")

    threading.Thread(
        target=socket_recv_thread,
        args=(sock,),
        daemon=True
    ).start()

    threading.Thread(
        target=image_process_thread,
        daemon=True
    ).start()

    print("[MAIN] controller running")

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
