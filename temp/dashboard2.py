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
import pandas as pd
import numpy as np
import plotly.express as px
import xml.etree.ElementTree as ET
import plotly.graph_objects as go
# =================================================================
# 1. BIẾN TOÀN CỤC & TỪ ĐIỂN
# =================================================================
REAL_WIDTH_M = 14.0
REAL_HEIGHT_M = 6.0
LOCATION_NAME = "Ngã tư Demo, TP.HCM"
TELEGRAM_BOT_TOKEN = "8724545022:AAEgeJZ8nE6zj5utIDb85C3dpNgzGcwsn2g"
TELEGRAM_CHAT_ID = "8066570830"
VECTOR_TURN_THRESHOLD = 80
CSV_FILE_PATH = "traffic_log.csv"

VI_CLASS_MAP = {'car': 'Ô tô', 'motorcycle': 'Xe máy', 'truck': 'Xe tải', 'bus': 'Xe buýt'}
COLOR_MAP = {'motorcycle': (255, 0, 0), 'bus': (0, 255, 255), 'car': (0, 255, 0), 'truck': (235, 134, 52), 'license_plate': (255, 255, 0)}
roi_points = []

# TẠO FILE CSV NẾU CHƯA CÓ KÈM TIÊU ĐỀ CHUẨN
if not os.path.isfile(CSV_FILE_PATH):
    with open(CSV_FILE_PATH, mode='w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(['Thời Gian', 'ID Xe', 'Loại Phương Tiện', 'Tốc Độ (km/h)', 'Biển Số', 'Trạng Thái'])

# =================================================================
# 2. CÁC HÀM HỖ TRỢ AI & LOGIC
# =================================================================
def send_telegram_async(bot_token, chat_id, text, img_path_full, img_path_crop, best_frame, best_crop):
    try:
        if best_frame is not None: cv2.imwrite(img_path_full, best_frame)
        if best_crop is not None: cv2.imwrite(img_path_crop, best_crop)
        url_photo = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        def send_with_retry(files_dict, data_dict, retries=3):
            for attempt in range(retries):
                try:
                    res = requests.post(url_photo, data=data_dict, files=files_dict, timeout=15)
                    if res.status_code == 200: return True
                    elif res.status_code == 429: time.sleep(int(res.json().get('parameters', {}).get('retry_after', 5)))
                    else: break
                except requests.exceptions.Timeout: time.sleep(2)
            return False
        if os.path.exists(img_path_full):
            with open(img_path_full, "rb") as f1: send_with_retry({"photo": f1}, {"chat_id": chat_id, "caption": text})
        time.sleep(0.8)
        if os.path.exists(img_path_crop):
            with open(img_path_crop, "rb") as f2: send_with_retry({"photo": f2}, {"chat_id": chat_id, "caption": "🔎 Ảnh cận cảnh biển số"})
    except Exception: pass

@st.cache_resource
def init_telegram_queue():
    q = queue.Queue()
    def worker():
        while True:
            task = q.get()
            if task is None: break
            try: send_telegram_async(*task)
            except Exception: pass
            finally: q.task_done()
            time.sleep(0.5)
    for _ in range(5): threading.Thread(target=worker, daemon=True).start()
    return q

tele_queue = init_telegram_queue()

def trigger_telegram(data, track_id, save_dir, speed_limit_live, violation_type='SPEED'):
    if data.get('tele_sent'): return
    data['tele_sent'], data['pending_tele'] = True, False
    v_type_vn = VI_CLASS_MAP.get(data.get('v_class', 'car'), 'Phương tiện')
    plate_str, time_str = data.get('plate_text', 'Không rõ'), datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if violation_type == 'TRAFFIC_JAM':
        alert_msg = f"🚨 CẢNH BÁO KẸT XE!\n📍 Địa điểm: {LOCATION_NAME}\n🚥 Mật độ: Cao ({data.get('jam_count')} xe)\n⏱ T.Gian: {time_str}"
    elif violation_type == 'REDLIGHT':
        alert_msg = f"🚨 VƯỢT ĐÈN ĐỎ!\n📍 {LOCATION_NAME}\n🚘 {v_type_vn} | 🆔 {plate_str}\n📌 Lỗi: {data.get('alert_msg')}\n⏱ {time_str}"
    elif violation_type == 'WRONGWAY':
        alert_msg = f"🚨 ĐI NGƯỢC CHIỀU!\n📍 {LOCATION_NAME}\n🚘 {v_type_vn} | 🆔 {plate_str}\n⏱ {time_str}"
    else:
        speed_report = data.get('violation_speed', int(data.get('speed', 0)))
        alert_msg = f"🚨 VI PHẠM TỐC ĐỘ!\n📍 {LOCATION_NAME}\n🚘 {v_type_vn} | 🆔 {plate_str}\n⚡ {speed_report} km/h (QĐ: {speed_limit_live})\n⏱ {time_str}"

    img_name_full = f"full_{track_id}_{plate_str}.jpg"
    img_name_crop = f"crop_{track_id}_{plate_str}.jpg"
    img_path_full = os.path.join(save_dir, img_name_full)
    img_path_crop = os.path.join(save_dir, img_name_crop) if data.get('best_crop') is not None else ""
    tele_queue.put((TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, alert_msg, img_path_full, img_path_crop, data['best_frame'], data.get('best_crop')))

def get_traffic_light_state(frame, roi):
    if roi == (0, 0, 0, 0): return 'UNKNOWN'
    crop = frame[roi[1]:roi[1] + roi[3], roi[0]:roi[0] + roi[2]]
    if crop.size == 0: return 'UNKNOWN'
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask_red = cv2.inRange(hsv, np.array([0, 40, 100]), np.array([7, 255, 255])) | cv2.inRange(hsv, np.array([170, 40, 100]), np.array([180, 255, 255]))
    mask_yellow = cv2.inRange(hsv, np.array([10, 40, 100]), np.array([35, 255, 255]))
    mask_green = cv2.inRange(hsv, np.array([40, 40, 100]), np.array([90, 255, 255]))
    r_c, y_c, g_c = cv2.countNonZero(mask_red), cv2.countNonZero(mask_yellow), cv2.countNonZero(mask_green)
    max_c = max(r_c, y_c, g_c)
    return 'YELLOW' if max_c == y_c and max_c > 15 else ('RED' if max_c == r_c and max_c > 15 else ('GREEN' if max_c == g_c and max_c > 15 else 'UNKNOWN'))

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s, diff = pts.sum(axis=1), np.diff(pts, axis=1)
    rect[0], rect[2], rect[1], rect[3] = pts[np.argmin(s)], pts[np.argmax(s)], pts[np.argmin(diff)], pts[np.argmax(diff)]
    return rect

def draw_polygon(event, x, y, flags, param):
    global roi_points
    if event == cv2.EVENT_LBUTTONDOWN and len(roi_points) < 4: roi_points.append([x, y])
    elif event == cv2.EVENT_RBUTTONDOWN and len(roi_points) > 0: roi_points.pop()

def draw_line(event, x, y, flags, param):
    global roi_points
    if event == cv2.EVENT_LBUTTONDOWN and len(roi_points) < 2: roi_points.append([x, y])
    elif event == cv2.EVENT_RBUTTONDOWN and len(roi_points) > 0: roi_points.pop()

def correct_vietnamese_plate(text, vehicle_class):
    char_list = list(re.sub(r'[^A-Z0-9]', '', text))
    if len(char_list) < 7 or len(char_list) > 9: return text
    letter_map = {'0': 'D', '1': 'T', '2': 'Z', '3': 'E', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B', '9': 'P'}
    number_map = {'A': '4', 'G': '6', 'B': '8', 'O': '0', 'D': '0', 'S': '5', 'Z': '2', 'I': '1', 'T': '7', 'J': '3', 'L': '4', 'U': '0', 'E': '3', 'F': '7'}
    for i in range(min(2, len(char_list))):
        if char_list[i] in number_map: char_list[i] = number_map[char_list[i]]
    if len(char_list) > 2:
        if char_list[2] in letter_map: char_list[2] = letter_map[char_list[2]]
        elif char_list[2].isdigit(): char_list[2] = letter_map.get(char_list[2], 'X')
    start_idx = 3 if vehicle_class in ['car', 'truck', 'bus'] else len(char_list) - 4
    for i in range(start_idx, len(char_list)):
        if char_list[i] in number_map: char_list[i] = number_map[char_list[i]]
    if len(char_list) == 9 and char_list[4] in number_map: char_list[4] = number_map[char_list[4]]
    res = "".join(char_list)
    return res[:4] + "-" + res[4:] if len(res) == 9 or (len(res) == 8 and res[3].isalpha()) else res[:3] + "-" + res[3:]

@st.cache_resource
def load_models():
    device = 0 if torch.cuda.is_available() else 'cpu'
    model = YOLO("yolov8small/best_small.pt", task='detect')
    reader = easyocr.Reader(['en'], gpu=True if device == 0 else False)
    return model, reader, device

# =================================================================
# 3. SETUP THREAD CHÍNH (KHỞI TẠO OpenCV ROI)
# =================================================================
def run_opencv_setup(frame, run_speed, run_redlight, run_wrongway, run_heatmap, DISPLAY_H, DISPLAY_W):
    setup_result = {
        'ok': True, 'cancel_reason': '', 'M_matrix': None, 'speed_polygon': None,
        'rl_light_straight_roi': (0, 0, 0, 0), 'rl_monitor_polygon': None,
        'ww_polygon': None, 'ww_vector': None, 'heatmap_polygon': None
    }
    # CÁC BƯỚC SETUP TƯƠNG TỰ BẢN V6
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
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1:
                cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (0, 255, 255), 2)
                if len(roi_points) == 4:
                    ordered = order_points(np.array(roi_points, dtype="float32"))
                    cv2.line(temp, tuple(ordered[2].astype(int)), tuple(ordered[3].astype(int)), (0, 255, 255), 3)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4:
                setup_result['rl_monitor_polygon'] = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
            elif key in [ord('c'), ord('C')]: roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Vượt Đèn Đỏ!"
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
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1: cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (255, 255, 0), 2)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4: break
            elif key in [ord('c'), ord('C')]: roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Đo Tốc Độ!"
                return setup_result
        cv2.destroyWindow("SETUP")
        src_pts = order_points(np.array(roi_points, dtype="float32"))
        dst_w, dst_h = int(REAL_WIDTH_M * 100), int(REAL_HEIGHT_M * 100)
        setup_result['M_matrix'] = cv2.getPerspectiveTransform(src_pts, np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype="float32"))
        setup_result['speed_polygon'] = np.array(src_pts, np.int32)

    if run_wrongway:
        roi_points.clear()
        cv2.namedWindow("SETUP")
        cv2.setMouseCallback("SETUP", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "VUNG CAM NGUOC CHIEU (Click 4 diem -> ENTER)", (10, 30), 0, 0.7, (255, 0, 255), 2)
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1: cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (255, 0, 255), 2)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4:
                setup_result['ww_polygon'] = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
            elif key in [ord('c'), ord('C')]: roi_points.clear()
            elif key == 27: return {'ok': False, 'cancel_reason': "Đã hủy thiết lập!"}
        roi_points.clear()
        cv2.setMouseCallback("SETUP", draw_line)
        while True:
            temp = frame.copy()
            cv2.polylines(temp, [setup_result['ww_polygon']], True, (255, 0, 255), 2)
            cv2.putText(temp, "MUI TEN HUONG DUNG (Click 2 diem -> ENTER)", (10, 30), 0, 0.7, (0, 255, 0), 2)
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0, 255, 0), -1)
            if len(roi_points) == 2: 
                cv2.arrowedLine(temp, tuple(roi_points[0]), tuple(roi_points[1]), (0, 255, 0), 3, tipLength=0.1)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 2:
                setup_result['ww_vector'] = (roi_points[1][0] - roi_points[0][0], roi_points[1][1] - roi_points[0][1])
                break
            elif key in [ord('c'), ord('C')]: roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Vector!"
                return setup_result
        cv2.destroyWindow("SETUP")

    if run_heatmap:
        roi_points.clear()
        cv2.namedWindow("SETUP")
        cv2.setMouseCallback("SETUP", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "Chuot phai: Xoa 1 diem | 'C': Xoa sach | 'ESC': Huy", (10, 30), 0, 0.7, (0, 255, 255), 2)
            cv2.putText(temp, "VUNG DO MAT DO (Kiem tra Ket xe) - Click 4 diem -> ENTER", (10, 60), 0, 0.7, (0, 165, 255), 2)
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0, 0, 255), -1)
            if len(roi_points) > 1: cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (0, 165, 255), 2)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 4:
                setup_result['heatmap_polygon'] = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
            elif key in [ord('c'), ord('C')]: roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows()
                setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Heatmap!"
                return setup_result
        cv2.destroyWindow("SETUP")

    return setup_result


