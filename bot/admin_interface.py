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

# Функции для проверки, является ли пользователь администратором
def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    try:
        logger.info(f"Проверка прав администратора для ID: {user_id}, тип: {type(user_id)}")
        
        # Быстрая проверка для системных администраторов (можно добавить фиксированные ID)
        system_admins = [6327802249]
        if user_id in system_admins:
            logger.info(f"ID {user_id} является системным администратором")
            return True
        
        # Конвертируем user_id в строку и int для надёжности сравнения
        user_id_str = str(user_id)
        user_id_int = int(user_id) if isinstance(user_id, (str, int)) else 0
        
        logger.info(f"Список ID администраторов: {ADMIN_IDS}, типы: {[type(admin_id) for admin_id in ADMIN_IDS]}")
        
        # Проверяем совпадение в виде строки
        for admin_id in ADMIN_IDS:
            # Проверка строковых представлений
            if user_id_str == str(admin_id):
                logger.info(f"ID {user_id} найден в списке администраторов (строковое сравнение)")
                return True
            
            # Проверка числовых представлений
            admin_id_int = int(admin_id) if isinstance(admin_id, (str, int)) else 0
            if user_id_int > 0 and admin_id_int > 0 and user_id_int == admin_id_int:
                logger.info(f"ID {user_id} найден в списке администраторов (числовое сравнение)")
                return True
        
        # Если не найдено совпадений
        logger.warning(f"ID {user_id} не найден в списке администраторов: {ADMIN_IDS}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке прав администратора: {e}")
        # В случае ошибки возвращаем False
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
            await process_admin_settings(callback_query, state)
            
        elif callback_data == "admin:stats":
            await process_admin_stats(callback_query, state)
            
        elif callback_data == "admin:users":
            await process_admin_users(callback_query, state)
            
        elif callback_data == "admin:pending":
            await process_admin_pending(callback_query, state)
            
        elif callback_data == "admin:accounts":
            await process_admin_accounts(callback_query, state)
            
        elif callback_data == "admin:back_to_main":
            await process_admin_back_to_main(callback_query, state)
            
        elif callback_data.startswith("admin:settings_chat:"):
            chat_id = int(callback_data.split(":")[2])
            await process_admin_settings_chat(callback_query, state)
            
        elif callback_data.startswith("admin:approve_request:"):
            request_id = int(callback_data.split(":")[2])
            await process_admin_approve_request(callback_query, state)
            
        elif callback_data.startswith("admin:reject_request:"):
            request_id = int(callback_data.split(":")[2])
            await process_admin_reject_request(callback_query, state)
            
        elif callback_data == "admin:add_account":
            await process_admin_add_account(callback_query, state)
            
        elif callback_data == "admin:refresh_session":
            await process_admin_refresh_session(callback_query, state)
            
        elif callback_data.startswith("admin:edit_chat_info:"):
            await process_edit_chat_info(callback_query, state)
            
        elif callback_data.startswith("admin:edit_welcome:"):
            await process_edit_welcome(callback_query, state)
            
        elif callback_data.startswith("admin:edit_join_mode:"):
            await process_edit_join_mode(callback_query, state)
            
        elif callback_data.startswith("admin:set_join_mode:"):
            await process_set_join_mode(callback_query, state)
            
        elif callback_data.startswith("admin:toggle_active:"):
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
            "Введите имя пользователя (username) аккаунта-администратора, "
            "который будет использоваться для добавления участников в чаты.\n\n"
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
    """Обработчик ввода имени пользователя администратора"""
    # Получаем текущее состояние
    current_state = await state.get_state()
    if current_state != AdminStates.adding_account.state:
        return
    
    # Получаем введенный юзернейм
    username = message.text.strip()
    if username.startswith('@'):
        username = username[1:]
    
    # Проверяем формат юзернейма
    if not username or len(username) < 3:
        await message.answer(
            "❌ Некорректное имя пользователя. Пожалуйста, введите правильное имя пользователя (минимум 3 символа).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:accounts")
            ]])
        )
        return
    
    # Добавляем аккаунт администратора в БД
    try:
        # Создаем запись в БД
        session = DBManager.get_session()
        from db_manager import AdminAccount
        
        # Проверяем, не существует ли уже такого аккаунта
        existing_account = session.query(AdminAccount).filter(AdminAccount.username == username).first()
        if existing_account:
            await message.answer(
                f"❌ Аккаунт с именем @{username} уже существует в базе данных!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
                ]])
            )
            return
        
        # Генерируем фиктивный номер телефона (просто для структуры БД)
        import random
        phone_prefix = "+7"
        phone_body = ''.join([str(random.randint(0, 9)) for _ in range(10)])
        fake_phone = f"{phone_prefix}{phone_body}"
        
        # Создаем новую запись
        admin_account = AdminAccount(
            username=username,
            phone=fake_phone,
            session_file=f"session_{username}",  # Используем имя пользователя в имени файла
            is_active=True,
            chat_1_access=True,
            chat_2_access=True
        )
        
        session.add(admin_account)
        session.commit()
        
        # Возвращаемся в главное меню управления аккаунтами
        await state.set_state(AdminStates.managing_accounts)
        await message.answer(
            f"✅ Аккаунт @{username} успешно добавлен в систему.\n\n"
            "⚠️ Убедитесь, что этот аккаунт является администратором в чатах, "
            "куда будут добавляться пользователи.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к управлению аккаунтами", callback_data="admin:accounts")
            ]])
        )
        
        # Логируем действие
        DBManager.log_action(
            action="add_admin_account",
            description=f"Добавлен аккаунт администратора @{username}",
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
        
        # Формируем список аккаунтов для выбора
        text = "🔄 Выберите аккаунт для обновления сессии:"
        buttons = []
        
        for account in accounts:
            status = "✅ Активен" if account.is_active else "❌ Неактивен"
            row = [InlineKeyboardButton(
                text=f"@{account.username} ({account.phone}) - {status}",
                callback_data=f"admin:refresh_session:{account.id}"
            )]
            buttons.append(row)
        
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

# Новые обработчики для редактирования информации о чате
async def process_edit_chat_info(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки редактирования информации о чате"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[2])
    
    # Сохраняем выбранный чат в состоянии
    await state.update_data(edit_chat_id=chat_id)
    
    # Получаем информацию о чате
    chat_info = await chat_manager.get_chat_info(chat_id)
    
    # Устанавливаем состояние редактирования информации о чате
    await state.set_state(AdminStates.editing_chat_info)
    
    # Отправляем сообщение с инструкцией
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=(
            f"📝 Редактирование информации о чате: {chat_info['name']}\n\n"
            f"Текущее описание:\n{chat_info['description']}\n\n"
            "Отправьте новое название и описание чата в формате:\n"
            "Название чата\n---\nОписание чата"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=MESSAGES["back_button"], callback_data=f"admin:settings_chat:{chat_id}")
        ]])
    )

