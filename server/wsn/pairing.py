"""QR code that pairs the phone app with this server (install.sh pair).

The server's TLS certificate is issued by Caddy's own local CA, unknown to the phone. The QR code carries the
server address and the SHA-256 of that CA's root certificate; the app downloads /ca.crt, accepts it only if the
hash matches and from then on trusts exactly that CA (no "accept any certificate", no CA baked into the APK).

usage: cat root.crt | python -m wsn.pairing --host 192.168.1.10 [--port 8443]
"""

from __future__ import annotations

import argparse
import hashlib
import ssl
import sys
from urllib.parse import urlencode


def ca_fingerprint(pem: str) -> str:
    """SHA-256 of the DER certificate, hex lower case (the same as the app computes)."""
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def pair_uri(host: str, port: int, fp: str) -> str:
    return "wsn://pair?" + urlencode({"h": host, "p": port, "ca": fp})


def short_code(fp: str) -> str:
    """First 16 hex digits in groups of 4: to compare by eye when the code is typed in by hand."""
    return "-".join(fp[i:i + 4] for i in range(0, 16, 4)).upper()


def print_qr(text: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(tty=False, invert=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--apk", action="store_true", help="also a QR code with the link to download the app")
    a = ap.parse_args(argv)
    pem = sys.stdin.read()
    if "BEGIN CERTIFICATE" not in pem:
        print("ERROR: no certificate on stdin (has the server started?)", file=sys.stderr)
        return 2
    fp = ca_fingerprint(pem)
    if a.apk:
        url = f"https://{a.host}:{a.port}/app.apk"
        print(f"\nПриложение для телефона: {url}\n(браузер предупредит о сертификате — это ваш сервер, продолжайте)\n")
        print_qr(url)
    uri = pair_uri(a.host, a.port, fp)
    print("\nСопряжение: в приложении «Сканировать QR» и наведите камеру на этот код\n")
    print_qr(uri)
    print(f"\nВручную: сервер {a.host}:{a.port}, код сертификата {short_code(fp)}")
    print(f"(полностью: {fp})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
