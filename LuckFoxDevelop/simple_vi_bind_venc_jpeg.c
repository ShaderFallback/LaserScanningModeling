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

static bool quit = false;
static RK_CHAR *g_pOutPath = "/tmp/";
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
 * VENC 取流线程（只在触发后保存）
 * =========================== */
static void *GetMediaBuffer0(void *arg) {
    (void)arg;
    void *pData = RK_NULL;
    int s32Ret;
    char jpeg_path[256];

    VENC_STREAM_S stFrame;
    stFrame.pstPack = malloc(sizeof(VENC_PACK_S));

    while (!quit) {
        s32Ret = RK_MPI_VENC_GetStream(0, &stFrame, 500);
        if (s32Ret != RK_SUCCESS)
            continue;

        if (atomic_exchange(&g_take_picture, 0) == 1) {
            pthread_mutex_lock(&g_snap_lock);

            snprintf(jpeg_path, sizeof(jpeg_path),
                     "%s/%s",
                     g_pOutPath, g_snap_name);

            pthread_mutex_unlock(&g_snap_lock);

            FILE *fp = fopen(jpeg_path, "wb"); // wb = 覆盖
            if (fp) {
                pData = RK_MPI_MB_Handle2VirAddr(
                    stFrame.pstPack->pMbBlk);
                fwrite(pData, 1,
                       stFrame.pstPack->u32Len, fp);
                fclose(fp);

                RK_LOGI("Saved jpeg: %s", jpeg_path);
            } else {
                RK_LOGE("Failed to open %s", jpeg_path);
            }
        }

        RK_MPI_VENC_ReleaseStream(0, &stFrame);
    }

    free(stFrame.pstPack);
    return NULL;
}


/* ===========================
 * VENC 初始化
 * =========================== */
static RK_S32 test_venc_init(int chnId,
                             int width,
                             int height,
                             RK_CODEC_ID_E enType) {
    VENC_CHN_ATTR_S stAttr;
    VENC_CHN_PARAM_S stParam;
    VENC_RECV_PIC_PARAM_S stRecvParam;

    memset(&stAttr, 0, sizeof(stAttr));
    memset(&stParam, 0, sizeof(stParam));

    stAttr.stVencAttr.enType = enType;
    stAttr.stVencAttr.enPixelFormat = RK_FMT_YUV420SP;
    stAttr.stVencAttr.u32PicWidth = width;
    stAttr.stVencAttr.u32PicHeight = height;
    stAttr.stVencAttr.u32VirWidth = width;
    stAttr.stVencAttr.u32VirHeight = height;
    stAttr.stVencAttr.u32StreamBufCnt = 2;
    stAttr.stVencAttr.u32BufSize = width * height * 3 / 2;

    stAttr.stVencAttr.stAttrJpege.enReceiveMode =
        VENC_PIC_RECEIVE_SINGLE;

    RK_MPI_VENC_CreateChn(chnId, &stAttr);

    memset(&stRecvParam, 0, sizeof(stRecvParam));
    stRecvParam.s32RecvPicNum = 1;
    RK_MPI_VENC_StartRecvFrame(chnId, &stRecvParam);

    return 0;
}

/* ===========================
 * VI 初始化（保持原样）
 * =========================== */
int vi_dev_init() {
    int devId = 0;
    int pipeId = devId;
    VI_DEV_ATTR_S stDevAttr;
    VI_DEV_BIND_PIPE_S stBindPipe;

    memset(&stDevAttr, 0, sizeof(stDevAttr));
    memset(&stBindPipe, 0, sizeof(stBindPipe));

    if (RK_MPI_VI_GetDevAttr(devId, &stDevAttr) ==
        RK_ERR_VI_NOT_CONFIG) {
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

int vi_chn_init(int channelId, int width, int height) {
	int ret;
	int buf_cnt = 2;
	// VI init
	VI_CHN_ATTR_S vi_chn_attr;
	memset(&vi_chn_attr, 0, sizeof(vi_chn_attr));
	vi_chn_attr.stIspOpt.u32BufCount = buf_cnt;
	vi_chn_attr.stIspOpt.enMemoryType =
	    VI_V4L2_MEMORY_TYPE_DMABUF; // VI_V4L2_MEMORY_TYPE_MMAP;
	vi_chn_attr.stSize.u32Width = width;
	vi_chn_attr.stSize.u32Height = height;
	vi_chn_attr.enPixelFormat = RK_FMT_YUV420SP;
	vi_chn_attr.enCompressMode = COMPRESS_MODE_NONE; // COMPRESS_AFBC_16x16;
	vi_chn_attr.u32Depth = 0; //0, get fail, 1 - u32BufCount, can get, if bind to other device, must be < u32BufCount
	ret = RK_MPI_VI_SetChnAttr(0, channelId, &vi_chn_attr);
	ret |= RK_MPI_VI_EnableChn(0, channelId);
	if (ret) {
		printf("ERROR: create VI error! ret=%d\n", ret);
		return ret;
	}

	return ret;
}

/* ===========================
 * main
 * =========================== */
int main(int argc, char *argv[]) {
    int width = 1920;
    int height = 1080;
    int chn = 0;

    signal(SIGINT, sigterm_handler);

    RK_MPI_SYS_Init();

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
            } else {
                printf("Usage: snap filename.jpg\n");
            }
        }
    }

    pthread_join(th, NULL);

    RK_MPI_SYS_UnBind(&src, &dst);
    RK_MPI_VENC_DestroyChn(0);
    RK_MPI_VI_DisableChn(0, chn);
    RK_MPI_VI_DisableDev(0);
    RK_MPI_SYS_Exit();

    return 0;
}
