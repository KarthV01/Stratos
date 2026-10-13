"""Run the Stratos gateway's TCP and gRPC Full Agent listeners."""

from __future__ import annotations

import asyncio

from app.broker import redis_client
from app.config import (
    GATEWAY_GRPC_HOST,
    GATEWAY_GRPC_PORT,
    GATEWAY_TCP_HOST,
    GATEWAY_TCP_PORT,
    GATEWAY_TLS_CERT,
    GATEWAY_TLS_CLIENT_CA,
    GATEWAY_TLS_KEY,
)
from app.stratos_gateway.backend import GatewayBackend
from app.stratos_gateway.grpc_service import create_grpc_server
from app.stratos_gateway.tcp import TcpGatewayServer
from app.stratos_gateway.tls import grpc_server_credentials, tcp_server_context


async def main() -> None:
    redis = redis_client()
    backend = GatewayBackend(redis)
    tcp = TcpGatewayServer(
        backend,
        GATEWAY_TCP_HOST,
        GATEWAY_TCP_PORT,
        ssl_context=tcp_server_context(
            GATEWAY_TLS_CERT, GATEWAY_TLS_KEY, GATEWAY_TLS_CLIENT_CA
        ),
    )
    grpc_server = create_grpc_server(
        backend,
        GATEWAY_GRPC_HOST,
        GATEWAY_GRPC_PORT,
        credentials=grpc_server_credentials(
            GATEWAY_TLS_CERT, GATEWAY_TLS_KEY, GATEWAY_TLS_CLIENT_CA
        ),
    )
    await tcp.start()
    await grpc_server.start()
    security = "TLS" if GATEWAY_TLS_CERT else "development plaintext"
    print(
        f"[gateway] TCP :{GATEWAY_TCP_PORT}, gRPC :{GATEWAY_GRPC_PORT} ({security})",
        flush=True,
    )
    try:
        await grpc_server.wait_for_termination()
    finally:
        await tcp.close()
        await grpc_server.stop(grace=5)
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
