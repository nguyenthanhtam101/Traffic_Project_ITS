import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
import streamlit as st
import cv2
import numpy as np
import torch
from ultralytics import YOLO
import easyocr
import csv
import time
from datetime import datetime
import requests
import threading
import tempfile
import queue
import re

# =================================================================
# 1. BIẾN TOÀN CỤC & TỪ ĐIỂN
# =================================================================
REAL_WIDTH_M = 14.0
REAL_HEIGHT_M = 6.0

LOCATION_NAME = "Ngã tư Demo, TP.HCM"
TELEGRAM_BOT_TOKEN = "8724545022:AAEgeJZ8nE6zj5utIDb85C3dpNgzGcwsn2g"
TELEGRAM_CHAT_ID = "8066570830"

VECTOR_TURN_THRESHOLD = 80

VI_CLASS_MAP = {
    'car': 'Ô tô',
    'motorcycle': 'Xe máy',
    'truck': 'Xe tải',
    'bus': 'Xe buýt'
}

COLOR_MAP = {
    'motorcycle': (255, 0, 0),
    'bus': (0, 255, 255),
    'car': (0, 255, 0),
    'truck': (235, 134, 52),
    'license_plate': (255, 255, 0)
}

roi_points = []

# =================================================================
# 2. CÁC HÀM HỖ TRỢ AI & LOGIC
# =================================================================

def send_telegram_async(bot_token, chat_id, text, img_path_full, img_path_crop, best_frame, best_crop):
    try:
        if best_frame is not None:
            cv2.imwrite(img_path_full, best_frame)
        if best_crop is not None:
            cv2.imwrite(img_path_crop, best_crop)

        url_photo = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

        def send_with_retry(files_dict, data_dict, retries=3):
            for attempt in range(retries):
                try:
                    res = requests.post(url_photo, data=data_dict, files=files_dict, timeout=15)
                    if res.status_code == 200:
                        return True
                    elif res.status_code == 429:
                        wait_time = int(res.json().get('parameters', {}).get('retry_after', 5))
                        print(f"⏳ Rate limit! Đợi {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"❌ Lỗi HTTP {res.status_code}: {res.text[:200]}")
                        break
                except requests.exceptions.Timeout:
                    print(f"⏳ Timeout lần {attempt+1}, thử lại...")
                    time.sleep(2)
            return False

        if os.path.exists(img_path_full):
            with open(img_path_full, "rb") as f1:
                send_with_retry({"photo": f1}, {"chat_id": chat_id, "caption": text})

        time.sleep(0.8)

        if os.path.exists(img_path_crop):
            with open(img_path_crop, "rb") as f2:
                send_with_retry({"photo": f2}, {"chat_id": chat_id, "caption": "🔎 Ảnh cận cảnh biển số"})

    except Exception as e:
        print(f"❌ Lỗi gửi Telegram: {e}")


# ✅ FIX BUG 1: khởi tạo tele_queue ở module level (dùng cache để không tạo lại khi Streamlit rerun)
@st.cache_resource
def init_telegram_queue():
    q = queue.Queue()

    def worker():
        while True:
            task = q.get()
            if task is None:
                break
            try:
                bot_token, chat_id, text, img_path_full, img_path_crop, best_frame, best_crop = task
                send_telegram_async(bot_token, chat_id, text, img_path_full, img_path_crop, best_frame, best_crop)
            except Exception as e:
                print(f"❌ Worker error: {e}")
            finally:
                q.task_done()
            time.sleep(0.5)

    for _ in range(5):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    return q

# ✅ FIX BUG 1: gọi ngay ở module level, KHÔNG để trong if/button
tele_queue = init_telegram_queue()


def trigger_telegram(data, track_id, save_dir, speed_limit_live, violation_type='SPEED'):
    if data.get('tele_sent'):
        return
    data['tele_sent'] = True
    data['pending_tele'] = False

    v_type_vn = VI_CLASS_MAP.get(data.get('v_class', 'car'), 'Phương tiện')
    plate_str = data.get('plate_text', 'Không rõ')
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if violation_type == 'REDLIGHT':
        alert_msg = f"🚨 PHÁT HIỆN VƯỢT ĐÈN ĐỎ!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n📌 Lỗi: {data.get('alert_msg')}\n⏱ Thời gian: {time_str}"
    elif violation_type == 'WRONGWAY':
        alert_msg = f"🚨 PHÁT HIỆN ĐI NGƯỢC CHIỀU!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n📌 Lỗi: Đi ngược chiều\n⏱ Thời gian: {time_str}"
    else:
        speed_report = data.get('violation_speed', int(data.get('speed', 0)))
        alert_msg = f"🚨 PHÁT HIỆN VI PHẠM TỐC ĐỘ!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n⚡ Tốc độ: {speed_report} km/h (QĐ: {speed_limit_live} km/h)\n⏱ Thời gian: {time_str}"

    img_name_full = f"full_{track_id}_{plate_str}.jpg"
    img_name_crop = f"crop_{track_id}_{plate_str}.jpg"
    img_path_full = os.path.join(save_dir, img_name_full)
    img_path_crop = os.path.join(save_dir, img_name_crop)

    tele_queue.put((
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, alert_msg,
        img_path_full, img_path_crop,
        data['best_frame'], data['best_crop']
    ))


def get_traffic_light_state(frame, roi):
    if roi == (0, 0, 0, 0):
        return 'UNKNOWN'
    x, y, w, h = roi
    crop = frame[y:y + h, x:x + w]
    if crop.size == 0:
        return 'UNKNOWN'
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask_red1 = cv2.inRange(hsv, np.array([0, 40, 100]), np.array([7, 255, 255]))
    mask_red2 = cv2.inRange(hsv, np.array([170, 40, 100]), np.array([180, 255, 255]))
    mask_red = mask_red1 | mask_red2
    mask_yellow = cv2.inRange(hsv, np.array([10, 40, 100]), np.array([35, 255, 255]))
    mask_green = cv2.inRange(hsv, np.array([40, 40, 100]), np.array([90, 255, 255]))
    r_c = cv2.countNonZero(mask_red)
    y_c = cv2.countNonZero(mask_yellow)
    g_c = cv2.countNonZero(mask_green)
    max_c = max(r_c, y_c, g_c)
    if max_c > 15:
        if max_c == y_c: return 'YELLOW'
        if max_c == r_c: return 'RED'
        if max_c == g_c: return 'GREEN'
    return 'UNKNOWN'


def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0], rect[2] = pts[np.argmin(s)], pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1], rect[3] = pts[np.argmin(diff)], pts[np.argmax(diff)]
    return rect


