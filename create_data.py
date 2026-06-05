import csv
import random
from datetime import datetime, timedelta

def generate_vietnamese_plate(v_type):
    """Hàm tạo biển số xe Việt Nam ngẫu nhiên"""
    # AI đôi khi không nhìn rõ biển số
    if random.random() < 0.15: 
        return "Không rõ"
        
    region = random.randint(41, 99) # Mã vùng
    if v_type == 'Xe máy':
        letter = random.choice('ABCDEFGHJKLMNPRSTUVWXYZ')
        num1 = random.randint(1, 9)
        num2 = random.randint(10000, 99999)
        return f"{region}-{letter}{num1} {num2}"
    else:
        letter = random.choice('ABCDEF')
        num2 = random.randint(1000, 99999)
        return f"{region}{letter}-{num2}"

def create_mock_data():
    filename = "data_simulate.csv"
    
    vehicle_types = ['Ô tô', 'Xe máy', 'Xe tải', 'Xe buýt']
    # Tỷ lệ: 40% Ô tô, 45% Xe máy, 10% Xe tải, 5% Xe buýt
    type_weights = [0.4, 0.45, 0.1, 0.05] 
    
    statuses = ['Bình Thường', 'Quá Tốc Độ', 'Vượt Đèn Đỏ', 'Ngược Chiều']
    # Tỷ lệ: 80% Bình thường, 10% Quá tốc độ, 5% Vượt đèn đỏ, 5% Ngược chiều
    status_weights = [0.8, 0.1, 0.05, 0.05]

    # Lùi thời gian về 2 tiếng trước để cộng dồn lên hiện tại
    current_time = datetime.now() - timedelta(hours=2)

    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Viết dòng tiêu đề (Header)
        writer.writerow(['Thời Gian', 'ID Xe', 'Loại Phương Tiện', 'Tốc Độ (km/h)', 'Biển Số', 'Trạng Thái'])

        for i in range(1, 101): # Sinh ra đúng 100 dòng
            # Mỗi xe chạy qua cách nhau ngẫu nhiên từ 2 đến 45 giây
            current_time += timedelta(seconds=random.randint(2, 45))
            time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # Chọn loại xe và trạng thái
            v_type = random.choices(vehicle_types, weights=type_weights)[0]
            status = random.choices(statuses, weights=status_weights)[0]
            
            # Logic tốc độ hợp lý
            if status == 'Quá Tốc Độ':
                speed = random.randint(60, 95) # Chắc chắn lớn hơn 55
            elif status == 'Bình Thường':
                speed = random.randint(15, 50)
            else:
                speed = random.randint(20, 60) # Vượt đèn đỏ/Ngược chiều thì tốc độ ngẫu nhiên
                
            # Tạo biển số
            plate = generate_vietnamese_plate(v_type)
            
            # Ghi dòng dữ liệu vào file
            writer.writerow([time_str, i, v_type, speed, plate, status])

    print(f"🎉 Đã tạo thành công file '{filename}' với 100 dòng dữ liệu siêu chuẩn!")
    print("👉 Hãy mở Tab 2 (Phân tích), bấm nút Upload và tải file này lên để Test biểu đồ nhé.")

if __name__ == "__main__":
    create_mock_data()