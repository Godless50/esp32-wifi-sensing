#include "soc/rtc_cntl_reg.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "console_cmds.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "csi_capture.h"
#include "survey.h"
#include "wifi_mgr.h"
#include "wsn_util.h"

static wsn_settings_t s_active;
static csi_hz_fn_t s_csi_hz;

/* Each setter edits the stored settings (not the running ones); applied after reboot. */
static int edit(void (*fn)(wsn_settings_t *, char **), char **argv, const char *ok)
{
    wsn_settings_t s;
    if (settings_load(&s) != ESP_OK) {
        puts("ERR nvs");
        return 1;
    }
    fn(&s, argv);
    if (settings_save(&s) != ESP_OK) {
        puts("ERR nvs");
        return 1;
    }
    printf("%s (reboot to apply)\n", ok);
    return 0;
}

static void set_wifi(wsn_settings_t *s, char **argv)
{
    strlcpy(s->ssid, argv[2], sizeof(s->ssid));
    strlcpy(s->pass, argv[3], sizeof(s->pass));
    wsn_parse_mac(argv[4], s->bssid);
}

static int cmd_wifi(int argc, char **argv)
{
    uint8_t mac[6];
    if (argc != 5 || strcmp(argv[1], "set") != 0) {
        puts("ERR usage: wifi set <ssid> <pass> <bssid>");
        return 1;
    }
    if (strlen(argv[2]) == 0 || strlen(argv[2]) > 32 || !wsn_valid_wpa_pass(argv[3]) || wsn_parse_mac(argv[4], mac)) {
        puts("ERR bad ssid/pass/bssid (pass: empty, 8..63 chars or 64 hex)");
        return 1;
    }
    return edit(set_wifi, argv, "OK wifi");
}

static void set_server(wsn_settings_t *s, char **argv)
{
    strlcpy(s->host, argv[2], sizeof(s->host));
    s->port = (uint16_t)atoi(argv[3]);
}

static int cmd_server(int argc, char **argv)
{
    if (argc != 4 || strcmp(argv[1], "set") != 0 || strlen(argv[2]) == 0 || strlen(argv[2]) >= 64) {
        puts("ERR usage: server set <host> <port>");
        return 1;
    }
    int port = atoi(argv[3]);
    if (port < 1 || port > 65535) {
        puts("ERR bad port");
        return 1;
    }
    return edit(set_server, argv, "OK server");
}

static void set_key(wsn_settings_t *s, char **argv) { wsn_parse_hex(argv[2], s->key, 32); }

static int cmd_key(int argc, char **argv)
{
    uint8_t k[32];
    if (argc != 3 || strcmp(argv[1], "set") != 0 || wsn_parse_hex(argv[2], k, 32)) {
        puts("ERR usage: key set <64 hex>");
        return 1;
    }
    return edit(set_key, argv, "OK key");
}

static void set_ping(wsn_settings_t *s, char **argv) { s->ping_hz = (uint16_t)atoi(argv[2]); }

static int cmd_ping_hz(int argc, char **argv)
{
    int hz = argc == 3 ? atoi(argv[2]) : 0;
    if (argc != 3 || strcmp(argv[1], "set") != 0 || hz < 1 || hz > 200) {
        puts("ERR usage: ping_hz set <1..200>");
        return 1;
    }
    return edit(set_ping, argv, "OK ping_hz");
}

static void set_src(wsn_settings_t *s, char **argv) { s->csi_source = strcmp(argv[2], "lltf") == 0; }

static int cmd_csi_source(int argc, char **argv)
{
    if (argc != 3 || strcmp(argv[1], "set") != 0 || (strcmp(argv[2], "ht") && strcmp(argv[2], "lltf"))) {
        puts("ERR usage: csi_source set ht|lltf");
        return 1;
    }
    return edit(set_src, argv, "OK csi_source");
}

void diag_print(void);  /* app_main.c */
void wifi_mgr_diag_print(void);  /* wifi_mgr.c */
/* Перезагрузка в режим прошивки без кнопки BOOT: на части плат USB-сброс esptool не переводит чип в загрузчик. */
static int cmd_dl(int argc, char **argv)
{
    printf("rebooting into download mode\n");
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(100));
    REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    esp_restart();
    return 0;
}

/* Проверка записи причины падения (panic_trace.c): crash abort | crash fault. */
static int cmd_crash(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "abort") == 0) abort();
    static volatile uintptr_t bad = 0x10; /* адрес вне памяти: StoreProhibited */
    if (argc == 2 && strcmp(argv[1], "fault") == 0) *(volatile uint32_t *)bad = 1;
    puts("ERR usage: crash abort|fault");
    return 1;
}

