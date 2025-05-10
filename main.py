import asyncio
import logging
import signal
import sys
from bot import start_bot, register_admin_handlers, dp
from session_manager import session_manager
from config import MESSAGES

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Обработка сигналов для корректного завершения
def handle_shutdown_signals():
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda signum, frame: asyncio.create_task(shutdown()))
        except (ValueError, AttributeError):
            # Может произойти при запуске не из основного потока
            logger.warning(f"Не удалось установить обработчик для сигнала {sig}")

async def shutdown():
    """Корректное завершение работы"""
    logger.info("Остановка бота...")
    
    # Остановка всех клиентов Pyrogram
    logger.info("Остановка клиентов Pyrogram...")
    await session_manager.stop_all_clients()
    
    # Завершаем работу программы
    logger.info(MESSAGES.get("bot_stopped", "Бот остановлен."))
    sys.exit(0)

async def main():
    try:
        # Регистрация обработчиков сигналов
        handle_shutdown_signals()
        
        # Регистрация обработчиков админского интерфейса
        logger.info("Регистрация обработчиков админского интерфейса...")
        register_admin_handlers(dp)
        
        # Запуск бота
        logger.info(MESSAGES.get("bot_starting", "Запуск бота..."))
        result = await start_bot()
        
        if result:
            logger.info(MESSAGES.get("bot_started", "Бот успешно запущен!"))
        else:
            logger.error("Ошибка при запуске бота")
            
    except Exception as e:
        logger.error(f"Произошла ошибка при запуске бота: {e}")
        await shutdown()

if __name__ == '__main__':
    try:
        # Запуск асинхронного приложения
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        # Обработка закрытия бота
        logger.info(MESSAGES.get("bot_stopped", "Бот остановлен!"))
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        # Аварийное завершение с кодом ошибки
        sys.exit(1) 