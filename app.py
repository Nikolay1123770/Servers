from flask import Flask, render_template_string, request, jsonify, send_from_directory
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
import hashlib
import time

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)
app.config['SECRET_KEY'] = 'deploy-manager-pro-secret-key-v4'

# Конфигурация
PROJECTS_DIR = "/app/projects"
CONFIG_FILE = "/app/config/config.json"
LOG_FILE = "/app/config/deploy.log"
UPLOADS_DIR = "/app/uploads"
BOT_TOKEN = os.getenv('BOT_TOKEN', '7966969765:AAEZLNOFRmv2hPJ8fQaE3u2KSPsoxreDn-E')
ADMIN_IDS = [1769269442]

# Настройки Flask для BotHost
FLASK_PORT = int(os.getenv('PORT', 3000))
FLASK_HOST = '0.0.0.0'

# Создаём директории
for dir_path in [PROJECTS_DIR, UPLOADS_DIR, "/app/config"]:
    os.makedirs(dir_path, exist_ok=True)

# Telegram Bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные
user_states = {}
flask_running = False
system_stats = {
    "start_time": datetime.now(),
    "deploys": 0,
    "updates": 0,
    "errors": 0
}

# === MIDDLEWARE ===
@app.before_request
def log_request_info():
    logger.info(f"🌐 {request.method} {request.path} from {request.remote_addr}")

@app.after_request
def after_request(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['X-Powered-By'] = 'Deploy Manager Pro v4.0'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# === UTILITY FUNCTIONS ===

def download_repo_from_github(repo_url, branch="main", target_dir=None):
    """Скачивание репозитория через GitHub API"""
    try:
        system_stats["deploys"] += 1
        logger.info(f"Скачивание {repo_url}, ветка {branch}")
        
        if "github.com" not in repo_url:
            raise Exception("Поддерживается только GitHub")
        
        parts = repo_url.replace("https://github.com/", "").replace(".git", "").split("/")
        if len(parts) < 2:
            raise Exception("Неверный формат URL")
        
        username, repo_name = parts[0], parts[1]
        zip_url = f"https://github.com/{username}/{repo_name}/archive/refs/heads/{branch}.zip"
        
        response = requests.get(zip_url, timeout=30)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            temp_file.write(response.content)
            temp_zip_path = temp_file.name
        
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            with tempfile.TemporaryDirectory() as temp_extract_dir:
                zip_ref.extractall(temp_extract_dir)
                
                extracted_folders = os.listdir(temp_extract_dir)
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
        logger.info(f"Репозиторий скачан в {target_dir}")
        return True
        
    except Exception as e:
        system_stats["errors"] += 1
        logger.error(f"Ошибка скачивания: {str(e)}")
        raise e

def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки конфига: {e}")
    return {"projects": {}, "settings": {"webhook_secret": "", "auto_deploy": True}}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения конфига: {e}")

def log_action(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] [{level}] {message}"
    logger.info(log_message)
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_message + "\n")
    except Exception as e:
        logger.error(f"Ошибка записи в лог: {e}")

