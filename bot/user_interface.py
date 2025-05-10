import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, CHAT_ID_1, CHAT_ID_2, CHATS, MESSAGES, PRIVACY_INSTRUCTIONS, ADMIN_IDS
from db_manager import DBManager
from chat_manager import chat_manager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Определение состояний для FSM (конечного автомата)
class UserStates(StatesGroup):
    selecting_chat = State()
    confirming_join = State()
    changing_privacy = State()
    feedback = State()  # Новое состояние для обратной связи

# Функции для создания клавиатур
def get_start_keyboard():
    """Создает клавиатуру для начального меню"""
    # Создаем кнопки
    select_chat_btn = InlineKeyboardButton(text="Хочу в чат 🔐", callback_data="select_chat")
    info_btn = InlineKeyboardButton(text=MESSAGES["info_button"], callback_data="info")
    support_btn = InlineKeyboardButton(text=MESSAGES["support_button"], callback_data="support")
    
    # Создаем клавиатуру с нужной структурой
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [select_chat_btn],  # Первая строка с одной кнопкой
        [info_btn, support_btn]  # Вторая строка с двумя кнопками
    ])
    
    return keyboard

def get_chat_selection_keyboard():
    """Создает клавиатуру для выбора чата"""
    # Создаем кнопки
    chat1_btn = InlineKeyboardButton(
        text=CHATS[CHAT_ID_1]["name"],
        callback_data=f"join_chat:{CHAT_ID_1}"
    )
    
    chat2_btn = InlineKeyboardButton(
        text=CHATS[CHAT_ID_2]["name"],
        callback_data=f"join_chat:{CHAT_ID_2}"
    )
    
    back_btn = InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="back_to_start"
    )
    
    # Создаем клавиатуру с нужной структурой
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [chat1_btn],  # Первая строка
        [chat2_btn],  # Вторая строка
        [back_btn]    # Третья строка
    ])
    
    return keyboard

def get_confirm_join_keyboard(chat_id):
    """Создает клавиатуру для подтверждения вступления в чат"""
    # Создаем кнопки
    join_btn = InlineKeyboardButton(
        text=MESSAGES["join_button"],
        callback_data=f"confirm_join:{chat_id}"
    )
    
    back_btn = InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="select_chat"
    )
    
    # Создаем клавиатуру с нужной структурой
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [join_btn],  # Первая строка
        [back_btn]   # Вторая строка
    ])
    
    return keyboard

def get_privacy_issue_keyboard():
    """Создает клавиатуру для случая, когда есть проблемы с приватностью"""
    # Создаем кнопки
    instructions_btn = InlineKeyboardButton(
        text="Показать инструкцию 📋",
        callback_data="show_privacy_instructions"
    )
    
    try_again_btn = InlineKeyboardButton(
        text="Попробовать снова 🔄",
        callback_data="try_again"
    )
    
    contact_admin_btn = InlineKeyboardButton(
        text="Связаться с админом 👨‍💼",
        callback_data="contact_admin"
    )
    
    back_btn = InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="select_chat"
    )
    
    # Создаем клавиатуру с нужной структурой
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [instructions_btn],  # Первая строка
        [try_again_btn],     # Вторая строка
        [contact_admin_btn], # Третья строка
        [back_btn]           # Четвертая строка
    ])
    
    return keyboard

def get_feedback_keyboard():
    """Создает клавиатуру для сбора обратной связи"""
    back_btn = InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="back_to_start"
    )
    
    # Создаем клавиатуру с одной кнопкой назад
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    
    return keyboard

# Вспомогательные функции
def get_admin_contact_info():
    """Возвращает контактную информацию администратора"""
    # В реальном приложении берем из базы данных или конфига
    return {
        "username": "admin_support", 
        "email": "admin@yourdomain.com",
        "phone": "+7 (XXX) XXX-XX-XX"
    }

