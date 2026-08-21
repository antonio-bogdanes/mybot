import os
import sys
import logging
import json
from flask import Flask, request
from telegram import Bot, Update
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# ===== ЛОГИ =====
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("🚀 Бот запускается...")

# ===== ТОКЕН И КАТЕГОРИИ =====
TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    logger.error("❌ TOKEN не задан")
    sys.exit(1)
logger.info("✅ Токен получен")

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')
CATEGORIES = ['Закупка товара', 'Аренда', 'Зарплата', 'Реклама', 'Коммунальные', 'Транспорт', 'Налоги', 'Прочее']

# ===== GOOGLE SHEETS (если есть файл) =====
def add_expense(category, amount):
    try:
        if not os.path.exists('credentials.json'):
            logger.warning("Файл credentials.json не найден, запись не выполняется")
            return False
        creds = Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        day = datetime.now().day
        col = day + 1
        cats = sheet.col_values(1)
        if category not in cats:
            logger.error(f"Категория {category} не найдена")
            return False
        row = cats.index(category) + 1
        cell = sheet.cell(row, col)
        current = float(cell.value) if cell.value else 0
        sheet.update_cell(row, col, current + amount)
        logger.info(f"Записано {amount} в {category}")
        return True
    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        return False

# ===== БОТ =====
bot = Bot(token=TOKEN)

# ===== FLASK =====
flask_app = Flask(__name__)

# Главная страница для проверки
@flask_app.route('/')
def index():
    return "Bot is running!", 200

# Принимаем все POST-запросы (и от Telegram, и от Render health checks)
@flask_app.route('/', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            logger.warning("Пустой запрос")
            return "ok", 200
        update = Update.de_json(data, bot)
        if update.message and update.message.text:
            chat_id = update.message.chat.id
            text = update.message.text
            logger.info(f"Сообщение от {chat_id}: {text}")

            if text.startswith('/start'):
                bot.send_message(chat_id=chat_id, text="👕 Бот работает!\nОтправь: сумма категория\nПример: 15000 Закупка товара")
                return "ok", 200

            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                bot.send_message(chat_id=chat_id, text="Пиши: сумма категория")
                return "ok", 200
            amount_str, category = parts
            amount = float(amount_str.replace(',', '.'))
            if category not in CATEGORIES:
                bot.send_message(chat_id=chat_id, text=f"Категории: {', '.join(CATEGORIES)}")
                return "ok", 200
            if add_expense(category, amount):
                bot.send_message(chat_id=chat_id, text=f"✅ Записано {amount} в {category}")
            else:
                bot.send_message(chat_id=chat_id, text="❌ Ошибка записи (возможно, нет credentials.json или доступа)")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "error", 500

# ===== ЗАПУСК =====
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Запуск на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)
