import cv2
import numpy as np
import os
import csv
import time
import queue
import threading
import re
from datetime import datetime
from config import *
from core.ai_engine import load_models, correct_vietnamese_plate
from core.telegram_bot import trigger_telegram, tele_queue
import requests
import urllib.request

# ĐƯA DATABASE VÀO LÕI AI
from core.database import save_event_to_db

roi_points = []

def run_opencctv_processing(raw_url, frame_queue, kpi_queue, stop_event):
    """Lõi AI tốc độ cao: Cào ảnh từ OpenCCTV.org (0.5s / Khung hình)"""
    try:
        model, _, AI_DEVICE = load_models()
        
        loading_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.putText(loading_frame, "Dang ket noi he thong OpenCCTV...", (300, 360), 0, 1.2, (0, 255, 255), 2)
        try: frame_queue.put_nowait(loading_frame)
        except queue.Full: pass

        base_url = raw_url.split('?')[0] if '?' in raw_url else raw_url
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0',
            'Referer': 'https://opencctv.org/',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        }

        while not stop_event.is_set():
            try:
                current_time_ms = int(time.time() * 1000)
                fetch_url = f"{base_url}?t={current_time_ms}"
                res = requests.get(fetch_url, headers=headers, timeout=5)
                
                if res.status_code == 200:
                    arr = np.asarray(bytearray(res.content), dtype=np.uint8)
                    frame = cv2.imdecode(arr, -1)
                    
                    if frame is None: raise Exception("Không thể giải mã ảnh")
                    frame = cv2.resize(frame, (1280, 720))
                    
                    results = model(frame, conf=0.3, imgsz=1024, device=AI_DEVICE, verbose=False)
                    boxes = results[0].boxes
                    
                    current_density = 0
                    if boxes is not None:
                        for box in boxes:
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            class_name = model.names[int(box.cls[0])]
                            if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                                current_density += 1
                                box_color = COLOR_MAP.get(class_name, (0, 255, 0))
                                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                                cv2.putText(frame, class_name, (x1, y1 - 10), 0, 0.6, box_color, 2)
                    
                    time_now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                    cv2.putText(frame, f"OPENCCTV LIVE - {time_now}", (20, 50), 0, 1.0, (0, 255, 255), 3)

                    try: frame_queue.put_nowait(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    except queue.Full: pass
                    
                    try: kpi_queue.put_nowait(("KPI", current_density, 0)) 
                    except queue.Full: pass
                    
                else:
                    kpi_queue.put(("ERROR", f"Server báo lỗi (Mã: {res.status_code})."))
                    break

            except Exception as e:
                kpi_queue.put(("ERROR", f"Đứt kết nối: {str(e)}"))
                break

            for _ in range(5):
                if stop_event.is_set(): break
                time.sleep(0.1)

        kpi_queue.put(("DONE", 0, 0))
    except Exception as e:
        kpi_queue.put(("ERROR", f"Lỗi lõi API: {str(e)}"))
        
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

def run_opencv_setup(frame, run_speed, run_redlight, run_wrongway, run_heatmap, DISPLAY_H, DISPLAY_W):
    global roi_points
    setup_result = {'ok': True, 'cancel_reason': '', 'M_matrix': None, 'speed_polygon': None, 'rl_light_straight_roi': (0, 0, 0, 0), 'rl_monitor_polygon': None, 'ww_polygon': None, 'ww_vector': None, 'heatmap_polygon': None}
    
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
                cv2.destroyAllWindows(); setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Vượt Đèn Đỏ!"
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
                cv2.destroyAllWindows(); setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Đo Tốc Độ!"
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
            if len(roi_points) == 2: cv2.arrowedLine(temp, tuple(roi_points[0]), tuple(roi_points[1]), (0, 255, 0), 3, tipLength=0.1)
            cv2.imshow("SETUP", temp)
            key = cv2.waitKey(1) & 0xFF
            if key == 13 and len(roi_points) == 2:
                setup_result['ww_vector'] = (roi_points[1][0] - roi_points[0][0], roi_points[1][1] - roi_points[0][1])
                break
            elif key in [ord('c'), ord('C')]: roi_points.clear()
            elif key == 27:
                cv2.destroyAllWindows(); setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Vector!"
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
                cv2.destroyAllWindows(); setup_result['ok'], setup_result['cancel_reason'] = False, "Đã hủy thiết lập Heatmap!"
                return setup_result
        cv2.destroyWindow("SETUP")
    return setup_result

def run_video_processing(tfile_path, setup_result, run_speed, run_redlight, run_wrongway, run_heatmap,
                         speed_limit_live, allow_moto_right, allow_car_right, jam_threshold, live_config,
                         DISPLAY_W, DISPLAY_H, frame_queue, kpi_queue, stop_event):
    try:
        run_just_detect = not (run_speed or run_redlight or run_wrongway)
        model, reader, AI_DEVICE = load_models()
        save_dir = "saved_plates"
        os.makedirs(save_dir, exist_ok=True)
        
        # 👉 TẠO THƯ MỤC LƯU ẢNH BẰNG CHỨNG VI PHẠM (EVIDENCE)
        os.makedirs("evidence", exist_ok=True)
        
        M_matrix, speed_polygon = setup_result['M_matrix'], setup_result['speed_polygon']
        rl_light_straight_roi, rl_monitor_polygon = setup_result['rl_light_straight_roi'], setup_result['rl_monitor_polygon']
        ww_polygon, ww_vector = setup_result['ww_polygon'], setup_result['ww_vector']
        heatmap_polygon = setup_result['heatmap_polygon']

        plate_buffer, vehicle_tracking_data = {}, {}
        total_vehicles_counted, total_violations = 0, 0
        heatmap_matrix = np.zeros((DISPLAY_H, DISPLAY_W), dtype=np.float32)
        jam_start_time, jam_alert_sent = None, False

        cap = cv2.VideoCapture(tfile_path)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        if not video_fps or np.isnan(video_fps): video_fps = 30.0
        
        frame_count = 0
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
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    class_name = model.names[int(box.cls[0])]
                    track_id = int(box.id[0]) if box.id is not None else -1
                    if track_id != -1: current_frame_ids.add(track_id)
                    center_x, bottom_y = int((x1 + x2) / 2), int(y2)

                    if class_name in ['car', 'motorcycle', 'truck', 'bus']:
                        if track_id not in vehicle_tracking_data:
                            vehicle_tracking_data[track_id] = {
                                'history': [], 'speed': 0, 'v_class': class_name,
                                'plate_text': "Không rõ", 'tele_sent': False, 'pending_tele': False,
                                'max_area': 0, 'best_frame': None, 'best_crop': None, 'violation_speed': 0,
                                'rl_state': 'WAITING', 'entry_x': 0, 'entry_light_s': 'UNKNOWN', 'is_redlight_err': False, 'alert_msg': "",
                                'ww_state': 'WAITING', 'ww_start_pt': (0, 0), 'is_wrongway_err': False,
                                'pending_tele_time': None, 'needs_best_frame': False
                            }
                            total_vehicles_counted += 1 

                        data = vehicle_tracking_data[track_id]
                        
                        if live_config.get('show_heatmap', False):
                            if heatmap_polygon is not None and cv2.pointPolygonTest(heatmap_polygon, (center_x, bottom_y), False) >= 0: jam_count += 1
                            elif heatmap_polygon is None: jam_count += 1 
                            veh_width = x2 - x1
                            heat_thickness = max(10, int(veh_width * 0.8))
                            if 'prev_center' in data:
                                px, py = data['prev_center']
                                cv2.line(temp_heat, (px, py), (center_x, bottom_y), 5.0, thickness=heat_thickness)
                            else: cv2.circle(temp_heat, (center_x, bottom_y), heat_thickness // 2, 5.0, -1)
                            data['prev_center'] = (center_x, bottom_y)

                        if (x2 - x1) * (y2 - y1) > data['max_area']:
                            data['max_area'] = (x2 - x1) * (y2 - y1)
                            # 👉 ĐÂY LÀ ẢNH CHIẾC XE SẼ ĐƯỢC LƯU LẠI LÀM BẰNG CHỨNG
                            data['best_crop'] = clean_frame[max(0, y1):min(DISPLAY_H, y2), max(0, x1):min(DISPLAY_W, x2)].copy()

                        if run_wrongway and not data['tele_sent'] and not data['is_wrongway_err'] and ww_polygon is not None:
                            if cv2.pointPolygonTest(ww_polygon, (center_x, bottom_y), False) >= 0:
                                if data['ww_state'] == 'WAITING': data['ww_state'], data['ww_start_pt'] = 'TRACKING', (center_x, bottom_y)
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
                                        elif direction == 'RIGHT' and not ((class_name == 'motorcycle' and allow_moto_right) or (class_name == 'car' and allow_car_right)): v, msg = True, "Rẽ phải lúc Đèn Đỏ"
                                    if v:
                                        data['pending_tele'], data['is_redlight_err'], data['needs_best_frame'] = True, True, True
                                        data['pending_tele_time'], data['alert_msg'] = video_current_time, msg
                                        total_violations += 1

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
                                        
                                        if int(data['speed']) > speed_limit_live and not data['pending_tele']:
                                            data['pending_tele'], data['needs_best_frame'] = True, True
                                            data['pending_tele_time'], data['violation_speed'] = video_current_time, int(data['speed'])
                                            total_violations += 1

            if live_config.get('show_heatmap', False):
                if frame_count % int(video_fps) == 0:
                    with open(HEATMAP_CSV_PATH, mode='a', newline='', encoding='utf-8') as f:
                        csv.writer(f).writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), jam_count])

                heatmap_matrix += temp_heat
                heatmap_matrix = np.clip(heatmap_matrix, 0, 255) * 0.985 
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
                else: cv2.putText(frame, msg_density, (10, 160), 0, 0.7, (0, 165, 255), 2)
                
                if jam_count >= jam_threshold:
                    if jam_start_time is None: jam_start_time = video_current_time
                    else:
                        jam_timeout = 30.0 if cur_light_s == 'RED' else 5.0 
                        if video_current_time - jam_start_time > jam_timeout and not jam_alert_sent:
                            jam_data = {'tele_sent': False, 'jam_count': jam_count, 'best_frame': frame.copy(), 'best_crop': None}
                            trigger_telegram(jam_data, "JAM", save_dir, speed_limit_live, violation_type='TRAFFIC_JAM')
                            jam_alert_sent = True
                else: jam_start_time, jam_alert_sent = None, False

            if run_speed and speed_polygon is not None: cv2.polylines(frame, [speed_polygon], True, (255, 255, 0), 2)
            if run_redlight and rl_monitor_polygon is not None:
                cv2.polylines(frame, [rl_monitor_polygon], True, (0, 0, 255), 2)
                cv2.line(frame, tuple(rl_monitor_polygon[2]), tuple(rl_monitor_polygon[3]), (0, 255, 255), 3)
                cv2.putText(frame, f"Den Thang: {cur_light_s}", (10, 120), 0, 0.8, (0, 255, 255) if cur_light_s == 'YELLOW' else ((0, 0, 255) if cur_light_s == 'RED' else (0, 255, 0)), 2)
            if run_wrongway and ww_polygon is not None: cv2.polylines(frame, [ww_polygon], True, (255, 0, 255), 2)

            if boxes is not None and boxes.id is not None:
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
                                        px_c, py_c = (x1 + x2) // 2, (y1 + y2) // 2
                                        for v_box in boxes:
                                            if model.names[int(v_box.cls[0])] in ['car', 'motorcycle', 'truck', 'bus']:
                                                vx1, vy1, vx2, vy2 = map(int, v_box.xyxy[0])
                                                if (vx1 - 20) <= px_c <= (vx2 + 20) and (vy1 - 20) <= py_c <= (vy2 + 20):
                                                    v_id = int(v_box.id[0]) if v_box.id is not None else -1
                                                    if v_id in vehicle_tracking_data:
                                                        if v_id not in plate_buffer: plate_buffer[v_id] = []
                                                        plate_buffer[v_id].append(plate_txt)
                                                        if plate_buffer[v_id].count(plate_txt) >= 2:
                                                            data = vehicle_tracking_data[v_id]
                                                            if not data['tele_sent'] and data['pending_tele']:
                                                                if data['is_wrongway_err']: v_type_csv, v_type_tele = 'Ngược Chiều', 'WRONGWAY'
                                                                elif data['is_redlight_err']: v_type_csv, v_type_tele = 'Vượt Đèn Đỏ', 'REDLIGHT'
                                                                elif run_speed and int(data['speed']) > speed_limit_live: v_type_csv, v_type_tele = 'Quá Tốc Độ', 'SPEED'
                                                                else: v_type_csv, v_type_tele = 'Bình Thường', 'NONE'

                                                                # =========================================================
                                                                # ĐIỂM XUẤT DATA 1: GHI NHẬN VI PHẠM (ĐÃ CHỐT BIỂN SỐ)
                                                                # =========================================================
                                                                data['plate_text'] = plate_txt
                                                                csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                                                                
                                                                with open(CSV_FILE_PATH, mode='a', newline='', encoding='utf-8') as f:
                                                                    csv.writer(f).writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv])
                                                                
                                                                try: save_event_to_db(v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv, "Hệ thống Camera AI")
                                                                except: pass

                                                                # 👉 LƯU ẢNH BẰNG CHỨNG XUỐNG Ổ CỨNG
                                                                if v_type_tele != 'NONE':
                                                                    if data.get('best_crop') is not None:
                                                                        cv2.imwrite(f"evidence/violation_{v_id}.jpg", data['best_crop'])
                                                                    trigger_telegram(data, v_id, save_dir, speed_limit_live, violation_type=v_type_tele)
                                                                    
                                                                data['tele_sent'] = True
                                                                break
                                                    
            for v_id, data in list(vehicle_tracking_data.items()):
                if data.get('pending_tele') and not data.get('tele_sent') and data.get('pending_tele_time') is not None:
                    if video_current_time - data['pending_tele_time'] > PLATE_CONFIRM_TIMEOUT or v_id not in current_frame_ids:
                        if data['is_wrongway_err']: v_type_csv, v_type_tele = 'Ngược Chiều', 'WRONGWAY'
                        elif data['is_redlight_err']: v_type_csv, v_type_tele = 'Vượt Đèn Đỏ', 'REDLIGHT'
                        else: v_type_csv, v_type_tele = 'Quá Tốc Độ', 'SPEED'
                        
                        plate_txt = data.get('plate_text', 'Không rõ')
                        csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                        time_str_csv = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        # =========================================================
                        # ĐIỂM XUẤT DATA 2: TIMEOUT (CHƯA RÕ BIỂN MÀ XE ĐÃ ĐI MẤT)
                        # =========================================================
                        with open(CSV_FILE_PATH, mode='a', newline='', encoding='utf-8') as f:
                            csv.writer(f).writerow([time_str_csv, v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv])
                            
                        try: save_event_to_db(v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv, "Hệ thống Camera AI")
                        except: pass

                        # 👉 LƯU ẢNH BẰNG CHỨNG XUỐNG Ổ CỨNG
                        if v_type_tele != 'NONE':
                            if data.get('best_crop') is not None:
                                cv2.imwrite(f"evidence/violation_{v_id}.jpg", data['best_crop'])
                            trigger_telegram(data, v_id, save_dir, speed_limit_live, violation_type=v_type_tele)
                            
                        data['tele_sent'] = True 

                elif not data.get('tele_sent') and v_id not in current_frame_ids:
                    plate_txt = data.get('plate_text', 'Không rõ')
                    csv_speed = int(data.get('speed', 0))
                    time_str_csv = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    v_type_csv = 'Bình Thường' if not run_just_detect else 'Lưu thông Tự do'
                    
                    # =========================================================
                    # ĐIỂM XUẤT DATA 3: XE BÌNH THƯỜNG RỜI KHỎI MÀN HÌNH
                    # =========================================================
                    with open(CSV_FILE_PATH, mode='a', newline='', encoding='utf-8') as f:
                        csv.writer(f).writerow([time_str_csv, v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv])
                        
                    try: save_event_to_db(v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv, "Hệ thống Camera AI")
                    except: pass

                    data['tele_sent'] = True

            if frame_count % 3 == 0:
                try: frame_queue.put_nowait(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                except queue.Full: pass
                try: kpi_queue.put_nowait(("KPI", total_vehicles_counted, total_violations))
                except queue.Full: pass

        for v_id, data in list(vehicle_tracking_data.items()):
            if not data.get('tele_sent'):
                v_type_csv = 'Bình Thường' if not run_just_detect else 'Lưu thông Tự do'
                if data['is_wrongway_err']: v_type_csv = 'Ngược Chiều'
                elif data['is_redlight_err']: v_type_csv = 'Vượt Đèn Đỏ'
                elif run_speed and int(data['speed']) > speed_limit_live: v_type_csv = 'Quá Tốc Độ'
                
                plate_txt = data.get('plate_text', 'Không rõ')
                csv_speed = data['violation_speed'] if data['violation_speed'] > 0 else int(data['speed'])
                time_str_csv = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # =========================================================
                # ĐIỂM XUẤT DATA 4: KẾT THÚC VIDEO (ĐẨY TOÀN BỘ CÁC XE CÒN LẠI)
                # =========================================================
                with open(CSV_FILE_PATH, mode='a', newline='', encoding='utf-8') as f:
                    csv.writer(f).writerow([time_str_csv, v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv])
                    
                try: save_event_to_db(v_id, VI_CLASS_MAP.get(data['v_class'], ''), csv_speed, plate_txt, v_type_csv, "Hệ thống Camera AI")
                except: pass

                # 👉 LƯU ẢNH BẰNG CHỨNG NẾU XE NÀY CÓ LỖI (CHƯA KỊP GỬI TELEGRAM)
                if v_type_csv not in ['Bình Thường', 'Lưu thông Tự do']:
                    if data.get('best_crop') is not None:
                        cv2.imwrite(f"evidence/violation_{v_id}.jpg", data['best_crop'])
                
                data['tele_sent'] = True

        cap.release()
        tele_queue.join()
        kpi_queue.put(("DONE", total_vehicles_counted, total_violations))
    except Exception as e:
        kpi_queue.put(("ERROR", str(e)))