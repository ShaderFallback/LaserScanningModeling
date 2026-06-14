#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/poll.h>
#include <unistd.h>
#include <stdatomic.h>
#include <time.h>

#include "rk_debug.h"
#include "rk_defines.h"
#include "rk_mpi_adec.h"
#include "rk_mpi_aenc.h"
#include "rk_mpi_ai.h"
#include "rk_mpi_ao.h"
#include "rk_mpi_avs.h"
#include "rk_mpi_cal.h"
#include "rk_mpi_ivs.h"
#include "rk_mpi_mb.h"
#include "rk_mpi_rgn.h"
#include "rk_mpi_sys.h"
#include "rk_mpi_tde.h"
#include "rk_mpi_vdec.h"
#include "rk_mpi_venc.h"
#include "rk_mpi_vi.h"
#include "rk_mpi_vo.h"
#include "rk_mpi_vpss.h"
// RkAiq uAPI2 Headers for RV1106
#include "rk_aiq_user_api2_sysctl.h"
#include "rk_aiq_user_api2_ae.h"

static bool quit = false;
static rk_aiq_sys_ctx_t *g_aiq_ctx = NULL; // 全局 AIQ 上下文
// 保持在 /tmp 目录，去掉末尾多余的斜杠，防止后面拼接出双斜杠
static RK_CHAR *g_pOutPath = "/tmp";
static atomic_int g_take_picture = 0;
#define SNAP_NAME_MAX 128
static char g_snap_name[SNAP_NAME_MAX] = {0};
static pthread_mutex_t g_snap_lock = PTHREAD_MUTEX_INITIALIZER;

static void sigterm_handler(int sig) {
    fprintf(stderr, "signal %d\n", sig);
    quit = true;
}

static RK_U64 TEST_COMM_GetNowUs() {
    struct timespec time = {0, 0};
    clock_gettime(CLOCK_MONOTONIC, &time);
    return (RK_U64)time.tv_sec * 1000000 +
           (RK_U64)time.tv_nsec / 1000;
}

/* ===========================
 * VENC 取流线程（精准修复地址获取与输出同步）
 * =========================== */
static void *GetMediaBuffer0(void *arg) {
    (void)arg;
    void *pData = RK_NULL;
    int s32Ret;
    char jpeg_path[256];
    char local_name[SNAP_NAME_MAX];

    VENC_STREAM_S stFrame;
    stFrame.pstPack = malloc(sizeof(VENC_PACK_S));

    while (!quit) {
        s32Ret = RK_MPI_VENC_GetStream(0, &stFrame, 500);
        if (s32Ret != RK_SUCCESS)
            continue;

        if (atomic_exchange(&g_take_picture, 0) == 1) {
            pthread_mutex_lock(&g_snap_lock);
            strncpy(local_name, g_snap_name, SNAP_NAME_MAX - 1);
            snprintf(jpeg_path, sizeof(jpeg_path), "%s/%s", g_pOutPath, g_snap_name);
            pthread_mutex_unlock(&g_snap_lock);

            FILE *fp = fopen(jpeg_path, "wb"); 
            if (fp) {
                // 【修复】恢复原版通过系统标准函数将句柄转为虚拟内存地址的写法
                pData = RK_MPI_MB_Handle2VirAddr(stFrame.pstPack->pMbBlk);
                
                fwrite(pData, 1, stFrame.pstPack->u32Len, fp);
                fclose(fp);

                RK_LOGI("Saved jpeg: %s", jpeg_path);
                
                // 【关键同步】向标准输出打印标识，通知 Python 线程落盘已完成
                printf("[DONE] %s\n", local_name);
                fflush(stdout);
            } else {
                RK_LOGE("Failed to open %s", jpeg_path);
                printf("[ERROR] Failed to open %s\n", local_name);
                fflush(stdout);
            }
        }

        RK_MPI_VENC_ReleaseStream(0, &stFrame);
    }

    free(stFrame.pstPack);
    return NULL;
}


/* ===========================
 * VENC 初始化 (还原原版标准初始化，消除不兼容的帧率字段报错)
 * =========================== */
