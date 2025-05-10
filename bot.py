import os
import logging
import asyncio
import base64
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pyrogram import Client, filters, types, errors, raw, enums
from pyrogram.raw import functions
from pyrogram.errors import UserAlreadyParticipant, UserPrivacyRestricted, PeerFlood, InviteHashExpired
from cryptography.fernet import Fernet
import json
from database import init_db, get_session, User, AdminAccount, JoinRequest, encrypt_session, decrypt_session, get_fernet_key, get_setting, set_setting, Base, engine, RateLimitBlock, check_rate_limit, block_user_rate_limit, unblock_user_rate_limit, get_rate_limited_users
import re
import html

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# Получаем ID чатов напрямую из переменных окружения
CHAT_ID_1_STR = os.getenv("CHAT_ID_1")
CHAT_ID_2_STR = os.getenv("CHAT_ID_2")

# Прямое использование ID чатов
CHAT_ID_1 = int(CHAT_ID_1_STR) if CHAT_ID_1_STR and CHAT_ID_1_STR != "0" else 0
CHAT_ID_2 = int(CHAT_ID_2_STR) if CHAT_ID_2_STR and CHAT_ID_2_STR != "0" else 0

# Запасные ссылки на чаты
CHAT_LINK_1 = os.getenv("CHAT_LINK_1", "")
CHAT_LINK_2 = os.getenv("CHAT_LINK_2", "")

logger.info(f"Используются ID чатов: {CHAT_ID_1}, {CHAT_ID_2}")
logger.info(f"Запасные ссылки на чаты: {CHAT_LINK_1}, {CHAT_LINK_2}")

