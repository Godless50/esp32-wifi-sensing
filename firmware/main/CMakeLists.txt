idf_component_register(SRCS "app_main.c" "settings.c" "wifi_mgr.c" "console_cmds.c"
                            "ping_gen.c" "csi_capture.c" "espnow_beacon.c" "panic_trace.c" "uplink.c" "infer.cc" "model_ota.c"
                       INCLUDE_DIRS "."
                       REQUIRES wsn_proto wsn_feat esp-tflite-micro esp_partition spi_flash esp_wifi esp_netif esp_event nvs_flash console esp_timer
                                lwip mbedtls esp_ringbuf esp_driver_usb_serial_jtag app_update esp_app_format)
# panic_trace.c: запоминает причину паники в RTC-памяти до перезапуска
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=esp_panic_handler" "-Wl,--wrap=panicHandler" "-Wl,--wrap=xt_unhandled_exception")
