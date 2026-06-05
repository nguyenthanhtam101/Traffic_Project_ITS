# CẤU HÌNH TỌA ĐỘ VÀ CAMERA
import os
import csv

REAL_WIDTH_M = 14.0
REAL_HEIGHT_M = 6.0
LOCATION_NAME = "Ngã tư X, TP.HCM"
VECTOR_TURN_THRESHOLD = 80
SGP_API_KEY = "v2:624967477e687c03ad77b348329f12293080ed52f2098b4dc67b20a1518340c5:JKUI3Cn29MLwxDgJCGewq9WhE1jCp3Rc"
os.makedirs("data", exist_ok=True)
CSV_FILE_PATH = "data/traffic_log.csv"
HEATMAP_CSV_PATH = "heatmap_log.csv"
# CẤU HÌNH TELEGRAM
TELEGRAM_BOT_TOKEN = "8724545022:AAEgeJZ8nE6zj5utIDb85C3dpNgzGcwsn2g"
TELEGRAM_CHAT_ID = "8066570830"

# KIỂM TRA VÀ TẠO TIÊU ĐỀ CSV CHUẨN
if not os.path.isfile(CSV_FILE_PATH) or os.path.getsize(CSV_FILE_PATH) == 0:
    with open(CSV_FILE_PATH, mode='w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(['Thời Gian', 'ID Xe', 'Loại Phương Tiện', 'Tốc Độ (km/h)', 'Biển Số', 'Trạng Thái'])
        
if not os.path.isfile(HEATMAP_CSV_PATH) or os.path.getsize(HEATMAP_CSV_PATH) == 0:
    with open(HEATMAP_CSV_PATH, mode='w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(['Thời Gian', 'Số Xe Kẹt'])
# CẤU HÌNH TỪ ĐIỂN MAP (DICT)
VI_CLASS_MAP = {'car': 'Ô tô', 'motorcycle': 'Xe máy', 'truck': 'Xe tải', 'bus': 'Xe buýt'}
COLOR_MAP = {'motorcycle': (255, 0, 0), 'bus': (0, 255, 255), 'car': (0, 255, 0), 'truck': (235, 134, 52), 'license_plate': (255, 255, 0)}

# CẤU HÌNH TOMTOM API
TOMTOM_API_KEY = "1Wa4kgOJxHIrECvCEjAPE82U6bDDT864" 
HCM_HOTSPOTS = {
    "Hầm Thủ Thiêm (Q1)": "10.7716,106.7133",
    "Ngã 6 Phù Đổng (Q1)": "10.7714,106.6923",
    "Vòng xoay Điện Biên Phủ (Q1)": "10.7915,106.7011",
    "Ngã Tư Hàng Xanh (Bình Thạnh)": "10.8015,106.7111",
    "Cầu Sài Gòn (Bình Thạnh)": "10.7984,106.7233",
    "Ngã Tư Phú Nhuận (Phú Nhuận)": "10.7981,106.6775",
    "Vòng Xoay Lăng Cha Cả (Tân Bình)": "10.7986,106.6575",
    "Ngã Tư Bảy Hiền (Tân Bình)": "10.7932,106.6527",
    "Vòng xoay Phạm Văn Đồng (Gò Vấp)": "10.8202,106.6874",
    "Vòng Xoay Dân Chủ (Q3)": "10.7935,106.6806",
    "Ngã 7 Lý Thái Tổ (Q10)": "10.7672,106.6745",
    "Cầu Vượt 3/2 (Q10)": "10.7735,106.6722",
    "Cầu Kênh Tẻ (Q4)": "10.7548,106.6975",
    "Ngã tư Nguyễn Văn Linh (Q7)": "10.7339,106.7032",
    "Ngã tư An Sương (Q12)": "10.8333,106.6136"
}