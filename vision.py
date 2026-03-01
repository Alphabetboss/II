from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from . import config

try:
    from ultralytics import YOLO
    _HAVE_YOLO = True
except Exception:
    YOLO = None  # type: ignore
    _HAVE_YOLO = False


@dataclass
class HealthAssessment:
    greenness_score: float
    water_flag: bool
    dry_flag: bool
    yellow_flag: bool
    brown_flag: bool
    summary: str
    remedy: str
    raw: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "greenness_score": self.greenness_score,
            "water_flag": self.water_flag,
            "dry_flag": self.dry_flag,
            "yellow_flag": self.yellow_flag,
            "brown_flag": self.brown_flag,
            "summary": self.summary,
            "remedy": self.remedy,
            "raw": self.raw,
        }


@dataclass
class PeopleAssessment:
    people_present: bool
    count: int
    boxes: list[list[int]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "people_present": self.people_present,
            "count": self.count,
            "boxes": self.boxes,
        }


class CameraManager:
    def __init__(self) -> None:
        self.index = config.CAMERA_INDEX
        self.url = config.CAMERA_URL
        self.width = config.CAMERA_WIDTH
        self.height = config.CAMERA_HEIGHT

    def capture(self) -> np.ndarray | None:
        source: object = self.url if self.url else self.index
        cap = cv2.VideoCapture(source)
        if self.width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        started = time.time()
        try:
            while time.time() - started < config.CAMERA_TIMEOUT:
                ok, frame = cap.read()
                if ok and frame is not None:
                    return frame
            return None
        finally:
            cap.release()

    def save_snapshot(self, frame: np.ndarray, prefix: str = "snapshot") -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = config.SNAPSHOT_DIR / f"{prefix}_{ts}.jpg"
        cv2.imwrite(str(path), frame)
        return path


