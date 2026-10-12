idf_component_register(SRCS "app_main.c" "settings.c" "wifi_mgr.c" "console_cmds.c"
                       INCLUDE_DIRS "."
                       REQUIRES wsn_proto esp_wifi esp_netif esp_event nvs_flash console esp_timer)
