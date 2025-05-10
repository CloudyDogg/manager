# Инициализационный файл пакета bot

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