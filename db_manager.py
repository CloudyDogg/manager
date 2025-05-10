from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, DateTime, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
from config import DATABASE_URL, CHAT_ID_1, CHAT_ID_2, MAX_ADDS_PER_DAY
import logging

# Создание базы данных
engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

class AdminAccount(Base):
    """Модель для хранения данных об аккаунтах администраторов"""
    __tablename__ = 'admin_accounts'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False, unique=True)
    session_file = Column(String(255), nullable=False)  # Путь к файлу сессии
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_used = Column(DateTime, nullable=True)
    daily_count = Column(Integer, default=0)  # Количество добавлений за день
    count_reset_date = Column(DateTime, default=datetime.datetime.utcnow)  # Дата сброса счётчика
    
    # Добавление привязки к чатам
    chat_1_access = Column(Boolean, default=True)  # Доступ к чату 1
    chat_2_access = Column(Boolean, default=True)  # Доступ к чату 2

class User(Base):
    """Модель для хранения данных о пользователях"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    is_banned = Column(Boolean, default=False)

class ChatMember(Base):
    """Модель для хранения связи пользователя с чатом"""
    __tablename__ = 'chat_members'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    chat_id = Column(Integer, nullable=False)  # ID чата в Telegram
    added_by = Column(Integer, ForeignKey('admin_accounts.id'), nullable=True)  # Какой админ добавил
    added_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    user = relationship("User")
    admin = relationship("AdminAccount")

class JoinRequest(Base):
    """Модель для хранения заявок на вступление в чат"""
    __tablename__ = 'join_requests'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    chat_id = Column(Integer, nullable=False)  # ID чата в Telegram
    status = Column(String(20), default='pending')  # pending, approved, rejected, manual_needed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    processed_by = Column(Integer, ForeignKey('admin_accounts.id'), nullable=True)  # Какой админ обработал
    
    user = relationship("User")
    admin = relationship("AdminAccount")

class ChatSettings(Base):
    """Модель для хранения настроек чатов"""
    __tablename__ = 'chat_settings'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, nullable=False, unique=True)  # ID чата в Telegram
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    welcome_message = Column(Text, nullable=True)
    join_mode = Column(String(20), default='auto_approve')  # auto_approve, admin_approve, questions
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.datetime.utcnow)

class Log(Base):
    """Модель для хранения логов действий"""
    __tablename__ = 'logs'
    
    id = Column(Integer, primary_key=True)
    action = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    user_id = Column(Integer, nullable=True)  # ID пользователя Telegram
    admin_id = Column(Integer, nullable=True)  # ID аккаунта админа
    chat_id = Column(Integer, nullable=True)  # ID чата
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# Создание таблиц в базе данных
def init_db():
    Base.metadata.create_all(engine)

class DBManager:
    """Класс для работы с базой данных"""
    
    @staticmethod
    def check_connection():
        """Проверяет соединение с базой данных"""
        session = Session()
        try:
            # Выполняем простой запрос для проверки соединения
            session.execute("SELECT 1")
            return True
        except Exception as e:
            logging.error(f"Ошибка при подключении к базе данных: {e}")
            return False
        finally:
            session.close()
    
    @staticmethod
    def get_session():
        """Возвращает сессию SQLAlchemy"""
        return Session()
    
    @staticmethod
    def add_user(user_id, username=None, first_name=None, last_name=None):
        """Добавляет пользователя в базу данных или возвращает существующего"""
        session = Session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                user = User(
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name
                )
                session.add(user)
                session.commit()
            return user
        finally:
            session.close()
    
    @staticmethod
    def create_join_request(user_id, chat_id):
        """Создает заявку на вступление в чат"""
        session = Session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                return None
            
            request = JoinRequest(
                user_id=user.id,
                chat_id=chat_id
            )
            session.add(request)
            session.commit()
            return request
        finally:
            session.close()
    
    @staticmethod
    def get_admin_for_chat(chat_id):
        """Возвращает доступный аккаунт админа для добавления пользователя в чат"""
        session = Session()
        try:
            # Получаем текущую дату UTC
            current_date = datetime.datetime.utcnow().date()
            
            # Получаем все активные аккаунты с доступом к указанному чату
            query = session.query(AdminAccount).filter(AdminAccount.is_active == True)
            
            # Проверяем доступ к конкретному чату (логика используется для совместимости)
            # Важно: chat_id может быть полным ID из Telegram (например, -1002698797779)
            # Но в базе данных мы используем 1 и 2 для чатов 1 и 2
            if chat_id == CHAT_ID_1 or str(chat_id) == str(CHAT_ID_1):
                query = query.filter(AdminAccount.chat_1_access == True)
            elif chat_id == CHAT_ID_2 or str(chat_id) == str(CHAT_ID_2):
                query = query.filter(AdminAccount.chat_2_access == True)
            
            # Получаем аккаунты, у которых не исчерпан лимит добавлений за день
            admins = query.all()
            
            for admin in admins:
                # Если дата сброса счетчика не совпадает с текущей, сбрасываем счетчик
                if admin.count_reset_date.date() != current_date:
                    admin.daily_count = 0
                    admin.count_reset_date = datetime.datetime.utcnow()
                    session.commit()
                
                # Если не достигнут лимит добавлений за день, возвращаем этот аккаунт
                if admin.daily_count < MAX_ADDS_PER_DAY:
                    return admin
            
            # Если нет доступных аккаунтов, возвращаем None
            return None
        finally:
            session.close()
    
    @staticmethod
    def update_admin_usage(admin_id):
        """Обновляет счетчик использования аккаунта админа"""
        session = Session()
        try:
            admin = session.query(AdminAccount).filter(AdminAccount.id == admin_id).first()
            if admin:
                admin.last_used = datetime.datetime.utcnow()
                admin.daily_count += 1
                session.commit()
        finally:
            session.close()
    
    @staticmethod
    def log_action(action, description=None, user_id=None, admin_id=None, chat_id=None):
        """Логирует действие в базе данных"""
        session = Session()
        try:
            log = Log(
                action=action,
                description=description,
                user_id=user_id,
                admin_id=admin_id,
                chat_id=chat_id
            )
            session.add(log)
            session.commit()
        finally:
            session.close()
    
    @staticmethod
    def add_chat_member(user_id, chat_id, admin_id):
        """Добавляет запись о пользователе в чате"""
        session = Session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                return False
            
            member = ChatMember(
                user_id=user.id,
                chat_id=chat_id,
                added_by=admin_id
            )
            session.add(member)
            session.commit()
            return True
        finally:
            session.close()
    
    @staticmethod
    def update_join_request(request_id, status, admin_id=None):
        """Обновляет статус заявки на вступление"""
        session = Session()
        try:
            request = session.query(JoinRequest).filter(JoinRequest.id == request_id).first()
            if request:
                request.status = status
                request.processed_at = datetime.datetime.utcnow()
                request.processed_by = admin_id
                session.commit()
                return True
            return False
        finally:
            session.close()
    
    @staticmethod
    def get_pending_requests():
        """Возвращает список ожидающих заявок на вступление"""
        session = Session()
        try:
            requests = session.query(JoinRequest).filter(
                JoinRequest.status == 'pending'
            ).order_by(JoinRequest.created_at).all()
            return requests
        finally:
            session.close()
    
    @staticmethod
    def get_chat_settings(chat_id):
        """Возвращает настройки чата"""
        session = Session()
        try:
            settings = session.query(ChatSettings).filter(ChatSettings.chat_id == chat_id).first()
            return settings
        finally:
            session.close()
    
    @staticmethod
    def update_chat_settings(chat_id, name=None, description=None, welcome_message=None, join_mode=None, is_active=None):
        """Обновляет настройки чата"""
        session = Session()
        try:
            settings = session.query(ChatSettings).filter(ChatSettings.chat_id == chat_id).first()
            
            if not settings:
                settings = ChatSettings(chat_id=chat_id)
                session.add(settings)
            
            if name is not None:
                settings.name = name
            
            if description is not None:
                settings.description = description
            
            if welcome_message is not None:
                settings.welcome_message = welcome_message
            
            if join_mode is not None:
                settings.join_mode = join_mode
            
            if is_active is not None:
                settings.is_active = is_active
            
            settings.updated_at = datetime.datetime.utcnow()
            session.commit()
            return True
        finally:
            session.close()

# Инициализация базы данных при импорте модуля
init_db() 