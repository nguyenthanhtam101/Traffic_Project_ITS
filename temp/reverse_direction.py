import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

# Ép Terminal xuất tiếng Việt có dấu
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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

# =================================================================
# 1. CẤU HÌNH HỆ THỐNG VÀ TELEGRAM
# =================================================================
REAL_WIDTH_M = 14.0   
REAL_HEIGHT_M = 6.0 
SPEED_LIMIT = 55.0   

LOCATION_NAME = "Xa lộ Hà Nội, TP.HCM"

# ĐIỀN CHUẨN TOKEN VÀ ID CỦA BẠN VÀO ĐÂY
TELEGRAM_BOT_TOKEN = "8724545022:AAEgeJZ8nE6zj5utIDb85C3dpNgzGcwsn2g"
TELEGRAM_CHAT_ID = "8066570830"

# CẤU HÌNH ĐÈN ĐỎ
ALLOW_MOTO_RIGHT_TURN_ON_RED = True 
HAS_LEFT_TURN_LIGHT = False          
VECTOR_TURN_THRESHOLD = 80  

# TỪ ĐIỂN DỊCH TÊN XE SANG TIẾNG VIỆT
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

# BIẾN TOÀN CỤC (GIỮ NGUYÊN)
roi_points = [] 
M_matrix = None 
plate_buffer = {} 
vehicle_tracking_data = {} 
total_vehicles_counted = 0

# =================================================================
# 2. CÁC HÀM HỖ TRỢ VÀ THUẬT TOÁN
# =================================================================
def send_telegram_alert(bot_token, chat_id, text, img_path_full, img_path_crop):
    try:
        print(f"\n[TELEGRAM] Đang gửi cảnh báo vi phạm...")
        url_photo = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        
        if img_path_full and os.path.exists(img_path_full):
            with open(img_path_full, "rb") as f1:
                res1 = requests.post(url_photo, data={"chat_id": chat_id, "caption": text}, files={"photo": f1})
                if res1.status_code != 200: print(f"❌ Lỗi gửi ảnh toàn cảnh: {res1.text}")
                
        if img_path_crop and os.path.exists(img_path_crop):
            with open(img_path_crop, "rb") as f2:
                res2 = requests.post(url_photo, data={"chat_id": chat_id, "caption": "🔎 Ảnh cận cảnh (Biển số / Phương tiện)"}, files={"photo": f2})
                if res2.status_code != 200: print(f"❌ Lỗi gửi ảnh cận cảnh: {res2.text}")
                
        print(f"[TELEGRAM] ✅ TING TING! ĐÃ GỬI THÀNH CÔNG!\n")
    except Exception as e:
        print(f"❌ [LỖI TELEGRAM NGHIÊM TRỌNG]: {e}")

def trigger_telegram(data, track_id, save_dir, violation_type='SPEED'):
    if data.get('tele_sent'): return
    data['tele_sent'] = True
    data['pending_tele'] = False 
    
    v_type_vn = VI_CLASS_MAP.get(data.get('v_class', 'car'), 'Phương tiện')
    plate_str = data.get('plate_text', 'Không rõ')
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Phân nhánh tin nhắn theo loại vi phạm
    if violation_type == 'REDLIGHT':
        alert_msg = f"🚨 PHÁT HIỆN VƯỢT ĐÈN ĐỎ!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n📌 Lỗi: {data.get('alert_msg')}\n⏱ Thời gian: {time_str}"
    elif violation_type == 'WRONGWAY':
        alert_msg = f"🚨 PHÁT HIỆN ĐI NGƯỢC CHIỀU!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n📌 Lỗi: Đi ngược chiều\n⏱ Thời gian: {time_str}"
    else:
        speed_report = data.get('violation_speed', int(data.get('speed', 0)))
        alert_msg = f"🚨 PHÁT HIỆN VI PHẠM TỐC ĐỘ!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n⚡ Tốc độ: {speed_report} km/h (QĐ: {int(SPEED_LIMIT)})\n⏱ Thời gian: {time_str}"
    
    img_name_full = f"full_{track_id}_{plate_str}.jpg"
    img_name_crop = f"crop_{track_id}_{plate_str}.jpg"
    img_path_full = os.path.join(save_dir, img_name_full)
    img_path_crop = os.path.join(save_dir, img_name_crop)
    
    if data['best_frame'] is not None: cv2.imwrite(img_path_full, data['best_frame'])
    if data['best_crop'] is not None: cv2.imwrite(img_path_crop, data['best_crop'])
    
    threading.Thread(target=send_telegram_alert, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, alert_msg, img_path_full, img_path_crop)).start()