# Обработчик ввода новой информации о чате
async def process_edit_chat_info_input(message: types.Message, state: FSMContext):
    """Обработчик ввода новой информации о чате"""
    # Получаем данные из состояния
    data = await state.get_data()
    chat_id = data.get("edit_chat_id")
    
    if not chat_id:
        await message.answer("❌ Ошибка: не выбран чат для редактирования")
        await state.set_state(AdminStates.viewing_settings)
        return
    
    # Разбираем введенный текст
    text = message.text.strip()
    parts = text.split("---")
    
    if len(parts) < 2:
        await message.answer(
            "❌ Неверный формат. Используйте разделитель '---' между названием и описанием.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Попробовать снова", callback_data=f"admin:edit_chat_info:{chat_id}")
            ]])
        )
        return
    
    name = parts[0].strip()
    description = parts[1].strip()
    
    if not name or len(name) < 3:
        await message.answer(
            "❌ Название чата должно содержать минимум 3 символа.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Попробовать снова", callback_data=f"admin:edit_chat_info:{chat_id}")
            ]])
        )
        return
    
    # Обновляем информацию о чате
    success = chat_manager.update_chat_settings(
        chat_id=chat_id,
        name=name,
        description=description
    )
    
    if success:
        await message.answer(
            f"✅ Информация о чате успешно обновлена!\n\nНазвание: {name}\nОписание: {description}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к настройкам чата", callback_data=f"admin:settings_chat:{chat_id}")
            ]])
        )
        
        # Логируем действие
        DBManager.log_action(
            action="edit_chat_info",
            description=f"Изменена информация о чате {chat_id}",
            admin_id=message.from_user.id,
            chat_id=chat_id
        )
        
        # Обновляем состояние
        await state.set_state(AdminStates.viewing_settings)
    else:
        await message.answer(
            "❌ Ошибка при обновлении информации о чате.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Попробовать снова", callback_data=f"admin:edit_chat_info:{chat_id}")
            ]])
        )

