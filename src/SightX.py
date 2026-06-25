"""
╔═══════════════════════════════════════════════════════════╗
║         SIGHTX v3.0 — AI WEAPON DETECTION WEB APP        ║
║     Production Server with Full Backend Integration       ║
╚═══════════════════════════════════════════════════════════╝

Run:
    pip install flask waitress ultralytics opencv-python psutil pyttsx3
    python SightX_WebApp.py

Then open: http://localhost:5000
"""

import os
import cv2
import sys
import threading
import time
import sqlite3
import pyttsx3
import psutil
import datetime
import numpy as np
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from flask import Flask, render_template, Response, jsonify
from ultralytics import YOLO


# =====================================================================
# CONFIGURATION
# =====================================================================

class Settings:
    MODEL_PATH = None
    CONF = 0.6
    ALERT_COOLDOWN = 2
    EMAIL_ENABLED = True
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "rajyashkarn@gmail.com"
    SENDER_PASSWORD = "esezqtxssokgaedu"
    RECEIVER_EMAIL = "karnyash52@gmail.com"

    ALERT_CONFIG = {
        "knife":   {"color": (0, 0, 255), "authority": "SECURITY & POLICE", "priority": "HIGH"},
        "weapon":  {"color": (0, 0, 255), "authority": "SECURITY", "priority": "HIGH"},
        "gun":     {"color": (0, 0, 255), "authority": "POLICE", "priority": "CRITICAL"},
    }

    @staticmethod
    def auto_find_model():
        common_paths = [
            r"C:\Users\hshar\Desktop\best.pt",
            r"C:\Users\hshar\Desktop\Project SightX Assets\SightX SRC\SightX_Training\knife_detection_fast\weights\best.pt",
            "SightX_Training/knife_detection_fast/weights/best.pt",
            "best.pt",
            "weights/best.pt",
            "../best.pt"
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def load_config(config_file="sightx_config.txt"):
        if not os.path.exists(config_file):
            return False
        try:
            with open(config_file, 'r') as f:
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        if hasattr(Settings, key):
                            if key == "EMAIL_ENABLED":
                                setattr(Settings, key, value == "True")
                            else:
                                setattr(Settings, key, value)
            return True
        except:
            return False


# =====================================================================
# EMAIL ALERT SYSTEM
# =====================================================================

class EmailAlertSystem:
    alert_images_dir = "alert_images"

    def __init__(self):
        os.makedirs(self.alert_images_dir, exist_ok=True)

    def send_email_with_image(self, class_name, confidence, frame, timestamp):
        if not Settings.EMAIL_ENABLED:
            return

        def send_in_thread():
            try:
                image_filename = f"{class_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                image_path = os.path.join(self.alert_images_dir, image_filename)
                cv2.imwrite(image_path, frame)

                msg = MIMEMultipart()
                msg['From'] = Settings.SENDER_EMAIL
                msg['To'] = Settings.RECEIVER_EMAIL
                msg['Subject'] = f"🚨 SIGHTX ALERT: {class_name.upper()} DETECTED"

                priority = Settings.ALERT_CONFIG.get(class_name, {}).get('priority', 'MEDIUM')
                authority = Settings.ALERT_CONFIG.get(class_name, {}).get('authority', 'Security')
                body = (
                    f"⚠️ THREAT DETECTED: {class_name.upper()}\n"
                    f"Confidence: {confidence:.1%}\n"
                    f"Priority: {priority}\n"
                    f"Authority: {authority}\n"
                    f"Timestamp: {timestamp}\n"
                    f"Immediate Action Required."
                )
                msg.attach(MIMEText(body, 'plain'))

                if os.path.exists(image_path):
                    with open(image_path, 'rb') as f:
                        msg.attach(MIMEImage(f.read(), name=os.path.basename(image_path)))

                server = smtplib.SMTP(Settings.SMTP_SERVER, Settings.SMTP_PORT)
                server.starttls()
                server.login(Settings.SENDER_EMAIL, Settings.SENDER_PASSWORD)
                server.send_message(msg)
                server.quit()
                print(f"Email sent: {class_name}")
            except Exception as e:
                print(f"Email failed: {e}")

        threading.Thread(target=send_in_thread, daemon=True).start()


# =====================================================================
# TTS ALERT SYSTEM
# =====================================================================

class TTSAlertSystem:
    def __init__(self):
        self.is_speaking = False
        self.lock = threading.Lock()

    def speak_alert(self, message="Security breach"):
        with self.lock:
            if self.is_speaking:
                return
            self.is_speaking = True

        def run_speech():
            try:
                engine = pyttsx3.init()
                engine.say(message)
                engine.runAndWait()
            except:
                pass
            finally:
                with self.lock:
                    self.is_speaking = False

        threading.Thread(target=run_speech, daemon=True).start()


# =====================================================================
# DATABASE
# =====================================================================

def init_db():
    conn = sqlite3.connect('sightx_logs.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS incident_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    threat_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    threat_level REAL NOT NULL)''')
    conn.commit()
    conn.close()
    print("Database initialized")


def log_incident(threat_type, confidence, threat_level):
    try:
        conn = sqlite3.connect('sightx_logs.db')
        c = conn.cursor()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute('INSERT INTO incident_log (timestamp, threat_type, confidence, threat_level) VALUES (?, ?, ?, ?)',
                  (ts, threat_type, confidence, threat_level))
        conn.commit()
        conn.close()
        return ts
    except Exception as e:
        print(f"DB Error: {e}")
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =====================================================================
# FLASK APP + GLOBAL STATE
# =====================================================================

app = Flask(__name__)

email_system = EmailAlertSystem()
tts_system = TTSAlertSystem()
yolo_model = None
camera = None
camera_lock = threading.Lock()
last_alert = {}

telemetry = {
    "cpu": 0.0, "ram": 0.0, "fps": 0,
    "threat_level": 0.0, "safe_level": 1.0,
    "detected_threat": "None", "alert_active": False,
    "logs": []
}


# =====================================================================
# DETECTION BACKEND
# =====================================================================

def send_threat_alert(class_name, confidence, frame):
    if class_name not in Settings.ALERT_CONFIG:
        return
    now = time.time()
    if now - last_alert.get(class_name, 0) < Settings.ALERT_COOLDOWN:
        return
    last_alert[class_name] = now
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    email_system.send_email_with_image(class_name, confidence, frame, ts)


def draw_detection_box(frame, box, class_name, confidence):
    x1, y1, x2, y2 = map(int, box.xyxy[0])
    config = Settings.ALERT_CONFIG.get(class_name, {})
    color = config.get("color", (0, 255, 0))
    priority = config.get("priority", "LOW")

    # Main bounding box
    thickness = 3 if priority in ["HIGH", "CRITICAL"] else 2
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

    # Corner accents for style
    corner_len = 15
    cv2.line(frame, (x1, y1), (x1 + corner_len, y1), (0, 255, 204), 2)
    cv2.line(frame, (x1, y1), (x1, y1 + corner_len), (0, 255, 204), 2)
    cv2.line(frame, (x2, y1), (x2 - corner_len, y1), (0, 255, 204), 2)
    cv2.line(frame, (x2, y1), (x2, y1 + corner_len), (0, 255, 204), 2)
    cv2.line(frame, (x1, y2), (x1 + corner_len, y2), (0, 255, 204), 2)
    cv2.line(frame, (x1, y2), (x1, y2 - corner_len), (0, 255, 204), 2)
    cv2.line(frame, (x2, y2), (x2 - corner_len, y2), (0, 255, 204), 2)
    cv2.line(frame, (x2, y2), (x2, y2 - corner_len), (0, 255, 204), 2)

    # Label with background
    label = f"{class_name.upper()} {confidence:.0%}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(label, font, 0.6, 2)
    cv2.rectangle(frame, (x1, y1 - th - 14), (x1 + tw + 10, y1), color, -1)
    cv2.putText(frame, label, (x1 + 5, y1 - 7), font, 0.6, (255, 255, 255), 2)


def generate_frames():
    """MJPEG Generator — Reads camera, runs YOLO, streams frames."""
    global camera

    with camera_lock:
        if camera is None or not camera.isOpened():
            camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not camera.isOpened():
                camera = cv2.VideoCapture(0)

    prev_time = time.time()
    last_tts_time = 0

    while True:
        with camera_lock:
            if camera is None or not camera.isOpened():
                break
            success, frame = camera.read()

        if not success or frame is None:
            time.sleep(0.03)
            continue

        curr_time = time.time()
        fps = 1.0 / max(curr_time - prev_time, 0.001)
        prev_time = curr_time

        threat_val = 0.0
        threat_name = ""

        # ── YOLO Detection with ByteTrack ──
        if yolo_model is not None:
            try:
                results = yolo_model.track(
                    frame, persist=True, tracker="bytetrack.yaml",
                    imgsz=640, conf=Settings.CONF, iou=0.45, verbose=False
                )[0]

                for box in results.boxes:
                    cls_name = yolo_model.names[int(box.cls)]
                    conf = float(box.conf)
                    if cls_name in Settings.ALERT_CONFIG:
                        draw_detection_box(frame, box, cls_name, conf)
                        send_threat_alert(cls_name, conf, frame)
                        if conf > threat_val:
                            threat_val = conf
                            threat_name = cls_name
            except Exception as e:
                print(f"YOLO Error: {e}")

        threat_val = min(max(threat_val, 0.0), 1.0)
        is_alert = threat_val > 0.5

        # TTS + DB Logging
        if is_alert and (curr_time - last_tts_time > 4):
            tts_system.speak_alert(f"{threat_name} detected")
            log_incident(threat_name, threat_val, threat_val)
            last_tts_time = curr_time

        # Update shared telemetry state
        telemetry['fps'] = int(fps)
        telemetry['cpu'] = psutil.cpu_percent()
        telemetry['ram'] = psutil.virtual_memory().percent
        telemetry['threat_level'] = round(threat_val, 3)
        telemetry['safe_level'] = round(1.0 - threat_val, 3)
        telemetry['alert_active'] = is_alert
        telemetry['detected_threat'] = threat_name if is_alert else "None"

        # Encode frame to JPEG
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ret:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


# =====================================================================
# FLASK ROUTES
# =====================================================================

@app.route('/')
def home():
    return render_template('home.html')


@app.route('/dashboard')
def dashboard():
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/telemetry')
def get_telemetry():
    try:
        conn = sqlite3.connect('sightx_logs.db')
        c = conn.cursor()
        c.execute("SELECT timestamp, threat_type, confidence FROM incident_log ORDER BY id DESC LIMIT 8")
        rows = c.fetchall()
        telemetry['logs'] = [{"time": r[0], "type": r[1], "conf": f"{r[2]:.1%}"} for r in rows]
        conn.close()
    except Exception as e:
        print(f"DB Read Error: {e}")
    return jsonify(telemetry)


# =====================================================================
# PRODUCTION SERVER ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    init_db()
    Settings.load_config()

    # Load YOLO Model
    model_path = Settings.auto_find_model()
    if model_path:
        print(f"Loading YOLOv8 Model: {model_path}")
        yolo_model = YOLO(model_path)
        print(f"Model loaded — {len(yolo_model.names)} classes detected")
    else:
        print("No model found! Detection will be disabled.")
        print("   Place best.pt in the same directory or configure model path.")

    # ── TRY PRODUCTION SERVER (Waitress), FALLBACK TO FLASK DEV ──
    print("\n" + "=" * 60)
    print("   SIGHTX WEB APP v3.0")
    print("   Open in browser: http://localhost:5000")
    print("=" * 60 + "\n")

    try:
        from waitress import serve
        print("Starting production server (Waitress)...")
        serve(app, host='0.0.0.0', port=5000, threads=4)
    except ImportError:
        print("Starting development server (Flask)...")
        print("   TIP: Install waitress for production: pip install waitress")
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
