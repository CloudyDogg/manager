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
from database import init_db, get_session, User, AdminAccount, JoinRequest, encrypt_session, decrypt_session, get_fernet_key, get_setting, set_setting, Base, engine, RateLimitBlock, check_rate_limit, block_user_rate_limit, unblock_user_rate_limit, get_rate_limited_users, get_next_admin_account
import re

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
        
        # Проверяем, не превышает ли текущий админ лимиты использования
        session = get_session()
        try:
            # Получаем информацию о текущем аккаунте
            if hasattr(active_admin_client, '_phone'):
                admin_account = session.query(AdminAccount).filter_by(phone=active_admin_client._phone).first()
                if admin_account:
                    # Проверяем лимит использования
                    usage_threshold = int(get_setting("admin_usage_threshold", "50"))
                    if admin_account.usage_count >= usage_threshold:
                        logger.warning(f"Аккаунт {admin_account.phone} достиг порогового значения использований ({admin_account.usage_count}/{usage_threshold}). Выполняется ротация.")
                        # Если лимит превышен, останавливаем текущий клиент для выполнения ротации
                        try:
                            await active_admin_client.stop()
                        except Exception as e:
                            logger.error(f"Ошибка при остановке клиента: {e}")
                        active_admin_client = None
                    else:
                        # Если лимит не превышен, просто увеличиваем счетчик
                        admin_account.usage_count += 1
                        admin_account.last_used = datetime.now()
                        session.commit()
                        return active_admin_client
        except Exception as e:
            logger.error(f"Ошибка при проверке лимитов использования: {e}")
        finally:
            session.close()
            
        # Если всё проверили и клиент всё ещё активен, возвращаем его
        if active_admin_client and active_admin_client.is_connected:
            return active_admin_client
    
    # Если клиент существует, но не активен, пытаемся остановить его
    if active_admin_client:
        try:
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
            session_string = session_data.get("session_string")
            
            if not session_string:
                logger.error("Нет строки сессии в данных аккаунта")
                return None
                
            # Создаем клиент из строки сессии
            client = Client(
                name="admin",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
                in_memory=True
            )
            
            # Запускаем клиент
            await client.start()
            logger.info("Клиент администратора запущен успешно")
            
            # Получаем информацию о самом себе для проверки
            me = await client.get_me()
            logger.info(f"Авторизован как: {me.first_name} {me.last_name or ''} (@{me.username or 'нет'})")
            
            # Сохраняем телефон для идентификации аккаунта
            client._phone = admin_account.phone
            
            # Обновляем статистику использования
            admin_account.last_used = datetime.now()
            admin_account.usage_count += 1
            session.commit()
            
            active_admin_client = client
            return client
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
        
        # Добавляем пользователя напрямую
        logger.info(f"Попытка прямого добавления пользователя {user_id} в чат {target_chat.id}")
        
        try:
            # Проверка настроек приватности перед добавлением
            user_info = await admin_client.get_users(user_id)
            logger.info(f"Получена информация о пользователе: {user_info.first_name} {user_info.last_name or ''}")
            
            # Дополнительное логирование для проверки настроек приватности
            logger.info(f"Проверяем возможность добавления пользователя {user_id} в чат {target_chat.id}...")
            
            # Попытка добавления
            result = await admin_client.add_chat_members(
                chat_id=target_chat.id,
                user_ids=user_id
            )
            logger.info(f"Результат вызова add_chat_members: {result}")
            
            # Проверяем, действительно ли пользователь добавлен
            # Добавляем паузу для обновления списка участников
            await asyncio.sleep(1)
            
            # Получаем список участников чата после добавления
            logger.info(f"Проверяем наличие пользователя {user_id} в списке участников чата...")
            chat_members = []
            async for member in admin_client.get_chat_members(target_chat.id):
                chat_members.append(member.user.id)
            
            logger.info(f"Найдено {len(chat_members)} участников чата")
            
            if user_id in chat_members:
                logger.info(f"Пользователь {user_id} найден в списке участников чата")
                logger.info(f"Пользователь {user_id} успешно добавлен в чат (проверено)")
                
                # Отправляем пользователю уведомление об успешном добавлении ТОЛЬКО ЕСЛИ ДОБАВЛЕНИЕ ПРОШЛО УСПЕШНО!
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
                        join_request.approved_by = 0  # 0 означает автоматическое одобрение системой
                        join_request.approved_at = datetime.now()
                        session.commit()
                except Exception as e:
                    logger.error(f"Ошибка при обновлении статуса заявки: {e}")
                finally:
                    session.close()
                
                return True, "Пользователь успешно добавлен в чат"
            else:
                # Если add_chat_members не вызвало исключение, но пользователь не в списке участников,
                # значит, скорее всего, проблема с приватностью не была правильно обработана
                logger.warning(f"Пользователь {user_id} не найден в списке участников чата ({len(chat_members)} участников)")
                logger.warning(f"Вызов add_chat_members завершился без ошибок, но пользователь {user_id} не найден в списке участников")
                
                # Проверяем другие возможные причины
                logger.info(f"Проверяем дополнительные сведения о пользователе {user_id}...")
                logger.info(f"Имя: {user_info.first_name} {user_info.last_name or ''}")
                logger.info(f"Username: @{user_info.username or 'отсутствует'}")
                logger.info(f"Настройки приватности: неизвестно (предполагается ограничение)")
                
                # Явно указываем, что это ошибка приватности
                raise UserPrivacyRestricted("Пользователь не добавлен из-за настроек приватности (не выявлено явно)")
            
        except UserPrivacyRestricted as privacy_error:
            logger.warning(f"ОШИБКА ПРИВАТНОСТИ: {user_id} не может быть добавлен из-за настроек приватности")
            logger.warning(f"Детали ошибки: {str(privacy_error)}")
            logger.warning(f"Тип исключения: {type(privacy_error).__name__}")
            
            # Если не удалось добавить из-за настроек приватности, отправляем ссылку
            chat_info = await admin_client.get_chat(target_chat.id)
            
            # Отправляем только инструкции по настройкам приватности
            await bot.send_message(
                user_id,
                f"🔒 К сожалению, ваши настройки приватности не позволяют добавить вас в чат автоматически.\n\n"
                f"🔍 Чтобы решить эту проблему, вам необходимо изменить настройки конфиденциальности:\n\n"
                f"👉 Откройте настройки Telegram\n"
                f"👉 Перейдите в раздел 'Конфиденциальность'\n" 
                f"👉 Выберите 'Группы и каналы'\n"
                f"👉 Для опции 'Кто может добавить меня в группы' выберите 'Все'\n\n"
                f"📱 Вот как это выглядит (смотрите приложенные картинки):\n"
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
            
            # Отправляем уведомление администраторам о проблеме с приватностью
            try:
                user_info = await bot.get_users(user_id)
                admin_text = (
                    f"⚠️ Пользователь с ограничениями приватности:\n\n"
                    f"👤 {user_info.first_name} {user_info.last_name or ''} (@{user_info.username or 'нет'})\n"
                    f"📱 ID: {user_id}\n\n"
                    f"❌ Невозможно добавить автоматически из-за настроек приватности\n"
                    f"📋 Отправлены инструкции по изменению настроек\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}\n"
                )
                
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, admin_text)
                    except Exception as admin_err:
                        logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {admin_err}")
            except Exception as notify_err:
                logger.error(f"Ошибка при отправке уведомления администраторам: {notify_err}")
            
            # Возвращаем сообщение с явным указанием на проблему с приватностью
            return False, "UserPrivacyRestricted: Пользователь не может быть добавлен из-за настроек приватности"
            
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
                        
                        # Пытаемся найти и активировать следующий доступный аккаунт
                        next_account = get_next_admin_account()
                        if next_account:
                            # Останавливаем текущий клиент
                            global active_admin_client
                            if active_admin_client and active_admin_client.is_connected:
                                try:
                                    await active_admin_client.stop()
                                except Exception as e:
                                    logger.error(f"Ошибка при остановке клиента: {e}")
                            active_admin_client = None
                            
                            # Уведомляем администраторов о ротации
                            for admin_id in ADMIN_IDS:
                                try:
                                    await bot.send_message(
                                        admin_id,
                                        f"🔄 Выполнена автоматическая ротация аккаунтов администратора.\n\n"
                                        f"❌ Аккаунт {admin_account.phone} деактивирован из-за лимита добавления пользователей (PeerFlood).\n"
                                        f"✅ Активирован аккаунт {next_account.phone}\n"
                                        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
                                    )
                                except Exception as admin_err:
                                    logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {admin_err}")
                        else:
                            logger.error("Нет доступных аккаунтов для ротации")
                            for admin_id in ADMIN_IDS:
                                try:
                                    await bot.send_message(
                                        admin_id,
                                        f"⚠️ ВНИМАНИЕ! Все аккаунты администраторов деактивированы.\n\n"
                                        f"❌ Последний активный аккаунт {admin_account.phone} был деактивирован из-за лимита добавления пользователей (PeerFlood).\n"
                                        f"❗ Необходимо добавить новые аккаунты администраторов через панель управления.\n"
                                        f"⏰ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
                                    )
                                except Exception as admin_err:
                                    logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {admin_err}")
                else:
                    logger.warning("Не удалось определить телефон аккаунта для деактивации")
            except Exception as e:
                logger.error(f"Ошибка при деактивации аккаунта: {e}")
            finally:
                session.close()
                
            return False, "Достигнут лимит добавлений. Аккаунт администратора деактивирован и выполнена ротация. Попробуйте повторить запрос."
            
        except Exception as e:
            # Логируем все другие возможные ошибки для диагностики
            logger.error(f"Необработанная ошибка при добавлении пользователя: {type(e).__name__}: {str(e)}")
            
            # Проверяем, содержит ли сообщение об ошибке строку о приватности
            error_str = str(e).lower()
            if "privacy" in error_str or "приватности" in error_str or "restricted" in error_str:
                logger.warning(f"Обнаружена ошибка приватности из общего исключения: {str(e)}")
                return False, "UserPrivacyRestricted: Пользователь не может быть добавлен из-за настроек приватности"
            elif "user_not_mutual_contact" in error_str or "mutual contact" in error_str:
                # Обработка ошибки USER_NOT_MUTUAL_CONTACT - нужно добавить пользователя в контакты
                logger.warning(f"Обнаружена ошибка USER_NOT_MUTUAL_CONTACT: {str(e)}")
                
                try:
                    # Получаем информацию о пользователе
                    user_info = await admin_client.get_users(user_id)
                    logger.info(f"Получена информация о пользователе: {user_info.first_name} {user_info.last_name or ''}")
                    
                    # Пытаемся добавить пользователя в контакты
                    await admin_client.add_contact(
                        user_id=user_id,
                        first_name=user_info.first_name,
                        last_name=user_info.last_name or ""
                    )
                    logger.info(f"Пользователь {user_id} добавлен в контакты администратора")
                    
                    # Получаем информацию об администраторе для отправки контакта пользователю
                    admin_info = await admin_client.get_me()
                    logger.info(f"Информация об администраторе: {admin_info.first_name} {admin_info.last_name or ''}")
                    
                    # Отправляем пользователю контакт администратора
                    await bot.send_contact(
                        user_id,
                        phone_number=admin_info.phone_number,
                        first_name=admin_info.first_name,
                        last_name=admin_info.last_name or ""
                    )
                    
                    # Отправляем инструкцию пользователю
                    await bot.send_message(
                        user_id,
                        f"❗ Для того чтобы мы могли добавить вас в чат, необходимо:\n\n"
                        f"1️⃣ Добавьте этот контакт в свою телефонную книгу\n"
                        f"2️⃣ Подождите несколько секунд\n"
                        f"3️⃣ Вернитесь в бот и повторите попытку вступления в чат\n\n"
                        f"Это необходимо из-за ограничений Telegram при добавлении пользователей в групповые чаты."
                    )
                    
                    # Обновляем статус заявки
                    session = get_session()
                    try:
                        join_request = session.query(JoinRequest).filter_by(user_id=user_id, chat_id=chat_id, status="pending").first()
                        if join_request:
                            join_request.status = "contact_sent"
                            session.commit()
                            logger.info(f"Статус заявки для пользователя {user_id} обновлен на 'contact_sent'")
                    except Exception as db_err:
                        logger.error(f"Ошибка при обновлении статуса заявки: {db_err}")
                    finally:
                        session.close()
                    
                    # Отправляем уведомление администраторам
                    admin_text = (
                        f"👥 Пользователь требует добавления в контакты:\n\n"
                        f"👤 {user_info.first_name} {user_info.last_name or ''} (@{user_info.username or 'нет'})\n"
                        f"📱 ID: {user_id}\n\n"
                        f"ℹ️ Пользователь добавлен в контакты администратора\n"
                        f"📧 Отправлен контакт администратора и инструкции пользователю\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}\n"
                    )
                    
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id, admin_text)
                        except Exception as admin_err:
                            logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {admin_err}")
                    
                    return False, "Требуется взаимное добавление в контакты"
                except Exception as contact_err:
                    logger.error(f"Ошибка при добавлении пользователя в контакты: {contact_err}")
                    return False, f"Не удалось добавить пользователя в контакты: {str(contact_err)}"
            else:
                return False, f"Не удалось добавить пользователя: {str(e)}"
        
    except Exception as e:
        logger.error(f"Основная ошибка при добавлении пользователя: {type(e).__name__}: {str(e)}")
        return False, f"Ошибка при добавлении пользователя: {str(e)}"

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    """
    Обработка команды /start
    """
    try:
        user_id = message.from_user.id
        logger.info(f"Пользователь {user_id} запустил бота")
        
        # Получаем настраиваемые тексты из базы данных (с значениями по умолчанию)
        button_join_text = get_setting("button_join_text", "🚀 Вступить в чат")
        button_info_text = get_setting("button_info_text", "🔍 Что у нас тут?")
        button_support_text = get_setting("button_support_text", "🆘 Нужна помощь")
        welcome_message = get_setting("welcome_message", f"👋 Привет, {message.from_user.first_name}! Рад тебя видеть! 😎\n\nЯ помогу тебе попасть в наш крутой чат. Что будем делать?")
        
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
        await message.reply("Произошла ошибка. Пожалуйста, попробуйте позже.")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^show_chats$"))
