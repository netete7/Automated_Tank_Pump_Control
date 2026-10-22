#!/usr/bin/env python3
"""Local, read-only OpenPLC Runtime v4 bridge for the tank visualizer."""

from __future__ import annotations

import getpass
import json
import queue
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import socketio


ROOT = Path(__file__).resolve().parent
RUNTIME = "https://localhost:8443"
NAMESPACE = "/api/debug"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765
POLL_SECONDS = 0.5
CONFIG = json.loads((ROOT / "openplc-map.json").read_text(encoding="utf-8"))
VARIABLES = CONFIG["variables"]

state_lock = threading.Lock()
state = {
    "connected": False,
    "message": "Waiting for OpenPLC Runtime connection",
    "programMatches": False,
    "values": {item["name"]: None for item in VARIABLES},
    "updatedAt": None,
}
responses: queue.Queue = queue.Queue()


def login() -> str:
    username = input("OpenPLC username: ").strip()
    password = getpass.getpass("OpenPLC password (typed privately): ")
    payload = json.dumps({"username": username, "password": password}).encode()
    request = Request(
        f"{RUNTIME}/api/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # OpenPLC Runtime v4 uses a local self-signed certificate by default.
    context = ssl._create_unverified_context()
    try:
        with urlopen(request, context=context, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"OpenPLC login failed (HTTP {exc.code}). Check the username and password.") from exc
    except URLError as exc:
        raise RuntimeError("Could not reach https://localhost:8443. Check that Runtime v4 is running on this Mac.") from exc
    token = result.get("access_token") if isinstance(result, dict) else None
    if not token:
        raise RuntimeError("OpenPLC did not return a login token. Check the Runtime account and try again.")
    return token


def parse_hex_response(payload: object) -> bytes:
    if not isinstance(payload, dict) or not payload.get("success"):
        message = payload.get("error", "No debug response") if isinstance(payload, dict) else "Invalid debug response"
        raise RuntimeError(str(message))
    data = payload.get("data", "")
    if not isinstance(data, str):
        raise RuntimeError("OpenPLC returned an unexpected debug response format.")
    try:
        return bytes.fromhex(data)
    except ValueError as exc:
        raise RuntimeError("OpenPLC returned malformed debug data.") from exc


def debug_command(client: socketio.Client, command: bytes, timeout: float = 4.0) -> bytes:
    while True:
        try:
            responses.get_nowait()
        except queue.Empty:
            break
    client.emit("debug_command", {"command": " ".join(f"{byte:02X}" for byte in command)}, namespace=NAMESPACE)
    try:
        return parse_hex_response(responses.get(timeout=timeout))
    except queue.Empty as exc:
        raise RuntimeError("The OpenPLC debugger did not answer the read request.") from exc


def verify_program(client: socketio.Client) -> None:
    response = debug_command(client, bytes.fromhex("45 DE AD 00 00"))
    if len(response) < 34 or response[0] != 0x45 or response[1] != 0x7E:
        raise RuntimeError("Could not verify the program loaded in Runtime v4.")
    actual = response[2:34].decode("ascii", errors="replace").lower()
    expected = CONFIG["expectedMd5"].lower()
    if actual != expected:
        raise RuntimeError(
            "The running PLC program does not match the Runtime v4 debug map found in your project. "
            "Live values are paused to avoid showing the wrong tags."
        )


def read_values(client: socketio.Client) -> dict[str, bool]:
    entries = b"".join(
        bytes([item["arrayIdx"]]) + int(item["elemIdx"]).to_bytes(2, "big")
        for item in VARIABLES
    )
    command = bytes([0x44]) + len(VARIABLES).to_bytes(2, "big") + entries
    response = debug_command(client, command)
    if len(response) < 10 or response[0] != 0x44 or response[1] != 0x7E:
        raise RuntimeError("OpenPLC returned an error while reading the selected variables.")
    body_size = int.from_bytes(response[8:10], "big")
    body = response[10:10 + body_size]
    if len(body) < len(VARIABLES):
        raise RuntimeError("The debug response was shorter than the variable map expects.")
    return {item["name"]: bool(body[index]) for index, item in enumerate(VARIABLES)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path in ("/", "/index.html"):
            data = (ROOT / "index.html").read_bytes()
            content_type = "text/html; charset=utf-8"
            status_code = 200
        elif self.path == "/api/values":
            with state_lock:
                data = json.dumps(state).encode("utf-8")
            content_type = "application/json; charset=utf-8"
            status_code = 200
        else:
            data = b"Not found"
            content_type = "text/plain; charset=utf-8"
            status_code = 404
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve_page() -> None:
    server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    server.serve_forever()


def main() -> None:
    token = login()
    client = socketio.Client(ssl_verify=False, reconnection=True, logger=False, engineio_logger=False)

    @client.on("debug_response", namespace=NAMESPACE)
    def on_debug_response(payload: object) -> None:
        responses.put(payload)

    @client.event(namespace=NAMESPACE)
    def connect() -> None:
        with state_lock:
            state["message"] = "Connected to OpenPLC debugger"

    @client.event(namespace=NAMESPACE)
    def disconnect() -> None:
        with state_lock:
            state["connected"] = False
            state["message"] = "OpenPLC debugger disconnected"

    try:
        client.connect(
            RUNTIME,
            auth={"token": token},
            namespaces=[NAMESPACE],
            transports=["websocket"],
            wait_timeout=10,
        )
        verify_program(client)
        with state_lock:
            state["connected"] = True
            state["programMatches"] = True
            state["message"] = "Connected · read-only live values"
        threading.Thread(target=serve_page, daemon=True).start()
        print(f"\nTank visualizer ready: http://{WEB_HOST}:{WEB_PORT}")
        print("This helper reads debugger values only. Press Ctrl+C here to stop it.\n")
        while True:
            try:
                values = read_values(client)
                with state_lock:
                    state["connected"] = True
                    state["programMatches"] = True
                    state["message"] = "Connected · read-only live values"
                    state["values"] = values
                    state["updatedAt"] = time.time()
            except Exception as exc:
                with state_lock:
                    state["connected"] = False
                    state["message"] = str(exc)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nTank visualizer stopped.")
    finally:
        if client.connected:
            client.disconnect()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nCould not start the local tank visualizer: {exc}")