static int cmd_diag(int argc, char **argv)
{
    diag_print();
    wifi_mgr_diag_print();
    return 0;
}

static int cmd_status(int argc, char **argv)
{
    uint8_t mac[6];
    char mac_s[18], bssid_s[18];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    wsn_format_mac(mac, mac_s);
    wsn_format_mac(s_active.bssid, bssid_s);
    esp_ip4_addr_t ip = {0}, gw = {0};
    wifi_mgr_ip(&ip);
    wifi_mgr_gateway(&gw);
    printf("STATUS wifi=%s rssi=%d ip=" IPSTR " gw=" IPSTR " bssid=%s mac=%s csi_hz=%.1f fw=%d"
           " ping_hz=%u csi_source=%s server=%s:%u links=%u\n",
           wifi_mgr_state_str(wifi_mgr_state()), wifi_mgr_rssi(), IP2STR(&ip), IP2STR(&gw), bssid_s, mac_s,
           s_csi_hz ? s_csi_hz() : 0.0f, WSN_FW_VER, s_active.ping_hz, s_active.csi_source ? "lltf" : "ht",
           s_active.host[0] ? s_active.host : "-", s_active.port, s_active.n_links);
    return 0;
}

/* links set <mac>[,<mac>...] | links clear — extra transmitters whose CSI is forwarded (after reboot). */
static uint8_t s_new_links[WSN_MAX_LINKS][6];
static uint8_t s_new_n;

static void set_links(wsn_settings_t *s, char **argv)
{
    memcpy(s->links, s_new_links, sizeof(s->links));
    memset(s->link_ip, 0, sizeof(s->link_ip));  /* заданные вручную линии плата не пингует */
    s->n_links = s_new_n;
}

static int cmd_links(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "show") == 0) {
        wsn_settings_t st;
        if (settings_load(&st) != ESP_OK) {
            puts("ERR nvs");
            return 1;
        }
        for (int i = 0; i < st.n_links; i++) {
            char m[18];
            wsn_format_mac(st.links[i], m);
            const uint8_t *ip = (const uint8_t *)&st.link_ip[i];
            printf("LINK %s ping=%u.%u.%u.%u\n", m, ip[0], ip[1], ip[2], ip[3]);
        }
        printf("LINK_END n=%u promisc=%u tgt_hz=%u\n", st.n_links, st.promisc, st.tgt_hz);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "clear") == 0) {
        s_new_n = 0;
        memset(s_new_links, 0, sizeof(s_new_links));
        return edit(set_links, argv, "OK links 0");
    }
    if (argc != 3 || strcmp(argv[1], "set") != 0) {
        puts("ERR usage: links set <mac>[,<mac>...] | links clear");
        return 1;
    }
    char buf[WSN_MAX_LINKS * 18 + 1];  /* argv[2] длиннее — strlcpy обрежет, лишние MAC отвергнутся */
    strlcpy(buf, argv[2], sizeof(buf));
    s_new_n = 0;
    for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
        if (s_new_n == WSN_MAX_LINKS || wsn_parse_mac(tok, s_new_links[s_new_n])) {
            printf("ERR bad mac or more than %d: %s\n", WSN_MAX_LINKS, tok);
            return 1;
        }
        s_new_n++;
    }
    char ok[24];
    snprintf(ok, sizeof(ok), "OK links %u", s_new_n);
    return edit(set_links, argv, ok);
}

/* promisc on|off — runtime only: CSI from all transmitters on the channel (diagnostics). */
static int cmd_promisc(int argc, char **argv)
{
    if (argc != 2 || (strcmp(argv[1], "on") && strcmp(argv[1], "off") && strcmp(argv[1], "all"))) {
        puts("ERR usage: promisc on|all|off  (on = single data frames only, all = also A-MPDU)");
        return 1;
    }
    bool on = strcmp(argv[1], "off") != 0;
    if (on) {
        wifi_promiscuous_filter_t f = {.filter_mask = strcmp(argv[1], "all") == 0 ? WIFI_PROMIS_FILTER_MASK_DATA
                                                                                 : WIFI_PROMIS_FILTER_MASK_DATA_MPDU};
        esp_wifi_set_promiscuous_filter(&f);
    }
    esp_err_t err = esp_wifi_set_promiscuous(on);
    printf("%s promisc %s\n", err == ESP_OK ? "OK" : "ERR", esp_err_to_name(err));
    return err != ESP_OK;
}

