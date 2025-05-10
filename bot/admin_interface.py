import logging
from aiogram import Bot, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import BOT_TOKEN, CHAT_ID_1, CHAT_ID_2, CHATS, MESSAGES, ADMIN_IDS
from db_manager import DBManager
from chat_manager import chat_manager
from session_manager import session_manager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)

# Избегаем циклического импорта
_dp = None  # Создаем переменную для хранения диспетчера

# Функция для установки диспетчера извне
def set_dispatcher(dispatcher):
    global _dp
    _dp = dispatcher
    logger.info("Диспетчер успешно установлен в admin_interface")

# Определение состояний для FSM (конечного автомата)
class AdminStates(StatesGroup):
    main_menu = State()
    viewing_settings = State()
    editing_settings = State()
    viewing_users = State()
    viewing_requests = State()
    managing_accounts = State()
    adding_account = State()
    confirming_code = State()
    editing_chat_info = State()  # Добавлено для редактирования информации о чате
    editing_welcome = State()  # Добавлено для редактирования приветствия
    editing_join_mode = State()  # Добавлено для редактирования режима вступления
    searching_user = State()  # Добавлено для поиска пользователя
    entering_2fa = State()  # Добавлено для ввода пароля 2FA

# Функции для проверки, является ли пользователь администратором
def is_admin(user_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором
    
    Args:
        user_id: ID пользователя для проверки
        
    Returns:
        True если пользователь является администратором, иначе False
    """
    # Приводим user_id к int, если это строка
    if isinstance(user_id, str):
        try:
            user_id = int(user_id)
        except ValueError:
            logger.error(f"Невозможно преобразовать user_id '{user_id}' в целое число")
            return False
    
    logger.info(f"Проверка прав администратора для ID: {user_id}, тип: {type(user_id)}")
    
    # Проверяем, является ли пользователь системным администратором
    # Приведем ADMIN_IDS к списку строк и int для сравнения
    admin_ids_int = [int(admin_id) if isinstance(admin_id, str) else admin_id for admin_id in ADMIN_IDS]
    admin_ids_str = [str(admin_id) for admin_id in ADMIN_IDS]
    
    # Проверяем оба варианта - строковый и числовой
    is_system_admin = user_id in admin_ids_int or str(user_id) in admin_ids_str
    
    if is_system_admin:
        logger.info(f"ID {user_id} является системным администратором")
        return True
    
    # Проверяем, является ли пользователь администратором из базы данных
    try:
        session = DBManager.get_session()
        from db_manager import User
        
        # Проверяем и по числовому, и по строковому представлению
        user = session.query(User).filter(
            (User.user_id == user_id) | (User.user_id == str(user_id))
        ).first()
        
        if user and user.is_admin:
            logger.info(f"ID {user_id} является администратором согласно БД")
            return True
    except Exception as e:
        logger.error(f"Ошибка при проверке прав администратора для ID {user_id}: {e}")
    finally:
        session.close()
    
    logger.info(f"ID {user_id} не является администратором")
    return False

# Функции для создания клавиатур
def get_admin_main_keyboard():
    """Создает клавиатуру для главного меню администратора"""
    buttons = []
    
    # Создаем первый ряд
    row1 = [
        InlineKeyboardButton(text=MESSAGES["settings_button"], callback_data="admin:settings"),
        InlineKeyboardButton(text=MESSAGES["stats_button"], callback_data="admin:stats")
    ]
    
    # Создаем второй ряд
    row2 = [
        InlineKeyboardButton(text=MESSAGES["users_button"], callback_data="admin:users"),
        InlineKeyboardButton(text=MESSAGES["pending_button"], callback_data="admin:pending")
    ]
    
    # Создаем третий ряд
    row3 = [
        InlineKeyboardButton(text=MESSAGES["accounts_button"], callback_data="admin:accounts")
    ]
    
    # Добавляем ряды в список кнопок
    buttons.append(row1)
    buttons.append(row2)
    buttons.append(row3)
    
    # Создаем клавиатуру с указанием inline_keyboard
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_settings_keyboard():
    """Создает клавиатуру для меню настроек"""
    buttons = []
    
    # Добавляем кнопки для настройки чатов
    row1 = [
        InlineKeyboardButton(
            text=f"Настроить {CHATS[CHAT_ID_1]['name']} 🗣️",
            callback_data=f"admin:settings_chat:{CHAT_ID_1}"
        )
    ]
    
    row2 = [
        InlineKeyboardButton(
            text=f"Настроить {CHATS[CHAT_ID_2]['name']} 💬",
            callback_data=f"admin:settings_chat:{CHAT_ID_2}"
        )
    ]
    
    # Добавляем кнопку настроек бота
    row3 = [
        InlineKeyboardButton(
            text="Настройки бота 🤖",
            callback_data="admin:settings_bot"
        )
    ]
    
    # Добавляем кнопку назад
    row4 = [
        InlineKeyboardButton(
            text=MESSAGES["back_button"],
            callback_data="admin:back_to_main"
        )
    ]
    
    # Добавляем все ряды в список кнопок
    buttons.append(row1)
    buttons.append(row2)
    buttons.append(row3)
    buttons.append(row4)
    
    # Создаем клавиатуру
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_chat_settings_keyboard(chat_id):
    """Создает клавиатуру для настройки конкретного чата"""
    buttons = []
    
    # Добавляем кнопки настроек чата
    row1 = [
        InlineKeyboardButton(
            text="Изменить название/описание 📝",
            callback_data=f"admin:edit_chat_info:{chat_id}"
        )
    ]
    
    row2 = [
        InlineKeyboardButton(
            text="Настроить приветствие 👋",
            callback_data=f"admin:edit_welcome:{chat_id}"
        )
    ]
    
    row3 = [
        InlineKeyboardButton(
            text="Режим добавления 🔐",
            callback_data=f"admin:edit_join_mode:{chat_id}"
        )
    ]
    
    row4 = [
        InlineKeyboardButton(
            text="Выбрать аккаунты для добавления 👨‍💼",
            callback_data=f"admin:select_accounts:{chat_id}"
        )
    ]
    
    # Добавляем кнопку назад
    row5 = [
        InlineKeyboardButton(
            text=MESSAGES["back_button"],
            callback_data="admin:settings"
        )
    ]
    
    # Добавляем все ряды
    buttons.append(row1)
    buttons.append(row2)
    buttons.append(row3)
    buttons.append(row4)
    buttons.append(row5)
    
    # Создаем клавиатуру
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_users_keyboard():
    """Создает клавиатуру для меню пользователей"""
    buttons = []
    
    # Добавляем кнопки для просмотра пользователей по чатам
    row1 = [
        InlineKeyboardButton(
            text=f"Пользователи {CHATS[CHAT_ID_1]['name']} 🗣️",
            callback_data=f"admin:users_chat:{CHAT_ID_1}"
        )
    ]
    
    row2 = [
        InlineKeyboardButton(
            text=f"Пользователи {CHATS[CHAT_ID_2]['name']} 💬",
            callback_data=f"admin:users_chat:{CHAT_ID_2}"
        )
    ]
    
    # Добавляем кнопку поиска пользователя
    row3 = [
        InlineKeyboardButton(
            text="Поиск пользователя 🔍",
            callback_data="admin:search_user"
        )
    ]
    
    # Добавляем кнопку назад
    row4 = [
        InlineKeyboardButton(
            text=MESSAGES["back_button"],
            callback_data="admin:back_to_main"
        )
    ]
    
    # Добавляем все ряды
    buttons.append(row1)
    buttons.append(row2)
    buttons.append(row3)
    buttons.append(row4)
    
    # Создаем клавиатуру
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_accounts_keyboard():
    """Создает клавиатуру для управления аккаунтами"""
    buttons = []
    
    # Добавляем кнопки управления аккаунтами
    row1 = [
        InlineKeyboardButton(
            text="Добавить аккаунт ➕",
            callback_data="admin:add_account"
        )
    ]
    
    row2 = [
        InlineKeyboardButton(
            text="Обновить сессию 🔄",
            callback_data="admin:refresh_session"
        )
    ]
    
    row3 = [
        InlineKeyboardButton(
            text="Статус аккаунтов 🔍",
            callback_data="admin:account_status"
        )
    ]
    
    row4 = [
        InlineKeyboardButton(
            text="Сбросить сессии 🔐",
            callback_data="admin:reset_sessions"
        )
    ]
    
    # Добавляем кнопку назад
    row5 = [
        InlineKeyboardButton(
            text=MESSAGES["back_button"],
            callback_data="admin:back_to_main"
        )
    ]
    
    # Добавляем все ряды
    buttons.append(row1)
    buttons.append(row2)
    buttons.append(row3)
    buttons.append(row4)
    buttons.append(row5)
    
    # Создаем клавиатуру
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_stats_keyboard():
    """Создает клавиатуру для меню статистики"""
    buttons = []
    
    # Добавляем кнопки статистики
    row1 = [
        InlineKeyboardButton(
            text="Общая статистика 📈",
            callback_data="admin:stats_general"
        )
    ]
    
    row2 = [
        InlineKeyboardButton(
            text=f"Статистика {CHATS[CHAT_ID_1]['name']} 🗣️",
            callback_data=f"admin:stats_chat:{CHAT_ID_1}"
        ),
        InlineKeyboardButton(
            text=f"Статистика {CHATS[CHAT_ID_2]['name']} 💬",
            callback_data=f"admin:stats_chat:{CHAT_ID_2}"
        )
    ]
    
    row3 = [
        InlineKeyboardButton(
            text="По дням 📅",
            callback_data="admin:stats_daily"
        ),
        InlineKeyboardButton(
            text="По неделям 📆",
            callback_data="admin:stats_weekly"
        )
    ]
    
    # Добавляем кнопку назад
    row4 = [
        InlineKeyboardButton(
            text=MESSAGES["back_button"],
            callback_data="admin:back_to_main"
        )
    ]
    
    # Добавляем все ряды
    buttons.append(row1)
    buttons.append(row2)
    buttons.append(row3)
    buttons.append(row4)
    
    # Создаем клавиатуру
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_request_action_keyboard(request_id):
    """Создает клавиатуру для действий с заявкой"""
    buttons = []
    
    # Добавляем кнопки действий
    row1 = [
        InlineKeyboardButton(
            text="Одобрить ✅",
            callback_data=f"admin:approve_request:{request_id}"
        ),
        InlineKeyboardButton(
            text="Отклонить ❌",
            callback_data=f"admin:reject_request:{request_id}"
        )
    ]
    
    # Добавляем кнопку назад
    row2 = [
        InlineKeyboardButton(
            text=MESSAGES["back_button"],
            callback_data="admin:pending"
        )
    ]
    
    # Добавляем все ряды
    buttons.append(row1)
    buttons.append(row2)
    
    # Создаем клавиатуру
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Обработчик команды /admin
async def cmd_admin(message: types.Message, state: FSMContext):
    """Обработчик команды /admin"""
    # Проверяем права администратора еще раз
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав администратора.")
        return
    
    logger.info(f"Пользователь {message.from_user.id} авторизован как администратор")
    
    # Устанавливаем состояние основного меню админа
    await state.set_state(AdminStates.main_menu)
    
    # Отправляем приветствие и админскую клавиатуру
    await message.answer(
        "👑 Панель администратора\n\nВыберите действие:",
        reply_markup=get_admin_main_keyboard()
    )

# ЕДИНЫЙ ОБРАБОТЧИК ДЛЯ ВСЕХ АДМИНСКИХ КОЛБЭКОВ
async def process_admin_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Единый обработчик всех callback_query для админского интерфейса"""
    # Получаем callback_data
    callback_data = callback_query.data
    current_state = await state.get_state()
    
    logger.info(f"Получен админский callback: {callback_data}, текущее состояние: {current_state}")
    
    # Проверяем права администратора
    if not is_admin(callback_query.from_user.id):
        logger.warning(f"Пользователь {callback_query.from_user.id} попытался использовать админский callback без прав")
        await callback_query.answer("У вас нет прав администратора", show_alert=True)
        return
        
    try:
        # Ответим на callback_query сразу, чтобы избежать ошибки "query_id invalid"
        await callback_query.answer()
        
        # Маршрутизация на основе callback_data
        if callback_data == "admin:settings":
            logger.info(f"Обработка callback admin:settings для пользователя {callback_query.from_user.id}")
            await process_admin_settings(callback_query, state)
            
        elif callback_data == "admin:stats":
            logger.info(f"Обработка callback admin:stats для пользователя {callback_query.from_user.id}")
            await process_admin_stats(callback_query, state)
            
        elif callback_data == "admin:users":
            logger.info(f"Обработка callback admin:users для пользователя {callback_query.from_user.id}")
            await process_admin_users(callback_query, state)
            
        elif callback_data == "admin:pending":
            logger.info(f"Обработка callback admin:pending для пользователя {callback_query.from_user.id}")
            await process_admin_pending(callback_query, state)
            
        elif callback_data == "admin:accounts":
            logger.info(f"Обработка callback admin:accounts для пользователя {callback_query.from_user.id}")
            await process_admin_accounts(callback_query, state)
            
        elif callback_data == "admin:back_to_main":
            logger.info(f"Обработка callback admin:back_to_main для пользователя {callback_query.from_user.id}")
            await process_admin_back_to_main(callback_query, state)
            
        elif callback_data.startswith("admin:settings_chat:"):
            chat_id = int(callback_data.split(":")[2])
            logger.info(f"Обработка callback admin:settings_chat:{chat_id} для пользователя {callback_query.from_user.id}")
            await process_admin_settings_chat(callback_query, state)
            
        elif callback_data.startswith("admin:approve_request:"):
            request_id = int(callback_data.split(":")[2])
            logger.info(f"Обработка callback admin:approve_request:{request_id} для пользователя {callback_query.from_user.id}")
            await process_admin_approve_request(callback_query, state)
            
        elif callback_data.startswith("admin:reject_request:"):
            request_id = int(callback_data.split(":")[2])
            logger.info(f"Обработка callback admin:reject_request:{request_id} для пользователя {callback_query.from_user.id}")
            await process_admin_reject_request(callback_query, state)
            
        elif callback_data == "admin:add_account":
            logger.info(f"Обработка callback admin:add_account для пользователя {callback_query.from_user.id}")
            await process_admin_add_account(callback_query, state)
            
        elif callback_data == "admin:refresh_session":
            logger.info(f"Обработка callback admin:refresh_session для пользователя {callback_query.from_user.id}")
            await process_admin_refresh_session(callback_query, state)
            
        elif callback_data.startswith("admin:edit_chat_info:"):
            logger.info(f"Обработка callback {callback_data} для пользователя {callback_query.from_user.id}")
            await process_edit_chat_info(callback_query, state)
            
        elif callback_data.startswith("admin:edit_welcome:"):
            logger.info(f"Обработка callback {callback_data} для пользователя {callback_query.from_user.id}")
            await process_edit_welcome(callback_query, state)
            
        elif callback_data.startswith("admin:edit_join_mode:"):
            logger.info(f"Обработка callback {callback_data} для пользователя {callback_query.from_user.id}")
            await process_edit_join_mode(callback_query, state)
            
        elif callback_data.startswith("admin:set_join_mode:"):
            logger.info(f"Обработка callback {callback_data} для пользователя {callback_query.from_user.id}")
            await process_set_join_mode(callback_query, state)
            
        elif callback_data.startswith("admin:toggle_active:"):
            logger.info(f"Обработка callback {callback_data} для пользователя {callback_query.from_user.id}")
            await process_toggle_active(callback_query, state)
            
        else:
            # Неизвестная команда
            logger.warning(f"Неизвестный админский callback_data: {callback_data}")
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text="Неизвестная команда или устаревшая кнопка. Возвращаемся в главное меню.",
                reply_markup=get_admin_main_keyboard()
            )
            await state.set_state(AdminStates.main_menu)
            
    except Exception as e:
        # Логируем ошибку
        logger.error(f"Ошибка при обработке админского callback_query {callback_data}: {e}", exc_info=True)
        
        try:
            # Возвращаем в главное меню админа
            await state.set_state(AdminStates.main_menu)
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text="👑 Панель администратора\n\nПроизошла ошибка при обработке запроса. Выберите действие:",
                reply_markup=get_admin_main_keyboard()
            )
        except Exception as e2:
            logger.error(f"Ошибка при восстановлении после сбоя: {e2}")
            # В крайнем случае, отправляем новое сообщение
            try:
                await bot.send_message(
                    chat_id=callback_query.message.chat.id,
                    text="👑 Панель администратора\n\nПроизошла ошибка. Выберите действие:",
                    reply_markup=get_admin_main_keyboard()
                )
            except:
                pass

