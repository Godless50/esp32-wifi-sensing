cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# Опрос сети (main/survey.c) ждёт ответа ARP от десятков адресов сразу: точка доступа доставляет широковещательный
# запрос только к маяку DTIM (сотни мс), а при 10 записях таблицы lwIP (по умолчанию) записи живых устройств
# вытеснялись раньше ответа, и пинг до них не уходил (14.10: ответил один шлюз из ~20 устройств).
idf_build_set_property(COMPILE_DEFINITIONS "ARP_TABLE_SIZE=64" APPEND)
idf_build_set_property(COMPILE_DEFINITIONS "MEMP_NUM_ARP_QUEUE=64" APPEND)
project(wsn_fw)
