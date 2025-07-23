# HelloChat Telegram Bot

Этот Telegram-бот предназначен для автоматического приветствия новых участников в групповых чатах. Он также позволяет администраторам настраивать индивидуальные приветственные сообщения для каждого чата.

## Функциональность

*   **Приветствие новых участников**: Автоматически отправляет приветственное сообщение новым пользователям, присоединившимся к чату.
*   **Настраиваемые приветствия для каждого чата**: Администраторы могут установить свой собственный HTML-шаблон приветствия для каждой группы.
*   **Задержка перед приветствием**: Бот ожидает определенное время, чтобы убедиться, что новый участник не покинул чат и может отправлять сообщения.
*   **Автоматическое удаление сообщений**: Информационные сообщения бота и приветствия могут быть автоматически удалены через заданный промежуток времени.
*   **Административные команды**:
    *   `/start`: Запускает бота и показывает текущие настройки.
    *   `/setdelay <минуты>`: Устанавливает задержку перед приветствием (глобально).
    *   `/reloadconfig`: Перезагружает конфигурацию бота из `config.ini`.
    *   `/testwelcome`: Отправляет тестовое приветственное сообщение в чат.
    *   `/setwelcome`: Устанавливает HTML-шаблон приветствия для текущего чата. Используйте в ответ на сообщение с HTML-кодом или ` /setwelcome <HTML-код>`.
    *   `/showwelcome`: Показывает текущий шаблон приветствия для чата (кастомный или глобальный).
    *   `/resetwelcome`: Сбрасывает кастомный шаблон приветствия для чата, возвращаясь к глобальному.

## Установка и запуск

### 1. Клонирование репозитория

```bash
git clone https://github.com/CyberScoper/hellochat.git
cd hellochat
```

### 2. Настройка виртуального окружения

Настоятельно рекомендуется использовать виртуальное окружение для управления зависимостями.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Файл конфигурации (`config.ini`)

Создайте файл `config.ini` в корневой директории бота (`hellochat/`) со следующим содержимым:

```ini
[TelegramBot]
TOKEN = ВАШ_ТОКЕН_БОТА
OWNER_ID = ВАШ_ID_ПОЛЬЗОВАТЕЛЯ # Опционально: ваш Telegram ID для админ-команд в ЛС
WAIT_MINUTES = 10
WELCOME_MESSAGE_FILE = welcome_template.html
RULES_URL = 
LOG_LEVEL = INFO
DELETE_WELCOME_AFTER_MINUTES = 10
DELETE_INFO_MSG_AFTER_SECONDS = 5

[Paths]
LOG_FILE = bot.log
```

**ВАЖНО: `config.ini` и `user_joins.db` ИГНОРИРУЮТСЯ Git-ом и НЕ ДОЛЖНЫ содержать ваш реальный токен или личные данные при отправке в публичные репозитории! После создания `config.ini`, убедитесь, что ваш токен бота отозван, если он ранее был в истории Git, и создайте новый токен в BotFather.**

### 4. Шаблон приветственного сообщения (`welcome_template.html`)

Создайте или отредактируйте файл `welcome_template.html` в корневой директории бота. Это будет глобальный шаблон приветствия по умолчанию.

Пример содержимого:
```html
<p>Добро пожаловать, {mention}! Рады видеть вас в нашем чате.</p>
<p>{rules_link_html}</p>
<p>Вы - {monthly_join_count}-й новый участник в этом месяце!</p>
```
Доступные плейсхолдеры:
*   `{mention}`: Упоминание пользователя с ссылкой на его профиль.
*   `{user_id}`: ID пользователя.
*   `{user_firstname}`: Имя пользователя.
*   `{user_lastname}`: Фамилия пользователя.
*   `{user_fullname}`: Полное имя пользователя.
*   `{rules_link_html}`: HTML-ссылка на правила чата (если `RULES_URL` указан в `config.ini`).
*   `{monthly_join_count}`: Количество уникальных пользователей, присоединившихся к чату в текущем месяце.

### 5. Настройка Systemd Service

Для постоянного запуска бота в фоновом режиме рекомендуется использовать `systemd`. Создайте файл `hellochat-bot.service` в директории `/etc/systemd/system/` (вам потребуются права root):

```service
[Unit]
Description=HelloChat Telegram Bot Service
After=network.target

[Service]
Type=simple
# Пользователь, от которого будет работать бот.
# Если это root, то можно закомментировать, systemd сам запустит от root.
# User=root

# Абсолютный путь к директории, где лежит main.py, config.ini и т.д.
WorkingDirectory=/root/Telegrambots/hellochat/

# Команда для запуска. Используем абсолютные пути.
# Флаг -u для python3 отключает буферизацию вывода, что полезно для логов.
ExecStart=/root/Telegrambots/hellochat/venv/bin/python3 -u /root/Telegrambots/hellochat/main.py

# Перезапускать службу при любом сбое
Restart=always
RestartSec=10 # Пауза перед перезапуском 10 секунд

# Вместо файлов логов, будем смотреть логи через journalctl для начала
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Сохраните файл, затем выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl enable hellochat-bot.service
sudo systemctl start hellochat-bot.service
```

## Управление ботом

*   **Запустить:** `sudo systemctl start hellochat-bot.service`
*   **Остановить:** `sudo systemctl stop hellochat-bot.service`
*   **Перезапустить (после изменений в коде/конфигурации):** `sudo systemctl restart hellochat-bot.service`
*   **Просмотреть логи в реальном времени:** `sudo journalctl -u hellochat-bot.service -f`
*   **Проверить статус:** `sudo systemctl status hellochat-bot.service`
