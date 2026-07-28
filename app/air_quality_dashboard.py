"""
Air Quality Monitor — Desktop Dashboard
========================================
Reads a 7-metric JSON line from the sensor node's Serial output, shows it on
a Tkinter dashboard (Arabic UI), optionally pushes readings to a Blynk
dashboard, and optionally asks an LLM for a plain-language environmental
summary every few minutes.

This is a consolidation of ~15 near-duplicate scripts from the original
prototype (one script per feature combination: DHT-only, +Blynk, +UV,
+ThingSpeak, +OpenAI, ...). All of that becomes feature flags here, driven
by which environment variables are set — same firmware, same dashboard, no
duplicated files.

SECURITY NOTE
-------------
An earlier prototype had a live OpenAI API key and a live Blynk auth token
hardcoded directly in the source file. That is why every credential here is
read from the environment (or a local, git-ignored `.env` file) and NEVER
written into this file. See ../.env.example for the variables this script
looks for. If you are the original author of this project: rotate any key
that was ever committed to source control before reusing it.

USAGE
-----
    python air_quality_dashboard.py                 # real hardware on SERIAL_PORT
    python air_quality_dashboard.py --simulate       # no hardware needed, fake data
"""

import argparse
import json
import os
import random
import sys
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import PhotoImage

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can also be set directly

try:
    from bidi.algorithm import get_display
    from arabic_reshaper import reshape

    def rtl(text: str) -> str:
        """Reshape + reorder Arabic text so Tkinter renders it correctly."""
        return get_display(reshape(text))
except ImportError:
    def rtl(text: str) -> str:
        return text  # fallback: readable but not reshaped, if libs are missing

ASSETS_DIR = Path(__file__).parent / "assets"

# ---------------- Configuration (all from environment, nothing hardcoded) ----------------
SERIAL_PORT = os.environ.get("AQM_SERIAL_PORT", "COM3")
BAUD_RATE = int(os.environ.get("AQM_BAUD_RATE", "115200"))
BLYNK_AUTH_TOKEN = os.environ.get("BLYNK_AUTH_TOKEN", "")       # empty = upload disabled
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")            # empty = analysis disabled
ANALYSIS_INTERVAL_S = int(os.environ.get("AQM_ANALYSIS_INTERVAL_S", "300"))
BLYNK_UPLOAD_INTERVAL_S = int(os.environ.get("AQM_BLYNK_INTERVAL_S", "5"))

METRIC_LABELS = [
    ("temperature", "درجة الحرارة", "°C", "Temperature.png"),
    ("humidity", "نسبة الرطوبة", "%", "Humidity.png"),
    ("uv", "الأشعة فوق البنفسجية", "UV Index", "UV.png"),
    ("dust", "الغبار العالق", "µg/m³", "Dust.png"),
    ("co2", "ثاني أكسيد الكربون", "ppm", "Co2.png"),
    ("tvoc", "المركبات العضوية المتطايرة", "ppb", "Particles.png"),
    ("h2s", "غاز كبريتيد الهيدروجين", "ppm", "H2S.png"),
]

DEFAULT_ANALYSIS_TEXT = rtl(
    "مرحباً! أنا مساعدك الذكي لتحليل بيانات الجو. بانتظار أول قراءة..."
)