# Инициализация бота
bot = Client("telegram_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Глобальная переменная для хранения активной сессии администратора
active_admin_client = None

def convert_to_supergroup_id(chat_id):
    """
    Преобразует публичный ID супергруппы в формат, который требуется для Pyrogram
    """
    if isinstance(chat_id, int) and chat_id < 0:
        # Если ID начинается с -100, преобразуем его
        if str(chat_id).startswith('-100'):
            # Отрезаем -100 и возвращаем числовой ID
            return int(str(chat_id)[4:])
    return chat_id

async def get_admin_client():
    """
    Получение активного клиента администратора с ротацией
    """
    global active_admin_client
    
    # Если клиент уже создан и активен, возвращаем его
    if active_admin_client and active_admin_client.is_connected:
        logger.info("Используем существующий клиент администратора")
        return active_admin_client
    
    # Если клиент существует, но не активен, пытаемся остановить его
    if active_admin_client:
        try:
            current_phone = "неизвестно"
            if hasattr(active_admin_client, "_phone"):
                current_phone = active_admin_client._phone
            logger.info(f"Останавливаем неактивный клиент для аккаунта {current_phone}")
            await active_admin_client.stop()
        except Exception as e:
            logger.error(f"Ошибка при остановке клиента: {e}")
        active_admin_client = None
    
    session = get_session()
    try:
        # Получаем аккаунт администратора с наименьшим количеством использований
        admin_account = session.query(AdminAccount).filter_by(active=True).order_by(AdminAccount.usage_count).first()
        
        if not admin_account:
            logger.error("Нет доступных аккаунтов администраторов")
            return None
        
        logger.info(f"Используем аккаунт администратора: {admin_account.phone}")
        
        # Расшифровываем данные сессии
        try:
            session_data = decrypt_session(admin_account.session_data)
            if not session_data:
                logger.error(f"Не удалось расшифровать данные сессии для аккаунта {admin_account.phone}")
                # Деактивируем проблемный аккаунт
                admin_account.active = False
                session.commit()
                logger.warning(f"Аккаунт {admin_account.phone} деактивирован из-за ошибки расшифровки")
                return None
                
            session_string = session_data.get("session_string")
            
            if not session_string:
                logger.error("Нет строки сессии в данных аккаунта")
                # Деактивируем проблемный аккаунт
                admin_account.active = False
                session.commit()
                logger.warning(f"Аккаунт {admin_account.phone} деактивирован из-за отсутствия строки сессии")
                return None
                
            # Создаем клиент из строки сессии
            logger.info(f"Создаем клиент для аккаунта {admin_account.phone}")
            client = Client(
                name=f"admin_{admin_account.phone}",  # Уникальное имя для каждого клиента
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
                in_memory=True
            )
            
            # Устанавливаем атрибут телефона для удобства идентификации
            client._phone = admin_account.phone
            
            try:
                # Запускаем клиент
                logger.info(f"Запускаем клиент для аккаунта {admin_account.phone}")
                await client.start()
                logger.info("Клиент администратора запущен успешно")
                
                # Получаем информацию о самом себе для проверки
                me = await client.get_me()
                logger.info(f"Авторизован как: {me.first_name} {me.last_name or ''} (@{me.username or 'нет'})")
                
                # Обновляем статистику использования
                admin_account.last_used = datetime.now()
                admin_account.usage_count += 1
                session.commit()
                
                active_admin_client = client
                return client
            except Exception as client_err:
                logger.error(f"Ошибка при запуске клиента для аккаунта {admin_account.phone}: {client_err}")
                # Деактивируем проблемный аккаунт
                admin_account.active = False
                session.commit()
                logger.warning(f"Аккаунт {admin_account.phone} деактивирован из-за ошибки запуска клиента")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при инициализации клиента: {e}")
            # Деактивируем проблемный аккаунт
            admin_account.active = False
            session.commit()
            logger.warning(f"Аккаунт {admin_account.phone} деактивирован из-за ошибки")
            return None
    except Exception as e:
        logger.error(f"Ошибка при получении клиента администратора: {e}")
        return None
    finally:
        session.close()

async def switch_to_next_admin():
    """
    Переключение на следующий доступный аккаунт администратора
    """
    global active_admin_client
    
    # Останавливаем текущий клиент, если он есть
    if active_admin_client:
        try:
            current_phone = None
            if hasattr(active_admin_client, "_phone"):
                current_phone = active_admin_client._phone
                
            logger.info(f"Переключение с текущего аккаунта администратора: {current_phone}")
            
            await active_admin_client.stop()
        except Exception as e:
            logger.error(f"Ошибка при остановке клиента: {e}")
        active_admin_client = None
    
    session = get_session()
    try:
        # Получаем все активные аккаунты админов
        active_accounts = session.query(AdminAccount).filter_by(active=True).order_by(AdminAccount.usage_count).all()
        
        if not active_accounts:
            logger.error("Нет доступных аккаунтов администраторов для переключения")
            return False
        
        # Выбираем аккаунт с наименьшим количеством использований
        next_account = active_accounts[0]
        
        logger.info(f"Переключение на аккаунт: {next_account.phone} (использований: {next_account.usage_count})")
        
        # Устанавливаем счетчик использований заново
        active_admin_client = None
        
        # При следующем вызове get_admin_client() будет выбран новый аккаунт
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при переключении на следующий аккаунт: {e}")
        return False
    finally:
        session.close()

async def add_user_to_chat(user_id, chat_id):
    """
    Прямое добавление пользователя в чат администратором
    """
    # Проверяем, включено ли автоматическое добавление
    auto_add_enabled = get_setting("auto_add_enabled", "true")
    logger.info(f"Статус автодобавления: auto_add_enabled = '{auto_add_enabled}'")
    
    if auto_add_enabled.lower() != "true":
        logger.info(f"⚠️ Автоматическое добавление отключено. Пользователь {user_id} не был добавлен автоматически.")
        return False, "auto_add_disabled"
    
    logger.info(f"✅ Автоматическое добавление включено. Начинаем добавление пользователя {user_id}")
    
    admin_client = await get_admin_client()
    if not admin_client:
        logger.error(f"Нет доступного администратора")
        return False, "Нет доступного администратора для добавления в чат"
    
    # Определяем имя чата
    chat_name = "основной чат" if chat_id == CHAT_ID_1 else "второй чат"
    
    try:
        # Получаем список всех чатов для админа
        logger.info("Получаем список чатов")
        dialogs = []
        async for dialog in admin_client.get_dialogs():
            dialogs.append(dialog)
            logger.info(f"Найден чат: {dialog.chat.title or dialog.chat.first_name} (ID: {dialog.chat.id})")
        
        # Ищем чат с заголовком "test"
        target_chat = None
        for dialog in dialogs:
            if dialog.chat.title == "test":
                target_chat = dialog.chat
                logger.info(f"Найден нужный чат: {dialog.chat.title} (ID: {dialog.chat.id})")
                break
        
        if not target_chat:
            logger.error("Не удалось найти целевой чат")
            return False, "Не удалось найти чат для добавления"
        
        # Получаем информацию о пользователе
        user_info = await admin_client.get_users(user_id)
        logger.info(f"Получена информация о пользователе: {user_info.first_name} {user_info.last_name or ''}")
        
        # Проверяем статус предыдущей заявки
        session = get_session()
        try:
            # Проверяем, была ли отправка контакта ранее
            previous_request = session.query(JoinRequest).filter_by(
                user_id=user_id, 
                chat_id=chat_id, 
                status="contact_sent"
            ).first()
            
            if previous_request:
                logger.info(f"Найдена предыдущая заявка со статусом contact_sent для пользователя {user_id}")
                
                # Добавляем пользователя в контакты администратора для взаимной связи
                try:
                    # Импортируем необходимые классы для работы с контактами
                    from pyrogram.raw.functions.contacts import ImportContacts
                    from pyrogram.raw.types import InputPhoneContact
                    
                    # Генерируем случайный ID клиента
                    import random
                    client_id = random.randint(0, 2**63 - 1)
                    
                    # Получаем телефон пользователя или создаем фейковый на основе ID
                    user_phone = f"+{user_id}"  # Используем ID как номер телефона если нет реального
                    
                    # Добавляем пользователя в контакты администратора
                    logger.info(f"Добавляем пользователя {user_id} в контакты администратора")
                    result = await admin_client.invoke(
                        ImportContacts(
                            contacts=[
                                InputPhoneContact(
                                    client_id=client_id,
                                    phone=user_phone,
                                    first_name=user_info.first_name or "User",
                                    last_name=user_info.last_name or ""
                                )
                            ]
                        )
                    )
                    logger.info(f"Результат добавления в контакты администратора: {result}")
                    
                    # Отправляем уведомление администратору о взаимных контактах
                    try:
                        admin_info = await admin_client.get_me()
                        await admin_client.send_message(
                            admin_info.id,
                            f"👥 Установлена взаимная связь контактов с пользователем {user_info.first_name} {user_info.last_name or ''} (ID: {user_id}).\n\n"
                            f"✅ Теперь бот сможет добавить его в чат, так как контакты стали взаимными."
                        )
                        logger.info(f"Отправлено уведомление администратору о взаимных контактах с пользователем {user_id}")
                    except Exception as admin_notify_error:
                        logger.error(f"Ошибка при отправке уведомления администратору о взаимных контактах: {admin_notify_error}")
                    
                    # Обновляем статус заявки на pending для дальнейшей обработки
                    previous_request.status = "pending"
                    session.commit()
                    logger.info(f"Статус заявки изменен на pending для повторной попытки добавления")
                    
                    # Сообщаем пользователю о процессе добавления
                    try:
                        await bot.send_message(
                            user_id,
                            f"✅ Отлично! Вижу, что вы добавили контакт администратора.\n\n"
                            f"🔄 Теперь бот настраивает взаимные контакты для добавления вас в чат...\n"
                            f"⏳ Пожалуйста, подождите, скоро вы будете добавлены в {chat_name}!"
                        )
                    except Exception as user_notify_error:
                        logger.error(f"Ошибка при отправке уведомления пользователю о процессе: {user_notify_error}")
                    
                except Exception as contact_error:
                    logger.error(f"Ошибка при добавлении пользователя в контакты администратора: {contact_error}")
        except Exception as db_error:
            logger.error(f"Ошибка при проверке предыдущей заявки: {db_error}")
        finally:
            session.close()
            
        # Прямое добавление через администратора
        logger.info(f"Попытка прямого добавления пользователя {user_id} в чат {target_chat.id}")
        
        try:
            # Вначале попробуем стандартный метод add_chat_members
            try:
                # Получаем информацию о пользователе (уже получена выше)
                if not 'user_info' in locals():
                    user_info = await admin_client.get_users(user_id)
                    logger.info(f"Получена информация о пользователе: {user_info.first_name} {user_info.last_name or ''}")
                
                # Пробуем добавить
                result = await admin_client.add_chat_members(
                    chat_id=target_chat.id,
                    user_ids=user_id
                )
                logger.info(f"Пользователь {user_id} успешно добавлен через add_chat_members")
                
                # Отправляем сообщение об успешном добавлении
                await bot.send_message(
                    user_id,
                    f"✅ Вы были успешно добавлены в {chat_name}!\n\n"
                    f"Можете открыть чат в своем приложении Telegram."
                )
                
                # Обновляем статус заявки
                session = get_session()
                try:
                    join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                    if join_request:
                        join_request.status = "approved"
                        join_request.approved_by = 0
                        join_request.approved_at = datetime.now()
                        session.commit()
                except Exception as e:
                    logger.error(f"Ошибка при обновлении статуса заявки: {e}")
                finally:
                    session.close()
                
                return True, "Пользователь успешно добавлен в чат"
                
            except Exception as standard_error:
                logger.error(f"Ошибка при стандартном методе добавления: {standard_error}")
                
                # Если стандартный метод не сработал, пробуем через RAW API
                try:
                    # Прямой метод через raw API
                    logger.info(f"Пробуем добавить через raw API")
                    
                    # Получаем peer для чата и пользователя
                    chat_peer = await admin_client.resolve_peer(target_chat.id)
                    
                    try:
                        # Пробуем получить peer пользователя
                        user_peer = await admin_client.resolve_peer(user_id)
                    except Exception as peer_error:
                        logger.error(f"Ошибка получения peer пользователя: {peer_error}")
                        # Создаем InputPeerUser напрямую
                        from pyrogram.raw.types import InputPeerUser
                        user_peer = InputPeerUser(user_id=user_id, access_hash=0)
                    
                    # Вызываем InviteToChannel
                    logger.info(f"Вызов InviteToChannel для пользователя {user_id}")
                    await admin_client.invoke(
                        raw.functions.channels.InviteToChannel(
                            channel=chat_peer,
                            users=[user_peer]
                        )
                    )
                    
                    logger.info(f"Пользователь {user_id} успешно добавлен через raw API")
                    
                    # Отправляем сообщение об успешном добавлении
                await bot.send_message(
                    user_id,
                    f"✅ Вы были успешно добавлены в {chat_name}!\n\n"
                    f"Можете открыть чат в своем приложении Telegram."
                )
                
                # Обновляем статус заявки
                session = get_session()
                try:
                    join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                    if join_request:
                        join_request.status = "approved"
                            join_request.approved_by = 0
                        join_request.approved_at = datetime.now()
                        session.commit()
                except Exception as e:
                    logger.error(f"Ошибка при обновлении статуса заявки: {e}")
                finally:
                    session.close()
                
                    return True, "Пользователь успешно добавлен в чат через raw API"
                    
                except Exception as raw_error:
                    logger.error(f"Ошибка при добавлении через raw API: {raw_error}")
                    
                    # Если raw API не помог, попробуем добавить пользователя в контакты
                    try:
                        # Добавляем пользователя в контакты
                        logger.info(f"Пробуем добавить пользователя {user_id} в контакты")
                        
                        from pyrogram.raw.functions.contacts import ImportContacts
                        from pyrogram.raw.types import InputPhoneContact
                        
                        # Генерируем случайный ID клиента
                        import random
                        client_id = random.randint(0, 2**63 - 1)
                        
                        # Импортируем контакт
                        result = await admin_client.invoke(
                            ImportContacts(
                                contacts=[
                                    InputPhoneContact(
                                        client_id=client_id,
                                        phone=f"+{user_id}",  # Используем ID как номер телефона
                                        first_name=user_info.first_name or "User",
                                        last_name=user_info.last_name or ""
                                    )
                                ]
                            )
                        )
                        logger.info(f"Результат ImportContacts: {result}")
                        
                        # Пробуем добавить пользователя в чат
                        logger.info(f"Повторная попытка добавления после импорта контакта")
                        await admin_client.add_chat_members(
                            chat_id=target_chat.id,
                            user_ids=user_id
                        )
                        
                        logger.info(f"Пользователь {user_id} успешно добавлен после импорта контакта")
                        
                        # Отправляем сообщение об успешном добавлении
            await bot.send_message(
                user_id,
                            f"✅ Вы были успешно добавлены в {chat_name}!\n\n"
                            f"Можете открыть чат в своем приложении Telegram."
                        )
                        
                        # Обновляем статус заявки
                        session = get_session()
                        try:
                            join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                            if join_request:
                                join_request.status = "approved"
                                join_request.approved_by = 0
                                join_request.approved_at = datetime.now()
                                session.commit()
                        except Exception as e:
                            logger.error(f"Ошибка при обновлении статуса заявки: {e}")
                        finally:
                            session.close()
                        
                        return True, "Пользователь успешно добавлен в чат через контакты"
                        
                    except Exception as import_error:
                        logger.error(f"Ошибка при импорте контакта: {import_error}")
                        
                        # Если ничего не помогло, отправляем контакт администратора пользователю
                        try:
                            # Получаем информацию об администраторе
                            admin_info = await admin_client.get_me()
                            logger.info(f"Получена информация об администраторе: {admin_info.first_name} {admin_info.last_name or ''}")
                            
                            # Получаем телефон администратора (если доступен)
                            admin_phone = ""
                            if hasattr(admin_client, '_phone'):
                                admin_phone = admin_client._phone
                                logger.info(f"Телефон администратора: {admin_phone}")
                            
                            # Сначала отправляем информационное сообщение
                            await bot.send_message(
                                user_id,
                                f"🔄 К сожалению, мы не смогли добавить вас автоматически в {chat_name}.\n\n"
                                f"👋 Но не волнуйтесь! Мы нашли решение: добавьте нашего администратора в контакты, "
                                f"и бот сможет вас пригласить!\n\n"
                                f"👇 Сейчас я отправлю вам контакт администратора. Пожалуйста, добавьте его в свои контакты."
                            )
                            
                            # Отправляем контакт администратора
                            await bot.send_contact(
                                user_id,
                                phone_number=admin_phone or f"+{admin_info.id}",
                                first_name=admin_info.first_name,
                                last_name=admin_info.last_name or ""
                            )
                            
                            # Отправляем сообщение с инструкцией
                            await bot.send_message(
                                user_id,
                                f"✅ Теперь:\n\n"
                                f"1️⃣ Добавьте этот контакт в свой список контактов\n"
                                f"2️⃣ После добавления контакта вернитесь в этого бота и нажмите 'Хочу в чат' еще раз\n\n"
                                f"🎯 Это позволит боту успешно добавить вас в чат!"
                            )
                            
                            # Уведомляем администратора
                            try:
                                await admin_client.send_message(
                                    admin_info.id,
                                    f"👋 Привет! Пользователь {user_info.first_name} {user_info.last_name or ''} (ID: {user_id}) "
                                    f"получил ваш контакт для автоматического добавления в чат.\n\n"
                                    f"💬 Пользователю рекомендовано добавить вас в контакты и повторить попытку вступления в чат через бота. "
                                    f"Дальнейшие действия с вашей стороны не требуются."
                                )
                            except Exception as admin_notify_error:
                                logger.error(f"Ошибка при отправке уведомления администратору: {admin_notify_error}")
                            
                            # Обновляем статус заявки
                            session = get_session()
                            try:
                                join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                                if join_request:
                                    join_request.status = "contact_sent"
                                    session.commit()
                            except Exception as db_err:
                                logger.error(f"Ошибка при обновлении статуса заявки: {db_err}")
                            finally:
                                session.close()
                            
                            # Отправляем уведомление администраторам
                            admin_text = (
                                f"ℹ️ Пользователю отправлен контакт администратора:\n\n"
                                f"👤 {user_info.first_name} {user_info.last_name or ''} (@{user_info.username or 'нет'})\n"
                                f"📱 ID: {user_id}\n\n"
                                f"❗ Причина: не удалось добавить автоматически\n"
                                f"👤 Отправлен контакт: {admin_info.first_name} {admin_info.last_name or ''}\n"
                                f"⏰ {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}\n"
                                f"🔄 Пользователю рекомендовано добавить контакт и повторить попытку вступления через бота"
                            )
                            
                            for admin_id in ADMIN_IDS:
                                try:
                                    await bot.send_message(admin_id, admin_text)
                                except Exception as admin_err:
                                    logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {admin_err}")
                            
                            return False, "contact_sent"
                            
                        except Exception as contact_send_error:
                            logger.error(f"Ошибка при отправке контакта администратора: {contact_send_error}")
                            
                            # Только если не удалось отправить контакт, отправляем инструкции по настройкам приватности
                            await bot.send_message(
                                user_id,
                                f"🔒 К сожалению, мы не смогли добавить вас в чат. Возможно, дело в настройках Telegram.\n\n"
                                f"🔍 Чтобы решить эту проблему, попробуйте изменить настройки конфиденциальности:\n\n"
                f"👉 Откройте настройки Telegram\n"
                f"👉 Перейдите в раздел 'Конфиденциальность'\n" 
                f"👉 Выберите 'Группы и каналы'\n"
                f"👉 Для опции 'Кто может добавить меня в группы' выберите 'Все'\n\n"
                                f"📱 Мы приложили инструкции с картинками:"
            )
            
            # Отправляем инструкции с картинками
            try:
                # Отправляем изображения с инструкциями, если они доступны
                await bot.send_photo(
                    user_id,
                    "screen/1.jpg",
                    caption="1. Откройте настройки и выберите 'Конфиденциальность'"
                )
                
                await bot.send_photo(
                    user_id,
                    "screen/2.jpg",
                    caption="2. Выберите 'Группы и каналы'"
                )
                
                await bot.send_photo(
                    user_id,
                    "screen/3.jpg",
                    caption="3. Установите 'Кто может добавить меня в группы' на 'Все'"
                )
                
                await bot.send_message(
                    user_id,
                    "🎉 После изменения настроек вернитесь сюда и повторите попытку! Мы сможем добавить вас автоматически."
                )
                
            except Exception as photo_err:
                logger.error(f"Ошибка при отправке инструкций с изображениями: {photo_err}")
            
            # Обновляем статус заявки
            session = get_session()
            try:
                join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                if join_request:
                    join_request.status = "link_sent"
                    session.commit()
            except Exception as db_err:
                logger.error(f"Ошибка при обновлении статуса заявки: {db_err}")
            finally:
                session.close()
            
            return False, "UserPrivacyRestricted: Пользователь не может быть добавлен из-за настроек приватности"
            
                raise standard_error
                
        except UserAlreadyParticipant:
            logger.info(f"Пользователь {user_id} уже состоит в чате")
            
            await bot.send_message(
                user_id,
                f"ℹ️ Вы уже состоите в этом чате. Откройте его в своем приложении Telegram."
            )
            
            # Обновляем статус заявки
            session = get_session()
            try:
                join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                if join_request:
                    join_request.status = "approved"
                    session.commit()
            except Exception as e:
                logger.error(f"Ошибка при обновлении статуса заявки: {e}")
            finally:
                session.close()
                
            return True, "Пользователь уже состоит в чате"
            
        except PeerFlood:
            logger.error(f"Слишком много запросов на добавление, лимит превышен")
            
            # Деактивируем текущий аккаунт администратора
            session = get_session()
            try:
                if hasattr(admin_client, '_phone'):
                    admin_account = session.query(AdminAccount).filter_by(phone=admin_client._phone).first()
                    if admin_account:
                        admin_account.active = False
                        session.commit()
                        logger.warning(f"Аккаунт {admin_account.phone} деактивирован из-за лимита добавлений")
                else:
                    logger.warning("Не удалось определить телефон аккаунта для деактивации")
            except Exception as e:
                logger.error(f"Ошибка при деактивации аккаунта: {e}")
            finally:
                session.close()
            
            # Пробуем переключиться на другой аккаунт
            logger.info("Пробуем переключиться на другой аккаунт администратора")
            switched = await switch_to_next_admin()
            
            if switched:
                logger.info("Успешно переключились на другой аккаунт, пробуем добавить пользователя снова")
                
                # Получаем новый клиент
                new_admin_client = await get_admin_client()
                if new_admin_client:
                    logger.info("Получен новый клиент администратора")
                    
                    # Пробуем добавить пользователя еще раз
                    try:
                            # Добавляем пользователя
                        await new_admin_client.add_chat_members(
                                chat_id=target_chat.id,
                                user_ids=user_id
                            )
                                logger.info(f"Пользователь {user_id} успешно добавлен в чат после переключения аккаунта")
                                
                                # Отправляем уведомление об успешном добавлении
                                await bot.send_message(
                                    user_id,
                                    f"✅ Вы были успешно добавлены в {chat_name}!\n\n"
                                    f"Можете открыть чат в своем приложении Telegram."
                                )
                                
                                # Обновляем статус заявки
                                session = get_session()
                                try:
                                    join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                                    if join_request:
                                        join_request.status = "approved"
                                        join_request.approved_by = 0
                                        join_request.approved_at = datetime.now()
                                        session.commit()
                                except Exception as e:
                                    logger.error(f"Ошибка при обновлении статуса заявки: {e}")
                                finally:
                                    session.close()
                                
                                return True, "Пользователь успешно добавлен в чат после переключения аккаунта"
                    except Exception as retry_error:
                        logger.error(f"Ошибка при повторной попытке добавления после переключения аккаунта: {retry_error}")
            
            return False, "Достигнут лимит добавлений. Попробуйте позже."
        
    except Exception as e:
        logger.error(f"Основная ошибка при добавлении пользователя: {type(e).__name__}: {str(e)}")
        return False, f"Ошибка при добавлении пользователя: {str(e)}"

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    """
    Обработка команды /start
    """
    user_id = message.from_user.id
    session = get_session()
    
    try:
        # Проверяем, зарегистрирован ли пользователь
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            new_user = User(
                user_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            session.add(new_user)
            session.commit()
        
        # Получаем настраиваемые тексты из базы данных (с значениями по умолчанию)
        button_join_text = get_setting("button_join_text", "🚀 Вступить в чат!")
        button_info_text = get_setting("button_info_text", "🔍 Что тут у вас?")
        button_support_text = get_setting("button_support_text", "🆘 Нужна помощь")
        welcome_message = get_setting("welcome_message", f"👋 Хэй, {message.from_user.first_name}! Рады видеть тебя! 😎\n\nЯ твой личный помощник для вступления в наши крутые чаты. Что будем делать?")
        
        # Используем полученный текст для приветствия
        welcome_text = welcome_message
        
        # Создаем клавиатуру с новыми кнопками
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton(button_join_text, callback_data="show_chats")],
            [types.InlineKeyboardButton(button_info_text, callback_data="show_info")],
            [types.InlineKeyboardButton(button_support_text, callback_data="support")]
        ])
        
        await message.reply(welcome_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")
        await message.reply("Ой! Что-то пошло не так 😅 Попробуй еще раз чуть позже, ладно?")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^show_chats$"))
async def show_chats_callback(client, callback_query):
    """
    Показывает меню выбора чатов
    """
    # Получаем настраиваемый текст для заголовка
    chat_select_text = get_setting("chat_select_text", "🤔 В какой классный чат хочешь попасть? Выбирай!")
    
    # Создаем клавиатуру для выбора чатов
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Чат #1 🔥", callback_data="select_chat_1")],
        [types.InlineKeyboardButton("Чат #2 ✨", callback_data="select_chat_2")],
        [types.InlineKeyboardButton("↩️ Назад в меню", callback_data="back_to_menu")]
    ])
    
    await callback_query.edit_message_text(chat_select_text, reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^show_info$"))
async def show_info_callback(client, callback_query):
    """
    Показывает информацию о чатах
    """
    # Получаем настраиваемый текст информации
    info_text = get_setting("info_text", 
        "ℹ️ *Наши супер-чаты* 🌟\n\n"
        "У нас есть два классных чата для общения:\n\n"
        "*Чат #1*: 🔥 Здесь кипит общение на разные темы! Новости, обсуждения и просто душевные беседы.\n\n"
        "*Чат #2*: ✨ Это особый чат для профессионалов и тех, кто хочет ими стать!\n\n"
        "Готов присоединиться? Жми кнопку ниже! 👇"
    )
    
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🚀 Хочу в чат!", callback_data="show_chats")],
        [types.InlineKeyboardButton("↩️ Назад в меню", callback_data="back_to_menu")]
    ])
    
    await callback_query.edit_message_text(info_text, reply_markup=keyboard, parse_mode=enums.ParseMode.MARKDOWN)

