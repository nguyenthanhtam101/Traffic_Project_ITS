import requests
import time
import pandas as pd
import streamlit as st
from datetime import datetime

@st.cache_data(ttl=30)
def fetch_realtime_traffic(api_key, hotspots):
    data_records = []
    fetch_time = datetime.now()
    for name, coords in hotspots.items():
        url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?key={api_key}&point={coords}"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                flow = res.json().get('flowSegmentData', {})
                curr_speed = flow.get('currentSpeed', 0)
                free_speed = flow.get('freeFlowSpeed', 1)
                cong_pct = round((1 - (curr_speed / free_speed)) * 100, 1)
                cong_pct = max(0, min(100, cong_pct))
                
                if cong_pct > 60: status = "Kẹt Cứng"
                elif cong_pct > 30: status = "Ùn Ứ"
                else: status = "Thông Thoáng"
                
                district = name.split('(')[-1].replace(')', '').strip()
                node_name = name.split('(')[0].strip()
                lat, lon = map(float, coords.split(','))
                
                data_records.append({
                    "Thời Gian Fetch": fetch_time, "Khu Vực": district, "Nút Giao": node_name,
                    "Lat": lat, "Lon": lon, "Tốc Độ (km/h)": curr_speed,
                    "Mức Ùn Tắc (%)": cong_pct, "Trạng Thái": status
                })
        except Exception as e:
            pass
        time.sleep(1) # DELAY TRÁNH BỊ TOMTOM BLOCK
    return pd.DataFrame(data_records)