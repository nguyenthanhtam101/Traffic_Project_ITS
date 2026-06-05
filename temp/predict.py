import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

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
# CẤU HÌNH HỆ THỐNG VÀ TELEGRAM
# =================================================================
REAL_WIDTH_M = 14.0   
REAL_HEIGHT_M = 6.0 
SPEED_LIMIT = 55.0   

LOCATION_NAME = "Xa lộ Hà Nội, TP.HCM"

# ĐIỀN CHUẨN TOKEN VÀ ID CỦA BẠN VÀO ĐÂY
TELEGRAM_BOT_TOKEN = "8724545022:AAEgeJZ8nE6zj5utIDb85C3dpNgzGcwsn2g"
TELEGRAM_CHAT_ID = "8066570830"

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

roi_points = [] 
M_matrix = None 
plate_buffer = {} 
vehicle_tracking_data = {} 
total_vehicles_counted = 0

def send_telegram_alert(bot_token, chat_id, text, img_path_full, img_path_crop):
    """Hàm gửi Telegram có báo lỗi ra màn hình để dễ theo dõi"""
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

def trigger_telegram(data, track_id, save_dir):
    """Hàm trung tâm để format và gọi lệnh gửi Telegram, kết hợp xuất ảnh lưu nháp"""
    if data.get('tele_sent'): return
    data['tele_sent'] = True
    data['pending_tele'] = False 
    
    v_type_vn = VI_CLASS_MAP.get(data.get('v_class', 'car'), 'Phương tiện')
    plate_str = data.get('plate_text', 'Không rõ')
    
    # Lấy tốc độ vi phạm cao nhất để báo cáo (Số nguyên)
    speed_report = data.get('violation_speed', int(data.get('speed', 0)))
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Format tin nhắn KHÔNG DẤU PHẨY
    alert_msg = f"🚨 PHÁT HIỆN VI PHẠM TỐC ĐỘ!\n📍 Địa điểm: {LOCATION_NAME}\n🚘 Loại xe: {v_type_vn}\n🆔 Biển số: {plate_str}\n⚡ Tốc độ: {speed_report} km/h (QĐ: {int(SPEED_LIMIT)})\n⏱ Thời gian: {time_str}"
    
    img_name_full = f"full_{track_id}_{plate_str}.jpg"
    img_name_crop = f"crop_{track_id}_{plate_str}.jpg"
    img_path_full = os.path.join(save_dir, img_name_full)
    img_path_crop = os.path.join(save_dir, img_name_crop)
    
    if data['best_frame'] is not None: cv2.imwrite(img_path_full, data['best_frame'])
    if data['best_crop'] is not None: cv2.imwrite(img_path_crop, data['best_crop'])
    
    threading.Thread(target=send_telegram_alert, args=(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, alert_msg, img_path_full, img_path_crop)).start()

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def draw_polygon(event, x, y, flags, param):
    global roi_points
    if event == cv2.EVENT_LBUTTONDOWN and len(roi_points) < 4:
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