def get_project_info(project_path):
    """Получение информации о проекте"""
    info = {
        "files_count": 0,
        "size_mb": 0,
        "last_modified": None,
        "has_requirements": False,
        "python_files": 0
    }
    
    try:
        if os.path.exists(project_path):
            total_size = 0
            for root, dirs, files in os.walk(project_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.exists(file_path):
                        file_size = os.path.getsize(file_path)
                        total_size += file_size
                        info["files_count"] += 1
                        
                        if file.endswith('.py'):
                            info["python_files"] += 1
                        elif file == 'requirements.txt':
                            info["has_requirements"] = True
                        
                        # Последнее изменение
                        mtime = os.path.getmtime(file_path)
                        if not info["last_modified"] or mtime > info["last_modified"]:
                            info["last_modified"] = datetime.fromtimestamp(mtime)
            
            info["size_mb"] = round(total_size / (1024 * 1024), 2)
            if info["last_modified"]:
                info["last_modified"] = info["last_modified"].strftime("%Y-%m-%d %H:%M:%S")
                
    except Exception as e:
        logger.error(f"Ошибка получения информации о проекте: {e}")
    
    return info

def is_admin(user_id):
    return user_id in ADMIN_IDS

def safe_message_send(message_text, parse_mode="HTML"):
    if len(message_text) > 4000:
        return message_text[:4000] + "..."
    return message_text

def install_requirements(project_path, project_name):
    """Установка зависимостей проекта"""
    req_file = os.path.join(project_path, 'requirements.txt')
    if os.path.exists(req_file):
        log_action(f"Установка зависимостей для {project_name}")
        try:
            result = subprocess.run(
                ['pip', 'install', '-r', req_file],
                capture_output=True,
                text=True,
                timeout=300  # 5 минут таймаут
            )
            if result.returncode == 0:
                log_action(f"Зависимости установлены для {project_name}")
                return True, "Зависимости установлены успешно"
            else:
                log_action(f"Ошибка установки зависимостей для {project_name}: {result.stderr}", "ERROR")
                return False, result.stderr
        except subprocess.TimeoutExpired:
            return False, "Таймаут установки зависимостей"
        except Exception as e:
            return False, str(e)
    return True, "requirements.txt не найден"

# === TELEGRAM BOT HANDLERS (улучшенные) ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    
    config = load_config()
    projects_count = len(config.get('projects', {}))
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="📦 Мои проекты", callback_data="list_projects"),
        InlineKeyboardButton(text="🚀 Деплой проекта", callback_data="deploy_start"),
        InlineKeyboardButton(text="🌐 Веб-панель", url="https://server.bothost.ru"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        InlineKeyboardButton(text="📋 Логи", callback_data="logs")
    )
    keyboard.adjust(2, 1, 2, 1)
    
    uptime = datetime.now() - system_stats["start_time"]
    uptime_str = str(uptime).split('.')[0]
    
    response_text = safe_message_send(
        f"🚀 <b>Deploy Manager Pro v4.0</b>\n\n"
        f"✅ Система работает: {uptime_str}\n"
        f"📦 Проектов: {projects_count}\n"
        f"🔄 Деплоев: {system_stats['deploys']}\n"
        f"🌐 Веб-панель: server.bothost.ru\n\n"
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
            "Используйте кнопку ниже для деплоя первого проекта.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Деплой", callback_data="deploy_start"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )
        return
    
    text = "📦 <b>Мои проекты:</b>\n\n"
    keyboard = InlineKeyboardBuilder()
    
    for name, info in list(projects.items())[:6]:  # Показываем до 6 проектов
        project_info = get_project_info(info.get('path', ''))
        
        text += f"▪️ <b>{name}</b>\n"
        text += f"   🔗 {info.get('repo_url', 'N/A')[:45]}...\n"
        text += f"   🌿 {info.get('branch', 'main')} • "
        text += f"📁 {project_info['files_count']} файлов • "
        text += f"💾 {project_info['size_mb']} MB\n"
        text += f"   🕐 {info.get('last_update', 'Никогда')}\n\n"
        
        keyboard.add(InlineKeyboardButton(
            text=f"⚙️ {name}", 
            callback_data=f"manage_{name}"
        ))
    
    if len(projects) > 6:
        text += f"... и ещё {len(projects) - 6} проектов\n\n"
    
    keyboard.add(
        InlineKeyboardButton(text="🚀 Новый проект", callback_data="deploy_start"),
        InlineKeyboardButton(text="🔄 Обновить все", callback_data="update_all"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    keyboard.adjust(2, 2, 1)
    
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
    project_info = get_project_info(project['path'])
    
    text = f"⚙️ <b>Проект: {project_name}</b>\n\n"
    text += f"🔗 <b>Репозиторий:</b>\n{project['repo_url']}\n\n"
    text += f"🌿 <b>Ветка:</b> {project['branch']}\n"
    text += f"📁 <b>Файлов:</b> {project_info['files_count']} "
    text += f"(🐍 Python: {project_info['python_files']})\n"
    text += f"💾 <b>Размер:</b> {project_info['size_mb']} MB\n"
    text += f"📦 <b>Requirements:</b> {'✅' if project_info['has_requirements'] else '❌'}\n"
    text += f"🕐 <b>Обновлено:</b> {project.get('last_update', 'Никогда')}\n"
    text += f"📝 <b>Изменён:</b> {project_info.get('last_modified', 'Неизвестно')}\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"update_{project_name}"),
        InlineKeyboardButton(text="📁 Файлы", callback_data=f"files_{project_name}"),
        InlineKeyboardButton(text="📋 Логи", callback_data=f"project_logs_{project_name}"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"project_settings_{project_name}"),
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{project_name}"),
        InlineKeyboardButton(text="🔙 К проектам", callback_data="list_projects")
    )
    keyboard.adjust(2, 2, 1, 1)
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("update_"))
async def update_project(callback: CallbackQuery):
    project_name = callback.data.split("update_")[1]
    
    try:
        await callback.answer("🔄 Обновление...")
        await callback.message.edit_text("🔄 <b>Обновление проекта...</b>\n\nПодождите, это может занять время.", parse_mode="HTML")
        
        config = load_config()
        project = config['projects'][project_name]
        
        log_action(f"Bot: Начато обновление {project_name}")
        
        # Скачиваем обновления
        download_repo_from_github(project['repo_url'], project['branch'], project['path'])
        
        # Устанавливаем зависимости
        success, deps_msg = install_requirements(project['path'], project_name)
        
        # Обновляем конфиг
        config['projects'][project_name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        
        system_stats["updates"] += 1
        
        status_emoji = "✅" if success else "⚠️"
        
        await callback.message.edit_text(
            f"{status_emoji} <b>Проект {project_name} обновлен!</b>\n\n"
            f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}\n"
            f"📦 Зависимости: {deps_msg[:100]}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 К проекту", callback_data=f"manage_{project_name}"),
                InlineKeyboardButton(text="📦 Все проекты", callback_data="list_projects")
            ]])
        )
        
        log_action(f"Bot: Обновление {project_name} завершено")
        
    except Exception as e:
        system_stats["errors"] += 1
        log_action(f"Bot: Ошибка обновления {project_name}: {str(e)}", "ERROR")
        await callback.message.edit_text(
            f"❌ <b>Ошибка обновления {project_name}:</b>\n\n"
            f"<code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 К проекту", callback_data=f"manage_{project_name}")
            ]])
        )

@dp.callback_query(F.data == "update_all")
async def update_all_projects(callback: CallbackQuery):
    config = load_config()
    projects = config.get('projects', {})
    
    if not projects:
        await callback.answer("❌ Нет проектов для обновления")
        return
    
    await callback.answer("🔄 Обновление всех проектов...")
    await callback.message.edit_text(
        f"🔄 <b>Массовое обновление</b>\n\n"
        f"Обновляю {len(projects)} проектов...\n"
        f"Это может занять несколько минут.",
        parse_mode="HTML"
    )
    
    updated = 0
    errors = 0
    
    for name, project in projects.items():
        try:
            log_action(f"Mass update: {name}")
            download_repo_from_github(project['repo_url'], project['branch'], project['path'])
            install_requirements(project['path'], name)
            config['projects'][name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated += 1
        except Exception as e:
            log_action(f"Mass update error for {name}: {str(e)}", "ERROR")
            errors += 1
    
    save_config(config)
    
    await callback.message.edit_text(
        f"✅ <b>Массовое обновление завершено!</b>\n\n"
        f"✅ Обновлено: {updated}\n"
        f"❌ Ошибок: {errors}\n"
        f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📦 Проекты", callback_data="list_projects"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="back_to_main")
        ]])
    )

