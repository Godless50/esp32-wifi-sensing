import pytest

from wsn.provision import console_quote, parse_status


def esp_split_argv(line: str) -> list[str]:
    """Reference re-implementation of ESP-IDF esp_console_split_argv (components/console/split_argv.c)."""
    args, cur, state = [], [], "space"
    i = 0
    while i < len(line):
        ch = line[i]
        if state == "space":
            if ch == " ":
                pass
            elif ch == '"':
                state, cur = "quoted", []
            elif ch == "\\":
                state, cur = "escaped", []
            else:
                state, cur = "arg", [ch]
        elif state == "arg":
            if ch == " ":
                args.append("".join(cur)); state = "space"
            elif ch == "\\":
                state = "escaped"
            else:
                cur.append(ch)
        elif state == "quoted":
            if ch == '"':
                args.append("".join(cur)); state = "space"
            elif ch == "\\":
                state = "qescaped"
            else:
                cur.append(ch)
        elif state in ("escaped", "qescaped"):
            if ch in ('"', "\\"):
                cur.append(ch)
            else:
                cur.extend(["\\", ch])
            state = "arg" if state == "escaped" else "quoted"
        i += 1
    if state in ("arg", "quoted", "escaped", "qescaped"):
        args.append("".join(cur))
    return args


@pytest.mark.parametrize("s", ["abc", "a b", 'a"b', "a\\b", "пароль 1", 'x\\"y', "  lead", ""])
def test_console_quote_roundtrip(s):
    line = "wifi set " + console_quote(s) + " " + console_quote("tail")
    assert esp_split_argv(line) == ["wifi", "set", s, "tail"]


def test_parse_status():
    line = ("STATUS wifi=CONNECTED rssi=-47 ip=192.168.4.120 gw=192.168.4.1 "
            "bssid=02:00:00:00:a1:18 mac=24:ec:4a:01:02:03 csi_hz=98.0 fw=1")
    st = parse_status(line)
    assert st["wifi"] == "CONNECTED" and st["mac"] == "24:ec:4a:01:02:03" and st["rssi"] == "-47"


def test_wait_status_retries_while_board_boots():
    from wsn.provision import wait_status

    class FakeCon:
        calls = 0

        def status(self, seconds=3.0):
            self.calls += 1
            return None if self.calls < 3 else {"mac": "7c:4f:ad:bc:9b:54"}

    con = FakeCon()
    assert wait_status(con, attempts=5, pause=0) == {"mac": "7c:4f:ad:bc:9b:54"}
    assert con.calls == 3
    assert wait_status(type("Dead", (), {"status": lambda self, seconds=3.0: None})(), attempts=2, pause=0) is None


def test_parse_status_ignores_other_lines():
    assert parse_status("I (123) wifi: connected") is None


def test_scan_picks_the_strongest_node_of_the_network():
    from wsn.provision import best_bssid, networks, parse_scan
    lines = ["scan", "AP 02:00:00:00:a1:18 ch=6 rssi=-71 ssid=Home",
             "AP 02:00:00:00:a1:2c ch=6 rssi=-48 ssid=Home", "AP 11:22:33:44:55:66 ch=1 rssi=-60 ssid=Neighbour",
             "AP 11:22:33:44:55:77 ch=1 rssi=-80 ssid=", "AP_END n=4", "AP 99:99:99:99:99:99 ch=1 rssi=-1 ssid=Late"]
    aps = parse_scan(lines)
    assert len(aps) == 4 and aps[0]["bssid"] == "02:00:00:00:a1:18"
    assert best_bssid(aps, "Home") == "02:00:00:00:a1:2c"
    assert best_bssid(aps, "Nope") is None
    assert networks(aps) == [("Home", -48), ("Neighbour", -60)]
