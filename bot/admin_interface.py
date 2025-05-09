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

# Функции для проверки, является ли пользователь администратором
def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id in ADMIN_IDS

# Функции для создания клавиатур
def get_admin_main_keyboard():
    """Создает клавиатуру для главного меню администратора"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    # Добавляем кнопки основных функций
    keyboard.add(
        InlineKeyboardButton(text=MESSAGES["settings_button"], callback_data="admin:settings"),
        InlineKeyboardButton(text=MESSAGES["stats_button"], callback_data="admin:stats")
    )
    
    keyboard.add(
        InlineKeyboardButton(text=MESSAGES["users_button"], callback_data="admin:users"),
        InlineKeyboardButton(text=MESSAGES["pending_button"], callback_data="admin:pending")
    )
    
    keyboard.add(
        InlineKeyboardButton(text=MESSAGES["accounts_button"], callback_data="admin:accounts")
    )
    
    return keyboard

def get_settings_keyboard():
    """Создает клавиатуру для меню настроек"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопки для настройки чатов
    keyboard.add(
        InlineKeyboardButton(
            text=f"Настроить {CHATS[CHAT_ID_1]['name']} 🗣️",
            callback_data=f"admin:settings_chat:{CHAT_ID_1}"
        ),
        InlineKeyboardButton(
            text=f"Настроить {CHATS[CHAT_ID_2]['name']} 💬",
            callback_data=f"admin:settings_chat:{CHAT_ID_2}"
        )
    )
    
    # Добавляем кнопку настроек бота
    keyboard.add(InlineKeyboardButton(
        text="Настройки бота 🤖",
        callback_data="admin:settings_bot"
    ))
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="admin:back_to_main"
    ))
    
    return keyboard

def get_chat_settings_keyboard(chat_id):
    """Создает клавиатуру для настройки конкретного чата"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопки настроек чата
    keyboard.add(
        InlineKeyboardButton(
            text="Изменить название/описание 📝",
            callback_data=f"admin:edit_chat_info:{chat_id}"
        ),
        InlineKeyboardButton(
            text="Настроить приветствие 👋",
            callback_data=f"admin:edit_welcome:{chat_id}"
        ),
        InlineKeyboardButton(
            text="Режим добавления 🔐",
            callback_data=f"admin:edit_join_mode:{chat_id}"
        ),
        InlineKeyboardButton(
            text="Выбрать аккаунты для добавления 👨‍💼",
            callback_data=f"admin:select_accounts:{chat_id}"
        )
    )
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="admin:settings"
    ))
    
    return keyboard

def get_users_keyboard():
    """Создает клавиатуру для меню пользователей"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопки для просмотра пользователей по чатам
    keyboard.add(
        InlineKeyboardButton(
            text=f"Пользователи {CHATS[CHAT_ID_1]['name']} 🗣️",
            callback_data=f"admin:users_chat:{CHAT_ID_1}"
        ),
        InlineKeyboardButton(
            text=f"Пользователи {CHATS[CHAT_ID_2]['name']} 💬",
            callback_data=f"admin:users_chat:{CHAT_ID_2}"
        )
    )
    
    # Добавляем кнопку поиска пользователя
    keyboard.add(InlineKeyboardButton(
        text="Поиск пользователя 🔍",
        callback_data="admin:search_user"
    ))
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="admin:back_to_main"
    ))
    
    return keyboard

def get_accounts_keyboard():
    """Создает клавиатуру для управления аккаунтами"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопки управления аккаунтами
    keyboard.add(
        InlineKeyboardButton(
            text="Добавить аккаунт ➕",
            callback_data="admin:add_account"
        ),
        InlineKeyboardButton(
            text="Обновить сессию 🔄",
            callback_data="admin:refresh_session"
        ),
        InlineKeyboardButton(
            text="Статус аккаунтов 🔍",
            callback_data="admin:account_status"
        ),
        InlineKeyboardButton(
            text="Сбросить сессии 🔐",
            callback_data="admin:reset_sessions"
        )
    )
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="admin:back_to_main"
    ))
    
    return keyboard

def get_stats_keyboard():
    """Создает клавиатуру для меню статистики"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    # Добавляем кнопки статистики
    keyboard.add(
        InlineKeyboardButton(
            text="Общая статистика 📈",
            callback_data="admin:stats_general"
        )
    )
    
    keyboard.add(
        InlineKeyboardButton(
            text=f"Статистика {CHATS[CHAT_ID_1]['name']} 🗣️",
            callback_data=f"admin:stats_chat:{CHAT_ID_1}"
        ),
        InlineKeyboardButton(
            text=f"Статистика {CHATS[CHAT_ID_2]['name']} 💬",
            callback_data=f"admin:stats_chat:{CHAT_ID_2}"
        )
    )
    
    keyboard.add(
        InlineKeyboardButton(
            text="По дням 📅",
            callback_data="admin:stats_daily"
        ),
        InlineKeyboardButton(
            text="По неделям 📆",
            callback_data="admin:stats_weekly"
        )
    )
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="admin:back_to_main"
    ))
    
    return keyboard