static RK_S32 test_venc_init(int chnId,
                             int width,
                             int height,
                             RK_CODEC_ID_E enType) {
    VENC_CHN_ATTR_S stAttr;
    VENC_RECV_PIC_PARAM_S stRecvParam;

    memset(&stAttr, 0, sizeof(stAttr));

    stAttr.stVencAttr.enType = enType;
    stAttr.stVencAttr.enPixelFormat = RK_FMT_YUV420SP;
    stAttr.stVencAttr.u32PicWidth = width;
    stAttr.stVencAttr.u32PicHeight = height;
    stAttr.stVencAttr.u32VirWidth = width;
    stAttr.stVencAttr.u32VirHeight = height;
    stAttr.stVencAttr.u32StreamBufCnt = 2;
    stAttr.stVencAttr.u32BufSize = width * height * 3 / 2;

    stAttr.stVencAttr.stAttrJpege.enReceiveMode = VENC_PIC_RECEIVE_SINGLE;

    RK_MPI_VENC_CreateChn(chnId, &stAttr);

    //设置 JPEG 最高质量,减少颜色失真
    if (enType == RK_VIDEO_ID_JPEG) {
        VENC_RC_PARAM_S stRcParam;
        memset(&stRcParam, 0, sizeof(stRcParam));
        stRcParam.s32FirstFrameStartQp = -1;  // 使用默认起始QP
        stRcParam.stParamMjpeg.u32Qfactor = 99;      // JPEG 质量因子 (1-99)
        stRcParam.stParamMjpeg.u32MaxQfactor = 99;   // 最大质量因子
        stRcParam.stParamMjpeg.u32MinQfactor = 99;   // 最小质量因子
        
        RK_S32 ret = RK_MPI_VENC_SetRcParam(chnId, &stRcParam);
        if (ret != RK_SUCCESS) {
            RK_LOGE("Failed to set JPEG quality parameter: %d", ret);
        } else {
            RK_LOGI("JPEG quality set to: 99 (highest)");
        }
    }

    memset(&stRecvParam, 0, sizeof(stRecvParam));
    stRecvParam.s32RecvPicNum = 1;
    RK_MPI_VENC_StartRecvFrame(chnId, &stRecvParam);

    return 0;
}

/* ===========================
 * VI 设备初始化（保持原版原样）
 * =========================== */
int vi_dev_init() {
    int devId = 0;
    int pipeId = devId;
    VI_DEV_ATTR_S stDevAttr;
    VI_DEV_BIND_PIPE_S stBindPipe;

    memset(&stDevAttr, 0, sizeof(stDevAttr));
    memset(&stBindPipe, 0, sizeof(stBindPipe));

    if (RK_MPI_VI_GetDevAttr(devId, &stDevAttr) == RK_ERR_VI_NOT_CONFIG) {
        RK_MPI_VI_SetDevAttr(devId, &stDevAttr);
    }

    if (RK_MPI_VI_GetDevIsEnable(devId) != RK_SUCCESS) {
        RK_MPI_VI_EnableDev(devId);
        stBindPipe.u32Num = 1;
        stBindPipe.PipeId[0] = pipeId;
        RK_MPI_VI_SetDevBindPipe(devId, &stBindPipe);
    }
    return 0;
}

/* ===========================
 * VI 通道初始化（保持原版原样）
 * =========================== */
int vi_chn_init(int channelId, int width, int height) {
	int ret;
	int buf_cnt = 2;
	VI_CHN_ATTR_S vi_chn_attr;
	memset(&vi_chn_attr, 0, sizeof(vi_chn_attr));
	vi_chn_attr.stIspOpt.u32BufCount = buf_cnt;
	vi_chn_attr.stIspOpt.enMemoryType = VI_V4L2_MEMORY_TYPE_DMABUF; 
	vi_chn_attr.stSize.u32Width = width;
	vi_chn_attr.stSize.u32Height = height;
	vi_chn_attr.enPixelFormat = RK_FMT_YUV420SP;
	vi_chn_attr.enCompressMode = COMPRESS_MODE_NONE; 
	vi_chn_attr.u32Depth = 0; 
	ret = RK_MPI_VI_SetChnAttr(0, channelId, &vi_chn_attr);
	ret |= RK_MPI_VI_EnableChn(0, channelId);
	if (ret) {
		printf("ERROR: create VI error! ret=%d\n", ret);
		return ret;
	}

	return ret;
}

/* ===========================
 * ISP Callbacks (参考 simple_vi_get_frame_rkaiq.c)
 * =========================== */
static XCamReturn SIMPLE_COMM_ISP_SofCb(rk_aiq_metas_t *meta) {
    // SOF (Start of Frame) callback
    return XCAM_RETURN_NO_ERROR;
}

static XCamReturn SIMPLE_COMM_ISP_ErrCb(rk_aiq_err_msg_t *msg) {
    // Error callback
    if (msg->err_code == XCAM_RETURN_BYPASS) {
        printf("[ERROR] AIQ error, bypass mode\n");
        fflush(stdout);
    }
    return XCAM_RETURN_NO_ERROR;
}

/* ===========================
 * 初始化 ISP 并加载 IQ 文件 (使用 uAPI2 - 参考官方示例)
 * =========================== */