def draw_polygon(event, x, y, flags, param):
    global roi_points
    if event == cv2.EVENT_LBUTTONDOWN and len(roi_points) < 4:
        roi_points.append([x, y])
    elif event == cv2.EVENT_RBUTTONDOWN and len(roi_points) > 0:
        roi_points.pop()


def draw_line(event, x, y, flags, param):
    global roi_points
    if event == cv2.EVENT_LBUTTONDOWN and len(roi_points) < 2:
        roi_points.append([x, y])
    elif event == cv2.EVENT_RBUTTONDOWN and len(roi_points) > 0:
        roi_points.pop()


def correct_vietnamese_plate(text, vehicle_class):
    if len(text) < 7 or len(text) > 9:
        return text
    char_list = list(text)
    letter_mapping = {'0': 'D', '1': 'T', '2': 'Z', '3': 'E', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B', '9': 'P'}
    number_mapping = {'A': '4', 'G': '6', 'B': '8', 'O': '0', 'D': '0', 'S': '5', 'Z': '2', 'I': '1', 'T': '7', 'J': '3', 'L': '4', 'U': '0', 'E': '3', 'F': '7'}
    for i in range(min(2, len(char_list))):
        if char_list[i] in number_mapping:
            char_list[i] = number_mapping[char_list[i]]
    if len(char_list) > 2:
        if char_list[2] in letter_mapping:
            char_list[2] = letter_mapping[char_list[2]]
        elif char_list[2].isdigit():
            char_list[2] = letter_mapping.get(char_list[2], 'X')
    if vehicle_class in ['car', 'truck', 'bus']:
        for i in range(3, len(char_list)):
            if char_list[i] in number_mapping:
                char_list[i] = number_mapping[char_list[i]]
    else:
        for i in range(len(char_list) - 4, len(char_list)):
            if char_list[i] in number_mapping:
                char_list[i] = number_mapping[char_list[i]]
        if len(char_list) == 9:
            if char_list[4] in number_mapping:
                char_list[4] = number_mapping[char_list[4]]
    return "".join(char_list)


@st.cache_resource
def load_models():
    device = 0 if torch.cuda.is_available() else 'cpu'
    model = YOLO("yolov8small/best_small.pt", task='detect')
    reader = easyocr.Reader(['en'], gpu=True if device == 0 else False)
    return model, reader, device


# =================================================================
# ✅ FIX BUG 2: Tách setup OpenCV thành hàm riêng, chạy trên MAIN THREAD
# =================================================================
def run_opencv_setup(frame, run_speed, run_redlight, run_wrongway, DISPLAY_H, DISPLAY_W):
    """
    Toàn bộ cửa sổ OpenCV (imshow, setMouseCallback) chạy ở main thread.
    Trả về dict kết quả setup để truyền vào AI thread.
    """
    setup_result = {
        'ok': True,
        'cancel_reason': '',
        'M_matrix': None,
        'speed_polygon': None,
        'rl_light_straight_roi': (0, 0, 0, 0),
        'rl_monitor_polygon': None,
        'ww_polygon': None,
        'ww_vector': None,
    }

    if run_redlight:
        roi = cv2.selectROI("SETUP: Keo chuot chon Den Di Thang -> ENTER", frame, False, False)
        cv2.destroyWindow("SETUP: Keo chuot chon Den Di Thang -> ENTER")
        setup_result['rl_light_straight_roi'] = roi

        roi_points.clear()
        cv2.namedWindow("SETUP")
        cv2.setMouseCallback("SETUP", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "Chuot phai: Xoa 1 diem | 'C': Xoa sach | 'ESC': Huy", (10, 30), 0, 0.7, (0, 255, 255), 2)
            cv2.putText(temp, "Click 4 diem tao KHUNG GIAM SAT. Bam ENTER", (10, 60), 0, 0.7, (0, 0, 255), 2)
            for pt in roi_points:
                cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1:
                pts = np.array(roi_points, np.int32)
                cv2.polylines(temp, [pts], isClosed=(len(roi_points) == 4), color=(0, 255, 255), thickness=2)
                if len(roi_points) == 4:
                    ordered = order_points(np.array(roi_points, dtype="float32"))
                    cv2.line(temp, tuple(ordered[2].astype(int)), tuple(ordered[3].astype(int)), (0, 255, 255), 3)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4:
                setup_result['rl_monitor_polygon'] = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
            elif key in [ord('c'), ord('C')]:
                roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'] = False
                setup_result['cancel_reason'] = "Đã hủy thiết lập Vượt Đèn Đỏ!"
                return setup_result
        cv2.destroyWindow("SETUP")

    if run_speed:
        roi_points.clear()
        cv2.namedWindow("SETUP")
        cv2.setMouseCallback("SETUP", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "Chuot phai: Xoa 1 diem | 'C': Xoa sach | 'ESC': Huy", (10, 30), 0, 0.7, (0, 255, 255), 2)
            cv2.putText(temp, "Click 4 diem tao VUNG DO TOC DO. Xong bam ENTER", (10, 60), 0, 0.7, (255, 255, 0), 2)
            for pt in roi_points:
                cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1:
                cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (255, 255, 0), 2)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4:
                break
            elif key in [ord('c'), ord('C')]:
                roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'] = False
                setup_result['cancel_reason'] = "Đã hủy thiết lập Đo Tốc Độ!"
                return setup_result
        cv2.destroyWindow("SETUP")

        src_pts = order_points(np.array(roi_points, dtype="float32"))
        dst_w, dst_h = int(REAL_WIDTH_M * 100), int(REAL_HEIGHT_M * 100)
        setup_result['M_matrix'] = cv2.getPerspectiveTransform(
            src_pts,
            np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype="float32")
        )
        setup_result['speed_polygon'] = np.array(src_pts, np.int32)

    if run_wrongway:
        roi_points.clear()
        cv2.namedWindow("SETUP")
        cv2.setMouseCallback("SETUP", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "Chuot phai: Xoa 1 diem | 'C': Xoa sach | 'ESC': Huy", (10, 30), 0, 0.7, (0, 255, 255), 2)
            cv2.putText(temp, "VUNG CAM NGUOC CHIEU (Click 4 diem -> ENTER)", (10, 60), 0, 0.7, (255, 0, 255), 2)
            for pt in roi_points:
                cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1:
                cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (255, 0, 255), 2)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4:
                setup_result['ww_polygon'] = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
            elif key in [ord('c'), ord('C')]:
                roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'] = False
                setup_result['cancel_reason'] = "Đã hủy thiết lập Ngược Chiều!"
                return setup_result

        roi_points.clear()
        cv2.setMouseCallback("SETUP", draw_line)
        while True:
            temp = frame.copy()
            cv2.polylines(temp, [setup_result['ww_polygon']], True, (255, 0, 255), 2)
            cv2.putText(temp, "Chuot phai: Xoa 1 diem | 'C': Xoa sach | 'ESC': Huy", (10, 30), 0, 0.7, (0, 255, 255), 2)
            cv2.putText(temp, "MUI TEN HUONG DUNG (Click 2 diem -> ENTER)", (10, 60), 0, 0.7, (0, 255, 0), 2)
            for pt in roi_points:
                cv2.circle(temp, tuple(pt), 5, (0, 255, 0), -1)
            if len(roi_points) == 2:
                cv2.arrowedLine(temp, tuple(roi_points[0]), tuple(roi_points[1]), (0, 255, 0), 3, tipLength=0.1)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 2:
                setup_result['ww_vector'] = (roi_points[1][0] - roi_points[0][0], roi_points[1][1] - roi_points[0][1])
                break
            elif key in [ord('c'), ord('C')]:
                roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'] = False
                setup_result['cancel_reason'] = "Đã hủy thiết lập Vector Ngược Chiều!"
                return setup_result
        cv2.destroyWindow("SETUP")

    return setup_result


