FROM python:3.9-slim

# Cài đặt thư viện hệ thống
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Cài đặt Python
RUN pip3 install -r requirements.txt

EXPOSE 7860
ENTRYPOINT ["streamlit", "run", "main.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.enableCORS=false", "--server.enableXsrfProtection=false", "--server.maxUploadSize=500"]