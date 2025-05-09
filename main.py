import asyncio
import logging
from bot import start_bot, register_admin_handlers, dp
from session_manager import session_manager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    try:
        # Регистрация обработчиков админского интерфейса
        register_admin_handlers(dp)
        
        # Запуск бота
        logger.info("Запуск бота...")
        await start_bot()
    except Exception as e:
        logger.error(f"Произошла ошибка при запуске бота: {e}")
    finally:
        # Остановка всех клиентов Pyrogram
        logger.info("Остановка клиентов Pyrogram...")
        await session_manager.stop_all_clients()

if __name__ == '__main__':
    try:
        # Запуск асинхронного приложения
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        # Обработка закрытия бота
        logger.info("Бот остановлен!")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise e 