@bot.on_callback_query(filters.regex(r"^select_chat_(\d+)$"))
async def select_chat_callback(client, callback_query):
    """
    Обработка выбора чата
    """
    user_id = callback_query.from_user.id
    chat_num = callback_query.data.split("_")[-1]
    chat_id = CHAT_ID_1 if chat_num == "1" else CHAT_ID_2
    
    if chat_id == 0:
        await callback_query.answer("Ой! Этот чат сейчас отдыхает 😴 Выбери другой!")
        return
    
    session = get_session()
    try:
        # Проверяем, не в черном ли списке пользователь
        user = session.query(User).filter_by(user_id=user_id).first()
        if user and user.is_blacklisted:
            await callback_query.answer("Упс! К сожалению, ты не можешь быть добавлен в чат 😞")
            return
        
        # Проверяем ограничение на количество заявок
        rate_limited, current_count = check_rate_limit(user_id, limit=5, period_minutes=1)
        if rate_limited:
            # Если пользователь превысил лимит, блокируем его и выводим сообщение
            block_user_rate_limit(user_id)
            logger.warning(f"Пользователь {user_id} превысил лимит заявок (текущее количество: {current_count}) и был заблокирован")
            
            # Информируем пользователя о блокировке
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад в меню", callback_data="back_to_menu")]
            ])
            
            await callback_query.edit_message_text(
                "⚠️ Вау, полегче! Ты отправил слишком много заявок (5 за минуту).\n\n"
                "⛔ Мы временно заблокировали эту функцию для тебя. Сделай глубокий вдох, выпей чашечку чая ☕\n\n"
                "⏳ Попробуй снова чуть позже или свяжись с нашей поддержкой, если это какая-то ошибка!",
                reply_markup=keyboard
            )
            
            # Уведомляем администраторов о блокировке
            user_info = await client.get_users(user_id)
            admin_text = (
                f"🚫 Пользователь заблокирован за превышение лимита заявок:\n\n"
                f"👤 <b>Пользователь:</b>\n"
                f"ID: <code>{user_id}</code>\n"
                f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
                f"Username: @{user_info.username or 'отсутствует'}\n\n"
                f"⚠️ <b>Превышен лимит</b>: 5 заявок в минуту\n"
                f"📅 Дата блокировки: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            )
            
            # Добавляем кнопку разблокировки для админов
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock_rate_limit_{user_id}")]
            ])
            
            for admin_id in ADMIN_IDS:
                try:
                    await client.send_message(
                        admin_id,
                        admin_text,
                        reply_markup=keyboard,
                        parse_mode=enums.ParseMode.HTML
                    )
                    logger.info(f"Администратору {admin_id} отправлено уведомление о блокировке пользователя {user_id}")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
            
            return
        
        # Создаем заявку на добавление
        join_request = JoinRequest(
            user_id=user_id,
            chat_id=chat_id,
            status="pending"
        )
        session.add(join_request)
        session.commit()
        
        # Сообщаем пользователю, что его заявка обрабатывается
        await callback_query.edit_message_text(
            "⏳ Обрабатываем твою заявку...\n\n"
            "Секундочку! Готовим для тебя приглашение в чат... 🧙‍♂️✨"
        )
        
        # Добавляем пользователя
        success, message = await add_user_to_chat(user_id, chat_id)
        logger.info(f"Результат add_user_to_chat: success={success}, message={message}")
        
        if success:
            # Если пользователь успешно добавлен, не нужно повторно отправлять сообщение
            # так как оно уже отправлено в функции add_user_to_chat
            # Только отмечаем в БД, что пользователь добавлен
            join_request.status = "approved"
            user.chat_joined = chat_id
            session.commit()
            
            return True, "Пользователь успешно добавлен в чат"
        else:
            # Если не получилось добавить, проверяем тип ошибки
            logger.info(f"Не удалось добавить пользователя. Сообщение: {message}")
            
            # Дополнительное логирование для диагностики
            logger.info(f"Проверка на ошибку приватности. Результаты проверок:")
            logger.info(f"- 'приватности' in message.lower(): {('приватности' in message.lower())}")
            logger.info(f"- 'privacy' in message.lower(): {('privacy' in message.lower())}")
            logger.info(f"- 'UserPrivacyRestricted' in message: {('UserPrivacyRestricted' in message)}")
            logger.info(f"- message.startswith('UserPrivacyRestricted:'): {message.startswith('UserPrivacyRestricted:')}")
            
            # Проверяем сообщение об ошибке на наличие ключевых слов о приватности
            if "приватности" in message.lower() or "privacy" in message.lower() or "UserPrivacyRestricted" in message or message.startswith("UserPrivacyRestricted:"):
                # В случае настроек приватности, отправляем инструкции и ссылку напрямую
                join_request.status = "link_sent"
                session.commit()
                
                logger.info(f"Отправляю пользователю инструкции по настройкам приватности")
                
                # Информируем пользователя о проблеме в интерфейсе бота
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")]
                ])
                
                # Получаем настраиваемый текст сообщения о настройках приватности
                privacy_message = get_setting("privacy_message",
                    "🔒 Ой-ой! Твои настройки приватности не дают нам добавить тебя автоматически. 😢\n\n"
                    "✉️ Но не волнуйся! Мы отправили тебе подробные инструкции по изменению настроек в личном сообщении.\n\n"
                    "👆 Проверь сообщения и просто следуй инструкциям! Через минуту ты будешь в чате! 🚀"
                )
                
                await callback_query.edit_message_text(
                    privacy_message,
                    reply_markup=keyboard,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                
                # Отправляем администраторам информацию о пользователе
                user_info = await client.get_users(user_id)
                admin_text = (
                    f"⚠️ Новая заявка (требуется изменение приватности):\n\n"
                    f"📋 Информация о пользователе:\n"
                    f"ID: {user_id}\n"
                    f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
                    f"Username: @{user_info.username or 'отсутствует'}\n\n"
                    f"🔒 Причина: Ограничения приватности пользователя\n"
                    f"📧 Действие: Отправлены инструкции по изменению настроек\n"
                    f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                )
                
                for admin_id in ADMIN_IDS:
                    try:
                        await client.send_message(admin_id, admin_text)
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
            elif message == "auto_add_disabled":
                # Если автодобавление отключено, оповещаем пользователя о ручной проверке
                logger.info(f"⚠️ Режим автодобавления отключен. Заявка пользователя {user_id} переведена в статус ручной проверки")
                
                join_request.status = "manual_check"
                session.commit()
                logger.info(f"Обновлен статус заявки на 'manual_check' для пользователя {user_id}")
                
                # Информируем пользователя о ручной проверке
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")]
                ])
                
                # Получаем настраиваемый текст сообщения о ручной проверке
                manual_check_message = get_setting("manual_check_message",
                    "⏳ Твоя заявка принята! Сейчас она в руках наших админов! 👨‍💻\n\n"
                    "📋 В данный момент включен режим ручного добавления пользователей.\n"
                    "⌛ Мы добавим тебя в чат, как только один из наших админов одобрит заявку. Обычно это происходит быстро! 🏎️"
                )
                
                await callback_query.edit_message_text(
                    manual_check_message,
                    reply_markup=keyboard,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                logger.info(f"Отправлено уведомление пользователю {user_id} о ручной проверке")
                
                # Отправляем администраторам информацию о пользователе с кнопками для добавления
                user_info = await client.get_users(user_id)
                
                # Собираем дополнительную информацию о пользователе
                premium_status = "✅" if user_info.is_premium else "❌"
                language_code = user_info.language_code or "неизвестно"
                is_bot = "✅" if user_info.is_bot else "❌"
                is_fake = "✅" if hasattr(user_info, "is_fake") and user_info.is_fake else "❌"
                is_scam = "✅" if hasattr(user_info, "is_scam") and user_info.is_scam else "❌"
                
                admin_text = (
                    f"📝 Новая заявка (ручное добавление):\n\n"
                    f"👤 <b>Пользователь:</b>\n"
                    f"ID: <code>{user_id}</code>\n"
                    f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
                    f"Username: @{user_info.username or 'отсутствует'}\n\n"
                    f"📊 <b>Дополнительная информация:</b>\n"
                    f"Язык: {language_code}\n"
                    f"Premium: {premium_status}\n"
                    f"Бот: {is_bot}\n"
                    f"Фейк: {is_fake}\n"
                    f"Скам: {is_scam}\n\n"
                    f"⏰ Дата заявки: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                )
                
                # Создаем кнопки для добавления пользователя
                chat_name = "Чат #1" if chat_id == CHAT_ID_1 else "Чат #2"
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton(f"✅ Добавить в {chat_name}", callback_data=f"manual_add_{user_id}_{chat_id}")],
                    [types.InlineKeyboardButton("❌ Отклонить", callback_data=f"manual_reject_{user_id}_{chat_id}")]
                ])
                
                # Логирование созданных callback_data
                logger.info(f"Создана кнопка добавления с callback_data: manual_add_{user_id}_{chat_id}")
                logger.info(f"Создана кнопка отклонения с callback_data: manual_reject_{user_id}_{chat_id}")
                
                # Отправляем уведомление всем администраторам
                for admin_id in ADMIN_IDS:
                    try:
                        # Отправляем фото профиля, если оно есть
                        try:
                            profile_photos = await client.get_profile_photos(user_id, limit=1)
                            if profile_photos.total_count > 0:
                                await client.send_photo(
                                    admin_id,
                                    profile_photos.photos[0][0].file_id,
                                    caption=admin_text,
                                    reply_markup=keyboard,
                                    parse_mode=enums.ParseMode.HTML
                                )
                                logger.info(f"Администратору {admin_id} отправлено уведомление с фото о заявке пользователя {user_id}")
                            else:
                                await client.send_message(
                                    admin_id,
                                    admin_text,
                                    reply_markup=keyboard,
                                    parse_mode=enums.ParseMode.HTML
                                )
                                logger.info(f"Администратору {admin_id} отправлено текстовое уведомление о заявке пользователя {user_id}")
                        except Exception as photo_err:
                            logger.error(f"Ошибка при отправке фото профиля: {photo_err}")
                            await client.send_message(
                                admin_id,
                                admin_text,
                                reply_markup=keyboard,
                                parse_mode=enums.ParseMode.HTML
                            )
                            logger.info(f"Администратору {admin_id} отправлено текстовое уведомление о заявке пользователя {user_id}")
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
            else:
                # Для других ошибок обновляем статус и показываем сообщение об ошибке
                join_request.status = "rejected"
                session.commit()
                
                # Получаем настраиваемый текст сообщения об ошибке
                error_message_template = get_setting("error_message_template", 
                    "❌ Ой! Что-то пошло не так: {error}\n\n"
                    "Попробуй еще раз чуть позже или напиши нам в поддержку - мы обязательно поможем! 💪"
                )
                
                # Подставляем конкретную ошибку в шаблон
                error_text = error_message_template.replace("{error}", message)
                
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")],
                    [types.InlineKeyboardButton("📞 Поддержка", callback_data="support")]
                ])
                
                await callback_query.edit_message_text(
                    error_text, 
                    reply_markup=keyboard,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                
                # Уведомляем администраторов об ошибке
                admin_error_text = f"❌ Ошибка при добавлении пользователя:\n\n"
                admin_error_text += f"ID: {user_id}\n"
                admin_error_text += f"Ошибка: {message}\n"
                admin_error_text += f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                
                for admin_id in ADMIN_IDS:
                    try:
                        await client.send_message(admin_id, admin_error_text)
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при выборе чата: {e}")
        await callback_query.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^back_to_menu$"))
async def back_to_menu_callback(client, callback_query):
    """
    Возврат в главное меню
    """
    # Получаем настраиваемые тексты из базы данных (с значениями по умолчанию)
    button_join_text = get_setting("button_join_text", "🚀 Вступить в чат!")
    button_info_text = get_setting("button_info_text", "🔍 Что тут у вас?")
    button_support_text = get_setting("button_support_text", "🆘 Нужна помощь")
    welcome_message = get_setting("welcome_message", "👋 Хэй! Что будем делать дальше? 😎")
    
    # Создаем клавиатуру с новыми кнопками
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(button_join_text, callback_data="show_chats")],
        [types.InlineKeyboardButton(button_info_text, callback_data="show_info")],
        [types.InlineKeyboardButton(button_support_text, callback_data="support")]
    ])
    
    await callback_query.edit_message_text(welcome_message, reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^support$"))
async def support_callback(client, callback_query):
    """
    Обработка запроса в поддержку
    """
    # Получаем настраиваемый текст поддержки
    support_text = get_setting("support_text", 
        "🆘 *Нужна помощь?*\n\n"
        "Не переживай! Наша команда поддержки всегда готова помочь тебе! 💪\n\n"
        "Просто напиши личное сообщение нашему администратору, и мы постараемся ответить как можно быстрее! 🏎️"
    )
    
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("↩️ Назад в меню", callback_data="back_to_menu")]
    ])
    
    await callback_query.edit_message_text(support_text, reply_markup=keyboard, parse_mode=enums.ParseMode.MARKDOWN)

