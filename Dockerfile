FROM python:3.12-slim

# Установка ffmpeg и gcc (нужен для сборки tgcrypto)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg gcc libc6-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости (кэшируется отдельным слоем)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код бота
COPY bot.py .

# Папка для загрузок
RUN mkdir -p /app/downloads

CMD ["python", "bot.py"]
