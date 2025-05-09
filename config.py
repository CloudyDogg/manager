import os
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# Конфигурация бота
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Данные для Pyrogram
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

# Ключ шифрования
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default_key")

# ID чатов
CHAT_ID_1 = int(os.getenv("CHAT_ID_1", 0))
CHAT_ID_2 = int(os.getenv("CHAT_ID_2", 0))

# Информация о чатах
CHATS = {
    CHAT_ID_1: {
        "name": "Чат №1",
        "description": "Описание первого чата",
    },
    CHAT_ID_2: {
        "name": "Чат №2",
        "description": "Описание второго чата",
    }
}

# Настройки базы данных
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")

# Пути к файлам сессий
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Максимальное количество добавлений пользователей в день для одного аккаунта
MAX_ADDS_PER_DAY = 40

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
}

# Инструкции по изменению настроек приватности с путями к изображениям
PRIVACY_INSTRUCTIONS = [
    {"text": "1. Открой настройки Telegram", "image": "screen/1.jpg"},
    {"text": "2. Перейди в 'Конфиденциальность'", "image": "screen/2.jpg"},
    {"text": "3. Выбери 'Группы и каналы' и разреши добавление 'Всем'", "image": "screen/3.jpg"},
] 