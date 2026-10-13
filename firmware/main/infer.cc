// On-board model — see infer.h.
#include "infer.h"

#include <math.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "wsn_feat.h"

static const char *TAG = "infer";

namespace {

constexpr int kMaxLinks = 16;
constexpr int kCap = 768;            // кадров на линию: 4.3 с по 140 Гц у точки доступа с запасом
constexpr int kHdr = 128;
constexpr float kLagS = 0.3f;        // окно заканчивается чуть раньше «сейчас»: кадры ещё в очереди
constexpr size_t kArena = 160 * 1024;

struct Entry {
    double t;          // секунды от старта платы (float за неделю работы потерял бы точность 0.1 с)
    uint8_t ht;        // 1 = HT-LTF, 0 = L-LTF (кадр 11a/g)
    uint16_t amp[WSN_FEAT_SC];
};

struct Ring {
    Entry *e;
    int head, count;
};

uint8_t s_links[kMaxLinks][6];
int s_n_links;
Ring s_ring[kMaxLinks];
SemaphoreHandle_t s_mu;
uint32_t s_ver;
uint8_t s_order[3];  // позиции выходов act / zone / move

const tflite::Model *s_model;
tflite::MicroInterpreter *s_interp;
uint8_t *s_arena;
wsn_feat_frame_t *s_frames;  // рабочий буфер окна одной линии

portMUX_TYPE s_res_mux = portMUX_INITIALIZER_UNLOCKED;
wsn_pred_t s_res;
bool s_res_new;

int link_index(const uint8_t mac[6])
{
    for (int i = 0; i < s_n_links; i++)
        if (memcmp(s_links[i], mac, 6) == 0) return i;
    return -1;
}

bool load_model()
{
    const esp_partition_t *part = esp_partition_find_first((esp_partition_type_t)0x40, (esp_partition_subtype_t)0x00, "model_a");
    if (!part) { ESP_LOGW(TAG, "no model_a partition"); return false; }
    const void *map = nullptr;
    esp_partition_mmap_handle_t h;
    if (esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &map, &h) != ESP_OK) return false;
    const uint8_t *p = static_cast<const uint8_t *>(map);
    uint32_t ver, n_links, len, crc;
    memcpy(&ver, p + 4, 4); memcpy(&n_links, p + 8, 4); memcpy(&len, p + 12, 4); memcpy(&crc, p + 16, 4);
    if (memcmp(p, "WSNM", 4) != 0 || n_links == 0 || n_links > kMaxLinks || len == 0 || len > part->size - kHdr) {
        ESP_LOGW(TAG, "model_a: no valid model blob");
        return false;
    }
    uint32_t got = esp_rom_crc32_le(0, p + kHdr, len);
    if (got != crc) { ESP_LOGW(TAG, "model_a: crc %08lx != %08lx", (unsigned long)got, (unsigned long)crc); return false; }
    s_n_links = (int)n_links;
    memcpy(s_links, p + 20, 6 * n_links);
    memcpy(s_order, p + 20 + 6 * kMaxLinks, 3);
    s_model = tflite::GetModel(p + kHdr);
    s_ver = ver;
    ESP_LOGI(TAG, "model v%lu: %lu links, %lu bytes", (unsigned long)ver, (unsigned long)n_links, (unsigned long)len);
    return true;
}

/* кадры одной линии за окно [t0, now] в хронологическом порядке; время — относительно t0 */
int collect(int li, double t0, int *n_used_kind)
{
    Ring &r = s_ring[li];
    int n_ht = 0, n_lg = 0;
    for (int k = 0; k < r.count; k++) {
        const Entry &e = r.e[(r.head - r.count + k + kCap) % kCap];
        if (e.t >= t0) (e.ht ? n_ht : n_lg)++;
    }
    const uint8_t want = (n_ht && n_ht >= 0.2 * (n_ht + n_lg)) ? 1 : 0;  // HT, если линия не «только 11g»
    int n = 0;
    for (int k = 0; k < r.count && n < 1024; k++) {
        const Entry &e = r.e[(r.head - r.count + k + kCap) % kCap];
        if (e.t < t0 || e.ht != want) continue;
        s_frames[n].t = (float)(e.t - t0);
        memcpy(s_frames[n].amp, e.amp, sizeof(e.amp));
        n++;
    }
    *n_used_kind = want;
    return n;
}

uint8_t q255(float p) { return (uint8_t)fminf(255.0f, fmaxf(0.0f, roundf(p * 255.0f))); }

void softmax4(const float *x, float *p)
{
    float m = fmaxf(fmaxf(x[0], x[1]), fmaxf(x[2], x[3])), s = 0;
    for (int i = 0; i < 4; i++) { p[i] = expf(x[i] - m); s += p[i]; }
    for (int i = 0; i < 4; i++) p[i] /= s;
}

void dequant(const TfLiteTensor *t, float *out, int n)
{
    const float sc = t->params.scale;
    const int zp = t->params.zero_point;
    for (int i = 0; i < n; i++) out[i] = (t->data.int8[i] - zp) * sc;
}

