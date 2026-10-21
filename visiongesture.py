"""VisionGesture v2 — Python 3.13 + OpenCV + MediaPipe Tasks API.

MODE PERPINDAHAN OTOMATIS:
- Tunjukkan gestur jempol ke atas dengan empat jari lain terlipat.
- Tahan sekitar 0,9 detik untuk berpindah ke mode berikutnya.
- Turunkan tangan/jempol dari pose itu sebelum mencoba pindah mode lagi.
- Q/Esc keluar; H menampilkan/menyembunyikan petunjuk. Angka 1-6 juga tersedia sebagai override/debug.

MODE:
1 Virtual Mouse: telunjuk=gerak; cubit ibu jari+telunjuk=klik kiri;
  cubit ibu jari+jari tengah=klik kanan; dua jari=scroll mengikuti gerakan naik/turun.
2 Volume: jarak ibu jari-telunjuk mengatur volume sistem.
3 Brightness: jarak ibu jari-telunjuk mengatur kecerahan bila didukung driver/perangkat.
4 Media: kepalan=play/pause; gestur jempol kiri/kanan=mundur/maju; tiga jari=fullscreen.
5 Shortcuts: cubit ibu jari+telunjuk=copy; cubit ibu jari+jari tengah=paste;
  kepalan membuka Alt+Tab; cubit kiri/kanan memilih jendela sebelumnya/berikutnya; telapak terbuka konfirmasi.
  Empat jari terbuka dengan jempol terlipat=screenshot.
6 Touchless Kiosk: kendali kursor, klik cubit, dan dwell-click saat kursor diam.

Peringatan: perintah dikirim ke aplikasi aktif. Uji pada aplikasi/dokumen tidak penting.
"""
import math
import platform
import subprocess
import time
from pathlib import Path

# On Windows with display scaling (e.g. 150%-200%), OpenCV/PyAutoGUI may see
# logical dimensions while the desktop window uses physical pixels. Enable DPI
# awareness before importing GUI libraries so the preview and pointer map to the
# same display coordinates.
if platform.system().lower() == "windows":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import cv2
import mediapipe as mp
import pyautogui

# ------------------- KONFIGURASI -------------------
CAMERA_INDEX = 0
CAMERA_REQUEST_W, CAMERA_REQUEST_H = 1280, 720
PROCESS_W, PROCESS_H = 960, 540
SMOOTHING = 3.8                   # kecil = lebih responsif
CLICK_COOLDOWN = 0.42
MODE_HOLD_SECONDS = 0.90          # tahan jempol ke atas untuk pindah mode
CONTROL_INTERVAL = 0.35
CONTROL_CHANGE_THRESHOLD = 2      # cegah panggilan OS berlebihan
SCROLL_DEADZONE = 0.004
SCROLL_SENSITIVITY = 420
DWELL_SECONDS = 1.35
MODEL_PATH = Path(__file__).with_name("hand_landmarker.task")
WINDOW_NAME = "VisionGesture v2 - Q untuk keluar"

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.01
SCREEN_W, SCREEN_H = pyautogui.size()
OS_NAME = platform.system().lower()
MODE_NAMES = {
    1: "VIRTUAL MOUSE",
    2: "VOLUME CONTROL",
    3: "BRIGHTNESS CONTROL",
    4: "MEDIA CONTROLLER",
    5: "DESKTOP SHORTCUTS + ALT-TAB",
    6: "TOUCHLESS KIOSK",
}


def dist(lm, a, b):
    return math.hypot(lm[a].x - lm[b].x, lm[a].y - lm[b].y)


def finger_up(lm, tip, pip):
    # Tangan diarahkan kira-kira menghadap kamera.
    return lm[tip].y < lm[pip].y


def finger_state(lm):
    return {
        "index": finger_up(lm, 8, 6),
        "middle": finger_up(lm, 12, 10),
        "ring": finger_up(lm, 16, 14),
        "pinky": finger_up(lm, 20, 18),
    }


def thumb_up(lm):
    # Ujung jempol lebih tinggi dari sendi di bawahnya.
    return lm[4].y < lm[3].y - 0.035


def thumb_down(lm):
    return lm[4].y > lm[3].y + 0.045


def thumb_horizontal(lm):
    # Digunakan hanya jika empat jari lain terlipat; dead-zone menghindari trigger kecil.
    dx = lm[4].x - lm[3].x
    if dx > 0.075:
        return "right"
    if dx < -0.075:
        return "left"
    return "neutral"


