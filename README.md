# GlowMe Stock Tracker Bot

Простой Telegram-бот для отслеживания наличия товаров на сайте [glowme.ru](https://glowme.ru/).

## Возможности
- Поиск товаров по названию или прямой ссылке с `glowme.ru`.
- Просмотр списков товаров **«❌ Нет в наличии»** и **«✅ В наличии»**.
- Подписка на уведомления о появлении конкретного товара в наличии.
- Режим **«🌐 Все поступления»** — уведомления, как только любой отсутствующий товар возвращается в наличие или появляется новинка.
- Хранение подписок в локальной базе SQLite (`bot_data.db`).

## Деплой на сервер через Docker (рекомендуется)

1. Скопируйте проект на сервер (`git clone ...` или `scp -r glow_me_bot user@server:~/glow_me_bot`) и перейдите в папку:
   ```bash
   cd glow_me_bot
   ```
2. Создайте файл `.env` и укажите токен бота от `@BotFather`:
   ```bash
   cp .env.example .env
   nano .env
   ```
   ```env
   BOT_TOKEN=123456:ABC-DEF...
   CHECK_INTERVAL=60
   ```
3. Соберите и запустите контейнер в фоне:
   ```bash
   docker compose up -d --build
   ```
4. Полезные команды:
   ```bash
   # Посмотреть логи
   docker compose logs -f

   # Перезапустить после обновления кода
   docker compose up -d --build

   # Остановить бота
   docker compose down
   ```
   База подписок сохраняется на сервере в папке `./data/bot_data.db` и не пропадает при перезапуске контейнера.

## Локальный запуск (без Docker)

1. Создайте бота в Telegram через `@BotFather` и получите токен.
2. Установите зависимости:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Создайте `.env` из примера и укажите токен:
   ```bash
   cp .env.example .env
   ```
4. Запустите бота:
   ```bash
   python bot.py
   ```
