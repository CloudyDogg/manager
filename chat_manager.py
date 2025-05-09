import logging
from typing import Tuple, Optional, Dict, Any
from db_manager import DBManager
from session_manager import session_manager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ChatManager:
    """Класс для управления чатами и добавлением пользователей"""
    
    @staticmethod
    async def add_user_to_chat(user_id: int, chat_id: int) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Добавляет пользователя в чат
        
        Args:
            user_id: ID пользователя Telegram
            chat_id: ID чата Telegram
            
        Returns:
            Tuple с результатом (успех, сообщение, доп.данные)
        """
        try:
            # Получаем доступный аккаунт админа для добавления
            admin_account = DBManager.get_admin_for_chat(chat_id)
            if not admin_account:
                logger.error(f"Нет доступных админов для добавления пользователя {user_id} в чат {chat_id}")
                return False, "Нет доступных админов для добавления", None
            
            # Пытаемся добавить пользователя
            success, message = await session_manager.add_chat_member(
                admin_id=admin_account.id,
                chat_id=chat_id,
                user_id=user_id
            )
            
            if success:
                # Добавляем запись о пользователе в чате в БД
                DBManager.add_chat_member(user_id, chat_id, admin_account.id)
                
                # Возвращаем успешный результат с данными об админе
                return True, "Пользователь успешно добавлен", {
                    "admin_id": admin_account.id,
                    "admin_username": admin_account.username
                }
            else:
                # Проверяем, связана ли ошибка с настройками приватности
                if "USER_PRIVACY_RESTRICTED" in message:
                    # Обновляем статус заявки
                    join_request = DBManager.create_join_request(user_id, chat_id)
                    if join_request:
                        DBManager.update_join_request(join_request.id, "manual_needed")
                    
                    # Возвращаем ошибку с флагом приватности
                    return False, "Ограничения приватности пользователя", {
                        "privacy_restricted": True
                    }
                
                # Другие ошибки
                return False, f"Ошибка при добавлении: {message}", None
                
        except Exception as e:
            logger.error(f"Ошибка при добавлении пользователя {user_id} в чат {chat_id}: {e}")
            return False, f"Внутренняя ошибка: {str(e)}", None
    
    @staticmethod
    async def check_user_can_be_added(user_id: int, chat_id: int) -> Tuple[bool, Optional[str]]:
        """
        Проверяет, может ли пользователь быть добавлен в чат
        
        Args:
            user_id: ID пользователя Telegram
            chat_id: ID чата Telegram
            
        Returns:
            Tuple с результатом (можно добавить, причина если нельзя)
        """
        try:
            # Проверяем, не находится ли пользователь в черном списке
            session = DBManager.get_session()
            from db_manager import User
            user = session.query(User).filter(User.user_id == user_id).first()
            
            if user and user.is_banned:
                return False, "Пользователь заблокирован"
            
            # Проверяем, не состоит ли пользователь уже в чате
            from db_manager import ChatMember
            member = session.query(ChatMember).join(User).filter(
                User.user_id == user_id,
                ChatMember.chat_id == chat_id
            ).first()
            
            if member:
                return False, "Пользователь уже состоит в чате"
            
            # Тут можно добавить дополнительные проверки, например:
            # - Проверка на подозрительную активность
            # - Проверка на спам-аккаунты
            # - и т.д.
            
            return True, None
        except Exception as e:
            logger.error(f"Ошибка при проверке пользователя {user_id}: {e}")
            return False, f"Внутренняя ошибка: {str(e)}"
        finally:
            session.close()
    
    @staticmethod
    async def process_manual_join_request(request_id: int, approved: bool, admin_id: int) -> Tuple[bool, str]:
        """
        Обрабатывает заявку на вступление вручную
        
        Args:
            request_id: ID заявки
            approved: Одобрена или отклонена
            admin_id: ID админа, который обрабатывает заявку
            
        Returns:
            Tuple с результатом (успех, сообщение)
        """
        try:
            # Получаем заявку
            session = DBManager.get_session()
            from db_manager import JoinRequest, User
            request = session.query(JoinRequest).filter(JoinRequest.id == request_id).first()
            
            if not request:
                return False, "Заявка не найдена"
            
            # Получаем пользователя
            user = session.query(User).filter(User.id == request.user_id).first()
            if not user:
                return False, "Пользователь не найден"
            
            # Обрабатываем заявку
            if approved:
                # Обновляем статус заявки
                DBManager.update_join_request(request_id, "approved", admin_id)
                
                # Добавляем запись о пользователе в чате
                DBManager.add_chat_member(user.user_id, request.chat_id, admin_id)
                
                # Логируем действие
                DBManager.log_action(
                    action="manual_approve_request",
                    description=f"Админ {admin_id} вручную одобрил заявку {request_id}",
                    user_id=user.user_id,
                    admin_id=admin_id,
                    chat_id=request.chat_id
                )
                
                return True, "Заявка успешно одобрена"
            else:
                # Обновляем статус заявки
                DBManager.update_join_request(request_id, "rejected", admin_id)
                
                # Логируем действие
                DBManager.log_action(
                    action="manual_reject_request",
                    description=f"Админ {admin_id} отклонил заявку {request_id}",
                    user_id=user.user_id,
                    admin_id=admin_id,
                    chat_id=request.chat_id
                )
                
                return True, "Заявка отклонена"
        except Exception as e:
            logger.error(f"Ошибка при обработке заявки {request_id}: {e}")
            return False, f"Внутренняя ошибка: {str(e)}"
        finally:
            session.close()
    
    @staticmethod
    def get_chat_info(chat_id: int) -> Dict[str, Any]:
        """
        Получает информацию о чате
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Словарь с информацией о чате
        """
        # Получаем настройки чата из БД
        settings = DBManager.get_chat_settings(chat_id)
        
        # Если настройки не найдены, используем значения по умолчанию из конфигурации
        if not settings:
            # Импортируем тут, чтобы избежать циклических импортов
            from config import CHATS
            chat_info = CHATS.get(chat_id, {})
            
            return {
                "id": chat_id,
                "name": chat_info.get("name", f"Чат {chat_id}"),
                "description": chat_info.get("description", ""),
                "join_mode": "auto_approve",
                "is_active": True,
                "welcome_message": chat_info.get("welcome_message", "Добро пожаловать! 🎉")
            }
        
        # Возвращаем информацию из настроек
        return {
            "id": chat_id,
            "name": settings.name,
            "description": settings.description,
            "join_mode": settings.join_mode,
            "is_active": settings.is_active,
            "welcome_message": settings.welcome_message or "Добро пожаловать! 🎉"
        }
    
    @staticmethod
    def update_chat_settings(chat_id: int, **kwargs) -> bool:
        """
        Обновляет настройки чата
        
        Args:
            chat_id: ID чата Telegram
            **kwargs: Параметры для обновления (name, description, welcome_message, join_mode, is_active)
            
        Returns:
            True если успешно, иначе False
        """
        try:
            result = DBManager.update_chat_settings(chat_id, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Ошибка при обновлении настроек чата {chat_id}: {e}")
            return False

# Создаем глобальный экземпляр менеджера чатов
chat_manager = ChatManager() 