# Команды администратора
@bot.on_message(filters.command("admin") & filters.private & filters.user(ADMIN_IDS))
async def admin_command(client, message):
    """
    Панель администратора
    """
    admin_text = "🛠️ Панель администратора 🔧\n\n"
    admin_text += "Привет, супер-админ! 😎 Что будем делать сегодня?"
    
    # Получаем текущий статус автодобавления
    auto_add_enabled = get_setting("auto_add_enabled", "true").lower() == "true"
    auto_add_button_text = "🔴 Выключить автодобавление" if auto_add_enabled else "🟢 Включить автодобавление"
    auto_add_callback = "toggle_auto_add_off" if auto_add_enabled else "toggle_auto_add_on"
    
    # Создаем инлайн-клавиатуру для админа
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
        [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
        [types.InlineKeyboardButton(auto_add_button_text, callback_data=auto_add_callback)],
        [types.InlineKeyboardButton("✏️ Настройка текста интерфейса", callback_data="ui_text_settings")],
        [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
        [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
        [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
        [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
    ])
    
    await message.reply(admin_text, reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^admin_users$"))
async def admin_users_callback(client, callback_query):
    """
    Список пользователей с полной информацией
    """
    session = get_session()
    try:
        users = session.query(User).order_by(User.registration_date.desc()).limit(10).all()
        
        if not users:
            await callback_query.edit_message_text("Список пользователей пуст.")
            return
        
        users_text = "👥 Список последних пользователей:\n\n"
        
        for i, user_db in enumerate(users):
            try:
                # Получаем информацию о пользователе через API
                user_info = await client.get_users(user_db.user_id)
                
                # Базовая информация
                username = f"@{user_db.username}" if user_db.username else "нет"
                status = "🚫 Заблокирован" if user_db.is_blacklisted else "✅ Активен"
                chat = f"Чат #{1 if user_db.chat_joined == CHAT_ID_1 else 2}" if user_db.chat_joined else "Не в чате"
                
                # Дополнительная информация
                premium_status = "✅" if hasattr(user_info, "is_premium") and user_info.is_premium else "❌"
                language_code = user_info.language_code or "неизвестно"
                is_bot = "✅" if hasattr(user_info, "is_bot") and user_info.is_bot else "❌"
                is_fake = "✅" if hasattr(user_info, "is_fake") and user_info.is_fake else "❌"
                is_scam = "✅" if hasattr(user_info, "is_scam") and user_info.is_scam else "❌"
                
                # Получаем заявки пользователя
                join_requests = session.query(JoinRequest).filter_by(user_id=user_db.user_id).order_by(JoinRequest.created_at.desc()).limit(3).all()
                
                # Вся информация в одном сообщении
                users_text += f"{i+1}. <b>{user_db.first_name} {user_db.last_name or ''}</b> ({username})\n"
                users_text += f"ID: <code>{user_db.user_id}</code> | {status} | {chat}\n"
                users_text += f"Регистрация: {user_db.registration_date.strftime('%d.%m.%Y %H:%M')}\n"
                users_text += f"Premium: {premium_status} | Язык: {language_code}\n"
                users_text += f"Бот: {is_bot} | Фейк: {is_fake} | Скам: {is_scam}\n"
                
                if join_requests:
                    users_text += "История заявок:\n"
                    for req in join_requests:
                        chat_name = "Чат #1" if req.chat_id == CHAT_ID_1 else "Чат #2"
                        
                        if req.status == "approved":
                            status_emoji = "✅"
                            status_text = "Одобрена"
                        elif req.status == "rejected":
                            status_emoji = "❌"
                            status_text = "Отклонена"
                        elif req.status == "link_sent":
                            status_emoji = "🔗"
                            status_text = "Отправлена инструкция"
                        elif req.status == "manual_check":
                            status_emoji = "👨‍💼"
                            status_text = "Ожидает ручного добавления"
                        elif req.status == "pending":
                            status_emoji = "⏳"
                            status_text = "Обрабатывается"
                        else:
                            status_emoji = "❓"
                            status_text = req.status
                        
                        users_text += f"- {status_emoji} {chat_name}: {status_text} ({req.created_at.strftime('%d.%m.%Y %H:%M')})\n"
                
                # Убираем команды блокировки/разблокировки
                users_text += "\n"
                
            except Exception as user_err:
                logger.error(f"Ошибка при получении информации о пользователе {user_db.user_id}: {user_err}")
                users_text += f"{i+1}. <b>{user_db.first_name} {user_db.last_name or ''}</b>\n"
                users_text += f"ID: <code>{user_db.user_id}</code> | Ошибка получения данных\n\n"
        
        # Добавляем кнопку назад
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        # Разбиваем текст, если слишком длинный
        max_length = 4000  # Примерный максимум сообщения в Telegram
        if len(users_text) > max_length:
            chunks = [users_text[i:i+max_length] for i in range(0, len(users_text), max_length)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await callback_query.edit_message_text(chunk, reply_markup=None, parse_mode=enums.ParseMode.HTML)
                else:
                    await client.send_message(
                        callback_query.from_user.id, 
                        chunk, 
                        reply_markup=keyboard if i == len(chunks)-1 else None,
                        parse_mode=enums.ParseMode.HTML
                    )
        else:
            await callback_query.edit_message_text(users_text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка при получении списка пользователей: {e}")
        await callback_query.edit_message_text(f"Произошла ошибка при получении списка пользователей: {str(e)}")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^admin_active_requests$"))
async def admin_active_requests_callback(client, callback_query):
    """
    Просмотр активных заявок (ожидающих проверки)
    """
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("У тебя не хватает прав для этого 😅")
        return
    
    try:
        session = get_session()
        try:
            # Получаем все активные заявки
            active_requests = session.query(JoinRequest).filter_by(status="pending").all()
            
            if not active_requests:
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Назад в меню админа", callback_data="back_to_admin")]
                ])
                
                await callback_query.edit_message_text(
                    "📋 Активных заявок нет! Можно выдохнуть и выпить кофе! ☕",
                    reply_markup=keyboard
                )
                return
            
            # Создаем сообщение с активными заявками
            active_text = f"📋 Найдено {len(active_requests)} активных заявок:\n\n"
            
            for i, request in enumerate(active_requests, 1):
                user = session.query(User).filter_by(user_id=request.user_id).first()
                chat_name = "Чат #1" if request.chat_id == CHAT_ID_1 else "Чат #2"
                
                user_name = f"{user.first_name} {user.last_name or ''}" if user else "Неизвестный пользователь"
                username = f"@{user.username}" if user and user.username else "отсутствует"
                
                active_text += f"{i}. <b>Заявка #{request.id}</b>\n"
                active_text += f"👤 Пользователь: {user_name}\n"
                active_text += f"🆔 ID: <code>{request.user_id}</code>\n"
                active_text += f"👤 Username: {username}\n"
                active_text += f"🎯 Чат: {chat_name}\n"
                active_text += f"⏱ Дата создания: {request.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            
            # Добавляем кнопки для действий с заявками
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("👍 Одобрить все", callback_data="approve_all_requests")],
                [types.InlineKeyboardButton("👎 Отклонить все", callback_data="reject_all_requests")],
                [types.InlineKeyboardButton("↩️ Назад в меню админа", callback_data="back_to_admin")]
            ])
            
            # Добавляем кнопки для каждой заявки
            for request in active_requests:
                keyboard.inline_keyboard.append([
                    types.InlineKeyboardButton(f"👤 Открыть #{request.id}", callback_data=f"show_request_{request.id}")
                ])
            
            await callback_query.edit_message_text(
                active_text,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Ошибка при получении активных заявок: {e}")
            await callback_query.answer("Ой! Что-то пошло не так при загрузке заявок 😅")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Ошибка в admin_active_requests_callback: {e}")
        await callback_query.answer("Произошла ошибка. Попробуй снова!")

@bot.on_callback_query(filters.regex(r"^admin_requests_history$"))
async def admin_requests_history_callback(client, callback_query):
    """
    Список истории заявок через клавиатуру (approved, rejected, link_sent)
    """
    session = get_session()
    try:
        # Получаем историю заявок (approved, rejected, link_sent)
        requests = session.query(JoinRequest).filter(
            JoinRequest.status.in_(["approved", "rejected", "link_sent"])
        ).order_by(JoinRequest.created_at.desc()).limit(20).all()
        
        if not requests:
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
            ])
            await callback_query.edit_message_text("История заявок пуста.", reply_markup=keyboard)
            return
        
        requests_text = "📚 История заявок (обработанные):\n\n"
        
        for req in requests:
            user = session.query(User).filter_by(user_id=req.user_id).first()
            username = f"@{user.username}" if user and user.username else "нет"
            name = f"{user.first_name} {user.last_name or ''}" if user else "Неизвестный пользователь"
            
            chat_name = "Чат #1" if req.chat_id == CHAT_ID_1 else "Чат #2"
            
            if req.status == "approved":
                status_emoji = "✅"
                status_text = "Одобрена"
            elif req.status == "rejected":
                status_emoji = "❌"
                status_text = "Отклонена"
            elif req.status == "link_sent":
                status_emoji = "🔗"
                status_text = "Отправлена инструкция"
            else:
                status_emoji = "❓"
                status_text = req.status
            
            requests_text += f"ID: {req.user_id}\n"
            requests_text += f"Имя: {name}\n"
            requests_text += f"Username: {username}\n"
            requests_text += f"Чат: {chat_name}\n"
            requests_text += f"Статус: {status_emoji} {status_text}\n"
            requests_text += f"Дата: {req.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            
            # Добавляем информацию о том, кто и когда обработал заявку
            if req.approved_by is not None and req.approved_at is not None:
                if req.approved_by == 0:
                    admin_name = "Система (автоматически)"
                else:
                    try:
                        admin_info = await client.get_users(req.approved_by)
                        admin_name = f"{admin_info.first_name} {admin_info.last_name or ''} (@{admin_info.username or 'нет'})"
                    except Exception:
                        admin_name = f"Администратор ID:{req.approved_by}"
                
                requests_text += f"Обработана: {admin_name}\n"
                requests_text += f"Дата обработки: {req.approved_at.strftime('%d.%m.%Y %H:%M')}\n"
            
            requests_text += "\n"
        
        # Добавляем кнопку назад
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(requests_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении истории заявок: {e}")
        await callback_query.edit_message_text("Произошла ошибка при получении истории заявок.")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^admin_block$"))
async def admin_block_callback(client, callback_query):
    """
    Запрос ID для блокировки пользователя
    """
    await callback_query.edit_message_text(
        "🔒 Введите ID пользователя, которого хотите заблокировать.\n\n"
        "Отправьте сообщение в формате: /block ID",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
    )

@bot.on_callback_query(filters.regex(r"^admin_unblock$"))
async def admin_unblock_callback(client, callback_query):
    """
    Запрос ID для разблокировки пользователя
    """
    await callback_query.edit_message_text(
        "🔓 Введите ID пользователя, которого хотите разблокировать.\n\n"
        "Отправьте сообщение в формате: /unblock ID",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
    )

@bot.on_callback_query(filters.regex(r"^admin_add_account$"))
async def admin_add_account_callback(client, callback_query):
    """
    Запуск процесса добавления нового аккаунта администратора
    """
    # Используем состояние для хранения временных данных
    admin_id = callback_query.from_user.id
    
    # Сохраняем состояние "ожидание телефона"
    set_setting(f"waiting_phone_{admin_id}", "true")
    
    # Отправляем инструкцию
    await callback_query.edit_message_text(
        "📱 Добавление нового аккаунта администратора\n\n"
        "Пожалуйста, отправьте номер телефона в международном формате:\n"
        "Например: +79001234567\n\n"
        "❗ Важно: Будет создана сессия Telegram для этого аккаунта. Убедитесь, что у вас есть доступ к этому номеру для получения кода.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Отмена", callback_data="back_to_admin")]
        ])
    )

@bot.on_callback_query(filters.regex(r"^admin_remove_account$"))
async def admin_remove_account_callback(client, callback_query):
    """
    Запрос номера для удаления аккаунта
    """
    await callback_query.edit_message_text(
        "➖ Введите номер телефона аккаунта, который хотите удалить.\n\n"
        "Отправьте сообщение в формате: /remove_admin НОМЕР",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
    )

