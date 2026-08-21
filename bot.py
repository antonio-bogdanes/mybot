import os
import sys
import json
import logging
import requests
from flask import Flask, request
from telegram import Update
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("🚀 Бот запускается...")

TOKEN = os.environ.get('TOKEN')
if not TOKEN:
    logger.error("❌ TOKEN не задан")
    sys.exit(1)

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')
CATEGORIES = ['Закупка товара', 'Аренда', 'Зарплата', 'Реклама', 'Коммунальные', 'Транспорт', 'Налоги', 'Прочее']

def get_creds():
    """Возвращает учётные данные из переменной окружения или файла."""
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if creds_json:
        try:
            creds_dict = json.loads(creds_json)
            logger.info("✅ Учётные данные загружены из переменной окружения")
            return Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        except Exception as e:
            logger.error(f"Ошибка парсинга GOOGLE_CREDENTIALS: {e}")
    # fallback на файл
    if os.path.exists('credentials.json'):
        try:
            with open('credentials.json', 'r') as f:
                creds_dict = json.load(f)
            logger.info("✅ Учётные данные загружены из файла credentials.json")
            return Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        except Exception as e:
            logger.error(f"Ошибка чтения файла credentials.json: {e}")
    logger.error("❌ Не найдены учётные данные для Google Sheets")
    return None

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=5)
        if resp.status_code != 200:
            logger.error(f"Ошибка отправки: {resp.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def add_expense(category, amount):
    creds = get_creds()
    if not creds:
        return False, "Нет учётных данных для Google Sheets"
    try:
        # Принудительное обновление токена (решает проблему времени)
        creds.refresh(Request())
        client = gspread.authorize(creds)
        logger.info("✅ Подключение к Google Sheets успешно")
        
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        logger.info(f"✅ Таблица открыта: {sheet.title}")
        
        day = datetime.now().day
        col = day + 1  # колонка B для 1-го дня
        cats = sheet.col_values(1)
        logger.info(f"📋 Категории в таблице: {cats}")
        
        if category not in cats:
            return False, f"Категория '{category}' не найдена. Доступны: {', '.join(cats[1:])}"
        row = cats.index(category) + 1
        cell = sheet.cell(row, col)
        current = float(cell.value) if cell.value and str(cell.value).replace('.', '').isdigit() else 0
        new_value = current + amount
        sheet.update_cell(row, col, new_value)
        logger.info(f"✅ Ячейка {chr(64+col)}{row} обновлена на {new_value}")
        return True, f"Записано {amount} в {category} (ячейка {chr(64+col)}{row})"
    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        return False, f"Ошибка: {str(e)}"

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!", 200

@flask_app.route('/', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return "ok", 200
        update = Update.de_json(data, None)
        if update.message and update.message.text:
            chat_id = update.message.chat.id
            text = update.message.text
            logger.info(f"Сообщение от {chat_id}: {text}")
            if text.startswith('/start'):
                send_message(chat_id, "👕 Бот учёта расходов.\nОтправь: сумма категория\nПример: 15000 Закупка товара")
                return "ok", 200
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(chat_id, "Пиши: сумма категория")
                return "ok", 200
            amount_str, category = parts
            try:
                amount = float(amount_str.replace(',', '.'))
            except:
                send_message(chat_id, "Сумма должна быть числом")
                return "ok", 200
            if category not in CATEGORIES:
                send_message(chat_id, f"Доступные категории: {', '.join(CATEGORIES)}")
                return "ok", 200
            success, msg = add_expense(category, amount)
            send_message(chat_id, f"{'✅' if success else '❌'} {msg}")
        return "ok", 200
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return "error", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Запуск на порту {port}")
    flask_app.run(host='0.0.0.0', port=port)
