"""Resource-limited child entrypoint for untrusted document parsing."""
from __future__ import annotations

import json
import os
import socket
import sys


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _apply_limits() -> None:
    try:
        import resource
    except ImportError:
        return

    memory = max(128, _env_int("RAGB_PARSER_MEMORY_MB", 512)) * 1024 * 1024
    cpu = max(2, _env_int("RAGB_PARSER_CPU_SECONDS", 20))
    limits = (
        (getattr(resource, "RLIMIT_AS", None), (memory, memory)),
        (getattr(resource, "RLIMIT_CPU", None), (cpu, cpu + 1)),
        (getattr(resource, "RLIMIT_FSIZE", None), (16 * 1024 * 1024, 16 * 1024 * 1024)),
        (getattr(resource, "RLIMIT_NOFILE", None), (64, 64)),
        (getattr(resource, "RLIMIT_CORE", None), (0, 0)),
    )
    for kind, value in limits:
        if kind is None:
            continue
        try:
            resource.setrlimit(kind, value)
        except (OSError, ValueError):
            pass


def _disable_network() -> None:
    def denied(*_args, **_kwargs):
        raise PermissionError("parser_network_disabled")

    class NoNetworkSocket(socket.socket):
        def connect(self, *_args, **_kwargs):
            return denied()

        def connect_ex(self, *_args, **_kwargs):
            return denied()

    socket.socket = NoNetworkSocket  # type: ignore[assignment]
    socket.create_connection = denied  # type: ignore[assignment]
    socket.getaddrinfo = denied  # type: ignore[assignment]


def main() -> int:
    _apply_limits()
    _disable_network()
    max_input = max(1024 * 1024, _env_int("RAGB_PARSER_MAX_INPUT_BYTES", 32 * 1024 * 1024))
    max_output = max(1024 * 1024, _env_int("RAGB_PARSER_MAX_OUTPUT_BYTES", 8 * 1024 * 1024))
    mime = sys.argv[1] if len(sys.argv) > 1 else ""
    filename = sys.argv[2] if len(sys.argv) > 2 else ""
    data = sys.stdin.buffer.read(max_input + 1)
    if len(data) > max_input:
        payload = {"ok": False, "error": "parser_input_too_large"}
    else:
        try:
            from ragb.services.extract import _extract_in_process

            result = _extract_in_process(data, mime, filename)
            payload = {
                "ok": True,
                "text": result.text,
                "pages": result.pages,
                "title": result.title,
            }
        except Exception as e:  # noqa: BLE001
            payload = {"ok": False, "error": str(e)[:500] or e.__class__.__name__}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > max_output:
        raw = b'{"ok":false,"error":"parser_output_too_large"}'
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
