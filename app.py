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
logging.basicConfig(level=logging.INFO)

# Flask приложение
app = Flask(__name__)

# Конфигурация
PROJECTS_DIR = "/app/projects"
CONFIG_FILE = "/app/config/config.json"
LOG_FILE = "/app/config/deploy.log"
BOT_TOKEN = os.getenv('BOT_TOKEN', '7966969765:AAEZLNOFRmv2hPJ8fQaE3u2KSPsoxreDn-E')  # Ваш токен
ADMIN_IDS = [8473513085]  # Ваш Telegram ID

# Создаём директории
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs("/app/config", exist_ok=True)

# Telegram Bot
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные для состояний
user_states = {}

# === ФУНКЦИИ БЕЗ GIT ===

def download_repo_from_github(repo_url, branch="main", target_dir=None):
    """Скачивание репозитория через GitHub API без Git"""
    try:
        # Парсим URL GitHub
        if "github.com" not in repo_url:
            raise Exception("Поддерживается только GitHub")
        
        # Извлекаем username и repo name
        parts = repo_url.replace("https://github.com/", "").replace(".git", "").split("/")
        if len(parts) < 2:
            raise Exception("Неверный формат URL")
        
        username, repo_name = parts[0], parts[1]
        
        # URL для скачивания zip архива
        zip_url = f"https://github.com/{username}/{repo_name}/archive/refs/heads/{branch}.zip"
        
        log_action(f"Скачивание из {zip_url}")
        
        # Скачиваем архив
        response = requests.get(zip_url, timeout=30)
        if response.status_code != 200:
            raise Exception(f"Не удалось скачать репозиторий. HTTP {response.status_code}")
        
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_file:
            temp_file.write(response.content)
            temp_zip_path = temp_file.name
        
        # Распаковываем архив
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            # Создаём временную директорию для распаковки
            with tempfile.TemporaryDirectory() as temp_extract_dir:
                zip_ref.extractall(temp_extract_dir)
                
                # Находим папку с содержимым (обычно repo-name-branch)
                extracted_folders = os.listdir(temp_extract_dir)
                if not extracted_folders:
                    raise Exception("Пустой архив")
                
                source_dir = os.path.join(temp_extract_dir, extracted_folders[0])
                
                # Создаём целевую директорию если её нет
                if target_dir and not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                
                # Копируем файлы
                if target_dir:
                    # Очищаем целевую директорию
                    for item in os.listdir(target_dir):
                        item_path = os.path.join(target_dir, item)
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                    
                    # Копируем новые файлы
                    for item in os.listdir(source_dir):
                        source_item = os.path.join(source_dir, item)
                        target_item = os.path.join(target_dir, item)
                        if os.path.isdir(source_item):
                            shutil.copytree(source_item, target_item)
                        else:
                            shutil.copy2(source_item, target_item)
                else:
                    target_dir = source_dir
        
        # Удаляем временный zip файл
        os.unlink(temp_zip_path)
        
        log_action(f"Репозиторий успешно скачан в {target_dir}")
        return True
        
    except Exception as e:
        log_action(f"ОШИБКА скачивания репозитория: {str(e)}")
        raise e

# === ФУНКЦИИ КОНФИГУРАЦИИ ===
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"projects": {}}

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def log_action(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}\n"
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_message)
    print(log_message.strip())

