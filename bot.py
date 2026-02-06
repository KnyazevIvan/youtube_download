import os
import glob
import time
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import yt_dlp

# ═══════════════════════════════════════════════════════════════
#                         НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════

# Получить на https://my.telegram.org -> API Development
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")

# Получить у @BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Папка для временных файлов
DOWNLOAD_PATH = "./downloads"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
#                         ЛОГИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#                         ИНИЦИАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════

app = Client(
    "youtube_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    max_concurrent_transmissions=3,
)

# Хранилище данных пользователей (в памяти)
user_data = {}

# Раздельные пулы потоков
info_executor = ThreadPoolExecutor(max_workers=4)       # метаданные
download_executor = ThreadPoolExecutor(max_workers=3)   # загрузки

# ═══════════════════════════════════════════════════════════════
#                      КЭШИРОВАНИЕ ФАЙЛОВ
# ═══════════════════════════════════════════════════════════════

CACHE_TTL = 600  # 10 минут

# Кэш: (url, format_type, quality) -> {"path": str, "timestamp": float}
file_cache = {}

# Кэш Telegram file_id: (url, format_type, quality) -> telegram file_id
# Повторная отправка по file_id — мгновенная, без скачивания и аплоада
telegram_file_cache = {}


def cache_get(url: str, format_type: str, quality: str = "") -> str | None:
    """Получить файл из кэша, если есть и не протух"""
    key = (url, format_type, quality)
    entry = file_cache.get(key)
    if entry is None:
        return None
    if time.time() - entry["timestamp"] > CACHE_TTL:
        # Протух — удаляем запись (файл может быть уже удалён)
        file_cache.pop(key, None)
        return None
    if not os.path.exists(entry["path"]):
        file_cache.pop(key, None)
        return None
    return entry["path"]


def cache_put(url: str, format_type: str, quality: str, path: str):
    """Положить файл в кэш"""
    file_cache[(url, format_type, quality)] = {
        "path": path,
        "timestamp": time.time(),
    }


# ═══════════════════════════════════════════════════════════════
#                      ФОРМАТЫ КАЧЕСТВА
# ═══════════════════════════════════════════════════════════════

FORMAT_MAP = {
    "360":  "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/best",
    "720":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best",
    "1080": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best",
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
}

# ═══════════════════════════════════════════════════════════════
#                      ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════

def get_ydl_opts():
    """Базовые опции yt-dlp — ускорение + обход блокировок YouTube"""
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "extractor_retries": 5,
        "http_chunk_size": 10485760,            # 10 MB
        "external_downloader": "aria2c",
        "external_downloader_args": {
            "aria2c": [
                "--min-split-size=1M",
                "--max-connection-per-server=16",
                "--max-concurrent-downloads=16",
                "--split=16",
            ]
        },
        "throttledratelimit": 100000,           # переподключение при <100 KB/s
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }


def get_video_info(url: str) -> dict:
    """Получение информации о видео (синхронная)"""
    opts = get_ydl_opts()
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def make_progress_hook(loop, message):
    """Создаёт progress_hook для yt-dlp, обновляющий Telegram-сообщение"""
    last_update_time = [0.0]

    def hook(d):
        status = d.get("status")
        now = time.time()

        # Троттлинг: не чаще раза в 3 секунды
        if now - last_update_time[0] < 3:
            return

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta")

            if total > 0:
                percent = downloaded * 100 / total
                filled = int(percent / 5)
                bar = "█" * filled + "░" * (20 - filled)
                speed_str = f"{speed / (1024 * 1024):.1f} МБ/с" if speed else "..."
                eta_str = f"{eta}с" if eta else "..."
                text = (
                    f"⬇️ **Скачиваю с YouTube...**\n\n"
                    f"[{bar}] {percent:.1f}%\n"
                    f"📊 {format_size(downloaded)} / {format_size(total)}\n"
                    f"🚀 {speed_str} | ⏱ {eta_str}"
                )
            else:
                text = (
                    f"⬇️ **Скачиваю с YouTube...**\n\n"
                    f"📊 {format_size(downloaded)}"
                )

            last_update_time[0] = now
            asyncio.run_coroutine_threadsafe(
                _safe_edit(message, text), loop
            )

        elif status == "finished":
            last_update_time[0] = now
            asyncio.run_coroutine_threadsafe(
                _safe_edit(message, "⚙️ **Обработка FFmpeg...**"), loop
            )

    return hook


