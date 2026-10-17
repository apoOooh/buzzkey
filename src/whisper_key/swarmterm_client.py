import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

PROTOCOL_VERSION = 1
# Negotiated inside `hello`; the envelope above stays 1 so old Swarmterm
# builds keep working. Swarmterm 1.9.0 answers hello with bad-request.
PROTO = 2
CAPABILITIES = ["indicator"]
# A held dictation may stay silent this long; Swarmterm pushes `expired`
# at 120 s and closes the connection first.
HOLD_TIMEOUT_SECONDS = 130.0
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
    # Set when the pin lives on a held connection ("indicator").
    session: Optional["DictationSession"] = None


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
        self._timeout = timeout_seconds
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

    def _wait(self, overlapped, deadline: float) -> int:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        result = self._win32event.WaitForSingleObject(overlapped.hEvent, remaining)
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

    def _deadline(self, timeout_seconds: Optional[float]) -> float:
        # Per operation: a held dictation connection lives far longer than
        # any single request may take.
        return time.monotonic() + (self._timeout if timeout_seconds is None else timeout_seconds)

    def send_line(self, line: str, timeout_seconds: Optional[float] = None):
        deadline = self._deadline(timeout_seconds)
        overlapped = self._overlapped()
        try:
            self._win32file.WriteFile(self._handle, (line + "\n").encode("utf-8"), overlapped)
        except self._pywintypes.error as e:
            raise SwarmtermError(f"write failed: {e.strerror}") from e
        self._wait(overlapped, deadline)

    def read_line(self, timeout_seconds: Optional[float] = None) -> str:
        deadline = self._deadline(timeout_seconds)
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
            count = self._wait(overlapped, deadline)
            if count == 0:
                raise SwarmtermError("connection closed")
            data += bytes(buffer[:count])
        return data.split(b"\n", 1)[0].decode("utf-8")

    def close(self):
        try:
            self._handle.Close()
        except Exception:
            pass


