idf_component_register(SRCS "app_main.c" "settings.c" "wifi_mgr.c" "console_cmds.c"
                            "ping_gen.c" "csi_capture.c" "uplink.c" "infer.cc" "model_ota.c"
                       INCLUDE_DIRS "."
                       REQUIRES wsn_proto wsn_feat esp-tflite-micro esp_partition spi_flash esp_wifi esp_netif esp_event nvs_flash console esp_timer
                                lwip mbedtls esp_ringbuf esp_driver_usb_serial_jtag app_update esp_app_format)