async def _safe_edit(message, text):
    """Безопасное редактирование сообщения (игнорирует ошибки)"""
    try:
        await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


def download_media(url: str, output_path: str, format_type: str,
                   quality: str = "best", progress_hook=None) -> str:
    """Скачивание медиа (синхронная)"""
    base_opts = get_ydl_opts()

    if format_type == "video":
        fmt = FORMAT_MAP.get(quality, FORMAT_MAP["best"])
        ydl_opts = {
            **base_opts,
            "format": fmt,
            "outtmpl": output_path,
            "merge_output_format": "mp4",
            "postprocessor_args": {
                "merger": ["-c", "copy"],
                "ffmpeg": ["-threads", "0"],
            },
        }
    else:  # audio
        output_base = output_path.rsplit('.', 1)[0]
        ydl_opts = {
            **base_opts,
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "outtmpl": f"{output_base}.%(ext)s",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "postprocessor_args": {
                "ffmpeg": ["-threads", "0"],
            },
        }

    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Для аудио возвращаем путь к mp3
    if format_type == "audio":
        return f"{output_base}.mp3"
    return output_path


def cleanup_files(file_id: str):
    """Очистка всех файлов по префиксу file_id"""
    pattern = os.path.join(DOWNLOAD_PATH, f"{file_id}*")
    for f in glob.glob(pattern):
        try:
            os.remove(f)
            logger.info(f"Удалён: {f}")
        except Exception as e:
            logger.warning(f"Не удалось удалить {f}: {e}")


def cleanup_downloads_on_start():
    """Очистка старых файлов при старте бота"""
    pattern = os.path.join(DOWNLOAD_PATH, "*")
    removed = 0
    for f in glob.glob(pattern):
        if os.path.isfile(f):
            try:
                os.remove(f)
                removed += 1
            except Exception:
                pass
    if removed:
        logger.info(f"Очищено {removed} старых файлов при старте")


def format_duration(seconds: int) -> str:
    """Форматирование длительности"""
    if seconds < 3600:
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}:{secs:02d}"
    else:
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"


def format_size(bytes_size: int) -> str:
    """Форматирование размера файла"""
    if bytes_size < 1024:
        return f"{bytes_size} Б"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} КБ"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} МБ"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} ГБ"


# ═══════════════════════════════════════════════════════════════
#                         ПРОГРЕСС ЗАГРУЗКИ В TELEGRAM
# ═══════════════════════════════════════════════════════════════

async def progress_callback(current: int, total: int, message: Message, action: str):
    """Отображение прогресса загрузки в Telegram"""
    try:
        percent = current * 100 / total
        filled = int(percent / 5)
        bar = "█" * filled + "░" * (20 - filled)

        text = (
            f"⬆️ Загружаю в Telegram...\n\n"
            f"[{bar}] {percent:.1f}%\n"
            f"📊 {format_size(current)} / {format_size(total)}"
        )

        # Обновляем не чаще раза в 2 секунды
        if not hasattr(progress_callback, 'last_update'):
            progress_callback.last_update = {}

        msg_id = message.id
        now = asyncio.get_event_loop().time()

        if msg_id not in progress_callback.last_update or \
           now - progress_callback.last_update[msg_id] > 3:
            asyncio.create_task(_safe_edit(message, text))
            progress_callback.last_update[msg_id] = now

    except Exception as e:
        logger.debug(f"Ошибка обновления прогресса: {e}")