# Обработчики нажатий на кнопки
async def process_admin_settings(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки настроек"""
    # Устанавливаем состояние просмотра настроек
    await state.set_state(AdminStates.viewing_settings)
    
    # Отправляем сообщение с настройками
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="⚙️ Настройки\n\nВыберите, что вы хотите настроить:",
        reply_markup=get_settings_keyboard()
    )

async def process_admin_stats(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки статистики"""
    # Отправляем сообщение со статистикой
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="📊 Статистика\n\nВыберите тип статистики:",
        reply_markup=get_stats_keyboard()
    )

async def process_admin_users(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки пользователей"""
    # Устанавливаем состояние просмотра пользователей
    await state.set_state(AdminStates.viewing_users)
    
    # Отправляем сообщение с меню пользователей
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="👥 Пользователи\n\nВыберите действие:",
        reply_markup=get_users_keyboard()
    )

async def process_admin_pending(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки заявок"""
    # Устанавливаем состояние просмотра заявок
    await state.set_state(AdminStates.viewing_requests)
    
    # Получаем список ожидающих заявок
    pending_requests = DBManager.get_pending_requests()
    
    if not pending_requests:
        # Если нет ожидающих заявок
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text="🔄 Заявки\n\nВ настоящее время нет заявок, ожидающих рассмотрения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:back_to_main")
            ]])
        )
    else:
        # Если есть ожидающие заявки
        # Берем первую заявку для отображения
        request = pending_requests[0]
        
        # Получаем информацию о пользователе
        session = DBManager.get_session()
        try:
            from db_manager import User
            user = session.query(User).filter(User.id == request.user_id).first()
            
            # Получаем информацию о чате
            chat_info = chat_manager.get_chat_info(request.chat_id)
            
            # Формируем текст сообщения
            text = (
                f"🔄 Заявки (1/{len(pending_requests)})\n\n"
                f"Заявка #{request.id}\n"
                f"Пользователь: @{user.username if user.username else 'Нет юзернейма'} (ID: {user.user_id})\n"
                f"Имя: {user.first_name} {user.last_name if user.last_name else ''}\n"
                f"Чат: {chat_info['name']}\n"
                f"Дата заявки: {request.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"Что делаем с этой заявкой?"
            )
            
            # Отправляем сообщение с клавиатурой
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=text,
                reply_markup=get_request_action_keyboard(request.id)
            )
        finally:
            session.close()