# =================================================================
# 4. BACKGROUND THREAD: XỬ LÝ AI & LOGIC
# =================================================================
def run_video_processing(tfile_path, setup_result, run_speed, run_redlight, run_wrongway, run_heatmap,
                         speed_limit_live, allow_moto_right, allow_car_right, jam_threshold, live_config,
                         DISPLAY_W, DISPLAY_H, frame_queue, kpi_queue, stop_event):
    try:
        model, reader, AI_DEVICE = load_models()
        save_dir = "saved_plates"
        os.makedirs(save_dir, exist_ok=True)

        M_matrix, speed_polygon = setup_result['M_matrix'], setup_result['speed_polygon']
        rl_light_straight_roi, rl_monitor_polygon = setup_result['rl_light_straight_roi'], setup_result['rl_monitor_polygon']
        ww_polygon, ww_vector = setup_result['ww_polygon'], setup_result['ww_vector']
        heatmap_polygon = setup_result['heatmap_polygon']

        plate_buffer, vehicle_tracking_data = {}, {}
        total_vehicles_counted, total_violations = 0, 0

        # Khởi tạo ma trận nhiệt dài hạn
        heatmap_matrix = np.zeros((DISPLAY_H, DISPLAY_W), dtype=np.float32)
        jam_start_time, jam_alert_sent = None, False

        cap = cv2.VideoCapture(tfile_path)
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = 0
        csv_file = "traffic_log.csv"
        PLATE_CONFIRM_TIMEOUT = 3.0

        while cap.isOpened():
            if stop_event.is_set(): break
            success, frame = cap.read()
            if not success: break

            frame_count += 1
            video_current_time = frame_count / video_fps
            frame = cv2.resize(frame, (DISPLAY_W, DISPLAY_H))
            clean_frame = frame.copy()

            cur_light_s = get_traffic_light_state(clean_frame, rl_light_straight_roi) if run_redlight else 'UNKNOWN'
            results = model.track(clean_frame, persist=True, tracker="bytetrack.yaml", conf=0.3, imgsz=1024, device=AI_DEVICE, verbose=False)
            boxes = results[0].boxes
            current_frame_ids = set()

            jam_count = 0
            temp_heat = np.zeros((DISPLAY_H, DISPLAY_W), dtype=np.float32)

            if boxes is not None and boxes.id is not None:
                # --- VÒNG 1: LOGIC KIỂM TRA VI PHẠM ---
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    class_name = model.names[int(box.cls[0])]
                    track_id = int(box.id[0]) if box.id is not None else -1
                    if track_id != -1: current_frame_ids.add(track_id)
                    center_x, bottom_y = int((x1 + x2) / 2), int(y2)

                    if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                        if track_id not in vehicle_tracking_data:
                            vehicle_tracking_data[track_id] = {
                                'history': [], 'speed': 0, 'recorded': False, 'v_class': class_name,
                                'plate_text': "Không rõ", 'tele_sent': False, 'pending_tele': False,
                                'max_area': 0, 'best_frame': None, 'best_crop': None, 'violation_speed': 0,
                                'rl_state': 'WAITING', 'entry_x': 0, 'entry_light_s': 'UNKNOWN', 'is_redlight_err': False, 'alert_msg': "",
                                'ww_state': 'WAITING', 'ww_start_pt': (0, 0), 'is_wrongway_err': False,
                                'pending_tele_time': None, 'needs_best_frame': False
                            }
                        data = vehicle_tracking_data[track_id]

                        # VẼ LUỒNG NHIỆT (Heatmap Flow)
                        if live_config.get('show_heatmap', False):
                            if heatmap_polygon is not None and cv2.pointPolygonTest(heatmap_polygon, (center_x, bottom_y), False) >= 0:
                                jam_count += 1
                            elif heatmap_polygon is None:
                                jam_count += 1 
                            
                            veh_width = x2 - x1
                            heat_thickness = max(10, int(veh_width * 0.8))
                            if 'prev_center' in data:
                                px, py = data['prev_center']
                                cv2.line(temp_heat, (px, py), (center_x, bottom_y), 5.0, thickness=heat_thickness)
                            else:
                                cv2.circle(temp_heat, (center_x, bottom_y), heat_thickness // 2, 5.0, -1)
                            data['prev_center'] = (center_x, bottom_y)

                        if (x2 - x1) * (y2 - y1) > data['max_area']:
                            data['max_area'] = (x2 - x1) * (y2 - y1)
                            data['best_crop'] = clean_frame[max(0, y1):min(DISPLAY_H, y2), max(0, x1):min(DISPLAY_W, x2)].copy()

                        # Logic Đi Ngược Chiều
                        if run_wrongway and not data['tele_sent'] and not data['is_wrongway_err'] and ww_polygon is not None:
                            if cv2.pointPolygonTest(ww_polygon, (center_x, bottom_y), False) >= 0:
                                if data['ww_state'] == 'WAITING':
                                    data['ww_state'], data['ww_start_pt'] = 'TRACKING', (center_x, bottom_y)
                                elif data['ww_state'] == 'TRACKING':
                                    sx, sy = data['ww_start_pt']
                                    if np.sqrt((center_x - sx) ** 2 + (bottom_y - sy) ** 2) > 60:
                                        v_car = (center_x - sx, bottom_y - sy)
                                        dot_p = v_car[0] * ww_vector[0] + v_car[1] * ww_vector[1]
                                        mag_c, mag_r = np.sqrt(v_car[0]**2 + v_car[1]**2), np.sqrt(ww_vector[0]**2 + ww_vector[1]**2)
                                        if mag_c > 0 and mag_r > 0 and (dot_p / (mag_c * mag_r)) < -0.5:
                                            data['pending_tele'], data['is_wrongway_err'], data['needs_best_frame'] = True, True, True
                                            data['pending_tele_time'], data['ww_state'] = video_current_time, 'DONE'
                                            total_violations += 1
                                        else: data['ww_start_pt'] = (center_x, bottom_y)
                            else:
                                if data['ww_state'] == 'TRACKING': data['ww_state'] = 'DONE'

                        # Logic Đèn Đỏ
                        if run_redlight and not data['tele_sent'] and not data['is_wrongway_err'] and rl_monitor_polygon is not None:
                            in_monitor = cv2.pointPolygonTest(rl_monitor_polygon, (center_x, bottom_y), False) >= 0
                            b_edge_y = max(rl_monitor_polygon[2][1], rl_monitor_polygon[3][1])
                            if in_monitor and data['rl_state'] == 'WAITING':
                                data['rl_state'], data['entry_x'] = 'IN_ZONE', center_x
                                data['entry_light_s'] = cur_light_s if abs(bottom_y - b_edge_y) < 100 else 'SAFE'
                            elif data['rl_state'] == 'IN_ZONE' and not in_monitor:
                                data['rl_state'] = 'DONE'
                                if bottom_y < b_edge_y - 50:
                                    dx = center_x - data['entry_x']
                                    direction = 'LEFT' if dx < -VECTOR_TURN_THRESHOLD else ('RIGHT' if dx > VECTOR_TURN_THRESHOLD else 'STRAIGHT')
                                    v, msg = False, ""
                                    if data['entry_light_s'] == 'RED':
                                        if direction == 'STRAIGHT': v, msg = True, "Đi thẳng lúc Đèn Đỏ"
                                        elif direction == 'LEFT': v, msg = True, "Rẽ trái lúc Đèn Đỏ"
                                        elif direction == 'RIGHT' and not ((class_name == 'motorcycle' and allow_moto_right) or (class_name == 'car' and allow_car_right)):
                                            v, msg = True, "Rẽ phải lúc Đèn Đỏ"
                                    if v:
                                        data['pending_tele'], data['is_redlight_err'], data['needs_best_frame'] = True, True, True
                                        data['pending_tele_time'], data['alert_msg'] = video_current_time, msg
                                        total_violations += 1

                        # Logic Tốc Độ
                        if run_speed and not data['tele_sent'] and not data['is_redlight_err'] and not data['is_wrongway_err'] and speed_polygon is not None:
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
                                            data['pending_tele'], data['needs_best_frame'] = True, True
                                            data['pending_tele_time'], data['violation_speed'] = video_current_time, int(data['speed'])
                                            total_violations += 1

            # --- RENDER HEATMAP ---
            if live_config.get('show_heatmap', False):
                heatmap_matrix += temp_heat
                heatmap_matrix = np.clip(heatmap_matrix, 0, 255)
                heatmap_matrix *= 0.985 
                
                heatmap_blurred = cv2.GaussianBlur(heatmap_matrix, (61, 61), 0)
                heat_norm = np.clip(heatmap_blurred, 0, 180)
                heat_norm = (heat_norm / 180.0 * 255).astype(np.uint8)
                heat_color = cv2.applyColorMap(heat_norm, cv2.COLORMAP_JET)
                
                alpha = (heat_norm / 255.0) * 0.75 
                alpha = alpha[..., np.newaxis]
                frame = (heat_color * alpha + frame * (1 - alpha)).astype(np.uint8)

                msg_density = f"Density: {jam_count} / {jam_threshold}"
                if heatmap_polygon is not None:
                    cv2.polylines(frame, [heatmap_polygon], True, (0, 165, 255), 2)
                    cv2.putText(frame, msg_density, (heatmap_polygon[0][0], heatmap_polygon[0][1] - 10), 0, 0.7, (0, 165, 255), 2)
                else:
                    cv2.putText(frame, msg_density, (10, 160), 0, 0.7, (0, 165, 255), 2)

                if jam_count >= jam_threshold:
                    if jam_start_time is None: jam_start_time = video_current_time
                    else:
                        jam_timeout = 30.0 if cur_light_s == 'RED' else 5.0 
                        if video_current_time - jam_start_time > jam_timeout and not jam_alert_sent:
                            jam_data = {'tele_sent': False, 'jam_count': jam_count, 'best_frame': frame.copy(), 'best_crop': None}
                            trigger_telegram(jam_data, "JAM", save_dir, speed_limit_live, violation_type='TRAFFIC_JAM')
                            jam_alert_sent = True
                else:
                    jam_start_time, jam_alert_sent = None, False

            # VẼ UI TĨNH BÊN TRONG VIDEO
            if run_speed and speed_polygon is not None: cv2.polylines(frame, [speed_polygon], True, (255, 255, 0), 2)
            if run_redlight and rl_monitor_polygon is not None:
                cv2.polylines(frame, [rl_monitor_polygon], True, (0, 0, 255), 2)
                cv2.line(frame, tuple(rl_monitor_polygon[2]), tuple(rl_monitor_polygon[3]), (0, 255, 255), 3)
                cv2.putText(frame, f"Den Thang: {cur_light_s}", (10, 120), 0, 0.8, (0, 255, 255) if cur_light_s == 'YELLOW' else ((0, 0, 255) if cur_light_s == 'RED' else (0, 255, 0)), 2)
            if run_wrongway and ww_polygon is not None: cv2.polylines(frame, [ww_polygon], True, (255, 0, 255), 2)

            if boxes is not None and boxes.id is not None:
                # --- VÒNG 2: VẼ GIAO DIỆN LÊN TẤT CẢ XE ---
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    class_name = model.names[int(box.cls[0])]
                    track_id = int(box.id[0]) if box.id is not None else -1

                    if class_name in ['car', 'motorcycle', 'truck', 'bus'] and track_id in vehicle_tracking_data:
                        data = vehicle_tracking_data[track_id]
                        box_color, speed_int = COLOR_MAP.get(class_name, (255, 255, 255)), int(data['speed'])
                        label, text_color = f"ID:{track_id} {class_name}", box_color
                        if run_speed and speed_int > 0: label += f" {speed_int}km/h"
                        if data['is_wrongway_err']: label, text_color, box_color = label + " [NGUOC CHIEU]", (0, 0, 255), (0, 0, 255)
                        elif data['is_redlight_err']: label, text_color, box_color = label + " [VUOT DEN DO]", (0, 0, 255), (0, 0, 255)
                        elif run_speed and speed_int > speed_limit_live: text_color = (0, 0, 255)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 4 if data['pending_tele'] else 2)
                        cv2.putText(frame, label, (x1, y1 - 10), 0, 0.6, text_color, 2)
                    elif class_name == 'license_plate': cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_MAP['license_plate'], 2)

                # --- VÒNG 3: LƯU ẢNH CHỨA KHUNG ĐẦY ĐỦ ---
                for v_id, data in vehicle_tracking_data.items():
                    if data.get('needs_best_frame'):
                        data['best_frame'] = frame.copy()
                        data['needs_best_frame'] = False 

                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    if model.names[int(box.cls[0])] == 'license_plate' and (x2 - x1) >= 25:
                        cy1, cy2 = max(0, int(y1 - (y2 - y1)*0.05)), min(DISPLAY_H, int(y2 + (y2 - y1)*0.05))
                        cx1, cx2 = max(0, int(x1 - (x2 - x1)*0.05)), min(DISPLAY_W, int(x2 + (x2 - x1)*0.05))
                        if cy2 > cy1 and cx2 > cx1:
                            plate_crop = cv2.resize(clean_frame[cy1:cy2, cx1:cx2], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                            gray = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY)
                            morph = cv2.erode(cv2.GaussianBlur(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray), (3, 3), 0), np.ones((2, 2), np.uint8), iterations=1)
                            ocr_res = reader.readtext(morph, allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', decoder='beamsearch', detail=1)
                            if ocr_res:
                                val_res = [r for r in ocr_res if r[2] > 0.3]
                                if val_res:
                                    val_res.sort(key=lambda x: x[0][0][1])
                                    raw_text = re.sub(r'[^A-Z0-9]', '', "".join([r[1] for r in val_res]))
                                    cln_txt = correct_vietnamese_plate(raw_text, 'car')
                                    if 7 <= len(cln_txt) <= 9 and sum(c.isdigit() for c in cln_txt) >= 4:
                                        plate_txt = cln_txt[:4] + "-" + cln_txt[4:] if len(cln_txt) == 9 or (len(cln_txt) == 8 and cln_txt[3].isalpha()) else cln_txt[:3] + "-" + cln_txt[3:]
                                        cv2.putText(frame, f"BS: {plate_txt}", (x1, y2 + 20), 0, 0.8, (255, 255, 0), 2)
                                        px, py = (x1 + x2) // 2, (y1 + y2) // 2
                                        for v_box in boxes:
                                            if model.names[int(v_box.cls[0])] in ['car', 'motorcycle', 'truck', 'bus']:
                                                vx1, vy1, vx2, vy2 = map(int, v_box.xyxy[0])
                                                if (vx1 - 20) <= px <= (vx2 + 20) and (vy1 - 20) <= py <= (vy2 + 20):
                                                    v_id = int(v_box.id[0]) if v_box.id is not None else -1
                                                    if v_id in vehicle_tracking_data:
                                                        if v_id not in plate_buffer: plate_buffer[v_id] = []
                                                        plate_buffer[v_id].append(plate_txt)
                                                        if plate_buffer[v_id].count(plate_txt) >= 2:
                                                            data = vehicle_tracking_data[v_id]
                                                            if not data['tele_sent'] and data['pending_tele']:
                                                                data['plate_text'], data['best_crop'] = plate_txt, morph
                                                                csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                                                                
                                                                # GHI LOG DATA VÀO CSV
                                                                v_type = 'WRONGWAY' if data['is_wrongway_err'] else ('REDLIGHT' if data['is_redlight_err'] else 'SPEED')
                                                                with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                                                    csv.writer(f).writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type])
                                                                
                                                                trigger_telegram(data, v_id, save_dir, speed_limit_live, violation_type=v_type)
                                                    break
                # =========================================================
                # Timeout xử lý rời màn hình (SỬA LỖI KHÔNG GHI CSV Ở ĐÂY)
                # =========================================================
                for v_id, data in vehicle_tracking_data.items():
                    if data.get('pending_tele') and not data.get('tele_sent') and data.get('pending_tele_time') is not None:
                        # Nếu chờ quá 3 giây (Timeout) hoặc xe đã chạy khuất khỏi camera
                        if video_current_time - data['pending_tele_time'] > PLATE_CONFIRM_TIMEOUT or v_id not in current_frame_ids:
                            v_type = 'WRONGWAY' if data['is_wrongway_err'] else ('REDLIGHT' if data['is_redlight_err'] else 'SPEED')
                            
                            # THÊM LOGIC GHI CSV CHO XE KHÔNG ĐỌC ĐƯỢC BIỂN SỐ
                            plate_txt = data.get('plate_text', 'Không rõ') # Mặc định là Không rõ
                            csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                            time_str_csv = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            
                            with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                writer = csv.writer(f)
                                writer.writerow([time_str_csv, v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type])
                            
                            # Gửi cảnh báo Telegram
                            trigger_telegram(data, v_id, save_dir, speed_limit_live, violation_type=v_type)
            # Cập nhật Web qua Queue
            if frame_count % 3 == 0:
                try: frame_queue.put_nowait(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                except queue.Full: pass
                try: kpi_queue.put_nowait(("KPI", total_vehicles_counted, total_violations))
                except queue.Full: pass

        cap.release()
        tele_queue.join()
        kpi_queue.put(("DONE", total_vehicles_counted, total_violations))

    except Exception as e:
        kpi_queue.put(("ERROR", str(e)))

# =================================================================
# 5. STREAMLIT WEB APP & ANALYTICS DASHBOARD
# =================================================================
st.set_page_config(page_title="Hệ Thống ITS Đa Năng", layout="wide")
st.title("🚦 HỆ THỐNG GIÁM SÁT GIAO THÔNG THÔNG MINH (AI DASHBOARD)")

# --- STATE CHO HEATMAP LIVES ---
if 'live_config' not in st.session_state: st.session_state['live_config'] = {'show_heatmap': False}

# --- TẠO 2 TABS CHÍNH ---
tab_monitor, tab_analytics = st.tabs(["🎥 Giám Sát Trực Tiếp", "📊 Phân Tích Dữ Liệu (Analytics)"])

# =================================================================
# TAB 1: GIÁM SÁT TRỰC TIẾP
# =================================================================
with tab_monitor:
    st.sidebar.header("🎛️ KHỐI CẤU HÌNH & CHỨC NĂNG")
    uploaded_file = st.sidebar.file_uploader("📂 Tải Video Lên", type=["mp4", "avi", "mov", "jpg", "png", "jpeg"])

    run_just_detect = st.sidebar.checkbox("🔍 Nhận Diện Tự Do (Bỏ qua Setup)", value=False)
    if run_just_detect: run_speed, run_redlight, run_wrongway = False, False, False
    else:
        st.sidebar.markdown("### 🛠️ Kích Hoạt Tính Năng")
        run_speed    = st.sidebar.checkbox("⚡ Đo Tốc Độ", value=True)
        run_redlight = st.sidebar.checkbox("🚥 Bắt Vượt Đèn Đỏ", value=False)
        run_wrongway = st.sidebar.checkbox("⛔ Bắt Ngược Chiều", value=False)

    st.sidebar.markdown("### ⚙️ Tùy Chỉnh Chuyên Sâu")
    speed_limit_live = st.sidebar.slider("Giới hạn tốc độ (km/h):", 20, 120, 55)
    jam_threshold = st.sidebar.slider("Ngưỡng cảnh báo kẹt xe (Số xe):", 5, 50, 15)

    show_hm = st.sidebar.toggle("🔥 Bật/Tắt Heatmap (Live)", value=st.session_state['live_config']['show_heatmap'])
    st.session_state['live_config']['show_heatmap'] = show_hm

    run_heatmap = False
    if not run_just_detect and show_hm:
        run_heatmap = st.sidebar.checkbox("🎯 Đo Kẹt xe trong Vùng Tự Chọn", value=False)

    if not run_just_detect:
        st.sidebar.markdown("#### Đặc quyền Đèn Đỏ")
        allow_moto_right = st.sidebar.checkbox("✅ Cho phép Xe Máy rẽ phải", value=True)
        allow_car_right  = st.sidebar.checkbox("✅ Cho phép Ô Tô rẽ phải", value=False)
    else: allow_moto_right, allow_car_right = False, False

    if st.sidebar.button("🛑 DỪNG & TẮT HỆ THỐNG", type="primary", use_container_width=True):
        if 'stop_event' in st.session_state: st.session_state['stop_event'].set()
        st.session_state['running'] = False
        st.rerun()

    col1, col2 = st.columns([2.5, 1])
    with col1:
        st.markdown("### 🎥 Camera Live")
        stframe = st.empty()
    with col2:
        st.markdown("### 📊 Thông Số")
        kpi_count = st.empty()
        kpi_viol  = st.empty()
        st.markdown("---")
        if not run_just_detect: st.info("💡 **HƯỚNG DẪN SETUP:**\n- **Chuột Trái:** Chọn điểm.\n- **Chuột Phải:** Xóa 1 điểm.\n- Phím **'C'**: Xóa toàn bộ.\n- Phím **'ESC'**: Hủy & Dừng.\n- Phím **ENTER**: Lưu.")

    if uploaded_file is not None:
        if st.sidebar.button("▶️ BẮT ĐẦU CHẠY", use_container_width=True):
            tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
            tfile.write(uploaded_file.read())
            tfile.flush()

            cap_init = cv2.VideoCapture(tfile.name)
            ok, first_frame = cap_init.read()
            cap_init.release()

            if not ok: st.error("❌ Lỗi file video!"); st.stop()

            orig_h, orig_w = first_frame.shape[:2]
            DISPLAY_W, DISPLAY_H = 1280, int(orig_h * (1280 / orig_w))
            first_frame = cv2.resize(first_frame, (DISPLAY_W, DISPLAY_H))

            if not run_just_detect:
                st.info("⚙️ Cửa sổ setup đang mở...")
                setup_result = run_opencv_setup(first_frame, run_speed, run_redlight, run_wrongway, run_heatmap, DISPLAY_H, DISPLAY_W)
                if not setup_result['ok']: st.warning(f"🛑 {setup_result['cancel_reason']}"); st.stop()
            else:
                setup_result = {'M_matrix': None, 'speed_polygon': None, 'rl_light_straight_roi': (0,0,0,0), 'rl_monitor_polygon': None, 'ww_polygon': None, 'ww_vector': None, 'heatmap_polygon': None}

            st.success("✅ Đang khởi động AI...")

            frame_queue, kpi_queue, stop_event = queue.Queue(maxsize=5), queue.Queue(maxsize=20), threading.Event()
            ai_thread = threading.Thread(target=run_video_processing, args=(tfile.name, setup_result, run_speed, run_redlight, run_wrongway, run_heatmap, speed_limit_live, allow_moto_right, allow_car_right, jam_threshold, st.session_state['live_config'], DISPLAY_W, DISPLAY_H, frame_queue, kpi_queue, stop_event), daemon=True)
            
            st.session_state['ai_thread'] = ai_thread
            st.session_state['frame_queue'] = frame_queue
            st.session_state['kpi_queue'] = kpi_queue
            st.session_state['stop_event'] = stop_event
            st.session_state['running'] = True
            ai_thread.start()

        if st.session_state.get('running'):
            ai_thread = st.session_state['ai_thread']
            frame_queue = st.session_state['frame_queue']
            kpi_queue = st.session_state['kpi_queue']
            stop_event = st.session_state['stop_event']

            while ai_thread.is_alive() or not frame_queue.empty():
                try:
                    msg = kpi_queue.get_nowait()
                    if msg[0] == "KPI":
                        kpi_count.metric("Lưu lượng xe qua lại", msg[1])
                        kpi_viol.metric("Tổng vi phạm phát hiện", msg[2])
                    elif msg[0] == "DONE":
                        kpi_count.metric("Lưu lượng xe qua lại", msg[1])
                        kpi_viol.metric("Tổng vi phạm phát hiện", msg[2])
                        st.success("✅ Video xử lý xong.")
                        st.session_state['running'] = False
                        break
                    elif msg[0] == "ERROR":
                        st.error(f"❌ Lỗi AI: {msg[1]}")
                        st.session_state['running'] = False
                        break
                except queue.Empty: pass

                try:
                    frame_rgb = frame_queue.get(timeout=0.05)
                    stframe.image(frame_rgb, channels="RGB", use_container_width=True)
                except queue.Empty: pass

            stop_event.set()
            ai_thread.join(timeout=10)
import plotly.graph_objects as go
import plotly.express as px

# =================================================================
# TAB 2: PRO TRAFFIC ANALYTICS DASHBOARD (ENTERPRISE LEVEL)
# =================================================================
with tab_analytics:
    # Bật chế độ nền tối toàn cục cho biểu đồ
    import plotly.io as pio
    pio.templates.default = "plotly_dark"

    st.markdown("<h2 style='text-align: center; color: #00E5FF;'>🌐 TRUNG TÂM ĐIỀU HÀNH GIAO THÔNG THÔNG MINH</h2>", unsafe_allow_html=True)
    st.markdown("---")

    col_upload, col_action = st.columns([4, 1])
    with col_upload:
        csv_upload = st.file_uploader("📂 Tải lên Dữ liệu Giao thông (traffic_dataset.csv)", type=["csv"])
    with col_action:
        st.write("") # Căn lề
        st.write("")
        if st.button("🔄 Cập nhật Dashboard", use_container_width=True):
            st.rerun()

    df = None
    if csv_upload is not None:
        try:
            df = pd.read_csv(csv_upload, on_bad_lines='skip', encoding='utf-8')
        except Exception as e:
            st.error(f"Lỗi đọc file: {e}")
    elif os.path.exists("traffic_dataset.csv"):
        try:
            df = pd.read_csv("traffic_dataset.csv", on_bad_lines='skip', encoding='utf-8')
        except: pass

    if df is not None and not df.empty:
        # ==========================================
        # 1. TIỀN XỬ LÝ DỮ LIỆU (DATA WRANGLING)
        # ==========================================
        col_map = {
            'Timestamp': 'Thời Gian', 'Time': 'Thời Gian',
            'Avg Speed (km/h)': 'Tốc Độ', 'Vehicle_Speed': 'Tốc Độ', 'Tốc Độ (km/h)': 'Tốc Độ',
            'Vehicle Count': 'Lưu Lượng', 'Count': 'Lưu Lượng', 'Lưu Lượng': 'Lưu Lượng',
            'Vehicle Density (%)': 'Mật Độ', 'Mật Độ (%)': 'Mật Độ',
            'Traffic Congestion Level': 'Mức Ùn Tắc', 'Congestion_Level': 'Mức Ùn Tắc', 'Trạng Thái': 'Mức Ùn Tắc',
            'Vehicle Types Detected': 'Chi Tiết Xe'
        }
        df = df.rename(columns=col_map)
        df.columns = df.columns.str.strip()

        if 'Thời Gian' in df.columns:
            df['Thời Gian'] = pd.to_datetime(df['Thời Gian'], errors='coerce')
            df = df.dropna(subset=['Thời Gian']).sort_values('Thời Gian')

        # ==========================================
        # 2. HÀNG 1: ĐỒNG HỒ ĐO KPI (GAUGE CHARTS)
        # ==========================================
        st.markdown("#### ⚡ CHỈ SỐ HOẠT ĐỘNG CHÍNH (REAL-TIME KPIs)")
        g1, g2, g3, g4 = st.columns(4)

        # Tính toán giá trị trung bình/tổng
        avg_speed = pd.to_numeric(df['Tốc Độ'], errors='coerce').mean() if 'Tốc Độ' in df.columns else 0
        avg_density = pd.to_numeric(df['Mật Độ'], errors='coerce').mean() if 'Mật Độ' in df.columns else 0
        total_vehicles = pd.to_numeric(df['Lưu Lượng'], errors='coerce').sum() if 'Lưu Lượng' in df.columns else len(df)

        with g1:
            # Đồng hồ Tốc độ (Gauge)
            fig_spd = go.Figure(go.Indicator(
                mode="gauge+number",
                value=avg_speed,
                number={'suffix': " km/h", 'font': {'color': '#00E5FF', 'size': 30}},
                title={'text': "Tốc Độ Trung Bình", 'font': {'size': 16, 'color': 'white'}},
                gauge={
                    'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': "white"},
                    'bar': {'color': "#00E5FF"},
                    'bgcolor': "rgba(255,255,255,0.1)",
                    'steps': [
                        {'range': [0, 20], 'color': "rgba(255, 75, 75, 0.5)"}, # Đỏ (Chậm)
                        {'range': [20, 50], 'color': "rgba(255, 215, 0, 0.5)"}, # Vàng (TB)
                        {'range': [50, 100], 'color': "rgba(0, 229, 255, 0.5)"}  # Xanh (Nhanh)
                    ]
                }
            ))
            fig_spd.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_spd, use_container_width=True)

        with g2:
            # Đồng hồ Mật độ (Gauge)
            fig_den = go.Figure(go.Indicator(
                mode="gauge+number",
                value=avg_density,
                number={'suffix': " %", 'font': {'color': '#FF4B4B', 'size': 30}},
                title={'text': "Mật Độ / Chiếm Dụng", 'font': {'size': 16, 'color': 'white'}},
                gauge={
                    'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': "white"},
                    'bar': {'color': "#FF4B4B"},
                    'bgcolor': "rgba(255,255,255,0.1)",
                    'steps': [
                        {'range': [0, 50], 'color': "rgba(0, 229, 255, 0.3)"},
                        {'range': [50, 80], 'color': "rgba(255, 215, 0, 0.3)"},
                        {'range': [80, 100], 'color': "rgba(255, 75, 75, 0.5)"}
                    ]
                }
            ))
            fig_den.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_den, use_container_width=True)

        with g3:
            # Indicator Chỉ số lớn
            fig_vol = go.Figure(go.Indicator(
                mode="number+delta",
                value=total_vehicles,
                title={'text': "Tổng Phương Tiện", 'font': {'size': 16, 'color': 'white'}},
                number={'font': {'color': '#00FF7F', 'size': 40}},
                delta={'reference': total_vehicles * 0.9, 'relative': True, 'position': "top"}
            ))
            fig_vol.update_layout(height=250, margin=dict(l=10, r=10, t=60, b=10), paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_vol, use_container_width=True)

        with g4:
            # Trạng thái phổ biến nhất
            pop_status = df['Mức Ùn Tắc'].mode()[0] if 'Mức Ùn Tắc' in df.columns else "N/A"
            status_color = "#FF4B4B" if pop_status in ["High", "Đỏ"] else ("#FFD700" if pop_status in ["Medium", "Vàng"] else "#00FF7F")
            
            st.markdown(f"""
            <div style="background-color: rgba(255,255,255,0.05); padding: 30px; border-radius: 10px; height: 250px; display: flex; flex-direction: column; justify-content: center; align-items: center; border: 1px solid rgba(255,255,255,0.1);">
                <p style="color: white; font-size: 16px; margin: 0;">Trạng Thái Hiện Tại</p>
                <h1 style="color: {status_color}; font-size: 45px; margin: 0; text-transform: uppercase;">{pop_status}</h1>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        # ==========================================
        # 3. HÀNG 2: TRENDS & COMPOSITION (AREA & DONUT)
        # ==========================================
        c_left, c_right = st.columns([2, 1])

        with c_left:
            if 'Thời Gian' in df.columns and 'Lưu Lượng' in df.columns:
                st.markdown("#### 📈 Chu Kỳ Lưu Lượng (Traffic Flow Trend)")
                # Biểu đồ Diện tích (Area Chart) có Gradient nền
                fig_area = px.area(df, x="Thời Gian", y="Lưu Lượng", color_discrete_sequence=['#00E5FF'])
                fig_area.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                    margin=dict(l=0, r=0, t=20, b=0)
                )
                fig_area.update_traces(fillcolor="rgba(0, 229, 255, 0.2)") # Đổ màu mờ mờ ở dưới y hệt PowerBI
                st.plotly_chart(fig_area, use_container_width=True)

        with c_right:
            if 'Chi Tiết Xe' in df.columns:
                st.markdown("#### 🍩 Cơ Cấu Phương Tiện")
                # Thuật toán bóc tách dữ liệu chuỗi (VD: "10 Bikes, 90 Ambulance")
                v_types = {}
                for row in df['Chi Tiết Xe'].dropna():
                    for item in str(row).split(','):
                        parts = item.strip().split(' ')
                        if len(parts) >= 2 and parts[0].isdigit():
                            v_name = ' '.join(parts[1:])
                            v_types[v_name] = v_types.get(v_name, 0) + int(parts[0])
                
                if v_types:
                    df_types = pd.DataFrame(list(v_types.items()), columns=['Loại Xe', 'Số Lượng'])
                    fig_donut = px.pie(df_types, names='Loại Xe', values='Số Lượng', hole=0.6, 
                                       color_discrete_sequence=px.colors.qualitative.Pastel)
                    fig_donut.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=20, b=0),
                        legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5)
                    )
                    st.plotly_chart(fig_donut, use_container_width=True)
            
            elif 'Mức Ùn Tắc' in df.columns:
                st.markdown("#### 🍩 Cơ Cấu Ùn Tắc")
                fig_donut = px.pie(df, names='Mức Ùn Tắc', hole=0.6, color_discrete_sequence=['#FF4B4B', '#FFD700', '#00FF7F'])
                fig_donut.update_layout(paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=20, b=0))
                st.plotly_chart(fig_donut, use_container_width=True)

        st.markdown("---")

        # ==========================================
        # 4. HÀNG 3: ANALYTICAL DEEP DIVE (SCATTER & HISTOGRAM)
        # ==========================================
        c_bot1, c_bot2 = st.columns(2)
        
        with c_bot1:
            if 'Tốc Độ' in df.columns and 'Mật Độ' in df.columns:
                st.markdown("#### ⚖️ Mối Tương Quan: Tốc Độ vs Mật Độ")
                # Biểu đồ Scatter chứng minh quy luật giao thông
                fig_scatter = px.scatter(df, x="Tốc Độ", y="Mật Độ", color="Mức Ùn Tắc" if 'Mức Ùn Tắc' in df.columns else None,
                                         trendline="ols", trendline_color_override="#00FF7F",
                                         color_discrete_sequence=['#FF4B4B', '#FFD700', '#00E5FF'])
                fig_scatter.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"), 
                    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                    margin=dict(l=0, r=0, t=20, b=0)
                )
                st.plotly_chart(fig_scatter, use_container_width=True)

        with c_bot2:
            if 'Tốc Độ' in df.columns:
                st.markdown("#### 📊 Tần Suất Vi Phạm / Mức Tốc Độ")
                fig_hist = px.histogram(df, x="Tốc Độ", nbins=15, color_discrete_sequence=['#9D00FF'])
                fig_hist.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                    margin=dict(l=0, r=0, t=20, b=0)
                )
                st.plotly_chart(fig_hist, use_container_width=True)

    else:
        st.info("👈 Hãy tải file `traffic_dataset.csv` lên để khởi động Bảng Điều Khiển!")