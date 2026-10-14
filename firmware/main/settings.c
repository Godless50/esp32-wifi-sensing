#include "settings.h"

#include <string.h>

#include "nvs.h"

#define NS "wsn"

static bool all_zero(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (p[i]) return false;
    return true;
}

esp_err_t settings_load(wsn_settings_t *out)
{
    memset(out, 0, sizeof(*out));
    out->port = 5005;
    out->ping_hz = 100;
    out->csi_source = 0;
    out->promisc = 0;     /* включает опрос сети, когда находит устройства-линии (survey.c) */
    out->beacon_hz = 50;
    out->tgt_hz = 20;
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK; /* first boot */
    if (err != ESP_OK) return err;
    size_t len = sizeof(out->ssid);
    nvs_get_str(h, "ssid", out->ssid, &len);
    len = sizeof(out->pass);
    nvs_get_str(h, "pass", out->pass, &len);
    len = sizeof(out->host);
    nvs_get_str(h, "host", out->host, &len);
    len = sizeof(out->bssid);
    nvs_get_blob(h, "bssid", out->bssid, &len);
    len = sizeof(out->key);
    nvs_get_blob(h, "key", out->key, &len);
    nvs_get_u16(h, "port", &out->port);
    nvs_get_u16(h, "ping_hz", &out->ping_hz);
    nvs_get_u8(h, "csi_src", &out->csi_source);
    nvs_get_u8(h, "promisc", &out->promisc);
    nvs_get_u8(h, "beacon_hz", &out->beacon_hz);
    len = sizeof(out->links);
    if (nvs_get_blob(h, "links", out->links, &len) == ESP_OK) out->n_links = (uint8_t)(len / 6);
    len = sizeof(out->link_ip);
    if (nvs_get_blob(h, "link_ip", out->link_ip, &len) != ESP_OK || len != 4u * out->n_links)
        memset(out->link_ip, 0, sizeof(out->link_ip));  /* линии старого вида (сервер пинговал сам): не пинговать */
    nvs_get_u8(h, "tgt_hz", &out->tgt_hz);
    if (out->tgt_hz < 1 || out->tgt_hz > 50) out->tgt_hz = 20;
    nvs_get_u32(h, "hub_seq", &out->hub_seq);
    nvs_close(h);
    return ESP_OK;
}

esp_err_t settings_save(const wsn_settings_t *s)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    err = nvs_set_str(h, "ssid", s->ssid);
    if (!err) err = nvs_set_str(h, "pass", s->pass);
    if (!err) err = nvs_set_str(h, "host", s->host);
    if (!err) err = nvs_set_blob(h, "bssid", s->bssid, sizeof(s->bssid));
    if (!err) err = nvs_set_blob(h, "key", s->key, sizeof(s->key));
    if (!err) err = nvs_set_u16(h, "port", s->port);
    if (!err) err = nvs_set_u16(h, "ping_hz", s->ping_hz);
    if (!err) err = nvs_set_u8(h, "csi_src", s->csi_source);
    if (!err) err = nvs_set_u8(h, "promisc", s->promisc);
    if (!err) err = nvs_set_u8(h, "beacon_hz", s->beacon_hz);
    if (!err) err = nvs_set_u8(h, "tgt_hz", s->tgt_hz);
    if (!err) err = nvs_set_u32(h, "hub_seq", s->hub_seq);
    if (!err && s->n_links) err = nvs_set_blob(h, "links", s->links, (size_t)s->n_links * 6);
    if (!err && s->n_links) err = nvs_set_blob(h, "link_ip", s->link_ip, (size_t)s->n_links * 4);
    if (!err && !s->n_links) {
        esp_err_t e = nvs_erase_key(h, "links");
        err = (e == ESP_ERR_NVS_NOT_FOUND) ? ESP_OK : e;
        e = nvs_erase_key(h, "link_ip");
        if (!err && e != ESP_ERR_NVS_NOT_FOUND) err = e;
    }
    if (!err) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

uint32_t settings_next_boot_id(void)
{
    nvs_handle_t h;
    uint32_t boot = 0;
    if (nvs_open(NS, NVS_READWRITE, &h) != ESP_OK) return 0;
    nvs_get_u32(h, "boot", &boot); /* missing -> 0 */
    boot++;
    esp_err_t err = nvs_set_u32(h, "boot", boot);
    if (!err) err = nvs_commit(h);
    nvs_close(h);
    return err ? 0 : boot;
}

bool settings_complete(const wsn_settings_t *s)
{
    return s->ssid[0] && s->host[0] && !all_zero(s->bssid, 6) && !all_zero(s->key, 32);
}
