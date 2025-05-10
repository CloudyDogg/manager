import os
import logging
import asyncio
import base64
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pyrogram import Client, filters, types, errors, raw
from pyrogram.raw import functions
from pyrogram.errors import UserAlreadyParticipant, UserPrivacyRestricted, PeerFlood, InviteHashExpired
from cryptography.fernet import Fernet
import json
from database import init_db, get_session, User, AdminAccount, JoinRequest, encrypt_session, decrypt_session, get_fernet_key

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
        
        try:
            # Добавляем пользователя напрямую
            logger.info(f"Попытка прямого добавления пользователя {user_id} в чат {target_chat.id}")
            
            try:
                # Используем метод add_chat_members для добавления пользователя
                await admin_client.add_chat_members(
                    chat_id=target_chat.id,
                    user_ids=user_id
                )
                
                logger.info(f"Пользователь {user_id} успешно добавлен в чат")
                
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
                        session.commit()
                except Exception as e:
                    logger.error(f"Ошибка при обновлении статуса заявки: {e}")
                finally:
                    session.close()
                
                return True, "Пользователь успешно добавлен в чат"
                
            except Exception as e:
                # Проверяем, является ли ошибка связанной с приватностью
                if isinstance(e, UserPrivacyRestricted) or (hasattr(e, '__class__') and e.__class__.__name__ == 'UserPrivacyRestricted'):
                    logger.warning(f"ОШИБКА ПРИВАТНОСТИ: {user_id} не может быть добавлен из-за настроек приватности")
                    logger.warning(f"Детали ошибки: {str(e)}")
                    logger.warning(f"Тип исключения: {type(e).__name__}")
                    
                    # ВАЖНО: НЕ ОТПРАВЛЯЕМ сообщение об успешном добавлении, если произошла ошибка UserPrivacyRestricted
                    
                    # Если не удалось добавить из-за настроек приватности, отправляем ссылку
                    chat_info = await admin_client.get_chat(target_chat.id)
                    invite_link = await admin_client.create_chat_invite_link(
                        chat_id=target_chat.id,
                        creates_join_request=False
                    )
                    invite_link_url = invite_link.invite_link
                    
                    # Отправляем дружелюбное сообщение с инструкциями по настройкам приватности
                    await bot.send_message(
                        user_id,
                        f"🔒 К сожалению, ваши настройки приватности не позволяют добавить вас в чат автоматически.\n\n"
                        f"🔍 Не переживайте! Есть два простых варианта решения:\n\n"
                        f"1️⃣ Используйте эту ссылку для входа в {chat_name}:\n"
                        f"{invite_link_url}\n\n"
                        f"2️⃣ Измените настройки конфиденциальности, чтобы в будущем вас можно было добавлять в группы:\n"
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
                admin_account = session.query(AdminAccount).filter_by(phone=admin_client._phone).first()
                if admin_account:
                    admin_account.active = False
                    session.commit()
                    logger.warning(f"Аккаунт {admin_account.phone} деактивирован из-за лимита добавлений")
            except Exception as e:
                logger.error(f"Ошибка при деактивации аккаунта: {e}")
            finally:
                session.close()
                
            return False, "Достигнут лимит добавлений. Попробуйте позже."
            
        except Exception as e:
            # Логируем все другие возможные ошибки для диагностики
            logger.error(f"Необработанная ошибка при добавлении пользователя: {type(e).__name__}: {str(e)}")
            return False, f"Не удалось добавить пользователя: {str(e)}"
            
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
        
        # Формируем приветственное сообщение
        welcome_text = f"👋 Привет, {message.from_user.first_name}!\n\n"
        welcome_text += "Я бот для добавления в закрытые чаты. Выберите чат, в который хотите вступить:"
        
        # Создаем клавиатуру для выбора чатов
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("Чат #1", callback_data="select_chat_1")],
            [types.InlineKeyboardButton("Чат #2", callback_data="select_chat_2")],
            [types.InlineKeyboardButton("📞 Поддержка", callback_data="support")]
        ])
        
        await message.reply(welcome_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")
        await message.reply("Произошла ошибка. Пожалуйста, попробуйте позже.")
    finally:
        session.close()

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
            "⏳ Обрабатываем вашу заявку...\n\n"
            "Пожалуйста, подождите несколько секунд."
        )
        
        # Добавляем пользователя
        success, message = await add_user_to_chat(user_id, chat_id)
        
        if success:
            # Если пользователь успешно добавлен, не нужно повторно отправлять сообщение
            # так как оно уже отправлено в функции add_user_to_chat
            # Только отмечаем в БД, что пользователь добавлен
            join_request.status = "approved"
            user.chat_joined = chat_id
            session.commit()
            
            # Получаем подробную информацию о пользователе
            user_info = await client.get_users(user_id)
            
            # Собираем подробную информацию о пользователе для администраторов
            user_details = f"🔔 Новый пользователь в чате #{chat_num}:\n\n"
            user_details += f"📋 Основная информация:\n"
            user_details += f"ID: {user_id}\n"
            user_details += f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
            user_details += f"Username: @{user_info.username or 'отсутствует'}\n\n"
            
            # Дополнительная информация
            user_details += f"📱 Дополнительно:\n"
            user_details += f"Язык: {user_info.language_code or 'неизвестно'}\n"
            user_details += f"Премиум: {'✅' if hasattr(user_info, 'is_premium') and user_info.is_premium else '❌'}\n"
            user_details += f"Бот: {'✅' if user_info.is_bot else '❌'}\n"
            if hasattr(user_info, 'is_fake'):
                user_details += f"Фейк: {'✅' if user_info.is_fake else '❌'}\n"
            if hasattr(user_info, 'is_scam'):
                user_details += f"Скрыт: {'✅' if user_info.is_scam else '❌'}\n\n"
            
            # Статистика
            user_details += f"📊 Статистика:\n"
            user_details += f"Зарегистрирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            user_details += f"Статус заявки: ✅ Одобрена (автоматически)\n"
            user_details += f"Метод добавления: Прямое добавление администратором\n"
            
            # Уведомляем администраторов с подробной информацией
            for admin_id in ADMIN_IDS:
                try:
                    # Отправляем сообщение БЕЗ parse_mode
                    await client.send_message(
                        admin_id,
                        user_details
                    )
                    
                    # Отправляем фото профиля, если оно доступно
                    try:
                        user_photos = await client.get_user_profile_photos(user_id, limit=1)
                        if user_photos and user_photos.total_count > 0:
                            await client.send_photo(
                                admin_id,
                                user_photos.photos[0][0].file_id,
                                caption=f"Фото профиля пользователя {user_info.first_name} {user_info.last_name or ''}"
                            )
                    except Exception as e:
                        logger.error(f"Не удалось отправить фото профиля: {e}")
                        
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
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
                
                # Получаем аккаунт администратора
                admin_client = await get_admin_client()
                if not admin_client:
                    logger.error("Нет доступного администратора для создания ссылки")
                    await callback_query.edit_message_text(
                        "❌ Произошла ошибка при создании ссылки. Пожалуйста, попробуйте позже."
                    )
                    return
                
                # Получаем список чатов для админа
                dialogs = []
                async for dialog in admin_client.get_dialogs():
                    dialogs.append(dialog)
                
                # Ищем чат с заголовком "test"
                target_chat = None
                for dialog in dialogs:
                    if dialog.chat.title == "test":
                        target_chat = dialog.chat
                        logger.info(f"Найден чат test для создания ссылки (ID: {dialog.chat.id})")
                        break
                
                if not target_chat:
                    logger.error("Не удалось найти чат test для создания ссылки")
                    await callback_query.edit_message_text(
                        "❌ Произошла ошибка при создании ссылки. Пожалуйста, попробуйте позже."
                    )
                    return
                
                # Создаем ссылку для чата
                try:
                    invite_link = await admin_client.create_chat_invite_link(
                        chat_id=target_chat.id,
                        creates_join_request=False
                    )
                    invite_link_url = invite_link.invite_link
                    logger.info(f"Успешно создана ссылка: {invite_link_url}")
                except Exception as e:
                    logger.error(f"Ошибка при создании ссылки: {e}")
                    invite_link_url = CHAT_LINK_1 if chat_id == CHAT_ID_1 else CHAT_LINK_2
                    logger.info(f"Используем запасную ссылку: {invite_link_url}")
                
                # Отправляем ссылку и инструкции
                try:
                    # Определяем имя чата для этого блока кода
                    chat_name = "основной чат" if chat_id == CHAT_ID_1 else "второй чат"
                    
                    await client.send_message(
                        user_id,
                        f"🔒 К сожалению, ваши настройки приватности не позволяют добавить вас в чат автоматически.\n\n"
                        f"🔍 Не переживайте! Есть два простых варианта решения:\n\n"
                        f"1️⃣ Используйте эту ссылку для входа в {chat_name}:\n"
                        f"{invite_link_url}\n\n"
                        f"2️⃣ Измените настройки конфиденциальности, чтобы в будущем вас можно было добавлять в группы:\n"
                        f"👉 Откройте настройки Telegram\n"
                        f"👉 Перейдите в раздел 'Конфиденциальность'\n" 
                        f"👉 Выберите 'Группы и каналы'\n"
                        f"👉 Для опции 'Кто может добавить меня в группы' выберите 'Все'\n\n"
                        f"📱 Вот как это выглядит (смотрите приложенные картинки):\n"
                    )
                    
                    logger.info("Ссылка и инструкции отправлены пользователю")
                    
                    # Отправляем изображения с инструкциями
                    logger.info("Отправляю картинки с инструкциями")
                    
                    await client.send_photo(
                        user_id,
                        "screen/1.jpg",
                        caption="1. Откройте настройки и выберите 'Конфиденциальность'"
                    )
                    logger.info("Изображение 1 отправлено")
                    
                    await client.send_photo(
                        user_id,
                        "screen/2.jpg",
                        caption="2. Выберите 'Группы и каналы'"
                    )
                    logger.info("Изображение 2 отправлено")
                    
                    await client.send_photo(
                        user_id,
                        "screen/3.jpg",
                        caption="3. Установите 'Кто может добавить меня в группы' на 'Все'"
                    )
                    logger.info("Изображение 3 отправлено")
                    
                    await client.send_message(
                        user_id,
                        "🎉 После изменения настроек вернитесь сюда и повторите попытку! Мы сможем добавить вас автоматически."
                    )
                    logger.info("Финальное сообщение отправлено")
                    
                except Exception as e:
                    logger.error(f"Ошибка при отправке инструкций пользователю: {e}")
                
                # Дополнительное сообщение в чате с кнопкой возврата в меню
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")]
                ])
                await callback_query.edit_message_text(
                    "🔒 Из-за ваших настроек приватности мы не смогли добавить вас автоматически.\n\n"
                    "✉️ Мы отправили вам инструкции и ссылку-приглашение в личном сообщении.\n\n"
                    "👆 Проверьте сообщения от бота и следуйте инструкциям.",
                    reply_markup=keyboard
                )
                
                # Отправляем администраторам информацию о пользователе
                user_info = await client.get_users(user_id)
                admin_text = (
                    f"⚠️ Новая заявка (требуется ссылка-приглашение):\n\n"
                    f"📋 Информация о пользователе:\n"
                    f"ID: {user_id}\n"
                    f"Имя: {user_info.first_name} {user_info.last_name or ''}\n"
                    f"Username: @{user_info.username or 'отсутствует'}\n\n"
                    f"🔒 Причина: Ограничения приватности пользователя\n"
                    f"📧 Действие: Отправлена ссылка-приглашение и инструкции\n"
                    f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                )
                
                for admin_id in ADMIN_IDS:
                    try:
                        await client.send_message(admin_id, admin_text)
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
            else:
                # Для других ошибок обновляем статус и показываем сообщение об ошибке
                join_request.status = "rejected"
                session.commit()
                
                error_text = f"❌ Не удалось добавить вас в чат: {message}\n\nПопробуйте позже или обратитесь в поддержку."
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")],
                    [types.InlineKeyboardButton("📞 Поддержка", callback_data="support")]
                ])
                await callback_query.edit_message_text(error_text, reply_markup=keyboard)
                
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
    welcome_text = f"👋 Выберите чат, в который хотите вступить:"
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Чат #1", callback_data="select_chat_1")],
        [types.InlineKeyboardButton("Чат #2", callback_data="select_chat_2")],
        [types.InlineKeyboardButton("📞 Поддержка", callback_data="support")]
    ])
    await callback_query.edit_message_text(welcome_text, reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^support$"))
async def support_callback(client, callback_query):
    """
    Обработка запроса в поддержку
    """
    support_text = "📞 Для обращения в поддержку напишите личное сообщение администратору."
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("↩️ Вернуться в меню", callback_data="back_to_menu")]
    ])
    await callback_query.edit_message_text(support_text, reply_markup=keyboard)

