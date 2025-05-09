import os
import json
import logging
from pathlib import Path
from cryptography.fernet import Fernet
from pyrogram import Client
from config import API_ID, API_HASH, ENCRYPTION_KEY, SESSIONS_DIR
from db_manager import DBManager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionManager:
    """Класс для управления сессиями Pyrogram"""
    
    def __init__(self):
        """Инициализация менеджера сессий"""
        self.clients = {}  # Словарь активных клиентов (id: client)
        self.fernet = Fernet(self._get_or_create_key())
    
    def _get_or_create_key(self):
        """Получает ключ шифрования или создает новый"""
        key = ENCRYPTION_KEY
        if not key or len(key) < 32:
            # Генерация нового ключа, если не задан или неверной длины
            key = Fernet.generate_key().decode()
            logger.warning(f"Сгенерирован новый ключ шифрования. Рекомендуется сохранить его в .env файле.")
        
        # Убедимся, что ключ имеет правильный формат для Fernet
        if len(key) < 32:
            key = key.ljust(32, '0')
        
        # Преобразуем в байты, если нужно
        if isinstance(key, str):
            key = key.encode()
        
        return key
    
    def encrypt_session(self, session_string):
        """Шифрует строку сессии"""
        if isinstance(session_string, str):
            session_string = session_string.encode()
        
        return self.fernet.encrypt(session_string)
    
    def decrypt_session(self, encrypted_session):
        """Расшифровывает строку сессии"""
        if isinstance(encrypted_session, str):
            encrypted_session = encrypted_session.encode()
        
        return self.fernet.decrypt(encrypted_session)
    
    def save_session(self, phone, session_string):
        """Сохраняет сессию в зашифрованном виде"""
        session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
        
        # Шифруем и сохраняем
        encrypted_session = self.encrypt_session(session_string)
        with open(session_path, 'wb') as f:
            f.write(encrypted_session)
        
        return session_path
    
    def load_session(self, phone):
        """Загружает сессию из файла"""
        session_path = os.path.join(SESSIONS_DIR, f"{phone}.session")
        
        if not os.path.exists(session_path):
            logger.error(f"Сессия для {phone} не найдена")
            return None
        
        try:
            with open(session_path, 'rb') as f:
                encrypted_session = f.read()
            
            # Расшифровываем сессию
            session_string = self.decrypt_session(encrypted_session)
            return session_string.decode()
        except Exception as e:
            logger.error(f"Ошибка при загрузке сессии {phone}: {e}")
            return None
    
    async def start_client(self, admin_id):
        """Запускает клиент Pyrogram для указанного админа"""
        if admin_id in self.clients:
            # Клиент уже запущен
            return self.clients[admin_id]
        
        # Получаем данные админа из БД
        session = DBManager.get_session()
        try:
            from db_manager import AdminAccount
            admin = session.query(AdminAccount).filter(AdminAccount.id == admin_id).first()
            if not admin:
                logger.error(f"Админ с ID {admin_id} не найден")
                return None
            
            # Загружаем сессию
            session_string = self.load_session(admin.phone)
            if not session_string:
                logger.error(f"Не удалось загрузить сессию для админа {admin.username}")
                return None
            
            # Создаем и запускаем клиент
            client = Client(
                name=f"admin_{admin_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
                in_memory=True  # Сессия хранится в памяти
            )
            
            await client.start()
            self.clients[admin_id] = client
            
            # Обновляем информацию в БД
            admin.is_active = True
            session.commit()
            
            logger.info(f"Клиент для админа {admin.username} ({admin.phone}) успешно запущен")
            return client
        except Exception as e:
            logger.error(f"Ошибка при запуске клиента для админа {admin_id}: {e}")
            return None
        finally:
            session.close()
    
    async def stop_client(self, admin_id):
        """Останавливает клиент Pyrogram"""
        if admin_id not in self.clients:
            return False
        
        try:
            client = self.clients[admin_id]
            await client.stop()
            del self.clients[admin_id]
            
            logger.info(f"Клиент для админа {admin_id} успешно остановлен")
            return True
        except Exception as e:
            logger.error(f"Ошибка при остановке клиента для админа {admin_id}: {e}")
            return False
    
    async def stop_all_clients(self):
        """Останавливает все клиенты Pyrogram"""
        for admin_id in list(self.clients.keys()):
            await self.stop_client(admin_id)
    
    async def add_chat_member(self, admin_id, chat_id, user_id):
        """Добавляет пользователя в чат через аккаунт админа"""
        client = self.clients.get(admin_id)
        if not client:
            # Пытаемся запустить клиент
            client = await self.start_client(admin_id)
            if not client:
                return False, "Не удалось запустить клиент админа"
        
        try:
            # Пытаемся добавить пользователя в чат
            result = await client.add_chat_members(chat_id, user_id)
            
            # Обновляем счетчик использования админа
            DBManager.update_admin_usage(admin_id)
            
            # Логируем действие
            DBManager.log_action(
                action="add_chat_member",
                description=f"Пользователь {user_id} добавлен в чат {chat_id}",
                user_id=user_id,
                admin_id=admin_id,
                chat_id=chat_id
            )
            
            return True, "Пользователь успешно добавлен в чат"
        except Exception as e:
            error_message = str(e)
            
            # Логируем ошибку
            DBManager.log_action(
                action="add_chat_member_error",
                description=f"Ошибка при добавлении пользователя {user_id} в чат {chat_id}: {error_message}",
                user_id=user_id,
                admin_id=admin_id,
                chat_id=chat_id
            )
            
            return False, error_message
    
    async def create_new_session(self, phone, code_callback):
        """Создает новую сессию для админа (используется при регистрации нового аккаунта)"""
        try:
            # Создаем временный клиент для авторизации
            temp_client = Client(
                name=f"temp_{phone}",
                api_id=API_ID,
                api_hash=API_HASH,
                phone_number=phone,
                in_memory=True
            )
            
            # Запускаем клиент
            await temp_client.connect()
            
            # Отправляем код авторизации
            sent_code = await temp_client.send_code(phone)
            
            # Получаем код от пользователя через callback
            phone_code = await code_callback(sent_code.phone_code_hash)
            
            # Пытаемся войти
            await temp_client.sign_in(phone, sent_code.phone_code_hash, phone_code)
            
            # Если требуется 2FA, обрабатываем это
            if await temp_client.get_me() is None:
                password = await code_callback(None)  # Запрашиваем пароль
                await temp_client.check_password(password)
            
            # Получаем строку сессии
            session_string = await temp_client.export_session_string()
            
            # Сохраняем сессию
            session_path = self.save_session(phone, session_string)
            
            # Получаем информацию о пользователе
            me = await temp_client.get_me()
            
            # Останавливаем временный клиент
            await temp_client.disconnect()
            
            # Возвращаем информацию о новой сессии
            return {
                'success': True,
                'phone': phone,
                'username': me.username,
                'session_file': session_path,
                'user_id': me.id
            }
        except Exception as e:
            logger.error(f"Ошибка при создании сессии для {phone}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

# Создаем глобальный экземпляр менеджера сессий
session_manager = SessionManager() 