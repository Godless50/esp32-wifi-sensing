#include "console_cmds.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"
#include "esp_mac.h"
#include "esp_system.h"
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
           " ping_hz=%u csi_source=%s server=%s:%u\n",
           wifi_mgr_state_str(wifi_mgr_state()), wifi_mgr_rssi(), IP2STR(&ip), IP2STR(&gw), bssid_s, mac_s,
           s_csi_hz ? s_csi_hz() : 0.0f, WSN_FW_VER, s_active.ping_hz, s_active.csi_source ? "lltf" : "ht",
           s_active.host[0] ? s_active.host : "-", s_active.port);
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
        {.command = "reboot", .help = "restart", .func = cmd_reboot},
    };
    for (size_t i = 0; i < sizeof(cmds) / sizeof(cmds[0]); i++) ESP_ERROR_CHECK(esp_console_cmd_register(&cmds[i]));
    return esp_console_start_repl(repl);
}