# =================================================================
# AI THREAD: chỉ xử lý video, KHÔNG gọi bất kỳ cv2.imshow nào
# =================================================================
def run_video_processing(tfile_path, setup_result, run_speed, run_redlight, run_wrongway,
                          speed_limit_live, allow_moto_right, allow_car_right,
                          DISPLAY_W, DISPLAY_H,
                          frame_queue, kpi_queue, stop_event):

    try:
        model, reader, AI_DEVICE = load_models()
        save_dir = "saved_plates"
        os.makedirs(save_dir, exist_ok=True)

        # Lấy kết quả setup từ main thread
        M_matrix          = setup_result['M_matrix']
        speed_polygon      = setup_result['speed_polygon']
        rl_light_straight_roi = setup_result['rl_light_straight_roi']
        rl_monitor_polygon = setup_result['rl_monitor_polygon']
        ww_polygon         = setup_result['ww_polygon']
        ww_vector          = setup_result['ww_vector']

        plate_buffer = {}
        vehicle_tracking_data = {}
        total_vehicles_counted = 0
        total_violations = 0

        cap = cv2.VideoCapture(tfile_path)
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = 0
        csv_file = "traffic_log.csv"
        PLATE_CONFIRM_TIMEOUT = 3.0

        while cap.isOpened():
            if stop_event.is_set():
                break

            success, frame = cap.read()
            if not success:
                break

            frame_count += 1
            video_current_time = frame_count / video_fps
            frame = cv2.resize(frame, (DISPLAY_W, DISPLAY_H))
            clean_frame = frame.copy()

            cur_light_s = get_traffic_light_state(clean_frame, rl_light_straight_roi) if run_redlight else 'UNKNOWN'

            # Vẽ vùng ROI
            if run_speed and speed_polygon is not None:
                cv2.polylines(frame, [speed_polygon], True, (255, 255, 0), 2)
            if run_redlight and rl_monitor_polygon is not None:
                cv2.polylines(frame, [rl_monitor_polygon], True, (0, 0, 255), 2)
                cv2.line(frame, tuple(rl_monitor_polygon[2]), tuple(rl_monitor_polygon[3]), (0, 255, 255), 3)
                color_light = (0, 255, 255) if cur_light_s == 'YELLOW' else ((0, 0, 255) if cur_light_s == 'RED' else (0, 255, 0))
                cv2.putText(frame, f"Den Thang: {cur_light_s}", (10, 120), 0, 0.8, color_light, 2)
            if run_wrongway and ww_polygon is not None:
                cv2.polylines(frame, [ww_polygon], True, (255, 0, 255), 2)

            results = model.track(clean_frame, persist=True, tracker="bytetrack.yaml",
                                   conf=0.3, imgsz=1024, device=AI_DEVICE, verbose=False)
            boxes = results[0].boxes
            current_frame_ids = set()

            if boxes is not None and boxes.id is not None:
                # --- VÒNG LẶP 1: CẬP NHẬT TRẠNG THÁI & KIỂM TRA VI PHẠM ---
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_id = int(box.cls[0])
                    class_name = model.names[cls_id]
                    track_id = int(box.id[0]) if box.id is not None else -1
                    if track_id != -1:
                        current_frame_ids.add(track_id)
                    center_x, bottom_y = int((x1 + x2) / 2), int(y2)

                    if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                        if track_id not in vehicle_tracking_data:
                            vehicle_tracking_data[track_id] = {
                                'history': [], 'speed': 0, 'recorded': False, 'v_class': class_name,
                                'plate_text': "Không rõ", 'tele_sent': False, 'pending_tele': False,
                                'max_area': 0, 'best_frame': None, 'best_crop': None, 'violation_speed': 0,
                                'rl_state': 'WAITING', 'entry_x': 0, 'entry_light_s': 'UNKNOWN',
                                'is_redlight_err': False, 'alert_msg': "",
                                'ww_state': 'WAITING', 'ww_start_pt': (0, 0), 'is_wrongway_err': False,
                                'pending_tele_time': None,
                                'needs_best_frame': False,
                            }

                        data = vehicle_tracking_data[track_id]
                        area = (x2 - x1) * (y2 - y1)
                        if area > data['max_area']:
                            data['max_area'] = area
                            data['best_crop'] = clean_frame[max(0, y1):min(DISPLAY_H, y2),
                                                             max(0, x1):min(DISPLAY_W, x2)].copy()

                        # LOGIC NGƯỢC CHIỀU
                        if run_wrongway and ww_polygon is not None and ww_vector is not None \
                                and not data['tele_sent'] and not data['is_wrongway_err']:
                            if cv2.pointPolygonTest(ww_polygon, (center_x, bottom_y), False) >= 0:
                                if data['ww_state'] == 'WAITING':
                                    data['ww_state'] = 'TRACKING'
                                    data['ww_start_pt'] = (center_x, bottom_y)
                                elif data['ww_state'] == 'TRACKING':
                                    sx, sy = data['ww_start_pt']
                                    if np.sqrt((center_x - sx) ** 2 + (bottom_y - sy) ** 2) > 60:
                                        v_car = (center_x - sx, bottom_y - sy)
                                        dot_p = v_car[0] * ww_vector[0] + v_car[1] * ww_vector[1]
                                        mag_c = np.sqrt(v_car[0] ** 2 + v_car[1] ** 2)
                                        mag_r = np.sqrt(ww_vector[0] ** 2 + ww_vector[1] ** 2)
                                        if mag_c > 0 and mag_r > 0 and (dot_p / (mag_c * mag_r)) < -0.5:
                                            data['pending_tele'] = True
                                            data['pending_tele_time'] = video_current_time
                                            data['is_wrongway_err'] = True
                                            data['ww_state'] = 'DONE'
                                            data['needs_best_frame'] = True 
                                            total_violations += 1
                                        else:
                                            data['ww_start_pt'] = (center_x, bottom_y)
                            else:
                                if data['ww_state'] == 'TRACKING':
                                    data['ww_state'] = 'DONE'

                        # LOGIC ĐÈN ĐỎ
                        if run_redlight and rl_monitor_polygon is not None \
                                and not data['tele_sent'] and not data['is_wrongway_err']:
                            in_monitor = cv2.pointPolygonTest(rl_monitor_polygon, (center_x, bottom_y), False) >= 0
                            b_edge_y = max(rl_monitor_polygon[2][1], rl_monitor_polygon[3][1])
                            if in_monitor and data['rl_state'] == 'WAITING':
                                data['rl_state'] = 'IN_ZONE'
                                data['entry_x'] = center_x
                                data['entry_light_s'] = cur_light_s if abs(bottom_y - b_edge_y) < 100 else 'SAFE'
                            elif data['rl_state'] == 'IN_ZONE' and not in_monitor:
                                data['rl_state'] = 'DONE'
                                if bottom_y < b_edge_y - 50:
                                    dx = center_x - data['entry_x']
                                    direction = 'LEFT' if dx < -VECTOR_TURN_THRESHOLD else (
                                        'RIGHT' if dx > VECTOR_TURN_THRESHOLD else 'STRAIGHT')
                                    violation, msg = False, ""
                                    if direction == 'STRAIGHT' and data['entry_light_s'] == 'RED':
                                        violation, msg = True, "Đi thẳng lúc Đèn Đỏ"
                                    elif direction == 'LEFT' and data['entry_light_s'] == 'RED':
                                        violation, msg = True, "Rẽ trái lúc Đèn Đỏ"
                                    elif direction == 'RIGHT' and data['entry_light_s'] == 'RED':
                                        v_class = data['v_class']   # ← class được gán lúc đầu, không bao giờ sai
                                        if not ((v_class == 'motorcycle' and allow_moto_right) or
                                                (v_class == 'car' and allow_car_right)):
                                            violation, msg = True, "Rẽ phải lúc Đèn Đỏ"
                                    if violation:
                                        data['pending_tele'] = True
                                        data['pending_tele_time'] = video_current_time
                                        data['is_redlight_err'] = True
                                        data['alert_msg'] = msg
                                        
                                        total_violations += 1

                        # LOGIC TỐC ĐỘ
                        if run_speed and M_matrix is not None and speed_polygon is not None \
                                and not data['tele_sent'] and not data['is_redlight_err'] and not data['is_wrongway_err']:
                            if cv2.pointPolygonTest(speed_polygon, (center_x, bottom_y), False) >= 0:
                                pt = np.array([[[center_x, bottom_y]]], dtype="float32")
                                bev = cv2.perspectiveTransform(pt, M_matrix)[0][0]
                                data['history'].append((video_current_time, bev[0], bev[1]))
                                if len(data['history']) >= 3:
                                    t1, x1b, y1b = data['history'][0]
                                    t2, x2b, y2b = data['history'][-1]
                                    dt = t2 - t1
                                    d_m = np.sqrt((x2b - x1b) ** 2 + (y2b - y1b) ** 2) / 100
                                    if dt > 0 and d_m > 3.0:
                                        curr_spd = (d_m / dt) * 3.6
                                        data['speed'] = curr_spd if data['speed'] == 0 else data['speed'] * 0.7 + curr_spd * 0.3
                                        if not data['recorded']:
                                            data['recorded'] = True
                                            total_vehicles_counted += 1
                                        if int(data['speed']) > speed_limit_live and not data['pending_tele']:
                                            data['pending_tele'] = True
                                            data['pending_tele_time'] = video_current_time
                                            data['violation_speed'] = int(data['speed'])
                                            data['needs_best_frame'] = True 
                                            total_violations += 1

                # --- VÒNG LẶP 2: VẼ ---
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_id = int(box.cls[0])
                    class_name = model.names[cls_id]
                    track_id = int(box.id[0]) if box.id is not None else -1
                    if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                        if track_id in vehicle_tracking_data:
                            data = vehicle_tracking_data[track_id]
                            box_color = COLOR_MAP.get(class_name, (255, 255, 255))
                            speed_int = int(data['speed'])
                            text_color = box_color
                            label = f"ID:{track_id} {class_name}"
                            if run_speed and speed_int > 0:
                                label += f" {speed_int}km/h"
                            if data['is_wrongway_err']:
                                label += " [NGUOC CHIEU]"
                                text_color = box_color = (0, 0, 255)
                            elif data['is_redlight_err']:
                                label += " [VUOT DEN DO]"
                                text_color = box_color = (0, 0, 255)
                            elif run_speed and speed_int > speed_limit_live:
                                text_color = (0, 0, 255)
                            thickness = 4 if data['pending_tele'] else 2
                            cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, thickness)
                            cv2.putText(frame, label, (x1, y1 - 10), 0, 0.6, text_color, 2)
                    elif class_name == 'license_plate':
                        cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_MAP['license_plate'], 2)
                for v_id, data in vehicle_tracking_data.items():
                    if data.get('pending_tele') and not data.get('tele_sent'):
                        data['best_frame'] = frame.copy()
                        data['needs_best_frame'] = False 
                # --- VÒNG LẶP 3: OCR ---
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_id = int(box.cls[0])
                    class_name = model.names[cls_id]

                    if class_name == 'license_plate':
                        if (x2 - x1) < 25:
                            continue
                        crop_y1 = max(0, int(y1 - (y2 - y1) * 0.05))
                        crop_y2 = min(DISPLAY_H, int(y2 + (y2 - y1) * 0.05))
                        crop_x1 = max(0, int(x1 - (x2 - x1) * 0.05))
                        crop_x2 = min(DISPLAY_W, int(x2 + (x2 - x1) * 0.05))
                        if crop_y2 <= crop_y1 or crop_x2 <= crop_x1:
                            continue

                        plate_crop = clean_frame[crop_y1:crop_y2, crop_x1:crop_x2]
                        plate_crop_large = cv2.resize(plate_crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                        gray = cv2.cvtColor(plate_crop_large, cv2.COLOR_BGR2GRAY)
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                        enhanced = clahe.apply(gray)
                        blur = cv2.GaussianBlur(enhanced, (3, 3), 0)
                        morph = cv2.erode(blur, np.ones((2, 2), np.uint8), iterations=1)

                        ocr_result = reader.readtext(morph,
                                                      allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                                                      decoder='beamsearch', detail=1)
                        if ocr_result:
                            valid_results = [r for r in ocr_result if r[2] > 0.3]
                            if valid_results:
                                valid_results.sort(key=lambda x: x[0][0][1])
                                raw_text = re.sub(r'[^A-Z0-9]', '', "".join([r[1] for r in valid_results]))
                                clean_text = correct_vietnamese_plate(raw_text, 'car')
                                if 7 <= len(clean_text) <= 9 and sum(c.isdigit() for c in clean_text) >= 4:
                                    if len(clean_text) == 9 or (len(clean_text) == 8 and clean_text[3].isalpha()):
                                        plate_text = clean_text[:4] + "-" + clean_text[4:]
                                    else:
                                        plate_text = clean_text[:3] + "-" + clean_text[3:]

                                    cv2.putText(frame, f"BS: {plate_text}", (x1, y2 + 20), 0, 0.8, (255, 255, 0), 2)
                                    px, py = (x1 + x2) // 2, (y1 + y2) // 2
                                    v_id = -1
                                    for v_box in boxes:
                                        v_cls = int(v_box.cls[0])
                                        if model.names[v_cls] in ['car', 'motorcycle', 'truck', 'bus']:
                                            vx1, vy1, vx2, vy2 = map(int, v_box.xyxy[0])
                                            if (vx1 - 20) <= px <= (vx2 + 20) and (vy1 - 20) <= py <= (vy2 + 20):
                                                v_id = int(v_box.id[0]) if v_box.id is not None else -1
                                                break

                                    if v_id != -1 and v_id in vehicle_tracking_data:
                                        if v_id not in plate_buffer:
                                            plate_buffer[v_id] = []
                                        plate_buffer[v_id].append(plate_text)
                                        data = vehicle_tracking_data[v_id]

                                        if plate_buffer[v_id].count(plate_text) >= 2:
                                            if not data['tele_sent'] and data['pending_tele']:
                                                data['plate_text'] = plate_text
                                                data['best_crop'] = morph

                                                time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                                                with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                                    writer = csv.writer(f)
                                                    writer.writerow([time_str, v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_text])

                                                v_type = 'WRONGWAY' if data['is_wrongway_err'] else (
                                                    'REDLIGHT' if data['is_redlight_err'] else 'SPEED')
                                                trigger_telegram(data, v_id, save_dir, speed_limit_live, violation_type=v_type)

                # Timeout: gửi luôn nếu chờ quá lâu hoặc xe rời màn hình
                for v_id, data in vehicle_tracking_data.items():
                    if (data.get('pending_tele') and not data.get('tele_sent')
                            and data.get('pending_tele_time') is not None):
                        elapsed = video_current_time - data['pending_tele_time']
                        if elapsed > PLATE_CONFIRM_TIMEOUT or v_id not in current_frame_ids:
                            v_type = 'WRONGWAY' if data['is_wrongway_err'] else (
                                'REDLIGHT' if data['is_redlight_err'] else 'SPEED')
                            trigger_telegram(data, v_id, save_dir, speed_limit_live, violation_type=v_type)

            # Đẩy frame ra queue để Streamlit hiển thị
            if frame_count % 3 == 0:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                try:
                    frame_queue.put_nowait(frame_rgb)
                except queue.Full:
                    pass
                try:
                    kpi_queue.put_nowait(("KPI", total_vehicles_counted, total_violations))
                except queue.Full:
                    pass

        cap.release()

        # Chờ Telegram gửi xong trước khi báo DONE
        print("⏳ Video xong, đang chờ Telegram gửi nốt...")
        tele_queue.join()
        kpi_queue.put(("DONE", total_vehicles_counted, total_violations))

    except Exception as e:
        import traceback
        err_msg = f"{e}\n{traceback.format_exc()}"
        print(f"❌ AI Thread crash: {err_msg}")
        kpi_queue.put(("ERROR", str(e)))


# =================================================================
# 3. GIAO DIỆN STREAMLIT WEB
# =================================================================
st.set_page_config(page_title="Hệ Thống ITS Đa Năng", layout="wide")
st.title("🚦 HỆ THỐNG GIÁM SÁT GIAO THÔNG THÔNG MINH (AI DASHBOARD)")

st.sidebar.header("🎛️ KHỐI CẤU HÌNH & CHỨC NĂNG")
uploaded_file = st.sidebar.file_uploader("📂 Tải Video Lên", type=["mp4", "avi", "mov", "jpg", "png", "jpeg"])

st.sidebar.markdown("### 🛠️ Kích Hoạt Tính Năng")
run_speed    = st.sidebar.checkbox("⚡ Đo Tốc Độ", value=True)
run_redlight = st.sidebar.checkbox("🚥 Bắt Vượt Đèn Đỏ", value=False)
run_wrongway = st.sidebar.checkbox("⛔ Bắt Ngược Chiều", value=False)

st.sidebar.markdown("### ⚙️ Tùy Chỉnh Chuyên Sâu")
speed_limit_live = st.sidebar.slider("Giới hạn tốc độ (km/h):", 20, 120, 55)

st.sidebar.markdown("#### Đặc quyền Đèn Đỏ")
allow_moto_right = st.sidebar.checkbox("✅ Cho phép Xe Máy rẽ phải", value=True)
allow_car_right  = st.sidebar.checkbox("✅ Cho phép Ô Tô rẽ phải", value=False)

col1, col2 = st.columns([2.5, 1])
with col1:
    st.markdown("### 🎥 Bảng Điều Khiển Live Camera")
    stframe = st.empty()
with col2:
    st.markdown("### 📊 Thông Số Thời Gian Thực")
    kpi_count = st.empty()
    kpi_viol  = st.empty()
    st.markdown("---")
    st.info("💡 **HƯỚNG DẪN SETUP:**\n\n- **Chuột Trái:** Chọn điểm.\n- **Chuột Phải:** Xóa 1 điểm.\n- Phím **'C'**: Xóa toàn bộ.\n- Phím **'ESC'**: Hủy & Dừng.\n- Phím **ENTER**: Lưu.")

# =================================================================
# 4. ĐIỀU PHỐI: SETUP Ở MAIN THREAD → AI Ở BACKGROUND THREAD
# =================================================================
if uploaded_file is not None:
    if st.sidebar.button("▶️ BẮT ĐẦU CHẠY HỆ THỐNG", use_container_width=True):

        tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        tfile.write(uploaded_file.read())
        tfile.flush()

        cap_init = cv2.VideoCapture(tfile.name)
        ok, first_frame = cap_init.read()
        cap_init.release()

        if not ok:
            st.error("❌ Không đọc được file video!")
            st.stop()

        orig_h, orig_w = first_frame.shape[:2]
        DISPLAY_W = 1280
        DISPLAY_H = int(orig_h * (1280 / orig_w))
        first_frame = cv2.resize(first_frame, (DISPLAY_W, DISPLAY_H))

        st.info("⚙️ Cửa sổ setup đang mở...")
        setup_result = run_opencv_setup(first_frame, run_speed, run_redlight, run_wrongway, DISPLAY_H, DISPLAY_W)

        if not setup_result['ok']:
            st.warning(f"🛑 {setup_result['cancel_reason']}")
            st.stop()

        st.success("✅ Setup hoàn tất! Đang khởi động AI...")

        # ✅ Lưu vào session_state để rerun không tạo lại
        frame_queue = queue.Queue(maxsize=5)
        kpi_queue   = queue.Queue(maxsize=20)
        stop_event  = threading.Event()

        ai_thread = threading.Thread(
            target=run_video_processing,
            args=(
                tfile.name, setup_result,
                run_speed, run_redlight, run_wrongway,
                speed_limit_live, allow_moto_right, allow_car_right,
                DISPLAY_W, DISPLAY_H,
                frame_queue, kpi_queue, stop_event
            ),
            daemon=True
        )

        st.session_state['ai_thread']   = ai_thread
        st.session_state['frame_queue'] = frame_queue
        st.session_state['kpi_queue']   = kpi_queue
        st.session_state['stop_event']  = stop_event
        st.session_state['running']     = True

        # ✅ Chỉ start 1 lần duy nhất tại đây
        ai_thread.start()

    # ✅ Vòng hiển thị chạy khi session_state có running=True
    if st.session_state.get('running'):
        ai_thread   = st.session_state['ai_thread']
        frame_queue = st.session_state['frame_queue']
        kpi_queue   = st.session_state['kpi_queue']
        stop_event  = st.session_state['stop_event']

        while ai_thread.is_alive() or not frame_queue.empty():
            try:
                msg = kpi_queue.get_nowait()
                if msg[0] == "KPI":
                    kpi_count.metric("Lưu lượng xe qua lại", msg[1])
                    kpi_viol.metric("Tổng vi phạm phát hiện", msg[2])
                elif msg[0] == "DONE":
                    kpi_count.metric("Lưu lượng xe qua lại", msg[1])
                    kpi_viol.metric("Tổng vi phạm phát hiện", msg[2])
                    st.success("✅ Video xử lý xong. Telegram đã gửi đầy đủ!")
                    st.session_state['running'] = False
                    break
                elif msg[0] == "WARN":
                    st.warning(f"🛑 {msg[1]}")
                    st.session_state['running'] = False
                    break
                elif msg[0] == "ERROR":
                    st.error(f"❌ Lỗi AI: {msg[1]}")
                    st.session_state['running'] = False
                    break
            except queue.Empty:
                pass

            try:
                frame_rgb = frame_queue.get(timeout=0.05)
                stframe.image(frame_rgb, channels="RGB", use_container_width=True)
            except queue.Empty:
                pass

        stop_event.set()
        ai_thread.join(timeout=10)