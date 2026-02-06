# YouTube Downloader Bot (Pyrogram)

Telegram бот для скачивания видео с YouTube с поддержкой файлов **до 2 ГБ**.

## Возможности

- Скачивание видео в MP4 с выбором качества (360p / 720p / 1080p / лучшее)
- Извлечение аудио в MP3
- Прогресс-бар скачивания и загрузки в Telegram
- Быстрая передача через MTProto (до 2 ГБ вместо 50 МБ в Bot API)
- Ускоренное скачивание через aria2c (16 параллельных соединений)
- Двухуровневый кэш: файловый (10 мин) + Telegram file_id (мгновенная повторная отправка)
- Docker-деплой

## Установка

### 1. Клонируй репозиторий

```bash
git clone <url> && cd ydownload
```

### 2. Установи зависимости

```bash
pip install -r requirements.txt
```

### 3. Установи FFmpeg и aria2c

**Windows:**
```bash
winget install FFmpeg
winget install aria2
```

**macOS:**
```bash
brew install ffmpeg aria2
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update && sudo apt install ffmpeg aria2
```

## Настройка

### Шаг 1: Получи API_ID и API_HASH

1. Перейди на [my.telegram.org](https://my.telegram.org)
2. Войди по номеру телефона
3. Нажми **"API development tools"**
4. Создай приложение (название любое)
5. Скопируй `api_id` и `api_hash`

### Шаг 2: Получи токен бота

1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`
3. Придумай имя и username
4. Скопируй токен

### Шаг 3: Заполни `.env`

Скопируй `.env.example` в `.env` и заполни значения:

```bash
cp .env.example .env
```

```env
API_ID=12345678
API_HASH=your_api_hash_here
BOT_TOKEN=your_bot_token_here
```

## Запуск

### Локально

```bash
python bot.py
```

### Docker

```bash
docker compose up -d --build
```

## Использование

1. Найди бота в Telegram
2. Отправь `/start`
3. Отправь ссылку на YouTube видео
4. Выбери формат: видео (360p / 720p / 1080p / лучшее) или аудио (MP3)
5. Дождись загрузки

## Ограничения

| Параметр | Лимит |
|----------|-------|
| Размер файла | 2 ГБ |
| Источник | Только YouTube |

## Структура проекта

```
ydownload/
├── bot.py                # Основной код бота
├── requirements.txt      # Зависимости Python
├── Dockerfile            # Docker-образ
├── docker-compose.yml    # Docker Compose конфигурация
├── .env.example          # Шаблон переменных окружения
├── .env                  # Переменные окружения (не в git)
├── .dockerignore         # Исключения для Docker
├── downloads/            # Временные файлы (создаётся автоматически)
└── *.session             # Сессия Pyrogram (создаётся при запуске)
```

## Решение проблем

### Ошибки авторизации
- Проверь правильность `API_ID`, `API_HASH` и `BOT_TOKEN` в `.env`
- Убедись, что токен бота актуален

### FFmpeg / aria2c not found
- Установи FFmpeg и aria2 (см. инструкцию выше)
- Перезапусти терминал после установки

### Видео не скачивается
- Проверь, что видео не приватное
- Обнови yt-dlp: `pip install -U yt-dlp`
- YouTube может временно блокировать — подожди и попробуй снова

## Лицензия

MIT
