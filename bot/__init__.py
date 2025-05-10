# Инициализационный файл пакета bot

# Убираем циклические импорты
from .user_interface import bot, dp
from .admin_interface import register_admin_handlers, set_dispatcher

# Устанавливаем диспетчер для admin_interface
set_dispatcher(dp)

# Регистрируем админские обработчики
register_admin_handlers(dp)

__all__ = ['bot', 'dp', 'register_admin_handlers', 'start_bot']

# Импортируем функцию start_bot после установки диспетчера
from .user_interface import start_bot 