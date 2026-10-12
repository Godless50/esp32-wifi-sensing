# SDD ledger — plan: docs/superpowers/plans/2026-10-12-phase1-data-pipeline.md
Ruling: ledger kept manually on server (skill scripts live in the cloud container, repo is on the server) — cost if wrong: none, same format
Ruling: executor = native (user did not pick; my recommendation) — cost if wrong: no per-task reviews
Ruling: BSSID = node B 02:00:00:00:a1:18 (user said "other node", not which; B has best RSSI after A) — cost if wrong: one console command
Pre-flight: T1→T2 packet format (header 21B, frame 14B+len, telem 30B) consistent; T1/T3 parse_packet/peek_device_id consistent; T3→T4 on_packet(Packet, float) consistent; T2→T7 wsn_ctx_t/encode fns consistent; T6→T7 wsn_settings_t consistent; T4→T5 /api/devices, /ws/live consistent
Task 1: complete (commits 2edd7cb..65dc278, tests: pytest -q tests/test_protocol.py → 13 passed)
Task 2: complete (commits 65dc278..78432ea, tests: make test → OK; pytest -q → 15 passed)
Task 3: Ruling: added tests/behaviour beyond plan — KeyStore case-insensitive MACs + reload on file change (provision.py writes while api runs), callback_errors counter so a consumer bug can't kill UDP loop — cost if wrong: none
Task 3: complete (commits 78432ea..e200a62, tests: pytest -q → 25 passed)
Task 4: Ruling: LiveHub.subscribe() is sync and returns an async-iterable Subscription (plan said 'async subscribe() -> AsyncIterator') — registration must happen before WS accept / before first message; thread-safe put via call_soon_threadsafe — cost if wrong: API shape only
Task 4: Ruling: DeviceStats.counters is per-device {frames, packets}; global rejection counters come from IngestService in /api/devices — cost if wrong: none
Task 4: complete (commits e200a62..44433ad, tests: pytest -q → 43 passed)
Task 5: Ruling: replay lives in package as server/wsn/replay.py (python -m wsn.replay), not tools/replay.py — testable and shipped in image; --from-file dropped (csi.bin format belongs to phase 2) — cost if wrong: add flag in phase 2
Task 5: Ruling: compose runs api as uid/gid 1000 so data/device_keys.json stays readable by host tools — cost if wrong: env override WSN_UID/WSN_GID
Task 5: complete (commits 44433ad..9839aa6, tests: pytest -q → 46 passed; live check: /device at 100 Hz synthetic, screenshot OK)
Task 6: Ruling: provisioning code lives in server/wsn/provision.py (python -m wsn.provision) + tools/provision.sh wrapper, not tools/provision.py — testable, ships in image — cost if wrong: none
Task 6: Ruling: added host-tested wsn_util (MAC/hex parsers) to wsn_proto component; settings_complete(); wifi_mgr_set_link_cb/wifi_mgr_ip/wifi_mgr_rssi added to interface for T7 and status — cost if wrong: none
Task 6: Ruling: model partitions use custom type 0x40 subtypes 0x00/0x01 (data subtypes 0x40/0x41 are not in IDF's documented custom range) — cost if wrong: phase 3 reads by label anyway
Task 6: Ruling: serial opened with DTR=1,RTS=0 (pyserial sets DTR first; DTR=0 with RTS=1 resets S3 and USB re-enumerates); container gets -v /dev:/dev + cgroup rule 166:* — cost if wrong: provisioning flakiness
Task 6: Ruling: setters write NVS and apply after reboot (provision reboots) — cost if wrong: needs reboot after manual console edits
Task 6: complete (commits 9839aa6..ce37e1e, tests: make test → OK/OK util; pytest -q → 57 passed; HIL: provision → wifi=CONNECTED bssid=02:00:00:00:a1:18 rssi=-48)
Task 7: Ruling: csi_counters_t gained overflow + rssi_sum/rssi_n (telemetry rssi_avg, ring overflow visibility) — cost if wrong: none
Task 7: Ruling: CONFIG_FREERTOS_HZ=1000 (ping interval 10 ms needs ms ticks); ping timeout = 2*interval (min 20 ms) instead of 200 ms — cost if wrong: retune
Task 7: Ruling: Step 5 (person walks between board and node) not done — user not present; asked in final message
Task 7: complete (commits ce37e1e..HEAD~1, tests: make test OK; pytest 63 passed; HIL 30s 102 Hz PASS; HIL 600s avg 92.8 min 11.5 p10 55.5 below50 9.7% PASS; api stop/start and board reboot: stream resumes, replay 0)
Final review: subagent (opus) — 1 Critical, 5 Important, 8 Minor
Final: fixed C1 old-boot replay displaces live boot — monotonic NVS boot counter + persisted per-device boot + reset on key rotation; test_old_boot_replay_does_not_block_live_stream / test_old_boot_rejected_after_service_restart / test_boot_state_resets_when_key_rotates RED→GREEN, suite 63/63; HIL: provision→boot 2, reboot→boot 3, api restart, replay 0
Final: fixed I1 unbounded unknown_devices — LRU 32; test_unknown_devices_capped RED→GREEN, suite 63/63
Final: fixed I2 dead board shown healthy — devices(now) recomputes rate + age_s, UI "НЕТ СВЯЗИ" after 3 s; test_devices_rate_decays_when_silent RED→GREEN, suite 63/63; HIL: age 23 s / 0 Hz observed
Final: fixed I3 boot loop on bad wifi config — console starts first, set_config/csi/uplink errors non-fatal, wpa pass validated (wsn_valid_wpa_pass host test RED→GREEN)
Final: fixed I4 getaddrinfo spin — resolve retry every 5 s (firmware; verified by build + HIL only, no unit test: lwIP-bound)
Final: fixed I5 corrupt key file — keep last good keys, errors counter, no re-parse per packet; test_corrupt_key_file_keeps_last_good RED→GREEN, suite 63/63
Final: Ruling: spec 4.3/4.5 changed boot_id from random to monotonic NVS counter; provisioning always rotates key — needed for C1 — cost if wrong: re-provision after any NVS erase (already required for Wi-Fi creds)
Final: Ruling: after api restart seq of current boot is not persisted (any seq of current boot accepted once) — writing seq per packet is too costly; window is one boot — cost if wrong: replay of current-boot packets right after api restart
Final: minor (deferred): M1 wrong-key MACs not listed (only rejected_hmac count)
Final: minor (deferred): M2 STALL_HZ fixed at 20 → ping restart loop for ping_hz<20 or non-HT AP; no telemetry event for stall (spec 4.6)
Final: minor (deferred): M3 task watchdog only on uplink task (spec 4.6 says all tasks)
Final: minor (deferred): M4 cwb ignored; frames >384 B truncated instead of dropped
Final: minor (deferred): M5 provision.sh usage puts password in shell history; visible in docker inspect while running
Final: minor (deferred): M6 api 8080 open to LAN without auth until phase 2 TLS
Final: minor (deferred): M7 app never marks itself valid under rollback (matters for phase 3 OTA)
Final: minor (deferred): M8 closed WS tab lingers in hub until next broadcast