def get_traffic_light_state(frame, roi):
    if roi == (0,0,0,0): return 'UNKNOWN'
    x, y, w, h = roi
    crop = frame[y:y+h, x:x+w]
    if crop.size == 0: return 'UNKNOWN'
    
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

# HÀM MỚI ĐỂ VẼ VECTOR HƯỚNG ĐI
def draw_line(event, x, y, flags, param):
    global roi_points
    if event == cv2.EVENT_LBUTTONDOWN and len(roi_points) < 2:
        roi_points.append([x, y])
    elif event == cv2.EVENT_RBUTTONDOWN and len(roi_points) > 0:
        roi_points.pop()

def correct_vietnamese_plate(text, vehicle_class):
    if len(text) < 7 or len(text) > 9: return text
    char_list = list(text)
    letter_mapping = {'0': 'D', '1': 'T', '2': 'Z', '3': 'E', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B', '9': 'P'}
    number_mapping = {'A': '4', 'G': '6', 'B': '8', 'O': '0', 'D': '0', 'S': '5', 'Z': '2', 'I': '1', 'T': '7', 'J': '3', 'L': '4', 'U': '0', 'E': '3', 'F': '7'}
    for i in range(min(2, len(char_list))):
        if char_list[i] in number_mapping: char_list[i] = number_mapping[char_list[i]]
    if len(char_list) > 2:
        if char_list[2] in letter_mapping: char_list[2] = letter_mapping[char_list[2]]
        elif char_list[2].isdigit(): char_list[2] = letter_mapping.get(char_list[2], 'X')
    if vehicle_class in ['car', 'truck', 'bus']:
        for i in range(3, len(char_list)):
            if char_list[i] in number_mapping: char_list[i] = number_mapping[char_list[i]]
    else:
        for i in range(len(char_list) - 4, len(char_list)):
            if char_list[i] in number_mapping: char_list[i] = number_mapping[char_list[i]]
        if len(char_list) == 9:
            if char_list[4] in number_mapping: char_list[4] = number_mapping[char_list[4]]
    return "".join(char_list)

# =================================================================
# 3. CHƯƠNG TRÌNH CHÍNH (MAIN)
# =================================================================
def main():
    global roi_points, M_matrix, total_vehicles_counted, plate_buffer, vehicle_tracking_data
    
    print("========================================")
    print("HỆ THỐNG GIÁM SÁT GIAO THÔNG THÔNG MINH")
    print("========================================")
    print("1. Chế độ Đo Tốc Độ")
    print("2. Chế độ Bắt Vượt Đèn Đỏ (Vector)")
    choice = input("Vui lòng chọn (1/2): ").strip()
    
    ww_choice = input("Bạn có muốn TÍCH HỢP thêm tính năng bắt ĐI NGƯỢC CHIỀU? (y/n): ").strip().lower()

    RUN_SPEED = choice == '1'
    RUN_REDLIGHT = choice == '2'
    RUN_WRONGWAY = ww_choice == 'y'

    save_dir = "saved_plates"
    os.makedirs(save_dir, exist_ok=True)
    
    csv_file = "traffic_log.csv"
    if not os.path.isfile(csv_file):
        with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['Thoi Gian', 'ID_Xe', 'Loai', 'Toc do (km/h)', 'Bien So'])

    if torch.cuda.is_available():
        AI_DEVICE = 0 
        print(" Đã nhận Card NVIDIA. Chạy chế độ HIỆU SUẤT CAO.")
    else:
        AI_DEVICE = 'cpu' 

    model = YOLO("yolov8small/best_small.pt", task='detect') 
    reader = easyocr.Reader(['en'], gpu=True if AI_DEVICE == 0 else False) 

    cap = cv2.VideoCapture("Datanew/ChinaRoad.mp4", cv2.CAP_MSMF) 
    success, frame = cap.read()
    if not success: return
        
    orig_h, orig_w = frame.shape[:2]
    DISPLAY_W = 1280
    DISPLAY_H = int(orig_h * (DISPLAY_W / orig_w))
    frame = cv2.resize(frame, (DISPLAY_W, DISPLAY_H))
    clone = frame.copy()

    # KHỞI TẠO BIẾN SETUP
    rl_light_straight_roi = (0,0,0,0)
    rl_light_left_roi = (0,0,0,0)
    rl_monitor_polygon = None
    speed_polygon = None
    
    ww_polygon = None
    ww_vector = None # Vector chân lý

    # A. SETUP ĐÈN ĐỎ
    if RUN_REDLIGHT:
        print("\n[SETUP] Đang cấu hình Đèn đỏ...")
        print("-" * 50)
        print(">>> BƯỚC 1: Kéo thả chuột khoanh vùng ĐÈN ĐI THẲNG.")
        print("-" * 50)
        rl_light_straight_roi = cv2.selectROI("Khoanh vung Den Di Thang (Keo tha -> ENTER)", frame, False, False)
        cv2.destroyWindow("Khoanh vung Den Di Thang (Keo tha -> ENTER)")
        
        if HAS_LEFT_TURN_LIGHT:
            print("-" * 50)
            print(">>> BƯỚC 2: Kéo thả chuột khoanh vùng ĐÈN RẼ TRÁI.")
            print("-" * 50)
            rl_light_left_roi = cv2.selectROI("Khoanh vung Den Re Trai (Keo tha -> ENTER)", frame, False, False)
            cv2.destroyWindow("Khoanh vung Den Re Trai (Keo tha -> ENTER)")
            
        print("-" * 50)
        print(">>> BƯỚC 3: Vẽ KHUNG GIÁM SÁT (Cạnh đáy TỰ ĐỘNG thành vạch vàng)")
        print("-" * 50)
        roi_points.clear()
        cv2.namedWindow("Setup")
        cv2.setMouseCallback("Setup", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "Click 4 diem tao KHUNG GIAM SAT. Xong bam ENTER", (10, 30), 0, 0.7, (0,0,255), 2)
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0,0,255), -1)
            if len(roi_points) > 1:
                pts_arr = np.array(roi_points, np.int32)
                cv2.polylines(temp, [pts_arr], True, (0, 0, 255), 2)
                if len(roi_points) == 4:
                    ordered = order_points(np.array(roi_points, dtype="float32"))
                    cv2.line(temp, tuple(ordered[2].astype(int)), tuple(ordered[3].astype(int)), (0, 255, 255), 3) 
            cv2.imshow("Setup", temp)
            if cv2.waitKey(1) == 13 and len(roi_points) == 4: 
                rl_monitor_polygon = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
        cv2.destroyWindow("Setup")

    # B. SETUP TỐC ĐỘ
    elif RUN_SPEED:
        print("\n[SETUP] Đang cấu hình Tốc độ...")
        roi_points.clear()
        cv2.namedWindow("Setup")
        cv2.setMouseCallback("Setup", draw_polygon)
        while True:
            temp = frame.copy()
            cv2.putText(temp, "Click 4 diem tao HINH THANG (Vung do toc do). Roi bam ENTER", (10, 30), 0, 0.7, (255,255,255), 2)
            for pt in roi_points:
                cv2.circle(temp, tuple(pt), 5, (0,0,255), -1)
            if len(roi_points) > 1:
                pts = np.array(roi_points, np.int32)
                cv2.polylines(temp, [pts], isClosed=(len(roi_points)==4), color=(0, 255, 255), thickness=2)
            cv2.imshow("Setup", temp)
            if cv2.waitKey(1) == 13 and len(roi_points) == 4: break
        cv2.destroyWindow("Setup")

        src_pts = order_points(np.array(roi_points, dtype="float32"))
        PIXELS_PER_METER = 100 
        dst_w = int(REAL_WIDTH_M * PIXELS_PER_METER)
        dst_h = int(REAL_HEIGHT_M * PIXELS_PER_METER)
        dst_pts = np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype="float32")
        M_matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)
        speed_polygon = np.array(src_pts, np.int32)

    # C. SETUP NGƯỢC CHIỀU (Chạy tiếp nối nếu user gõ 'y')
    if RUN_WRONGWAY:
        print("\n[SETUP] Đang cấu hình Ngược Chiều...")
        print("-" * 50)
        print(">>> BƯỚC 1: Vẽ VÙNG CẤM NGƯỢC CHIỀU (Polygon 4 điểm)")
        print("-" * 50)
        roi_points.clear()
        cv2.namedWindow("Setup")
        cv2.setMouseCallback("Setup", draw_polygon)
        while True:
            temp = frame.copy()
            # Vẽ lưu vết vùng Tốc Độ hoặc Đèn đỏ cho dễ canh
            if RUN_SPEED: cv2.polylines(temp, [speed_polygon], True, (255, 255, 0), 2)
            if RUN_REDLIGHT: cv2.polylines(temp, [rl_monitor_polygon], True, (0, 0, 255), 2)
            
            cv2.putText(temp, "VUNG CAM NGUOC CHIEU (Click 4 diem -> ENTER)", (10, 30), 0, 0.7, (255,0,255), 2)
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0,0,255), -1)
            if len(roi_points) > 1:
                cv2.polylines(temp, [np.array(roi_points, np.int32)], True, (255, 0, 255), 2)
            cv2.imshow("Setup", temp)
            if cv2.waitKey(1) == 13 and len(roi_points) == 4: 
                ww_polygon = np.array(order_points(np.array(roi_points, dtype="float32")), np.int32)
                break
        
        print("-" * 50)
        print(">>> BƯỚC 2: Vẽ MŨI TÊN CHỈ HƯỚNG ĐÚNG (Click Điểm Đầu -> Điểm Cuối)")
        print("-" * 50)
        roi_points.clear()
        cv2.setMouseCallback("Setup", draw_line)
        while True:
            temp = frame.copy()
            cv2.polylines(temp, [ww_polygon], True, (255, 0, 255), 2) # Vẽ lại vùng cấm
            cv2.putText(temp, "MUI TEN HUONG DUNG (Click 2 diem -> ENTER)", (10, 30), 0, 0.7, (0,255,0), 2)
            for pt in roi_points: cv2.circle(temp, tuple(pt), 5, (0,255,0), -1)
            if len(roi_points) == 2:
                # Vẽ mũi tên cho ngầu
                cv2.arrowedLine(temp, tuple(roi_points[0]), tuple(roi_points[1]), (0, 255, 0), 3, tipLength=0.1)
            cv2.imshow("Setup", temp)
            if cv2.waitKey(1) == 13 and len(roi_points) == 2: 
                ref_x1, ref_y1 = roi_points[0]
                ref_x2, ref_y2 = roi_points[1]
                ww_vector = (ref_x2 - ref_x1, ref_y2 - ref_y1) # Vector chuẩn
                break
        cv2.destroyWindow("Setup")

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    if video_fps == 0 or np.isnan(video_fps): video_fps = 30.0
    frame_count = 0
    system_prev_time = time.time()

    print("\n==> HỆ THỐNG GIÁM SÁT ĐANG CHẠY...")

    while cap.isOpened():
        success, frame = cap.read()
        if not success: break

        frame_count += 1
        video_current_time = frame_count / video_fps
        frame = cv2.resize(frame, (DISPLAY_W, DISPLAY_H))
        clean_frame = frame.copy()
        
        sys_curr_time = time.time()
        fps_display = 1 / (sys_curr_time - system_prev_time) if system_prev_time > 0 else 0
        system_prev_time = sys_curr_time

        cur_light_s = get_traffic_light_state(clean_frame, rl_light_straight_roi) if RUN_REDLIGHT else 'UNKNOWN'
        cur_light_l = get_traffic_light_state(clean_frame, rl_light_left_roi) if (RUN_REDLIGHT and HAS_LEFT_TURN_LIGHT) else 'UNKNOWN'

        # Vẽ UI tĩnh
        if RUN_SPEED: 
            cv2.polylines(frame, [speed_polygon], isClosed=True, color=(255, 255, 0), thickness=2)
        if RUN_REDLIGHT:
            cv2.polylines(frame, [rl_monitor_polygon], True, (0, 0, 255), 2)
            cv2.line(frame, tuple(rl_monitor_polygon[2]), tuple(rl_monitor_polygon[3]), (0, 255, 255), 3) 
            cv2.putText(frame, f"Den Thang: {cur_light_s}", (10, 120), 0, 0.8, (0,255,255) if cur_light_s=='YELLOW' else ((0,0,255) if cur_light_s=='RED' else (0,255,0)), 2)
        if RUN_WRONGWAY:
            cv2.polylines(frame, [ww_polygon], True, (255, 0, 255), 2) # Viền tím cho Vùng ngược chiều

        results = model.track(clean_frame, persist=True, tracker="bytetrack.yaml", conf=0.3, imgsz=1024, device=AI_DEVICE, verbose=False)
        boxes = results[0].boxes

        current_frame_ids = set() 

        if boxes is not None and boxes.id is not None: 
            for box in boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cls_id = int(box.cls[0])
                class_name = model.names[cls_id]
                track_id = int(box.id[0]) if box.id is not None else -1
                
                if track_id != -1: current_frame_ids.add(track_id)
                center_x = int((x1 + x2) / 2)
                bottom_y = int(y2)

                if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                    if track_id not in vehicle_tracking_data:
                        vehicle_tracking_data[track_id] = {
                            'history': [], 'speed': 0, 'recorded': False, 'v_class': class_name,
                            'plate_text': "Không rõ", 'tele_sent': False, 'pending_tele': False,
                            'max_area': 0, 'best_frame': None, 'best_crop': None, 'violation_speed': 0,
                            'rl_state': 'WAITING', 'entry_x': 0, 'entry_light_s': 'UNKNOWN', 'entry_light_l': 'UNKNOWN', 'is_redlight_err': False, 'alert_msg': "",
                            'ww_state': 'WAITING', 'ww_start_pt': (0,0), 'is_wrongway_err': False # Config ngược chiều
                        }
                    
                    data = vehicle_tracking_data[track_id]
                    box_color = COLOR_MAP.get(class_name, (255, 255, 255))
                    
                    current_area = (x2 - x1) * (y2 - y1)
                    if current_area > data['max_area']:
                        data['max_area'] = current_area
                        data['best_crop'] = clean_frame[max(0, y1):min(clean_frame.shape[0], y2), max(0, x1):min(clean_frame.shape[1], x2)].copy()

                    # ----------------------------------------------------
                    # A. MODULE NGƯỢC CHIỀU (VECTOR DOT PRODUCT)
                    # ----------------------------------------------------
                    if RUN_WRONGWAY and not data['tele_sent'] and not data['is_wrongway_err']:
                        in_ww_zone = cv2.pointPolygonTest(ww_polygon, (center_x, bottom_y), False) >= 0
                        if in_ww_zone:
                            if data['ww_state'] == 'WAITING':
                                data['ww_state'] = 'TRACKING'
                                data['ww_start_pt'] = (center_x, bottom_y)
                            elif data['ww_state'] == 'TRACKING':
                                sx, sy = data['ww_start_pt']
                                dist_travelled = np.sqrt((center_x - sx)**2 + (bottom_y - sy)**2)
                                
                                # Đợi xe đi được khoảng > 60 pixel để có Vector chuẩn xác (lọc nhiễu)
                                if dist_travelled > 60:
                                    v_car = (center_x - sx, bottom_y - sy)
                                    
                                    # TÍNH TÍCH VÔ HƯỚNG ĐỂ TÌM GÓC ĐI LỆCH
                                    dot_product = v_car[0]*ww_vector[0] + v_car[1]*ww_vector[1]
                                    mag_car = np.sqrt(v_car[0]**2 + v_car[1]**2)
                                    mag_ref = np.sqrt(ww_vector[0]**2 + ww_vector[1]**2)
                                    
                                    if mag_car > 0 and mag_ref > 0:
                                        cos_theta = dot_product / (mag_car * mag_ref)
                                        # cos_theta < -0.5 tương đương góc lệch > 120 độ so với chiều đúng
                                        if cos_theta < -0.5:
                                            data['pending_tele'] = True
                                            data['is_wrongway_err'] = True
                                            data['best_frame'] = frame.copy()
                                            data['ww_state'] = 'DONE' # Phạt rồi thì khóa lại
                                        else:
                                            # Đi đúng chiều -> Cập nhật lại mốc để tracking tiếp
                                            data['ww_start_pt'] = (center_x, bottom_y)
                        else:
                            if data['ww_state'] == 'TRACKING':
                                data['ww_state'] = 'DONE' # Thoát khỏi vùng mà không bị gì là An Toàn

                    # ----------------------------------------------------
                    # B. MODULE ĐÈN ĐỎ
                    # ----------------------------------------------------
                    if RUN_REDLIGHT and not data['tele_sent'] and not data['is_wrongway_err']:
                        in_monitor = cv2.pointPolygonTest(rl_monitor_polygon, (center_x, bottom_y), False) >= 0
                        bottom_edge_y = max(rl_monitor_polygon[2][1], rl_monitor_polygon[3][1])

                        if in_monitor and data['rl_state'] == 'WAITING':
                            data['rl_state'] = 'IN_ZONE'
                            data['entry_x'] = center_x
                            if abs(bottom_y - bottom_edge_y) < 100:
                                data['entry_light_s'] = cur_light_s
                                data['entry_light_l'] = cur_light_l
                            else:
                                data['entry_light_s'] = 'SAFE'
                                data['entry_light_l'] = 'SAFE'
                            data['best_frame'] = frame.copy() 

                        elif data['rl_state'] == 'IN_ZONE' and not in_monitor:
                            data['rl_state'] = 'DONE'
                            if bottom_y < bottom_edge_y - 50: 
                                dx = center_x - data['entry_x']
                                direction = 'STRAIGHT'
                                if dx < -VECTOR_TURN_THRESHOLD: direction = 'LEFT'
                                elif dx > VECTOR_TURN_THRESHOLD: direction = 'RIGHT'
                                
                                violation = False
                                msg = ""
                                if direction == 'STRAIGHT' and data['entry_light_s'] == 'RED':
                                    violation, msg = True, "Đi thẳng lúc Đèn Đỏ"
                                elif direction == 'LEFT' and data['entry_light_l'] == 'RED':
                                    violation, msg = True, "Rẽ trái lúc Đèn Đỏ"
                                elif direction == 'RIGHT' and data['entry_light_s'] == 'RED':
                                    if not (class_name == 'motorcycle' and ALLOW_MOTO_RIGHT_TURN_ON_RED):
                                        violation, msg = True, "Rẽ phải lúc Đèn Đỏ"
                                        
                                if violation:
                                    data['pending_tele'] = True
                                    data['is_redlight_err'] = True
                                    data['alert_msg'] = msg

                    # ----------------------------------------------------
                    # C. MODULE TỐC ĐỘ 
                    # ----------------------------------------------------
                    if RUN_SPEED and not data['tele_sent'] and not data['is_redlight_err'] and not data['is_wrongway_err']:
                        is_inside = cv2.pointPolygonTest(speed_polygon, (center_x, bottom_y), False) >= 0
                        if is_inside:
                            cv2.circle(frame, (center_x, bottom_y), 5, (0,0,255), -1) 
                            pt = np.array([[[center_x, bottom_y]]], dtype="float32")
                            bev_pt = cv2.perspectiveTransform(pt, M_matrix)[0][0]

                            data['history'].append((video_current_time, bev_pt[0], bev_pt[1]))

                            if len(data['history']) >= 3:
                                t1, x1_bev, y1_bev = data['history'][0]  
                                t2, x2_bev, y2_bev = data['history'][-1] 
                                dist_meters = np.sqrt((x2_bev - x1_bev)**2 + (y2_bev - y1_bev)**2) / PIXELS_PER_METER
                                dt = t2 - t1
                                
                                if dt > 0 and dist_meters > 3.0:
                                    current_speed = (dist_meters / dt) * 3.6
                                    data['speed'] = current_speed if data['speed'] == 0 else (data['speed']*0.7 + current_speed*0.3)
                                    
                                    if not data['recorded']:
                                        data['recorded'] = True
                                        total_vehicles_counted += 1
                                        
                                    speed_int = int(data['speed'])
                                    if speed_int > SPEED_LIMIT:
                                        data['pending_tele'] = True
                                        data['violation_speed'] = speed_int 
                                        data['best_frame'] = frame.copy()   

                    # ----------------------------------------------------
                    # HIỂN THỊ UI THEO MỨC ĐỘ VI PHẠM ƯU TIÊN
                    # ----------------------------------------------------
                    speed_int = int(data['speed'])
                    text_color = box_color
                    label = f"ID:{track_id} {class_name}"
                    if RUN_SPEED and speed_int > 0: label += f" {speed_int}km/h"
                        
                    if data['is_wrongway_err']:
                        label += " [NGUOC CHIEU]"
                        text_color = (0, 0, 255) 
                        box_color = (0, 0, 255) 
                    elif data['is_redlight_err']:
                        label += " [VUOT DEN DO]"
                        text_color = (0, 0, 255) 
                        box_color = (0, 0, 255)  
                    elif RUN_SPEED and speed_int > SPEED_LIMIT:
                        text_color = (0, 0, 255)
                    
                    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2 if not data['pending_tele'] else 4)
                    cv2.putText(frame, label, (x1, y1 - 10), 0, 0.6, text_color, 2)

                # =====================================================
                # MODULE ĐỌC BIỂN SỐ 
                # =====================================================
                elif class_name == 'license_plate':
                    box_color = COLOR_MAP['license_plate']
                    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)

                    if (x2 - x1) < 25: continue 
                        
                    crop_y1 = max(0, int(y1 - (y2-y1)*0.05))
                    crop_y2 = min(clean_frame.shape[0], int(y2 + (y2-y1)*0.05))              
                    crop_x1 = max(0, int(x1 - (x2-x1)*0.05))
                    crop_x2 = min(clean_frame.shape[1], int(x2 + (x2-x1)*0.05))              
                    
                    if crop_y2 <= crop_y1 or crop_x2 <= crop_x1: continue
                        
                    plate_crop = clean_frame[crop_y1:crop_y2, crop_x1:crop_x2]
                    plate_crop_large = cv2.resize(plate_crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    gray = cv2.cvtColor(plate_crop_large, cv2.COLOR_BGR2GRAY)
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
                    enhanced = clahe.apply(gray)
                    blur = cv2.GaussianBlur(enhanced, (3, 3), 0)
                    morph = cv2.erode(blur, np.ones((2, 2), np.uint8), iterations=1)
                    
                    ocr_result = reader.readtext(morph, allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', decoder='beamsearch', detail=1)
                    
                    if len(ocr_result) > 0:
                        valid_results = [res for res in ocr_result if res[2] > 0.3] 
                        if valid_results:
                            valid_results.sort(key=lambda x: x[0][0][1])
                            import re
                            raw_text = re.sub(r'[^A-Z0-9]', '', "".join([res[1] for res in valid_results]))
                            clean_text = correct_vietnamese_plate(raw_text, 'car')
                            
                            if 7 <= len(clean_text) <= 9:
                                num_digits = sum(c.isdigit() for c in clean_text)
                                if num_digits >= 4:
                                    if len(clean_text) == 9 or (len(clean_text)==8 and clean_text[3].isalpha()):
                                        plate_text = clean_text[:4] + "-" + clean_text[4:]
                                    else:
                                        plate_text = clean_text[:3] + "-" + clean_text[3:]
                                        
                                    cv2.putText(frame, f"BS: {plate_text}", (x1, y2 + 20), 0, 0.8, box_color, 2)

                                    v_id = -1
                                    plate_center_x = (x1 + x2) // 2
                                    plate_center_y = (y1 + y2) // 2
                                    for v_box in boxes:
                                        v_cls = int(v_box.cls[0])
                                        if model.names[v_cls] in ['car', 'motorcycle', 'truck', 'bus']:
                                            vx1, vy1, vx2, vy2 = map(int, v_box.xyxy[0])
                                            if (vx1 - 20) <= plate_center_x <= (vx2 + 20) and (vy1 - 20) <= plate_center_y <= (vy2 + 20):
                                                v_id = int(v_box.id[0]) if v_box.id is not None else -1
                                                break
                                    
                                    if v_id != -1 and v_id in vehicle_tracking_data:
                                        if v_id not in plate_buffer: plate_buffer[v_id] = []
                                        plate_buffer[v_id].append(plate_text)
                                        
                                        if plate_buffer[v_id].count(plate_text) == 2: 
                                            data = vehicle_tracking_data[v_id]
                                            
                                            if not data['tele_sent']:
                                                data['plate_text'] = plate_text
                                                data['best_crop'] = blur 
                                                
                                                time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                                                with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                                    writer = csv.writer(f)
                                                    writer.writerow([time_str, v_id, VI_CLASS_MAP.get(data['v_class'],''), csv_speed, plate_text])

                                                if data['pending_tele']:
                                                    # Phân luồng Telegram theo loại lỗi
                                                    if data['is_wrongway_err']: v_type = 'WRONGWAY'
                                                    elif data['is_redlight_err']: v_type = 'REDLIGHT'
                                                    else: v_type = 'SPEED'
                                                    trigger_telegram(data, v_id, save_dir, violation_type=v_type)

        # XỬ LÝ KHI XE MẤT DẤU
        for v_id, data in vehicle_tracking_data.items():
            if data.get('pending_tele') and v_id not in current_frame_ids:
                if data['is_wrongway_err']: v_type = 'WRONGWAY'
                elif data['is_redlight_err']: v_type = 'REDLIGHT'
                else: v_type = 'SPEED'
                trigger_telegram(data, v_id, save_dir, violation_type=v_type)

        cv2.rectangle(frame, (10, 10), (300, 100), (0, 0, 0), -1)
        cv2.putText(frame, f"FPS: {int(fps_display)}", (20, 45), 0, 1, (0, 255, 255), 2)
        if RUN_SPEED: 
            cv2.putText(frame, f"COUNT: {total_vehicles_counted}", (20, 85), 0, 1, (0, 255, 0), 2)

        cv2.imshow("He Thong Giam Sat Giao Thong", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'): break
        elif key == ord('p') or key == 32:
            cv2.waitKey(-1)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()