class DictationSession:
    """One held connection for one dictation (capability "indicator").

    A single reader thread owns all reads on the handle: lines with an
    `event` key are pushes, every other line answers the one request in
    flight. Requests are serialised by a lock, so that rule holds.
    """

    def __init__(self, transport, token: str, on_cancelled: Callable[[str, str], None], logger):
        self._transport = transport
        self._token = token
        self._on_cancelled = on_cancelled
        self._logger = logger
        self._answers: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self.cancelled_reason: Optional[str] = None
        self._closed = False
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        while True:
            try:
                line = self._transport.read_line(HOLD_TIMEOUT_SECONDS)
            except SwarmtermError as e:
                self._answers.put(e)
                return
            try:
                message = json.loads(line)
            except ValueError:
                self._answers.put(SwarmtermError("invalid response"))
                continue
            if isinstance(message, dict) and "event" in message:
                if message.get("event") == "cancelled":
                    reason = str(message.get("reason", ""))
                    self.cancelled_reason = reason
                    pin = str(message.get("pin", ""))
                    self._logger.info(f"Swarmterm cancelled the dictation: {reason}")
                    # Own thread: the handler may call back into this
                    # session, whose answers only this reader delivers.
                    threading.Thread(target=self._on_cancelled, args=(pin, reason), daemon=True).start()
                continue
            self._answers.put(message)

    def request(self, payload: dict, timeout_seconds: float) -> dict:
        with self._lock:
            if self._closed:
                raise SwarmtermError("connection closed")
            # Anything queued now arrived while no request was open: never
            # let it pass as the answer to this one. A queued error means
            # the reader has ended.
            while True:
                try:
                    stale = self._answers.get_nowait()
                except queue.Empty:
                    break
                if isinstance(stale, Exception):
                    self._closed = True
                    raise stale
            self._transport.send_line(
                json.dumps({"v": PROTOCOL_VERSION, "token": self._token, **payload}), timeout_seconds)
            try:
                answer = self._answers.get(timeout=timeout_seconds)
            except queue.Empty as e:
                raise SwarmtermError("timeout") from e
        if isinstance(answer, Exception):
            raise answer
        if not isinstance(answer, dict):
            raise SwarmtermError("invalid response")
        return answer

    def close(self):
        self._closed = True
        self._transport.close()


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
        # Called (from a worker thread) when the user pressed Stop in the
        # Swarmterm pane: the recording is to be discarded.
        self.on_user_cancel: Optional[Callable[[], None]] = None
        # Pipes that answered hello like Swarmterm 1.9.0 (proto 1); their
        # name changes with every Swarmterm launch.
        self._legacy_pipes: set[str] = set()

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

    def _handle_cancelled(self, pin: str, reason: str):
        if reason == "user" and self.on_user_cancel is not None:
            try:
                self.on_user_cancel()
            except Exception as e:
                self.logger.error(f"Swarmterm stop handler failed: {e}")

    def _begin_held(self, endpoint: SwarmtermEndpoint) -> Optional[SwarmtermTarget]:
        """hello + begin on one connection. None = endpoint speaks proto 1."""
        transport = self.transport_factory(endpoint.pipe, self.begin_timeout_seconds)
        try:
            transport.send_line(json.dumps({
                "v": PROTOCOL_VERSION, "token": endpoint.token, "op": "hello", "requestId": uuid.uuid4().hex,
                "proto": PROTO, "app": "buzzkey", "version": _app_version(), "capabilities": CAPABILITIES,
            }), self.begin_timeout_seconds)
            hello = json.loads(transport.read_line(self.begin_timeout_seconds))
        except (SwarmtermError, ValueError):
            transport.close()
            raise
        if not isinstance(hello, dict) or not hello.get("ok") or "indicator" not in (hello.get("capabilities") or []):
            transport.close()
            if not (isinstance(hello, dict) and hello.get("ok")):
                self.logger.info(f"Swarmterm speaks proto 1 ({hello.get('reason') if isinstance(hello, dict) else hello})")
            self._legacy_pipes.add(endpoint.pipe)
            return None
        session = DictationSession(transport, endpoint.token, self._handle_cancelled, self.logger)
        try:
            response = session.request({"op": "begin", "requestId": uuid.uuid4().hex}, self.begin_timeout_seconds)
        except SwarmtermError:
            session.close()
            raise
        if response.get("ok") and isinstance(response.get("pin"), str):
            return SwarmtermTarget(endpoint=endpoint, pin=response["pin"], name=response.get("name"), session=session)
        session.close()
        return SwarmtermTarget(endpoint=endpoint, pin=None, error=str(response.get("reason", "rejected")))

    def begin(self) -> Optional[SwarmtermTarget]:
        endpoint = self.find_foreground_endpoint()
        if endpoint is None:
            return None
        if endpoint.pipe not in self._legacy_pipes:
            try:
                target = self._begin_held(endpoint)
            except (SwarmtermError, ValueError) as e:
                self.logger.warning(f"Swarmterm begin failed: {e}")
                return SwarmtermTarget(endpoint=endpoint, pin=None, error=str(e))
            if target is not None:
                return target
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
        session = target.session
        try:
            if session is not None:
                if session.cancelled_reason is not None:
                    # The pin is gone already; "user" means discard.
                    return False, "cancelled-by-user" if session.cancelled_reason == "user" else session.cancelled_reason
                response = session.request(payload, self.insert_timeout_seconds)
            else:
                response = self._request(target.endpoint, payload, self.insert_timeout_seconds)
        except SwarmtermError as e:
            self.logger.warning(f"Swarmterm insert failed: {e}")
            return False, "unknown-outcome" if str(e) == "timeout" else str(e)
        finally:
            target.pin = None
            if session is not None:
                session.close()
                target.session = None
        if response.get("ok"):
            return True, None
        return False, str(response.get("reason", "rejected"))

    def cancel(self, target: Optional[SwarmtermTarget]):
        if target is None or target.pin is None:
            return
        pin, target.pin = target.pin, None
        session, target.session = target.session, None
        try:
            if session is not None:
                if session.cancelled_reason is None:
                    session.request({"op": "cancel", "requestId": uuid.uuid4().hex, "pin": pin}, self.begin_timeout_seconds)
            else:
                self._request(target.endpoint, {"op": "cancel", "pin": pin}, self.begin_timeout_seconds)
        except SwarmtermError as e:
            self.logger.debug(f"Swarmterm cancel failed: {e}")
        finally:
            if session is not None:
                session.close()



# Status feed (Swarmterm SWA-41): one long-lived connection per running
# Swarmterm that only receives "agent finished / needs input" pushes. Kept
# apart from dictation connections on purpose; Swarmterm refuses dictation
# on a subscribed connection.
FEED_CAPABILITIES = ["turns"]
# Swarmterm closes a silent feed after 90 s; a ping every 30 s keeps it.
PING_INTERVAL_SECONDS = 30.0
FEED_OPEN_TIMEOUT_SECONDS = 2.0
# How often running Swarmterm instances are looked up again.
FEED_DISCOVERY_SECONDS = 5.0
TURN_KINDS = ("done", "needs_input")


@dataclass
class TurnEvent:
    terminal_id: str
    pane_name: str
    agent: str
    kind: str
    seq: int


def _parse_turn(message: dict) -> Optional[TurnEvent]:
    terminal_id, pane, agent = message.get("terminalId"), message.get("paneName"), message.get("agent")
    kind, seq = message.get("kind"), message.get("seq")
    if not (isinstance(terminal_id, str) and isinstance(pane, str) and isinstance(agent, str)):
        return None
    # Unknown kinds (a newer Swarmterm) are skipped, never guessed.
    if kind not in TURN_KINDS or not isinstance(seq, int) or isinstance(seq, bool):
        return None
    return TurnEvent(terminal_id=terminal_id, pane_name=pane, agent=agent, kind=kind, seq=seq)