class VisionEngine:
    def __init__(self) -> None:
        self.camera = CameraManager()
        self._people_hog = None
        self._model = None
        if config.PEOPLE_DETECTION_ENABLED:
            self._people_hog = cv2.HOGDescriptor()
            self._people_hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        if _HAVE_YOLO and config.YOLO_MODEL_PATH and Path(config.YOLO_MODEL_PATH).exists():
            try:
                self._model = YOLO(config.YOLO_MODEL_PATH)
            except Exception:
                self._model = None

    def capture_frame(self) -> np.ndarray | None:
        return self.camera.capture()

    def detect_people(self, frame: np.ndarray) -> PeopleAssessment:
        if frame is None or self._people_hog is None:
            return PeopleAssessment(False, 0, [])
        work = frame
        h, w = frame.shape[:2]
        scale = 1.0
        if w > 640:
            scale = 640.0 / w
            work = cv2.resize(frame, (640, int(h * scale)))
        boxes, weights = self._people_hog.detectMultiScale(
            work,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
        )
        kept: list[list[int]] = []
        for (x, y, bw, bh), weight in zip(boxes, weights):
            if float(weight) < config.PEOPLE_MIN_CONFIDENCE:
                continue
            if scale != 1.0:
                x = int(x / scale)
                y = int(y / scale)
                bw = int(bw / scale)
                bh = int(bh / scale)
            kept.append([int(x), int(y), int(x + bw), int(y + bh)])
        return PeopleAssessment(bool(kept), len(kept), kept)

    def _analyze_with_yolo(self, frame: np.ndarray) -> tuple[Optional[dict[str, Any]], dict[str, float]]:
        if self._model is None:
            return None, {"green": 0.0, "water": 0.0, "dead": 0.0}
        h, w = frame.shape[:2]
        total = float(max(1, h * w))
        areas = {"green": 0.0, "water": 0.0, "dead": 0.0}
        raw: dict[str, Any] = {"method": "yolo"}
        try:
            result = self._model.predict(frame, imgsz=640, conf=config.YOLO_CONF, verbose=False)[0]
            names = result.names
            if getattr(result, "boxes", None) is not None:
                for box in result.boxes:
                    cls = int(box.cls[0].item())
                    name = str(names.get(cls, cls)).lower()
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    area = max(0.0, (x2 - x1) * (y2 - y1)) / total
                    if "grass" in name or "green" in name:
                        areas["green"] += area
                    elif "water" in name or "mud" in name or "puddle" in name:
                        areas["water"] += area
                    elif "dead" in name or "brown" in name or "dry" in name:
                        areas["dead"] += area
            raw["areas"] = {k: round(v, 4) for k, v in areas.items()}
            return raw, areas
        except Exception as exc:
            return {"method": "yolo_failed", "error": str(exc)}, areas

    def analyze_health(self, frame: np.ndarray | None) -> HealthAssessment:
        if frame is None:
            return HealthAssessment(
                greenness_score=0.5,
                water_flag=False,
                dry_flag=False,
                yellow_flag=False,
                brown_flag=False,
                summary="No camera frame available.",
                remedy="Check camera wiring or camera URL before relying on vision automation.",
                raw={"error": "no_frame"},
            )

        yolo_raw, yolo_areas = self._analyze_with_yolo(frame)

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        total = float(frame.shape[0] * frame.shape[1])

        green_mask = cv2.inRange(hsv, np.array([30, 40, 40]), np.array([85, 255, 255]))
        yellow_mask = cv2.inRange(hsv, np.array([18, 70, 70]), np.array([35, 255, 255]))
        brown_mask = cv2.inRange(hsv, np.array([5, 50, 30]), np.array([20, 255, 220]))
        blue_mask = cv2.inRange(hsv, np.array([90, 40, 40]), np.array([140, 255, 255]))

        green_ratio = float(np.count_nonzero(green_mask)) / total
        yellow_ratio = float(np.count_nonzero(yellow_mask)) / total
        brown_ratio = float(np.count_nonzero(brown_mask)) / total
        water_ratio = float(np.count_nonzero(blue_mask)) / total

        if yolo_raw and yolo_raw.get("method") == "yolo":
            green_ratio = max(green_ratio, min(1.0, yolo_areas["green"]))
            water_ratio = max(water_ratio, min(1.0, yolo_areas["water"]))
            brown_ratio = max(brown_ratio, min(1.0, yolo_areas["dead"]))

        dry_flag = brown_ratio > 0.08 or (green_ratio < 0.18 and yellow_ratio > 0.05)
        water_flag = water_ratio > 0.03
        yellow_flag = yellow_ratio > 0.08
        brown_flag = brown_ratio > 0.06

        if water_flag:
            summary = "Standing water or oversaturated turf detected."
            remedy = "Reduce or skip watering, inspect drainage, and verify no valve is stuck open."
        elif dry_flag:
            summary = "Dry or stressed turf detected."
            remedy = "Increase runtime 15–30%, verify nozzle coverage, and inspect for clogged heads."
        elif yellow_flag:
            summary = "Possible nutrient stress or early plant sickness detected."
            remedy = "Inspect for pests/fungus, apply an appropriate lawn nutrient, and verify watering is even."
        else:
            summary = "Grass and planting beds look healthy."
            remedy = "Keep the current schedule and continue monitoring for hot spots or puddling."

        raw = {
            "method": "hsv+yolo" if yolo_raw else "hsv",
            "green_ratio": round(green_ratio, 4),
            "yellow_ratio": round(yellow_ratio, 4),
            "brown_ratio": round(brown_ratio, 4),
            "water_ratio": round(water_ratio, 4),
        }
        if yolo_raw:
            raw["yolo"] = yolo_raw

        return HealthAssessment(
            greenness_score=max(0.0, min(1.0, green_ratio)),
            water_flag=water_flag,
            dry_flag=dry_flag,
            yellow_flag=yellow_flag,
            brown_flag=brown_flag,
            summary=summary,
            remedy=remedy,
            raw=raw,
        )
