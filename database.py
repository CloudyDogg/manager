import os
import json
import base64
import hashlib
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime, func, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()

# Инициализация базы данных
database_url = os.getenv("DATABASE_URL")
engine = create_engine(database_url)
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Подготовка ключа шифрования
def get_fernet_key(password):
    # Преобразуем пароль в 32-байтный ключ
    key = hashlib.sha256(password.encode()).digest()
    # Кодируем в base64 в URL-safe формате
    return base64.urlsafe_b64encode(key)

# Инициализация шифрования
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
cipher_suite = Fernet(get_fernet_key(ENCRYPTION_KEY))

# Функция для шифрования данных сессии
def encrypt_session(session_data):
    return cipher_suite.encrypt(json.dumps(session_data).encode()).decode()

# Функция для расшифровки данных сессии
def decrypt_session(encrypted_data):
    return json.loads(cipher_suite.decrypt(encrypted_data.encode()).decode())

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    registration_date = Column(DateTime, default=datetime.now)
    is_blacklisted = Column(Boolean, default=False)
    chat_joined = Column(Integer, nullable=True)  # ID чата, к которому присоединился
    
class AdminAccount(Base):
    __tablename__ = "admin_accounts"
    
    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True)
    active = Column(Boolean, default=True)
    last_used = Column(DateTime, default=datetime.now)
    usage_count = Column(Integer, default=0)
    session_data = Column(Text)  # Зашифрованные данные сессии
    
class JoinRequest(Base):
    __tablename__ = "join_requests"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    chat_id = Column(BigInteger)
    status = Column(String, default="pending")  # pending, approved, rejected, link_sent, manual_check, contact_sent
    created_at = Column(DateTime, default=datetime.now)
    approved_by = Column(Integer, nullable=True)  # ID администратора, который обработал заявку
    approved_at = Column(DateTime, nullable=True)  # Время обработки заявки

class RateLimitBlock(Base):
    __tablename__ = "rate_limit_blocks"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    blocked_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)
    reason = Column(String, default="rate_limit_exceeded")  # Причина блокировки
    
class Settings(Base):
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    value = Column(String)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
def get_setting(key, default=None):
    """Получить значение настройки по ключу"""
    session = Session()
    try:
        setting = session.query(Settings).filter_by(key=key).first()
        return setting.value if setting else default
    finally:
        session.close()

def set_setting(key, value):
    """Установить значение настройки"""
    session = Session()
    try:
        setting = session.query(Settings).filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = Settings(key=key, value=value)
            session.add(setting)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        return False
    finally:
        session.close()

def check_rate_limit(user_id, limit=5, period_minutes=1):
    """
    Проверяет, превышает ли пользователь лимит заявок за указанный период
    
    Args:
        user_id: ID пользователя
        limit: Максимальное количество заявок за период
        period_minutes: Период в минутах, за который считать заявки
        
    Returns:
        (bool, int): Первое значение - превышен ли лимит, второе - текущее количество заявок
    """
    session = Session()
    try:
        # Сначала проверяем, не заблокирован ли уже пользователь
        block = session.query(RateLimitBlock).filter_by(user_id=user_id, is_active=True).first()
        if block:
            return True, 0
            
        # Проверяем количество заявок за последние period_minutes минут
        time_limit = datetime.now() - timedelta(minutes=period_minutes)
        count = session.query(func.count(JoinRequest.id)).filter(
            JoinRequest.user_id == user_id,
            JoinRequest.created_at >= time_limit
        ).scalar()
        
        return count >= limit, count
    finally:
        session.close()

def block_user_rate_limit(user_id, reason="rate_limit_exceeded"):
    """
    Блокирует пользователя за превышение лимита запросов
    
    Args:
        user_id: ID пользователя
        reason: Причина блокировки
    
    Returns:
        bool: Успешно ли выполнена операция
    """
    session = Session()
    try:
        # Проверяем, есть ли уже блокировка
        block = session.query(RateLimitBlock).filter_by(user_id=user_id).first()
        
        if block:
            if not block.is_active:
                block.is_active = True
                block.blocked_at = datetime.now()
                block.reason = reason
        else:
            block = RateLimitBlock(
                user_id=user_id,
                is_active=True,
                reason=reason
            )
            session.add(block)
            
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        return False
    finally:
        session.close()

def unblock_user_rate_limit(user_id):
    """
    Разблокирует пользователя, заблокированного за превышение лимита
    
    Args:
        user_id: ID пользователя
    
    Returns:
        bool: Успешно ли выполнена операция
    """
    session = Session()
    try:
        block = session.query(RateLimitBlock).filter_by(user_id=user_id, is_active=True).first()
        if block:
            block.is_active = False
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        return False
    finally:
        session.close()

def get_rate_limited_users():
    """
    Возвращает список пользователей, заблокированных за превышение лимита
    
    Returns:
        list: Список объектов RateLimitBlock активных блокировок
    """
    session = Session()
    try:
        return session.query(RateLimitBlock).filter_by(is_active=True).all()
    finally:
        session.close()

def get_next_admin_account(current_account_id=None):
    """
    Получает следующий активный аккаунт администратора для ротации
    
    Args:
        current_account_id: ID текущего активного аккаунта (если известен)
    
    Returns:
        AdminAccount: Следующий активный аккаунт или None, если нет активных аккаунтов
    """
    session = Session()
    try:
        # Получаем все активные аккаунты, отсортированные по количеству использований
        active_accounts = session.query(AdminAccount).filter_by(active=True).order_by(AdminAccount.usage_count).all()
        
        if not active_accounts:
            return None
            
        # Если нет текущего аккаунта, просто возвращаем аккаунт с наименьшим использованием
        if current_account_id is None:
            return active_accounts[0]
            
        # Находим индекс текущего аккаунта
        current_index = -1
        for i, account in enumerate(active_accounts):
            if account.id == current_account_id:
                current_index = i
                break
                
        # Если текущий аккаунт не найден среди активных, возвращаем первый
        if current_index == -1:
            return active_accounts[0]
            
        # Иначе возвращаем следующий аккаунт (с циклическим переходом)
        next_index = (current_index + 1) % len(active_accounts)
        return active_accounts[next_index]
    finally:
        session.close()
    
def init_db():
    Base.metadata.create_all(engine)

def migrate_chat_id_to_bigint():
    """
    Миграция для изменения типа chat_id с Integer на BigInteger в таблице join_requests.
    Должна быть вызвана при первом запуске после обновления кода.
    """
    conn = engine.connect()
    try:
        # SQLite
        if database_url.startswith('sqlite'):
            # В SQLite нет прямого изменения типа, поэтому создаем новую таблицу и переносим данные
            conn.execute(text("""
                CREATE TABLE join_requests_new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    chat_id BIGINT,
                    status TEXT,
                    created_at TIMESTAMP,
                    approved_by INTEGER,
                    approved_at TIMESTAMP
                )
            """))
            conn.execute(text("""
                INSERT INTO join_requests_new 
                SELECT id, user_id, chat_id, status, created_at, approved_by, approved_at
                FROM join_requests
            """))
            conn.execute(text("DROP TABLE join_requests"))
            conn.execute(text("ALTER TABLE join_requests_new RENAME TO join_requests"))
        # PostgreSQL
        else:
            conn.execute(text("ALTER TABLE join_requests ALTER COLUMN chat_id TYPE BIGINT"))
        
        print("Миграция типа chat_id успешно завершена")
        return True
    except Exception as e:
        print(f"Ошибка при миграции: {e}")
        return False
    finally:
        conn.close()

def get_session():
    return Session() 