# Команды администратора
@bot.on_message(filters.command("admin") & filters.private & filters.user(ADMIN_IDS))
async def admin_command(client, message):
    """
    Панель администратора
    """
    admin_text = "🔧 Панель администратора:\n\n"
    admin_text += "Выберите действие:"
    
    # Создаем инлайн-клавиатуру для админа
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [types.InlineKeyboardButton("📝 Список заявок", callback_data="admin_requests")],
        [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
        [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
        [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
        [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
    ])
    
    await message.reply(admin_text, reply_markup=keyboard)

@bot.on_callback_query(filters.regex(r"^admin_users$"))
async def admin_users_callback(client, callback_query):
    """
    Список пользователей через клавиатуру
    """
    session = get_session()
    try:
        users = session.query(User).order_by(User.registration_date.desc()).limit(20).all()
        
        if not users:
            await callback_query.edit_message_text("Список пользователей пуст.")
            return
        
        users_text = "👥 Список последних пользователей:\n\n"
        for user in users:
            username = f"@{user.username}" if user.username else "нет"
            status = "🚫 Заблокирован" if user.is_blacklisted else "✅ Активен"
            chat = f"Чат #{1 if user.chat_joined == CHAT_ID_1 else 2}" if user.chat_joined else "Не в чате"
            
            users_text += f"ID: {user.user_id}\n"
            users_text += f"Имя: {user.first_name} {user.last_name or ''}\n"
            users_text += f"Username: {username}\n"
            users_text += f"Статус: {status}\n"
            users_text += f"Чат: {chat}\n"
            users_text += f"Регистрация: {user.registration_date.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        # Добавляем кнопку назад
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(users_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении списка пользователей: {e}")
        await callback_query.edit_message_text("Произошла ошибка при получении списка пользователей.")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^admin_requests$"))
async def admin_requests_callback(client, callback_query):
    """
    Список заявок через клавиатуру
    """
    session = get_session()
    try:
        requests = session.query(JoinRequest).order_by(JoinRequest.created_at.desc()).limit(20).all()
        
        if not requests:
            keyboard = types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
            ])
            await callback_query.edit_message_text("Список заявок пуст.", reply_markup=keyboard)
            return
        
        requests_text = "📝 Список последних заявок:\n\n"
        
        for req in requests:
            user = session.query(User).filter_by(user_id=req.user_id).first()
            username = f"@{user.username}" if user and user.username else "нет"
            name = f"{user.first_name} {user.last_name or ''}" if user else "Неизвестный пользователь"
            
            chat_name = "Чат #1" if req.chat_id == CHAT_ID_1 else "Чат #2"
            status_emoji = "✅" if req.status == "approved" else "❌" if req.status == "rejected" else "⏳"
            
            requests_text += f"ID: {req.user_id}\n"
            requests_text += f"Имя: {name}\n"
            requests_text += f"Username: {username}\n"
            requests_text += f"Чат: {chat_name}\n"
            requests_text += f"Статус: {status_emoji} {req.status}\n"
            requests_text += f"Дата: {req.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        # Добавляем кнопку назад
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(requests_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении списка заявок: {e}")
        await callback_query.edit_message_text("Произошла ошибка при получении списка заявок.")
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
    Информация о добавлении аккаунта
    """
    await callback_query.edit_message_text(
        "⚙️ Для добавления аккаунта администратора необходимо создать JSON-сессию Pyrogram.\n\n"
        "Запустите скрипт session_creator.py для авторизации нового аккаунта.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
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
    admin_text = "🔧 Панель администратора:\n\nВыберите действие:"
    
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [types.InlineKeyboardButton("📝 Список заявок", callback_data="admin_requests")],
        [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
        [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
        [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
        [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
    ])
    
    await callback_query.edit_message_text(admin_text, reply_markup=keyboard)

async def startup():
    """
    Функция запуска
    """
    # Инициализация базы данных
    init_db()
    
    # Запуск бота
    await bot.start()
    logger.info("Бот запущен")
    
    # Бесконечный цикл для поддержания работы бота
    while True:
        await asyncio.sleep(3600)  # Ждем 1 час

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