/* csi_src [reset] — CSI frames per transmitter MAC since last reset. */
static int cmd_csi_src(int argc, char **argv)
{
    static csi_src_stat_t st[CSI_SRC_MAX];
    bool reset = argc == 2 && strcmp(argv[1], "reset") == 0;
    size_t n = csi_capture_sources(st, CSI_SRC_MAX, reset);
    for (size_t i = 0; i < n; i++) {
        char m[18];
        wsn_format_mac(st[i].mac, m);
        printf("SRC %s frames=%lu ht=%lu rssi=%ld\n", m, (unsigned long)st[i].frames, (unsigned long)st[i].ht,
               st[i].frames ? (long)(st[i].rssi_sum / (int32_t)st[i].frames) : 0L);
    }
    printf("SRC_END n=%u\n", (unsigned)n);
    return 0;
}

/* survey [seconds] [max_links] | survey show — find home devices that give this board extra links. */
static int cmd_survey(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "show") == 0) {
        survey_print();
        return 0;
    }
    survey_args_t a = {.seconds = 60, .max_links = 6, .apply = true};
    if (argc >= 2) a.seconds = (uint8_t)atoi(argv[1]);
    if (argc >= 3) a.max_links = (uint8_t)atoi(argv[2]);
    if (argc > 3 || (argc >= 2 && (atoi(argv[1]) < 20 || atoi(argv[1]) > 255)) ||
        (argc >= 3 && (atoi(argv[2]) < 0 || atoi(argv[2]) > WSN_MAX_DEVICE_LINKS))) {
        printf("ERR usage: survey [20..255 s] [0..%d links] | survey show\n", WSN_MAX_DEVICE_LINKS);
        return 1;
    }
    esp_err_t err = survey_start(&a);
    printf("%s survey %s\n", err == ESP_OK ? "OK" : "ERR", esp_err_to_name(err));
    return err != ESP_OK;
}

/* scan — APs on all channels (briefly interrupts CSI). */
static int cmd_scan(int argc, char **argv)
{
    wifi_scan_config_t sc = {.show_hidden = true};
    esp_err_t err = esp_wifi_scan_start(&sc, true);
    if (err != ESP_OK) {
        printf("ERR scan %s\n", esp_err_to_name(err));
        return 1;
    }
    uint16_t n = 32;
    static wifi_ap_record_t recs[32];
    esp_wifi_scan_get_ap_records(&n, recs);
    for (uint16_t i = 0; i < n; i++) {
        char m[18];
        wsn_format_mac(recs[i].bssid, m);
        printf("AP %s ch=%u rssi=%d ssid=%s\n", m, recs[i].primary, recs[i].rssi, (const char *)recs[i].ssid);
    }
    printf("AP_END n=%u\n", n);
    return 0;
}

static int cmd_reboot(int argc, char **argv)
{
    puts("OK reboot");
    fflush(stdout);
    esp_restart();
    return 0;
}

esp_err_t console_start(const wsn_settings_t *active, csi_hz_fn_t csi_hz)
{
    s_active = *active;
    s_csi_hz = csi_hz;
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t rc = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    rc.prompt = "wsn>";
    rc.max_cmdline_length = 256;
    esp_console_dev_usb_serial_jtag_config_t hw = ESP_CONSOLE_DEV_USB_SERIAL_JTAG_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_serial_jtag(&hw, &rc, &repl));
    const esp_console_cmd_t cmds[] = {
        {.command = "wifi", .help = "wifi set <ssid> <pass> <bssid>", .func = cmd_wifi},
        {.command = "server", .help = "server set <host> <port>", .func = cmd_server},
        {.command = "key", .help = "key set <64 hex>", .func = cmd_key},
        {.command = "ping_hz", .help = "ping_hz set <1..200>", .func = cmd_ping_hz},
        {.command = "csi_source", .help = "csi_source set ht|lltf", .func = cmd_csi_source},
        {.command = "status", .help = "print STATUS line", .func = cmd_status},
        {.command = "diag", .help = "reasons of the last restarts (power, panic, watchdog)", .func = cmd_diag},
        {.command = "crash", .help = "crash abort|fault (test of the crash record)", .func = cmd_crash},
        {.command = "dl", .help = "reboot into download mode (for flashing)", .func = cmd_dl},
        {.command = "reboot", .help = "restart", .func = cmd_reboot},
        {.command = "links", .help = "links set <mac,...> | links clear | links show", .func = cmd_links},
        {.command = "survey", .help = "survey [seconds] [max_links] | survey show: home devices as links", .func = cmd_survey},
        {.command = "promisc", .help = "promisc on|all|off (runtime; on = MPDU only)", .func = cmd_promisc},
        {.command = "csi_src", .help = "csi_src [reset]: CSI per transmitter", .func = cmd_csi_src},
        {.command = "scan", .help = "scan access points", .func = cmd_scan},
    };
    for (size_t i = 0; i < sizeof(cmds) / sizeof(cmds[0]); i++) ESP_ERROR_CHECK(esp_console_cmd_register(&cmds[i]));
    return esp_console_start_repl(repl);
}
