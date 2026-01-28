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

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)

# Конфигурация
PROJECTS_DIR = "/app/projects"
CONFIG_FILE = "/app/config/config.json"
LOG_FILE = "/app/config/deploy.log"
BOT_TOKEN = os.getenv('BOT_TOKEN', '8035930401:AAHU8hSEUc1pCav8-_GOHWkWLPC5yXR5FRc')
ADMIN_IDS = [8473513085]

# Создаём директории
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs("/app/config", exist_ok=True)

# Telegram Bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные
user_states = {}
flask_running = False

# === ФУНКЦИИ БЕЗ GIT ===

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
        
        # Скачиваем с таймаутом
        response = requests.get(zip_url, timeout=30)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: Не удалось скачать репозиторий")
        
        logger.info(f"Скачано {len(response.content)} байт")
        
        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            temp_file.write(response.content)
            temp_zip_path = temp_file.name
        
        # Распаковываем
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            with tempfile.TemporaryDirectory() as temp_extract_dir:
                zip_ref.extractall(temp_extract_dir)
                
                extracted_folders = os.listdir(temp_extract_dir)
                if not extracted_folders:
                    raise Exception("Пустой архив")
                
                source_dir = os.path.join(temp_extract_dir, extracted_folders[0])
                
                # Создаём целевую директорию
                if target_dir and not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                
                if target_dir:
                    # Очищаем и копируем
                    for item in os.listdir(target_dir):
                        item_path = os.path.join(target_dir, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                        except:
                            pass
                    
                    # Копируем файлы
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
        
        # Удаляем временный файл
        os.unlink(temp_zip_path)
        
        logger.info(f"Репозиторий успешно скачан в {target_dir}")
        return True
        
    except Exception as e:
        logger.error(f"ОШИБКА скачивания: {str(e)}")
        raise e

# === ФУНКЦИИ КОНФИГУРАЦИИ ===

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
    """Безопасная отправка сообщений с ограничением длины"""
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
        InlineKeyboardButton(text="🌐 Веб-панель", url="https://server.bothost.py"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="📋 Логи", callback_data="logs")
    )
    keyboard.adjust(2, 1, 2)
    
    response_text = safe_message_send(
        "🚀 <b>Deploy Manager Pro</b>\n\n"
        "Система управления деплоем!\n"
        "✅ BotHost совместимая v3.1\n"
        "✅ HTTP API без Git\n\n"
        "Выберите действие:"
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
    
    for name, info in list(projects.items())[:5]:  # Ограничиваем до 5 проектов
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
        
        # Обновляем зависимости
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
            state["project_name"] = message.text.strip()[:50]  # Ограничиваем длину
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
        
        # Зависимости
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.run(['pip', 'install', '-r', req_file], capture_output=True)
        
        # Сохраняем
        config = load_config()
        config['projects'][project_name] = {
            'repo_url': repo_url,
            'branch': branch,
            'path': project_path,
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_config(config)
        
        # Очищаем состояние
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
        f"📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"🌐 <b>Панель:</b> server.bothost.py\n"
        f"💡 <b>Версия:</b> v3.1",
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
            
            # Последние 10 строк
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
        InlineKeyboardButton(text="🌐 Панель", url="https://server.bothost.py"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats")
    )
    keyboard.adjust(2, 2)
    
    await callback.message.edit_text(
        "🚀 <b>Deploy Manager Pro</b>\n\n"
        "Система управления деплоем v3.1\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

# === FLASK ROUTES (упрощенные) ===

@app.route('/')
def index():
    return """
    <html>
    <head>
        <title>Deploy Manager Pro v3.1</title>
        <meta charset="utf-8">
        <style>
            body { 
                font-family: Arial; 
                background: linear-gradient(135deg, #667eea, #764ba2); 
                margin: 0; 
                padding: 20px; 
                color: white;
            }
            .container { 
                max-width: 800px; 
                margin: 0 auto; 
                background: white; 
                padding: 30px; 
                border-radius: 15px; 
                color: #333;
            }
            h1 { color: #333; margin-bottom: 20px; }
            .status { 
                background: #28a745; 
                color: white; 
                padding: 15px; 
                border-radius: 5px; 
                margin: 20px 0; 
                text-align: center;
            }
            .info { 
                background: #f8f9fa; 
                padding: 15px; 
                border-radius: 5px; 
                margin: 20px 0; 
            }
            button { 
                background: #667eea; 
                color: white; 
                border: none; 
                padding: 12px 20px; 
                border-radius: 5px; 
                cursor: pointer; 
                margin: 5px;
            }
            input { 
                padding: 10px; 
                border: 1px solid #ddd; 
                border-radius: 5px; 
                margin: 5px; 
                width: 300px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Deploy Manager Pro v3.1</h1>
            
            <div class="status">
                ✅ Система работает!
            </div>
            
            <div class="info">
                <h3>📱 Telegram Bot</h3>
                <p>Отправьте <code>/start</code> боту для управления</p>
                <p><strong>Функции:</strong></p>
                <ul>
                    <li>📦 Управление проектами</li>
                    <li>🚀 Деплой из GitHub</li>
                    <li>🔄 Обновление проектов</li>
                    <li>📊 Статистика и логи</li>
                </ul>
            </div>
            
            <div class="info">
                <h3>🌐 Веб API</h3>
                <p><strong>Доступные endpoints:</strong></p>
                <ul>
                    <li><code>GET /api/projects</code> - Список проектов</li>
                    <li><code>GET /api/logs</code> - Логи системы</li>
                    <li><code>POST /webhook</code> - GitHub webhook</li>
                </ul>
            </div>
            
            <div style="text-align: center; margin-top: 30px;">
                <button onclick="location.reload()">🔄 Обновить</button>
                <button onclick="window.open('/api/projects')">📦 API Проекты</button>
                <button onclick="window.open('/api/logs')">📋 Логи</button>
            </div>
            
            <div style="margin-top: 20px; text-align: center; color: #666;">
                <p>Deploy Manager Pro v3.1 - BotHost Compatible</p>
                <p>Работает на HTTP API без Git клиента</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/api/projects')
def api_projects():
    try:
        config = load_config()
        return jsonify(config.get('projects', {}))
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/logs')
def api_logs():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return content[-5000:]  # Последние 5000 символов
        return "Логи пусты"
    except Exception as e:
        return f"Ошибка чтения логов: {str(e)}"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        repo_url = data.get('repository', {}).get('clone_url')
        
        config = load_config()
        for name, project in config['projects'].items():
            if project['repo_url'] == repo_url:
                log_action(f"Webhook: обновление {name}")
                download_repo_from_github(repo_url, project['branch'], project['path'])
                config['projects'][name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_config(config)
                return jsonify({"status": "updated", "project": name})
        
        return jsonify({"status": "no matching project"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "3.1", "flask_running": flask_running})

# === ЗАПУСК (исправленный) ===

def run_flask():
    global flask_running
    try:
        logger.info("🌐 Запуск Flask сервера...")
        flask_running = True
        app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        logger.error(f"Ошибка Flask: {e}")
        flask_running = False

async def main():
    try:
        log_action("🚀 Deploy Manager Pro v3.1 запущен")
        
        # Запускаем Flask
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        # Ждём запуска Flask
        await asyncio.sleep(3)
        logger.info("✅ Flask запущен")
        
        # Запускаем бота
        log_action("🤖 Telegram Bot запущен")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main())
