import os
from dotenv import load_dotenv
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения из .env файла
load_dotenv()

# Конфигурация бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN не найден в переменных окружения!")
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Значение по умолчанию для разработки

# Данные для Pyrogram
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

# Ключ шифрования
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default_key")

# ID чатов
CHAT_ID_1 = int(os.getenv("CHAT_ID_1", "-1001234567890"))
CHAT_ID_2 = int(os.getenv("CHAT_ID_2", "-1009876543210"))

# ID администраторов
admin_ids_str = os.getenv("ADMIN_IDS", "")
logger.info(f"Строка с ID администраторов из .env: '{admin_ids_str}'")

# Преобразуем строку с ID администраторов в список целых чисел
ADMIN_IDS = []
for admin_id in admin_ids_str.split(","):
    admin_id = admin_id.strip()
    logger.info(f"Обработка ID администратора: '{admin_id}'")
    if admin_id.isdigit():
        ADMIN_IDS.append(int(admin_id))
        logger.info(f"Добавлен ID администратора: {int(admin_id)}")
    else:
        logger.warning(f"Некорректный ID администратора: '{admin_id}' - не является числом")

logger.info(f"Итоговый список ID администраторов: {ADMIN_IDS}")

# Если список администраторов пуст, добавляем значение по умолчанию
if not ADMIN_IDS:
    logger.warning("Список администраторов пуст! Добавляем значение по умолчанию.")
    ADMIN_IDS = [12345678]  # Значение по умолчанию

# Информация о чатах
CHATS = {
    CHAT_ID_1: {
        "name": "Чат №1",
        "description": "Описание первого чата",
        "welcome_message": "Добро пожаловать в наш чат №1! Рады видеть вас здесь! 🎉"
    },
    CHAT_ID_2: {
        "name": "Чат №2",
        "description": "Описание второго чата",
        "welcome_message": "Добро пожаловать в наш чат №2! Рады видеть вас здесь! 🎊"
    }
}

# Настройки базы данных
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")

# Пути к файлам сессий
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Максимальное количество добавлений пользователей в день для одного аккаунта
MAX_ADDS_PER_DAY = int(os.getenv("MAX_ADDS_PER_DAY", "40"))

# ID чата для уведомлений администраторов
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", None)

# Тексты сообщений
MESSAGES = {
    "welcome": "Привет! 😎 Выбери чат, в который хочешь вступить:",
    "select_chat": "Выбери чат из списка ниже:",
    "confirm_join": "Ты хочешь вступить в {chat_name}?\nНажми кнопку ниже для подтверждения:",
    "join_button": "Вступить в чат ✅",
    "back_button": "Назад 🔙",
    "privacy_issue": "К сожалению, я не могу добавить тебя в чат из-за настроек приватности. 🔒\nНужно изменить настройки:",
    "success_join": "Добро пожаловать в чат! 🎉",
    "admin_notification": "Новый участник: @{username} добавлен в {chat_name} через аккаунт @{admin_username}",
    "manual_approval_needed": "⚠️ Пользователя @{username} не удалось добавить автоматически. Требуется ручное добавление.",
    "support_button": "Поддержка 🆘",
    "info_button": "Информация ℹ️",
    "settings_button": "Настройки ⚙️",
    "stats_button": "Статистика 📊",
    "users_button": "Пользователи 👥",
    "pending_button": "Заявки 🔄",
    "accounts_button": "Управление аккаунтами 🔒",
    "error_message": "Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь к администратору.",
    "bot_starting": "Бот запускается...",
    "bot_started": "Бот успешно запущен!",
    "bot_stopped": "Бот остановлен.",
    "command_not_found": "Команда не найдена. Используйте /start для начала работы."
}

# Инструкции по изменению настроек приватности с путями к изображениям
PRIVACY_INSTRUCTIONS = [
    {"text": "1. Открой настройки Telegram", "image": "screen/1.jpg"},
    {"text": "2. Перейди в 'Конфиденциальность'", "image": "screen/2.jpg"},
    {"text": "3. Выбери 'Группы и каналы' и разреши добавление 'Всем'", "image": "screen/3.jpg"},
]

# Настройки безопасности
SECURITY = {
    "max_attempts": 3,           # Максимальное количество попыток авторизации
    "ban_duration": 24,          # Продолжительность бана в часах
    "throttle_rate": 5,          # Количество запросов в секунду
    "suspicious_activity": {
        "enabled": True,         # Включение/выключение проверки на подозрительную активность
        "max_requests": 10,      # Максимальное количество запросов в минуту
        "block_duration": 1      # Продолжительность блокировки в часах
    }
} 