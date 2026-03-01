# Ingenious Irrigation — Pi 5 + ESP32 Field Controller Build

This build is tuned for your current hardware:

- **Raspberry Pi 5 (Linux)** = the brain (dashboard, camera vision, decision engine, logs)
- **SparkFun ESP32 Thing Plus** = the field controller (relay + DHT11 + fast GPIO work)
- **Inland SRD-05VDC-SL-C relay module** = valve switching (currently 1 zone)
- **Touchscreen on the Pi** = local dashboard / control panel
- **S2Pi Tiny NVMe** = ideal place for logs, snapshots, and future training data

That split is the right move. The Pi handles AI + camera work. The ESP32 handles the physical edge I/O.

## What changed in this build

- Added a **serial bridge** so the Pi can command the ESP32 over USB.
- Added an **ESP32 field-controller firmware sketch** in `firmware/esp32_field_controller/esp32_field_controller.ino`.
- Added field-controller API endpoints:
  - `GET /api/field/ping`
  - `GET /api/field/status`
  - `GET /api/field/sensors`
- Added `II_DATA_ROOT` so you can store logs/telemetry on the NVMe instead of the SD card.
- Set the default `.env.example` for your current **one-relay / one-zone** setup.
- Kept the existing autonomous watering, person detection shutoff, pressure safety logic, and decision logging.

## Recommended wiring architecture

### Raspberry Pi 5
Use the Pi for:
- Flask dashboard
- camera stream(s)
- OpenCV / plant health analysis
- schedule + decision logs
- touchscreen UI
- long-term storage on the NVMe

### SparkFun ESP32 Thing Plus
Use the ESP32 for:
- relay trigger output
- DHT11 reading
- future soil or pressure sensor inputs
- real-time field-side safety logic if you add more edge sensors later

### Important relay note
Your **Inland SRD-05VDC-SL-C relay module is a 5V relay board**.
The SparkFun ESP32 Thing Plus uses **3.3V GPIO and its I/O is not 5V-tolerant**, so you should power the relay module from a 5V rail and keep a common ground.

Because many 5V relay modules *may* trigger unreliably from 3.3V logic, treat this as a bench-test item. If the relay does not switch cleanly, use a transistor/driver stage, an opto-isolated module known to accept 3.3V logic, or move the relay control to the Pi GPIO instead. This is a hardware limitation, not a software one.

### DHT11 note
The DHT11 can be read from the Pi, but in practice it is usually cleaner to let the ESP32 read it and send the values to the Pi. The DHT line also needs a pull-up resistor.

## Default deployment mode

The included `.env.example` assumes:

- `II_HARDWARE_BACKEND=ESP32_SERIAL`
- `II_SENSOR_BACKEND=ESP32`
- `II_ZONE_IDS=1`
- serial link on `/dev/ttyACM0`

That means:
- the **Pi** runs the app
- the **ESP32** receives relay commands and returns sensor data
- the app still works on your Pi even before every future sensor is added

## Field-controller protocol

The Pi sends simple newline commands over USB serial:

- `PING`
- `STATUS`
- `SENSORS`
- `ALL_OFF`
- `ZONE 1 ON`
- `ZONE 1 OFF`

The ESP32 replies with a single-line JSON payload.

This keeps the link simple and durable—no heavy parser required on the microcontroller.

## Install on Raspberry Pi 5

1. Copy this folder to the Pi.
2. Create a Python virtual environment.
3. Install dependencies.
4. Copy `.env.example` to `.env`.
5. Plug the ESP32 into the Pi over USB.
6. Flash the included ESP32 sketch.
7. Start `python app.py`.

### Suggested Raspberry Pi packages

- `sudo apt update`
- `sudo apt install python3-gpiozero python3-pip libgpiod2`

Then install the Python requirements in your venv.

## Touchscreen / local kiosk

If you want the touchscreen to act like a built-in control panel, the easiest path is to auto-launch Chromium in app mode to `http://localhost:5051` after login.
A starter script is included in `scripts/start_touchscreen_dashboard.sh`.

## NVMe usage

Set:

- `II_DATA_ROOT=/path/to/your/nvme_mount/ingenious_irrigation`

That moves:
- `data/`
- `logs/`
- `snapshots/`

off the SD card and onto your NVMe.

## API endpoints

### Existing core endpoints
- `GET /health`
- `GET /api/schedule`
- `POST /api/schedule/update`
- `POST /api/zone/<zone>/run`
- `POST /api/zone/<zone>/stop`
- `GET /api/system/status`
- `GET /api/telemetry`
- `POST /api/zone/<zone>/analyze`
- `GET /api/decisions`
- `GET /api/incidents`
- `POST /astra/chat`

### New field-controller endpoints
- `GET /api/field/ping`
- `GET /api/field/status`
- `GET /api/field/sensors`

## Hardware you can add next without redesigning the software

- more relay channels (expand to multi-zone)
- capacitive soil moisture sensor
- pressure transducer
- flow sensor
- leak detector
- per-zone camera views

## What I would do next for your exact bench setup

1. Run this build with **1 zone only**.
2. Put the **DHT11 on the ESP32**.
3. Bench-test whether that specific relay module triggers reliably from the ESP32.
4. If the relay input is flaky with 3.3V logic, switch the relay output to the Pi or add a transistor driver.
5. Once the relay path is proven, add moisture + pressure sensing.

That gets you to a stable, real-world prototype without overcomplicating the first deployment.
