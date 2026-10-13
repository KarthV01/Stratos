"""Shared TLS configuration for raw TCP and gRPC listeners."""

from __future__ import annotations

import ssl
from pathlib import Path

import grpc


def _validate(cert_path: str | None, key_path: str | None) -> bool:
    if bool(cert_path) != bool(key_path):
        raise RuntimeError("GATEWAY_TLS_CERT and GATEWAY_TLS_KEY must be set together")
    return bool(cert_path)


def tcp_server_context(
    cert_path: str | None, key_path: str | None, client_ca_path: str | None
) -> ssl.SSLContext | None:
    if not _validate(cert_path, key_path):
        return None
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    if client_ca_path:
        context.load_verify_locations(cafile=client_ca_path)
        context.verify_mode = ssl.CERT_REQUIRED
    return context


def grpc_server_credentials(
    cert_path: str | None, key_path: str | None, client_ca_path: str | None
) -> grpc.ServerCredentials | None:
    if not _validate(cert_path, key_path):
        return None
    private_key = Path(key_path).read_bytes()
    certificate_chain = Path(cert_path).read_bytes()
    client_ca = Path(client_ca_path).read_bytes() if client_ca_path else None
    return grpc.ssl_server_credentials(
        ((private_key, certificate_chain),),
        root_certificates=client_ca,
        require_client_auth=client_ca is not None,
    )
