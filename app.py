from flask import Flask, render_template_string, request, jsonify
import subprocess
import os
import json
import shutil
from datetime import datetime
import threading
import asyncio
import requests
import zipfile
import tempfile
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging
import socket

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)
app.config['SECRET_KEY'] = 'deploy-manager-pro-secret-key'

# Конфигурация (ИСПРАВЛЕННАЯ для BotHost)
PROJECTS_DIR = "/app/projects"
CONFIG_FILE = "/app/config/config.json"
LOG_FILE = "/app/config/deploy.log"
BOT_TOKEN = os.getenv('BOT_TOKEN', '7966969765:AAEZLNOFRmv2hPJ8fQaE3u2KSPsoxreDn-E')
ADMIN_IDS = [1769269442]

# ИСПРАВЛЕНИЕ: Используем точно тот порт, который требует BotHost
FLASK_PORT = int(os.getenv('PORT', 3000))  # BotHost указывает PORT=3000
FLASK_HOST = '0.0.0.0'

logger.info(f"🔧 ИСПРАВЛЕНИЕ: Будем использовать порт {FLASK_PORT} (из BotHost PORT)")

# Создаём директории
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs("/app/config", exist_ok=True)

# Telegram Bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные
user_states = {}
flask_running = False

# === MIDDLEWARE ===
@app.before_request
def log_request_info():
    logger.info(f"🌐 HTTP запрос: {request.method} {request.path} от {request.remote_addr}")

@app.after_request
def after_request(response):
    logger.info(f"📤 HTTP ответ: {response.status_code} для {request.path}")
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['X-Powered-By'] = 'Deploy Manager Pro v3.4'
    response.headers['Server'] = f'BotHost-Flask-{FLASK_PORT}'
    return response

# === ФУНКЦИИ (остаются такими же) ===

def download_repo_from_github(repo_url, branch="main", target_dir=None):
    """Скачивание репозитория через GitHub API"""
    try:
        logger.info(f"Начинаю скачивание {repo_url}, ветка {branch}")
        
        if "github.com" not in repo_url:
            raise Exception("Поддерживается только GitHub")
        
        # Парсинг URL
        parts = repo_url.replace("https://github.com/", "").replace(".git", "").split("/")
        if len(parts) < 2:
            raise Exception("Неверный формат URL")
        
        username, repo_name = parts[0], parts[1]
        zip_url = f"https://github.com/{username}/{repo_name}/archive/refs/heads/{branch}.zip"
        
        logger.info(f"Скачиваю: {zip_url}")
        
        response = requests.get(zip_url, timeout=30)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: Не удалось скачать репозиторий")
        
        logger.info(f"Скачано {len(response.content)} байт")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            temp_file.write(response.content)
            temp_zip_path = temp_file.name
        
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            with tempfile.TemporaryDirectory() as temp_extract_dir:
                zip_ref.extractall(temp_extract_dir)
                
                extracted_folders = os.listdir(temp_extract_dir)
                if not extracted_folders:
                    raise Exception("Пустой архив")
                
                source_dir = os.path.join(temp_extract_dir, extracted_folders[0])
                
                if target_dir and not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                
                if target_dir:
                    for item in os.listdir(target_dir):
                        item_path = os.path.join(target_dir, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                        except:
                            pass
                    
                    for item in os.listdir(source_dir):
                        source_item = os.path.join(source_dir, item)
                        target_item = os.path.join(target_dir, item)
                        try:
                            if os.path.isdir(source_item):
                                shutil.copytree(source_item, target_item)
                            else:
                                shutil.copy2(source_item, target_item)
                        except Exception as e:
                            logger.warning(f"Не удалось скопировать {item}: {e}")
        
        os.unlink(temp_zip_path)
        
        logger.info(f"Репозиторий успешно скачан в {target_dir}")
        return True
        
    except Exception as e:
        logger.error(f"ОШИБКА скачивания: {str(e)}")
        raise e

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки конфига: {e}")
    return {"projects": {}}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения конфига: {e}")

def log_action(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"
    logger.info(log_message)
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_message + "\n")
    except Exception as e:
        logger.error(f"Ошибка записи в лог: {e}")

def is_admin(user_id):
    return user_id in ADMIN_IDS

def safe_message_send(message_text, parse_mode="HTML"):
    if len(message_text) > 4000:
        return message_text[:4000] + "..."
    return message_text

# === TELEGRAM BOT HANDLERS ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="📦 Мои проекты", callback_data="list_projects"),
        InlineKeyboardButton(text="🚀 Деплой проекта", callback_data="deploy_start"),
        InlineKeyboardButton(text="🌐 Веб-панель", url="https://server.bothost.ru"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="📋 Логи", callback_data="logs")
    )
    keyboard.adjust(2, 1, 2)
    
    response_text = safe_message_send(
        f"🚀 <b>Deploy Manager Pro v3.4</b>\n\n"
        f"✅ Система работает!\n"
        f"🌐 Flask: порт {FLASK_PORT}\n"
        f"🔧 BotHost совместимая версия\n\n"
        f"Выберите действие:"
    )
    
    await message.answer(response_text, parse_mode="HTML", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data == "list_projects")