@bot.on_callback_query(filters.regex(r"^back_to_admin$"))
async def back_to_admin_callback(client, callback_query):
    """
    Возврат в панель администратора
    """
    try:
        logger.info(f"Вызвана функция back_to_admin_callback, callback_data: {callback_query.data}")
        await callback_query.answer("Возврат в панель администратора...")
        
        user_id = callback_query.from_user.id
        
        # Проверяем и сбрасываем все состояния пользователя
        waiting_phone = get_setting(f"waiting_phone_{user_id}", "false")
        waiting_code = get_setting(f"waiting_code_{user_id}", "false")
        
        if waiting_phone.lower() == "true" or waiting_code.lower() == "true":
            # Сбрасываем состояния
            set_setting(f"waiting_phone_{user_id}", "false")
            set_setting(f"waiting_code_{user_id}", "false")
            set_setting(f"temp_phone_{user_id}", "")
            set_setting(f"temp_code_hash_{user_id}", "")
            
            logger.info(f"Администратор {user_id} отменил добавление аккаунта")
        
        admin_text = "🛠️ Панель администратора 🔧\n\n"
        admin_text += "Привет, супер-админ! 😎 Что будем делать сегодня?"
        
        # Получаем текущий статус автодобавления
        auto_add_enabled = get_setting("auto_add_enabled", "true").lower() == "true"
        auto_add_button_text = "🔴 Выключить автодобавление" if auto_add_enabled else "🟢 Включить автодобавление"
        auto_add_callback = "toggle_auto_add_off" if auto_add_enabled else "toggle_auto_add_on"
        
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
            [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
            [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
            [types.InlineKeyboardButton(auto_add_button_text, callback_data=auto_add_callback)],
            [types.InlineKeyboardButton("✏️ Настройка текста интерфейса", callback_data="ui_text_settings")],
            [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
            [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
            [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
            [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
        ])
        
        try:
            await callback_query.edit_message_text(admin_text, reply_markup=keyboard)
            logger.info("Успешный возврат в панель администратора")
        except errors.MessageNotModified:
            logger.warning("Сообщение не было изменено (уже содержит панель администратора)")
            await callback_query.answer("Панель администратора")
        except Exception as edit_error:
            logger.error(f"Ошибка при редактировании сообщения: {edit_error}")
            # В случае ошибки редактирования, пробуем отправить новое сообщение
            try:
                await callback_query.message.delete()
            except:
                pass
                
            await client.send_message(
                callback_query.from_user.id,
                admin_text,
                reply_markup=keyboard
            )
            logger.info("Отправлено новое сообщение с панелью администратора")
    
    except Exception as e:
        logger.error(f"Ошибка при возврате в панель администратора: {e}")
        try:
            await callback_query.answer("Произошла ошибка. Используй команду /admin")
        except:
            pass

@bot.on_callback_query(filters.regex(r"^toggle_auto_add_on$"))
async def toggle_auto_add_on_callback(client, callback_query):
    """
    Включение автоматического добавления пользователей
    """
    try:
        # Устанавливаем флаг в значение true
        old_value = get_setting("auto_add_enabled", "true")
        logger.info(f"Текущее значение auto_add_enabled перед включением: {old_value}")
        
        # Принудительно устанавливаем новое значение
        set_setting("auto_add_enabled", "true")
        logger.info(f"Администратор {callback_query.from_user.id} включил автоматическое добавление")
        
        # Проверяем, что значение успешно изменено
        new_value = get_setting("auto_add_enabled", "true")
        logger.info(f"Новое значение auto_add_enabled после включения: {new_value}")
        
        # Уведомляем администратора
        await callback_query.answer("✅ Автоматическое добавление пользователей включено")
        
        # Обновляем меню настроек
        await update_settings_menu(client, callback_query)
        
        # Уведомляем других администраторов
        actor = callback_query.from_user
        notification = f"ℹ️ Администратор {actor.first_name} (@{actor.username or 'нет'}) включил автоматическое добавление пользователей."
        
        for admin_id in ADMIN_IDS:
            if admin_id != callback_query.from_user.id:  # Не отправляем уведомление тому, кто включил
                try:
                    await client.send_message(admin_id, notification)
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
    
    except Exception as e:
        logger.error(f"Ошибка при включении автодобавления: {e}")
        await callback_query.answer("❌ Произошла ошибка при включении автодобавления")

@bot.on_callback_query(filters.regex(r"^toggle_auto_add_off$"))
async def toggle_auto_add_off_callback(client, callback_query):
    """
    Отключение автоматического добавления пользователей
    """
    try:
        # Устанавливаем флаг в значение false
        old_value = get_setting("auto_add_enabled", "true")
        logger.info(f"Текущее значение auto_add_enabled перед отключением: {old_value}")
        
        # Принудительно устанавливаем новое значение
        set_setting("auto_add_enabled", "false")
        logger.info(f"Администратор {callback_query.from_user.id} отключил автоматическое добавление")
        
        # Проверяем, что значение успешно изменено
        new_value = get_setting("auto_add_enabled", "false")
        logger.info(f"Новое значение auto_add_enabled после отключения: {new_value}")
        
        # Уведомляем администратора
        await callback_query.answer("✅ Автоматическое добавление пользователей отключено")
        
        # Обновляем меню настроек
        await update_settings_menu(client, callback_query)
        
        # Уведомляем других администраторов
        actor = callback_query.from_user
        notification = f"ℹ️ Администратор {actor.first_name} (@{actor.username or 'нет'}) отключил автоматическое добавление пользователей.\nПользователи будут добавляться только вручную через команду /admin."
        
        for admin_id in ADMIN_IDS:
            if admin_id != callback_query.from_user.id:  # Не отправляем уведомление тому, кто отключил
                try:
                    await client.send_message(admin_id, notification)
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
    
    except Exception as e:
        logger.error(f"Ошибка при отключении автодобавления: {e}")
        await callback_query.answer("❌ Произошла ошибка при отключении автодобавления")

async def check_pending_manual_requests():
    """
    Проверяет наличие заявок, ожидающих ручного добавления, и отправляет 
    обобщенное уведомление администраторам (без кнопок и отдельных сообщений)
    """
    # Проверяем, включена ли функция уведомлений о заявках при запуске
    notify_on_startup = get_setting("notify_on_startup", "true")
    if notify_on_startup.lower() != "true":
        logger.info("Уведомления о заявках при запуске отключены в настройках")
        return
    
    logger.info("Проверка наличия заявок, ожидающих ручного добавления...")
    
    session = get_session()
    try:
        # Получаем актуальные заявки со статусом manual_check
        # Добавляем временное ограничение - заявки не старше 1 дня
        time_limit = datetime.now() - timedelta(days=1)
        pending_requests = session.query(JoinRequest).filter(
            JoinRequest.status == "manual_check",
            JoinRequest.created_at >= time_limit
        ).all()
        
        if not pending_requests:
            logger.info("Актуальных заявок, ожидающих ручного добавления, не найдено")
            return
        
        count = len(pending_requests)
        logger.info(f"Найдено {count} актуальных заявок, ожидающих ручного добавления")
        
        # Отправляем ОДНО общее уведомление админам (без отдельных сообщений о каждой заявке)
        admin_text = f"📋 При запуске бота обнаружено {count} актуальных заявок, ожидающих проверки.\n\n"
        admin_text += "Для просмотра и обработки заявок используйте команду /admin и выберите 'Активные заявки'.\n\n"
        admin_text += "Чтобы отключить это уведомление, используйте команду /settings."
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    admin_text,
                    parse_mode=enums.ParseMode.HTML
                )
                logger.info(f"Администратору {admin_id} отправлено обобщенное уведомление о {count} заявках")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка при проверке заявок, ожидающих ручного добавления: {e}")
    finally:
        session.close()

async def startup():
    """
    Функция запуска
    """
    # Инициализация базы данных
    logger.info("Инициализация базы данных...")
    init_db()
    logger.info("База данных инициализирована")
    
    # Проверка таблицы Settings
    session = get_session()
    try:
        logger.info("Проверка таблицы Settings...")
        # Проверяем, существует ли таблица settings
        from sqlalchemy import inspect
        inspector = inspect(engine)
        if 'settings' in inspector.get_table_names():
            logger.info("Таблица Settings существует")
        else:
            logger.warning("Таблица Settings не существует. Пытаемся создать...")
            Base.metadata.tables['settings'].create(engine)
            logger.info("Таблица Settings создана")
    except Exception as e:
        logger.error(f"Ошибка при проверке таблицы Settings: {e}")
    finally:
        session.close()
    
    # Инициализация настроек
    logger.info("Проверка настроек...")
    
    # Настройка автодобавления
    auto_add_value = get_setting("auto_add_enabled")
    if auto_add_value is None:
        logger.info("Настройка auto_add_enabled не найдена. Инициализация со значением 'true'...")
        set_setting("auto_add_enabled", "true")
    
    # Настройка уведомлений при запуске
    notify_value = get_setting("notify_on_startup")
    if notify_value is None:
        logger.info("Настройка notify_on_startup не найдена. Инициализация со значением 'true'...")
        set_setting("notify_on_startup", "true")
    
    # Логируем финальные значения
    logger.info(f"auto_add_enabled: {get_setting('auto_add_enabled', 'true')}")
    logger.info(f"notify_on_startup: {get_setting('notify_on_startup', 'true')}")
    
    # Запуск бота
    logger.info("Запуск бота...")
    await bot.start()
    logger.info("Бот запущен")
    
    # Проверка заявок, ожидающих ручного добавления
    await check_pending_manual_requests()
    
    # Бесконечный цикл для поддержания работы бота
    while True:
        await asyncio.sleep(3600)  # Ждем 1 час
        
# Добавляем команду для настройки бота
@bot.on_message(filters.command("settings") & filters.private & filters.user(ADMIN_IDS))
async def settings_command(client, message):
    """
    Настройки бота
    """
    auto_add_enabled = get_setting("auto_add_enabled", "true")
    auto_add_status = "✅ Включено" if auto_add_enabled.lower() == "true" else "❌ Отключено"
    
    notify_on_startup = get_setting("notify_on_startup", "true")
    notify_status = "✅ Включено" if notify_on_startup.lower() == "true" else "❌ Отключено"
    
    settings_text = "⚙️ Настройки твоего бота 🤖\n\n"
    settings_text += f"🔄 Автоматическое добавление: {auto_add_status}\n"
    settings_text += f"🔔 Уведомления о заявках при запуске: {notify_status}\n"
    
    # Создаем кнопки для изменения настроек
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            "🔄 Автодобавление: выключить" if auto_add_enabled.lower() == "true" else "🔄 Автодобавление: включить", 
            callback_data="toggle_auto_add_off" if auto_add_enabled.lower() == "true" else "toggle_auto_add_on"
        )],
        [types.InlineKeyboardButton(
            "🔔 Уведомления: выключить" if notify_on_startup.lower() == "true" else "🔔 Уведомления: включить", 
            callback_data="toggle_notify_off" if notify_on_startup.lower() == "true" else "toggle_notify_on"
        )],
        [types.InlineKeyboardButton("✏️ Настройка текстов", callback_data="ui_text_settings")],
        [types.InlineKeyboardButton("↩️ Назад в меню админа", callback_data="back_to_admin")]
    ])
    
    await message.reply(settings_text, reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^toggle_notify_on$"))
async def toggle_notify_on_callback(client, callback_query):
    """
    Включение уведомлений о заявках при запуске бота
    """
    try:
        # Устанавливаем флаг в значение true
        old_value = get_setting("notify_on_startup", "true")
        logger.info(f"Текущее значение notify_on_startup перед включением: {old_value}")
        
        # Принудительно устанавливаем новое значение
        set_setting("notify_on_startup", "true")
        logger.info(f"Администратор {callback_query.from_user.id} включил уведомления о заявках при запуске")
        
        # Проверяем, что значение успешно изменено
        new_value = get_setting("notify_on_startup", "true")
        logger.info(f"Новое значение notify_on_startup после включения: {new_value}")
        
        # Уведомляем администратора
        await callback_query.answer("✅ Уведомления о заявках при запуске включены")
        
        # Обновляем меню настроек
        await update_settings_menu(client, callback_query)
        
    except Exception as e:
        logger.error(f"Ошибка при включении уведомлений: {e}")
        await callback_query.answer("❌ Произошла ошибка при включении уведомлений")

@bot.on_callback_query(filters.regex(r"^toggle_notify_off$"))
async def toggle_notify_off_callback(client, callback_query):
    """
    Отключение уведомлений о заявках при запуске бота
    """
    try:
        # Устанавливаем флаг в значение false
        old_value = get_setting("notify_on_startup", "true")
        logger.info(f"Текущее значение notify_on_startup перед отключением: {old_value}")
        
        # Принудительно устанавливаем новое значение
        set_setting("notify_on_startup", "false")
        logger.info(f"Администратор {callback_query.from_user.id} отключил уведомления о заявках при запуске")
        
        # Проверяем, что значение успешно изменено
        new_value = get_setting("notify_on_startup", "false")
        logger.info(f"Новое значение notify_on_startup после отключения: {new_value}")
        
        # Уведомляем администратора
        await callback_query.answer("✅ Уведомления о заявках при запуске отключены")
        
        # Обновляем меню настроек
        await update_settings_menu(client, callback_query)
        
    except Exception as e:
        logger.error(f"Ошибка при отключении уведомлений: {e}")
        await callback_query.answer("❌ Произошла ошибка при отключении уведомлений")

async def update_settings_menu(client, callback_query):
    """
    Обновляет меню настроек
    """
    auto_add_enabled = get_setting("auto_add_enabled", "true")
    auto_add_status = "✅ Включено" if auto_add_enabled.lower() == "true" else "❌ Отключено"
    
    notify_on_startup = get_setting("notify_on_startup", "true")
    notify_status = "✅ Включено" if notify_on_startup.lower() == "true" else "❌ Отключено"
    
    settings_text = "⚙️ Настройки бота:\n\n"
    settings_text += f"🔄 Автоматическое добавление: {auto_add_status}\n"
    settings_text += f"🔔 Уведомления о заявках при запуске: {notify_status}\n"
    
    # Создаем кнопки для изменения настроек
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            "🔄 Автодобавление: выключить" if auto_add_enabled.lower() == "true" else "🔄 Автодобавление: включить", 
            callback_data="toggle_auto_add_off" if auto_add_enabled.lower() == "true" else "toggle_auto_add_on"
        )],
        [types.InlineKeyboardButton(
            "🔔 Уведомления при запуске: выключить" if notify_on_startup.lower() == "true" else "🔔 Уведомления при запуске: включить", 
            callback_data="toggle_notify_off" if notify_on_startup.lower() == "true" else "toggle_notify_on"
        )],
        [types.InlineKeyboardButton("✏️ Настройка текста интерфейса", callback_data="ui_text_settings")],
        [types.InlineKeyboardButton("↩️ Назад в меню администратора", callback_data="back_to_admin")]
    ])
    
    try:
        await callback_query.edit_message_text(settings_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при обновлении меню настроек: {e}")
        
@bot.on_callback_query(filters.regex(r"^ui_text_settings$"))
async def ui_text_settings_callback(client, callback_query):
    """
    Меню настройки текстов пользовательского интерфейса
    """
    try:
        ui_text_menu = "✏️ Настройка текстов интерфейса 💬\n\n"
        ui_text_menu += "Какой текст хочешь изменить? Выбирай! 👇\n\n"
        ui_text_menu += "📝 <b>Тексты кнопок:</b>\n"
        
        # Создаем клавиатуру с кнопками для редактирования различных текстов
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("✏️ Кнопка 'Вступить в чат'", callback_data="edit_button_join_text")],
            [types.InlineKeyboardButton("✏️ Кнопка 'Что тут у вас?'", callback_data="edit_button_info_text")],
            [types.InlineKeyboardButton("✏️ Кнопка 'Нужна помощь'", callback_data="edit_button_support_text")],
            [types.InlineKeyboardButton("✏️ Приветственное сообщение", callback_data="edit_welcome_message")],
            [types.InlineKeyboardButton("───────────────────", callback_data="preview_no_action")],
            [types.InlineKeyboardButton("📄 <b>Содержимое сообщений:</b>", callback_data="preview_no_action")],
            [types.InlineKeyboardButton("✏️ Информация о чатах", callback_data="edit_info_text")],
            [types.InlineKeyboardButton("✏️ Текст выбора чата", callback_data="edit_chat_select_text")],
            [types.InlineKeyboardButton("✏️ Текст поддержки", callback_data="edit_support_text")],
            [types.InlineKeyboardButton("✏️ Сообщение о приватности", callback_data="edit_privacy_message")],
            [types.InlineKeyboardButton("✏️ Сообщение о ручной проверке", callback_data="edit_manual_check_message")],
            [types.InlineKeyboardButton("✏️ Шаблон сообщения об ошибке", callback_data="edit_error_message_template")],
            [types.InlineKeyboardButton("↩️ Назад в настройки", callback_data="back_to_settings")]
        ])
        
        await callback_query.edit_message_text(ui_text_menu, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка при отображении меню настройки текстов интерфейса: {e}")
        await callback_query.answer("Упс! Не могу загрузить настройки текстов 😅")

@bot.on_callback_query(filters.regex(r"^edit_(\w+)$"))
async def edit_ui_text_callback(client, callback_query):
    """
    Подготовка к редактированию выбранного текста интерфейса
    """
    try:
        # Получаем имя настройки из callback_data
        setting_name = callback_query.data.split("_", 1)[1]
        
        # Словарь соответствия настроек и их описаний
        setting_descriptions = {
            "button_join_text": "кнопки 'Хочу в чат'",
            "button_info_text": "кнопки 'Узнать подробности'",
            "button_support_text": "кнопки 'Поддержка'",
            "welcome_message": "приветственного сообщения",
            "chat_select_text": "текста выбора чата",
            "info_text": "информации о чатах",
            "support_text": "текста поддержки",
            "privacy_message": "текста сообщения о приватности",
            "manual_check_message": "текста сообщения о ручной проверке",
            "error_message_template": "шаблона сообщения об ошибке"
        }
        
        # Словарь значений по умолчанию
        default_values = {
            "button_join_text": "🚪 Хочу в чат",
            "button_info_text": "ℹ️ Узнать подробности",
            "button_support_text": "📞 Поддержка",
            "welcome_message": "👋 Привет! Выберите действие:",
            "chat_select_text": "Выберите чат, в который хотите вступить:",
            "info_text": "ℹ️ *Информация о чатах*\n\nНаши чаты предназначены для общения на разные темы.",
            "support_text": "📞 Поддержка бота",
            "privacy_message": "🔒 К сожалению, ваши настройки приватности не позволяют добавить вас автоматически.\n\n🔍 Чтобы решить эту проблему, вам необходимо изменить настройки конфиденциальности:\n\n👉 Откройте настройки Telegram\n👉 Перейдите в раздел 'Конфиденциальность'\n👉 Выберите 'Группы и каналы'\n👉 Для опции 'Кто может добавить меня в группы' выберите 'Все'\n\n📱 Вот как это выглядит (смотрите приложенные картинки):\n",
            "manual_check_message": "⏳ Ваша заявка принята и будет рассмотрена администратором.\n\n📋 В данный момент включен режим ручного добавления пользователей.\n⌛ Вы будете добавлены после одобрения заявки администратором.",
            "error_message_template": "❌ Не удалось добавить вас в чат: {error}\n\nПопробуйте позже или обратитесь в поддержку."
        }
        
        # Проверяем, есть ли такая настройка в наших словарях
        if setting_name not in setting_descriptions:
            await callback_query.answer("Неизвестная настройка")
            return
        
        # Получаем текущее значение настройки
        current_value = get_setting(setting_name, default_values.get(setting_name, ""))
        
        # Сохраняем в пользовательские данные имя настройки
        user_id = callback_query.from_user.id
        set_setting(f"temp_editing_{user_id}", setting_name)
        
        # Отправляем сообщение с инструкцией
        instruction_text = f"✏️ Редактирование текста {setting_descriptions[setting_name]}\n\n"
        instruction_text += f"Текущий текст:\n<code>{current_value}</code>\n\n"
        instruction_text += "Отправьте новый текст в следующем сообщении."
        
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Отмена", callback_data="ui_text_settings")]
        ])
        
        await callback_query.edit_message_text(instruction_text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
        
        # Устанавливаем состояние "ожидание нового текста"
        set_setting(f"waiting_text_{user_id}", "true")
        
    except Exception as e:
        logger.error(f"Ошибка при подготовке к редактированию текста: {e}")
        await callback_query.answer("Произошла ошибка при подготовке к редактированию")

@bot.on_callback_query(filters.regex(r"^back_to_settings$"))
async def back_to_settings_callback(client, callback_query):
    """
    Возврат в меню настроек
    """
    try:
        auto_add_enabled = get_setting("auto_add_enabled", "true")
        auto_add_status = "✅ Включено" if auto_add_enabled.lower() == "true" else "❌ Отключено"
        
        notify_on_startup = get_setting("notify_on_startup", "true")
        notify_status = "✅ Включено" if notify_on_startup.lower() == "true" else "❌ Отключено"
        
        settings_text = "⚙️ Настройки бота:\n\n"
        settings_text += f"🔄 Автоматическое добавление: {auto_add_status}\n"
        settings_text += f"🔔 Уведомления о заявках при запуске: {notify_status}\n"
        
        # Создаем кнопки для изменения настроек
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton(
                "🔄 Автодобавление: выключить" if auto_add_enabled.lower() == "true" else "🔄 Автодобавление: включить", 
                callback_data="toggle_auto_add_off" if auto_add_enabled.lower() == "true" else "toggle_auto_add_on"
            )],
            [types.InlineKeyboardButton(
                "🔔 Уведомления при запуске: выключить" if notify_on_startup.lower() == "true" else "🔔 Уведомления при запуске: включить", 
                callback_data="toggle_notify_off" if notify_on_startup.lower() == "true" else "toggle_notify_on"
            )],
            [types.InlineKeyboardButton("✏️ Настройка текста интерфейса", callback_data="ui_text_settings")],
            [types.InlineKeyboardButton("↩️ Назад в меню администратора", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(settings_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при возврате в меню настроек: {e}")
        await callback_query.answer("Произошла ошибка при возврате в меню настроек")

# Обработчик для получения нового текста настроек и номера телефона/кода
@bot.on_message(filters.private & filters.user(ADMIN_IDS) & filters.text)
async def handle_new_ui_text(client, message):
    """
    Обработка нового текста для пользовательского интерфейса
    или обработка добавления нового аккаунта администратора
    """
    user_id = message.from_user.id
    waiting_text = get_setting(f"waiting_text_{user_id}", "false")
    waiting_phone = get_setting(f"waiting_phone_{user_id}", "false")
    waiting_code = get_setting(f"waiting_code_{user_id}", "false")
    
    # Если пользователь в процессе ввода номера телефона для нового аккаунта
    if waiting_phone.lower() == "true":
        phone_number = message.text.strip()
        
        # Валидация номера телефона
        if not phone_number.startswith("+") or not phone_number[1:].isdigit():
            await message.reply("❌ Неверный формат номера телефона. Пожалуйста, введите номер в международном формате, например: +79001234567")
            return
        
        try:
            # Сохраняем номер телефона во временное хранилище
            set_setting(f"temp_phone_{user_id}", phone_number)
            
            # Создаем временного клиента Pyrogram
            temp_client = Client(
                name=f"temp_admin_{user_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True
            )
            
            # Запускаем клиент
            await temp_client.connect()
            
            # Отправляем код подтверждения
            sent_code = await temp_client.send_code(phone_number)
            
            # Сохраняем phone_code_hash
            set_setting(f"temp_code_hash_{user_id}", sent_code.phone_code_hash)
            
            # Сохраняем состояние "ожидание кода"
            set_setting(f"waiting_phone_{user_id}", "false")
            set_setting(f"waiting_code_{user_id}", "true")
            
            # Отправляем сообщение с инструкцией для ввода кода
            await message.reply(
                f"📱 Код подтверждения отправлен на номер {phone_number}\n\n"
                f"Пожалуйста, введите код подтверждения из Telegram:",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Отмена", callback_data="back_to_admin")]
                ])
            )
            
            # Отключаем временный клиент
            await temp_client.disconnect()
            
        except Exception as e:
            logger.error(f"Ошибка при отправке кода подтверждения: {e}")
            await message.reply(f"❌ Произошла ошибка при отправке кода подтверждения: {str(e)}")
            
            # Сбрасываем состояние
            set_setting(f"waiting_phone_{user_id}", "false")
            
        return
    
    # Если пользователь в процессе ввода кода подтверждения
    elif waiting_code.lower() == "true":
        code = message.text.strip()
        
        # Валидация кода
        if not code.isdigit() or len(code) < 5:
            await message.reply("❌ Неверный формат кода. Пожалуйста, введите код подтверждения (5 цифр).")
            return
        
        try:
            # Получаем сохраненные данные
            phone_number = get_setting(f"temp_phone_{user_id}", "")
            phone_code_hash = get_setting(f"temp_code_hash_{user_id}", "")
            
            if not phone_number or not phone_code_hash:
                raise ValueError("Отсутствуют необходимые данные для авторизации")
            
            # Создаем временного клиента Pyrogram
            temp_client = Client(
                name=f"temp_admin_{user_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True
            )
            
            # Подключаемся
            await temp_client.connect()
            
            # Пробуем войти в аккаунт
            await message.reply("⏳ Выполняется вход в аккаунт...")
            
            try:
                # Пытаемся войти с полученным кодом
                await temp_client.sign_in(
                    phone_number=phone_number,
                    phone_code_hash=phone_code_hash,
                    phone_code=code
                )
                
                # Если нужен пароль, сообщаем об этом
                if await temp_client.get_password_hint():
                    await message.reply(
                        "🔐 Этот аккаунт защищен двухфакторной аутентификацией.\n\n"
                        "Для безопасности аккаунта, мы не можем автоматически добавить его.\n"
                        "Пожалуйста, используйте скрипт session_creator.py вместо этого."
                    )
                    await temp_client.disconnect()
                    
                    # Сбрасываем состояние
                    set_setting(f"waiting_code_{user_id}", "false")
                    set_setting(f"temp_phone_{user_id}", "")
                    set_setting(f"temp_code_hash_{user_id}", "")
                    
                    return
            except errors.SessionPasswordNeeded:
                # Если требуется пароль
                await message.reply(
                    "🔐 Этот аккаунт защищен двухфакторной аутентификацией.\n\n"
                    "Для безопасности аккаунта, мы не можем автоматически добавить его.\n"
                    "Пожалуйста, используйте скрипт session_creator.py вместо этого."
                )
                await temp_client.disconnect()
                
                # Сбрасываем состояние
                set_setting(f"waiting_code_{user_id}", "false")
                set_setting(f"temp_phone_{user_id}", "")
                set_setting(f"temp_code_hash_{user_id}", "")
                
                return
            
            # Если вход успешный, получаем строку сессии
            session_string = await temp_client.export_session_string()
            
            # Получаем информацию о пользователе
            me = await temp_client.get_me()
            
            # Отключаемся от временного клиента
            await temp_client.disconnect()
            
            # Сохраняем аккаунт в базу данных
            session = get_session()
            try:
                # Проверяем, существует ли уже такой аккаунт
                existing_account = session.query(AdminAccount).filter_by(phone=phone_number).first()
                
                if existing_account:
                    # Обновляем существующий аккаунт
                    existing_account.session_data = encrypt_session({"session_string": session_string})
                    existing_account.active = True
                    existing_account.last_used = datetime.now()
                    session.commit()
                    
                    await message.reply(
                        f"✅ Аккаунт {phone_number} успешно обновлен в базе данных!\n\n"
                        f"👤 Имя: {me.first_name} {me.last_name or ''}\n"
                        f"👤 Username: @{me.username or 'отсутствует'}"
                    )
                else:
                    # Создаем новый аккаунт
                    new_account = AdminAccount(
                        phone=phone_number,
                        session_data=encrypt_session({"session_string": session_string}),
                        active=True,
                        added_by=user_id,
                        usage_count=0
                    )
                    session.add(new_account)
                    session.commit()
                    
                    await message.reply(
                        f"✅ Аккаунт {phone_number} успешно добавлен в базу данных!\n\n"
                        f"👤 Имя: {me.first_name} {me.last_name or ''}\n"
                        f"👤 Username: @{me.username or 'отсутствует'}"
                    )
            except Exception as db_error:
                logger.error(f"Ошибка при сохранении аккаунта в базу данных: {db_error}")
                await message.reply(f"❌ Произошла ошибка при сохранении аккаунта: {str(db_error)}")
            finally:
                session.close()
            
            # Сбрасываем состояние
            set_setting(f"waiting_code_{user_id}", "false")
            set_setting(f"temp_phone_{user_id}", "")
            set_setting(f"temp_code_hash_{user_id}", "")
            
        except Exception as e:
            logger.error(f"Ошибка при входе в аккаунт: {e}")
            await message.reply(f"❌ Произошла ошибка при входе в аккаунт: {str(e)}")
            
            # Сбрасываем состояние
            set_setting(f"waiting_code_{user_id}", "false")
            set_setting(f"temp_phone_{user_id}", "")
            set_setting(f"temp_code_hash_{user_id}", "")
        
        return
    
    # Если пользователь не в процессе ввода телефона или кода, обрабатываем как ввод текста для настроек
    elif waiting_text.lower() == "true":
        try:
            # Получаем имя настройки, которую редактируем
            setting_name = get_setting(f"temp_editing_{user_id}", "")
            
            if not setting_name:
                await message.reply("Ой! Не могу найти настройку, которую редактируем 🤔")
                return
            
            # Сохраняем новый текст
            new_text = message.text
            set_setting(setting_name, new_text)
            
            # Сбрасываем состояние ожидания
            set_setting(f"waiting_text_{user_id}", "false")
            set_setting(f"temp_editing_{user_id}", "")
            
            # Отправляем сообщение об успехе
            success_message = f"✅ Текст успешно обновлен! 🎉\n\nТеперь он выглядит так:\n<code>{new_text}</code>"
            
            # Создаем кнопки для возврата в меню настроек
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад к настройкам текстов", callback_data="ui_text_settings")],
                [types.InlineKeyboardButton("🔍 Посмотреть как будет выглядеть", callback_data="preview_ui_changes")]
            ])
            
            await message.reply(success_message, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка при сохранении нового текста: {e}")
            await message.reply(f"Ой! Что-то пошло не так при сохранении: {str(e)} 😅\nПопробуй еще раз!")
    
    # Игнорируем обычные сообщения, если пользователь не в режиме ввода

@bot.on_callback_query(filters.regex(r"^preview_ui_changes$"))
async def preview_ui_changes_callback(client, callback_query):
    """
    Предварительный просмотр изменений интерфейса
    """
    try:
        # Получаем настраиваемые тексты из базы данных (с значениями по умолчанию)
        button_join_text = get_setting("button_join_text", "🚀 Вступить в чат!")
        button_info_text = get_setting("button_info_text", "🔍 Что тут у вас?")
        button_support_text = get_setting("button_support_text", "🆘 Нужна помощь")
        welcome_message = get_setting("welcome_message", "👋 Хэй! Рады видеть тебя! 😎 Что будем делать?")
        
        # Создаем клавиатуру с новыми кнопками (preview)
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton(button_join_text, callback_data="preview_no_action")],
            [types.InlineKeyboardButton(button_info_text, callback_data="preview_no_action")],
            [types.InlineKeyboardButton(button_support_text, callback_data="preview_no_action")],
            [types.InlineKeyboardButton("↩️ Назад к настройкам текстов", callback_data="ui_text_settings")]
        ])
        
        preview_text = "🔍 Вот как это будет выглядеть 👇\n\n"
        preview_text += welcome_message
        
        await callback_query.edit_message_text(preview_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при предварительном просмотре интерфейса: {e}")
        await callback_query.answer("Ой! Не могу показать предпросмотр 😅")

@bot.on_callback_query(filters.regex(r"^preview_no_action$"))
async def preview_no_action_callback(client, callback_query):
    """
    Заглушка для кнопок в режиме предпросмотра
    """
    await callback_query.answer("Это просто предпросмотр! Красиво, правда? 😉")

async def shutdown():
    """
    Функция остановки
    """
    # Закрываем клиент администратора, если он активен
    global active_admin_client
    if active_admin_client and active_admin_client.is_connected:
        await active_admin_client.stop()
    
    # Останавливаем бот
    await bot.stop()
    logger.info("Бот остановлен")

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(startup())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown())
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")

