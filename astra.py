from __future__ import annotations

import re
from typing import Any

from .controller import IrrigationController
from .schedule import ScheduleStore
from .service import AutonomousService


class AstraAssistant:
    def __init__(self, controller: IrrigationController, schedule_store: ScheduleStore, service: AutonomousService) -> None:
        self.controller = controller
        self.schedule_store = schedule_store
        self.service = service

    def _schedule_summary(self) -> str:
        data = self.schedule_store.snapshot()
        parts = []
        for zone, cfg in sorted(data.get("zones", {}).items(), key=lambda item: int(item[0])):
            freq = cfg.get("frequency", "daily").replace("_", " ")
            parts.append(f"Zone {zone}: {cfg.get('start_time')} for {cfg.get('minutes')} min ({freq})")
        return " | ".join(parts)

    def respond(self, text: str) -> str:
        msg = (text or "").strip()
        if not msg:
            return "I’m Astra. Ask me to run a zone, stop watering, check plant health, or summarize the schedule."

        lower = msg.lower()

        m = re.search(r"(?:start|run|water)\s+zone\s*(\d+)\s*(?:for\s*(\d+)\s*(?:minutes?|mins?))?", lower)
        if m:
            zone = int(m.group(1))
            minutes = int(m.group(2) or self.schedule_store.get_zone(zone).get("minutes", 10))
            result = self.controller.start_zone(zone, minutes, reason="astra_command")
            if result.get("ok"):
                return f"Starting zone {zone} for {minutes} minutes now."
            return f"I couldn’t start zone {zone}: {result.get('error', 'unknown error')}."

        m = re.search(r"set\s+zone\s*(\d+)\s*(?:to|for)?\s*(\d+)\s*(?:minutes?|mins?)", lower)
        if m:
            zone = int(m.group(1))
            minutes = int(m.group(2))
            self.schedule_store.update_zone(zone, minutes=minutes)
            return f"Zone {zone} is now set to {minutes} minutes."

        m = re.search(r"(?:stop|shut off|cancel).*(?:zone\s*(\d+))?", lower)
        if m and any(word in lower for word in ["stop", "shut off", "cancel"]):
            zone = int(m.group(1)) if m.group(1) else None
            result = self.controller.stop_zone(zone=zone, reason="astra_stop")
            if result.get("ok"):
                return "Watering stopped."
            return f"I couldn’t stop that zone: {result.get('error', 'unknown error')}."

        if "schedule" in lower or "timer" in lower:
            return self._schedule_summary()

        if "status" in lower or "what's running" in lower or "whats running" in lower:
            status = self.controller.status()
            if status.get("watering"):
                return f"Zone {status['active_zone']} is running right now."
            return "No zone is running right now."

        if any(key in lower for key in ["health", "analyze", "camera", "grass", "plants"]):
            zone_match = re.search(r"zone\s*(\d+)", lower)
            zone = int(zone_match.group(1)) if zone_match else 1
            analysis = self.service.analyze_once(zone=zone)
            health = analysis["health"]
            decision = analysis["decision"]
            return (
                f"Zone {zone}: {health['summary']} Remedy: {health['remedy']} "
                f"Hydration score {decision['score']}/10. {decision['advisory']}"
            )

        if any(key in lower for key in ["person", "people", "someone got wet", "someone walks"]):
            analysis = self.service.analyze_once()
            people = analysis.get("people") or {}
            if people.get("people_present"):
                return f"I see {people.get('count', 1)} person in the yard. I will keep watering off in that area."
            return "No person is currently detected in the last camera frame."

        if "help" in lower or "what can you do" in lower:
            return (
                "I can run or stop zones, change runtimes, summarize schedules, analyze the lawn from camera frames, "
                "and automatically stop watering if someone enters the yard or pressure looks unsafe."
            )

        return (
            "I’m focused on irrigation, plant health, and safety. Try: ‘run zone 2 for 8 minutes’, "
            "‘set zone 1 to 12 minutes’, or ‘analyze zone 3’."
        )