async def show_projects(callback: CallbackQuery):
    config = load_config()
    projects = config.get('projects', {})
    
    if not projects:
        await callback.message.edit_text(
            "📦 <b>Проекты</b>\n\n"
            "❌ Пока нет проектов\n\n"
            "Нажмите 'Деплой проекта' для добавления.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Деплой", callback_data="deploy_start"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )
        return
    
    text = "📦 <b>Мои проекты:</b>\n\n"
    keyboard = InlineKeyboardBuilder()
    
    for name, info in list(projects.items())[:5]:
        text += f"▪️ <b>{name}</b>\n"
        text += f"   🔗 {info.get('repo_url', 'N/A')[:50]}...\n"
        text += f"   🌿 {info.get('branch', 'main')}\n\n"
        
        keyboard.add(InlineKeyboardButton(
            text=f"⚙️ {name}", 
            callback_data=f"manage_{name}"
        ))
    
    keyboard.add(
        InlineKeyboardButton(text="🚀 Деплой", callback_data="deploy_start"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    keyboard.adjust(2)
    
    response_text = safe_message_send(text)
    await callback.message.edit_text(response_text, parse_mode="HTML", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("manage_"))
async def manage_project(callback: CallbackQuery):
    project_name = callback.data.split("manage_")[1]
    config = load_config()
    
    if project_name not in config['projects']:
        await callback.answer("❌ Проект не найден")
        return
    
    project = config['projects'][project_name]
    
    text = f"⚙️ <b>Проект: {project_name}</b>\n\n"
    text += f"🔗 <b>Репозиторий:</b>\n{project['repo_url'][:60]}...\n\n"
    text += f"🌿 <b>Ветка:</b> {project['branch']}\n"
    text += f"🕐 <b>Обновлено:</b> {project.get('last_update', 'Никогда')}\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"update_{project_name}"),
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{project_name}"),
        InlineKeyboardButton(text="🔙 К проектам", callback_data="list_projects")
    )
    keyboard.adjust(2, 1)
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("update_"))
async def update_project(callback: CallbackQuery):
    project_name = callback.data.split("update_")[1]
    
    try:
        await callback.answer("🔄 Обновление...")
        await callback.message.edit_text("🔄 <b>Обновление начато...</b>", parse_mode="HTML")
        
        config = load_config()
        project = config['projects'][project_name]
        
        log_action(f"Bot: Обновление {project_name}")
        
        download_repo_from_github(project['repo_url'], project['branch'], project['path'])
        
        req_file = os.path.join(project['path'], 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.run(['pip', 'install', '-r', req_file], capture_output=True)
        
        config['projects'][project_name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        
        await callback.message.edit_text(
            f"✅ <b>Проект {project_name} обновлен!</b>\n\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 К проекту", callback_data=f"manage_{project_name}")
            ]])
        )
        
    except Exception as e:
        logger.error(f"Ошибка обновления: {e}")
        await callback.message.edit_text(
            f"❌ <b>Ошибка:</b>\n\n{str(e)[:200]}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data=f"manage_{project_name}")
            ]])
        )