def gestures(lm):
    fs = finger_state(lm)
    all_four = fs["index"] and fs["middle"] and fs["ring"] and fs["pinky"]
    no_four = not fs["index"] and not fs["middle"] and not fs["ring"] and not fs["pinky"]
    palm_width = max(dist(lm, 5, 17), 0.04)
    # Normalisasi terhadap ukuran telapak agar relatif konsisten walau tangan lebih dekat/jauh kamera.
    pinch_index = dist(lm, 4, 8) / palm_width
    pinch_middle = dist(lm, 4, 12) / palm_width
    left_pinch = pinch_index < 0.34 and pinch_middle > 0.43
    right_pinch = pinch_middle < 0.34 and pinch_index > 0.43
    open_palm = all_four and dist(lm, 4, 8) / palm_width > 0.55
    return {
        **fs,
        "all_four": all_four,
        "fist": no_four and not thumb_up(lm),
        "thumbs_up_pose": no_four and thumb_up(lm),
        "thumb_up": thumb_up(lm),
        "thumb_down": thumb_down(lm),
        "thumb_horizontal": thumb_horizontal(lm),
        "mouse_point": fs["index"] and not fs["middle"] and not fs["ring"] and not fs["pinky"],
        "scroll_pose": fs["index"] and fs["middle"] and not fs["ring"] and not fs["pinky"],
        "three_fingers": fs["index"] and fs["middle"] and fs["ring"] and not fs["pinky"],
        "four_fingers_thumb_tucked": all_four and not open_palm,
        "open_palm": open_palm,
        "left_pinch": left_pinch,
        "right_pinch": right_pinch,
        "pinch_value": pinch_index,
        "palm_width": palm_width,
    }


def scale_0_100(ratio, low=0.22, high=1.15):
    ratio = max(low, min(high, ratio))
    return int(round((ratio - low) / (high - low) * 100))


