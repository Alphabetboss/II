from __future__ import annotations

import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any

from . import config

try:
    import serial  # type: ignore
    _HAVE_SERIAL = True
except Exception:  # pragma: no cover - optional dependency
    serial = None  # type: ignore
    _HAVE_SERIAL = False


@dataclass
class BridgeDiagnostics:
    backend: str
    connected: bool
    port: str
    baud: int
    strict: bool
    last_error: str | None = None
    last_seen_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "connected": self.connected,
            "port": self.port,
            "baud": self.baud,
            "strict": self.strict,
            "last_error": self.last_error,
            "last_seen_at": self.last_seen_at,
        }


class ESP32SerialBridge:
    """Simple line-protocol bridge for a SparkFun ESP32 Thing Plus.

    The Raspberry Pi remains the system brain (UI, camera, decision engine).
    The ESP32 acts as the field controller for time-sensitive GPIO / sensor work.

    Commands (newline-terminated ASCII):
      - PING
      - STATUS
      - SENSORS
      - ALL_OFF
      - ZONE <zone> ON|OFF

    Responses are one-line JSON payloads.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        timeout: float = 2.0,
        boot_wait: float = 2.0,
        strict: bool = False,
    ) -> None:
        self.port = port
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.boot_wait = float(boot_wait)
        self.strict = bool(strict)
        self._lock = RLock()
        self._ser = None
        self._opened_once = False
        self._last_error: str | None = None
        self._last_seen_at: float | None = None
        self._sensor_cache: dict[str, Any] | None = None
        self._sensor_cache_ts = 0.0

    def _ensure_open_locked(self) -> bool:
        if self._ser is not None:
            return True
        if not _HAVE_SERIAL:
            self._last_error = "pyserial is not installed"
            return False
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)  # type: ignore[attr-defined]
            if not self._opened_once:
                time.sleep(max(0.0, self.boot_wait))
                self._opened_once = True
            try:
                self._ser.reset_input_buffer()
                self._ser.reset_output_buffer()
            except Exception:
                pass
            self._last_error = None
            return True
        except Exception as exc:  # pragma: no cover - hardware-specific
            self._ser = None
            self._last_error = str(exc)
            return False

    def _handle_unavailable(self, command: str) -> dict[str, Any]:
        payload = {
            "ok": False,
            "connected": False,
            "command": command,
            "error": self._last_error or "field controller unavailable",
        }
        if self.strict:
            raise RuntimeError(payload["error"])
        return payload

    def _exchange_locked(self, command: str) -> dict[str, Any]:
        if not self._ensure_open_locked():
            return self._handle_unavailable(command)
        assert self._ser is not None
        try:
            self._ser.write((command.strip() + "\n").encode("utf-8"))
            self._ser.flush()
            line = self._ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                self._last_error = f"No response for command: {command}"
                return self._handle_unavailable(command)
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"ok": False, "error": f"Non-JSON response: {line}", "raw": line}
            payload.setdefault("command", command)
            if payload.get("ok"):
                self._last_seen_at = time.time()
                self._last_error = None
            else:
                self._last_error = str(payload.get("error") or f"command failed: {command}")
            return payload
        except Exception as exc:  # pragma: no cover - hardware-specific
            self._last_error = str(exc)
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            return self._handle_unavailable(command)

    def send(self, command: str) -> dict[str, Any]:
        with self._lock:
            return self._exchange_locked(command)

    def ping(self) -> dict[str, Any]:
        return self.send("PING")

    def status(self) -> dict[str, Any]:
        payload = self.send("STATUS")
        payload.setdefault("bridge", self.diagnostics().as_dict())
        return payload

    def set_zone(self, zone: int, on: bool) -> dict[str, Any]:
        state = "ON" if on else "OFF"
        return self.send(f"ZONE {int(zone)} {state}")

    def all_off(self) -> dict[str, Any]:
        return self.send("ALL_OFF")

    def read_sensors(self, max_age: float = 2.0) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            if self._sensor_cache is not None and (now - self._sensor_cache_ts) <= max(0.0, max_age):
                cached = dict(self._sensor_cache)
                cached["cached"] = True
                return cached
            payload = self._exchange_locked("SENSORS")
            if payload.get("ok"):
                self._sensor_cache = dict(payload)
                self._sensor_cache_ts = now
            return payload

    def diagnostics(self) -> BridgeDiagnostics:
        return BridgeDiagnostics(
            backend="esp32_serial",
            connected=self._ser is not None and self._last_error is None,
            port=self.port,
            baud=self.baud,
            strict=self.strict,
            last_error=self._last_error,
            last_seen_at=self._last_seen_at,
        )

    def close(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None


class NullBridge:
    def __init__(self) -> None:
        self._diag = BridgeDiagnostics(
            backend="mock",
            connected=False,
            port="",
            baud=0,
            strict=False,
            last_error="remote field controller disabled",
            last_seen_at=None,
        )

    def ping(self) -> dict[str, Any]:
        return {"ok": False, "connected": False, "error": self._diag.last_error, "command": "PING"}

    def status(self) -> dict[str, Any]:
        return {"ok": False, "connected": False, "error": self._diag.last_error, "bridge": self._diag.as_dict()}

    def set_zone(self, zone: int, on: bool) -> dict[str, Any]:
        return {"ok": False, "connected": False, "zone": zone, "requested": on, "error": self._diag.last_error}

    def all_off(self) -> dict[str, Any]:
        return {"ok": False, "connected": False, "error": self._diag.last_error}

    def read_sensors(self, max_age: float = 2.0) -> dict[str, Any]:
        return {"ok": False, "connected": False, "error": self._diag.last_error}

    def diagnostics(self) -> BridgeDiagnostics:
        return self._diag

    def close(self) -> None:
        return None


_BRIDGE: ESP32SerialBridge | NullBridge | None = None


def get_field_bridge() -> ESP32SerialBridge | NullBridge:
    global _BRIDGE
    if _BRIDGE is None:
        backend = config.HARDWARE_BACKEND
        if backend == "ESP32_SERIAL" or (backend == "AUTO" and config.ESP32_SERIAL_PORT_EXPLICIT):
            _BRIDGE = ESP32SerialBridge(
                port=config.ESP32_SERIAL_PORT,
                baud=config.ESP32_BAUD,
                timeout=config.ESP32_TIMEOUT,
                boot_wait=config.ESP32_BOOT_WAIT,
                strict=config.STRICT_FIELD_IO,
            )
        else:
            _BRIDGE = NullBridge()
    return _BRIDGE
