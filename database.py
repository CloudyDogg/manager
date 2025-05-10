import os
import json
import base64
import hashlib
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
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
    chat_id = Column(Integer)
    status = Column(String, default="pending")  # pending, approved, rejected
    created_at = Column(DateTime, default=datetime.now)
    
def init_db():
    Base.metadata.create_all(engine)
    
def get_session():
    return Session() 