# Остальные обработчики Telegram (deploy, delete, settings, etc.) остаются похожими...

@dp.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    config = load_config()
    projects = config.get('projects', {})
    
    uptime = datetime.now() - system_stats["start_time"]
    uptime_str = str(uptime).split('.')[0]
    
    # Статистика проектов
    total_size = 0
    total_files = 0
    python_files = 0
    
    for project in projects.values():
        if project.get('path') and os.path.exists(project['path']):
            info = get_project_info(project['path'])
            total_size += info['size_mb']
            total_files += info['files_count']
            python_files += info['python_files']
    
    await callback.message.edit_text(
        "📊 <b>Статистика системы</b>\n\n"
        f"⏱️ <b>Время работы:</b> {uptime_str}\n"
        f"📦 <b>Проектов:</b> {len(projects)}\n"
        f"📁 <b>Всего файлов:</b> {total_files}\n"
        f"🐍 <b>Python файлов:</b> {python_files}\n"
        f"💾 <b>Общий размер:</b> {total_size:.1f} MB\n"
        f"🚀 <b>Деплоев:</b> {system_stats['deploys']}\n"
        f"🔄 <b>Обновлений:</b> {system_stats['updates']}\n"
        f"❌ <b>Ошибок:</b> {system_stats['errors']}\n\n"
        f"🌐 <b>Веб-панель:</b> server.bothost.ru\n"
        f"🔧 <b>Версия:</b> 4.0\n"
        f"📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="stats"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]])
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    if callback.from_user.id in user_states:
        del user_states[callback.from_user.id]
    
    await cmd_start(types.Message(
        message_id=callback.message.message_id,
        date=callback.message.date,
        chat=callback.message.chat,
        from_user=callback.from_user
    ))

# === FLASK ROUTES (полные) ===

@app.route('/')
def index():
    logger.info("🏠 Загрузка главной страницы")
    config = load_config()
    projects = config.get('projects', {})
    
    return render_template_string("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Deploy Manager Pro v4.0</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        :root {
            --primary: #667eea;
            --secondary: #764ba2;
            --success: #28a745;
            --warning: #ffc107;
            --danger: #dc3545;
            --info: #17a2b8;
            --dark: #343a40;
            --light: #f8f9fa;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            min-height: 100vh;
            color: #333;
        }
        
        .navbar {
            background: rgba(255,255,255,0.95);
            backdrop-filter: blur(20px);
            padding: 1rem 0;
            box-shadow: 0 2px 20px rgba(0,0,0,0.1);
            position: sticky;
            top: 0;
            z-index: 1000;
        }
        
        .nav-container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .nav-links {
            display: flex;
            gap: 2rem;
            list-style: none;
        }
        
        .nav-links a {
            text-decoration: none;
            color: #333;
            font-weight: 500;
            transition: color 0.3s;
        }
        
        .nav-links a:hover {
            color: var(--primary);
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }
        
        .hero {
            text-align: center;
            padding: 3rem 0;
            color: white;
        }
        
        .hero h1 {
            font-size: 3.5rem;
            margin-bottom: 1rem;
            text-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }
        
        .hero p {
            font-size: 1.3rem;
            margin-bottom: 2rem;
            opacity: 0.9;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 2rem;
            margin: 3rem 0;
        }
        
        .stat-card {
            background: rgba(255,255,255,0.95);
            backdrop-filter: blur(20px);
            padding: 2rem;
            border-radius: 20px;
            text-align: center;
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            transition: transform 0.3s ease;
        }
        
        .stat-card:hover {
            transform: translateY(-5px);
        }
        
        .stat-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .stat-number {
            font-size: 2.5rem;
            font-weight: 700;
            color: var(--primary);
            display: block;
        }
        
        .stat-label {
            font-size: 1.1rem;
            color: #666;
            margin-top: 0.5rem;
        }
        
        .features-section {
            background: rgba(255,255,255,0.95);
            backdrop-filter: blur(20px);
            border-radius: 20px;
            padding: 3rem;
            margin: 3rem 0;
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        }
        
        .features-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 2rem;
            margin-top: 2rem;
        }
        
        .feature-card {
            padding: 2rem;
            border-radius: 15px;
            background: #f8f9fa;
            border-left: 5px solid var(--primary);
        }
        
        .feature-icon {
            font-size: 2.5rem;
            color: var(--primary);
            margin-bottom: 1rem;
        }
        
        .deploy-section {
            background: rgba(255,255,255,0.95);
            backdrop-filter: blur(20px);
            border-radius: 20px;
            padding: 3rem;
            margin: 3rem 0;
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        }
        
        .form-group {
            margin: 1.5rem 0;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 600;
            color: #333;
        }
        
        .form-control {
            width: 100%;
            padding: 1rem;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            font-size: 1rem;
            transition: border-color 0.3s;
        }
        
        .form-control:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .btn {
            display: inline-block;
            padding: 1rem 2rem;
            border: none;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
        }
        
        .btn-success { background: var(--success); color: white; }
        .btn-info { background: var(--info); color: white; }
        .btn-warning { background: var(--warning); color: #333; }
        .btn-danger { background: var(--danger); color: white; }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        
        .projects-section {
            margin: 3rem 0;
        }
        
        .project-card {
            background: rgba(255,255,255,0.95);
            backdrop-filter: blur(20px);
            border-radius: 15px;
            padding: 2rem;
            margin: 1rem 0;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
            border-left: 5px solid var(--primary);
        }
        
        .project-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        
        .project-title {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--primary);
        }
        
        .project-status {
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-size: 0.9rem;
            font-weight: 500;
        }
        
        .status-active { background: #d4edda; color: #155724; }
        .status-updating { background: #fff3cd; color: #856404; }
        
        .project-info {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin: 1rem 0;
            font-size: 0.9rem;
            color: #666;
        }
        
        .project-actions {
            display: flex;
            gap: 1rem;
            margin-top: 1.5rem;
        }
        
        .btn-sm {
            padding: 0.5rem 1rem;
            font-size: 0.9rem;
        }
        
        .alert {
            padding: 1rem;
            border-radius: 10px;
            margin: 1rem 0;
            border-left: 5px solid;
        }
        
        .alert-success {
            background: #d4edda;
            color: #155724;
            border-color: #28a745;
        }
        
        .alert-error {
            background: #f8d7da;
            color: #721c24;
            border-color: #dc3545;
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .footer {
            text-align: center;
            padding: 3rem 0;
            color: rgba(255,255,255,0.8);
        }
        
        @media (max-width: 768px) {
            .nav-container { flex-direction: column; gap: 1rem; }
            .nav-links { flex-direction: column; text-align: center; }
            .hero h1 { font-size: 2.5rem; }
            .container { padding: 1rem; }
            .project-header { flex-direction: column; align-items: flex-start; gap: 1rem; }
            .project-actions { flex-direction: column; }
        }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="nav-container">
            <div class="logo">
                <i class="fas fa-rocket"></i> Deploy Manager Pro v4.0
            </div>
            <ul class="nav-links">
                <li><a href="#projects"><i class="fas fa-project-diagram"></i> Проекты</a></li>
                <li><a href="#deploy"><i class="fas fa-cloud-upload-alt"></i> Деплой</a></li>
                <li><a href="/api/logs" target="_blank"><i class="fas fa-file-alt"></i> Логи</a></li>
                <li><a href="/health" target="_blank"><i class="fas fa-heartbeat"></i> Статус</a></li>
            </ul>
        </div>
    </nav>

    <div class="hero">
        <div class="container">
            <h1><i class="fas fa-rocket"></i> Deploy Manager Pro</h1>
            <p>Профессиональная система управления деплоем на BotHost</p>
        </div>
    </div>

    <div class="container">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-project-diagram"></i></div>
                <span class="stat-number" id="projectsCount">{{ projects|length }}</span>
                <div class="stat-label">Активных проектов</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-cloud-upload-alt"></i></div>
                <span class="stat-number" id="deploysCount">{{ stats.deploys }}</span>
                <div class="stat-label">Всего деплоев</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-sync-alt"></i></div>
                <span class="stat-number" id="updatesCount">{{ stats.updates }}</span>
                <div class="stat-label">Обновлений</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon"><i class="fas fa-clock"></i></div>
                <span class="stat-number" id="uptime">Online</span>
                <div class="stat-label">Статус системы</div>
            </div>
        </div>

        <div class="features-section">
            <h2><i class="fas fa-star"></i> Возможности системы</h2>
            <div class="features-grid">
                <div class="feature-card">
                    <div class="feature-icon"><i class="fab fa-github"></i></div>
                    <h3>GitHub интеграция</h3>
                    <p>Прямая загрузка репозиториев через HTTP API без Git клиента. Поддержка автообновлений через webhooks.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon"><i class="fas fa-telegram-plane"></i></div>
                    <h3>Telegram управление</h3>
                    <p>Полное управление через Telegram бота: деплой, обновления, мониторинг и настройки.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon"><i class="fas fa-cogs"></i></div>
                    <h3>Автоматизация</h3>
                    <p>Автоустановка зависимостей, обработка requirements.txt, логирование всех операций.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon"><i class="fas fa-chart-line"></i></div>
                    <h3>Мониторинг</h3>
                    <p>Детальная статистика, логи в реальном времени, информация о проектах и системе.</p>
                </div>
            </div>
        </div>

        <div class="deploy-section" id="deploy">
            <h2><i class="fas fa-rocket"></i> Быстрый деплой</h2>
            <p>Деплой нового проекта из GitHub репозитория</p>
            
            <form id="deployForm">
                <div class="form-group">
                    <label for="projectName"><i class="fas fa-tag"></i> Название проекта</label>
                    <input type="text" id="projectName" class="form-control" placeholder="my-awesome-project" required>
                </div>
                
                <div class="form-group">
                    <label for="repoUrl"><i class="fab fa-github"></i> GitHub URL</label>
                    <input type="url" id="repoUrl" class="form-control" placeholder="https://github.com/username/repository.git" required>
                </div>
                
                <div class="form-group">
                    <label for="branch"><i class="fas fa-code-branch"></i> Ветка</label>
                    <input type="text" id="branch" class="form-control" placeholder="main" value="main">
                </div>
                
                <button type="submit" class="btn btn-primary">
                    <i class="fas fa-rocket"></i> Запустить деплой
                </button>
            </form>
            
            <div id="deployStatus"></div>
        </div>

        <div class="projects-section" id="projects">
            <h2><i class="fas fa-project-diagram"></i> Мои проекты</h2>
            <div id="projectsList">
                <div class="loading" style="margin: 2rem auto;"></div>
            </div>
        </div>
    </div>

    <div class="footer">
        <div class="container">
            <p><strong>Deploy Manager Pro v4.0</strong></p>
            <p>BotHost Compatible • Full-Featured • Production Ready</p>
            <p><i class="fas fa-heart" style="color: #ff6b6b;"></i> Made with Love for Developers</p>
        </div>
    </div>

    <script>
        // Обновление статистики в реальном времени
        function updateStats() {
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('projectsCount').textContent = data.projects || 0;
                    document.getElementById('deploysCount').textContent = data.deploys || 0;
                    document.getElementById('updatesCount').textContent = data.updates || 0;
                    document.getElementById('uptime').textContent = data.uptime || 'Online';
                })
                .catch(err => console.log('Ошибка загрузки статистики:', err));
        }

        // Загрузка проектов
        function loadProjects() {
            fetch('/api/projects')
                .then(r => r.json())
                .then(data => {
                    const projectsList = document.getElementById('projectsList');
                    
                    if (Object.keys(data).length === 0) {
                        projectsList.innerHTML = `
                            <div class="project-card">
                                <div style="text-align: center; padding: 2rem;">
                                    <i class="fas fa-inbox" style="font-size: 3rem; color: #ddd; margin-bottom: 1rem;"></i>
                                    <h3>Пока нет проектов</h3>
                                    <p>Используйте форму выше для деплоя первого проекта</p>
                                </div>
                            </div>
                        `;
                        return;
                    }

                    let html = '';
                    for (const [name, info] of Object.entries(data)) {
                        html += `
                            <div class="project-card">
                                <div class="project-header">
                                    <div class="project-title">
                                        <i class="fas fa-folder"></i> ${name}
                                    </div>
                                    <div class="project-status status-active">
                                        <i class="fas fa-check-circle"></i> Активен
                                    </div>
                                </div>
                                
                                <div class="project-info">
                                    <div><i class="fab fa-github"></i> <strong>Репозиторий:</strong> ${info.repo_url}</div>
                                    <div><i class="fas fa-code-branch"></i> <strong>Ветка:</strong> ${info.branch}</div>
                                    <div><i class="fas fa-clock"></i> <strong>Обновлено:</strong> ${info.last_update || 'Никогда'}</div>
                                    <div><i class="fas fa-folder"></i> <strong>Путь:</strong> ${info.path}</div>
                                </div>
                                
                                <div class="project-actions">
                                    <button class="btn btn-success btn-sm" onclick="updateProject('${name}')">
                                        <i class="fas fa-sync-alt"></i> Обновить
                                    </button>
                                    <button class="btn btn-info btn-sm" onclick="viewProjectFiles('${name}')">
                                        <i class="fas fa-folder-open"></i> Файлы
                                    </button>
                                    <button class="btn btn-warning btn-sm" onclick="viewProjectLogs('${name}')">
                                        <i class="fas fa-file-alt"></i> Логи
                                    </button>
                                    <button class="btn btn-danger btn-sm" onclick="deleteProject('${name}')">
                                        <i class="fas fa-trash"></i> Удалить
                                    </button>
                                </div>
                            </div>
                        `;
                    }
                    projectsList.innerHTML = html;
                })
                .catch(err => {
                    document.getElementById('projectsList').innerHTML = `
                        <div class="alert alert-error">
                            <i class="fas fa-exclamation-triangle"></i> Ошибка загрузки проектов: ${err.message}
                        </div>
                    `;
                });
        }

        // Деплой проекта
        document.getElementById('deployForm').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const projectName = document.getElementById('projectName').value.trim();
            const repoUrl = document.getElementById('repoUrl').value.trim();
            const branch = document.getElementById('branch').value.trim() || 'main';
            
            if (!projectName || !repoUrl) {
                showStatus('❌ Заполните все обязательные поля', 'error');
                return;
            }
            
            if (!repoUrl.includes('github.com')) {
                showStatus('❌ Поддерживается только GitHub репозитории', 'error');
                return;
            }
            
            showStatus('🔄 Деплой начат... Пожалуйста, подождите.', 'info');
            
            fetch('/api/deploy', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_name: projectName,
                    repo_url: repoUrl,
                    branch: branch
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showStatus('❌ ' + data.error, 'error');
                } else {
                    showStatus('✅ ' + data.message, 'success');
                    document.getElementById('deployForm').reset();
                    document.getElementById('branch').value = 'main';
                    loadProjects();
                    updateStats();
                }
            })
            .catch(err => showStatus('❌ Ошибка сети: ' + err.message, 'error'));
        });

        // Обновление проекта
        function updateProject(name) {
            showStatus(`🔄 Обновление проекта ${name}...`, 'info');
            
            fetch(`/api/update/${name}`, { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        showStatus('❌ ' + data.error, 'error');
                    } else {
                        showStatus('✅ ' + data.message, 'success');
                        loadProjects();
                        updateStats();
                    }
                })
                .catch(err => showStatus('❌ Ошибка: ' + err.message, 'error'));
        }

        // Удаление проекта
        function deleteProject(name) {
            if (!confirm(`Удалить проект "${name}"?\n\nЭто действие нельзя отменить!`)) {
                return;
            }
            
            showStatus(`🗑️ Удаление проекта ${name}...`, 'info');
            
            fetch(`/api/project/${name}`, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        showStatus('❌ ' + data.error, 'error');
                    } else {
                        showStatus('✅ ' + data.message, 'success');
                        loadProjects();
                        updateStats();
                    }
                })
                .catch(err => showStatus('❌ Ошибка: ' + err.message, 'error'));
        }

        // Просмотр файлов проекта
        function viewProjectFiles(name) {
            window.open(`/api/project/${name}/files`, '_blank');
        }

        // Просмотр логов проекта
        function viewProjectLogs(name) {
            window.open(`/api/project/${name}/logs`, '_blank');
        }

        // Показ статуса
        function showStatus(message, type) {
            const statusDiv = document.getElementById('deployStatus');
            const colors = {
                'success': 'alert-success',
                'error': 'alert-error',
                'info': 'alert-success'
            };
            
            statusDiv.innerHTML = `<div class="alert ${colors[type] || 'alert-success'}">${message}</div>`;
            
            setTimeout(() => {
                statusDiv.innerHTML = '';
            }, 5000);
        }

        // Инициализация
        document.addEventListener('DOMContentLoaded', function() {
            loadProjects();
            updateStats();
            
            // Обновление каждые 30 секунд
            setInterval(() => {
                updateStats();
                loadProjects();
            }, 30000);
        });
    </script>
