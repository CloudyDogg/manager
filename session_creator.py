import os
import json
import asyncio
import base64
import hashlib
from dotenv import load_dotenv
from pyrogram import Client
from cryptography.fernet import Fernet
import sqlite3
from database import init_db, get_session, AdminAccount, encrypt_session

# Загрузка переменных окружения
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# Удалена лишняя инициализация шифрования, используется функция из database.py

async def create_admin_session(phone_number):
    """
    Создание сессии администратора
    """
    print(f"Создание сессии для номера {phone_number}...")
    
    # Временный идентификатор сессии
    session_name = f"temp_session_{phone_number}"
    
    # Создаем клиент Pyrogram
    client = Client(
        session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        phone_number=phone_number
    )
    
    try:
        # Запускаем клиент (потребуется ввод кода подтверждения)
        await client.start()
        print(f"Авторизация успешна для номера {phone_number}")
        
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
        init_db()  # Убедимся, что база данных инициализирована
        session = get_session()
        
        # Проверяем, существует ли уже такой аккаунт
        existing_account = session.query(AdminAccount).filter_by(phone=phone_number).first()
        if existing_account:
            existing_account.session_data = encrypted_data
            session.commit()
            print(f"Обновлена сессия для аккаунта {phone_number}")
        else:
            new_account = AdminAccount(
                phone=phone_number,
                active=True,
                session_data=encrypted_data
            )
            session.add(new_account)
            session.commit()
            print(f"Добавлен новый аккаунт администратора {phone_number}")
            
        # Останавливаем клиент
        await client.stop()
        
        # Удаляем временные файлы сессии
        try:
            if os.path.exists(f"{session_name}.session"):
                os.remove(f"{session_name}.session")
            if os.path.exists(f"{session_name}.session-journal"):
                os.remove(f"{session_name}.session-journal")
        except Exception as e:
            print(f"Не удалось удалить временные файлы: {e}")
        
        print(f"Сессия для номера {phone_number} успешно создана и сохранена.")
        return True
    except Exception as e:
        print(f"Ошибка при создании сессии для номера {phone_number}: {e}")
        return False

async def main():
    print("=== Создание сессий администраторов для Telegram бота ===")
    print("Введите номер телефона в международном формате (например, +79123456789):")
    phone_number = input("> ")
    
    if not phone_number.startswith("+"):
        print("Номер телефона должен начинаться с '+' и кода страны")
        return
    
    success = await create_admin_session(phone_number)
    
    if success:
        print("\nСессия успешно создана и сохранена в базе данных.")
        print("Теперь вы можете запустить основного бота.")
    else:
        print("\nНе удалось создать сессию. Проверьте логи для деталей.")

if __name__ == "__main__":
    asyncio.run(main()) 