static int init_isp_with_iq(const char *iq_dir) {
    RK_S32 ret;
    rk_aiq_static_info_t static_info;

    printf("[DEBUG] Step 1: Enumerating sensor static metas...\n");
    fflush(stdout);
    
    // 1. 枚举传感器静态信息 (uAPI2)
    ret = rk_aiq_uapi2_sysctl_enumStaticMetas(0, &static_info);
    if (ret != 0) {
        RK_LOGE("Enum static metas failed: %d", ret);
        printf("[ERROR] Enum static metas failed: %d\n", ret);
        fflush(stdout);
        return -1;
    }
    
    printf("[DEBUG] Sensor name: %s\n", static_info.sensor_info.sensor_name);
    printf("[DEBUG] IQ dir: %s\n", iq_dir);
    fflush(stdout);

    printf("[DEBUG] Step 2: Initializing AIQ sysctl (uAPI2)...\n");
    fflush(stdout);
    
    // 2. 初始化 AIQ sysctl (uAPI2 - 带回调)
    g_aiq_ctx = rk_aiq_uapi2_sysctl_init(
        static_info.sensor_info.sensor_name,
        iq_dir,
        SIMPLE_COMM_ISP_ErrCb,  // Error callback
        SIMPLE_COMM_ISP_SofCb   // SOF callback
    );
    
    if (!g_aiq_ctx) {
        RK_LOGE("AIQ sysctl init failed");
        printf("[ERROR] AIQ sysctl init failed!\n");
        fflush(stdout);
        return -1;
    }
    printf("[DEBUG] AIQ context created: %p\n", (void*)g_aiq_ctx);
    fflush(stdout);

    printf("[DEBUG] Step 3: Preparing AIQ engine...\n");
    fflush(stdout);
    
    // 3. 准备 AIQ 引擎
    ret = rk_aiq_uapi2_sysctl_prepare(g_aiq_ctx, 0, 0, RK_AIQ_WORKING_MODE_NORMAL);
    if (ret != 0) {
        RK_LOGE("AIQ prepare failed: %d", ret);
        printf("[ERROR] AIQ prepare failed: %d\n", ret);
        rk_aiq_uapi2_sysctl_deinit(g_aiq_ctx);
        g_aiq_ctx = NULL;
        fflush(stdout);
        return -1;
    }
    printf("[DEBUG] AIQ prepare succeeded\n");
    fflush(stdout);

    printf("[DEBUG] Step 4: Starting AIQ...\n");
    fflush(stdout);
    
    // 4. 启动 AIQ
    ret = rk_aiq_uapi2_sysctl_start(g_aiq_ctx);
    if (ret != 0) {
        RK_LOGE("AIQ start failed: %d", ret);
        printf("[ERROR] AIQ start failed: %d\n", ret);
        rk_aiq_uapi2_sysctl_deinit(g_aiq_ctx);
        g_aiq_ctx = NULL;
        fflush(stdout);
        return -1;
    }

    RK_LOGI("RkAiq uAPI2 initialized with sensor: %s, IQ dir: %s", 
            static_info.sensor_info.sensor_name, iq_dir);
    printf("[INFO] RkAiq initialized successfully! g_aiq_ctx=%p\n", (void*)g_aiq_ctx);
    fflush(stdout);
    return 0;
}

/* ===========================
 * ISP 手动曝光设置函数 (基于 RkAiq uAPI2)
 * =========================== */
static void set_manual_exposure(RK_U32 exp_time_us, RK_U32 gain) {
    printf("[DEBUG] set_manual_exposure called: time=%u us, gain=%u\n", exp_time_us, gain);
    fflush(stdout);
    
    if (!g_aiq_ctx) {
        printf("[ERROR] AIQ context is NULL, cannot set exposure\n");
        fflush(stdout);
        return;
    }

    Uapi_ExpSwAttrV2_t exp_sw_attr;
    memset(&exp_sw_attr, 0, sizeof(exp_sw_attr));

    // 设置为同步模式，确保立即生效
    exp_sw_attr.sync.sync_mode = RK_AIQ_UAPI_MODE_SYNC;
    
    // 开启手动控制
    exp_sw_attr.Enable = RK_TRUE;
    exp_sw_attr.AecOpType = RK_AIQ_OP_MODE_MANUAL;

    // 设置线性 AE 的手动曝光参数
    exp_sw_attr.stManual.LinearAE.ManualTimeEn = RK_TRUE;
    exp_sw_attr.stManual.LinearAE.ManualGainEn = RK_TRUE;
    
    // 转换单位：us -> s
    exp_sw_attr.stManual.LinearAE.TimeValue = (float)exp_time_us / 1000000.0f;
    exp_sw_attr.stManual.LinearAE.GainValue = (float)gain / 1024.0f; // 假设 gain 1024 为 1x

    printf("[DEBUG] Calling rk_aiq_user_api2_ae_setExpSwAttr with TimeValue=%.6f, GainValue=%.6f\n",
           exp_sw_attr.stManual.LinearAE.TimeValue,
           exp_sw_attr.stManual.LinearAE.GainValue);
    fflush(stdout);

    int ret = rk_aiq_user_api2_ae_setExpSwAttr(g_aiq_ctx, exp_sw_attr);
    if (ret == 0) {
        RK_LOGI("Exposure set: Time=%d us, Gain=%d", exp_time_us, gain);
        printf("[INFO] Exposure updated\n");
        fflush(stdout);
    } else {
        RK_LOGE("Failed to set AE attributes via uAPI2, error code: %d", ret);
        printf("[ERROR] Failed to set exposure, error code: %d\n", ret);
        fflush(stdout);
    }
}