</body>
</html>
    """, projects=projects, stats=system_stats)

@app.route('/api/stats')
def api_stats():
    """API статистики"""
    config = load_config()
    uptime = datetime.now() - system_stats["start_time"]
    
    total_size = 0
    total_files = 0
    
    for project in config.get('projects', {}).values():
        if project.get('path') and os.path.exists(project['path']):
            info = get_project_info(project['path'])
            total_size += info['size_mb']
            total_files += info['files_count']
    
    return jsonify({
        "projects": len(config.get('projects', {})),
        "deploys": system_stats["deploys"],
        "updates": system_stats["updates"],
        "errors": system_stats["errors"],
        "uptime": str(uptime).split('.')[0],
        "total_size_mb": round(total_size, 1),
        "total_files": total_files,
        "flask_port": FLASK_PORT,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/deploy', methods=['POST'])
def api_deploy():
    """API деплоя проектов"""
    try:
        data = request.json
        repo_url = data.get('repo_url')
        project_name = data.get('project_name')
        branch = data.get('branch', 'main')
        
        if not repo_url or not project_name:
            return jsonify({"error": "Не указаны repo_url и project_name"}), 400
        
        if "github.com" not in repo_url:
            return jsonify({"error": "Поддерживается только GitHub"}), 400
        
        # Проверка на существование проекта
        config = load_config()
        projects = config.get('projects', {})
        
        project_path = os.path.join(PROJECTS_DIR, project_name)
        
        if project_name in projects:
            log_action(f"API: Обновление существующего проекта {project_name}")
            action = "обновлен"
        else:
            log_action(f"API: Создание нового проекта {project_name}")
            action = "создан"
        
        # Скачивание репозитория
        if not os.path.exists(project_path):
            os.makedirs(project_path, exist_ok=True)
        
        download_repo_from_github(repo_url, branch, project_path)
        
        # Установка зависимостей
        success, deps_msg = install_requirements(project_path, project_name)
        
        # Сохранение конфигурации
        config['projects'][project_name] = {
            'repo_url': repo_url,
            'branch': branch,
            'path': project_path,
            'created': datetime.now().strftime("%Y-%m-%d %H:%M:%S") if action == "создан" else projects.get(project_name, {}).get('created'),
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'deploy_count': projects.get(project_name, {}).get('deploy_count', 0) + 1
        }
        save_config(config)
        
        log_action(f"API: Проект {project_name} успешно {action}")
        
        return jsonify({
            "status": "success",
            "action": action,
            "project": project_name,
            "message": f"Проект {project_name} успешно {action}!",
            "dependencies": deps_msg,
            "path": project_path,
            "info": get_project_info(project_path)
        })
    
    except Exception as e:
        system_stats["errors"] += 1
        log_action(f"API: ОШИБКА деплоя: {str(e)}", "ERROR")
        return jsonify({"error": str(e)}), 500

@app.route('/api/projects')
def api_projects():
    """API списка проектов"""
    try:
        config = load_config()
        projects = config.get('projects', {})
        
        # Добавляем дополнительную информацию к каждому проекту
        enhanced_projects = {}
        for name, project in projects.items():
            enhanced_projects[name] = {
                **project,
                **get_project_info(project.get('path', ''))
            }
        
        return jsonify(enhanced_projects)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/update/<name>', methods=['POST'])
def api_update_project(name):
    """API обновления проекта"""
    try:
        config = load_config()
        projects = config.get('projects', {})
        
        if name not in projects:
            return jsonify({"error": "Проект не найден"}), 404
        
        project = projects[name]
        
        log_action(f"API: Начато обновление проекта {name}")
        
        # Скачивание обновлений
        download_repo_from_github(project['repo_url'], project['branch'], project['path'])
        
        # Установка зависимостей
        success, deps_msg = install_requirements(project['path'], name)
        
        # Обновление конфигурации
        config['projects'][name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config['projects'][name]['update_count'] = config['projects'][name].get('update_count', 0) + 1
        save_config(config)
        
        system_stats["updates"] += 1
        log_action(f"API: Проект {name} успешно обновлен")
        
        return jsonify({
            "status": "success",
            "project": name,
            "message": f"Проект {name} обновлен!",
            "dependencies": deps_msg,
            "info": get_project_info(project['path'])
        })
    
    except Exception as e:
        system_stats["errors"] += 1
        log_action(f"API: ОШИБКА обновления {name}: {str(e)}", "ERROR")
        return jsonify({"error": str(e)}), 500

@app.route('/api/project/<name>', methods=['DELETE'])
def api_delete_project(name):
    """API удаления проекта"""
    try:
        config = load_config()
        projects = config.get('projects', {})
        
        if name not in projects:
            return jsonify({"error": "Проект не найден"}), 404
        
        project_path = projects[name]['path']
        
        log_action(f"API: Удаление проекта {name}")
        
        # Удаление директории
        if os.path.exists(project_path):
            shutil.rmtree(project_path)
        
        # Удаление из конфигурации
        del config['projects'][name]
        save_config(config)
        
        log_action(f"API: Проект {name} успешно удален")
        
        return jsonify({
            "status": "success",
            "project": name,
            "message": f"Проект {name} удален!"
        })
    
    except Exception as e:
        log_action(f"API: ОШИБКА удаления {name}: {str(e)}", "ERROR")
        return jsonify({"error": str(e)}), 500

@app.route('/api/project/<name>/files')
def api_project_files(name):
    """API просмотра файлов проекта"""
    try:
        config = load_config()
        projects = config.get('projects', {})
        
        if name not in projects:
            return jsonify({"error": "Проект не найден"}), 404
        
        project_path = projects[name]['path']
        
        if not os.path.exists(project_path):
            return jsonify({"error": "Директория проекта не найдена"}), 404
        
        files = []
        for root, dirs, filenames in os.walk(project_path):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, project_path)
                file_size = os.path.getsize(file_path)
                file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                
                files.append({
                    "name": filename,
                    "path": rel_path,
                    "size": file_size,
                    "size_human": f"{file_size / 1024:.1f} KB" if file_size > 1024 else f"{file_size} B",
                    "modified": file_mtime.strftime("%Y-%m-%d %H:%M:%S"),
                    "type": filename.split('.')[-1] if '.' in filename else 'unknown'
                })
        
        # Сортировка по имени
        files.sort(key=lambda x: x['name'])
        
        return jsonify({
            "project": name,
            "files": files,
            "total_files": len(files),
            "total_size": sum(f['size'] for f in files)
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/project/<name>/logs')
def api_project_logs(name):
    """API логов проекта"""
    try:
        config = load_config()
        projects = config.get('projects', {})
        
        if name not in projects:
            return "Проект не найден", 404
        
        # Фильтрация логов по названию проекта
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                all_logs = f.read()
            
            # Выбираем только логи, связанные с этим проектом
            project_logs = []
            for line in all_logs.split('\n'):
                if name in line:
                    project_logs.append(line)
            
            return '\n'.join(project_logs[-100:]), 200, {'Content-Type': 'text/plain; charset=utf-8'}
        
        return f"Логи для проекта {name} не найдены", 200, {'Content-Type': 'text/plain; charset=utf-8'}
    
    except Exception as e:
        return f"Ошибка: {str(e)}", 500, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/health')
def health():
    """API проверки здоровья системы"""
    config = load_config()
    uptime = datetime.now() - system_stats["start_time"]
    
    return jsonify({
        "status": "ok", 
        "version": "4.0",
        "message": "Deploy Manager Pro Full Edition",
        "uptime": str(uptime).split('.')[0],
        "flask_running": flask_running,
        "flask_port": FLASK_PORT,
        "flask_host": FLASK_HOST,
        "projects_count": len(config.get('projects', {})),
        "system_stats": system_stats,
        "timestamp": datetime.now().isoformat(),
        "bot_active": True,
        "features": [
            "GitHub Integration",
            "Telegram Bot",
            "Auto Dependencies",
            "Real-time Monitoring",
            "Project Management",
            "Webhook Support"
        ],
        "environment": {
            "PORT": os.getenv('PORT'),
            "BOTHOST_USER_ID": os.getenv('BOTHOST_USER_ID'),
            "BOTHOST_USER_PLAN": os.getenv('BOTHOST_USER_PLAN'),
            "BOTHOST_MAX_BOTS": os.getenv('BOTHOST_MAX_BOTS')
        }
    })

@app.route('/api/logs')
def api_logs():
    """API системных логов"""
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Последние 1000 строк или 50KB
            lines = content.split('\n')[-1000:]
            recent_logs = '\n'.join(lines)
            
            if len(recent_logs) > 50000:
                recent_logs = recent_logs[-50000:]
            
            return recent_logs, 200, {'Content-Type': 'text/plain; charset=utf-8'}
        
        return "Системные логи пусты", 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        return f"Ошибка чтения логов: {str(e)}", 500, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/webhook', methods=['POST'])
def webhook():
    """GitHub webhook обработчик"""
    try:
        data = request.json
        
        if not data:
            return jsonify({"error": "Пустые данные"}), 400
        
        # Получаем URL репозитория
        repo_url = data.get('repository', {}).get('clone_url') or data.get('repository', {}).get('html_url')
        
        if not repo_url:
            return jsonify({"error": "URL репозитория не найден"}), 400
        
        # Ищем соответствующий проект
        config = load_config()
        updated_projects = []
        
        for name, project in config['projects'].items():
            if project['repo_url'] in repo_url or repo_url in project['repo_url']:
                try:
                    log_action(f"Webhook: автообновление {name}")
                    download_repo_from_github(project['repo_url'], project['branch'], project['path'])
                    install_requirements(project['path'], name)
                    
                    config['projects'][name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    config['projects'][name]['webhook_updates'] = config['projects'][name].get('webhook_updates', 0) + 1
                    
                    updated_projects.append(name)
                    log_action(f"Webhook: {name} успешно обновлен")
                    
                except Exception as e:
                    log_action(f"Webhook: ошибка обновления {name}: {str(e)}", "ERROR")
        
        save_config(config)
        
        if updated_projects:
            return jsonify({
                "status": "success", 
                "updated_projects": updated_projects,
                "message": f"Обновлено проектов: {len(updated_projects)}"
            })
        else:
            return jsonify({"status": "no_matching_projects"}), 404
    
    except Exception as e:
        log_action(f"Webhook: ОШИБКА: {str(e)}", "ERROR")
        return jsonify({"error": str(e)}), 500

# Добавляем обработчики для остальных Telegram команд...
# (deploy_start, handle_deploy_steps, и т.д. - те же что в предыдущих версиях)

@dp.callback_query(F.data == "deploy_start")
async def deploy_start(callback: CallbackQuery):
    user_states[callback.from_user.id] = {"step": "name"}
    
    await callback.message.edit_text(
        "🚀 <b>Деплой нового проекта</b>\n\n"
        "Шаг 1/3: Введите название проекта\n\n"
        "📋 Требования:\n"
        "• Только латинские буквы и цифры\n"
        "• Длина: 3-50 символов\n"
        "• Пример: <code>my-awesome-bot</code>",
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
            project_name = message.text.strip()
            
            # Валидация названия
            if not project_name or len(project_name) < 3 or len(project_name) > 50:
                await message.answer("❌ Название должно быть от 3 до 50 символов")
                return
            
            # Проверка на существование
            config = load_config()
            if project_name in config.get('projects', {}):
                await message.answer(f"❌ Проект '{project_name}' уже существует")
                return
            
            state["project_name"] = project_name
            state["step"] = "url"
            
            await message.answer(
                f"📦 <b>Деплой проекта</b>\n\n"
                f"✅ Название: <code>{project_name}</code>\n\n"
                f"Шаг 2/3: Введите URL GitHub репозитория\n\n"
                f"📋 Поддерживаемые форматы:\n"
                f"• <code>https://github.com/user/repo.git</code>\n"
                f"• <code>https://github.com/user/repo</code>\n\n"
                f"⚠️ Поддерживается только GitHub!",
                parse_mode="HTML"
            )
            
        elif state["step"] == "url":
            repo_url = message.text.strip()
            
            if "github.com" not in repo_url:
                await message.answer("❌ Поддерживается только GitHub репозитории")
                return
            
            state["repo_url"] = repo_url
            state["step"] = "branch"
            
            await message.answer(
                f"🌿 <b>Деплой проекта</b>\n\n"
                f"✅ Название: <code>{state['project_name']}</code>\n"
                f"✅ Репозиторий: GitHub\n\n"
                f"Шаг 3/3: Введите ветку или выберите main",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Использовать main", callback_data="deploy_main_branch"),
                    InlineKeyboardButton(text="🌿 master", callback_data="deploy_master_branch")
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

@dp.callback_query(F.data == "deploy_master_branch")
async def deploy_master_branch(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_states:
        user_states[user_id]["branch"] = "master"
        await start_deploy(callback.message, user_states[user_id])

async def start_deploy(message, state):
    try:
        await message.answer("🔄 <b>Запуск деплоя...</b>\n\nПодождите, это может занять время.", parse_mode="HTML")
        
        project_name = state["project_name"]
        repo_url = state["repo_url"]
        branch = state["branch"]
        
        project_path = os.path.join(PROJECTS_DIR, project_name)
        
        # Создание директории
        os.makedirs(project_path, exist_ok=True)
        
        # Скачивание репозитория
        log_action(f"Bot: Начат деплой {project_name}")
        download_repo_from_github(repo_url, branch, project_path)
        
        # Установка зависимостей
        success, deps_msg = install_requirements(project_path, project_name)
        
        # Получение информации о проекте
        project_info = get_project_info(project_path)
        
        # Сохранение конфигурации
        config = load_config()
        config['projects'][project_name] = {
            'repo_url': repo_url,
            'branch': branch,
            'path': project_path,
            'created': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'deploy_count': 1
        }
        save_config(config)
        
        # Очистка состояния
        del user_states[message.from_user.id]
        
        status_emoji = "✅" if success else "⚠️"
        
        await message.answer(
            f"{status_emoji} <b>Проект успешно задеплоен!</b>\n\n"
            f"📦 <b>Название:</b> {project_name}\n"
            f"🌿 <b>Ветка:</b> {branch}\n"
            f"📁 <b>Файлов:</b> {project_info['files_count']} (🐍 {project_info['python_files']})\n"
            f"💾 <b>Размер:</b> {project_info['size_mb']} MB\n"
            f"📦 <b>Зависимости:</b> {deps_msg[:50]}...\n"
            f"🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⚙️ Управление", callback_data=f"manage_{project_name}"),
                InlineKeyboardButton(text="📦 Все проекты", callback_data="list_projects"),
                InlineKeyboardButton(text="🏠 Главная", callback_data="back_to_main")
            ]])
        )
        
        log_action(f"Bot: Проект {project_name} успешно задеплоен")
        
    except Exception as e:
        system_stats["errors"] += 1
        log_action(f"Bot: ОШИБКА деплоя {project_name}: {str(e)}", "ERROR")
        
        if message.from_user.id in user_states:
            del user_states[message.from_user.id]
        
        await message.answer(
            f"❌ <b>Ошибка деплоя:</b>\n\n"
            f"<code>{str(e)[:300]}</code>\n\n"
            f"Проверьте:\n"
            f"• Корректность URL репозитория\n"
            f"• Существование указанной ветки\n"
            f"• Доступность GitHub",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="deploy_start"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )

# === ЗАПУСК СИСТЕМЫ ===

def run_flask():
    global flask_running
    try:
        logger.info(f"🌐 Запуск Flask сервера на {FLASK_HOST}:{FLASK_PORT}")
        flask_running = True
        
        app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
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
        log_action("🚀 Deploy Manager Pro v4.0 - ПОЛНАЯ ВЕРСИЯ запущена")
        
        logger.info(f"🔧 Конфигурация:")
        logger.info(f"   Flask: {FLASK_HOST}:{FLASK_PORT}")
        logger.info(f"   Проекты: {PROJECTS_DIR}")
        logger.info(f"   Логи: {LOG_FILE}")
        logger.info(f"   BotHost План: {os.getenv('BOTHOST_USER_PLAN', 'unknown')}")
        
        # Запуск Flask сервера
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # Ожидание запуска Flask
        await asyncio.sleep(4)
        
        # Тест подключения
        try:
            response = requests.get(f'http://localhost:{FLASK_PORT}/health', timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ Веб-панель доступна на порту {FLASK_PORT}")
                logger.info(f"✅ Статистика: {response.json()}")
            else:
                logger.warning(f"⚠️ Веб-панель отвечает с кодом {response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Тест веб-панели не удался: {e}")
        
        # Запуск Telegram бота
        log_action("🤖 Telegram Bot запущен - ПОЛНАЯ ВЕРСИЯ")
        logger.info("🎉 DEPLOY MANAGER PRO v4.0 ГОТОВ К РАБОТЕ!")
        logger.info("🌐 Веб-панель: https://server.bothost.ru")
        logger.info("🤖 Telegram: @RegisterMarketPlace_bot")
        
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Критическая ошибка запуска: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main())