def is_admin(user_id):
    return user_id in ADMIN_IDS

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
    
    await message.answer(
        "🚀 <b>Deploy Manager Pro</b>\n\n"
        "Добро пожаловать в систему управления деплоем!\n"
        "✅ BotHost совместимая версия\n"
        "✅ Работает без Git через HTTP API\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data == "list_projects")
async def show_projects(callback: CallbackQuery):
    config = load_config()
    projects = config.get('projects', {})
    
    if not projects:
        await callback.message.edit_text(
            "📦 <b>Проекты</b>\n\n"
            "❌ Пока нет проектов\n\n"
            "Нажмите 'Деплой проекта' чтобы добавить первый проект.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Деплой проекта", callback_data="deploy_start"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )
        return
    
    text = "📦 <b>Мои проекты:</b>\n\n"
    keyboard = InlineKeyboardBuilder()
    
    for name, info in projects.items():
        text += f"▪️ <b>{name}</b>\n"
        text += f"   🔗 {info.get('repo_url', 'N/A')}\n"
        text += f"   🌿 {info.get('branch', 'main')}\n"
        text += f"   🕐 {info.get('last_update', 'Никогда')}\n\n"
        
        keyboard.add(InlineKeyboardButton(
            text=f"⚙️ {name}", 
            callback_data=f"manage_{name}"
        ))
    
    keyboard.add(
        InlineKeyboardButton(text="🚀 Деплой проекта", callback_data="deploy_start"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    keyboard.adjust(2)
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data.startswith("manage_"))
async def manage_project(callback: CallbackQuery):
    project_name = callback.data.split("manage_")[1]
    config = load_config()
    
    if project_name not in config['projects']:
        await callback.answer("❌ Проект не найден")
        return
    
    project = config['projects'][project_name]
    
    text = f"⚙️ <b>Управление проектом: {project_name}</b>\n\n"
    text += f"🔗 <b>Репозиторий:</b> {project['repo_url']}\n"
    text += f"🌿 <b>Ветка:</b> {project['branch']}\n"
    text += f"📁 <b>Путь:</b> {project['path']}\n"
    text += f"🕐 <b>Обновлено:</b> {project.get('last_update', 'Никогда')}\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"update_{project_name}"),
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{project_name}"),
        InlineKeyboardButton(text="📋 Детали", callback_data=f"details_{project_name}"),
        InlineKeyboardButton(text="🔙 К проектам", callback_data="list_projects")
    )
    keyboard.adjust(2, 1, 1)
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data.startswith("update_"))
async def update_project(callback: CallbackQuery):
    project_name = callback.data.split("update_")[1]
    
    try:
        await callback.answer("🔄 Обновление начато...")
        
        config = load_config()
        project = config['projects'][project_name]
        project_path = project['path']
        repo_url = project['repo_url']
        branch = project['branch']
        
        log_action(f"Telegram Bot: Обновление проекта {project_name}")
        
        # Скачиваем обновления через HTTP API
        download_repo_from_github(repo_url, branch, project_path)
        
        # Обновляем зависимости
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.run(['pip', 'install', '-r', req_file])
        
        # Обновляем время
        config['projects'][project_name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        
        await callback.message.edit_text(
            f"✅ <b>Проект {project_name} обновлен!</b>\n\n"
            f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 К проекту", callback_data=f"manage_{project_name}"),
                InlineKeyboardButton(text="📦 Все проекты", callback_data="list_projects")
            ]])
        )
        
    except Exception as e:
        log_action(f"ОШИБКА обновления через бота: {str(e)}")
        await callback.message.edit_text(
            f"❌ <b>Ошибка обновления:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 К проекту", callback_data=f"manage_{project_name}")
            ]])
        )

@dp.callback_query(F.data.startswith("delete_"))
async def confirm_delete(callback: CallbackQuery):
    project_name = callback.data.split("delete_")[1]
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="⚠️ Да, удалить", callback_data=f"confirm_delete_{project_name}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"manage_{project_name}")
    )
    
    await callback.message.edit_text(
        f"⚠️ <b>Подтверждение удаления</b>\n\n"
        f"Вы уверены что хотите удалить проект <b>{project_name}</b>?\n\n"
        f"❗️ Это действие нельзя отменить!",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def delete_project(callback: CallbackQuery):
    project_name = callback.data.split("confirm_delete_")[1]
    
    try:
        config = load_config()
        project_path = config['projects'][project_name]['path']
        
        log_action(f"Telegram Bot: Удаление проекта {project_name}")
        
        # Удаляем директорию
        if os.path.exists(project_path):
            shutil.rmtree(project_path)
        
        # Удаляем из конфига
        del config['projects'][project_name]
        save_config(config)
        
        await callback.message.edit_text(
            f"✅ <b>Проект {project_name} удален!</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📦 Все проекты", callback_data="list_projects")
            ]])
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка удаления:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML"
        )