# ═══════════════════════════════════════════════════════════════
#                         ОБРАБОТЧИКИ КОМАНД
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Приветственное сообщение"""
    await message.reply_text(
        "👋 **Привет!** Я бот для скачивания видео с YouTube.\n\n"
        "🎬 Просто отправь мне ссылку на видео, и я скачаю его для тебя.\n\n"
        "✨ **Возможности:**\n"
        "• Скачивание видео до **2 ГБ**\n"
        "• Выбор качества: 360p / 720p / 1080p / Лучшее\n"
        "• Извлечение аудио в MP3\n"
        "• Прогресс-бар скачивания\n\n"
        "📖 Команды:\n"
        "/start - начать\n"
        "/help - помощь",
        parse_mode=ParseMode.MARKDOWN
    )


@app.on_message(filters.command("help"))
async def help_command(client: Client, message: Message):
    """Справка"""
    await message.reply_text(
        "📖 **Как пользоваться:**\n\n"
        "1️⃣ Скопируй ссылку на YouTube видео\n"
        "2️⃣ Отправь её мне\n"
        "3️⃣ Выбери качество видео или аудио\n"
        "4️⃣ Дождись загрузки\n\n"
        "🎬 **Качество видео:**\n"
        "• 360p — быстро, мало трафика\n"
        "• 720p — хорошее качество\n"
        "• 1080p — высокое качество\n"
        "• Лучшее — максимум\n\n"
        "⚠️ **Ограничения:**\n"
        "• Максимальный размер: 2 ГБ\n"
        "• Только YouTube ссылки\n\n"
        "💡 **Совет:** 360p загружается в 5-10 раз быстрее!",
        parse_mode=ParseMode.MARKDOWN
    )


# ═══════════════════════════════════════════════════════════════
#                      ОБРАБОТКА ССЫЛОК
# ═══════════════════════════════════════════════════════════════

@app.on_message(filters.text & ~filters.command(["start", "help"]))
async def handle_url(client: Client, message: Message):
    """Обработка YouTube ссылки"""
    url = message.text.strip()

    # Проверяем, что это ссылка на YouTube
    if "youtube.com" not in url and "youtu.be" not in url:
        await message.reply_text(
            "❌ Пожалуйста, отправь ссылку на YouTube видео.\n\n"
            "Примеры:\n"
            "• `https://www.youtube.com/watch?v=...`\n"
            "• `https://youtu.be/...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Сохраняем URL
    user_data[message.from_user.id] = {"url": url}

    status_msg = await message.reply_text("🔍 Получаю информацию о видео...")

    try:
        # Получаем информацию в пуле для метаданных
        loop = asyncio.get_event_loop()
        info = await asyncio.wait_for(
            loop.run_in_executor(info_executor, get_video_info, url),
            timeout=60.0
        )

        title = info.get("title", "Видео")
        duration = info.get("duration", 0)
        channel = info.get("channel", info.get("uploader", "Неизвестно"))
        view_count = info.get("view_count", 0)

        # Сохраняем информацию
        user_data[message.from_user.id]["info"] = info

        # Форматируем просмотры
        if view_count >= 1_000_000:
            views_str = f"{view_count / 1_000_000:.1f}M"
        elif view_count >= 1_000:
            views_str = f"{view_count / 1_000:.1f}K"
        else:
            views_str = str(view_count)

        # Кнопки выбора качества
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📹 360p", callback_data="video_360"),
                InlineKeyboardButton("📺 720p", callback_data="video_720"),
            ],
            [
                InlineKeyboardButton("🎬 1080p", callback_data="video_1080"),
                InlineKeyboardButton("🏆 Лучшее", callback_data="video_best"),
            ],
            [
                InlineKeyboardButton("🎵 MP3 (аудио)", callback_data="audio"),
            ],
            [
                InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
            ]
        ])

        await status_msg.edit_text(
            f"📹 **{title}**\n\n"
            f"📺 Канал: {channel}\n"
            f"⏱ Длительность: {format_duration(duration)}\n"
            f"👁 Просмотры: {views_str}\n\n"
            "**Выбери качество:**",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    except asyncio.TimeoutError:
        logger.error("Таймаут при получении информации")
        await status_msg.edit_text(
            "❌ **Ошибка:** превышено время ожидания.\n\n"
            "Возможные причины:\n"
            "• Видео недоступно в вашем регионе\n"
            "• Проблемы с сетью\n"
            "• YouTube временно блокирует запросы\n\n"
            "Попробуй позже.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text(
            f"❌ **Ошибка:** не удалось получить информацию.\n\n"
            f"`{str(e)[:200]}`",
            parse_mode=ParseMode.MARKDOWN
        )


# ═══════════════════════════════════════════════════════════════
#                      ОБРАБОТКА КНОПОК
# ═══════════════════════════════════════════════════════════════

@app.on_callback_query()
async def handle_callback(client: Client, callback: CallbackQuery):
    """Обработка нажатия кнопок"""
    await callback.answer()

    user_id = callback.from_user.id
    action = callback.data

    # Отмена
    if action == "cancel":
        if user_id in user_data:
            del user_data[user_id]
        await callback.message.edit_text("❌ Загрузка отменена.")
        return

    # Проверяем данные
    if user_id not in user_data or "url" not in user_data[user_id]:
        await callback.message.edit_text(
            "❌ Ссылка не найдена. Отправь её заново."
        )
        return

    url = user_data[user_id]["url"]
    info = user_data[user_id].get("info", {})
    title = info.get("title", "video")

    # Определяем тип и качество
    if action.startswith("video_"):
        format_type = "video"
        quality = action.replace("video_", "")  # "360", "720", "1080", "best"
        quality_label = {"360": "360p", "720": "720p", "1080": "1080p", "best": "Лучшее"}
        dl_text = f"Скачиваю видео ({quality_label.get(quality, quality)})..."
    else:
        format_type = "audio"
        quality = ""
        dl_text = "Извлекаю аудио..."

    # 1) Проверяем кэш Telegram file_id (мгновенная отправка)
    tg_cache_key = (url, format_type, quality)
    cached_file_id = telegram_file_cache.get(tg_cache_key)
    if cached_file_id:
        logger.info(f"Telegram file_id кэш-хит: {tg_cache_key}")
        try:
            if format_type == "video":
                await client.send_video(
                    chat_id=callback.message.chat.id,
                    video=cached_file_id,
                    caption=f"🎬 **{title}**",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                )
            else:
                await client.send_audio(
                    chat_id=callback.message.chat.id,
                    audio=cached_file_id,
                    caption=f"🎵 **{title}**",
                    parse_mode=ParseMode.MARKDOWN,
                    title=title,
                )
            await callback.message.delete()
            if user_id in user_data:
                del user_data[user_id]
            return
        except Exception as e:
            logger.warning(f"Ошибка отправки по file_id: {e}")
            telegram_file_cache.pop(tg_cache_key, None)

    # 2) Проверяем файловый кэш (файл на диске)
    cached_path = cache_get(url, format_type, quality)
    if cached_path:
        logger.info(f"Файловый кэш-хит: {cached_path}")
        await callback.message.edit_text(
            f"⚡ **Файл найден в кэше!**\n\n"
            f"📤 Загружаю в Telegram...",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            file_size = os.path.getsize(cached_path)
            if file_size > 2 * 1024 * 1024 * 1024:
                await callback.message.edit_text(
                    f"❌ Файл слишком большой: {format_size(file_size)}\nЛимит Telegram: 2 ГБ"
                )
                return
            await _send_file(client, callback, cached_path, format_type, title, tg_cache_key)
            await callback.message.delete()
            if user_id in user_data:
                del user_data[user_id]
            return
        except Exception as e:
            logger.warning(f"Ошибка отправки из кэша: {e}")
            # Продолжаем обычную загрузку

    # Начинаем загрузку
    await callback.message.edit_text(
        f"⏳ **{dl_text}**\n\n"
        "Это может занять несколько минут.",
        parse_mode=ParseMode.MARKDOWN
    )

    # Уникальное имя файла
    file_id = f"{user_id}_{int(time.time())}"

    if format_type == "video":
        output_file = os.path.join(DOWNLOAD_PATH, f"{file_id}.mp4")
    else:
        output_file = os.path.join(DOWNLOAD_PATH, f"{file_id}.mp3")

    try:
        loop = asyncio.get_event_loop()

        # Создаём progress hook
        hook = make_progress_hook(loop, callback.message)

        # Скачиваем в пуле для загрузок
        final_file = await loop.run_in_executor(
            download_executor,
            download_media,
            url,
            output_file,
            format_type,
            quality,
            hook,
        )

        # Проверяем файл
        if not os.path.exists(final_file):
            await callback.message.edit_text("❌ Не удалось скачать файл.")
            return

        file_size = os.path.getsize(final_file)

        # Проверяем размер (лимит Telegram 2 ГБ)
        if file_size > 2 * 1024 * 1024 * 1024:
            await callback.message.edit_text(
                f"❌ Файл слишком большой: {format_size(file_size)}\n\n"
                "Лимит Telegram: 2 ГБ\n\n"
                "💡 Попробуй выбрать качество пониже (360p или 720p)."
            )
            cleanup_files(file_id)
            return

        # Кэшируем файл
        cache_put(url, format_type, quality, final_file)

        # Отправляем файл
        await callback.message.edit_text(
            f"📤 **Загружаю в Telegram...**\n\n"
            f"📊 Размер: {format_size(file_size)}",
            parse_mode=ParseMode.MARKDOWN
        )

        await _send_file(client, callback, final_file, format_type, title, tg_cache_key)

        # Удаляем статусное сообщение
        await callback.message.delete()

        # Очищаем данные пользователя
        if user_id in user_data:
            del user_data[user_id]

    except Exception as e:
        logger.error(f"Ошибка при скачивании: {e}")
        await callback.message.edit_text(
            f"❌ **Ошибка при скачивании:**\n\n"
            f"`{str(e)[:300]}`",
            parse_mode=ParseMode.MARKDOWN
        )

    finally:
        # Удаляем временные файлы (кроме закэшированных)
        cached = cache_get(url, format_type, quality)
        if not cached:
            cleanup_files(file_id)


async def _send_file(client: Client, callback: CallbackQuery,
                     file_path: str, format_type: str, title: str,
                     tg_cache_key: tuple = None):
    """Отправка файла в Telegram + кэширование file_id"""
    if format_type == "video":
        msg = await client.send_video(
            chat_id=callback.message.chat.id,
            video=file_path,
            caption=f"🎬 **{title}**",
            parse_mode=ParseMode.MARKDOWN,
            supports_streaming=True,
            progress=progress_callback,
            progress_args=(callback.message, "upload")
        )
        # Сохраняем file_id для мгновенной повторной отправки
        if tg_cache_key and msg.video:
            telegram_file_cache[tg_cache_key] = msg.video.file_id
            logger.info(f"Telegram file_id закэширован: {tg_cache_key}")
    else:
        msg = await client.send_audio(
            chat_id=callback.message.chat.id,
            audio=file_path,
            caption=f"🎵 **{title}**",
            parse_mode=ParseMode.MARKDOWN,
            title=title,
            progress=progress_callback,
            progress_args=(callback.message, "upload")
        )
        # Сохраняем file_id для мгновенной повторной отправки
        if tg_cache_key and msg.audio:
            telegram_file_cache[tg_cache_key] = msg.audio.file_id
            logger.info(f"Telegram file_id закэширован: {tg_cache_key}")


# ═══════════════════════════════════════════════════════════════
#                           ЗАПУСК
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Очистка старых файлов при старте
    cleanup_downloads_on_start()

    print("=" * 50)
    print("🤖 YouTube Downloader Bot")
    print("=" * 50)
    print(f"📁 Папка загрузок: {os.path.abspath(DOWNLOAD_PATH)}")
    print("✨ Pyrogram MTProto - файлы до 2 ГБ")
    print("🚀 aria2c — 16 соединений параллельно")
    print("📊 Прогресс-бар скачивания")
    print("💾 Кэш файлов (10 мин) + Telegram file_id кэш")
    print("=" * 50)
    print("🚀 Бот запускается...")
    print()

    app.run()