/* ===========================
 * main 核心命令接收逻辑
 * =========================== */
int main(int argc, char *argv[]) {
    (void)argc; (void)argv;
    int width = 1920;
    int height = 1080;
    int chn = 0;

    signal(SIGINT, sigterm_handler);

    RK_MPI_SYS_Init();

    //手动加载 IQ 文件，确保画质与 rkipc 一致
    printf("[DEBUG] Initializing ISP with IQ files from /oem/usr/share/iqfiles...\n");
    fflush(stdout);
    
    int iq_ret = init_isp_with_iq("/oem/usr/share/iqfiles");
    if (iq_ret != 0) {
        printf("[ERROR] Failed to initialize ISP with IQ files! ret=%d\n", iq_ret);
        printf("[ERROR] g_aiq_ctx will be NULL, exposure control will not work!\n");
        fflush(stdout);
    } else {
        printf("[INFO] ISP initialized successfully, g_aiq_ctx=%p\n", (void*)g_aiq_ctx);
        fflush(stdout);
    }

    vi_dev_init();
    vi_chn_init(chn, width, height);
    test_venc_init(0, width, height, RK_VIDEO_ID_JPEG);

    MPP_CHN_S src = {RK_ID_VI, 0, chn};
    MPP_CHN_S dst = {RK_ID_VENC, 0, 0};
    RK_MPI_SYS_Bind(&src, &dst);

    pthread_t th;
    pthread_create(&th, NULL, GetMediaBuffer0, NULL);

    char cmd[64];
    VENC_RECV_PIC_PARAM_S stRecvParam;

    while (!quit) {
        if (!fgets(cmd, sizeof(cmd), stdin))
            continue;

        if (strstr(cmd, "quit")) {
            quit = true;
            break;
        }

        if (strncmp(cmd, "snap", 4) == 0) {
            char name[SNAP_NAME_MAX] = {0};
            if (sscanf(cmd, "snap %127s", name) == 1) {
                pthread_mutex_lock(&g_snap_lock);
                strncpy(g_snap_name, name, SNAP_NAME_MAX - 1);
                pthread_mutex_unlock(&g_snap_lock);
                atomic_store(&g_take_picture, 1);
                memset(&stRecvParam, 0, sizeof(stRecvParam));
                stRecvParam.s32RecvPicNum = 1;
                RK_MPI_VENC_StartRecvFrame(0, &stRecvParam);
                printf("Snap request: %s\n", name);
                fflush(stdout);
            }
        }
        
        //处理曝光参数指令: exp time_us gain_val
        if (strncmp(cmd, "exp", 3) == 0) {
            RK_U32 exp_time = 10000; // 默认 10ms
            RK_U32 gain_val = 1024;  // 默认 1x 增益
            
            // 打印原始命令的十六进制，检查是否有隐藏字符
            printf("[DEBUG] Raw cmd length=%zu, content=[", strlen(cmd));
            for (size_t i = 0; i < strlen(cmd) && i < 64; i++) {
                if (cmd[i] == '\n') printf("\\n");
                else if (cmd[i] == '\r') printf("\\r");
                else printf("%c", cmd[i]);
            }
            printf("]\n");
            fflush(stdout);
            
            int parsed = sscanf(cmd, "exp %u %u", &exp_time, &gain_val);
            printf("[DEBUG] sscanf returned: %d, exp_time=%u, gain_val=%u\n", parsed, exp_time, gain_val);
            fflush(stdout);
            
            if (parsed == 2) {
                set_manual_exposure(exp_time, gain_val);
            } else {
                printf("Usage: exp [time_us] [gain]\n");
                fflush(stdout);
            }
        }
    }

    pthread_join(th, NULL);

    // 退出时清理 AIQ (uAPI2)
    if (g_aiq_ctx) {
        printf("[DEBUG] Deinitializing AIQ (uAPI2)...\n");
        fflush(stdout);
        rk_aiq_uapi2_sysctl_deinit(g_aiq_ctx);
        g_aiq_ctx = NULL;
    }

    RK_MPI_SYS_UnBind(&src, &dst);
    RK_MPI_VENC_DestroyChn(0);
    RK_MPI_VI_DisableChn(0, chn);
    RK_MPI_VI_DisableDev(0);
    RK_MPI_SYS_Exit();

    return 0;
}