@dp.callback_query(F.data == "deploy_start")
async def deploy_start(callback: CallbackQuery):
    user_states[callback.from_user.id] = {"step": "name"}
    
    await callback.message.edit_text(
        "🚀 <b>Деплой нового проекта</b>\n\n"
        "Шаг 1/3: Введите название проекта\n\n"
        "Пример: <code>my-telegram-bot</code>",
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
    
    if state["step"] == "name":
        state["project_name"] = message.text.strip()
        state["step"] = "url"
        
        await message.answer(
            "📦 <b>Деплой нового проекта</b>\n\n"
            f"✅ Название: <code>{state['project_name']}</code>\n\n"
            "Шаг 2/3: Введите URL GitHub репозитория\n\n"
            "Пример: <code>https://github.com/user/repo.git</code>\n"
            "⚠️ Поддерживается только GitHub!",
            parse_mode="HTML"
        )
        
    elif state["step"] == "url":
        state["repo_url"] = message.text.strip()
        state["step"] = "branch"
        
        await message.answer(
            "🌿 <b>Деплой нового проекта</b>\n\n"
            f"✅ Название: <code>{state['project_name']}</code>\n"
            f"✅ URL: <code>{state['repo_url']}</code>\n\n"
            "Шаг 3/3: Введите ветку (или нажмите 'Использовать main')\n\n"
            "Пример: <code>main</code>, <code>master</code>, <code>develop</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Использовать main", callback_data="deploy_main_branch")
            ]])
        )
        
    elif state["step"] == "branch":
        state["branch"] = message.text.strip()
        await start_deploy(message, state)

@dp.callback_query(F.data == "deploy_main_branch")
async def deploy_main_branch(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_states:
        user_states[user_id]["branch"] = "main"
        await start_deploy(callback.message, user_states[user_id])

async def start_deploy(message, state):
    try:
        await message.answer("🔄 <b>Деплой начат...</b>\n\nСкачивание через HTTP API...", parse_mode="HTML")
        
        project_name = state["project_name"]
        repo_url = state["repo_url"]
        branch = state["branch"]
        
        project_path = os.path.join(PROJECTS_DIR, project_name)
        
        # Проверяем что это GitHub
        if "github.com" not in repo_url:
            raise Exception("Поддерживается только GitHub")
        
        # Скачиваем репозиторий
        if os.path.exists(project_path):
            log_action(f"Telegram Bot: Обновление проекта {project_name}")
            download_repo_from_github(repo_url, branch, project_path)
            action = "обновлен"
        else:
            log_action(f"Telegram Bot: Клонирование проекта {project_name}")
            os.makedirs(project_path, exist_ok=True)
            download_repo_from_github(repo_url, branch, project_path)
            action = "задеплоен"
        
        # Устанавливаем зависимости
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            log_action(f"Установка зависимостей для {project_name}")
            result = subprocess.run(
                ['pip', 'install', '-r', req_file],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                log_action(f"Предупреждение установки зависимостей: {result.stderr}")
        
        # Сохраняем конфигурацию
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
            f"✅ <b>Проект {action}!</b>\n\n"
            f"📦 <b>Название:</b> {project_name}\n"
            f"🔗 <b>Репозиторий:</b> {repo_url}\n"
            f"🌿 <b>Ветка:</b> {branch}\n"
            f"📁 <b>Путь:</b> {project_path}\n"
            f"🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}\n"
            f"💡 <b>Метод:</b> HTTP API (BotHost совместимо)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📦 Мои проекты", callback_data="list_projects"),
                InlineKeyboardButton(text="🏠 Главная", callback_data="back_to_main")
            ]])
        )
        
    except Exception as e:
        log_action(f"ОШИБКА деплоя через бота: {str(e)}")
        if message.from_user.id in user_states:
            del user_states[message.from_user.id]
        
        await message.answer(
            f"❌ <b>Ошибка деплоя:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )

@dp.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    config = load_config()
    projects = config.get('projects', {})
    
    total_projects = len(projects)
    
    # Считаем проекты обновленные сегодня
    today = datetime.now().strftime("%Y-%m-%d")
    today_updates = 0
    
    for project in projects.values():
        if project.get('last_update', '').startswith(today):
            today_updates += 1
    
    await callback.message.edit_text(
        "📊 <b>Статистика Deploy Manager</b>\n\n"
        f"📦 <b>Всего проектов:</b> {total_projects}\n"
        f"🔄 <b>Обновлено сегодня:</b> {today_updates}\n"
        f"🕐 <b>Текущее время:</b> {datetime.now().strftime('%H:%M:%S')}\n"
        f"📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"🌐 <b>Веб-панель:</b> server.bothost.py\n"
        f"💡 <b>Версия:</b> BotHost Compatible v3.0\n"
        f"🔧 <b>Метод:</b> HTTP API (без Git)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="stats"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ]])
    )

@dp.callback_query(F.data == "logs")
async def show_logs(callback: CallbackQuery):
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            logs = f.read()
        
        # Берем последние 20 строк
        log_lines = logs.split('\n')[-20:]
        recent_logs = '\n'.join(log_lines)
        
        if len(recent_logs) > 3000:
            recent_logs = recent_logs[-3000:]
        
        await callback.message.edit_text(
            f"📋 <b>Последние логи:</b>\n\n"
            f"<code>{recent_logs}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Обновить", callback_data="logs"),
                InlineKeyboardButton(text="🗑️ Очистить логи", callback_data="clear_logs"),
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )
    else:
        await callback.message.edit_text(
            "📋 <b>Логи пусты</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
            ]])
        )

