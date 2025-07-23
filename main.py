import asyncio
import logging
import html # Для правильного HTML-экранирования
import configparser
from pathlib import Path
import sqlite3 
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
import nest_asyncio
nest_asyncio.apply()
from typing import Optional

# --- Глобальные переменные для конфигурации ---
BOT_TOKEN = None
OWNER_ID = None # Будет загружен из config.ini
WAIT_MINUTES = 10
WELCOME_MESSAGE_TEMPLATE = "Добро пожаловать, {mention}!"
LOG_LEVEL_STR = "INFO"
CONFIG_FILE_PATH = Path('config.ini')
WELCOME_MESSAGE_FILE_PATH_STR = "welcome_template.html"
RULES_URL = ""
DB_PATH = Path('user_joins.db')
DELETE_WELCOME_AFTER_MINUTES = 10 
DELETE_INFO_MSG_AFTER_SECONDS = 5  

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Асинхронная функция для удаления сообщений ---
async def delete_message_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay_seconds: int):
    if delay_seconds <= 0: 
        return
    
    await asyncio.sleep(delay_seconds)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Сообщение {message_id} удалено из чата {chat_id} после задержки.")
    except BadRequest as e:
        logger.warning(f"Не удалось удалить сообщение {message_id} из чата {chat_id}: {e}")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при удалении сообщения {message_id} из чата {chat_id}: {e}")

# --- Функции для работы с БД ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS joined_users (
                user_id INTEGER,
                chat_id INTEGER,
                join_timestamp TEXT,
                username TEXT,
                full_name TEXT,
                is_bot INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id, join_timestamp)
            )
        ''')
        conn.commit()
        # Проверка и добавление столбца is_bot, если его нет (для обратной совместимости)
        cursor.execute("PRAGMA table_info(joined_users)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'is_bot' not in columns:
            logger.info("Столбец 'is_bot' не найден в 'joined_users'. Добавляю...")
            cursor.execute("ALTER TABLE joined_users ADD COLUMN is_bot INTEGER DEFAULT 0")
            conn.commit()
            logger.info("Столбец 'is_bot' добавлен.")

        logger.info(f"База данных '{DB_PATH}' инициализирована успешно.")

        # --- НОВАЯ ТАБЛИЦА ДЛЯ НАСТРОЕК ЧАТА ---
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                welcome_template TEXT
            )
        ''')
        conn.commit()
        logger.info("Таблица 'chat_settings' проверена/создана.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при инициализации БД '{DB_PATH}': {e}")
    finally:
        if conn:
            conn.close()

def add_joined_user_to_db(user_id: int, chat_id: int, username: str, full_name: str, is_bot_flag: bool = False):
    join_time = datetime.utcnow().isoformat()
    is_bot_int = 1 if is_bot_flag else 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO joined_users (user_id, chat_id, join_timestamp, username, full_name, is_bot)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, join_time, username, full_name, is_bot_int))
        conn.commit()
        user_type = "Бот" if is_bot_flag else "Пользователь"
        logger.info(f"{user_type} ID:{user_id} ({full_name or 'N/A'}) добавлен в БД для чата {chat_id}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при добавлении пользователя ID:{user_id} в БД: {e}")
    finally:
        if conn:
            conn.close()