def main():
    global roi_points, M_matrix, total_vehicles_counted, plate_buffer, vehicle_tracking_data
    
    save_dir = "saved_plates"
    os.makedirs(save_dir, exist_ok=True)
    
    csv_file = "traffic_log.csv"
    if not os.path.isfile(csv_file):
        with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['Thoi Gian', 'ID_Xe', 'Loai', 'Toc do (km/h)', 'Bien So'])

    if torch.cuda.is_available():
        AI_DEVICE = 0 
        print("✅ Đã nhận Card NVIDIA. Chạy chế độ HIỆU SUẤT CAO.")
    else:
        AI_DEVICE = 'cpu' 
        print("⚠️ Cảnh báo: Đang dùng CPU.")

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

    cv2.namedWindow("Setup")
    cv2.setMouseCallback("Setup", draw_polygon)
    
    while True:
        temp = clone.copy()
        cv2.putText(temp, "Click 4 diem tao thanh HINH THANG (Vung do toc do). Roi bam ENTER", (10, 30), 0, 0.7, (255,255,255), 2)
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
    roi_polygon = np.array(src_pts, np.int32)

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    if video_fps == 0 or np.isnan(video_fps): video_fps = 30.0
    frame_count = 0
    system_prev_time = time.time()

    print("==> HỆ THỐNG GIÁM SÁT ĐANG CHẠY...")

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

        cv2.polylines(frame, [roi_polygon], isClosed=True, color=(0, 255, 255), thickness=2)

        results = model.track(frame, persist=True, tracker="bytetrack.yaml", conf=0.3, imgsz=1024, device=AI_DEVICE, verbose=False)
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

                # =====================================================
                # 1. THEO DÕI TỐC ĐỘ VÀ VẼ UI LÊN FRAME
                # =====================================================
                if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                    if track_id not in vehicle_tracking_data:
                        vehicle_tracking_data[track_id] = {
                            'history': [], 'speed': 0, 'recorded': False, 'v_class': class_name,
                            'plate_text': "Không rõ", 'tele_sent': False, 'pending_tele': False,
                            'max_area': 0, 'best_frame': None, 'best_crop': None,
                            'violation_speed': 0 # <--- Biến mới khóa chết tốc độ vi phạm
                        }
                    
                    data = vehicle_tracking_data[track_id]
                    box_color = COLOR_MAP.get(class_name, (255, 255, 255))
                    is_inside = cv2.pointPolygonTest(roi_polygon, (center_x, bottom_y), False) >= 0

                    if is_inside:
                        cv2.circle(frame, (center_x, bottom_y), 5, (0,0,255), -1) 
                        
                        pt = np.array([[[center_x, bottom_y]]], dtype="float32")
                        bev_pt = cv2.perspectiveTransform(pt, M_matrix)[0][0]

                        data['history'].append((video_current_time, bev_pt[0], bev_pt[1]))

                        if len(data['history']) >= 3:
                            t1, x1_bev, y1_bev = data['history'][0]  
                            t2, x2_bev, y2_bev = data['history'][-1] 
                            
                            dist_pixels = np.sqrt((x2_bev - x1_bev)**2 + (y2_bev - y1_bev)**2)
                            dist_meters = dist_pixels / PIXELS_PER_METER
                            dt = t2 - t1
                            
                            if dt > 0 and dist_meters > 3.0:
                                current_speed = (dist_meters / dt) * 3.6
                                
                                if data['speed'] == 0:
                                    data['speed'] = current_speed
                                else:
                                    data['speed'] = (data['speed'] * 0.7) + (current_speed * 0.3)
                                
                                if not data['recorded']:
                                    data['recorded'] = True
                                    total_vehicles_counted += 1

                    # ÉP KIỂU VỀ SỐ NGUYÊN NGAY LẬP TỨC
                    speed_int = int(data['speed'])

                    # BƯỚC QUAN TRỌNG: VẼ UI LÊN FRAME TRƯỚC KHI CHỤP ẢNH
                    text_color = (0, 0, 255) if speed_int > SPEED_LIMIT else box_color
                    
                    # 1. Khung xe LUÔN LUÔN giữ màu mặc định theo loại xe (box_color)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2 if speed_int <= SPEED_LIMIT else 4)
                    
                    # 2. Dòng chữ thông tin mới đổi màu đỏ nếu vi phạm tốc độ
                    label = f"ID:{track_id} {class_name} {speed_int}km/h" if speed_int > 0 else f"ID:{track_id} {class_name}"
                    cv2.putText(frame, label, (x1, y1 - 10), 0, 0.6, text_color, 2)

                    # LƯU ẢNH CHỈ KHI NÀO XE ĐANG VI PHẠM (ĐẢM BẢO ẢNH CÓ CHỮ ĐỎ VÀ TỐC ĐỘ CAO NHẤT)
                    if speed_int > SPEED_LIMIT:
                        data['pending_tele'] = True
                        
                        current_area = (x2 - x1) * (y2 - y1)
                        if current_area > data['max_area']:
                            data['max_area'] = current_area
                            data['violation_speed'] = speed_int # Khóa cứng tốc độ đang hiện trên màn hình
                            data['best_frame'] = frame.copy()   # Lấy bức ảnh đã vẽ sẵn UI cảnh báo
                            data['best_crop'] = clean_frame[max(0, y1):min(clean_frame.shape[0], y2), max(0, x1):min(clean_frame.shape[1], x2)].copy()

                # =====================================================
                # 2. XỬ LÝ BIỂN SỐ 
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
                                                data['best_crop'] = blur # Ghi đè biển số sắc nét
                                                
                                                time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                                csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                                                with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                                    writer = csv.writer(f)
                                                    writer.writerow([time_str, v_id, VI_CLASS_MAP.get(data['v_class'],''), csv_speed, plate_text])
                                                    
                                                print(f"-> ĐÃ CHỐT BIỂN SỐ: {plate_text} (Thuộc xe ID {v_id})")

                                                if data['pending_tele']:
                                                    # Gửi ngay (Dùng lại ảnh toàn cảnh báo vi phạm màu đỏ đã lưu)
                                                    trigger_telegram(data, v_id, save_dir)

        # =====================================================
        # 3. QUÉT NHỮNG XE VI PHẠM ĐÃ BIẾN MẤT
        # =====================================================
        for v_id, data in vehicle_tracking_data.items():
            if data.get('pending_tele') and v_id not in current_frame_ids:
                # Xe chạy khỏi màn hình -> Gửi cái ảnh xịn nhất đã lưu lúc nó đang chạy quá tốc độ
                trigger_telegram(data, v_id, save_dir)

        cv2.rectangle(frame, (10, 10), (300, 100), (0, 0, 0), -1)
        cv2.putText(frame, f"FPS: {int(fps_display)}", (20, 45), 0, 1, (0, 255, 255), 2)
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