@dp.callback_query(F.data == "clear_logs")
async def clear_logs(callback: CallbackQuery):
    try:
        open(LOG_FILE, 'w').close()
        await callback.answer("✅ Логи очищены")
        await show_logs(callback)
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    # Очищаем состояние пользователя
    if callback.from_user.id in user_states:
        del user_states[callback.from_user.id]
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="📦 Мои проекты", callback_data="list_projects"),
        InlineKeyboardButton(text="🚀 Деплой проекта", callback_data="deploy_start"),
        InlineKeyboardButton(text="🌐 Веб-панель", url="https://server.bothost.py"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="📋 Логи", callback_data="logs")
    )
    keyboard.adjust(2, 1, 2)
    
    await callback.message.edit_text(
        "🚀 <b>Deploy Manager Pro</b>\n\n"
        "Добро пожаловать в систему управления деплоем!\n"
        "✅ BotHost совместимая версия v3.0\n"
        "✅ Работает без Git через HTTP API\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )

# === FLASK ROUTES ===

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Deploy Manager Pro - BotHost v3.0</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { 
            max-width: 1400px; 
            margin: 0 auto; 
            background: white; 
            padding: 30px; 
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 { 
            color: #333; 
            margin-bottom: 10px;
            font-size: 2.5em;
        }
        .subtitle {
            color: #666;
            margin-bottom: 20px;
            font-size: 1.1em;
        }
        .bothost-info {
            background: linear-gradient(135deg, #28a745 0%, #20c997 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            text-align: center;
        }
        .section {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
        }
        h2 {
            color: #444;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }
        .form-group { 
            margin: 15px 0; 
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        input { 
            padding: 12px 15px;
            border-radius: 6px;
            border: 2px solid #ddd;
            font-size: 14px;
            transition: border-color 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        input.input-large { width: 400px; }
        input.input-medium { width: 250px; }
        input.input-small { width: 150px; }
        
        button { 
            padding: 12px 25px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        button:hover { 
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        .btn-primary { 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; 
        }
        .btn-success { 
            background: #28a745; 
            color: white; 
        }
        .btn-danger { 
            background: #dc3545; 
            color: white; 
        }
        .btn-info { 
            background: #17a2b8; 
            color: white; 
        }
        
        .project { 
            background: white;
            padding: 20px;
            margin: 15px 0;
            border-radius: 10px;
            border-left: 5px solid #667eea;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }
        .project:hover {
            transform: translateX(5px);
        }
        .project h3 { 
            color: #333; 
            margin-bottom: 15px;
            font-size: 1.5em;
        }
        .project p { 
            color: #666; 
            margin: 8px 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .project-actions {
            margin-top: 15px;
            display: flex;
            gap: 10px;
        }
        
        .status { 
            padding: 15px 20px;
            border-radius: 8px;
            margin: 15px 0;
            animation: slideIn 0.3s;
        }
        @keyframes slideIn {
            from { transform: translateY(-20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        .success { 
            background: #d4edda; 
            color: #155724; 
            border: 2px solid #28a745;
        }
        .error { 
            background: #f8d7da; 
            color: #721c24; 
            border: 2px solid #dc3545;
        }
        
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .stat-number {
            font-size: 3em;
            font-weight: bold;
            color: #667eea;
        }
        .stat-label {
            color: #666;
            font-size: 1.1em;
            margin-top: 10px;
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #999;
        }
        .empty-state-icon {
            font-size: 5em;
            margin-bottom: 20px;
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        @media (max-width: 768px) {
            .form-group { flex-direction: column; }
            input { width: 100% !important; }
            .project-actions { flex-direction: column; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Deploy Manager Pro v3.0</h1>
        <p class="subtitle">Управление деплоем проектов на BotHost</p>
        
        <div class="bothost-info">
            <h3>✅ BotHost Compatible v3.0</h3>
            <p>🔧 Исправлена проблема с потоками • 🌐 HTTP API • ⚡ Стабильная работа</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number" id="projectCount">0</div>
                <div class="stat-label">Проектов</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="lastUpdate">-</div>
                <div class="stat-label">Последнее обновление</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">🤖</div>
                <div class="stat-label">Telegram Bot</div>
            </div>
        </div>
        
        <div class="section">
            <h2>📦 Деплой нового проекта</h2>
            <div class="form-group">
                <input type="text" id="projectName" placeholder="Название проекта" class="input-medium">
                <input type="text" id="repoUrl" placeholder="GitHub Repository URL" class="input-large">
                <input type="text" id="branch" placeholder="Branch (main)" class="input-small">
                <button onclick="deployProject()" class="btn-primary">🚀 Деплой</button>
            </div>
            
            <details style="margin-top: 15px;">
                <summary style="cursor: pointer; color: #667eea; font-weight: 600;">ℹ️ Инструкции для BotHost</summary>
                <div style="margin-top: 10px; padding: 10px; background: white; border-radius: 5px;">
                    <p><strong>⚠️ Только GitHub:</strong> Поддерживается только GitHub репозитории</p>
                    <p><strong>Пример URL:</strong> https://github.com/username/repo.git</p>
                    <p><strong>Ветки:</strong> main, master, develop, etc.</p>
                    <p><strong>Метод:</strong> HTTP API (без Git клиента)</p>
                    <p><strong>Telegram:</strong> Отправьте /start боту для управления</p>
                </div>
            </details>
        </div>
        
        <div id="status"></div>
        
        <div class="section">
            <h2>📋 Мои проекты</h2>
            <div id="projects">
                <div class="loading" style="margin: 20px auto;"></div>
            </div>
        </div>
        
        <div class="section">
            <h2>📊 Логи</h2>
            <button onclick="viewLogs()" class="btn-info">Просмотреть логи</button>
            <button onclick="clearLogs()" class="btn-danger">Очистить логи</button>
            <pre id="logs" style="background: #2d2d2d; color: #f8f8f2; padding: 15px; border-radius: 5px; max-height: 300px; overflow-y: auto; margin-top: 10px; display: none;"></pre>
        </div>
    </div>
    
    <script>
        function deployProject() {
            const projectName = document.getElementById('projectName').value.trim();
            const repoUrl = document.getElementById('repoUrl').value.trim();
            const branch = document.getElementById('branch').value.trim() || 'main';
            
            if (!projectName || !repoUrl) {
                showStatus({error: 'Заполните название проекта и URL репозитория'});
                return;
            }
            
            if (!repoUrl.includes('github.com')) {
                showStatus({error: 'Поддерживается только GitHub репозитории'});
                return;
            }
            
            showStatus({info: 'Деплой начат... Скачивание через HTTP API...'});
            
            const data = {
                project_name: projectName,
                repo_url: repoUrl,
                branch: branch
            };
            
            fetch('/api/deploy', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(r => r.json())
            .then(data => {
                showStatus(data);
                if (!data.error) {
                    document.getElementById('projectName').value = '';
                    document.getElementById('repoUrl').value = '';
                    document.getElementById('branch').value = '';
                }
                loadProjects();
            })
            .catch(err => showStatus({error: err.message}));
        }
        
        function loadProjects() {
            fetch('/api/projects')
            .then(r => r.json())
            .then(data => {
                const projects = Object.entries(data);
                document.getElementById('projectCount').textContent = projects.length;
                
                if (projects.length === 0) {
                    document.getElementById('projects').innerHTML = `
                        <div class="empty-state">
                            <div class="empty-state-icon">📦</div>
                            <h3>Пока нет проектов</h3>
                            <p>Задеплойте GitHub репозиторий выше или через Telegram Bot</p>
                        </div>
                    `;
                    return;
                }
                
                const html = projects.map(([name, info]) => `
                    <div class="project">
                        <h3>📦 ${name}</h3>
                        <p><strong>🔗 Репозиторий:</strong> ${info.repo_url}</p>
                        <p><strong>🌿 Ветка:</strong> ${info.branch}</p>
                        <p><strong>📁 Путь:</strong> ${info.path}</p>
                        ${info.last_update ? `<p><strong>🕐 Обновлено:</strong> ${info.last_update}</p>` : ''}
                        <div class="project-actions">
                            <button onclick="updateProject('${name}')" class="btn-success">🔄 Обновить</button>
                            <button onclick="viewProject('${name}')" class="btn-info">👁️ Просмотр</button>
                            <button onclick="deleteProject('${name}')" class="btn-danger">🗑️ Удалить</button>
                        </div>
                    </div>
                `).join('');
                document.getElementById('projects').innerHTML = html;
                
                const lastUpdate = new Date().toLocaleTimeString('ru-RU');
                document.getElementById('lastUpdate').textContent = lastUpdate;
            })
            .catch(err => console.error('Ошибка загрузки проектов:', err));
        }
        
        function updateProject(name) {
            showStatus({info: `Обновление ${name} через HTTP API...`});
            fetch(`/api/update/${name}`, {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                showStatus(data);
                loadProjects();
            });
        }
        
        function viewProject(name) {
            fetch(`/api/project/${name}/info`)
            .then(r => r.json())
            .then(data => {
                alert(JSON.stringify(data, null, 2));
            });
        }
        
        function deleteProject(name) {
            if (!confirm(`Удалить проект "${name}"?\n\nЭто действие нельзя отменить!`)) {
                return;
            }
            
            showStatus({info: `Удаление ${name}...`});
            fetch(`/api/project/${name}`, {method: 'DELETE'})
            .then(r => r.json())
            .then(data => {
                showStatus(data);
                loadProjects();
            });
        }
        
        function viewLogs() {
            const logsEl = document.getElementById('logs');
            if (logsEl.style.display === 'none') {
                fetch('/api/logs')
                .then(r => r.text())
                .then(data => {
                    logsEl.textContent = data || 'Логи пусты';
                    logsEl.style.display = 'block';
                    logsEl.scrollTop = logsEl.scrollHeight;
                });
            } else {
                logsEl.style.display = 'none';
            }
        }
        
        function clearLogs() {
            if (confirm('Очистить все логи?')) {
                fetch('/api/logs/clear', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    showStatus(data);
                    document.getElementById('logs').textContent = '';
                });
            }
        }
        
        function showStatus(data) {
            const statusDiv = document.getElementById('status');
            let className = 'success';
            let message = '';
            
            if (data.error) {
                className = 'error';
                message = `❌ Ошибка: ${data.error}`;
            } else if (data.info) {
                className = 'success';
                message = `ℹ️ ${data.info}`;
            } else {
                message = `✅ ${JSON.stringify(data)}`;
            }
            
            statusDiv.innerHTML = `<div class="status ${className}">${message}</div>`;
            setTimeout(() => statusDiv.innerHTML = '', 5000);
        }
        
        loadProjects();
        setInterval(loadProjects, 30000);
        
        document.getElementById('branch').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') deployProject();
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/deploy', methods=['POST'])
def api_deploy():
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
            log_action(f"WEB: Клонирование проекта: {project_name}")
            os.makedirs(project_path, exist_ok=True)
            download_repo_from_github(repo_url, branch, project_path)
            action = "задеплоен"
        
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            log_action(f"Установка зависимостей для {project_name}")
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
            "message": f"Проект {project_name} успешно {action} через HTTP API!"
        })
    
    except Exception as e:
        log_action(f"ОШИБКА WEB деплоя: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/projects')
def api_projects():
    config = load_config()
    return jsonify(config.get('projects', {}))

@app.route('/api/update/<name>', methods=['POST'])
def api_update(name):
    try:
        config = load_config()
        
        if name not in config['projects']:
            return jsonify({"error": "Проект не найден"}), 404
        
        project = config['projects'][name]
        project_path = project['path']
        repo_url = project['repo_url']
        branch = project['branch']
        
        log_action(f"WEB: Обновление проекта: {name}")
        
        download_repo_from_github(repo_url, branch, project_path)
        
        req_file = os.path.join(project_path, 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.run(['pip', 'install', '-r', req_file])
        
        config['projects'][name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
        
        return jsonify({
            "status": "success",
            "project": name,
            "message": f"Проект {name} обновлен через HTTP API!"
        })
    
    except Exception as e:
        log_action(f"ОШИБКА WEB обновления {name}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/project/<name>/info')
def api_project_info(name):
    config = load_config()
    if name in config['projects']:
        return jsonify(config['projects'][name])
    return jsonify({"error": "Проект не найден"}), 404

@app.route('/api/project/<name>', methods=['DELETE'])
def api_delete_project(name):
    try:
        config = load_config()
        
        if name not in config['projects']:
            return jsonify({"error": "Проект не найден"}), 404
        
        project_path = config['projects'][name]['path']
        
        log_action(f"WEB: Удаление проекта: {name}")
        
        if os.path.exists(project_path):
            shutil.rmtree(project_path)
        
        del config['projects'][name]
        save_config(config)
        
        return jsonify({
            "status": "success",
            "project": name,
            "message": f"Проект {name} удален!"
        })
    
    except Exception as e:
        log_action(f"ОШИБКА WEB удаления {name}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/logs')
def api_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    return "Логи пусты"

@app.route('/api/logs/clear', methods=['POST'])
def api_clear_logs():
    try:
        open(LOG_FILE, 'w').close()
        return jsonify({"status": "success", "message": "Логи очищены"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        repo_url = data.get('repository', {}).get('clone_url')
        
        config = load_config()
        for name, project in config['projects'].items():
            if project['repo_url'] == repo_url:
                log_action(f"GitHub Webhook: обновление {name}")
                download_repo_from_github(repo_url, project['branch'], project['path'])
                
                config['projects'][name]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_config(config)
                
                return jsonify({"status": "updated", "project": name})
        
        return jsonify({"status": "no matching project"}), 404
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# === ЗАПУСК ПРИЛОЖЕНИЯ (ИСПРАВЛЕННЫЙ) ===

def run_flask():
    """Запуск Flask сервера в отдельном потоке"""
    log_action("🌐 Flask сервер запущен на server.bothost.py:8080")
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

async def main():
    """Основная функция - запускает и Flask и Telegram Bot"""
    log_action("🚀 Deploy Manager Pro BotHost v3.0 запущен")
    
    # Запускаем Flask сервер в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Ждём немного чтобы Flask запустился
    await asyncio.sleep(2)
    
    # Запускаем Telegram бота в основном потоке
    log_action("🤖 Telegram Bot запущен (main thread)")
    await dp.start_polling(bot)

if __name__ == '__main__':
    # Запускаем всё через asyncio.run()
    asyncio.run(main())