def notify_admins(message, user_id=None, chat_id=None):
    """Отправляет уведомление всем администраторам"""
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                chat_id=admin_id,
                text=message
            )
            logger.info(f"Уведомление отправлено админу {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    # Сбрасываем состояние
    await state.clear()
    
    # Добавляем пользователя в БД
    DBManager.add_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
    # Отправляем приветствие
    await message.answer(
        MESSAGES["welcome"],
        reply_markup=get_start_keyboard()
    )
    
    # Логируем действие
    logger.info(f"Пользователь {message.from_user.id} (@{message.from_user.username}) запустил бота")

# Добавляем обработчик команды /admin
@dp.message(Command("admin"))
async def cmd_admin_forward(message: types.Message, state: FSMContext):
    """Перенаправляет команду /admin в админский интерфейс"""
    # Проверяем, является ли пользователь администратором
    from bot.admin_interface import is_admin, cmd_admin
    
    # Логируем получение команды
    logger.info(f"Получена команда /admin от пользователя {message.from_user.id} (@{message.from_user.username})")
    
    # Проверяем права администратора
    if message.from_user.id in ADMIN_IDS or str(message.from_user.id) in [str(admin_id) for admin_id in ADMIN_IDS]:
        # Если пользователь является администратором, перенаправляем на обработчик в admin_interface
        logger.info(f"Пользователь {message.from_user.id} является администратором, перенаправляем на админскую панель")
        await cmd_admin(message, state)
    else:
        # Если пользователь не является администратором
        logger.warning(f"Отказано в доступе к админке пользователю {message.from_user.id}")
        await message.answer("У вас нет доступа к администраторским функциям. Если вы считаете, что это ошибка, свяжитесь с владельцем бота.")

# Обработчики нажатий на кнопки
@dp.callback_query(lambda c: c.data == "select_chat")
async def process_select_chat(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки выбора чата"""
    # Устанавливаем состояние выбора чата
    await state.set_state(UserStates.selecting_chat)
    
    # Отправляем сообщение с кнопками выбора чата
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=MESSAGES["select_chat"],
        reply_markup=get_chat_selection_keyboard()
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("join_chat:"))
async def process_join_chat(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора конкретного чата"""
    # Получаем ID чата из callback data
    chat_id = int(callback_query.data.split(":")[1])
    
    # Получаем информацию о чате
    chat_info = await chat_manager.get_chat_info(chat_id)
    
    # Сохраняем выбранный чат в состоянии
    await state.update_data(selected_chat_id=chat_id)
    
    # Устанавливаем состояние подтверждения вступления
    await state.set_state(UserStates.confirming_join)
    
    # Отправляем сообщение с подтверждением
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=MESSAGES["confirm_join"].format(chat_name=chat_info["name"]),
        reply_markup=get_confirm_join_keyboard(chat_id)
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("confirm_join:"))
async def process_confirm_join(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик подтверждения вступления в чат"""
    # Получаем данные из состояния
    data = await state.get_data()
    chat_id = int(callback_query.data.split(":")[1])
    
    # Проверяем, может ли пользователь быть добавлен
    can_be_added, reason = await chat_manager.check_user_can_be_added(
        user_id=callback_query.from_user.id,
        chat_id=chat_id
    )
    
    if not can_be_added:
        # Если пользователь не может быть добавлен
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"К сожалению, вы не можете быть добавлены в чат: {reason}",
            reply_markup=get_chat_selection_keyboard()
        )
        await callback_query.answer()
        return
    
    # Пытаемся добавить пользователя в чат
    success, message, additional_data = await chat_manager.add_user_to_chat(
        user_id=callback_query.from_user.id,
        chat_id=chat_id
    )
    
    if success:
        # Если пользователь успешно добавлен
        # Получаем информацию о чате
        chat_info = await chat_manager.get_chat_info(chat_id)
        
        # Отправляем сообщение об успешном добавлении
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=chat_info["welcome_message"] or MESSAGES["success_join"],
            reply_markup=None
        )
        
        # Отправляем уведомление админам
        if additional_data and "admin_username" in additional_data:
            admin_username = additional_data["admin_username"]
            
            # Отправляем уведомление всем админам
            user_name = callback_query.from_user.username or f"id{callback_query.from_user.id}"
            notify_message = MESSAGES["admin_notification"].format(
                username=user_name,
                chat_name=chat_info["name"],
                admin_username=admin_username
            )
            await notify_admins(notify_message, callback_query.from_user.id, chat_id)
    else:
        # Если возникла ошибка при добавлении
        if additional_data and additional_data.get("privacy_restricted"):
            # Если проблема с настройками приватности
            await state.set_state(UserStates.changing_privacy)
            
            # Отправляем сообщение с инструкцией
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=MESSAGES["privacy_issue"],
                reply_markup=get_privacy_issue_keyboard()
            )
        else:
            # Если другая ошибка
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=f"Ошибка при добавлении в чат: {message}",
                reply_markup=get_chat_selection_keyboard()
            )
            
            # Логируем ошибку
            logger.error(f"Ошибка при добавлении пользователя {callback_query.from_user.id} в чат {chat_id}: {message}")
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "show_privacy_instructions")
async def process_show_privacy_instructions(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик показа инструкции по изменению настроек приватности"""
    # Отправляем первую инструкцию
    for i, instruction in enumerate(PRIVACY_INSTRUCTIONS):
        # Отправляем текст инструкции
        if i == 0:
            # Редактируем текущее сообщение для первой инструкции
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=instruction["text"],
                reply_markup=None
            )
        else:
            # Отправляем новые сообщения для остальных инструкций
            await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text=instruction["text"]
            )
        
        # Отправляем изображение, если оно есть
        if "image" in instruction and instruction["image"]:
            try:
                with open(instruction["image"], "rb") as photo:
                    await bot.send_photo(
                        chat_id=callback_query.message.chat.id,
                        photo=photo
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке изображения {instruction['image']}: {e}")
                await bot.send_message(
                    chat_id=callback_query.message.chat.id,
                    text="Не удалось загрузить изображение с инструкцией"
                )
    
    # Отправляем сообщение с кнопкой "Попробовать снова"
    await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text="После изменения настроек, попробуйте снова:",
        reply_markup=get_privacy_issue_keyboard()
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "try_again")
async def process_try_again(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик повторной попытки добавления после изменения настроек приватности"""
    # Получаем данные из состояния
    data = await state.get_data()
    chat_id = data.get("selected_chat_id")
    
    if not chat_id:
        # Если чат не выбран, возвращаем к выбору чата
        await state.set_state(UserStates.selecting_chat)
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=MESSAGES["select_chat"],
            reply_markup=get_chat_selection_keyboard()
        )
        await callback_query.answer()
        return
    
    # Пытаемся добавить пользователя в чат
    success, message, additional_data = await chat_manager.add_user_to_chat(
        user_id=callback_query.from_user.id,
        chat_id=chat_id
    )
    
    if success:
        # Если пользователь успешно добавлен
        # Получаем информацию о чате
        chat_info = await chat_manager.get_chat_info(chat_id)
        
        # Отправляем сообщение об успешном добавлении
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=chat_info["welcome_message"] or MESSAGES["success_join"],
            reply_markup=None
        )
        
        # Отправляем уведомление админам
        if additional_data and "admin_username" in additional_data:
            admin_username = additional_data["admin_username"]
            
            # Отправляем уведомление всем админам
            user_name = callback_query.from_user.username or f"id{callback_query.from_user.id}"
            notify_message = MESSAGES["admin_notification"].format(
                username=user_name,
                chat_name=chat_info["name"],
                admin_username=admin_username
            )
            await notify_admins(notify_message, callback_query.from_user.id, chat_id)
    else:
        # Если все еще есть проблемы с добавлением
        if additional_data and additional_data.get("privacy_restricted"):
            # Если проблема с настройками приватности
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text="К сожалению, проблема с настройками приватности все еще существует. "
                     "Убедитесь, что вы правильно изменили настройки, или свяжитесь с админом.",
                reply_markup=get_privacy_issue_keyboard()
            )
        else:
            # Если другая ошибка
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=f"Ошибка при добавлении в чат: {message}",
                reply_markup=get_chat_selection_keyboard()
            )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "contact_admin")
async def process_contact_admin(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик запроса связи с админом"""
    # Получаем контактную информацию администратора
    admin_info = get_admin_contact_info()
    
    # Формируем текст сообщения
    contact_text = (
        "Для связи с администратором, используйте один из способов:\n\n"
        f"• Telegram: @{admin_info['username']}\n"
        f"• Email: {admin_info['email']}\n"
        f"• Телефон: {admin_info['phone']}\n\n"
        "Или оставьте сообщение прямо здесь, отправив его в ответ на это сообщение."
    )
    
    # Отправляем сообщение
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=contact_text,
        reply_markup=get_privacy_issue_keyboard()
    )
    
    # Устанавливаем состояние для обратной связи
    await state.set_state(UserStates.feedback)
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.message(UserStates.feedback)
async def process_feedback(message: types.Message, state: FSMContext):
    """Обработчик сообщений обратной связи"""
    # Получаем данные из состояния
    data = await state.get_data()
    chat_id = data.get("selected_chat_id")
    
    # Формируем сообщение для администраторов
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"id{message.from_user.id}"
    admin_message = (
        f"📬 Новое сообщение от пользователя {user_info}:\n\n"
        f"{message.text}\n\n"
        f"ID пользователя: {message.from_user.id}\n"
        f"Имя: {message.from_user.first_name} {message.from_user.last_name or ''}"
    )
    
    # Отправляем сообщение администраторам
    await notify_admins(admin_message, message.from_user.id, chat_id)
    
    # Отправляем подтверждение пользователю
    await message.answer("✅ Ваше сообщение отправлено администратору! Ожидайте ответа.")
    
    # Возвращаем пользователя к начальному меню
    await state.clear()
    await message.answer(
        MESSAGES["welcome"],
        reply_markup=get_start_keyboard()
    )

