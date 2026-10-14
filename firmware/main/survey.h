#pragma once
/* Survey of the home network: which devices give this board extra radio links.
 *
 * In a home Wi-Fi every frame from another device reaches the board through the access point, so its CSI
 * belongs to the link "access point -> board". The device's own transmission (its reply to the access point)
 * is heard only with the sniffer on: that frame carries CSI of the path "device -> board", one more line through
 * the flat. The survey finds which hosts answer an echo request, then pings them one at a time and watches
 * which transmitter starts speaking: the device itself, a mesh node that relays it, or nobody (a wired device,
 * 5 GHz, too far). Up to max_links devices that the board hears well become its links: it keeps pinging them
 * (ping_gen) and forwards their CSI like the other links. About a minute; best done while nobody moves. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "settings.h"
#include "wsn_proto.h"

typedef struct {
    uint8_t seconds;    /* time budget, 20..255 */
    uint8_t max_links;  /* device links to keep, 0..WSN_MAX_DEVICE_LINKS */
    uint8_t n_excl;
    uint32_t excl[WSN_CMD_SURVEY_MAX_EXCL]; /* hosts not to ping (lwIP order), e.g. phones */
    bool apply;         /* keep the chosen devices as links (stored in flash) */
} survey_args_t;

/* Start in the background. ESP_ERR_INVALID_STATE: already running or no IP. */
esp_err_t survey_start(const survey_args_t *a);
bool survey_running(void);
/* Latest report; true when it changed (or a resend of the final result is due: UDP may lose it). */
bool survey_report(wsn_survey_t *out);
/* Keep exactly these device links (transmitter MAC + host to ping), store them; n = 0 removes all
 * (and turns the sniffer off). */
esp_err_t survey_set_links(const uint8_t (*macs)[6], const uint32_t *ips, size_t n);
/* The hub's address (lwIP order): never pinged by the survey (it is a phone that will be carried around). */
void survey_set_hub(uint32_t ip);
/* Print the latest report to the console. */
void survey_print(void);
