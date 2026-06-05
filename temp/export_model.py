from ultralytics import YOLO

# Load mô hình tốt nhất của bạn
model = YOLO("best.pt")

# Thực hiện Ép xung (Export sang định dạng ONNX 16-bit)
model.export(format="onnx", half=True)