async def show_chats_callback(client, callback_query):
    """
    Показывает меню выбора чатов
    """
    # Получаем настраиваемый текст для заголовка
    chat_select_text = get_setting("chat_select_text", "🤔 В какой чат ты хочешь попасть? Выбирай, не стесняйся!")
    
    # Создаем клавиатуру для выбора чатов
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🔥 Чат #1", callback_data="select_chat_1")],
        [types.InlineKeyboardButton("🌟 Чат #2", callback_data="select_chat_2")],
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
        "🔥 *Наши супер-чаты*\n\n"
        "У нас есть классные чаты на разные темы - выбирай, что по душе! 😉\n\n"
        "*Чат #1*: Общение на самые разные темы, куча интересных людей и бурные дискуссии\n"
        "*Чат #2*: Тусовка для профессионалов, где можно обсудить серьезные вопросы\n\n"
        "Ждем тебя! 🎉"
    )
    
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🚀 Вступить в чат", callback_data="show_chats")],
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
        await callback_query.answer("Этот чат временно недоступен")
        return
    
    session = get_session()
    try:
        # Проверяем, не в черном ли списке пользователь
        user = session.query(User).filter_by(user_id=user_id).first()
        if user and user.is_blacklisted:
            await callback_query.answer("Вы не можете быть добавлены в чат")
            return
        
        # Проверяем ограничение на количество заявок
        rate_limited, current_count = check_rate_limit(user_id, limit=5, period_minutes=1)
        if rate_limited:
            # Если пользователь превысил лимит, блокируем его и выводим сообщение
            block_user_rate_limit(user_id)
            logger.warning(f"Пользователь {user_id} превысил лимит заявок (текущее количество: {current_count}) и был заблокирован")
            
            # Информируем пользователя о блокировке
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")]
            ])
            
            await callback_query.edit_message_text(
                "⚠️ Ой, похоже ты слишком активно нажимаешь кнопки! 😅\n\n"
                "⛔ Мы временно приостановили твои запросы. Сделай глубокий вдох и немного подожди!\n\n"
                "⏳ Скоро все заработает, или напиши нашей поддержке - они быстро помогут! 👌",
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
            "⏳ Магия происходит... Добавляем тебя в чат! ✨\n\n"
            "Секундочку, уже почти готово! 🧙‍♂️"
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
                    "🔒 Из-за ваших настроек приватности мы не смогли добавить вас автоматически.\n\n"
                    "✉️ Мы отправили вам инструкции по изменению настроек приватности в личном сообщении.\n\n"
                    "👆 Проверьте сообщения от бота и следуйте инструкциям."
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
                    "⏳ Ваша заявка принята и будет рассмотрена администратором.\n\n"
                    "📋 В данный момент включен режим ручного добавления пользователей.\n"
                    "⌛ Вы будете добавлены после одобрения заявки администратором."
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
            elif "mutual" in message.lower() or "contact" in message.lower() or "user_not_mutual_contact" in message.lower():
                # Если ошибка связана с необходимостью взаимных контактов
                join_request.status = "contact_sent"
                session.commit()
                logger.info(f"Обновлен статус заявки на 'contact_sent' для пользователя {user_id}")
                
                # Информируем пользователя о необходимости добавить контакт
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("🔄 Повторить попытку", callback_data=f"select_chat_{chat_num}")],
                    [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")]
                ])
                
                # Пользовательский текст сообщения
                contact_message = get_setting("contact_message",
                    "👤 Для добавления в чат вам необходимо сохранить контакт администратора.\n\n"
                    "1️⃣ Сохраните отправленный вам контакт в своей телефонной книге\n"
                    "2️⃣ Подождите несколько секунд\n"
                    "3️⃣ Нажмите кнопку 'Повторить попытку'\n\n"
                    "❗ Это необходимо из-за ограничений Telegram при добавлении в групповые чаты."
                )
                
                await callback_query.edit_message_text(
                    contact_message,
                    reply_markup=keyboard,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                logger.info(f"Отправлено уведомление пользователю {user_id} о необходимости добавления контакта")
            else:
                # Для других ошибок обновляем статус и показываем сообщение об ошибке
                join_request.status = "rejected"
                session.commit()
                
                # Получаем настраиваемый текст сообщения об ошибке
                error_message_template = get_setting("error_message_template", 
                    "❌ Не удалось добавить вас в чат: {error}\n\nПопробуйте позже или обратитесь в поддержку."
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
    button_join_text = get_setting("button_join_text", "🚀 Вступить в чат")
    button_info_text = get_setting("button_info_text", "🔍 Что у нас тут?")
    button_support_text = get_setting("button_support_text", "🆘 Нужна помощь")
    welcome_message = get_setting("welcome_message", "👋 Привет! Рад тебя видеть! 😎\n\nЯ помогу тебе попасть в наш крутой чат. Что будем делать?")
    
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
    Показывает информацию о поддержке
    """
    # Получаем настраиваемый текст поддержки
    support_text = get_setting("support_text",
        "📲 Возникли вопросы? Не проблема!\n\n"
        "Напиши нашему админу, и мы поможем решить любые сложности 😉\n\n"
        "Контакт администратора: @admin_username"
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
    admin_text = "🔧 Панель администратора:\n\n"
    admin_text += "Выберите действие:"
    
    # Получаем текущий статус автодобавления
    auto_add_enabled = get_setting("auto_add_enabled", "true").lower() == "true"
    auto_add_button_text = "🔴 Отключить автодобавление" if auto_add_enabled else "🟢 Включить автодобавление"
    auto_add_callback = "toggle_auto_add_off" if auto_add_enabled else "toggle_auto_add_on"
    
    # Создаем инлайн-клавиатуру для админа
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
        [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
        [types.InlineKeyboardButton(auto_add_button_text, callback_data=auto_add_callback)],
        [types.InlineKeyboardButton("✏️ Настройка текста интерфейса", callback_data="ui_text_settings")],
        [types.InlineKeyboardButton("🔄 Управление аккаунтами админа", callback_data="admin_accounts_management")],
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
    Список активных заявок без лишних кнопок
    """
    session = get_session()
    try:
        # Получаем активные заявки (pending и manual_check)
        requests = session.query(JoinRequest).filter(
            JoinRequest.status.in_(["pending", "manual_check"])
        ).order_by(JoinRequest.created_at.desc()).limit(20).all()
        
        if not requests:
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
            ])
            await callback_query.edit_message_text("Список активных заявок пуст.", reply_markup=keyboard)
            return
        
        requests_text = "📋 Активные заявки (ожидают рассмотрения):\n\n"
        
        for req in requests:
            try:
                user = session.query(User).filter_by(user_id=req.user_id).first()
                username = f"@{user.username}" if user and user.username else "нет"
                name = f"{user.first_name} {user.last_name or ''}" if user else "Неизвестный пользователь"
                
                # Получаем расширенную информацию через API
                user_info = await client.get_users(req.user_id)
                
                chat_name = "Чат #1" if req.chat_id == CHAT_ID_1 else "Чат #2"
                
                # Статусы заявок
                if req.status == "pending":
                    status_emoji = "⏳"
                    status_text = "Обрабатывается"
                elif req.status == "manual_check":
                    status_emoji = "👨‍💼"
                    status_text = "Ожидает ручного добавления"
                else:
                    status_emoji = "❓"
                    status_text = req.status
                
                # Получаем дополнительную информацию
                premium_status = "✅" if hasattr(user_info, "is_premium") and user_info.is_premium else "❌"
                language_code = user_info.language_code or "неизвестно"
                is_bot = "✅" if hasattr(user_info, "is_bot") and user_info.is_bot else "❌"
                is_fake = "✅" if hasattr(user_info, "is_fake") and user_info.is_fake else "❌"
                is_scam = "✅" if hasattr(user_info, "is_scam") and user_info.is_scam else "❌"
                
                # Форматируем информацию о заявке
                requests_text += f"👤 <b>{name}</b> ({username})\n"
                requests_text += f"ID: <code>{req.user_id}</code>\n"
                requests_text += f"Чат: {chat_name}\n"
                requests_text += f"Статус: {status_emoji} {status_text}\n"
                requests_text += f"Язык: {language_code} | Premium: {premium_status}\n"
                requests_text += f"Бот: {is_bot} | Фейк: {is_fake} | Скам: {is_scam}\n"
                requests_text += f"Дата: {req.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                
            except Exception as user_err:
                logger.error(f"Ошибка при получении информации о пользователе {req.user_id}: {user_err}")
                requests_text += f"ID: {req.user_id}\n"
                requests_text += f"Ошибка получения данных: {str(user_err)[:100]}\n\n"
        
        # Добавляем кнопку назад
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(requests_text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка при получении списка активных заявок: {e}")
        await callback_query.edit_message_text(f"Произошла ошибка при получении списка активных заявок: {str(e)}")
    finally:
        session.close()

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
    Запрос номера телефона для добавления аккаунта администратора
    """
    # Устанавливаем состояние "ожидание номера телефона"
    user_id = callback_query.from_user.id
    set_setting(f"waiting_admin_phone_{user_id}", "true")
    
    await callback_query.edit_message_text(
        "📱 <b>Добавление аккаунта администратора</b>\n\n"
        "1️⃣ Введите номер телефона в международном формате (например, <code>+79123456789</code>):\n\n"
        "<i>Этот номер будет использоваться для добавления пользователей в чаты.</i>",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Отмена", callback_data="back_to_admin")]
        ]),
        parse_mode=enums.ParseMode.HTML
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
    admin_text = "🔧 Панель администратора:\n\nВыберите действие:"
    
    # Получаем текущий статус автодобавления
    auto_add_enabled = get_setting("auto_add_enabled", "true").lower() == "true"
    auto_add_button_text = "🔴 Отключить автодобавление" if auto_add_enabled else "🟢 Включить автодобавление"
    auto_add_callback = "toggle_auto_add_off" if auto_add_enabled else "toggle_auto_add_on"
    
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
        [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
        [types.InlineKeyboardButton(auto_add_button_text, callback_data=auto_add_callback)],
        [types.InlineKeyboardButton("✏️ Настройка текста интерфейса", callback_data="ui_text_settings")],
        [types.InlineKeyboardButton("🔄 Управление аккаунтами админа", callback_data="admin_accounts_management")],
        [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
        [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
        [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
        [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
    ])
    
    await callback_query.edit_message_text(admin_text, reply_markup=keyboard)

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
    
    # Настройка сообщения о необходимости добавления контакта
    contact_message_value = get_setting("contact_message")
    if contact_message_value is None:
        logger.info("Настройка contact_message не найдена. Инициализация со значением по умолчанию...")
        default_contact_message = (
            "👤 Для добавления в чат вам необходимо сохранить контакт администратора.\n\n"
            "1️⃣ Сохраните отправленный вам контакт в своей телефонной книге\n"
            "2️⃣ Подождите несколько секунд\n"
            "3️⃣ Нажмите кнопку 'Повторить попытку'\n\n"
            "❗ Это необходимо из-за ограничений Telegram при добавлении в групповые чаты."
        )
        set_setting("contact_message", default_contact_message)
    
    # Логируем финальные значения
    logger.info(f"auto_add_enabled: {get_setting('auto_add_enabled', 'true')}")
    logger.info(f"notify_on_startup: {get_setting('notify_on_startup', 'true')}")
    logger.info(f"contact_message: настроено")
    
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
        ui_text_menu = "✏️ Настройка текстов интерфейса:\n\n"
        ui_text_menu += "Выберите, какой текст вы хотите изменить:\n\n"
        ui_text_menu += "📝 <b>Тексты кнопок:</b>\n"
        
        # Создаем клавиатуру с кнопками для редактирования различных текстов
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("✏️ Кнопка 'Хочу в чат'", callback_data="edit_button_join_text")],
            [types.InlineKeyboardButton("✏️ Кнопка 'Узнать подробности'", callback_data="edit_button_info_text")],
            [types.InlineKeyboardButton("✏️ Кнопка 'Поддержка'", callback_data="edit_button_support_text")],
            [types.InlineKeyboardButton("✏️ Приветственное сообщение", callback_data="edit_welcome_message")],
            [types.InlineKeyboardButton("───────────────────", callback_data="preview_no_action")],
            [types.InlineKeyboardButton("📄 <b>Содержимое сообщений:</b>", callback_data="preview_no_action")],
            [types.InlineKeyboardButton("✏️ Информация о чатах", callback_data="edit_info_text")],
            [types.InlineKeyboardButton("✏️ Текст выбора чата", callback_data="edit_chat_select_text")],
            [types.InlineKeyboardButton("✏️ Текст поддержки", callback_data="edit_support_text")],
            [types.InlineKeyboardButton("✏️ Сообщение о приватности", callback_data="edit_privacy_message")],
            [types.InlineKeyboardButton("✏️ Сообщение о ручной проверке", callback_data="edit_manual_check_message")],
            [types.InlineKeyboardButton("✏️ Сообщение о контактах", callback_data="edit_contact_message")],
            [types.InlineKeyboardButton("✏️ Шаблон сообщения об ошибке", callback_data="edit_error_message_template")],
            [types.InlineKeyboardButton("↩️ Назад в настройки", callback_data="back_to_settings")]
        ])
        
        await callback_query.edit_message_text(ui_text_menu, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка при отображении меню настройки текстов интерфейса: {e}")
        await callback_query.answer("Произошла ошибка при загрузке настроек текстов")

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
            "contact_message": "текста сообщения о контактах",
            "error_message_template": "шаблона сообщения об ошибке"
        }
        
        # Словарь значений по умолчанию
        default_values = {
            "button_join_text": "🚀 Вступить в чат",
            "button_info_text": "🔍 Что у нас тут?",
            "button_support_text": "🆘 Нужна помощь",
            "welcome_message": "👋 Привет! Рад тебя видеть! 😎\n\nЯ помогу тебе попасть в наш крутой чат. Что будем делать?",
            "chat_select_text": "🤔 В какой чат ты хочешь попасть? Выбирай, не стесняйся!",
            "info_text": "🔥 *Наши супер-чаты*\n\nУ нас есть классные чаты на разные темы - выбирай, что по душе! 😉\n\n*Чат #1*: Общение на самые разные темы, куча интересных людей и бурные дискуссии\n*Чат #2*: Тусовка для профессионалов, где можно обсудить серьезные вопросы\n\nЖдем тебя! 🎉",
            "support_text": "📲 Возникли вопросы? Не проблема!\n\nНапиши нашему админу, и мы поможем решить любые сложности 😉",
            "privacy_message": "🔒 Ой-ой! Похоже, твои настройки приватности не дают нам пригласить тебя в чат автоматически 😔\n\n✨ Давай это исправим за пару кликов:\n\n👉 Открой настройки Telegram\n👉 Зайди в раздел 'Конфиденциальность'\n👉 Выбери 'Группы и каналы'\n👉 Для опции 'Кто может добавить меня в группы' поставь 'Все'\n\n📱 Вот как это выглядит (смотри картинки):\n",
            "manual_check_message": "⏳ Твоя заявка принята! Скоро наши админы её рассмотрят 🧐\n\n📋 Сейчас у нас включен режим ручного добавления.\n⌛ Как только админ одобрит - сразу добавим тебя! Обещаем, это быстро 😉",
            "contact_message": "👋 Для добавления в чат нам нужно стать контактами в Telegram!\n\n1️⃣ Сохрани отправленный контакт в телефоне\n2️⃣ Подожди пару секунд ⌛\n3️⃣ Нажми 'Повторить попытку'\n\n🤫 Это маленькая хитрость из-за ограничений Telegram для групповых чатов 😉",
            "error_message_template": "😱 Упс! Что-то пошло не так: {error}\n\nДавай попробуем позже или обратимся к нашей поддержке? Они точно помогут! 👍"
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

# Обработчик для получения нового текста настроек
@bot.on_message(filters.private & filters.user(ADMIN_IDS) & filters.text)
async def handle_new_ui_text(client, message):
    """
    Обработка нового текста для пользовательского интерфейса
    """
    user_id = message.from_user.id
    waiting_status = get_setting(f"waiting_text_{user_id}", "false")
    
    # Проверяем, не обрабатывается ли сообщение в функции handle_admin_input
    if get_setting(f"waiting_admin_phone_{user_id}", "false").lower() == "true" or \
       get_setting(f"waiting_admin_code_{user_id}", "false").lower() == "true":
        # Это сообщение для добавления админ-аккаунта, игнорируем здесь
        return
    
    # Если пользователь не в состоянии ожидания нового текста, игнорируем
    if waiting_status.lower() != "true":
        return
    
    try:
        # Получаем имя настройки, которую редактируем
        setting_name = get_setting(f"temp_editing_{user_id}", "")
        
        if not setting_name:
            await message.reply("Ошибка: не найдена редактируемая настройка.")
            return
        
        # Сохраняем новый текст
        new_text = message.text
        set_setting(setting_name, new_text)
        
        # Сбрасываем состояние ожидания
        set_setting(f"waiting_text_{user_id}", "false")
        set_setting(f"temp_editing_{user_id}", "")
        
        # Отправляем сообщение об успехе
        success_message = f"✅ Текст успешно изменен!\n\nНовый текст:\n<code>{new_text}</code>"
        
        # Создаем кнопки для возврата в меню настроек
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад к настройкам текстов", callback_data="ui_text_settings")],
            [types.InlineKeyboardButton("🔍 Посмотреть изменения", callback_data="preview_ui_changes")]
        ])
        
        await message.reply(success_message, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка при сохранении нового текста: {e}")
        await message.reply(f"Произошла ошибка при сохранении текста: {str(e)}")

@bot.on_callback_query(filters.regex(r"^preview_ui_changes$"))
async def preview_ui_changes_callback(client, callback_query):
    """
    Предварительный просмотр изменений интерфейса
    """
    try:
        # Получаем настраиваемые тексты из базы данных (с значениями по умолчанию)
        button_join_text = get_setting("button_join_text", "🚀 Вступить в чат")
        button_info_text = get_setting("button_info_text", "🔍 Что у нас тут?")
        button_support_text = get_setting("button_support_text", "🆘 Нужна помощь")
        welcome_message = get_setting("welcome_message", "👋 Привет! Рад тебя видеть! 😎\n\nЯ помогу тебе попасть в наш крутой чат. Что будем делать?")
        
        # Создаем клавиатуру с новыми кнопками (preview)
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton(button_join_text, callback_data="preview_no_action")],
            [types.InlineKeyboardButton(button_info_text, callback_data="preview_no_action")],
            [types.InlineKeyboardButton(button_support_text, callback_data="preview_no_action")],
            [types.InlineKeyboardButton("↩️ Назад к настройкам текстов", callback_data="ui_text_settings")]
        ])
        
        preview_text = "🔍 Предварительный просмотр интерфейса\n\n"
        preview_text += welcome_message
        
        await callback_query.edit_message_text(preview_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при предварительном просмотре интерфейса: {e}")
        await callback_query.answer("Произошла ошибка при предварительном просмотре")

@bot.on_callback_query(filters.regex(r"^preview_no_action$"))
async def preview_no_action_callback(client, callback_query):
    """
    Заглушка для кнопок в режиме предпросмотра
    """
    await callback_query.answer("Это только предварительный просмотр")

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
    Список пользователей, заблокированных за превышение лимита заявок
    """
    # Возвращаем пользователя в админ-меню вместо отображения списка
    # так как функционал был удален
    await back_to_admin_callback(client, callback_query)
    return
    
    try:
        # Получаем список заблокированных пользователей
        blocked_users = get_rate_limited_users()
        
        if not blocked_users:
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
            ])
            await callback_query.edit_message_text(
                "🔍 Нет пользователей с ограничениями по лимиту заявок.",
                reply_markup=keyboard
            )
            return
        
        # Формируем сообщение со списком пользователей
        users_text = "⚠️ Пользователи с ограничениями по лимиту заявок:\n\n"
        
        for i, block in enumerate(blocked_users):
            try:
                # Получаем информацию о пользователе
                user_info = await client.get_users(block.user_id)
                
                username = f"@{user_info.username}" if user_info.username else "нет"
                
                # Форматируем информацию
                users_text += f"{i+1}. <b>{user_info.first_name} {user_info.last_name or ''}</b> ({username})\n"
                users_text += f"ID: <code>{block.user_id}</code>\n"
                users_text += f"Заблокирован: {block.blocked_at.strftime('%d.%m.%Y %H:%M')}\n"
                users_text += f"Причина: {block.reason}\n\n"
                
            except Exception as user_err:
                logger.error(f"Ошибка при получении информации о пользователе {block.user_id}: {user_err}")
                users_text += f"{i+1}. ID: <code>{block.user_id}</code>\n"
                users_text += f"Заблокирован: {block.blocked_at.strftime('%d.%m.%Y %H:%M')}\n"
                users_text += f"Ошибка получения данных: {str(user_err)[:100]}\n\n"
        
        # Создаем клавиатуру с кнопками разблокировки
        buttons = []
        for block in blocked_users:
            buttons.append([types.InlineKeyboardButton(
                f"🔓 Разблокировать ID: {block.user_id}",
                callback_data=f"unblock_rate_limit_{block.user_id}"
            )])
        
        # Добавляем кнопку возврата
        buttons.append([types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")])
        
        keyboard = types.InlineKeyboardMarkup(buttons)
        
        # Отправляем сообщение
        await callback_query.edit_message_text(
            users_text, 
            reply_markup=keyboard, 
            parse_mode=enums.ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении списка пользователей с ограничениями: {e}")
        
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(
            f"❌ Произошла ошибка при получении списка пользователей с ограничениями: {str(e)[:200]}",
            reply_markup=keyboard
        )

async def create_admin_session(phone_number, code, user_id):
    """
    Создание сессии администратора
    """
    logger.info(f"Создание сессии для номера {phone_number}...")
    
    # Временный идентификатор сессии
    session_name = f"temp_session_{phone_number}"
    
    # Сохраняем код для текущего пользователя
    set_setting(f"admin_auth_code_{user_id}", code)
    
    # Создаем клиент Pyrogram
    client = Client(
        session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        phone_number=phone_number,
        in_memory=True
    )
    
    try:
        # Функция для обработки кода подтверждения
        async def phone_code_callback(*args, **kwargs):
            return get_setting(f"admin_auth_code_{user_id}")
        
        # Устанавливаем обработчик
        client.phone_code_callback = phone_code_callback
        
        # Запускаем клиент (используем сохраненный код)
        await client.start()
        logger.info(f"Авторизация успешна для номера {phone_number}")
        
        # Получаем данные сессии
        session_string = await client.export_session_string()
        
        # Создаем структуру данных сессии в формате JSON
        session_data = {
            "session_string": session_string,
            "phone_number": phone_number
        }
        
        # Шифруем данные сессии
        encrypted_data = encrypt_session(session_data)
        
        # Сохраняем в базу данных
        session = get_session()
        
        # Проверяем, существует ли уже такой аккаунт
        existing_account = session.query(AdminAccount).filter_by(phone=phone_number).first()
        if existing_account:
            existing_account.session_data = encrypted_data
            existing_account.active = True
            session.commit()
            logger.info(f"Обновлена сессия для аккаунта {phone_number}")
        else:
            new_account = AdminAccount(
                phone=phone_number,
                active=True,
                session_data=encrypted_data
            )
            session.add(new_account)
            session.commit()
            logger.info(f"Добавлен новый аккаунт администратора {phone_number}")
            
        # Останавливаем клиент
        await client.stop()
        
        # Удаляем временные данные
        set_setting(f"admin_auth_code_{user_id}", "")
        set_setting(f"waiting_admin_phone_{user_id}", "false")
        set_setting(f"waiting_admin_code_{user_id}", "false")
        set_setting(f"admin_phone_{user_id}", "")
        
        logger.info(f"Сессия для номера {phone_number} успешно создана и сохранена.")
        return True, "Аккаунт администратора успешно добавлен"
    except Exception as e:
        logger.error(f"Ошибка при создании сессии для номера {phone_number}: {e}")
        return False, f"Ошибка при создании сессии: {str(e)}"

@bot.on_message(filters.private & filters.user(ADMIN_IDS) & filters.text)
async def handle_admin_input(client, message):
    """
    Обработка ввода от администратора для добавления аккаунта
    """
    user_id = message.from_user.id
    waiting_phone = get_setting(f"waiting_admin_phone_{user_id}", "false")
    waiting_code = get_setting(f"waiting_admin_code_{user_id}", "false")
    
    # Обработка номера телефона
    if waiting_phone.lower() == "true":
        phone_number = message.text.strip()
        
        # Проверяем формат номера
        if not phone_number.startswith("+"):
            await message.reply("❌ Неверный формат номера. Номер должен начинаться с '+' и кода страны.\n\nПопробуйте еще раз:")
            return
        
        # Сохраняем номер телефона
        set_setting(f"admin_phone_{user_id}", phone_number)
        # Меняем состояние на ожидание кода
        set_setting(f"waiting_admin_phone_{user_id}", "false")
        set_setting(f"waiting_admin_code_{user_id}", "true")
        
        # Отправляем инструкцию для ввода кода
        await message.reply(
            "2️⃣ Телефон принят. На указанный номер отправлен код подтверждения.\n\n"
            "Введите полученный код:",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_admin_add")]
            ])
        )
        return
    
    # Обработка кода подтверждения
    elif waiting_code.lower() == "true":
        auth_code = message.text.strip()
        phone_number = get_setting(f"admin_phone_{user_id}", "")
        
        if not phone_number:
            await message.reply("❌ Произошла ошибка: номер телефона не найден.\n\nПопробуйте заново начать процесс добавления.")
            return
        
        # Отправляем сообщение о процессе
        status_message = await message.reply("⏳ Выполняется авторизация и создание сессии...")
        
        # Создаем сессию
        success, result_message = await create_admin_session(phone_number, auth_code, user_id)
        
        if success:
            # Обновляем статусное сообщение
            await status_message.edit_text(
                f"✅ {result_message}\n\n"
                f"Телефон: {phone_number}\n\n"
                "Теперь этот аккаунт может использоваться для добавления пользователей в чаты.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("🔙 Вернуться в панель администратора", callback_data="back_to_admin")]
                ])
            )
        else:
            # Обновляем статусное сообщение
            await status_message.edit_text(
                f"❌ {result_message}\n\n"
                "Попробуйте снова или используйте другой номер телефона.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("🔄 Попробовать снова", callback_data="admin_add_account")],
                    [types.InlineKeyboardButton("🔙 Вернуться в панель администратора", callback_data="back_to_admin")]
                ])
            )
        
        # Сбрасываем состояния
        set_setting(f"waiting_admin_phone_{user_id}", "false")
        set_setting(f"waiting_admin_code_{user_id}", "false")
        set_setting(f"admin_phone_{user_id}", "")
        return

