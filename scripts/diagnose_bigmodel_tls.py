#!/usr/bin/env python3
"""Unauthenticated TLS preflight through the evaluator's production transport.

Sends only HEAD to the fixed, previously registered BigModel endpoint. Never
loads runtime credentials, submits a model body, or disables TLS verification.
Run with the evaluation interpreter in a fresh process; SSL_CERT_FILE can pin
the existing system trust bundle without changing production TLS code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENDPOINT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"


def _peer(response):
    """Read public peer evidence only after a verified TLS connection."""
    objects = [response]
    for name in ("fp", "fp", "raw", "_sock"):
        objects.extend(getattr(obj, name, None) for obj in tuple(objects))
    for obj in objects:
        if not isinstance(obj, ssl.SSLSocket):
            continue
        certificate = obj.getpeercert()
        der = obj.getpeercert(binary_form=True)
        result = {
            "leafSha256": hashlib.sha256(der).hexdigest(),
            "subject": certificate.get("subject"),
            "issuer": certificate.get("issuer"),
            "subjectAltName": certificate.get("subjectAltName"),
            "notBefore": certificate.get("notBefore"),
            "notAfter": certificate.get("notAfter"),
            "tlsVersion": obj.version(),
            "cipher": obj.cipher(),
            "peerAddress": obj.getpeername(),
        }
        chain_reader = getattr(getattr(obj, "_sslobj", None), "get_verified_chain", None)
        if chain_reader is not None:
            chain = []
            for item in chain_reader():
                pem = item.public_bytes()
                details = item.get_info()
                chain.append({
                    "sha256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest(),
                    "subject": details.get("subject"), "issuer": details.get("issuer"),
                })
            result["verifiedChain"] = chain
        return result
    return None


def _proxy_summary():
    result = {}
    for scheme, raw in urllib.request.getproxies().items():
        if scheme == "no":
            result["noProxyPresent"] = bool(raw)
            continue
        value = urllib.parse.urlsplit(raw if "://" in raw else "//" + raw)
        result[scheme] = {
            "scheme": value.scheme, "hostname": value.hostname, "port": value.port,
            "credentialsPresent": value.username is not None or value.password is not None,
        }
    return result


def probe():
    from minicode.openai_adapter import _open_verified_url_controlled
    from minicode.tls import create_verified_ssl_context

    context = create_verified_ssl_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    ca_file = os.environ.get("SSL_CERT_FILE")
    result = {
        "schemaVersion": 1,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "pythonExecutable": str(Path(sys.executable).resolve()),
        "pythonVersion": sys.version,
        "opensslVersion": ssl.OPENSSL_VERSION,
        "endpoint": ENDPOINT,
        "endpointSha256": hashlib.sha256(ENDPOINT.encode()).hexdigest(),
        "method": "HEAD", "authorizationSent": False, "requestBodyBytes": 0,
        "defaultVerifyPaths": ssl.get_default_verify_paths()._asdict(),
        "defaultCertStore": ssl.create_default_context().cert_store_stats(),
        "productionCertStore": context.cert_store_stats(),
        "checkHostname": context.check_hostname, "verifyMode": int(context.verify_mode),
        "sslCertFile": ca_file,
        "sslCertFileSha256": hashlib.sha256(Path(ca_file).read_bytes()).hexdigest() if ca_file else None,
        "proxies": _proxy_summary(),
    }
    request = urllib.request.Request(ENDPOINT, method="HEAD")
    request._minicode_connect_timeout_seconds = 10
    assert not request.data and not request.has_header("Authorization")
    started = time.monotonic()
    response = None
    try:
        try:
            response = _open_verified_url_controlled(
                request, timeout=15, deadline_monotonic=started + 15,
            )
        except urllib.error.HTTPError as error:
            # 401/403/405 are valid unauthenticated transport observations.
            # They cannot be received until hostname/chain verification passes.
            response = error
        result.update(tlsVerified=True, httpStatus=response.status)
        try:
            result["peer"] = _peer(response)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            result.update(peer=None, peerEvidenceErrorType=type(error).__name__)
    except Exception as error:
        reason = getattr(error, "reason", error)
        result.update(
            tlsVerified=False, errorType=type(error).__name__, reasonType=type(reason).__name__,
            errorMessage=str(reason), verifyCode=getattr(reason, "verify_code", None),
            verifyMessage=getattr(reason, "verify_message", None),
        )
    finally:
        if response is not None:
            response.close()
    result["elapsedSeconds"] = round(time.monotonic() - started, 6)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; retain each diagnostic observation separately")
    result = probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "tlsVerified": result["tlsVerified"],
                      "httpStatus": result.get("httpStatus"), "errorType": result.get("errorType")}))
    return 0 if result["tlsVerified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
