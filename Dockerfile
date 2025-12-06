FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 改用這個連結（Google Fonts CDN）
RUN mkdir -p fonts && \
    curl -L "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansTC-Regular.otf" \
    -o fonts/NotoSansTC-Regular.otf

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