void infer_once()
{
    static float feat[WSN_FEAT_NT][WSN_FEAT_NC];
    const int64_t t_start = esp_timer_get_time();
    const double t0 = t_start / 1e6 - kLagS - WSN_FEAT_WIN_S;
    TfLiteTensor *in = s_interp->input(0);
    const float sc = in->params.scale;
    const int zp = in->params.zero_point;
    int live = 0;
    for (int li = 0; li < s_n_links; li++) {
        int kind, n;
        xSemaphoreTake(s_mu, portMAX_DELAY);
        n = collect(li, t0 - 0.05, &kind);
        xSemaphoreGive(s_mu);
        live += wsn_feat_link(s_frames, n, 0.05f, feat) > 0;  // окно начинается в t0 (кадры взяты с запасом 50 мс)
        vTaskDelay(1);  // даём поработать задаче простоя ядра 0 (сторожевой таймер)
        int8_t *dst = in->data.int8 + li * WSN_FEAT_NT * WSN_FEAT_NC;
        for (int k = 0; k < WSN_FEAT_NT; k++)
            for (int c = 0; c < WSN_FEAT_NC; c++) {
                float q = roundf(wsn_feat_squash(feat[k][c]) / sc) + zp;
                dst[k * WSN_FEAT_NC + c] = (int8_t)fminf(127.0f, fmaxf(-128.0f, q));
            }
    }
    const int64_t t_feat = esp_timer_get_time();
    if (s_interp->Invoke() != kTfLiteOk) { ESP_LOGW(TAG, "invoke failed"); return; }
    static int s_runs;
    if (s_runs++ < 3)
        ESP_LOGI(TAG, "features %lld ms, network %lld ms, %d links live", (t_feat - t_start) / 1000,
                 (esp_timer_get_time() - t_feat) / 1000, live);
    float act[4], zone[4], mv[1], pa[4], pz[4];
    dequant(s_interp->output(s_order[0]), act, 4);
    dequant(s_interp->output(s_order[1]), zone, 4);
    dequant(s_interp->output(s_order[2]), mv, 1);
    softmax4(act, pa);
    softmax4(zone, pz);
    wsn_pred_t r = {};
    r.model_ver = s_ver;
    r.infer_ms = (uint16_t)((esp_timer_get_time() - t_start) / 1000);
    r.links_live = (uint8_t)live;
    for (int i = 0; i < 4; i++) { r.act[i] = q255(pa[i]); r.zone[i] = q255(pz[i]); }
    r.move = q255(1.0f / (1.0f + expf(-mv[0])));
    portENTER_CRITICAL(&s_res_mux);
    s_res = r;
    s_res_new = true;
    portEXIT_CRITICAL(&s_res_mux);
}

void infer_task(void *)
{
    TickType_t last = xTaskGetTickCount();
    int64_t log_at = 0;
    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(1000));
        infer_once();
        if (xTaskGetTickCount() - last > pdMS_TO_TICKS(900)) {  // не успели за секунду: пауза, не крутимся без сна
            vTaskDelay(pdMS_TO_TICKS(200));
            last = xTaskGetTickCount();
        }
        if (esp_timer_get_time() > log_at) {
            log_at = esp_timer_get_time() + 60 * 1000000LL;
            ESP_LOGI(TAG, "%u ms, %u links live, move %u", s_res.infer_ms, s_res.links_live, s_res.move);
        }
    }
}

}  // namespace

extern "C" void infer_push(const wsn_csi_frame_t *f)
{
    if (!s_interp || !f || f->len < 128) return;
    const int li = link_index(f->src);
    if (li < 0) return;
    const uint8_t ht = f->len >= 256 ? 1 : 0;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    Ring &r = s_ring[li];
    Entry &e = r.e[r.head];
    e.t = f->t_us / 1e6;
    e.ht = ht;
    wsn_feat_amplitude(f->data + (ht ? 128 : 0), e.amp);
    r.head = (r.head + 1) % kCap;
    if (r.count < kCap) r.count++;
    xSemaphoreGive(s_mu);
}

extern "C" bool infer_take(wsn_pred_t *out)
{
    bool fresh;
    portENTER_CRITICAL(&s_res_mux);
    fresh = s_res_new;
    if (fresh) { *out = s_res; s_res_new = false; }
    portEXIT_CRITICAL(&s_res_mux);
    return fresh;
}

extern "C" uint32_t infer_model_ver(void) { return s_interp ? s_ver : 0; }

extern "C" esp_err_t infer_start(void)
{
    if (!load_model()) return ESP_ERR_NOT_FOUND;
    static tflite::MicroMutableOpResolver<8> ops;
    ops.AddPad();
    ops.AddConv2D();
    ops.AddFullyConnected();
    ops.AddMean();
    ops.AddReduceMax();
    ops.AddReshape();
    ops.AddConcatenation();
    ops.AddAdd();
    s_arena = (uint8_t *)heap_caps_aligned_alloc(16, kArena, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_arena) s_arena = (uint8_t *)heap_caps_aligned_alloc(16, kArena, MALLOC_CAP_SPIRAM);
    s_frames = (wsn_feat_frame_t *)heap_caps_calloc(1024, sizeof(wsn_feat_frame_t), MALLOC_CAP_SPIRAM);
    if (!s_arena || !s_frames) return ESP_ERR_NO_MEM;
    for (int i = 0; i < s_n_links; i++) {
        s_ring[i].e = (Entry *)heap_caps_calloc(kCap, sizeof(Entry), MALLOC_CAP_SPIRAM);
        if (!s_ring[i].e) return ESP_ERR_NO_MEM;
    }
    s_mu = xSemaphoreCreateMutex();
    static tflite::MicroInterpreter interp(s_model, ops, s_arena, kArena);
    if (interp.AllocateTensors() != kTfLiteOk) { ESP_LOGE(TAG, "AllocateTensors failed"); return ESP_FAIL; }
    TfLiteTensor *in = interp.input(0);
    if (in->type != kTfLiteInt8 || in->bytes != (size_t)s_n_links * WSN_FEAT_NT * WSN_FEAT_NC) {
        ESP_LOGE(TAG, "unexpected input: type %d, %u bytes", in->type, (unsigned)in->bytes);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "arena %u of %u bytes used", (unsigned)interp.arena_used_bytes(), (unsigned)kArena);
    s_interp = &interp;
    return xTaskCreatePinnedToCore(infer_task, "infer", 12288, nullptr, 2, nullptr, 0) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