def get_monthly_join_count(chat_id: int) -> int:
    current_month_str = datetime.utcnow().strftime('%Y-%m')
    count = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) 
            FROM joined_users 
            WHERE chat_id = ? AND strftime('%Y-%m', join_timestamp) = ? AND is_bot = 0
        ''', (chat_id, current_month_str))
        result = cursor.fetchone()
        if result:
            count = result[0]
        logger.debug(f"Запрошено кол-во присоединившихся (не ботов) за месяц {current_month_str} для чата {chat_id}: {count}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении статистики присоединившихся за месяц для чата {chat_id}: {e}")
    finally:
        if conn:
            conn.close()
    return count

# --- НОВЫЕ ФУНКЦИИ ДЛЯ НАСТРОЕК ЧАТА ---

def get_chat_settings(chat_id: int):
    """Возвращает словарь с настройками чата или None-значениями, если они не заданы."""
    settings = {"welcome_template": None}
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT welcome_template FROM chat_settings WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        if row:
            settings["welcome_template"] = row[0]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении настроек чата {chat_id}: {e}")
    finally:
        if conn:
            conn.close()
    return settings


def set_chat_welcome_template(chat_id: int, template_text: Optional[str]):
    """Создаёт или обновляет шаблон приветствия для заданного чата."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if template_text is None:
            # Сбрасываем шаблон: ставим NULL
            cursor.execute('UPDATE chat_settings SET welcome_template = NULL WHERE chat_id = ?', (chat_id,))
        else:
            cursor.execute('UPDATE chat_settings SET welcome_template = ? WHERE chat_id = ?', (template_text, chat_id))
            if cursor.rowcount == 0:
                cursor.execute('INSERT INTO chat_settings (chat_id, welcome_template) VALUES (?, ?)', (chat_id, template_text))
        conn.commit()
        logger.info(f"Шаблон приветствия для чата {chat_id} {'обновлён' if template_text else 'сброшен'}.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при сохранении шаблона приветствия для чата {chat_id}: {e}")
    finally:
        if conn:
            conn.close()

# --- Функции для работы с конфигурацией ---
def load_config_and_template():
    global BOT_TOKEN, OWNER_ID, WAIT_MINUTES, WELCOME_MESSAGE_TEMPLATE, LOG_LEVEL_STR
    global WELCOME_MESSAGE_FILE_PATH_STR, RULES_URL
    global DELETE_WELCOME_AFTER_MINUTES, DELETE_INFO_MSG_AFTER_SECONDS

    config_ok = True 
    config = configparser.ConfigParser()
    try:
        with CONFIG_FILE_PATH.open('r', encoding='utf-8') as f:
            config.read_file(f)
    except FileNotFoundError:
        logger.critical(f"Критическая ошибка: Файл конфигурации '{CONFIG_FILE_PATH}' не найден!")
        return False 

    if not config.has_section('TelegramBot'):
        logger.critical(f"Критическая ошибка: Секция [TelegramBot] не найдена в '{CONFIG_FILE_PATH}'.")
        return False

    try:
        BOT_TOKEN = config.get('TelegramBot', 'TOKEN')
        if not BOT_TOKEN:
            logger.critical("Критическая ошибка: Параметр TOKEN не найден или пуст в [TelegramBot].")
            config_ok = False
    except configparser.NoOptionError:
        logger.critical("Критическая ошибка: Параметр TOKEN не найден в [TelegramBot].")
        config_ok = False

    try:
        OWNER_ID = config.getint('TelegramBot', 'OWNER_ID')
    except (configparser.NoOptionError, ValueError):
        logger.warning(f"Параметр OWNER_ID не найден или имеет неверный формат в [TelegramBot]. Админ-команды в ЛС будут недоступны.")
        OWNER_ID = None # Явно устанавливаем в None, если не удалось загрузить

    WAIT_MINUTES = config.getint('TelegramBot', 'WAIT_MINUTES', fallback=WAIT_MINUTES)
    WELCOME_MESSAGE_FILE_PATH_STR = config.get('TelegramBot', 'WELCOME_MESSAGE_FILE', fallback=WELCOME_MESSAGE_FILE_PATH_STR)
    LOG_LEVEL_STR = config.get('TelegramBot', 'LOG_LEVEL', fallback=LOG_LEVEL_STR).upper()
    RULES_URL = config.get('TelegramBot', 'RULES_URL', fallback="").strip()
    DELETE_WELCOME_AFTER_MINUTES = config.getint('TelegramBot', 'DELETE_WELCOME_AFTER_MINUTES', fallback=DELETE_WELCOME_AFTER_MINUTES)
    DELETE_INFO_MSG_AFTER_SECONDS = config.getint('TelegramBot', 'DELETE_INFO_MSG_AFTER_SECONDS', fallback=DELETE_INFO_MSG_AFTER_SECONDS)
    
    numeric_log_level = getattr(logging, LOG_LEVEL_STR, logging.INFO)
    logging.getLogger().setLevel(numeric_log_level) 
    logger.setLevel(numeric_log_level)
    logger.info(f"Уровень логирования установлен на: {LOG_LEVEL_STR}")
    logger.info(f"Удаление приветствий через: {DELETE_WELCOME_AFTER_MINUTES} мин.")
    logger.info(f"Удаление инфо-сообщений через: {DELETE_INFO_MSG_AFTER_SECONDS} сек.")
    if OWNER_ID:
        logger.info(f"ID Владельца бота установлен: {OWNER_ID}")
    else:
        logger.warning("ID Владельца бота (OWNER_ID) не установлен. Админ-команды в ЛС будут недоступны.")

    welcome_file_actual_path = Path(WELCOME_MESSAGE_FILE_PATH_STR)
    if not welcome_file_actual_path.is_file():
        base_dir = Path(__file__).parent
        welcome_file_actual_path = base_dir / WELCOME_MESSAGE_FILE_PATH_STR

    try:
        with welcome_file_actual_path.open('r', encoding='utf-8') as f:
            loaded_template = f.read()
            if loaded_template.strip():
                WELCOME_MESSAGE_TEMPLATE = loaded_template
                logger.info(f"Шаблон приветствия успешно загружен из '{welcome_file_actual_path}'.")
            else:
                logger.warning(f"Файл приветственного сообщения '{welcome_file_actual_path}' пуст. Используется предыдущий или стандартный шаблон.")
    except FileNotFoundError:
        logger.error(f"Файл приветственного сообщения '{welcome_file_actual_path}', указанный в config.ini, не найден. Используется предыдущий или стандартный шаблон.")
    except Exception as e:
        logger.error(f"Ошибка при чтении файла приветственного сообщения '{welcome_file_actual_path}': {e}. Используется предыдущий или стандартный шаблон.")
    
    return config_ok and bool(BOT_TOKEN)

def save_wait_minutes_to_config(new_wait_minutes: int):
    global WAIT_MINUTES
    config = configparser.ConfigParser()
    try:
        if CONFIG_FILE_PATH.exists():
            with CONFIG_FILE_PATH.open('r', encoding='utf-8') as f:
                config.read_file(f)
        
        if not config.has_section('TelegramBot'):
            config.add_section('TelegramBot')
        
        config.set('TelegramBot', 'WAIT_MINUTES', str(new_wait_minutes))
        
        with CONFIG_FILE_PATH.open('w', encoding='utf-8') as f:
            config.write(f)
        WAIT_MINUTES = new_wait_minutes 
        logger.info(f"Новое время ожидания {new_wait_minutes} минут сохранено в '{CONFIG_FILE_PATH}'.")
        return True
    except Exception as e:
        logger.error(f"Не удалось сохранить WAIT_MINUTES в '{CONFIG_FILE_PATH}': {e}")
        return False

# --- Декоратор для проверки прав администратора ---
def admin_required(func): # <--- ВОССТАНОВЛЕННЫЙ/ПРОВЕРЕННЫЙ ДЕКОРАТОР
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        is_authorized = False

        if update.effective_chat.type == "private":
            if OWNER_ID and user_id == OWNER_ID:
                is_authorized = True
            else:
                logger.warning(f"Пользователь {user_id} попытался выполнить админ-команду в ЛС, не являясь владельцем (OWNER_ID: {OWNER_ID}).")
        else: 
            try:
                chat_member = await context.bot.get_chat_member(chat_id, user_id)
                if chat_member.status in ['administrator', 'creator']:
                    is_authorized = True
                else:
                    logger.warning(f"Пользователь {user_id} попытался выполнить админ-команду в чате {chat_id} без прав администратора группы.")
            except Exception as e:
                logger.error(f"Ошибка при проверке прав администратора для {user_id} в чате {chat_id}: {e}")
                sent_message = await update.message.reply_text("Не удалось проверить ваши права. Попробуйте позже.")
                if DELETE_INFO_MSG_AFTER_SECONDS > 0:
                    asyncio.create_task(delete_message_after_delay(context, chat_id, sent_message.message_id, DELETE_INFO_MSG_AFTER_SECONDS))
                return None 

        if is_authorized:
            return await func(update, context, *args, **kwargs)
        else:
            sent_message = await update.message.reply_text("Эта команда доступна только администраторам группы (или владельцу бота в ЛС).")
            if DELETE_INFO_MSG_AFTER_SECONDS > 0:
                asyncio.create_task(delete_message_after_delay(context, chat_id, sent_message.message_id, DELETE_INFO_MSG_AFTER_SECONDS))
            return None
    return wrapper

# --- Команды бота ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Команда /start получена от {update.effective_user.id} в чате {update.effective_chat.id}")
    sent_message = await update.message.reply_text(
        f'Бот запущен! Приветствую новых участников через {WAIT_MINUTES} минут, если они все еще в группе и могут писать.'
    )
    if DELETE_INFO_MSG_AFTER_SECONDS > 0:
       asyncio.create_task(delete_message_after_delay(context, update.effective_chat.id, sent_message.message_id, DELETE_INFO_MSG_AFTER_SECONDS))

@admin_required # <--- УБЕДИТЕСЬ, ЧТО ДЕКОРАТОР ЗДЕСЬ
async def set_delay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.effective_chat.id
    reply_message_text = ""

    if not args or not args[0].isdigit():
        reply_message_text = "Использование: /setdelay <минуты>\nНапример: /setdelay 15"
    else:
        new_delay = int(args[0])
        if new_delay < 0: 
            reply_message_text = "Время ожидания не может быть отрицательным."
        else:
            if save_wait_minutes_to_config(new_delay):
                reply_message_text = f"Время ожидания успешно изменено на {new_delay} минут. Настройки сохранены."
                logger.info(f"Администратор {update.effective_user.id} изменил время ожидания на {new_delay} минут в чате {chat_id}.")
            else:
                reply_message_text = "Не удалось сохранить новое время ожидания. Проверьте логи."
    
    sent_message = await update.message.reply_text(reply_message_text)
    if DELETE_INFO_MSG_AFTER_SECONDS > 0:
        asyncio.create_task(delete_message_after_delay(context, chat_id, sent_message.message_id, DELETE_INFO_MSG_AFTER_SECONDS))

@admin_required # <--- УБЕДИТЕСЬ, ЧТО ДЕКОРАТОР ЗДЕСЬ
async def reload_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Администратор {update.effective_user.id} инициировал перезагрузку конфигурации в чате {update.effective_chat.id}.")
    reply_message_text = ""
    if load_config_and_template(): 
        rules_url_status = f"URL правил: {RULES_URL}" if RULES_URL else "URL правил: не задан"
        delete_welcome_status = f"{DELETE_WELCOME_AFTER_MINUTES} мин." if DELETE_WELCOME_AFTER_MINUTES > 0 else "не удалять"
        delete_info_status = f"{DELETE_INFO_MSG_AFTER_SECONDS} сек." if DELETE_INFO_MSG_AFTER_SECONDS > 0 else "не удалять"
        owner_status = f"ID Владельца: {OWNER_ID}" if OWNER_ID else "ID Владельца: не установлен"


        reply_message_text = (f"Конфигурация и шаблон приветствия успешно перезагружены.\n"
                              f"Время ожидания: {WAIT_MINUTES} минут.\n"
                              f"Файл шаблона: {WELCOME_MESSAGE_FILE_PATH_STR}\n"
                              f"{rules_url_status}\n"
                              f"Удаление приветствий: {delete_welcome_status}\n"
                              f"Удаление инфо-сообщений: {delete_info_status}\n"
                              f"{owner_status}\n" # Добавлено отображение OWNER_ID
                              f"Уровень логов: {LOG_LEVEL_STR}")
    else:
        reply_message_text = "Произошла ошибка при перезагрузке конфигурации (возможно, отсутствует токен/owner_id или файл config.ini). Проверьте логи. Бот может не запуститься или работать некорректно."
    
    sent_message = await update.message.reply_text(reply_message_text)
    if DELETE_INFO_MSG_AFTER_SECONDS > 0: 
        asyncio.create_task(delete_message_after_delay(context, update.effective_chat.id, sent_message.message_id, DELETE_INFO_MSG_AFTER_SECONDS))


@admin_required # <--- УБЕДИТЕСЬ, ЧТО ДЕКОРАТОР ЗДЕСЬ
async def test_welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Берём либо кастомный шаблон чата, либо глобальный
    chat_settings = get_chat_settings(chat_id)
    active_template = chat_settings.get("welcome_template") or WELCOME_MESSAGE_TEMPLATE

    if not active_template:
        sent_err_msg = await update.message.reply_text("Шаблон приветственного сообщения не загружен или пуст. Не могу отправить тест.")
        if DELETE_INFO_MSG_AFTER_SECONDS > 0:
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent_err_msg.message_id, DELETE_INFO_MSG_AFTER_SECONDS))
        logger.warning(f"Администратор {user.id} запросил тест приветствия в чате {chat_id}, но шаблон не загружен.")
        return
    
    user_mention = mention_html(user) # Используем html.escape внутри mention_html
    rules_link_html_content = ""
    if RULES_URL:
        link_text = "Пожалуйста, ознакомься с правилами чата."
        safe_link_text = html.escape(link_text) # Используем html.escape
        rules_link_html_content = f'<a href="{html.escape(RULES_URL)}">{safe_link_text}</a>' # Экранируем и URL на всякий случай
        rules_link_html_content = f"\n{rules_link_html_content}\n"

    monthly_joins = get_monthly_join_count(chat_id)

    try:
        welcome_message_final = active_template.format(
            mention=user_mention,
            user_id=user.id,
            user_firstname=html.escape(user.first_name or ""), # Экранируем все пользовательские данные
            user_lastname=html.escape(user.last_name or ""),
            user_fullname=html.escape(user.full_name or ""),
            rules_link_html=rules_link_html_content,
            monthly_join_count=monthly_joins
        )
        sent_test_header = await context.bot.send_message(chat_id, "-- Тестовое приветственное сообщение --", parse_mode=ParseMode.HTML)
        sent_welcome_msg = await context.bot.send_message(
            chat_id,
            welcome_message_final.strip(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        logger.info(f"Администратор {user.id} успешно протестировал приветственное сообщение в чате {chat_id}.")
        
        if DELETE_INFO_MSG_AFTER_SECONDS > 0:
             asyncio.create_task(delete_message_after_delay(context, chat_id, sent_test_header.message_id, DELETE_INFO_MSG_AFTER_SECONDS * 2)) 

        if DELETE_WELCOME_AFTER_MINUTES > 0:
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent_welcome_msg.message_id, DELETE_WELCOME_AFTER_MINUTES * 60))

    except KeyError as e:
        sent_err_msg = await update.message.reply_text(f"Ошибка форматирования тестового приветствия: не найден ключ {e}. Проверьте шаблон '{WELCOME_MESSAGE_FILE_PATH_STR}' и доступные плейсхолдеры.")
        if DELETE_INFO_MSG_AFTER_SECONDS > 0:
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent_err_msg.message_id, DELETE_INFO_MSG_AFTER_SECONDS))
        logger.error(f"Ошибка форматирования тестового приветствия для {user.id} в чате {chat_id}: не найден ключ {e}.")
    except Exception as e:
        sent_err_msg = await update.message.reply_text(f"Произошла ошибка при отправке тестового приветствия: {e}")
        if DELETE_INFO_MSG_AFTER_SECONDS > 0:
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent_err_msg.message_id, DELETE_INFO_MSG_AFTER_SECONDS))
        logger.error(f"Ошибка при отправке тестового приветствия для {user.id} в чате {chat_id}: {e}")

# --- НОВЫЕ КОМАНДЫ ДЛЯ РАБОТЫ С ШАБЛОНАМИ ---
@admin_required
async def set_welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает HTML-шаблон приветствия для текущего чата.
    Варианты использования:
      1. Ответить на сообщение, содержащее HTML-текст, командой /setwelcome
      2. Отправить /setwelcome <HTML-код> одной строкой
    """
    chat_id = update.effective_chat.id
    template_text = None

    # Приоритет – ответ на сообщение
    if update.message.reply_to_message:
        # Сохраняем исходный HTML-текст, если он есть
        template_text = update.message.reply_to_message.html_text or update.message.reply_to_message.text
    # Если нет – берём аргументы команды
    if not template_text and context.args:
        template_text = ' '.join(context.args)

    if not template_text:
        sent = await update.message.reply_text(
            "Использование: \n1. Ответьте командой /setwelcome на сообщение с HTML-шаблоном.\n"
            "2. Или /setwelcome <HTML-шаблон одной строкой>"
        )
        if DELETE_INFO_MSG_AFTER_SECONDS > 0:
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent.message_id, DELETE_INFO_MSG_AFTER_SECONDS))
        return

    set_chat_welcome_template(chat_id, template_text)
    sent = await update.message.reply_text("Шаблон приветствия сохранён для этого чата.")
    if DELETE_INFO_MSG_AFTER_SECONDS > 0:
        asyncio.create_task(delete_message_after_delay(context, chat_id, sent.message_id, DELETE_INFO_MSG_AFTER_SECONDS))


@admin_required
async def show_welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий шаблон приветствия чата."""
    chat_id = update.effective_chat.id
    settings = get_chat_settings(chat_id)
    template = settings.get("welcome_template")
    if template:
        await update.message.reply_text(template, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        sent = await update.message.reply_text("Для этого чата используется шаблон по умолчанию.")
        if DELETE_INFO_MSG_AFTER_SECONDS > 0:
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent.message_id, DELETE_INFO_MSG_AFTER_SECONDS))


@admin_required
async def reset_welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает кастомный шаблон, возвращаясь к глобальному."""
    chat_id = update.effective_chat.id
    set_chat_welcome_template(chat_id, None)
    sent = await update.message.reply_text("Шаблон приветствия для этого чата сброшен. Используется глобальный.")
    if DELETE_INFO_MSG_AFTER_SECONDS > 0:
        asyncio.create_task(delete_message_after_delay(context, chat_id, sent.message_id, DELETE_INFO_MSG_AFTER_SECONDS))

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not update.message or not update.message.new_chat_members:
        logger.warning("new_member_handler вызван без new_chat_members.")
        return

    member_details_log = []
    human_members_count = 0 
    for member in update.message.new_chat_members:
        if member.is_bot:
            logger.info(f"Новый участник ID:{member.id} (Username:{member.username or 'N/A'}, Name: '{member.full_name or 'N/A'}') является БОТОМ. Приветствие не планируется. Запись в БД с флагом is_bot=1.")
            add_joined_user_to_db(member.id, chat_id, member.username or "", member.full_name or "", is_bot_flag=True)
            continue 
        
        human_members_count += 1
        member_details_log.append(f"ID:{member.id}, Username:{member.username or 'N/A'}, FullName:'{html.escape(member.full_name or 'N/A')}'") # Экранируем для логов
        
        add_joined_user_to_db(member.id, chat_id, member.username or "", member.full_name or "", is_bot_flag=False)

        logger.info(f"Планируется проверка и приветствие для пользователя ID:{member.id} ('{html.escape(member.full_name or 'N/A')}') через {WAIT_MINUTES} минут в чате {chat_id}.")
        asyncio.create_task(
            check_and_welcome_after_delay(
                chat_id, member.id, member.full_name or "Новый участник", context
            )
        )
    
    if human_members_count > 0: 
        logger.info(f"Обнаружены новые участники (люди) в чате {chat_id}: {'; '.join(member_details_log)}")

def mention_html(user_data):
    if user_data.username:
        # Имена пользователей Telegram (@username) безопасны и не требуют экранирования для @-упоминания
        return f"@{user_data.username}"
    else:
        # Экранируем имена, которые будут вставлены в HTML тег <a>
        display_name = html.escape(user_data.first_name or user_data.full_name or f"Участник ID:{user_data.id}")
        return f'<a href="tg://user?id={user_data.id}">{display_name}</a>'

async def check_and_welcome_after_delay(chat_id, user_id, user_full_name_on_join, context: ContextTypes.DEFAULT_TYPE):
    wait_time_seconds = WAIT_MINUTES * 60
    logger.info(f"Пользователь ID:{user_id} ('{html.escape(user_full_name_on_join)}') в чате {chat_id}: ожидание {WAIT_MINUTES} минут перед проверкой.")
    await asyncio.sleep(wait_time_seconds)
    logger.info(f"Пользователь ID:{user_id} ('{html.escape(user_full_name_on_join)}') в чате {chat_id}: время ожидания истекло, начинаю проверку.")

    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        actual_full_name = chat_member.user.full_name or user_full_name_on_join

        if chat_member.status in ['left', 'kicked']:
            logger.info(f"Пользователь ID:{user_id} ('{html.escape(actual_full_name)}') покинул чат {chat_id}. Приветствие не будет отправлено.")
            return

        can_post_messages = True
        if chat_member.status == 'restricted':
            if not chat_member.can_send_messages:
                 can_post_messages = False
        
        if not can_post_messages:
            logger.info(f"Пользователь ID:{user_id} ('{html.escape(actual_full_name)}') в чате {chat_id} ограничен в правах на отправку сообщений. Приветствие не будет отправлено.")
            return

        # Берём либо кастомный шаблон чата, либо глобальный
        chat_settings = get_chat_settings(chat_id)
        active_template = chat_settings.get("welcome_template") or WELCOME_MESSAGE_TEMPLATE

        if not active_template:
            logger.warning(f"Шаблон приветственного сообщения не загружен или пуст. Приветствие для ID:{user_id} не будет отправлено.")
            return

        user_mention = mention_html(chat_member.user)
        rules_link_html_content = ""
        if RULES_URL:
            link_text = "Пожалуйста, ознакомься с правилами чата."
            safe_link_text = html.escape(link_text) # Используем html.escape
            rules_link_html_content = f'<a href="{html.escape(RULES_URL)}">{safe_link_text}</a>' # Экранируем и URL
            rules_link_html_content = f"\n{rules_link_html_content}\n"
        
        monthly_joins = get_monthly_join_count(chat_id)

        welcome_message_final = active_template.format(
            mention=user_mention, 
            user_id=user_id, 
            user_firstname=html.escape(chat_member.user.first_name or ""), # Экранируем все пользовательские данные
            user_lastname=html.escape(chat_member.user.last_name or ""), 
            user_fullname=html.escape(chat_member.user.full_name or ""),
            rules_link_html=rules_link_html_content,
            monthly_join_count=monthly_joins
        )
        
        sent_message = await context.bot.send_message( 
            chat_id,
            welcome_message_final.strip(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        logger.info(f"Приветственное сообщение {sent_message.message_id} отправлено пользователю ID:{user_id} ('{html.escape(actual_full_name)}') в чате {chat_id}.")

        if DELETE_WELCOME_AFTER_MINUTES > 0:
            delay_seconds = DELETE_WELCOME_AFTER_MINUTES * 60
            asyncio.create_task(delete_message_after_delay(context, chat_id, sent_message.message_id, delay_seconds))

    except KeyError as e:
        logger.error(f"Ошибка форматирования приветственного сообщения для ID:{user_id}: не найден ключ {e}.")
    except Exception as e:
        logger.error(f"Ошибка при проверке или отправке приветствия для пользователя ID:{user_id} ('{html.escape(user_full_name_on_join)}'): {e}")

async def run_bot():
    init_db() 
    
    if not load_config_and_template(): 
        logger.critical("Критическая ошибка: Не удалось загрузить конфигурацию, отсутствует TOKEN или OWNER_ID. Проверьте config.ini. Бот не может быть запущен.")
        return 

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setdelay", set_delay_command))
    application.add_handler(CommandHandler("reloadconfig", reload_config_command))
    application.add_handler(CommandHandler("testwelcome", test_welcome_command))
    application.add_handler(CommandHandler("setwelcome", set_welcome_command))
    application.add_handler(CommandHandler("showwelcome", show_welcome_command))
    application.add_handler(CommandHandler("resetwelcome", reset_welcome_command))

    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler)
    )

    logger.info(f"Запуск бота... Уровень логов: {LOG_LEVEL_STR}")
    await application.run_polling()

if __name__ == '__main__':
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот останавливается...")
    finally:
        logger.info("Бот остановлен.")