# Обработчик редактирования приветственного сообщения
async def process_edit_welcome(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки редактирования приветственного сообщения"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[2])
    
    # Сохраняем выбранный чат в состоянии
    await state.update_data(edit_chat_id=chat_id)
    
    # Получаем информацию о чате
    chat_info = await chat_manager.get_chat_info(chat_id)
    
    # Устанавливаем состояние редактирования приветствия
    await state.set_state(AdminStates.editing_welcome)
    
    # Отправляем сообщение с инструкцией
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=(
            f"👋 Редактирование приветственного сообщения для: {chat_info['name']}\n\n"
            f"Текущее приветствие:\n{chat_info['welcome_message']}\n\n"
            "Отправьте новое приветственное сообщение:"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=MESSAGES["back_button"], callback_data=f"admin:settings_chat:{chat_id}")
        ]])
    )

# Обработчик ввода нового приветственного сообщения
async def process_edit_welcome_input(message: types.Message, state: FSMContext):
    """Обработчик ввода нового приветственного сообщения"""
    # Получаем данные из состояния
    data = await state.get_data()
    chat_id = data.get("edit_chat_id")
    
    if not chat_id:
        await message.answer("❌ Ошибка: не выбран чат для редактирования")
        await state.set_state(AdminStates.viewing_settings)
        return
    
    # Получаем введенный текст
    welcome_message = message.text.strip()
    
    if not welcome_message or len(welcome_message) < 10:
        await message.answer(
            "❌ Приветственное сообщение должно содержать минимум 10 символов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Попробовать снова", callback_data=f"admin:edit_welcome:{chat_id}")
            ]])
        )
        return
    
    # Обновляем приветственное сообщение
    success = chat_manager.update_chat_settings(
        chat_id=chat_id,
        welcome_message=welcome_message
    )
    
    if success:
        await message.answer(
            f"✅ Приветственное сообщение успешно обновлено!\n\n{welcome_message}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Вернуться к настройкам чата", callback_data=f"admin:settings_chat:{chat_id}")
            ]])
        )
        
        # Логируем действие
        DBManager.log_action(
            action="edit_welcome_message",
            description=f"Изменено приветственное сообщение для чата {chat_id}",
            admin_id=message.from_user.id,
            chat_id=chat_id
        )
        
        # Обновляем состояние
        await state.set_state(AdminStates.viewing_settings)
    else:
        await message.answer(
            "❌ Ошибка при обновлении приветственного сообщения.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Попробовать снова", callback_data=f"admin:edit_welcome:{chat_id}")
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

# Регистрация обработчиков админских команд
def register_admin_handlers(dp):
    """Регистрирует обработчики для админского интерфейса"""
    global _dp
    _dp = dp  # Сохраняем диспетчер для использования внутри модуля
    
    # Регистрация команды
    dp.message.register(cmd_admin, Command("admin"))
    
    # Регистрация обработчика всех админских callback_query
    dp.callback_query.register(process_admin_callback, lambda c: c.data and c.data.startswith("admin:"))
    
    # Регистрация обработчиков текстовых сообщений в различных состояниях
    dp.message.register(process_admin_username_input, AdminStates.adding_account)
    dp.message.register(process_edit_chat_info_input, AdminStates.editing_chat_info)
    dp.message.register(process_edit_welcome_input, AdminStates.editing_welcome)

    logger.info("Обработчики админского интерфейса успешно зарегистрированы")