@dp.callback_query(lambda c: c.data == "back_to_start")
async def process_back_to_start(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик возврата в начальное меню"""
    # Сбрасываем состояние
    await state.clear()
    
    # Отправляем начальное меню
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=MESSAGES["welcome"],
        reply_markup=get_start_keyboard()
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "info")
async def process_info(callback_query: types.CallbackQuery):
    """Обработчик кнопки информации"""
    # Отправляем информацию о боте и чатах
    info_text = (
        "ℹ️ Информация о чатах:\n\n"
        f"1. {CHATS[CHAT_ID_1]['name']}\n"
        f"{CHATS[CHAT_ID_1]['description']}\n\n"
        f"2. {CHATS[CHAT_ID_2]['name']}\n"
        f"{CHATS[CHAT_ID_2]['description']}\n\n"
        "Для вступления в один из чатов используйте кнопку 'Хочу в чат' в главном меню."
    )
    
    back_btn = InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="back_to_start")
    
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=info_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "support")
async def process_support(callback_query: types.CallbackQuery):
    """Обработчик кнопки поддержки"""
    # Получаем контактную информацию администратора
    admin_info = get_admin_contact_info()
    
    # Отправляем информацию о поддержке
    support_text = (
        "🆘 Поддержка\n\n"
        "Если у вас возникли проблемы или вопросы, вы можете:\n"
        f"• Написать администратору: @{admin_info['username']}\n"
        f"• Отправить письмо: {admin_info['email']}\n\n"
        "Спасибо за использование нашего бота!"
    )
    
    back_btn = InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="back_to_start")
    
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=support_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

# Обработчик неизвестных callback_query
@dp.callback_query()
async def process_unknown_callback(callback_query: types.CallbackQuery):
    """Обработчик всех остальных callback_query"""
    await callback_query.answer("Неизвестная команда или устаревшая кнопка")

# Обработчик всех остальных сообщений
@dp.message()
async def process_other_messages(message: types.Message):
    """Обработчик всех остальных сообщений"""
    # Отправляем сообщение с инструкцией
    await message.answer(
        "Я не понимаю такую команду. Пожалуйста, используйте кнопки для навигации.",
        reply_markup=get_start_keyboard()
    )

# Функция для запуска бота
async def start_bot():
    """Запускает бота"""
    try:
        # Запускаем бота
        logger.info("Запуск бота...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        # Не пробрасываем исключение, чтобы не прерывать работу программы
        # Вместо этого логируем ошибку и возвращаем False
        return False
    return True 