@dp.callback_query(F.data.startswith("delete_"))
async def confirm_delete(callback: CallbackQuery):
    project_name = callback.data.split("delete_")[1]
    
    await callback.message.edit_text(
        f"⚠️ <b>Удалить {project_name}?</b>\n\n❗️ Действие нельзя отменить!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚠️ Да, удалить", callback_data=f"confirm_delete_{project_name}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"manage_{project_name}")
        ]])
    )

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def delete_project(callback: CallbackQuery):
    project_name = callback.data.split("confirm_delete_")[1]
    
    try:
        config = load_config()
        project_path = config['projects'][project_name]['path']
        
        if os.path.exists(project_path):
            shutil.rmtree(project_path)
        
        del config['projects'][project_name]
        save_config(config)
        
        log_action(f"Bot: Удален проект {project_name}")
        
        await callback.message.edit_text(
            f"✅ <b>Проект {project_name} удален!</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📦 Проекты", callback_data="list_projects")
            ]])
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка:</b> {str(e)[:200]}",
            parse_mode="HTML"
        )

@dp.callback_query(F.data == "deploy_start")
async def deploy_start(callback: CallbackQuery):
    user_states[callback.from_user.id] = {"step": "name"}
    
    await callback.message.edit_text(
        "🚀 <b>Деплой проекта</b>\n\n"
        "Шаг 1/3: Введите название\n\n"
        "Пример: <code>my-bot</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_main")
        ]])
    )

@dp.message(F.text)
async def handle_deploy_steps(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    user_id = message.from_user.id
    if user_id not in user_states:
        return
    
    state = user_states[user_id]
    
    try:
        if state["step"] == "name":
            state["project_name"] = message.text.strip()[:50]
            state["step"] = "url"
            
            await message.answer(
                f"📦 <b>Деплой проекта</b>\n\n"
                f"✅ Название: <code>{state['project_name']}</code>\n\n"
                f"Шаг 2/3: GitHub URL\n\n"
                f"Пример:\n<code>https://github.com/user/repo.git</code>",
                parse_mode="HTML"
            )
            
        elif state["step"] == "url":
            state["repo_url"] = message.text.strip()
            state["step"] = "branch"
            
            await message.answer(
                f"🌿 <b>Деплой проекта</b>\n\n"
                f"✅ Название: <code>{state['project_name']}</code>\n"
                f"✅ URL: GitHub репозиторий\n\n"
                f"Шаг 3/3: Введите ветку",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ main", callback_data="deploy_main_branch")
                ]])
            )
            
        elif state["step"] == "branch":
            state["branch"] = message.text.strip()
            await start_deploy(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка в шагах деплоя: {e}")
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")

@dp.callback_query(F.data == "deploy_main_branch")
async def deploy_main_branch(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_states:
        user_states[user_id]["branch"] = "main"
        await start_deploy(callback.message, user_states[user_id])

async def start_deploy(message, state):
    try:
        await message.answer("🔄 <b>Деплой начат...</b>", parse_mode="HTML")
        
        project_name = state["project_name"]
        repo_url = state["repo_url"]
        branch = state["branch"]
        
        if "github.com" not in repo_url:
            raise Exception("Только GitHub!")
        
        project_path = os.path.join(PROJECTS_DIR, project_name)
        
        if os.path.exists(project_path):
            download_repo_from_github(repo_url, branch, project_path)
            action = "обновлен"
        else:
            os.makedirs(project_path, exist_ok=True)
            download_repo_from_github(repo_url, branch, project_path)
            action = "задеплоен"
        
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.run(['pip', 'install', '-r', req_file], capture_output=True)
        
        config = load_config()
        config['projects'][project_name] = {
            'repo_url': repo_url,
            'branch': branch,
            'path': project_path,
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_config(config)
        
        del user_states[message.from_user.id]
        
        await message.answer(
            f"✅ <b>Готово!</b>\n\n"
            f"📦 {project_name}\n"
            f"🌿 {branch}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📦 Проекты", callback_data="list_projects"),
                InlineKeyboardButton(text="🏠 Главная", callback_data="back_to_main")
            ]])
        )
        
        log_action(f"Bot: Задеплоен {project_name}")
        
    except Exception as e:
        logger.error(f"Ошибка деплоя: {e}")
        if message.from_user.id in user_states:
            del user_states[message.from_user.id]
        
        await message.answer(
            f"❌ <b>Ошибка:</b>\n\n{str(e)[:200]}",
            parse_mode="HTML"
        )

