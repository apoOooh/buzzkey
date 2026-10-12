import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

PROTOCOL_VERSION = 1
MAX_RESPONSE_BYTES = 64 * 1024
DISCOVERY_FILE = "voice-endpoint.json"


@dataclass
class SwarmtermEndpoint:
    pipe: str
    token: str
    pid: int


@dataclass
class SwarmtermTarget:
    endpoint: SwarmtermEndpoint
    pin: Optional[str]
    name: Optional[str] = None
    error: Optional[str] = None


class SwarmtermError(Exception):
    pass


class PipeTransport:
    def __init__(self, pipe_name: str, timeout_seconds: float):
        import pywintypes
        import win32event
        import win32file

        self._win32event = win32event
        self._win32file = win32file
        self._pywintypes = pywintypes
        self._deadline = time.monotonic() + timeout_seconds
        try:
            self._handle = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as e:
            raise SwarmtermError(f"connect failed: {e.strerror}") from e

    def _remaining_ms(self) -> int:
        return max(0, int((self._deadline - time.monotonic()) * 1000))

    def _wait(self, overlapped) -> int:
        result = self._win32event.WaitForSingleObject(overlapped.hEvent, self._remaining_ms())
        if result != self._win32event.WAIT_OBJECT_0:
            self._win32file.CancelIo(self._handle)
            raise SwarmtermError("timeout")
        try:
            return self._win32file.GetOverlappedResult(self._handle, overlapped, False)
        except self._pywintypes.error as e:
            raise SwarmtermError(f"pipe error: {e.strerror}") from e

    def _overlapped(self):
        overlapped = self._pywintypes.OVERLAPPED()
        overlapped.hEvent = self._win32event.CreateEvent(None, True, False, None)
        return overlapped

    def send_line(self, line: str):
        overlapped = self._overlapped()
        try:
            self._win32file.WriteFile(self._handle, (line + "\n").encode("utf-8"), overlapped)
        except self._pywintypes.error as e:
            raise SwarmtermError(f"write failed: {e.strerror}") from e
        self._wait(overlapped)

    def read_line(self) -> str:
        data = b""
        while b"\n" not in data:
            if len(data) > MAX_RESPONSE_BYTES:
                raise SwarmtermError("response too large")
            buffer = self._win32file.AllocateReadBuffer(4096)
            overlapped = self._overlapped()
            try:
                self._win32file.ReadFile(self._handle, buffer, overlapped)
            except self._pywintypes.error as e:
                raise SwarmtermError(f"read failed: {e.strerror}") from e
            count = self._wait(overlapped)
            if count == 0:
                raise SwarmtermError("connection closed")
            data += bytes(buffer[:count])
        return data.split(b"\n", 1)[0].decode("utf-8")

    def close(self):
        try:
            self._handle.Close()
        except Exception:
            pass


def default_discovery_dirs(app_ids: list[str]) -> list[Path]:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    return [Path(base) / app_id for app_id in app_ids]


class SwarmtermClient:
    def __init__(self,
                 enabled: bool,
                 app_ids: list[str],
                 begin_timeout_seconds: float = 1.0,
                 insert_timeout_seconds: float = 10.0,
                 foreground_pid: Optional[Callable[[], Optional[int]]] = None,
                 transport_factory: Callable[[str, float], object] = PipeTransport,
                 discovery_dirs: Optional[list[Path]] = None):
        self.logger = logging.getLogger(__name__)
        self.enabled = enabled
        self.begin_timeout_seconds = begin_timeout_seconds
        self.insert_timeout_seconds = insert_timeout_seconds
        self.foreground_pid = foreground_pid or (lambda: None)
        self.transport_factory = transport_factory
        self.discovery_dirs = discovery_dirs if discovery_dirs is not None else default_discovery_dirs(app_ids)

    def _read_endpoints(self) -> list[SwarmtermEndpoint]:
        endpoints = []
        for directory in self.discovery_dirs:
            path = directory / DISCOVERY_FILE
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("version") != PROTOCOL_VERSION:
                continue
            pipe, token, pid = data.get("pipe"), data.get("token"), data.get("pid")
            if isinstance(pipe, str) and pipe.startswith("\\\\.\\pipe\\") and isinstance(token, str) and isinstance(pid, int):
                endpoints.append(SwarmtermEndpoint(pipe=pipe, token=token, pid=pid))
        return endpoints

    def find_foreground_endpoint(self) -> Optional[SwarmtermEndpoint]:
        if not self.enabled:
            return None
        pid = self.foreground_pid()
        if pid is None:
            return None
        for endpoint in self._read_endpoints():
            if endpoint.pid == pid:
                return endpoint
        return None

    def _request(self, endpoint: SwarmtermEndpoint, payload: dict, timeout_seconds: float) -> dict:
        transport = self.transport_factory(endpoint.pipe, timeout_seconds)
        try:
            transport.send_line(json.dumps({"v": PROTOCOL_VERSION, "token": endpoint.token, **payload}))
            line = transport.read_line()
        finally:
            transport.close()
        try:
            response = json.loads(line)
        except ValueError as e:
            raise SwarmtermError("invalid response") from e
        if not isinstance(response, dict):
            raise SwarmtermError("invalid response")
        return response

    def begin(self) -> Optional[SwarmtermTarget]:
        endpoint = self.find_foreground_endpoint()
        if endpoint is None:
            return None
        try:
            response = self._request(endpoint, {"op": "begin", "requestId": uuid.uuid4().hex}, self.begin_timeout_seconds)
        except SwarmtermError as e:
            self.logger.warning(f"Swarmterm begin failed: {e}")
            return SwarmtermTarget(endpoint=endpoint, pin=None, error=str(e))
        if response.get("ok") and isinstance(response.get("pin"), str):
            return SwarmtermTarget(endpoint=endpoint, pin=response["pin"], name=response.get("name"))
        return SwarmtermTarget(endpoint=endpoint, pin=None, error=str(response.get("reason", "rejected")))

    def insert(self, target: SwarmtermTarget, text: str, submit: bool) -> tuple[bool, Optional[str]]:
        if target.pin is None:
            return False, target.error or "no-target"
        payload = {"op": "insert", "requestId": uuid.uuid4().hex, "pin": target.pin, "text": text, "submit": submit}
        try:
            response = self._request(target.endpoint, payload, self.insert_timeout_seconds)
        except SwarmtermError as e:
            self.logger.warning(f"Swarmterm insert failed: {e}")
            return False, "unknown-outcome" if str(e) == "timeout" else str(e)
        finally:
            target.pin = None
        if response.get("ok"):
            return True, None
        return False, str(response.get("reason", "rejected"))

    def cancel(self, target: Optional[SwarmtermTarget]):
        if target is None or target.pin is None:
            return
        pin, target.pin = target.pin, None
        try:
            self._request(target.endpoint, {"op": "cancel", "pin": pin}, self.begin_timeout_seconds)
        except SwarmtermError as e:
            self.logger.debug(f"Swarmterm cancel failed: {e}")