class TurnSubscription:
    """hello + subscribe, then pushes until the connection ends.

    One thread owns the handle: reads wait at most PING_INTERVAL_SECONDS,
    an idle read sends a ping. Lines without `event` are ping answers.
    """

    def __init__(self, transport, endpoint: SwarmtermEndpoint, logger=None):
        self._transport = transport
        self._endpoint = endpoint
        self._logger = logger or logging.getLogger(__name__)
        self._last_seq: Optional[int] = None

    def _send(self, payload: dict):
        self._transport.send_line(json.dumps({"v": PROTOCOL_VERSION, "token": self._endpoint.token, **payload}),
                                  FEED_OPEN_TIMEOUT_SECONDS)

    def _answer(self) -> dict:
        try:
            answer = json.loads(self._transport.read_line(FEED_OPEN_TIMEOUT_SECONDS))
        except ValueError as e:
            raise SwarmtermError("invalid response") from e
        if not isinstance(answer, dict):
            raise SwarmtermError("invalid response")
        return answer

    def open(self) -> Optional[int]:
        """The server's current seq, or None if it offers no feed."""
        try:
            self._send({"op": "hello", "requestId": uuid.uuid4().hex, "proto": PROTO, "app": "buzzkey",
                        "version": _app_version(), "capabilities": FEED_CAPABILITIES})
            hello = self._answer()
            if not hello.get("ok") or "turns" not in (hello.get("capabilities") or []):
                self._transport.close()
                return None
            self._send({"op": "subscribe", "requestId": uuid.uuid4().hex})
            answer = self._answer()
        except SwarmtermError:
            self._transport.close()
            raise
        if not answer.get("ok") or not isinstance(answer.get("seq"), int):
            self._logger.info(f"Swarmterm refused the status feed: {answer.get('reason')}")
            self._transport.close()
            return None
        self._last_seq = answer["seq"]
        return self._last_seq

    def pump(self, on_turn: Callable[[TurnEvent], None]):
        try:
            while True:
                try:
                    line = self._transport.read_line(PING_INTERVAL_SECONDS)
                except SwarmtermError as e:
                    if str(e) != "timeout":
                        return
                    self._send({"op": "ping", "requestId": uuid.uuid4().hex})
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict) or message.get("event") != "turn":
                    continue
                event = _parse_turn(message)
                if event is None:
                    continue
                if self._last_seq is not None and event.seq != self._last_seq + 1:
                    self._logger.info(f"Swarmterm status feed skipped from {self._last_seq} to {event.seq}")
                self._last_seq = event.seq
                try:
                    on_turn(event)
                except Exception as e:
                    self._logger.error(f"Swarmterm turn handler failed: {e}")
        except SwarmtermError:
            return
        finally:
            self._transport.close()


class TurnFeed:
    """Keeps one TurnSubscription per running Swarmterm (installed app and
    dev build alike — announcements do not depend on the foreground window).
    A lost connection is retried on the next discovery pass with the then
    current pipe name and token."""

    def __init__(self, client: "SwarmtermClient", on_turn: Callable[[TurnEvent], None],
                 transport_factory: Callable[[str, float], object] = PipeTransport):
        self._client = client
        self._on_turn = on_turn
        self._transport_factory = transport_factory
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._discover_loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _discover_loop(self):
        while not self._stop.is_set():
            for endpoint in self._client._read_endpoints():
                with self._lock:
                    if endpoint.pipe in self._active:
                        continue
                    self._active.add(endpoint.pipe)
                threading.Thread(target=self._run, args=(endpoint,), daemon=True).start()
            self._stop.wait(FEED_DISCOVERY_SECONDS)

    def _run(self, endpoint: SwarmtermEndpoint):
        try:
            transport = self._transport_factory(endpoint.pipe, FEED_OPEN_TIMEOUT_SECONDS)
            sub = TurnSubscription(transport, endpoint, self._client.logger)
            if sub.open() is not None:
                sub.pump(self._on_turn)
        except SwarmtermError as e:
            self._client.logger.debug(f"Swarmterm status feed: {e}")
        finally:
            # A refusing server is asked again after the next pass; one
            # that has gone away leaves a stale discovery file only until
            # it is removed on shutdown.
            self._stop.wait(FEED_DISCOVERY_SECONDS)
            with self._lock:
                self._active.discard(endpoint.pipe)

def _app_version() -> str:
    try:
        from .utils import get_version
        return str(get_version())[:64]
    except Exception:
        return "unknown"