# Обработчики для ручного добавления и отклонения заявок
@bot.on_callback_query(filters.regex(r"^manual_add_\d+_-?\d+$"))
async def manual_add_callback(client, callback_query):
    """
    Ручное добавление пользователя администратором из уведомления
    """
    try:
        # Извлекаем ID пользователя и чата из callback_data
        logger.info(f"ОБРАБОТКА CALLBACK ДЛЯ ДОБАВЛЕНИЯ: {callback_query.data} от пользователя {callback_query.from_user.id}")
        
        user_id = int(callback_query.data.split("_")[2])
        chat_id = int(callback_query.data.split("_")[3])
        
        logger.info(f"Начато ручное добавление пользователя {user_id} в чат {chat_id} администратором {callback_query.from_user.id}")
        
        # Сообщаем администратору, что начинаем процесс добавления
        await callback_query.answer("Начинаем процесс добавления пользователя...")
        
        # Временно включаем автодобавление для этой операции
        current_auto_add = get_setting("auto_add_enabled", "true")
        logger.info(f"Сохранено текущее значение настройки auto_add_enabled: {current_auto_add}")
        
        # Принудительно включаем автодобавление для этой операции
        set_setting("auto_add_enabled", "true")
        logger.info(f"Временно включено автодобавление для ручной операции")
        
        # Вызываем функцию добавления пользователя
        success, message = await add_user_to_chat(user_id, chat_id)
        logger.info(f"Результат ручного добавления: success={success}, message={message}")
        
        # Восстанавливаем предыдущее значение настройки
        set_setting("auto_add_enabled", current_auto_add)
        logger.info(f"Восстановлено предыдущее значение auto_add_enabled: {current_auto_add}")
        
        if success:
            # Обновляем статус заявки
            session = get_session()
            try:
                join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="manual_check").first()
                if join_request:
                    join_request.status = "approved"
                    join_request.approved_by = callback_query.from_user.id
                    join_request.approved_at = datetime.now()
                    session.commit()
                    logger.info(f"Статус заявки обновлен на 'approved'")
                else:
                    logger.warning(f"Не найдена заявка в статусе 'manual_check' для пользователя {user_id} и чата {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка при обновлении статуса заявки: {e}")
            finally:
                session.close()
            
            # Уведомляем пользователя об успешном добавлении
            try:
                chat_name = "основной чат" if chat_id == CHAT_ID_1 else "второй чат"
                await bot.send_message(
                    user_id,
                    f"✅ Вы были успешно добавлены в {chat_name} администратором!\n\n"
                    f"Можете открыть чат в своем приложении Telegram."
                )
                logger.info(f"Отправлено уведомление пользователю {user_id} об успешном добавлении")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
            
            # Уведомляем других администраторов
            user_info = await client.get_users(user_id)
            notification = (
                f"✅ Администратор {callback_query.from_user.first_name} (@{callback_query.from_user.username or 'нет'}) "
                f"вручную добавил пользователя {user_info.first_name} {user_info.last_name or ''} (@{user_info.username or 'нет'}) в чат."
            )
            
            for admin_id in ADMIN_IDS:
                if admin_id != callback_query.from_user.id:  # Не отправляем уведомление тому, кто добавил
                    try:
                        await client.send_message(admin_id, notification)
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
            
            # Обновляем сообщение в интерфейсе администратора
            admin_text = (
                f"✅ Пользователь успешно добавлен:\n\n"
                f"ID: {user_id}\n"
                f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
                f"Username: @{user_info.username or 'отсутствует'}\n\n"
                f"Чат: {'Чат #1' if chat_id == CHAT_ID_1 else 'Чат #2'}\n"
                f"Добавлен вручную администратором: {callback_query.from_user.first_name}\n"
                f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
            
            try:
                # Если сообщение было с фото (caption)
                await callback_query.edit_message_caption(caption=admin_text)
                logger.info(f"Обновлено сообщение с фото для администратора")
            except Exception as caption_err:
                # Если сообщение было текстовым
                logger.info(f"Не удалось обновить caption, пробуем обновить текст сообщения: {caption_err}")
                try:
                    await callback_query.edit_message_text(admin_text)
                    logger.info(f"Обновлено текстовое сообщение для администратора")
                except Exception as text_err:
                    logger.error(f"Не удалось обновить сообщение администратора: {text_err}")
        else:
            # Обрабатываем ошибку
            error_text = (
                f"❌ Не удалось добавить пользователя:\n\n"
                f"ID: {user_id}\n"
                f"Ошибка: {message}\n"
                f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
            
            # Обновляем статус заявки в зависимости от ошибки
            session = get_session()
            try:
                join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="manual_check").first()
                if join_request:
                    if "приватности" in message.lower() or "privacy" in message.lower() or "UserPrivacyRestricted" in message:
                        join_request.status = "link_sent"
                        logger.info(f"Статус заявки обновлен на 'link_sent' из-за проблем с приватностью")
                    else:
                        join_request.status = "rejected"
                        logger.info(f"Статус заявки обновлен на 'rejected' из-за ошибки")
                    session.commit()
                else:
                    logger.warning(f"Не найдена заявка в статусе 'manual_check' для пользователя {user_id} и чата {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка при обновлении статуса заявки: {e}")
            finally:
                session.close()
            
            try:
                # Если сообщение было с фото (caption)
                await callback_query.edit_message_caption(caption=error_text)
                logger.info(f"Обновлено сообщение с фото для администратора (ошибка)")
            except Exception as caption_err:
                # Если сообщение было текстовым
                logger.info(f"Не удалось обновить caption, пробуем обновить текст сообщения: {caption_err}")
                try:
                    await callback_query.edit_message_text(error_text)
                    logger.info(f"Обновлено текстовое сообщение для администратора (ошибка)")
                except Exception as text_err:
                    logger.error(f"Не удалось обновить сообщение администратора: {text_err}")
                
    except Exception as e:
        logger.error(f"Ошибка при ручном добавлении пользователя: {e}")
        await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")

@bot.on_callback_query(filters.regex(r"^manual_reject_\d+_-?\d+$"))
async def manual_reject_callback(client, callback_query):
    """
    Отклонение заявки на добавление пользователя из уведомления
    """
    try:
        # Извлекаем ID пользователя и чата из callback_data
        logger.info(f"ОБРАБОТКА CALLBACK ДЛЯ ОТКЛОНЕНИЯ: {callback_query.data} от пользователя {callback_query.from_user.id}")
        
        user_id = int(callback_query.data.split("_")[2])
        chat_id = int(callback_query.data.split("_")[3])
        
        # Сообщаем администратору, что заявка отклонена
        await callback_query.answer("Заявка отклонена")
        
        # Обновляем статус заявки
        session = get_session()
        try:
            join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="manual_check").first()
            if join_request:
                join_request.status = "rejected"
                session.commit()
                logger.info(f"Статус заявки пользователя {user_id} изменен на 'rejected'")
            else:
                logger.warning(f"Не найдена заявка в статусе 'manual_check' для пользователя {user_id} и чата {chat_id}")
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса заявки: {e}")
        finally:
            session.close()
        
        # Получаем информацию о пользователе для уведомлений
        user_info = await client.get_users(user_id)
        
        # Уведомляем пользователя
        try:
            await client.send_message(
                user_id,
                "❌ К сожалению, ваша заявка на вступление в чат была отклонена администратором.\n\n"
                "Если у вас есть вопросы, вы можете связаться с поддержкой."
            )
            logger.info(f"Отправлено уведомление пользователю {user_id} об отклонении заявки")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
        
        # Уведомляем других администраторов
        notification = (
            f"❌ Администратор {callback_query.from_user.first_name} (@{callback_query.from_user.username or 'нет'}) "
            f"отклонил заявку пользователя {user_info.first_name} {user_info.last_name or ''} (@{user_info.username or 'нет'})."
        )
        
        for admin_id in ADMIN_IDS:
            if admin_id != callback_query.from_user.id:  # Не отправляем уведомление тому, кто отклонил
                try:
                    await client.send_message(admin_id, notification)
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления администратору {admin_id}: {e}")
        
        # Обновляем сообщение в интерфейсе администратора
        admin_text = (
            f"❌ Заявка отклонена:\n\n"
            f"ID: {user_id}\n"
            f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
            f"Username: @{user_info.username or 'отсутствует'}\n\n"
            f"Чат: {'Чат #1' if chat_id == CHAT_ID_1 else 'Чат #2'}\n"
            f"Отклонено администратором: {callback_query.from_user.first_name}\n"
            f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        try:
            # Если сообщение было с фото (caption)
            await callback_query.edit_message_caption(caption=admin_text)
            logger.info(f"Обновлено сообщение с фото для администратора")
        except Exception as caption_err:
            # Если сообщение было текстовым
            logger.info(f"Не удалось обновить caption, пробуем обновить текст сообщения: {caption_err}")
            try:
                await callback_query.edit_message_text(admin_text)
                logger.info(f"Обновлено текстовое сообщение для администратора")
            except Exception as text_err:
                logger.error(f"Не удалось обновить сообщение администратора: {text_err}")
                
    except Exception as e:
        logger.error(f"Ошибка при отклонении заявки: {e}")
        await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")

# Оставляем обработчики команд для блокировки и разблокировки пользователей
@bot.on_message(filters.command("block") & filters.private & filters.user(ADMIN_IDS))
async def block_command(client, message):
    """
    Блокировка пользователя
    """
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply("Использование: /block [user_id]")
            return
        
        user_id = int(args[1])
        session = get_session()
        
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            await message.reply(f"Пользователь с ID {user_id} не найден.")
            return
        
        user.is_blacklisted = True
        session.commit()
        await message.reply(f"✅ Пользователь с ID {user_id} заблокирован.")
    except ValueError:
        await message.reply("ID пользователя должен быть числом.")
    except Exception as e:
        logger.error(f"Ошибка при блокировке пользователя: {e}")
        await message.reply("Произошла ошибка при блокировке пользователя.")
    finally:
        session.close()

@bot.on_message(filters.command("unblock") & filters.private & filters.user(ADMIN_IDS))
async def unblock_command(client, message):
    """
    Разблокировка пользователя
    """
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply("Использование: /unblock [user_id]")
            return
        
        user_id = int(args[1])
        session = get_session()
        
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            await message.reply(f"Пользователь с ID {user_id} не найден.")
            return
        
        user.is_blacklisted = False
        session.commit()
        await message.reply(f"✅ Пользователь с ID {user_id} разблокирован.")
    except ValueError:
        await message.reply("ID пользователя должен быть числом.")
    except Exception as e:
        logger.error(f"Ошибка при разблокировке пользователя: {e}")
        await message.reply("Произошла ошибка при разблокировке пользователя.")
    finally:
        session.close()

@bot.on_message(filters.command("remove_admin") & filters.private & filters.user(ADMIN_IDS))
async def remove_admin_command(client, message):
    """
    Удаление аккаунта администратора
    """
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply("Использование: /remove_admin [phone]")
            return
        
        phone = args[1]
        session = get_session()
        
        admin_account = session.query(AdminAccount).filter_by(phone=phone).first()
        if not admin_account:
            await message.reply(f"Аккаунт с номером {phone} не найден.")
            return
        
        session.delete(admin_account)
        session.commit()
        await message.reply(f"✅ Аккаунт с номером {phone} удален.")
    except Exception as e:
        logger.error(f"Ошибка при удалении аккаунта администратора: {e}")
        await message.reply("Произошла ошибка при удалении аккаунта администратора.")
    finally:
        session.close() 

@bot.on_callback_query(filters.regex(r"^user_details_\d+$"))
async def user_details_callback(client, callback_query):
    """
    Просмотр детальной информации о пользователе
    """
    try:
        callback_data = callback_query.data
        logger.info(f"ВЫЗВАН ОБРАБОТЧИК ДЕТАЛЬНОЙ ИНФОРМАЦИИ. Callback data: {callback_data}")
        
        if not callback_data.startswith("user_details_"):
            logger.error(f"Неверный формат callback_data: {callback_data}")
            await callback_query.answer("Ошибка: неверный формат данных")
            return
            
        user_id = int(callback_data.split('_')[2])
        logger.info(f"Запрошена детальная информация о пользователе {user_id}. Callback data: {callback_data}")
        
        # Сообщаем пользователю, что запрос обрабатывается
        await callback_query.answer("Загрузка информации...")
    
        session = get_session()
        try:
            user_db = session.query(User).filter_by(user_id=user_id).first()
            
            if not user_db:
                await callback_query.answer("Пользователь не найден в базе данных")
                return
            
            try:
                # Получаем расширенную информацию о пользователе через Telegram API
                user_info = await client.get_users(user_id)
                
                # Базовая информация
                username = f"@{user_db.username}" if user_db.username else "нет"
                status = "🚫 Заблокирован" if user_db.is_blacklisted else "✅ Активен"
                chat = f"Чат #{1 if user_db.chat_joined == CHAT_ID_1 else 2}" if user_db.chat_joined else "Не в чате"
                
                # Дополнительная информация
                premium_status = "✅" if hasattr(user_info, "is_premium") and user_info.is_premium else "❌"
                language_code = user_info.language_code or "неизвестно"
                is_bot = "✅" if hasattr(user_info, "is_bot") and user_info.is_bot else "❌"
                is_fake = "✅" if hasattr(user_info, "is_fake") and user_info.is_fake else "❌"
                is_scam = "✅" if hasattr(user_info, "is_scam") and user_info.is_scam else "❌"
                
                # Информация о заявках
                join_requests = session.query(JoinRequest).filter_by(user_id=user_id).order_by(JoinRequest.created_at.desc()).limit(5).all()
                
                # Подробная информация о пользователе
                details_text = f"👤 <b>Подробная информация о пользователе</b>\n\n"
                details_text += f"<b>Основные данные:</b>\n"
                details_text += f"ID: <code>{user_id}</code>\n"
                details_text += f"Имя: {user_db.first_name} {user_db.last_name or ''}\n"
                details_text += f"Username: {username}\n"
                details_text += f"Статус: {status}\n"
                details_text += f"Чат: {chat}\n"
                details_text += f"Регистрация: {user_db.registration_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                
                details_text += f"<b>Telegram профиль:</b>\n"
                details_text += f"Язык: {language_code}\n"
                details_text += f"Premium: {premium_status}\n"
                details_text += f"Бот: {is_bot}\n"
                details_text += f"Фейк: {is_fake}\n"
                details_text += f"Скам: {is_scam}\n\n"
                
                if join_requests:
                    details_text += f"<b>История заявок:</b>\n"
                    for req in join_requests:
                        chat_name = "Чат #1" if req.chat_id == CHAT_ID_1 else "Чат #2"
                        
                        if req.status == "approved":
                            status_emoji = "✅"
                            status_text = "Одобрена"
                        elif req.status == "rejected":
                            status_emoji = "❌"
                            status_text = "Отклонена"
                        elif req.status == "link_sent":
                            status_emoji = "🔗"
                            status_text = "Отправлена инструкция"
                        elif req.status == "manual_check":
                            status_emoji = "👨‍💼"
                            status_text = "Ожидает ручного добавления"
                        elif req.status == "pending":
                            status_emoji = "⏳"
                            status_text = "Обрабатывается"
                        else:
                            status_emoji = "❓"
                            status_text = req.status
                        
                        details_text += f"{status_emoji} {chat_name}: {status_text} ({req.created_at.strftime('%d.%m.%Y %H:%M')})\n"
                    details_text += "\n"
                
                # Создаем клавиатуру с кнопками
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("🔒 Заблокировать", callback_data=f"block_user_{user_id}"),
                     types.InlineKeyboardButton("🔓 Разблокировать", callback_data=f"unblock_user_{user_id}")],
                    [types.InlineKeyboardButton("↩️ Назад к списку", callback_data="admin_users")]
                ])
                
                try:
                    # Отправляем информацию вместе с фото профиля
                    profile_photos = await client.get_profile_photos(user_id, limit=1)
                    if profile_photos.total_count > 0:
                        await callback_query.message.delete()
                        await client.send_photo(
                            callback_query.from_user.id,
                            profile_photos.photos[0][0].file_id,
                            caption=details_text,
                            reply_markup=keyboard,
                            parse_mode=enums.ParseMode.HTML
                        )
                    else:
                        await callback_query.edit_message_text(
                            details_text, 
                            reply_markup=keyboard, 
                            parse_mode=enums.ParseMode.HTML
                        )
                except Exception as photo_err:
                    logger.error(f"Ошибка при отправке фото профиля: {photo_err}")
                    await callback_query.edit_message_text(
                        details_text, 
                        reply_markup=keyboard, 
                        parse_mode=enums.ParseMode.HTML
                    )
                    
            except Exception as user_err:
                logger.error(f"Ошибка при получении расширенной информации о пользователе {user_id}: {user_err}")
                
                # Отображаем только базовую информацию при ошибке
                username = f"@{user_db.username}" if user_db.username else "нет"
                status = "🚫 Заблокирован" if user_db.is_blacklisted else "✅ Активен"
                chat = f"Чат #{1 if user_db.chat_joined == CHAT_ID_1 else 2}" if user_db.chat_joined else "Не в чате"
                
                error_text = f"👤 <b>Информация о пользователе</b>\n\n"
                error_text += f"ID: <code>{user_id}</code>\n"
                error_text += f"Имя: {user_db.first_name} {user_db.last_name or ''}\n"
                error_text += f"Username: {username}\n"
                error_text += f"Статус: {status}\n"
                error_text += f"Чат: {chat}\n"
                error_text += f"Регистрация: {user_db.registration_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                error_text += f"❗ <b>Ошибка получения расширенной информации</b>: {str(user_err)}\n"
                
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Назад к списку", callback_data="admin_users")]
                ])
                
                await callback_query.edit_message_text(
                    error_text, 
                    reply_markup=keyboard, 
                    parse_mode=enums.ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Ошибка при просмотре информации о пользователе: {e}")
            await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Ошибка при получении информации о пользователе: {e}")
        await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")

@bot.on_callback_query(filters.regex(r"^block_user_\d+$"))
async def block_user_callback(client, callback_query):
    """
    Блокировка пользователя через просмотр профиля
    """
    user_id = int(callback_query.data.split('_')[2])
    
    session = get_session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            await callback_query.answer("Пользователь не найден в базе данных")
            return
        
        user.is_blacklisted = True
        session.commit()
        
        await callback_query.answer(f"✅ Пользователь с ID {user_id} заблокирован")
        
        # Возвращаемся к просмотру информации о пользователе
        await user_details_callback(client, callback_query)
    except Exception as e:
        logger.error(f"Ошибка при блокировке пользователя: {e}")
        await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^unblock_user_\d+$"))
async def unblock_user_callback(client, callback_query):
    """
    Разблокировка пользователя из черного списка
    """
    user_id = int(callback_query.data.split('_')[2])
    
    session = get_session()
    try:
        user = session.query(User).filter_by(user_id=user_id).first()
        if not user:
            await callback_query.answer("Пользователь не найден в базе данных")
            return
        
        user.is_blacklisted = False
        session.commit()
        
        await callback_query.answer(f"✅ Пользователь с ID {user_id} разблокирован")
        
        # Возвращаемся к просмотру информации о пользователе
        await user_details_callback(client, callback_query)
    except Exception as e:
        logger.error(f"Ошибка при разблокировке пользователя: {e}")
        await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^unblock_rate_limit_\d+$"))
async def unblock_rate_limit_callback(client, callback_query):
    """
    Разблокировка пользователя, заблокированного за превышение лимита заявок
    """
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("У вас нет прав для выполнения этого действия")
        return
    
    # Получаем ID пользователя из callback_data
    match = re.match(r"^unblock_rate_limit_(\d+)$", callback_query.data)
    if not match:
        await callback_query.answer("Неверный формат данных")
        return
    
    user_id = int(match.group(1))
    logger.info(f"Попытка разблокировки пользователя {user_id} администратором {callback_query.from_user.id}")
    
    # Разблокируем пользователя
    from database import unblock_user_rate_limit
    success = unblock_user_rate_limit(user_id)
    
    if success:
        logger.info(f"Пользователь {user_id} успешно разблокирован администратором {callback_query.from_user.id}")
        await callback_query.answer("Пользователь успешно разблокирован")
        
        # Обновляем текст сообщения, удаляя кнопку разблокировки
        try:
            user_info = await client.get_users(user_id)
            updated_text = (
                f"✅ Пользователь разблокирован:\n\n"
                f"👤 <b>Пользователь:</b>\n"
                f"ID: <code>{user_id}</code>\n"
                f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
                f"Username: @{user_info.username or 'отсутствует'}\n\n"
                f"🔓 <b>Статус</b>: Разблокирован\n"
                f"📅 Дата разблокировки: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"👮 Разблокировал: Администратор {callback_query.from_user.id}\n"
            )
            
            await callback_query.edit_message_text(updated_text, parse_mode=enums.ParseMode.HTML)
            
            # Отправляем сообщение пользователю о разблокировке
            try:
                await client.send_message(
                    user_id,
                    "✅ Вы были разблокированы администратором.\n\n"
                    "Теперь вы снова можете подавать заявки на вступление в чат.\n"
                    "Но помните о лимите в 5 заявок в минуту."
                )
                logger.info(f"Пользователю {user_id} отправлено сообщение о разблокировке")
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение пользователю {user_id} о разблокировке: {e}")
                
        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения о разблокировке: {e}")
            await callback_query.edit_message_text(
                f"✅ Пользователь {user_id} разблокирован.",
                parse_mode=enums.ParseMode.HTML
            )
    else:
        logger.warning(f"Не удалось разблокировать пользователя {user_id}")
        await callback_query.answer("Не удалось разблокировать пользователя. Возможно, он уже разблокирован.")

@bot.on_callback_query(filters.regex(r"^admin_rate_limited_users$"))
async def admin_rate_limited_users_callback(client, callback_query):
    """
    Заглушка для удаленного функционала списка пользователей с ограничениями
    """
    await callback_query.answer("Эта функция отключена")
    await back_to_admin_callback(client, callback_query)

@bot.on_callback_query(filters.regex(r"^admin_manage_accounts$"))
async def admin_manage_accounts_callback(client, callback_query):
    """
    Заглушка для удаленного функционала управления аккаунтами
    """
    await callback_query.answer("Эта функция отключена")
    await back_to_admin_callback(client, callback_query)

@bot.on_callback_query(filters.regex(r"^activate_account_(\d+)$"))
async def activate_account_callback(client, callback_query):
    """
    Заглушка для удаленного функционала активации аккаунта
    """
    await callback_query.answer("Эта функция отключена")
    await back_to_admin_callback(client, callback_query)

@bot.on_callback_query(filters.regex(r"^deactivate_account_(\d+)$"))
async def deactivate_account_callback(client, callback_query):
    """
    Заглушка для удаленного функционала деактивации аккаунта
    """
    await callback_query.answer("Эта функция отключена")
    await back_to_admin_callback(client, callback_query)

@bot.on_callback_query(filters.regex(r"^switch_active_account$"))
async def switch_active_account_callback(client, callback_query):
    """
    Заглушка для удаленного функционала переключения аккаунта
    """
    await callback_query.answer("Эта функция отключена")
    await back_to_admin_callback(client, callback_query)

@bot.on_callback_query(filters.regex(r"^use_account_(\d+)$"))
async def use_account_callback(client, callback_query):
    """
    Заглушка для удаленного функционала выбора аккаунта
    """
    await callback_query.answer("Эта функция отключена")
    await back_to_admin_callback(client, callback_query)

@bot.on_callback_query(filters.regex(r"^toggle_auto_add_(on|off)$"))
async def toggle_auto_add_callback(client, callback_query):
    """
    Включение/выключение автоматического добавления пользователей
    """
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("У тебя не хватает прав для этого 😅")
        return
    
    action = callback_query.data.split("_")[-1]
    new_value = "true" if action == "on" else "false"
    
    set_setting("auto_add_enabled", new_value)
    
    status_text = "✅ Теперь включено!" if action == "on" else "❌ Теперь выключено!"
    
    await callback_query.answer(f"Автодобавление пользователей: {status_text}")
    
    # Возвращаемся в меню настроек или админ-панель
    if "admin" in callback_query.message.text.lower():
        await back_to_admin_callback(client, callback_query)
    else:
        await back_to_settings_callback(client, callback_query)