def get_request_action_keyboard(request_id):
    """Создает клавиатуру для действий с заявкой"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    # Добавляем кнопки действий
    keyboard.add(
        InlineKeyboardButton(
            text="Одобрить ✅",
            callback_data=f"admin:approve_request:{request_id}"
        ),
        InlineKeyboardButton(
            text="Отклонить ❌",
            callback_data=f"admin:reject_request:{request_id}"
        )
    )
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="admin:pending"
    ))
    
    return keyboard

# Обработчики команд
async def cmd_admin(message: types.Message, state: FSMContext):
    """Обработчик команды /admin"""
    # Проверяем, является ли пользователь администратором
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет доступа к административным функциям.")
        return
    
    # Сбрасываем состояние
    await state.clear()
    
    # Устанавливаем состояние главного меню
    await state.set_state(AdminStates.main_menu)
    
    # Отправляем приветствие и меню
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\nВы вошли в панель администратора.",
        reply_markup=get_admin_main_keyboard()
    )

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
    
    # Отвечаем на callback query
    await callback_query.answer()

async def process_admin_stats(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки статистики"""
    # Отправляем сообщение со статистикой
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="📊 Статистика\n\nВыберите тип статистики:",
        reply_markup=get_stats_keyboard()
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

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
    
    # Отвечаем на callback query
    await callback_query.answer()

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
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="admin:back_to_main")
            )
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
    
    # Отвечаем на callback query
    await callback_query.answer()

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
    
    # Отвечаем на callback query
    await callback_query.answer()

async def process_admin_back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки возврата в главное меню"""
    # Устанавливаем состояние главного меню
    await state.set_state(AdminStates.main_menu)
    
    # Отправляем сообщение с главным меню
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Панель администратора",
        reply_markup=get_admin_main_keyboard()
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

async def process_admin_settings_chat(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки настройки конкретного чата"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[2])
    
    # Сохраняем выбранный чат в состоянии
    await state.update_data(selected_chat_id=chat_id)
    
    # Получаем информацию о чате
    chat_info = chat_manager.get_chat_info(chat_id)
    
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
    
    # Отвечаем на callback query
    await callback_query.answer()

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
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            )
        )
    else:
        # Если возникла ошибка
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"❌ Ошибка при обработке заявки: {message}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            )
        )
    
    # Отвечаем на callback query
    await callback_query.answer()

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
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            )
        )
    else:
        # Если возникла ошибка
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"❌ Ошибка при обработке заявки: {message}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton(text="К списку заявок", callback_data="admin:pending")
            )
        )
    
    # Отвечаем на callback query
    await callback_query.answer()

# Регистрация обработчиков админских команд
def register_admin_handlers(dp):
    """Регистрирует обработчики для админского интерфейса"""
    # Регистрация команды
    dp.message.register(cmd_admin, Command("admin"))
    
    # Регистрация обработчиков основных кнопок
    dp.callback_query.register(
        process_admin_settings, 
        lambda c: c.data == "admin:settings"
    )
    
    dp.callback_query.register(
        process_admin_stats, 
        lambda c: c.data == "admin:stats"
    )
    
    dp.callback_query.register(
        process_admin_users, 
        lambda c: c.data == "admin:users"
    )
    
    dp.callback_query.register(
        process_admin_pending, 
        lambda c: c.data == "admin:pending"
    )
    
    dp.callback_query.register(
        process_admin_accounts, 
        lambda c: c.data == "admin:accounts"
    )
    
    dp.callback_query.register(
        process_admin_back_to_main, 
        lambda c: c.data == "admin:back_to_main"
    )
    
    # Регистрация обработчиков настроек чатов
    dp.callback_query.register(
        process_admin_settings_chat, 
        lambda c: c.data.startswith("admin:settings_chat:")
    )
    
    # Регистрация обработчиков заявок
    dp.callback_query.register(
        process_admin_approve_request, 
        lambda c: c.data.startswith("admin:approve_request:")
    )
    
    dp.callback_query.register(
        process_admin_reject_request, 
        lambda c: c.data.startswith("admin:reject_request:")
    )
    
    # Здесь можно добавить регистрацию остальных обработчиков 