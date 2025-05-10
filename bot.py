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
from database import init_db, get_session, User, AdminAccount, JoinRequest, encrypt_session, decrypt_session, get_fernet_key, get_setting, set_setting, Base, engine

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
                else:
                    logger.warning("Не удалось определить телефон аккаунта для деактивации")
            except Exception as e:
                logger.error(f"Ошибка при деактивации аккаунта: {e}")
            finally:
                session.close()
                
            return False, "Достигнут лимит добавлений. Попробуйте позже."
            
        except Exception as e:
            # Логируем все другие возможные ошибки для диагностики
            logger.error(f"Необработанная ошибка при добавлении пользователя: {type(e).__name__}: {str(e)}")
            
            # Проверяем, содержит ли сообщение об ошибке строку о приватности
            error_str = str(e).lower()
            if "privacy" in error_str or "приватности" in error_str or "restricted" in error_str:
                logger.warning(f"Обнаружена ошибка приватности из общего исключения: {str(e)}")
                return False, "UserPrivacyRestricted: Пользователь не может быть добавлен из-за настроек приватности"
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
                await callback_query.edit_message_text(
                    "🔒 Из-за ваших настроек приватности мы не смогли добавить вас автоматически.\n\n"
                    "✉️ Мы отправили вам инструкции по изменению настроек приватности в личном сообщении.\n\n"
                    "👆 Проверьте сообщения от бота и следуйте инструкциям.",
                    reply_markup=keyboard
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
                await callback_query.edit_message_text(
                    "⏳ Ваша заявка принята и будет рассмотрена администратором.\n\n"
                    "📋 В данный момент включен режим ручного добавления пользователей.\n"
                    "⌛ Вы будете добавлены после одобрения заявки администратором.",
                    reply_markup=keyboard
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
                                    parse_mode="HTML"
                                )
                                logger.info(f"Администратору {admin_id} отправлено уведомление с фото о заявке пользователя {user_id}")
                            else:
                                await client.send_message(
                                    admin_id,
                                    admin_text,
                                    reply_markup=keyboard,
                                    parse_mode="HTML"
                                )
                                logger.info(f"Администратору {admin_id} отправлено текстовое уведомление о заявке пользователя {user_id}")
                        except Exception as photo_err:
                            logger.error(f"Ошибка при отправке фото профиля: {photo_err}")
                            await client.send_message(
                                admin_id,
                                admin_text,
                                reply_markup=keyboard,
                                parse_mode="HTML"
                            )
                            logger.info(f"Администратору {admin_id} отправлено текстовое уведомление о заявке пользователя {user_id}")
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
        
        keyboard = []
        
        for i, user_db in enumerate(users):
            username = f"@{user_db.username}" if user_db.username else "нет"
            status = "🚫 Заблокирован" if user_db.is_blacklisted else "✅ Активен"
            chat = f"Чат #{1 if user_db.chat_joined == CHAT_ID_1 else 2}" if user_db.chat_joined else "Не в чате"
            
            # Базовая информация о пользователе
            users_text += f"{i+1}. <b>{user_db.first_name} {user_db.last_name or ''}</b> ({username})\n"
            users_text += f"ID: <code>{user_db.user_id}</code> | {status} | {chat}\n\n"
            
            # Добавляем кнопку для просмотра детальной информации
            keyboard.append([types.InlineKeyboardButton(
                f"📋 Подробнее о {user_db.first_name}", 
                callback_data=f"user_details_{user_db.user_id}"
            )])
        
        # Добавляем кнопку назад
        keyboard.append([types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")])
        
        # Создаем клавиатуру с кнопками
        reply_markup = types.InlineKeyboardMarkup(keyboard)
        
        await callback_query.edit_message_text(users_text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при получении списка пользователей: {e}")
        await callback_query.edit_message_text("Произошла ошибка при получении списка пользователей.")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^admin_active_requests$"))
async def admin_active_requests_callback(client, callback_query):
    """
    Список активных заявок через клавиатуру (pending и manual_check)
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
            user = session.query(User).filter_by(user_id=req.user_id).first()
            username = f"@{user.username}" if user and user.username else "нет"
            name = f"{user.first_name} {user.last_name or ''}" if user else "Неизвестный пользователь"
            
            chat_name = "Чат #1" if req.chat_id == CHAT_ID_1 else "Чат #2"
            
            if req.status == "pending":
                status_emoji = "⏳"
                status_text = "Обрабатывается"
            elif req.status == "manual_check":
                status_emoji = "👨‍💼"
                status_text = "Ожидает ручного добавления"
            else:
                status_emoji = "❓"
                status_text = req.status
            
            requests_text += f"ID: {req.user_id}\n"
            requests_text += f"Имя: {name}\n"
            requests_text += f"Username: {username}\n"
            requests_text += f"Чат: {chat_name}\n"
            requests_text += f"Статус: {status_emoji} {status_text}\n"
            requests_text += f"Дата: {req.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        # Добавляем кнопку назад
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("↩️ Назад", callback_data="back_to_admin")]
        ])
        
        await callback_query.edit_message_text(requests_text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении списка активных заявок: {e}")
        await callback_query.edit_message_text("Произошла ошибка при получении списка активных заявок.")
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
            requests_text += f"Дата: {req.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        
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
    
    # Получаем текущий статус автодобавления
    auto_add_enabled = get_setting("auto_add_enabled", "true").lower() == "true"
    auto_add_button_text = "🔴 Отключить автодобавление" if auto_add_enabled else "🟢 Включить автодобавление"
    auto_add_callback = "toggle_auto_add_off" if auto_add_enabled else "toggle_auto_add_on"
    
    keyboard = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
        [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
        [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
        [types.InlineKeyboardButton(auto_add_button_text, callback_data=auto_add_callback)],
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
        
        # Возвращаемся в панель администратора с обновленным статусом
        admin_text = "🔧 Панель администратора:\n\nВыберите действие:"
        
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
            [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
            [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
            [types.InlineKeyboardButton("🔴 Отключить автодобавление", callback_data="toggle_auto_add_off")],
            [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
            [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
            [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
            [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
        ])
        
        await callback_query.edit_message_text(admin_text, reply_markup=keyboard)
        
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
        
        # Возвращаемся в панель администратора с обновленным статусом
        admin_text = "🔧 Панель администратора:\n\nВыберите действие:"
        
        keyboard = types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users")],
            [types.InlineKeyboardButton("📋 Активные заявки", callback_data="admin_active_requests")],
            [types.InlineKeyboardButton("📚 История заявок", callback_data="admin_requests_history")],
            [types.InlineKeyboardButton("🟢 Включить автодобавление", callback_data="toggle_auto_add_on")],
            [types.InlineKeyboardButton("🔒 Заблокировать пользователя", callback_data="admin_block")],
            [types.InlineKeyboardButton("🔓 Разблокировать пользователя", callback_data="admin_unblock")],
            [types.InlineKeyboardButton("➕ Добавить админ-аккаунт", callback_data="admin_add_account")],
            [types.InlineKeyboardButton("➖ Удалить админ-аккаунт", callback_data="admin_remove_account")]
        ])
        
        await callback_query.edit_message_text(admin_text, reply_markup=keyboard)
        
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
    Проверяет наличие заявок, ожидающих ручного добавления, и отправляет уведомления администраторам
    """
    logger.info("Проверка наличия заявок, ожидающих ручного добавления...")
    
    session = get_session()
    try:
        # Получаем все заявки со статусом manual_check
        pending_requests = session.query(JoinRequest).filter_by(status="manual_check").all()
        
        if not pending_requests:
            logger.info("Заявок, ожидающих ручного добавления, не найдено")
            return
        
        logger.info(f"Найдено {len(pending_requests)} заявок, ожидающих ручного добавления")
        
        # Отправляем уведомления администраторам о каждой заявке
        for request in pending_requests:
            try:
                user_id = request.user_id
                chat_id = request.chat_id
                
                # Получаем информацию о пользователе
                user_info = await bot.get_users(user_id)
                
                # Собираем дополнительную информацию о пользователе
                premium_status = "✅" if user_info.is_premium else "❌"
                language_code = user_info.language_code or "неизвестно"
                is_bot = "✅" if user_info.is_bot else "❌"
                is_fake = "✅" if hasattr(user_info, "is_fake") and user_info.is_fake else "❌"
                is_scam = "✅" if hasattr(user_info, "is_scam") and user_info.is_scam else "❌"
                
                # Формируем текст уведомления
                admin_text = (
                    f"📝 [ВОССТАНОВЛЕНО] Заявка ожидает ручного добавления:\n\n"
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
                    f"🕒 Дата создания заявки: {request.created_at.strftime('%d.%m.%Y %H:%M')}\n"
                    f"⚠️ Внимание: эта заявка была обнаружена при перезапуске бота\n"
                )
                
                # Создаем кнопки для добавления пользователя
                chat_name = "Чат #1" if chat_id == CHAT_ID_1 else "Чат #2"
                keyboard = types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton(f"✅ Добавить в {chat_name}", callback_data=f"manual_add_{user_id}_{chat_id}")],
                    [types.InlineKeyboardButton("❌ Отклонить", callback_data=f"manual_reject_{user_id}_{chat_id}")]
                ])
                
                # Отправляем уведомление всем администраторам
                for admin_id in ADMIN_IDS:
                    try:
                        # Пытаемся отправить фото профиля
                        try:
                            profile_photos = await bot.get_profile_photos(user_id, limit=1)
                            if profile_photos.total_count > 0:
                                await bot.send_photo(
                                    admin_id,
                                    profile_photos.photos[0][0].file_id,
                                    caption=admin_text,
                                    reply_markup=keyboard,
                                    parse_mode="HTML"
                                )
                                logger.info(f"Администратору {admin_id} отправлено уведомление с фото о заявке пользователя {user_id}")
                            else:
                                await bot.send_message(
                                    admin_id,
                                    admin_text,
                                    reply_markup=keyboard,
                                    parse_mode="HTML"
                                )
                                logger.info(f"Администратору {admin_id} отправлено текстовое уведомление о заявке пользователя {user_id}")
                        except Exception as photo_err:
                            logger.error(f"Ошибка при отправке фото профиля: {photo_err}")
                            await bot.send_message(
                                admin_id,
                                admin_text,
                                reply_markup=keyboard,
                                parse_mode="HTML"
                            )
                            logger.info(f"Администратору {admin_id} отправлено текстовое уведомление о заявке пользователя {user_id}")
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление администратору {admin_id} о заявке пользователя {user_id}: {e}")
                
                # Отправляем пользователю напоминание
                try:
                    await bot.send_message(
                        user_id,
                        "⏳ Напоминаем, что ваша заявка на вступление в чат находится на рассмотрении у администратора.\n\n"
                        "Вы будете уведомлены, когда администратор примет решение."
                    )
                    logger.info(f"Отправлено напоминание пользователю {user_id} о рассмотрении заявки")
                except Exception as user_msg_err:
                    logger.error(f"Ошибка при отправке напоминания пользователю {user_id}: {user_msg_err}")
                
            except Exception as request_err:
                logger.error(f"Ошибка при обработке заявки {request.id} пользователя {request.user_id}: {request_err}")
        
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
    logger.info("Проверка настройки auto_add_enabled...")
    auto_add_value = get_setting("auto_add_enabled")
    
    if auto_add_value is None:
        logger.info("Настройка auto_add_enabled не найдена. Инициализация со значением 'true'...")
        set_setting("auto_add_enabled", "true")
        logger.info("Настройка auto_add_enabled создана со значением 'true'")
    else:
        logger.info(f"Настройка auto_add_enabled найдена. Текущее значение: '{auto_add_value}'")
    
    # Повторная проверка настройки
    current_value = get_setting("auto_add_enabled", "true")
    logger.info(f"Итоговое значение настройки auto_add_enabled: '{current_value}'")
    
    # Запуск бота
    logger.info("Запуск бота...")
    await bot.start()
    logger.info("Бот запущен")
    
    # Проверка заявок, ожидающих ручного добавления
    await check_pending_manual_requests()
    
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

# Обработчики для ручного добавления и отклонения заявок
@bot.on_callback_query(filters.regex(r"^manual_add_(\d+)_(-?\d+)$"))
async def manual_add_callback(client, callback_query):
    """
    Ручное добавление пользователя администратором из уведомления
    """
    try:
        # Извлекаем ID пользователя и чата из callback_data
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

@bot.on_callback_query(filters.regex(r"^manual_reject_(\d+)_(-?\d+)$"))
async def manual_reject_callback(client, callback_query):
    """
    Отклонение заявки на добавление пользователя из уведомления
    """
    try:
        # Извлекаем ID пользователя и чата из callback_data
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

@bot.on_callback_query(filters.regex(r"^user_details_(\d+)$"))
async def user_details_callback(client, callback_query):
    """
    Просмотр детальной информации о пользователе
    """
    user_id = int(callback_query.data.split('_')[2])
    logger.info(f"Запрошена детальная информация о пользователе {user_id}")
    
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
                        parse_mode="HTML"
                    )
                else:
                    await callback_query.edit_message_text(
                        details_text, 
                        reply_markup=keyboard, 
                        parse_mode="HTML"
                    )
            except Exception as photo_err:
                logger.error(f"Ошибка при отправке фото профиля: {photo_err}")
                await callback_query.edit_message_text(
                    details_text, 
                    reply_markup=keyboard, 
                    parse_mode="HTML"
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
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Ошибка при просмотре информации о пользователе: {e}")
        await callback_query.answer(f"Произошла ошибка: {str(e)[:200]}")
    finally:
        session.close()

@bot.on_callback_query(filters.regex(r"^block_user_(\d+)$"))
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

@bot.on_callback_query(filters.regex(r"^unblock_user_(\d+)$"))
async def unblock_user_callback(client, callback_query):
    """
    Разблокировка пользователя через просмотр профиля
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