async def process_admin_accounts(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки управления аккаунтами"""
    # Устанавливаем состояние управления аккаунтами
    await state.set_state(AdminStates.managing_accounts)
    
    # Отправляем сообщение с меню управления аккаунтами
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="🔒 Управление аккаунтами\n\nВыберите действие:",
        reply_markup=get_accounts_keyboard()
    )

async def process_admin_back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки возврата в главное меню"""
    # Устанавливаем состояние главного меню
    await state.set_state(AdminStates.main_menu)
    
    # Отправляем сообщение с главным меню
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="👑 Панель администратора\n\nВыберите действие:",
        reply_markup=get_admin_main_keyboard()
    )

async def process_admin_settings_chat(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки настройки конкретного чата"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[2])
    
    # Сохраняем выбранный чат в состоянии
    await state.update_data(selected_chat_id=chat_id)
    
    # Получаем информацию о чате
    chat_info = await chat_manager.get_chat_info(chat_id)
    
    # Формируем текст сообщения
    text = (
        f"⚙️ Настройки чата: {chat_info['name']}\n\n"
        f"Описание: {chat_info['description']}\n"
        f"Режим добавления: {chat_info['join_mode']}\n"
        f"Активен: {'Да' if chat_info['is_active'] else 'Нет'}\n\n"
        f"Выберите параметр для настройки:"
    )
    
    # Отправляем сообщение с настройками чата
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=text,
        reply_markup=get_chat_settings_keyboard(chat_id)
    )

