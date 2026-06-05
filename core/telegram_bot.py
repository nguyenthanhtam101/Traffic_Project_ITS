import os
import requests
import cv2
import time
import threading
import queue
import streamlit as st
from datetime import datetime
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOCATION_NAME, VI_CLASS_MAP

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
    plate_str = data.get('plate_text', 'Không rõ')
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ==============================================================
    # FORMAT TIN NHẮN CHUẨN ĐẦY ĐỦ THÔNG TIN
    # ==============================================================
    if violation_type == 'TRAFFIC_JAM': 
        alert_msg = (
            f"🚨 CẢNH BÁO KẸT XE!\n"
            f"📍 Địa điểm: {LOCATION_NAME}\n"
            f"🚥 Mật độ: Cao ({data.get('jam_count')} xe)\n"
            f"⏱ Thời gian: {time_str}"
        )
    elif violation_type == 'REDLIGHT': 
        alert_msg = (
            f"🚨 PHÁT HIỆN VƯỢT ĐÈN ĐỎ!\n"
            f"📍 Địa điểm: {LOCATION_NAME}\n"
            f"🚘 Loại xe: {v_type_vn}\n"
            f"🆔 Biển số: {plate_str}\n"
            f"📌 Lỗi: {data.get('alert_msg')}\n"
            f"⏱ Thời gian: {time_str}"
        )
    elif violation_type == 'WRONGWAY': 
        alert_msg = (
            f"🚨 PHÁT HIỆN ĐI NGƯỢC CHIỀU!\n"
            f"📍 Địa điểm: {LOCATION_NAME}\n"
            f"🚘 Loại xe: {v_type_vn}\n"
            f"🆔 Biển số: {plate_str}\n"
            f"📌 Lỗi: Đi ngược chiều\n"
            f"⏱ Thời gian: {time_str}"
        )
    else: # SPEED
        speed_report = data.get('violation_speed', int(data.get('speed', 0)))
        alert_msg = (
            f"🚨 PHÁT HIỆN VI PHẠM TỐC ĐỘ!\n"
            f"📍 Địa điểm: {LOCATION_NAME}\n"
            f"🚘 Loại xe: {v_type_vn}\n"
            f"🆔 Biển số: {plate_str}\n"
            f"⚡ Tốc độ: {speed_report} km/h (QĐ: {speed_limit_live} km/h)\n"
            f"⏱ Thời gian: {time_str}"
        )
    
    img_name_full = f"full_{track_id}_{plate_str}.jpg"
    img_name_crop = f"crop_{track_id}_{plate_str}.jpg"
    img_path_full = os.path.join(save_dir, img_name_full)
    img_path_crop = os.path.join(save_dir, img_name_crop) if data.get('best_crop') is not None else ""
    
    tele_queue.put((TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, alert_msg, img_path_full, img_path_crop, data['best_frame'], data.get('best_crop')))