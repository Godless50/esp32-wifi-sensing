import stat

from wsn.keys import KeyStore

MAC = "02:00:00:00:00:01"


def test_keystore_roundtrip_and_perms(tmp_path):
    path = tmp_path / "device_keys.json"
    ks = KeyStore(path)
    assert ks.get(MAC) is None
    ks.set(MAC, bytes(range(32)))
    assert KeyStore(path).get(MAC) == bytes(range(32))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_keystore_mac_case_insensitive(tmp_path):
    ks = KeyStore(tmp_path / "k.json")
    ks.set("AA:BB:CC:DD:EE:FF", b"\x01" * 32)
    assert ks.get("aa:bb:cc:dd:ee:ff") == b"\x01" * 32


def test_keystore_reloads_when_file_changes(tmp_path):
    path = tmp_path / "k.json"
    ks = KeyStore(path)
    assert ks.get(MAC) is None
    KeyStore(path).set(MAC, b"\x02" * 32)  # другой процесс (provision.py) дописал ключ
    assert ks.get(MAC) == b"\x02" * 32