@dp.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    config = load_config()
    projects = config.get('projects', {})
    
    await callback.message.edit_text(
        "📊 <b>Статистика</b>\n\n"
        f"📦 <b>Проектов:</b> {len(projects)}\n"
        f"🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}\n"
        f"📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y')}\n"
        f"🌐 <b>Порт:</b> {FLASK_PORT}\n"
        f"🔧 <b>Версия:</b> v3.4",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="stats"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]])
    )

@dp.callback_query(F.data == "logs")
async def show_logs(callback: CallbackQuery):
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                logs = f.read()
            
            log_lines = logs.split('\n')[-10:]
            recent_logs = '\n'.join(log_lines)[:3000]
        else:
            recent_logs = "Логи пусты"
    except:
        recent_logs = "Ошибка чтения логов"
    
    await callback.message.edit_text(
        f"📋 <b>Логи:</b>\n\n<code>{recent_logs}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="logs"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]])
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    if callback.from_user.id in user_states:
        del user_states[callback.from_user.id]
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="📦 Проекты", callback_data="list_projects"),
        InlineKeyboardButton(text="🚀 Деплой", callback_data="deploy_start"),
        InlineKeyboardButton(text="🌐 Панель", url="https://server.bothost.ru"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats")
    )
    keyboard.adjust(2, 2)
    
    await callback.message.edit_text(
        f"🚀 <b>Deploy Manager Pro v3.4</b>\n\n"
        f"✅ Система работает на порту {FLASK_PORT}\n"
        f"🌐 Веб: server.bothost.ru\n\n"
        f"Выберите действие:",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

# === FLASK ROUTES ===

@app.route('/')
def index():
    logger.info("🏠 Загрузка главной страницы")
    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Deploy Manager Pro v3.4 - BotHost</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{ 
            max-width: 1000px; 
            margin: 0 auto; 
            background: rgba(255,255,255,0.98); 
            padding: 30px; 
            border-radius: 20px; 
            box-shadow: 0 25px 70px rgba(0,0,0,0.3);
            backdrop-filter: blur(15px);
        }}
        h1 {{ 
            color: #333; 
            text-align: center;
            font-size: 3em;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .status {{ 
            background: linear-gradient(135deg, #28a745, #20c997);
            color: white; 
            padding: 25px; 
            border-radius: 15px; 
            margin: 25px 0; 
            text-align: center;
            font-size: 1.4em;
            font-weight: 700;
            box-shadow: 0 10px 25px rgba(40, 167, 69, 0.3);
        }}
        .fix-notice {{
            background: linear-gradient(135deg, #ff6b6b, #ffa500);
            color: white;
            padding: 20px;
            border-radius: 15px;
            margin: 20px 0;
            text-align: center;
            font-weight: 600;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 25px;
            margin: 30px 0;
        }}
        .card {{ 
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 12px 35px rgba(0,0,0,0.12);
            border-left: 6px solid #667eea;
            transition: all 0.3s ease;
        }}
        .card:hover {{
            transform: translateY(-8px);
            box-shadow: 0 20px 50px rgba(0,0,0,0.2);
        }}
        .card h3 {{
            color: #333;
            margin-bottom: 15px;
            font-size: 1.4em;
        }}
        .card ul {{
            list-style: none;
            padding: 0;
        }}
        .card li {{
            color: #666;
            margin: 8px 0;
            padding-left: 20px;
            position: relative;
        }}
        .card li:before {{
            content: "✅";
            position: absolute;
            left: 0;
        }}
        .button-group {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin: 30px 0;
            justify-content: center;
        }}
        .btn {{ 
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white; 
            border: none; 
            padding: 15px 30px; 
            border-radius: 30px; 
            cursor: pointer; 
            font-size: 15px;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: all 0.3s ease;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .btn:hover {{ 
            transform: translateY(-3px);
            box-shadow: 0 12px 30px rgba(0,0,0,0.25);
        }}
        .btn-success {{ background: linear-gradient(135deg, #28a745, #20c997); }}
        .btn-info {{ background: linear-gradient(135deg, #17a2b8, #138496); }}
        .btn-warning {{ background: linear-gradient(135deg, #ffc107, #e0a800); color: #333; }}
        .footer {{
            text-align: center;
            margin-top: 40px;
            padding-top: 30px;
            border-top: 2px solid #eee;
            color: #666;
            font-size: 0.95em;
        }}
        .debug {{
            background: #f8f9fa;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            padding: 20px;
            margin: 20px 0;
            font-family: 'Courier New', monospace;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Deploy Manager Pro</h1>
        
        <div class="status">
            ✅ Система запущена на правильном порту!<br>
            🌐 Flask работает на порту {FLASK_PORT}
        </div>
        
        <div class="fix-notice">
            🔧 <strong>ИСПРАВЛЕНО v3.4:</strong> Теперь используется порт {FLASK_PORT} из BotHost!<br>
            Веб-панель должна быть доступна по адресу: <strong>https://server.bothost.ru</strong>
        </div>
        
        <div class="debug">
            <strong>📋 Диагностика BotHost:</strong><br>
            PORT (env): {os.getenv('PORT')}<br>
            Flask Port: {FLASK_PORT}<br>
            Host: {FLASK_HOST}<br>
            Время: {datetime.now().strftime('%H:%M:%S')}<br>
            PID: {os.getpid()}
        </div>
        
        <div class="grid">
            <div class="card">
                <h3>📱 Telegram Bot</h3>
                <p><strong>@RegisterMarketPlace_bot</strong></p>
                <p>Отправьте <code>/start</code> боту</p>
                <ul>
                    <li>Управление проектами</li>
                    <li>Деплой из GitHub</li>
                    <li>Обновление проектов</li>
                    <li>Мониторинг системы</li>
                </ul>
            </div>
            
            <div class="card">
                <h3>🌐 Web API</h3>
                <ul>
                    <li>GET /health - Статус системы</li>
                    <li>GET /api/projects - Список проектов</li>
                    <li>GET /api/logs - Логи работы</li>
                    <li>POST /webhook - GitHub webhook</li>
                </ul>
            </div>
            
            <div class="card">
                <h3>⚙️ Новое в v3.4</h3>
                <ul>
                    <li>Исправлен порт для BotHost</li>
                    <li>Правильная работа с proxy</li>
                    <li>Улучшенная диагностика</li>
                    <li>Стабильная веб-панель</li>
                </ul>
            </div>
        </div>
        
        <div class="button-group">
            <a href="/health" class="btn btn-success" target="_blank">🏥 Проверить статус</a>
            <a href="/api/projects" class="btn btn-info" target="_blank">📦 Мои проекты</a>
            <a href="/api/logs" class="btn btn-warning" target="_blank">📋 Системные логи</a>
        </div>
        
        <div class="footer">
            <p><strong>Deploy Manager Pro v3.4</strong></p>
            <p>BotHost Compatible • Port Fix • Enhanced Stability</p>
            <p>🌐 Работает на порту {FLASK_PORT} • Powered by Flask + aiogram</p>
        </div>
    </div>
</body>
</html>
    """

@app.route('/health')
def health():
    logger.info("🏥 Проверка здоровья")
    config = load_config()
    return jsonify({
        "status": "ok", 
        "version": "3.4",
        "message": "BotHost port fix applied",
        "flask_running": flask_running,
        "flask_port": FLASK_PORT,
        "flask_host": FLASK_HOST,
        "projects_count": len(config.get('projects', {})),
        "timestamp": datetime.now().isoformat(),
        "bot_active": True,
        "environment": {
            "PORT": os.getenv('PORT'),
            "BOTHOST_USER_ID": os.getenv('BOTHOST_USER_ID'),
            "BOTHOST_USER_PLAN": os.getenv('BOTHOST_USER_PLAN')
        }
    })

@app.route('/api/projects')
def api_projects():
    logger.info("📦 API запрос проектов")
    try:
        config = load_config()
        return jsonify(config.get('projects', {}))
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/logs')
def api_logs():
    logger.info("📋 API запрос логов")
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return content[-5000:], 200, {'Content-Type': 'text/plain; charset=utf-8'}
        return "Логи пусты", 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        return f"Ошибка: {str(e)}", 500, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/api/deploy', methods=['POST'])
def api_deploy():
    logger.info("🚀 API деплой запрос")
    try:
        data = request.json
        repo_url = data.get('repo_url')
        project_name = data.get('project_name')
        branch = data.get('branch', 'main')
        
        if not repo_url or not project_name:
            return jsonify({"error": "Не указаны repo_url и project_name"}), 400
        
        if "github.com" not in repo_url:
            return jsonify({"error": "Поддерживается только GitHub"}), 400
        
        project_path = os.path.join(PROJECTS_DIR, project_name)
        
        if os.path.exists(project_path):
            log_action(f"WEB: Обновление проекта: {project_name}")
            download_repo_from_github(repo_url, branch, project_path)
            action = "обновлен"
        else:
            log_action(f"WEB: Деплой проекта: {project_name}")
            os.makedirs(project_path, exist_ok=True)
            download_repo_from_github(repo_url, branch, project_path)
            action = "задеплоен"
        
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.run(['pip', 'install', '-r', req_file])
        
        config = load_config()
        config['projects'][project_name] = {
            'repo_url': repo_url,
            'branch': branch,
            'path': project_path,
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_config(config)
        
        return jsonify({
            "status": "success",
            "action": action,
            "project": project_name,
            "message": f"Проект {project_name} успешно {action}!"
        })
    
    except Exception as e:
        logger.error(f"ОШИБКА API деплоя: {str(e)}")
        return jsonify({"error": str(e)}), 500

# === ЗАПУСК (ИСПРАВЛЕННЫЙ для BotHost) ===

def run_flask():
    global flask_running
    try:
        logger.info(f"🌐 ИСПРАВЛЕНИЕ: Запуск Flask на порту {FLASK_PORT} (BotHost требует)")
        flask_running = True
        
        # ИСПРАВЛЕНИЕ: Используем точно тот порт, который указан BotHost
        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,  # Используем PORT=3000 из BotHost
            debug=False,
            use_reloader=False,
            threaded=True,
            processes=1
        )
        
    except Exception as e:
        logger.error(f"КРИТИЧЕСКАЯ ошибка Flask: {e}")
        flask_running = False

async def main():
    try:
        log_action(f"🚀 Deploy Manager Pro v3.4 запущен (порт {FLASK_PORT})")
        
        logger.info(f"🔧 BotHost настройки:")
        logger.info(f"   Требуемый порт: {FLASK_PORT}")
        logger.info(f"   Хост: {FLASK_HOST}")
        logger.info(f"   План: {os.getenv('BOTHOST_USER_PLAN')}")
        
        # Запускаем Flask на правильном порту
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        # Ждём запуска Flask
        await asyncio.sleep(4)
        
        # Тестируем подключение
        try:
            response = requests.get(f'http://localhost:{FLASK_PORT}/health', timeout=5)
            logger.info(f"✅ Flask отвечает на порту {FLASK_PORT}: {response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Flask тест не удался: {e}")
        
        # Запускаем бота
        log_action("🤖 Telegram Bot запущен")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main())
