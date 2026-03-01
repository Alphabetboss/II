from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from . import config
from .ai_engine import DecisionEngine
from .controller import IrrigationController
from .notifications import Notifier
from .schedule import ScheduleStore
from .sensors import SensorSuite
from .utils import append_jsonl, iso_utc, write_json, tail_jsonl
from .vision import VisionEngine


class AutonomousService:
    def __init__(
        self,
        controller: IrrigationController,
        schedule_store: ScheduleStore,
        sensors: SensorSuite,
        vision: VisionEngine,
        notifier: Notifier,
    ) -> None:
        self.controller = controller
        self.schedule_store = schedule_store
        self.sensors = sensors
        self.vision = vision
        self.notifier = notifier
        self.engine = DecisionEngine()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_tick: str | None = None
        self._last_analysis: dict[str, Any] | None = None

    def start(self) -> None:
        if not config.AUTONOMY_ENABLED:
            return
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="ii-autonomy", daemon=True)
            self._thread.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._running = False

    def _log_incident(self, kind: str, message: str, extra: dict[str, Any] | None = None, alert: bool = False) -> None:
        payload = {"ts": iso_utc(), "kind": kind, "message": message, "extra": extra or {}}
        append_jsonl(config.INCIDENT_LOG, payload)
        if alert:
            self.notifier.notify(f"Ingenious Irrigation: {kind}", message, level="warning")

    def latest_telemetry(self) -> dict[str, Any]:
        return self._last_analysis or {
            "ts": self._last_tick,
            "telemetry": None,
            "health": None,
            "people": None,
        }

    def recent_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        return tail_jsonl(config.DECISION_LOG, limit=limit)

    def analyze_once(self, zone: int | None = None) -> dict[str, Any]:
        frame = self.vision.capture_frame()
        people = self.vision.detect_people(frame) if frame is not None else None
        health = self.vision.analyze_health(frame)
        telemetry = self.sensors.read()
        decision = None
        if zone is not None:
            zone_cfg = self.schedule_store.get_zone(zone)
            decision = self.engine.recommend(zone, int(zone_cfg.get("minutes", 10)), telemetry, health)
        payload = {
            "ts": iso_utc(),
            "telemetry": telemetry.as_dict(),
            "health": health.as_dict(),
            "people": people.as_dict() if people is not None else None,
            "decision": decision.as_dict() if decision is not None else None,
        }
        self._last_analysis = payload
        write_json(config.TELEMETRY_FILE, payload)
        return payload

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                self._log_incident("service_error", f"Autonomy loop error: {exc}")
            self._stop_event.wait(config.POLL_SECONDS)

    def _tick(self) -> None:
        now = datetime.now()
        self._last_tick = iso_utc()
        analysis = self.analyze_once()
        telemetry = analysis["telemetry"]
        health = analysis["health"]
        people = analysis["people"] or {"people_present": False, "count": 0, "boxes": []}

        status = self.controller.status()
        pressure = telemetry.get("pressure_psi")
        if pressure is not None and (pressure < config.PRESSURE_LOW_PSI or pressure > config.PRESSURE_HIGH_PSI):
            if status.get("watering"):
                self.controller.stop_all(reason="pressure_fault")
                self._log_incident(
                    "pressure_fault",
                    f"Pressure out of safe range ({pressure:.1f} PSI). Water shut off.",
                    {"pressure_psi": pressure},
                    alert=True,
                )
                return

        if status.get("watering") and people.get("people_present"):
            self.controller.stop_all(reason="person_detected")
            self._log_incident(
                "person_detected",
                f"A person entered the watering area. Water shut off to avoid spraying someone.",
                {"count": people.get("count", 0)},
                alert=False,
            )
            return

        # Only trigger scheduled runs if idle.
        if status.get("watering"):
            return

        due = self.schedule_store.due_zones(now)
        if not due:
            return

        for zone_cfg in due:
            zone = int(zone_cfg["zone"])
            decision = self.engine.recommend(
                zone=zone,
                base_minutes=int(zone_cfg.get("minutes", 10)),
                telemetry=self.sensors.read(),
                health=self.vision.analyze_health(self.vision.capture_frame()),
            )
            record = {
                "ts": iso_utc(),
                "zone": zone,
                "schedule": zone_cfg,
                "decision": decision.as_dict(),
            }
            append_jsonl(config.DECISION_LOG, record)

            if decision.should_skip or decision.adjusted_minutes <= 0:
                self.schedule_store.mark_ran(zone, now)
                if "Standing water" in decision.advisory:
                    self._log_incident("skip_due_to_water", f"Zone {zone}: {decision.advisory}", {"zone": zone}, alert=False)
                continue

            result = self.controller.start_zone(zone, decision.adjusted_minutes, reason="autonomous_schedule")
            if result.get("ok"):
                self.schedule_store.mark_ran(zone, now)
                if health.get("dry_flag"):
                    self.notifier.notify(
                        f"Zone {zone} increased runtime",
                        f"Astra increased zone {zone} to {decision.adjusted_minutes} minutes because the yard looked dry.",
                        level="info",
                    )
                break

    def status(self) -> dict[str, Any]:
        return {
            "autonomy_enabled": config.AUTONOMY_ENABLED,
            "running": self._running,
            "last_tick": self._last_tick,
            "last_analysis": self._last_analysis,
        }
