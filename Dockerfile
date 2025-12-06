FROM python:3.11-slim

WORKDIR /app

# 安裝 curl（用來下載字體）
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 下載中文字體
RUN mkdir -p fonts && \
    curl -L "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC-Regular.ttf" \
    -o fonts/NotoSansTC-Regular.ttf

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