async def process_admin_approve_request(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки одобрения заявки"""
    # Получаем ID заявки из callback data
    request_id = int(callback_query.data.split(":")[2])
    
    # Обрабатываем заявку
    success, message = await chat_manager.process_manual_join_request(
        request_id=request_id,
        approved=True,
        admin_id=callback_query.from_user.id
    )
    
    if success:
        # Если заявка успешно обработана
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"✅ Заявка #{request_id} успешно одобрена!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            ]])
        )
    else:
        # Если возникла ошибка
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"❌ Ошибка при обработке заявки: {message}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            ]])
        )

async def process_admin_reject_request(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки отклонения заявки"""
    # Получаем ID заявки из callback data
    request_id = int(callback_query.data.split(":")[2])
    
    # Обрабатываем заявку
    success, message = await chat_manager.process_manual_join_request(
        request_id=request_id,
        approved=False,
        admin_id=callback_query.from_user.id
    )
    
    if success:
        # Если заявка успешно обработана
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"❌ Заявка #{request_id} отклонена.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            ]])
        )
    else:
        # Если возникла ошибка
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"❌ Ошибка при обработке заявки: {message}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            ]])
        )

# Обработчики нажатий на кнопки админ-панели
async def process_admin_add_account(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки добавления аккаунта администратора"""
    # Отправляем сообщение с инструкциями
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=(
            "🔒 Добавление аккаунта администратора\n\n"
            "Введите телефонный номер аккаунта-администратора в международном формате "
            "(например, +79XXXXXXXXX), который будет использоваться для добавления участников в чаты.\n\n"
            "❗ Этот аккаунт должен быть администратором в чатах, куда будут добавляться участники."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")
        ]])
    )
    
    # Устанавливаем состояние добавления аккаунта
    await state.set_state(AdminStates.adding_account)

# Обработчик ввода имени пользователя
async def process_admin_username_input(message: types.Message, state: FSMContext):
    """Обработчик ввода телефонного номера администратора"""
    # Получаем текущее состояние
    current_state = await state.get_state()
    if current_state != AdminStates.adding_account.state:
        return
    
    # Получаем введенный телефонный номер
    phone = message.text.strip()
    
    # Проверяем формат телефонного номера
    if not phone.startswith('+'):
        phone = '+' + phone
    
    if not phone or len(phone) < 10:
        await message.answer(
            "❌ Некорректный номер телефона. Пожалуйста, введите правильный номер телефона в международном формате (например, +79XXXXXXXXX).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")
            ]])
        )
        return
    
    # Проверяем, есть ли уже аккаунт с таким номером
    session = DBManager.get_session()
    try:
        from db_manager import AdminAccount
        existing_account = session.query(AdminAccount).filter(AdminAccount.phone == phone).first()
        if existing_account:
            # Если аккаунт существует, проверяем его сессию
            from session_manager import session_manager
            if not session_manager.load_session(phone):
                # Если сессия не существует, предлагаем создать новую
                await message.answer(
                    f"⚠️ Аккаунт с номером {phone} существует в базе данных, но сессия не найдена.\n\n"
                    "Хотите создать новую сессию для этого аккаунта?",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Да, создать сессию", callback_data=f"admin:create_session:{phone}")],
                        [InlineKeyboardButton(text="Нет, вернуться назад", callback_data="admin:accounts")]
                    ])
                )
                return
            else:
                # Если сессия существует, сообщаем об этом
                await message.answer(
                    f"⚠️ Аккаунт с номером {phone} уже существует в базе данных и сессия найдена.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
                    ]])
                )
                return
    finally:
        session.close()
    
    # Сохраняем номер телефона в состоянии
    await state.update_data(admin_phone=phone)
    
    # Запрашиваем имя пользователя
    await message.answer(
        f"📱 Номер телефона: {phone}\n\n"
        "Теперь введите имя пользователя (username) для этого аккаунта (без символа @):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")
        ]])
    )
    
    # Меняем состояние на ввод имени пользователя
    await state.set_state(AdminStates.confirming_code)

# Обработчик ввода имени пользователя
async def process_admin_phone_code_input(message: types.Message, state: FSMContext):
    """Обработчик ввода имени пользователя после телефона"""
    # Получаем текущее состояние
    current_state = await state.get_state()
    if current_state != AdminStates.confirming_code.state:
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    phone = data.get("admin_phone")
    
    if not phone:
        await message.answer("❌ Ошибка: телефонный номер не найден. Пожалуйста, начните заново.")
        await state.set_state(AdminStates.managing_accounts)
        return
    
    # Получаем введенное имя пользователя
    username = message.text.strip()
    if username.startswith('@'):
        username = username[1:]
    
    # Проверяем формат имени пользователя
    if not username or len(username) < 3:
        await message.answer(
            "❌ Некорректное имя пользователя. Пожалуйста, введите правильное имя пользователя (минимум 3 символа).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Попробовать снова", callback_data=f"admin:add_account")
            ]])
        )
        return
    
    # Добавляем аккаунт администратора в БД
    try:
        # Создаем запись в БД
        session = DBManager.get_session()
        from db_manager import AdminAccount
        
        # Проверяем, не существует ли уже такого аккаунта по имени пользователя
        existing_account = session.query(AdminAccount).filter(AdminAccount.username == username).first()
        if existing_account:
            await message.answer(
                f"❌ Аккаунт с именем @{username} уже существует в базе данных!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
                ]])
            )
            return
        
        # Создаем новую запись
        admin_account = AdminAccount(
            username=username,
            phone=phone,
            session_file=f"session_{username}",  # Используем имя пользователя в имени файла
            is_active=True,
            chat_1_access=True,
            chat_2_access=True
        )
        
        session.add(admin_account)
        session.commit()
        
        # Получаем ID созданного аккаунта
        admin_id = admin_account.id
        
        # Создаем новую сессию для аккаунта
        await message.answer(
            f"✅ Аккаунт @{username} с номером {phone} добавлен в систему.\n\n"
            "Теперь необходимо создать сессию для этого аккаунта. "
            "Нажмите кнопку, чтобы начать процесс авторизации:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Создать сессию", callback_data=f"admin:create_session:{phone}")],
                [InlineKeyboardButton(text="Пропустить (сделать позже)", callback_data="admin:accounts")]
            ])
        )
        
        # Логируем действие
        DBManager.log_action(
            action="add_admin_account",
            description=f"Добавлен аккаунт администратора @{username} ({phone})",
            admin_id=message.from_user.id
        )
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении аккаунта администратора: {e}")
        await message.answer(
            f"❌ Произошла ошибка при добавлении аккаунта: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
            ]])
        )
    finally:
        session.close()

# Новый обработчик для создания сессии аккаунта администратора
async def process_admin_create_session(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки создания сессии администратора"""
    # Получаем телефон из callback_data
    phone = callback_query.data.split(":")[2]
    
    # Сохраняем данные в состоянии
    await state.update_data(admin_phone=phone)
    
    # Проверяем, существует ли уже сессия
    from session_manager import session_manager
    if session_manager.load_session(phone):
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"✅ Сессия для номера {phone} уже существует!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
            ]])
        )
        return
    
    # Отправляем сообщение с инструкциями
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=(
            f"🔒 Создание сессии для администратора с номером {phone}\n\n"
            "Будет отправлен код авторизации на указанный номер телефона.\n\n"
            "Нажмите кнопку 'Отправить код', когда будете готовы получить код."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отправить код", callback_data=f"admin:send_code:{phone}")],
            [InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")]
        ])
    )

