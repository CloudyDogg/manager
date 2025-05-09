import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, CHAT_ID_1, CHAT_ID_2, CHATS, MESSAGES, PRIVACY_INSTRUCTIONS
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

# Функции для создания клавиатур
def get_start_keyboard():
    """Создает клавиатуру для начального меню"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопку выбора чата
    keyboard.add(InlineKeyboardButton(text="Хочу в чат 🔐", callback_data="select_chat"))
    
    # Добавляем кнопки информации и поддержки
    keyboard.add(
        InlineKeyboardButton(text=MESSAGES["info_button"], callback_data="info"),
        InlineKeyboardButton(text=MESSAGES["support_button"], callback_data="support")
    )
    
    return keyboard

def get_chat_selection_keyboard():
    """Создает клавиатуру для выбора чата"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопки для выбора чатов
    keyboard.add(
        InlineKeyboardButton(
            text=CHATS[CHAT_ID_1]["name"],
            callback_data=f"join_chat:{CHAT_ID_1}"
        ),
        InlineKeyboardButton(
            text=CHATS[CHAT_ID_2]["name"],
            callback_data=f"join_chat:{CHAT_ID_2}"
        ),
    )
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="back_to_start"
    ))
    
    return keyboard

def get_confirm_join_keyboard(chat_id):
    """Создает клавиатуру для подтверждения вступления в чат"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопку подтверждения
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["join_button"],
        callback_data=f"confirm_join:{chat_id}"
    ))
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="select_chat"
    ))
    
    return keyboard

def get_privacy_issue_keyboard():
    """Создает клавиатуру для случая, когда есть проблемы с приватностью"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Добавляем кнопку с инструкцией
    keyboard.add(InlineKeyboardButton(
        text="Показать инструкцию 📋",
        callback_data="show_privacy_instructions"
    ))
    
    # Добавляем кнопку повторной попытки
    keyboard.add(InlineKeyboardButton(
        text="Попробовать снова 🔄",
        callback_data="try_again"
    ))
    
    # Добавляем кнопку связи с админом
    keyboard.add(InlineKeyboardButton(
        text="Связаться с админом 👨‍💼",
        callback_data="contact_admin"
    ))
    
    # Добавляем кнопку назад
    keyboard.add(InlineKeyboardButton(
        text=MESSAGES["back_button"],
        callback_data="select_chat"
    ))
    
    return keyboard

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
    chat_info = chat_manager.get_chat_info(chat_id)
    
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
        chat_info = chat_manager.get_chat_info(chat_id)
        
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
            
            # Отправляем сообщение в группу админов или конкретному админу
            # Тут нужно будет указать ID чата для админов
            # await bot.send_message(
            #     chat_id=ADMIN_CHAT_ID,
            #     text=MESSAGES["admin_notification"].format(
            #         username=callback_query.from_user.username or callback_query.from_user.id,
            #         chat_name=chat_info["name"],
            #         admin_username=admin_username
            #     )
            # )
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
            with open(instruction["image"], "rb") as photo:
                await bot.send_photo(
                    chat_id=callback_query.message.chat.id,
                    photo=photo
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
        chat_info = chat_manager.get_chat_info(chat_id)
        
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
            
            # Отправляем сообщение в группу админов или конкретному админу
            # Тут нужно будет указать ID чата для админов
            # await bot.send_message(
            #     chat_id=ADMIN_CHAT_ID,
            #     text=MESSAGES["admin_notification"].format(
            #         username=callback_query.from_user.username or callback_query.from_user.id,
            #         chat_name=chat_info["name"],
            #         admin_username=admin_username
            #     )
            # )
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
    # Здесь можно добавить логику для связи с админом
    # Например, отправить сообщение админу или дать контактную информацию
    
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Для связи с администратором, напишите сообщение в @admin_username или свяжитесь по почте admin@example.com",
        reply_markup=get_privacy_issue_keyboard()
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

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
    
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=info_text,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="back_to_start")
        )
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "support")
async def process_support(callback_query: types.CallbackQuery):
    """Обработчик кнопки поддержки"""
    # Отправляем информацию о поддержке
    support_text = (
        "🆘 Поддержка\n\n"
        "Если у вас возникли проблемы или вопросы, вы можете:\n"
        "• Написать администратору: @admin_username\n"
        "• Отправить письмо: support@example.com\n\n"
        "Спасибо за использование нашего бота!"
    )
    
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=support_text,
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton(text=MESSAGES["back_button"], callback_data="back_to_start")
        )
    )
    
    # Отвечаем на callback query
    await callback_query.answer()

# Функция для запуска бота
async def start_bot():
    """Запускает бота"""
    try:
        # Запускаем бота
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise e 