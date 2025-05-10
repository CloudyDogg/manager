# Инициализационный файл пакета bot
import os
from pathlib import Path

# Проверяем и создаем необходимые директории
from config import SESSIONS_DIR

# Создаем директорию для сессий, если она не существует
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Делаем базовый импорт сначала
from .user_interface import bot, dp 
from .admin_interface import register_admin_handlers, set_dispatcher

# Устанавливаем диспетчер для admin_interface
set_dispatcher(dp)

# Сначала регистрируем админские обработчики, чтобы они имели приоритет
register_admin_handlers(dp)

# Затем импортируем остальные функции
from .user_interface import start_bot

__all__ = ['bot', 'dp', 'register_admin_handlers', 'start_bot'] 