# Обработчик для отправки кода авторизации
async def process_admin_send_code(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки отправки кода авторизации"""
    # Получаем телефон из callback_data
    phone = callback_query.data.split(":")[2]
    
    # Сохраняем данные в состоянии
    await state.update_data(admin_phone=phone)
    
    # Отправляем сообщение о начале процесса
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=f"🔄 Отправка кода авторизации на номер {phone}...",
        reply_markup=None
    )
    
    # Пытаемся отправить код авторизации
    try:
        # Создаем временный клиент
        from pyrogram import Client
        from config import API_ID, API_HASH
        
        temp_client = Client(
            name=f"temp_{phone}",
            api_id=API_ID,
            api_hash=API_HASH,
            phone_number=phone,
            in_memory=True
        )
        
        # Отправляем код
        await temp_client.connect()
        sent_code = await temp_client.send_code(phone)
        await temp_client.disconnect()
        
        # Сохраняем hash кода в состоянии
        await state.update_data(phone_code_hash=sent_code.phone_code_hash)
        
        # Запрашиваем ввод кода
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=(
                f"✅ Код авторизации отправлен на номер {phone}.\n\n"
                "Пожалуйста, введите полученный код:"
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Отменить", callback_data="admin:accounts")
            ]])
        )
        
        # Устанавливаем состояние для ввода кода
        await state.set_state(AdminStates.adding_account)
        
    except Exception as e:
        logger.error(f"Ошибка при отправке кода авторизации на номер {phone}: {e}")
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"❌ Ошибка при отправке кода авторизации: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
            ]])
        )

# Обработчик ввода кода авторизации
async def process_admin_auth_code_input(message: types.Message, state: FSMContext):
    """Обработчик ввода кода авторизации"""
    # Получаем текущее состояние
    current_state = await state.get_state()
    if current_state != AdminStates.adding_account.state:
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    phone = data.get("admin_phone")
    phone_code_hash = data.get("phone_code_hash")
    
    if not phone or not phone_code_hash:
        await message.answer(
            "❌ Ошибка: не найдены данные для авторизации. Пожалуйста, начните процесс заново.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
            ]])
        )
        return
    
    # Получаем введенный код
    code = message.text.strip()
    
    # Проверяем формат кода
    if not code or not code.isdigit() or len(code) < 5:
        await message.answer(
            "❌ Некорректный код авторизации. Пожалуйста, введите корректный код (5 или более цифр).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Отменить", callback_data="admin:accounts")
            ]])
        )
        return
    
    # Отправляем сообщение о процессе авторизации
    await message.answer("🔄 Выполняется авторизация, пожалуйста, подождите...")
    
    # Пытаемся авторизоваться и создать сессию
    try:
        # Создаем временный клиент
        from pyrogram import Client
        from config import API_ID, API_HASH
        
        temp_client = Client(
            name=f"temp_{phone}",
            api_id=API_ID,
            api_hash=API_HASH,
            phone_number=phone,
            in_memory=True
        )
        
        # Авторизуемся
        await temp_client.connect()
        
        try:
            await temp_client.sign_in(phone, phone_code_hash, code)
        except Exception as e:
            error_str = str(e).lower()
            if "password" in error_str or "2fa" in error_str:
                # Если требуется 2FA, запрашиваем пароль
                await message.answer(
                    "🔐 Для этого аккаунта требуется двухфакторная аутентификация.\n\n"
                    "Пожалуйста, введите пароль от аккаунта:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="Отменить", callback_data="admin:accounts")
                    ]])
                )
                
                # Устанавливаем состояние для ввода пароля
                await state.update_data(needs_2fa=True)
                return
            else:
                # Если другая ошибка, выводим её
                raise e
        
        # Получаем информацию о пользователе
        me = await temp_client.get_me()
        
        # Экспортируем строку сессии
        session_string = await temp_client.export_session_string()
        
        # Отключаемся
        await temp_client.disconnect()
        
        # Сохраняем сессию
        from session_manager import session_manager
        session_path = session_manager.save_session(phone, session_string)
        
        # Обновляем информацию в БД
        session = DBManager.get_session()
        try:
            from db_manager import AdminAccount
            admin_account = session.query(AdminAccount).filter(AdminAccount.phone == phone).first()
            
            if admin_account:
                admin_account.is_active = True
                admin_account.username = me.username or admin_account.username
                session.commit()
                
                # Логируем действие
                DBManager.log_action(
                    action="create_admin_session",
                    description=f"Создана сессия для аккаунта администратора @{admin_account.username} ({phone})",
                    admin_id=message.from_user.id
                )
                
                # Отправляем сообщение об успехе
                await message.answer(
                    f"✅ Сессия для аккаунта @{admin_account.username} ({phone}) успешно создана!\n\n"
                    "Теперь этот аккаунт может быть использован для добавления участников в чаты.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
                    ]])
                )
            else:
                # Если аккаунт не найден, создаем новый
                admin_account = AdminAccount(
                    username=me.username or "unknown",
                    phone=phone,
                    session_file=session_path,
                    is_active=True,
                    chat_1_access=True,
                    chat_2_access=True
                )
                
                session.add(admin_account)
                session.commit()
                
                # Логируем действие
                DBManager.log_action(
                    action="create_admin_account_and_session",
                    description=f"Создан новый аккаунт администратора @{me.username or 'unknown'} ({phone}) с сессией",
                    admin_id=message.from_user.id
                )
                
                # Отправляем сообщение об успехе
                await message.answer(
                    f"✅ Создан новый аккаунт администратора @{me.username or 'unknown'} ({phone}) и сессия для него!\n\n"
                    "Теперь этот аккаунт может быть использован для добавления участников в чаты.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
                    ]])
                )
        finally:
            session.close()
        
        # Возвращаемся в состояние управления аккаунтами
        await state.set_state(AdminStates.managing_accounts)
        
    except Exception as e:
        logger.error(f"Ошибка при авторизации и создании сессии для {phone}: {e}")
        await message.answer(
            f"❌ Ошибка при авторизации: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
            ]])
        )

# Обработчик изменения режима вступления
async def process_edit_join_mode(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки изменения режима вступления"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[2])
    
    # Сохраняем выбранный чат в состоянии
    await state.update_data(edit_chat_id=chat_id)
    
    # Получаем информацию о чате
    chat_info = await chat_manager.get_chat_info(chat_id)
    
    # Создаем кнопки для выбора режима
    buttons = []
    
    # Режим автоматического подтверждения
    auto_approve_btn = InlineKeyboardButton(
        text=f"{'✅' if chat_info['join_mode'] == 'auto_approve' else '⬜'} Автоматическое подтверждение",
        callback_data=f"admin:set_join_mode:{chat_id}:auto_approve"
    )
    
    # Режим подтверждения администратором
    admin_approve_btn = InlineKeyboardButton(
        text=f"{'✅' if chat_info['join_mode'] == 'admin_approve' else '⬜'} Подтверждение администратором",
        callback_data=f"admin:set_join_mode:{chat_id}:admin_approve"
    )
    
    # Режим с вопросами
    questions_btn = InlineKeyboardButton(
        text=f"{'✅' if chat_info['join_mode'] == 'questions' else '⬜'} С вопросами",
        callback_data=f"admin:set_join_mode:{chat_id}:questions"
    )
    
    # Кнопка активации/деактивации чата
    toggle_active_btn = InlineKeyboardButton(
        text=f"{'🟢 Чат активен' if chat_info['is_active'] else '🔴 Чат неактивен'}",
        callback_data=f"admin:toggle_active:{chat_id}"
    )
    
    # Кнопка назад
    back_btn = InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data=f"admin:settings_chat:{chat_id}"
    )
    
    # Добавляем кнопки в клавиатуру
    buttons.append([auto_approve_btn])
    buttons.append([admin_approve_btn])
    buttons.append([questions_btn])
    buttons.append([toggle_active_btn])
    buttons.append([back_btn])
    
    # Отправляем сообщение с выбором режима
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=(
            f"🔐 Настройка режима вступления для: {chat_info['name']}\n\n"
            f"Текущий режим: {chat_info['join_mode']}\n"
            f"Статус: {'Активен' if chat_info['is_active'] else 'Неактивен'}\n\n"
            "Выберите режим вступления:"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

# Обработчик установки режима вступления
async def process_set_join_mode(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки установки режима вступления"""
    # Получаем данные из callback data
    parts = callback_query.data.split(":")
    chat_id = int(parts[2])
    join_mode = parts[3]
    
    # Обновляем режим вступления
    success = chat_manager.update_chat_settings(
        chat_id=chat_id,
        join_mode=join_mode
    )
    
    if success:
        # Логируем действие
        DBManager.log_action(
            action="change_join_mode",
            description=f"Изменен режим вступления для чата {chat_id} на {join_mode}",
            admin_id=callback_query.from_user.id,
            chat_id=chat_id
        )
        
        # Возвращаемся в меню настройки режима вступления
        await process_edit_join_mode(callback_query, state)
    else:
        # Отправляем сообщение об ошибке
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text="❌ Ошибка при изменении режима вступления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к настройкам чата", callback_data=f"admin:settings_chat:{chat_id}")
            ]])
        )

