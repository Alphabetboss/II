from __future__ import annotations

from flask import Flask, jsonify, render_template, request, send_from_directory

from ingenious_irrigation import config
from ingenious_irrigation.astra import AstraAssistant
from ingenious_irrigation.controller import IrrigationController
from ingenious_irrigation.notifications import Notifier
from ingenious_irrigation.schedule import ScheduleStore
from ingenious_irrigation.sensors import SensorSuite
from ingenious_irrigation.service import AutonomousService
from ingenious_irrigation.utils import tail_jsonl
from ingenious_irrigation.vision import VisionEngine
from ingenious_irrigation.field_bus import get_field_bridge

app = Flask(__name__, static_folder=str(config.STATIC_DIR), template_folder=str(config.TEMPLATE_DIR))
app.config["TEMPLATES_AUTO_RELOAD"] = True

schedule_store = ScheduleStore()
controller = IrrigationController(config.ZONE_PINS, active_low=config.ACTIVE_LOW)
sensors = SensorSuite()
vision = VisionEngine()
notifier = Notifier()
service = AutonomousService(controller, schedule_store, sensors, vision, notifier)
astra = AstraAssistant(controller, schedule_store, service)
service.start()


@app.after_request
def _no_cache(resp):
    ct = resp.headers.get("Content-Type", "")
    if "text/html" in ct:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/")
def dashboard():
    return render_template("dashboard.html")


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": service.status(),
        "controller": controller.status(),
        "hardware": controller.hardware_status(),
        "config": {
            "data_root": str(config.DATA_ROOT),
            "hardware_backend": config.HARDWARE_BACKEND,
            "sensor_backend": config.SENSOR_BACKEND,
            "zone_ids": config.ZONE_IDS,
        },
    })


@app.get("/favicon.ico")
def favicon():
    path = config.STATIC_DIR / "favicon.ico"
    if path.exists():
        return send_from_directory(str(config.STATIC_DIR), "favicon.ico")
    return ("", 204)


@app.get("/api/schedule")
def api_get_schedule():
    return jsonify(schedule_store.snapshot())


@app.post("/api/schedule/update")
def api_update_schedule():
    payload = request.get_json(silent=True) or {}
    try:
        zone = int(payload.get("zone", 1))
    except Exception:
        zone = 1
    updates = {
        "minutes": payload.get("minutes"),
        "enabled": payload.get("enabled"),
        "start_time": payload.get("start_time"),
        "frequency": payload.get("frequency"),
        "every_x_days": payload.get("every_x_days"),
        "days_of_week": payload.get("days_of_week"),
    }
    updated = schedule_store.update_zone(zone, **updates)
    return jsonify({"ok": True, "zone": zone, "config": updated})


@app.post("/api/zone/<int:zone>/run")
def api_run_zone(zone: int):
    payload = request.get_json(silent=True) or {}
    minutes = int(payload.get("minutes", schedule_store.get_zone(zone).get("minutes", 10)))
    result = controller.start_zone(zone, minutes, reason="api_manual_run")
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.post("/api/zone/<int:zone>/stop")
def api_stop_zone(zone: int):
    result = controller.stop_zone(zone, reason="api_manual_stop")
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.get("/api/system/status")
def api_system_status():
    return jsonify({
        "controller": controller.status(),
        "service": service.status(),
        "telemetry": service.latest_telemetry(),
        "hardware": controller.hardware_status(),
    })


@app.get("/api/telemetry")
def api_telemetry():
    return jsonify(service.analyze_once())


@app.get("/api/field/ping")
def api_field_ping():
    return jsonify(get_field_bridge().ping())


@app.get("/api/field/status")
def api_field_status():
    payload = get_field_bridge().status()
    payload["controller_backend"] = controller.status().get("backend")
    return jsonify(payload)


@app.get("/api/field/sensors")
def api_field_sensors():
    return jsonify(get_field_bridge().read_sensors())


@app.post("/api/zone/<int:zone>/analyze")
def api_analyze_zone(zone: int):
    return jsonify(service.analyze_once(zone=zone))


@app.get("/api/decisions")
def api_decisions():
    limit = int(request.args.get("limit", 100))
    return jsonify(service.recent_decisions(limit=limit))


@app.get("/api/incidents")
def api_incidents():
    limit = int(request.args.get("limit", 100))
    return jsonify(tail_jsonl(config.INCIDENT_LOG, limit=limit))


@app.post("/astra/chat")
@app.post("/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    reply = astra.respond(message)
    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(host=config.APP_HOST, port=config.APP_PORT, debug=False)