@bot.on_callback_query(filters.regex(r"^cancel_admin_add$"))
async def cancel_admin_add_callback(client, callback_query):
    """
    Отмена процесса добавления аккаунта администратора
    """
    user_id = callback_query.from_user.id
    
    # Сбрасываем все состояния
    set_setting(f"waiting_admin_phone_{user_id}", "false")
    set_setting(f"waiting_admin_code_{user_id}", "false")
    set_setting(f"admin_phone_{user_id}", "")
    
    await callback_query.edit_message_text(
        "❌ Процесс добавления аккаунта отменен.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("🔙 Вернуться в панель администратора", callback_data="back_to_admin")]
        ])
    )

# Добавить новый обработчик для управления аккаунтами админа
@bot.on_callback_query(filters.regex(r"^admin_accounts_management$"))
async def admin_accounts_management_callback(client, callback_query):
    """
    Управление аккаунтами администратора (просмотр, ротация, активация/деактивация)
    """
    try:
        session = get_session()
        
        # Получаем все аккаунты администраторов
        accounts = session.query(AdminAccount).order_by(AdminAccount.active.desc(), AdminAccount.usage_count).all()
        
        if not accounts:
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("➕ Добавить аккаунт", callback_data="admin_add_account")],
                [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
            ])
            await callback_query.edit_message_text("Список аккаунтов администраторов пуст.", reply_markup=keyboard)
            return
        
        # Получаем текущий порог использования для автоматической ротации
        usage_threshold = int(get_setting("admin_usage_threshold", "50"))
        
        accounts_text = f"👥 <b>Управление аккаунтами администраторов:</b>\n\n"
        accounts_text += f"ℹ️ <b>Текущий порог авторотации:</b> {usage_threshold} использований\n\n"
        
        # Форматируем список аккаунтов
        for i, account in enumerate(accounts):
            # Статус аккаунта
            status = "✅ Активен" if account.active else "❌ Деактивирован"
            
            # Процент использования относительно порога
            if usage_threshold > 0:
                usage_percent = (account.usage_count / usage_threshold) * 100
                usage_status = f"{account.usage_count}/{usage_threshold} ({usage_percent:.1f}%)"
            else:
                usage_status = f"{account.usage_count}"
            
            # Информация о последнем использовании
            last_used = account.last_used.strftime('%d.%m.%Y %H:%M') if account.last_used else "никогда"
            
            accounts_text += f"{i+1}. <b>Телефон:</b> <code>{account.phone}</code>\n"
            accounts_text += f"   <b>Статус:</b> {status}\n"
            accounts_text += f"   <b>Использований:</b> {usage_status}\n"
            accounts_text += f"   <b>Последнее использование:</b> {last_used}\n\n"
        
        # Создаем кнопки для управления
        buttons = []
        
        # Кнопки для изменения порога автоматической ротации
        buttons.append([
            types.InlineKeyboardButton("⬇️ Уменьшить порог", callback_data="decrease_threshold"),
            types.InlineKeyboardButton("⬆️ Увеличить порог", callback_data="increase_threshold")
        ])
        
        # Кнопка для принудительной ротации
        buttons.append([types.InlineKeyboardButton("🔄 Выполнить ротацию сейчас", callback_data="force_rotation")])
        
        # Кнопки для активации/деактивации аккаунтов
        for account in accounts:
            action = "Деактивировать" if account.active else "Активировать"
            buttons.append([
                types.InlineKeyboardButton(
                    f"{'❌' if account.active else '✅'} {action} {account.phone}", 
                    callback_data=f"toggle_account_{account.id}_{1 if account.active else 0}"
                )
            ])
        
        # Кнопка для сброса счетчика использований
        buttons.append([types.InlineKeyboardButton("🔄 Сбросить все счетчики", callback_data="reset_usage_counters")])
        
        # Кнопка возврата
        buttons.append([types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")])
        
        keyboard = types.InlineKeyboardMarkup(buttons)
        
        await callback_query.edit_message_text(accounts_text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка при управлении аккаунтами администраторов: {e}")
        await callback_query.edit_message_text(
            "Произошла ошибка при загрузке списка аккаунтов.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
            ])
        )
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^increase_threshold$"))
async def increase_threshold_callback(client, callback_query):
    """
    Увеличение порога автоматической ротации
    """
    try:
        # Получаем текущий порог
        current_threshold = int(get_setting("admin_usage_threshold", "50"))
        
        # Увеличиваем на 10
        new_threshold = current_threshold + 10
        
        # Сохраняем новое значение
        set_setting("admin_usage_threshold", str(new_threshold))
        
        await callback_query.answer(f"✅ Порог увеличен до {new_threshold}")
        
        # Возвращаемся в меню управления аккаунтами
        await admin_accounts_management_callback(client, callback_query)
    except Exception as e:
        logger.error(f"Ошибка при увеличении порога ротации: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@bot.on_callback_query(filters.regex(r"^decrease_threshold$"))
async def decrease_threshold_callback(client, callback_query):
    """
    Уменьшение порога автоматической ротации
    """
    try:
        # Получаем текущий порог
        current_threshold = int(get_setting("admin_usage_threshold", "50"))
        
        # Уменьшаем на 10, но не меньше 10
        new_threshold = max(10, current_threshold - 10)
        
        # Сохраняем новое значение
        set_setting("admin_usage_threshold", str(new_threshold))
        
        await callback_query.answer(f"✅ Порог уменьшен до {new_threshold}")
        
        # Возвращаемся в меню управления аккаунтами
        await admin_accounts_management_callback(client, callback_query)
    except Exception as e:
        logger.error(f"Ошибка при уменьшении порога ротации: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@bot.on_callback_query(filters.regex(r"^force_rotation$"))
async def force_rotation_callback(client, callback_query):
    """
    Принудительная ротация аккаунтов администратора
    """
    try:
        # Получаем текущий активный аккаунт
        global active_admin_client
        current_account_id = None
        
        if active_admin_client and hasattr(active_admin_client, '_phone'):
            session = get_session()
            try:
                current_account = session.query(AdminAccount).filter_by(phone=active_admin_client._phone).first()
                if current_account:
                    current_account_id = current_account.id
            finally:
                session.close()
        
        # Получаем следующий аккаунт для ротации
        next_account = get_next_admin_account(current_account_id)
        
        if not next_account:
            await callback_query.answer("❌ Нет доступных аккаунтов для ротации")
            return
        
        # Останавливаем текущий клиент, если он активен
        if active_admin_client and active_admin_client.is_connected:
            try:
                await active_admin_client.stop()
            except Exception as e:
                logger.error(f"Ошибка при остановке клиента: {e}")
        
        # Сбрасываем клиент
        active_admin_client = None
        
        # Уведомляем о ротации
        await callback_query.answer(f"✅ Ротация выполнена. Активирован аккаунт {next_account.phone}")
        
        # Логирование
        logger.info(f"Выполнена ручная ротация на аккаунт {next_account.phone}")
        
        # Обновляем интерфейс
        await admin_accounts_management_callback(client, callback_query)
    except Exception as e:
        logger.error(f"Ошибка при ручной ротации аккаунтов: {e}")
        await callback_query.answer("❌ Произошла ошибка при ротации")

@bot.on_callback_query(filters.regex(r"^toggle_account_(\d+)_([01])$"))
async def toggle_account_callback(client, callback_query):
    """
    Активация/деактивация аккаунта администратора
    """
    try:
        # Получаем ID аккаунта и текущий статус из callback_data
        match = re.match(r"^toggle_account_(\d+)_([01])$", callback_query.data)
        if not match:
            await callback_query.answer("❌ Некорректные данные")
            return
            
        account_id = int(match.group(1))
        is_active = int(match.group(2)) == 1  # 1 означает, что аккаунт активен
        
        session = get_session()
        try:
            # Получаем аккаунт по ID
            account = session.query(AdminAccount).filter_by(id=account_id).first()
            if not account:
                await callback_query.answer("❌ Аккаунт не найден")
                return
                
            # Изменяем статус аккаунта
            account.active = not is_active
            session.commit()
            
            # Проверяем, не деактивируем ли мы текущий активный аккаунт
            if is_active and active_admin_client and hasattr(active_admin_client, '_phone') and active_admin_client._phone == account.phone:
                # Останавливаем текущий клиент
                try:
                    await active_admin_client.stop()
                except Exception as e:
                    logger.error(f"Ошибка при остановке клиента: {e}")
                    
                # Сбрасываем клиент
                global active_admin_client
                active_admin_client = None
                
                logger.info(f"Деактивирован текущий активный аккаунт {account.phone}")
            
            await callback_query.answer(f"✅ Статус аккаунта {account.phone} изменен")
            
            # Обновляем интерфейс
            await admin_accounts_management_callback(client, callback_query)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Ошибка при изменении статуса аккаунта: {e}")
        await callback_query.answer("❌ Произошла ошибка")

@bot.on_callback_query(filters.regex(r"^reset_usage_counters$"))
async def reset_usage_counters_callback(client, callback_query):
    """
    Сброс счетчиков использования всех аккаунтов
    """
    try:
        session = get_session()
        try:
            # Сбрасываем счетчики для всех аккаунтов
            session.query(AdminAccount).update({AdminAccount.usage_count: 0})
            session.commit()
            
            await callback_query.answer("✅ Счетчики использования сброшены")
            
            # Обновляем интерфейс
            await admin_accounts_management_callback(client, callback_query)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Ошибка при сбросе счетчиков использования: {e}")
        await callback_query.answer("❌ Произошла ошибка")