class AirQualityDashboard:
    def __init__(self, root: tk.Tk, simulate: bool = False):
        self.root = root
        self.simulate = simulate
        self.data = {key: 0 for key, *_ in METRIC_LABELS}
        self.last_analysis_time = datetime.now() - timedelta(seconds=ANALYSIS_INTERVAL_S)
        self.last_blynk_time = datetime.now() - timedelta(seconds=BLYNK_UPLOAD_INTERVAL_S)
        self.analysis_text = DEFAULT_ANALYSIS_TEXT

        self.blynk_enabled = bool(BLYNK_AUTH_TOKEN)
        self.openai_enabled = bool(OPENAI_API_KEY)
        self._openai_client = None
        if self.openai_enabled:
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=OPENAI_API_KEY)
            except ImportError:
                print("openai package not installed — analysis disabled.", file=sys.stderr)
                self.openai_enabled = False

        self.serial_conn = None
        if not self.simulate:
            self._open_serial()

        self._build_ui()
        self._tick()

    # ---------------- Serial ----------------
    def _open_serial(self):
        try:
            import serial
            self.serial_conn = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        except Exception as exc:
            print(f"Could not open {SERIAL_PORT}: {exc}. Falling back to --simulate mode.",
                  file=sys.stderr)
            self.simulate = True

    def _read_serial_line(self):
        if self.simulate:
            return {
                "temperature": round(random.uniform(20, 32), 1),
                "humidity": round(random.uniform(30, 70), 1),
                "uv": round(random.uniform(0, 8), 2),
                "dust": round(random.uniform(0, 0.3), 2),
                "co2": random.randint(400, 900),
                "tvoc": random.randint(0, 60),
                "h2s": round(random.uniform(0, 4), 2),
            }
        try:
            if self.serial_conn and self.serial_conn.in_waiting > 0:
                line = self.serial_conn.readline().decode(errors="ignore").strip()
                if line:
                    return json.loads(line)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Serial read error: {exc}", file=sys.stderr)
        return None

    # ---------------- UI ----------------
    def _build_ui(self):
        self.root.title(rtl("مراقب الجو الذكي"))
        self.root.geometry("1400x820")
        self.root.configure(bg="white")

        header = tk.Label(self.root, text=rtl("مراقب الجو الذكي"),
                           font=("Arial", 30, "bold"), bg="white")
        header.pack(pady=20)

        if self.simulate:
            tk.Label(self.root, text="SIMULATION MODE — no hardware attached",
                     font=("Arial", 11, "italic"), fg="#b45309", bg="white").pack()

        self.frame = tk.Frame(self.root, bg="white")
        self.frame.pack()

        self.images = {}
        self.value_labels = {}
        for idx, (key, label_ar, unit, icon_file) in enumerate(METRIC_LABELS):
            icon_path = ASSETS_DIR / icon_file
            img = PhotoImage(file=str(icon_path)) if icon_path.exists() else None
            if img is not None:
                img = img.subsample(3, 3) if key != "tvoc" else img
                self.images[key] = img
                tk.Label(self.frame, image=img, bg="white").grid(row=0, column=idx, padx=25)

            value_label = tk.Label(self.frame, text=f"0 {unit}", font=("Arial", 16, "bold"), bg="white")
            value_label.grid(row=1, column=idx)
            self.value_labels[key] = (value_label, unit)

            tk.Label(self.frame, text=rtl(label_ar), font=("Arial", 14), bg="white").grid(row=2, column=idx)

        status_bits = []
        status_bits.append("Blynk: ON" if self.blynk_enabled else "Blynk: off (no BLYNK_AUTH_TOKEN)")
        status_bits.append("AI analysis: ON" if self.openai_enabled else "AI analysis: off (no OPENAI_API_KEY)")
        tk.Label(self.root, text=" | ".join(status_bits), font=("Arial", 10), fg="#555", bg="white").pack()

        self.analysis_label = tk.Label(self.root, text=self.analysis_text, font=("Arial", 16),
                                        bg="white", wraplength=1250, justify="right")
        self.analysis_label.pack(pady=20)

    def _update_display(self):
        for key, (label_widget, unit) in self.value_labels.items():
            label_widget.config(text=f"{self.data[key]} {unit}")
        self.analysis_label.config(text=self.analysis_text)

    # ---------------- Blynk ----------------
    def _upload_to_blynk(self):
        if not self.blynk_enabled:
            return
        if (datetime.now() - self.last_blynk_time).total_seconds() < BLYNK_UPLOAD_INTERVAL_S:
            return
        self.last_blynk_time = datetime.now()
        import requests
        virtual_pins = {"temperature": "V0", "humidity": "V1", "uv": "V2",
                         "co2": "V3", "dust": "V4", "tvoc": "V5", "h2s": "V7"}
        base = "https://blynk.cloud/external/api/update"
        for key, pin in virtual_pins.items():
            try:
                requests.get(base, params={"token": BLYNK_AUTH_TOKEN, pin: self.data[key]}, timeout=5)
            except Exception as exc:
                print(f"Blynk upload failed for {key}: {exc}", file=sys.stderr)

    # ---------------- AI analysis ----------------
    def _analyze_data(self):
        if not self.openai_enabled:
            return
        if (datetime.now() - self.last_analysis_time).total_seconds() < ANALYSIS_INTERVAL_S:
            return
        self.last_analysis_time = datetime.now()

        prompt = (
            "بيانات بيئية حالية:\n"
            f"- درجة الحرارة: {self.data['temperature']}°C\n"
            f"- الرطوبة: {self.data['humidity']}%\n"
            f"- الأشعة فوق البنفسجية: {self.data['uv']}\n"
            f"- الغبار: {self.data['dust']} µg/m³\n"
            f"- ثاني أكسيد الكربون: {self.data['co2']} ppm\n"
            f"- المركبات العضوية المتطايرة: {self.data['tvoc']} ppb\n"
            f"- كبريتيد الهيدروجين: {self.data['h2s']} ppm\n\n"
            "بإيجاز: هل توجد مخاطر، وما التوصية؟"
        )
        try:
            response = self._openai_client.chat.completions.create(
                model=os.environ.get("AQM_OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "أنت مساعد موجز لتحليل جودة الهواء البيئية."},
                    {"role": "user", "content": prompt},
                ],
            )
            analysis = response.choices[0].message.content
            self.analysis_text = rtl(analysis)
        except Exception as exc:
            self.analysis_text = rtl(f"تعذّر تحليل البيانات: {exc}")

    # ---------------- Main loop ----------------
    def _tick(self):
        reading = self._read_serial_line()
        if reading:
            for key, *_ in METRIC_LABELS:
                if key in reading:
                    self.data[key] = reading[key]
            self._update_display()
            self._upload_to_blynk()
            self._analyze_data()

        poll_ms = 1000 if self.simulate else 200
        self.root.after(poll_ms, self._tick)


def main():
    parser = argparse.ArgumentParser(description="Air Quality Monitor dashboard")
    parser.add_argument("--simulate", action="store_true",
                         help="run with synthetic sensor data, no hardware required")
    args = parser.parse_args()

    root = tk.Tk()
    AirQualityDashboard(root, simulate=args.simulate)
    root.mainloop()


if __name__ == "__main__":
    main()