def set_volume(percent):
    """Mengatur volume master; mengembalikan (berhasil, pesan)."""
    percent = max(0, min(100, int(percent)))
    try:
        if OS_NAME == "windows":
            from pycaw.pycaw import AudioUtilities
            speakers = AudioUtilities.GetSpeakers()
            # API pycaw terbaru.
            try:
                endpoint = speakers.EndpointVolume
            except Exception:
                # Kompatibilitas beberapa versi pycaw sebelumnya.
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume
                interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                endpoint = cast(interface, POINTER(IAudioEndpointVolume))
            endpoint.SetMasterVolumeLevelScalar(percent / 100.0, None)
            return True, f"Volume sistem {percent}%"
        if OS_NAME == "darwin":
            subprocess.run(["osascript", "-e", f"set volume output volume {percent}"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            return True, f"Volume sistem {percent}%"
        if OS_NAME == "linux":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            return True, f"Volume sistem {percent}%"
        return False, f"OS {OS_NAME} belum didukung untuk volume sistem"
    except ModuleNotFoundError as exc:
        return False, f"Modul volume hilang: {exc.name}. Jalankan pip install -r requirements.txt"
    except Exception as exc:
        return False, f"Volume gagal ({type(exc).__name__}): {str(exc)[:65]}"


def set_brightness(percent):
    """Mengatur kecerahan layar jika monitor/driver mendukungnya."""
    percent = max(0, min(100, int(percent)))
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(percent)
        return True, f"Kecerahan layar {percent}%"
    except ModuleNotFoundError as exc:
        return False, f"Modul brightness hilang: {exc.name}. Jalankan pip install -r requirements.txt"
    except Exception as exc:
        return False, f"Brightness gagal ({type(exc).__name__}): {str(exc)[:65]}"


def draw_text(frame, text, xy, scale=0.52, color=(245, 245, 245), thickness=1):
    cv2.putText(frame, str(text), xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def draw_hand(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    links = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
             (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),
             (13,17),(17,18),(18,19),(19,20),(0,17)]
    for a, b in links:
        cv2.line(frame, pts[a], pts[b], (80, 220, 120), 2, cv2.LINE_AA)
    for p in pts:
        cv2.circle(frame, p, 4, (0, 220, 255), -1, cv2.LINE_AA)


def do_screenshot():
    if OS_NAME == "windows":
        pyautogui.hotkey("win", "shift", "s")
        return "Screenshot: Snipping Tool"
    if OS_NAME == "darwin":
        pyautogui.hotkey("command", "shift", "4")
        return "Screenshot: macOS"
    path = Path.home() / "visiongesture_screenshot.png"
    pyautogui.screenshot().save(str(path))
    return f"Screenshot disimpan: {path.name}"


def main():
    if not MODEL_PATH.is_file():
        print("Model hand_landmarker.task belum ditemukan.")
        print("Unduh model resmi dari URL berikut, lalu simpan di folder yang sama dengan visiongesture.py:")
        print("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
        return

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW if OS_NAME == "windows" else cv2.CAP_ANY)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_REQUEST_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_REQUEST_H)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Kamera tidak dapat dibuka. Periksa izin kamera atau ubah CAMERA_INDEX.")
        return

    BaseOptions = mp.tasks.BaseOptions
    HandLandmarker = mp.tasks.vision.HandLandmarker
    HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
    RunningMode = mp.tasks.vision.RunningMode
    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.55,
        min_hand_presence_confidence=0.55,
        min_tracking_confidence=0.55,
    )

    mode = 1
    show_help = True
    start_time = time.monotonic()
    previous_cursor = (SCREEN_W / 2, SCREEN_H / 2)
    last_click_time = 0.0
    last_control_time = 0.0
    last_control_value = None
    last_command_time = 0.0
    last_action = "Siap"
    status_line = "Jempol ke atas 0,9 dtk = mode berikutnya"
    last_switch_pose_time = None
    mode_switch_lock = False
    previous_click_gesture = None
    previous_mode4_action = None
    previous_mode5_action = None
    alt_picker_open = False
    alt_picker_action_latched = False
    scroll_anchor_y = None
    scroll_accumulator = 0.0
    dwell_start = None
    dwell_clicked = False
    last_cursor_for_dwell = None

    # Normal window + fullscreen + frame dipaksa ke ukuran layar agar tidak menyisakan area abu-abu.
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    try:
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    except cv2.error:
        pass

    try:
        with HandLandmarker.create_from_options(options) as landmarker:
            while True:
                ok, raw = cap.read()
                if not ok:
                    status_line = "Gagal membaca kamera"
                    break

                raw = cv2.flip(raw, 1)
                # Standarkan rasio video agar overlay tidak terpotong pada webcam 4:3/ukuran berbeda.
                frame = cv2.resize(raw, (PROCESS_W, PROCESS_H), interpolation=cv2.INTER_LINEAR)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = int((time.monotonic() - start_time) * 1000)
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                now = time.monotonic()
                gesture_label = "Posisikan satu tangan di depan kamera"
                ui_gesture = None
                current_cursor = None

                if result.hand_landmarks:
                    lm = result.hand_landmarks[0]
                    draw_hand(frame, lm)
                    g = gestures(lm)
                    ui_gesture = g

                    # Penggantian mode otomatis: tahan thumbs-up 0,9 detik; perlu melepas pose untuk mengulang.
                    if g["thumbs_up_pose"]:
                        if not mode_switch_lock:
                            if last_switch_pose_time is None:
                                last_switch_pose_time = now
                            elapsed = now - last_switch_pose_time
                            gesture_label = f"Tahan jempol atas untuk pindah mode: {max(0, MODE_HOLD_SECONDS-elapsed):.1f} dtk"
                            if elapsed >= MODE_HOLD_SECONDS:
                                if alt_picker_open:
                                    try:
                                        pyautogui.keyUp("alt")
                                    except Exception:
                                        pass
                                    alt_picker_open = False
                                mode = 1 if mode >= 6 else mode + 1
                                last_action = f"Mode otomatis: {MODE_NAMES[mode]}"
                                status_line = "Mode berganti tanpa tombol keyboard"
                                last_switch_pose_time = None
                                mode_switch_lock = True
                                # reset gesture states to prevent action fired on switch frame
                                # Cegah gestur pelepasan jempol langsung memicu klik/Alt+Tab/play-pause.
                                previous_click_gesture = "left" if mode in (1, 6) else None
                                previous_mode4_action = "playpause" if mode == 4 else None
                                previous_mode5_action = "alt_tab" if mode == 5 else None
                                scroll_anchor_y = None
                                dwell_start = None
                                dwell_clicked = False
                        else:
                            gesture_label = "Turunkan jempol untuk mengaktifkan pemilih mode lagi"
                        # Jangan jalankan aksi mode lain ketika pose penggantian mode terdeteksi.
                    else:
                        last_switch_pose_time = None
                        if mode_switch_lock:
                            mode_switch_lock = False

                        # MODE 1 / 6: gestur klik diperiksa TERLEBIH DAHULU, sehingga jari yang menekuk saat mencubit tidak menghalangi klik.
                        if mode in (1, 6):
                            click_type = "left" if g["left_pinch"] else ("right" if g["right_pinch"] else None)
                            if click_type and click_type != previous_click_gesture and now - last_click_time >= CLICK_COOLDOWN:
                                if click_type == "left":
                                    pyautogui.click()
                                    last_action = "Klik kiri"
                                else:
                                    pyautogui.rightClick()
                                    last_action = "Klik kanan"
                                last_click_time = now
                            previous_click_gesture = click_type

                            if click_type:
                                gesture_label = "Cubitan terdeteksi: " + ("klik kiri" if click_type == "left" else "klik kanan")
                                scroll_anchor_y = None
                                dwell_start = None
                                dwell_clicked = False
                            elif g["scroll_pose"]:
                                gesture_label = "Scroll: gerakkan dua jari naik/turun"
                                center_y = (lm[8].y + lm[12].y) / 2.0
                                if scroll_anchor_y is not None:
                                    # Koordinat gambar y makin kecil ke arah atas; delta positif berarti scroll ke atas.
                                    delta = scroll_anchor_y - center_y
                                    if abs(delta) > SCROLL_DEADZONE:
                                        scroll_accumulator += delta * SCROLL_SENSITIVITY
                                        amount = int(scroll_accumulator)
                                        if amount:
                                            pyautogui.scroll(amount)
                                            scroll_accumulator -= amount
                                    scroll_anchor_y = center_y  # selalu update agar scroll mengikuti kedua jari
                                else:
                                    scroll_anchor_y = center_y
                                dwell_start = None
                                dwell_clicked = False
                            elif g["mouse_point"]:
                                gesture_label = "Virtual mouse: telunjuk menggerakkan kursor"
                                # 5%-95% dari frame kamera dipetakan ke seluruh layar.
                                nx = (lm[8].x - 0.05) / 0.90
                                ny = (lm[8].y - 0.05) / 0.90
                                tx = max(0, min(SCREEN_W - 1, int(nx * (SCREEN_W - 1))))
                                ty = max(0, min(SCREEN_H - 1, int(ny * (SCREEN_H - 1))))
                                cx = previous_cursor[0] + (tx - previous_cursor[0]) / SMOOTHING
                                cy = previous_cursor[1] + (ty - previous_cursor[1]) / SMOOTHING
                                pyautogui.moveTo(cx, cy)
                                previous_cursor = (cx, cy)
                                current_cursor = (cx, cy)
                                scroll_anchor_y = None
                                scroll_accumulator = 0.0
                                if mode == 6:
                                    # Kiosk dwell-click: tahan kursor diam 1,35 detik untuk klik otomatis.
                                    if last_cursor_for_dwell is None or math.hypot(cx-last_cursor_for_dwell[0], cy-last_cursor_for_dwell[1]) > 13:
                                        dwell_start = now
                                        dwell_clicked = False
                                    elif dwell_start is None:
                                        dwell_start = now
                                    elif now - dwell_start >= DWELL_SECONDS and not dwell_clicked:
                                        pyautogui.click()
                                        dwell_clicked = True
                                        last_action = "Dwell-click"
                                    last_cursor_for_dwell = (cx, cy)
                                else:
                                    dwell_start = None
                                    dwell_clicked = False
                            else:
                                gesture_label = "Siap: telunjuk=gerak, cubit=klik, dua jari=scroll"
                                scroll_anchor_y = None
                                scroll_accumulator = 0.0
                                if mode == 6:
                                    dwell_start = None
                                    dwell_clicked = False
                            if mode == 6 and dwell_start is not None and not dwell_clicked:
                                progress = min(1.0, (now - dwell_start) / DWELL_SECONDS)
                                cv2.circle(frame, (PROCESS_W - 70, 75), 28, (70, 70, 70), 5)
                                cv2.ellipse(frame, (PROCESS_W - 70, 75), (28, 28), -90, 0, int(360*progress), (80, 230, 120), 5)

                        elif mode == 2:
                            gesture_label = "Volume: renggangkan/cubit ibu jari dan telunjuk"
                            # Gunakan ukuran telapak sebagai normalisasi untuk volume.
                            value = scale_0_100(g["pinch_value"], 0.20, 0.95)
                            if now-last_control_time >= CONTROL_INTERVAL and (last_control_value is None or abs(value-last_control_value) >= CONTROL_CHANGE_THRESHOLD):
                                _, status_line = set_volume(value)
                                last_control_time, last_control_value = now, value
                            cv2.rectangle(frame, (20, 170), (PROCESS_W-20, 190), (45,45,45), -1)
                            cv2.rectangle(frame, (20, 170), (20+int((PROCESS_W-40)*value/100), 190), (80,220,120), -1)
                            draw_text(frame, f"Target volume: {value}%", (20, 215), 0.55, (255,255,255), 2)

                        elif mode == 3:
                            gesture_label = "Brightness: renggangkan/cubit ibu jari dan telunjuk"
                            value = scale_0_100(g["pinch_value"], 0.20, 0.95)
                            if now-last_control_time >= CONTROL_INTERVAL and (last_control_value is None or abs(value-last_control_value) >= CONTROL_CHANGE_THRESHOLD):
                                _, status_line = set_brightness(value)
                                last_control_time, last_control_value = now, value
                            cv2.rectangle(frame, (20, 170), (PROCESS_W-20, 190), (45,45,45), -1)
                            cv2.rectangle(frame, (20, 170), (20+int((PROCESS_W-40)*value/100), 190), (80,180,255), -1)
                            draw_text(frame, f"Target kecerahan: {value}%", (20, 215), 0.55, (255,255,255), 2)

                        elif mode == 4:
                            # Jalankan aksi hanya saat gesture baru muncul; mencegah spam per frame.
                            action = None
                            if g["fist"]:
                                action = "playpause"
                            elif g["three_fingers"]:
                                action = "fullscreen"
                            elif all(not g[k] for k in ("index","middle","ring","pinky")) and g["thumb_horizontal"] in ("left","right"):
                                action = g["thumb_horizontal"]
                            gesture_label = "Media: kepalan=play/pause; jempol kiri/kanan=prev/next; 3 jari=fullscreen"
                            if action and action != previous_mode4_action and now-last_command_time > 0.7:
                                if action == "playpause":
                                    pyautogui.press("space")
                                    last_action = "Play/Pause (Space)"
                                elif action == "fullscreen":
                                    pyautogui.press("f")
                                    last_action = "Fullscreen (F)"
                                elif action == "left":
                                    pyautogui.press("left")
                                    last_action = "Mundur / sebelumnya"
                                elif action == "right":
                                    pyautogui.press("right")
                                    last_action = "Maju / berikutnya"
                                last_command_time = now
                            previous_mode4_action = action

                        elif mode == 5:
                            # Alt+Tab: kepalan membuka task switcher dan menahan Alt.
                            # Saat terbuka, cubit ibu jari+telunjuk=next, cubit ibu jari+jari tengah=previous,
                            # telapak terbuka=konfirmasi (melepas Alt).
                            gesture_label = "Shortcut: pinch kiri=copy/next; pinch tengah=paste/prev; kepalan=Alt+Tab"
                            if alt_picker_open:
                                if g["open_palm"]:
                                    pyautogui.keyUp("alt")
                                    alt_picker_open = False
                                    alt_picker_action_latched = False
                                    last_action = "Jendela dipilih"
                                    last_command_time = now
                                else:
                                    picker_dir = "next" if g["left_pinch"] else ("prev" if g["right_pinch"] else None)
                                    if picker_dir and not alt_picker_action_latched and now-last_command_time > 0.22:
                                        if picker_dir == "next":
                                            pyautogui.press("tab")
                                        else:
                                            pyautogui.keyDown("shift")
                                            pyautogui.press("tab")
                                            pyautogui.keyUp("shift")
                                        alt_picker_action_latched = True
                                        last_command_time = now
                                        last_action = "Pemilih aplikasi: " + ("berikutnya" if picker_dir == "next" else "sebelumnya")
                                    if not g["left_pinch"] and not g["right_pinch"]:
                                        alt_picker_action_latched = False
                            else:
                                action = None
                                if g["fist"]:
                                    action = "alt_tab"
                                elif g["left_pinch"]:
                                    action = "copy"
                                elif g["right_pinch"]:
                                    action = "paste"
                                elif g["four_fingers_thumb_tucked"]:
                                    action = "screenshot"
                                if action and action != previous_mode5_action and now-last_command_time > 0.65:
                                    if action == "alt_tab":
                                        pyautogui.keyDown("alt")
                                        pyautogui.press("tab")
                                        alt_picker_open = True
                                        alt_picker_action_latched = False
                                        last_action = "Alt+Tab terbuka: cubit untuk pilih, telapak untuk konfirmasi"
                                    elif action == "copy":
                                        pyautogui.hotkey("ctrl", "c")
                                        last_action = "Ctrl+C (copy)"
                                    elif action == "paste":
                                        pyautogui.hotkey("ctrl", "v")
                                        last_action = "Ctrl+V (paste)"
                                    elif action == "screenshot":
                                        last_action = do_screenshot()
                                    last_command_time = now
                                previous_mode5_action = action

                else:
                    # Kehilangan tangan: reset gesture edge-state supaya gestur berikutnya bisa memicu aksi baru.
                    previous_click_gesture = None
                    previous_mode4_action = None
                    previous_mode5_action = None
                    last_switch_pose_time = None
                    mode_switch_lock = False
                    scroll_anchor_y = None
                    gesture_label = "Tangan belum terdeteksi"

                # UI digambar pada kanvas 16:9 konsisten, lalu diperbesar tepat ke ukuran layar.
                panel_bottom = 132
                cv2.rectangle(frame, (0, 0), (PROCESS_W, panel_bottom), (20, 20, 20), -1)
                draw_text(frame, "VisionGesture v2 | Python 3.13 + MediaPipe Tasks", (18, 27), 0.58, (255,255,255), 2)
                draw_text(frame, f"MODE {mode}/6 - {MODE_NAMES[mode]}", (18, 54), 0.56, (80,235,130), 2)
                draw_text(frame, gesture_label[:105], (18, 81), 0.44, (240,240,240), 1)
                draw_text(frame, "Aksi: " + last_action[:85], (18, 105), 0.41, (220,220,220), 1)
                draw_text(frame, "Status: " + status_line[:95], (18, 126), 0.37, (190,220,255), 1)

                if show_help:
                    lines = [
                        "MODE OTOMATIS: tahan JEMPOL KE ATAS 0,9 detik -> mode berikutnya; turunkan untuk mengulang.",
                        "1 Mouse: telunjuk=gerak | cubit jempol+telunjuk=klik kiri | cubit jempol+tengah=klik kanan | 2 jari=scroll",
                        "2 Volume | 3 Brightness: jarak ibu jari-telunjuk mengatur nilai; cek Status jika OS/driver menolak.",
                        "4 Media: kepalan=play/pause | jempol kiri/kanan=prev/next | 3 jari=fullscreen.",
                        "5 Shortcut: cubit telunjuk=copy / tengah=paste | kepalan=Alt+Tab | cubit saat picker=pilih | telapak=konfirmasi.",
                        "6 Kiosk: arahkan telunjuk; tahan kursor 1,35 detik untuk dwell-click. H=petunjuk | Q/Esc=keluar.",
                    ]
                    box_top = PROCESS_H - 137
                    cv2.rectangle(frame, (8, box_top-20), (PROCESS_W-8, PROCESS_H-8), (20,20,20), -1)
                    y = box_top
                    for line in lines:
                        draw_text(frame, line, (18, y), 0.36, (255,255,255), 1)
                        y += 21

                # Skala output sampai ukuran display sebenarnya; mencegah isi hanya memenuhi 1/4 jendela.
                display_frame = cv2.resize(frame, (SCREEN_W, SCREEN_H), interpolation=cv2.INTER_LINEAR)
                cv2.imshow(WINDOW_NAME, display_frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), ord('Q'), 27):
                    break
                if key in (ord('h'), ord('H')):
                    show_help = not show_help
                # Angka tetap tersedia sebagai akses darurat/debug, tetapi pergantian normal memakai gesture.
                if key in [ord(str(n)) for n in range(1, 7)]:
                    if alt_picker_open:
                        try:
                            pyautogui.keyUp('alt')
                        except Exception:
                            pass
                        alt_picker_open = False
                    mode = int(chr(key))
                    last_action = f"Override keyboard: {MODE_NAMES[mode]}"
                    last_control_value = None
                    previous_mode4_action = None
                    previous_mode5_action = None
                    previous_click_gesture = None
                    scroll_anchor_y = None
                if key in (ord('f'), ord('F')):
                    # Tekan F untuk keluar/masuk fullscreen bila driver OpenCV tidak menampilkan fullscreen dengan benar.
                    try:
                        prop = cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN)
                        desired = cv2.WINDOW_NORMAL if prop == cv2.WINDOW_FULLSCREEN else cv2.WINDOW_FULLSCREEN
                        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, desired)
                    except cv2.error:
                        pass
    finally:
        if alt_picker_open:
            try:
                pyautogui.keyUp('alt')
            except Exception:
                pass
        cap.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