# Обработчик активации/деактивации чата
async def process_toggle_active(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки активации/деактивации чата"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[2])
    
    # Получаем информацию о чате
    chat_info = await chat_manager.get_chat_info(chat_id)
    
    # Инвертируем статус активности
    new_status = not chat_info['is_active']
    
    # Обновляем статус активности
    success = chat_manager.update_chat_settings(
        chat_id=chat_id,
        is_active=new_status
    )
    
    if success:
        # Логируем действие
        DBManager.log_action(
            action="toggle_chat_active",
            description=f"Изменен статус активности чата {chat_id} на {new_status}",
            admin_id=callback_query.from_user.id,
            chat_id=chat_id
        )
        
        # Возвращаемся в меню настройки режима вступления
        await process_edit_join_mode(callback_query, state)
    else:
        # Отправляем сообщение об ошибке
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text="❌ Ошибка при изменении статуса активности чата.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к настройкам чата", callback_data=f"admin:settings_chat:{chat_id}")
            ]])
        )

# Обработчик кнопки обновления сессии
async def process_admin_refresh_session(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки обновления сессии аккаунта администратора"""
    # Получаем список администраторских аккаунтов
    session = DBManager.get_session()
    try:
        from db_manager import AdminAccount
        accounts = session.query(AdminAccount).all()
        
        if not accounts:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text="❌ В системе нет аккаунтов администраторов. Сначала добавьте хотя бы один аккаунт.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")
                ]])
            )
            return
        
        # Получаем список всех доступных сессий
        from session_manager import session_manager
        available_sessions = session_manager.find_all_sessions()
        
        # Формируем список аккаунтов для выбора
        text = "🔄 Состояние аккаунтов администраторов:\n\n"
        buttons = []
        
        for account in accounts:
            # Проверяем наличие сессии
            has_session = account.phone in available_sessions
            status = "✅ Сессия найдена" if has_session else "❌ Сессия отсутствует"
            
            # Добавляем информацию о статусе аккаунта
            text += f"• @{account.username} ({account.phone}) - {status}\n"
            
            # Добавляем кнопку для действия в зависимости от статуса
            if has_session:
                # Если сессия есть, предлагаем обновить
                row = [InlineKeyboardButton(
                    text=f"🔄 Обновить @{account.username}",
                    callback_data=f"admin:refresh_session:{account.id}"
                )]
            else:
                # Если сессии нет, предлагаем создать
                row = [InlineKeyboardButton(
                    text=f"➕ Создать сессию для @{account.username}",
                    callback_data=f"admin:create_session:{account.phone}"
                )]
            
            buttons.append(row)
        
        # Добавляем кнопку добавления нового аккаунта
        buttons.append([
            InlineKeyboardButton(
                text="➕ Добавить новый аккаунт",
                callback_data="admin:add_account"
            )
        ])
        
        # Добавляем кнопку назад
        buttons.append([InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")])
        
        # Отправляем сообщение с выбором аккаунта
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
    except Exception as e:
        logger.error(f"Ошибка при получении списка аккаунтов: {e}")
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"❌ Произошла ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")
            ]])
        )
    finally:
        session.close()

# Регистрация обработчиков админских команд
def register_admin_handlers(dp):
    """Регистрирует обработчики для админского интерфейса"""
    global _dp
    _dp = dp  # Сохраняем диспетчер для использования внутри модуля
    
    # Регистрация команды
    dp.message.register(cmd_admin, Command("admin"))
    
    # Регистрация обработчика всех админских callback_query
    dp.callback_query.register(process_admin_callback, lambda c: c.data and c.data.startswith("admin:"))
    
    # Регистрация новых обработчиков для сессий администраторов
    dp.callback_query.register(process_admin_create_session, lambda c: c.data and c.data.startswith("admin:create_session:"))
    dp.callback_query.register(process_admin_send_code, lambda c: c.data and c.data.startswith("admin:send_code:"))
    
    # Регистрация обработчиков текстовых сообщений в различных состояниях
    dp.message.register(process_admin_username_input, AdminStates.adding_account)
    dp.message.register(process_admin_phone_code_input, AdminStates.confirming_code)
    dp.message.register(process_admin_auth_code_input, AdminStates.entering_2fa)
    dp.message.register(process_edit_chat_info, AdminStates.editing_chat_info)
    dp.message.register(process_edit_welcome, AdminStates.editing_welcome)
    
    logger.info("Обработчики админского интерфейса успешно зарегистрированы")