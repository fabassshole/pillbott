import asyncio
import sqlite3
import logging
import pytz
import re
import os
import dotenv
print("Успешно!")


API_TOKEN = os.getenv('BOT_TOKEN', 'не_нашел_токен') # берем из системы или пишем текст ошибки
print(f"DEBUG: Текущий токен начинается на: {API_TOKEN[:5]}...") # выведет первые 5 символов в логи

bot = Bot(token=API_TOKEN)
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ---

# Бот будет брать токен из настроек сервера
API_TOKEN = os.getenv('BOT_TOKEN')
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

WEEKDAYS = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}

# --- СОСТОЯНИЯ (FSM) ---
class AddMedicine(StatesGroup):
    waiting_for_name = State()
    waiting_for_count = State()
    waiting_for_days = State()
    waiting_for_times = State()
    
class EditMedicine(StatesGroup):
    waiting_for_new_count = State()
    waiting_for_new_times = State()  

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS medicines
        (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, count INTEGER, times TEXT, days TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS history
        (user_id INTEGER, name TEXT, timestamp TEXT, status TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings
                      (user_id INTEGER PRIMARY KEY, timezone TEXT)''')
    conn.commit()
    conn.close()
    print("✅ 1. База данных проверена и таблицы созданы.")

# --- КЛАВИАТУРЫ ---
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Добавить")
    builder.button(text="📋 Моя аптечка")
    builder.button(text="📊 Статистика")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)

# --- ЛОГИКА НАПОМИНАНИЙ ---
async def send_reminder(user_id, name, pill_id):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="✅ Приняла", callback_data=f"taken_{pill_id}"))
    builder.row(
        types.InlineKeyboardButton(text="⏳ Через 30 мин", callback_data=f"delay_{pill_id}"),
        types.InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip_{pill_id}")
    )
    try:
        await bot.send_message(user_id, f"🔔 ПОРА ПИТЬ: {name}!", reply_markup=builder.as_markup())
    except Exception as e:
        print(f"⚠️ Ошибка отправки пользователю {user_id}: {e}")

async def check_reminders():
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, name, id, times, days FROM medicines')
    all_meds = cursor.fetchall()
    cursor.execute('SELECT user_id, timezone FROM user_settings')
    user_tzs = dict(cursor.fetchall())
    conn.close()

    for user_id, name, pill_id, times, days in all_meds:
        user_tz_name = user_tzs.get(user_id, "UTC")
        user_tz = pytz.timezone(user_tz_name)
        now = datetime.now(user_tz)
        current_time = now.strftime("%H:%M")
        current_day_name = WEEKDAYS[now.weekday()]
        
        days_list = [d.strip().lower() for d in days.split(",")]
        if "каждый день" in days_list or current_day_name in days_list:
            if current_time in [t.strip() for t in times.split(",")]:
                await send_reminder(user_id, name, pill_id)

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("**Привет! Я твой помощник по приему лекарств. 💊**", parse_mode="Markdown", reply_markup=main_menu())

@dp.message(F.text == "❌ Отмена")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())

@dp.message(Command("timezone"))
async def set_timezone_start(message: types.Message):
    await message.answer("Напишите ваш город (напр: `Europe/Moscow`):", parse_mode="Markdown")

@dp.message(lambda message: "/" in message.text and len(message.text) > 5)
async def save_timezone(message: types.Message):
    tz_input = message.text.strip()
    try:
        pytz.timezone(tz_input)
        conn = sqlite3.connect('pills.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO user_settings VALUES (?, ?)', (message.from_user.id, tz_input))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Часовой пояс установлен: {tz_input}")
    except Exception:
        await message.answer("❌ Ошибка. Используйте формат Europe/Moscow")

# --- ПРОЦЕСС ДОБАВЛЕНИЯ ---

@dp.message(F.text == "➕ Добавить")
async def start_add(message: types.Message, state: FSMContext):
    await message.answer("Название лекарства:", reply_markup=cancel_keyboard())
    await state.set_state(AddMedicine.waiting_for_name)

@dp.message(AddMedicine.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"Сколько таблеток '{message.text}' в упаковке?", reply_markup=cancel_keyboard())
    await state.set_state(AddMedicine.waiting_for_count)

@dp.message(AddMedicine.waiting_for_count)
async def process_count(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠ Введите число цифрами!")
    await state.update_data(count=int(message.text))
    await message.answer("Дни приема (пн, вт... или каждый день):", reply_markup=cancel_keyboard())
    await state.set_state(AddMedicine.waiting_for_days)

@dp.message(AddMedicine.waiting_for_days)
async def process_days(message: types.Message, state: FSMContext):
    valid_days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс", "каждый день"]
    user_input = message.text.strip().lower()
    if not any(day in user_input for day in valid_days):
        return await message.answer("⚠ Укажите корректные дни (пн, вт...)")
    await state.update_data(days=user_input)
    await message.answer("Время приема (напр: 08:00):", reply_markup=cancel_keyboard())
    await state.set_state(AddMedicine.waiting_for_times)

@dp.message(AddMedicine.waiting_for_times)
async def process_times(message: types.Message, state: FSMContext):
    times = message.text.replace(".", ":").strip()
    if not re.search(r'\d{1,2}:\d{2}', times):
        return await message.answer("⚠ Формат времени должен быть ЧЧ:ММ")
    
    data = await state.get_data()
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO medicines (user_id, name, count, days, times) VALUES (?, ?, ?, ?, ?)',
                   (message.from_user.id, data['name'], data['count'], data['days'], times))
    conn.commit()
    conn.close()
    await message.answer("✅ Лекарство добавлено!", reply_markup=main_menu())
    await state.clear()

# --- КНОПКИ МЕНЮ ---

@dp.message(F.text == "📋 Моя аптечка")
async def show_pills(message: types.Message):
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, count, days, times FROM medicines WHERE user_id = ?', (message.from_user.id,))
    pills = cursor.fetchall()
    conn.close()
    if not pills:
        return await message.answer("💊 Аптечка пуста.")
    
    for pill_id, name, count, days, times in pills:
        builder = InlineKeyboardBuilder()
        # Новые кнопки управления
        builder.row(
            types.InlineKeyboardButton(text="➕ Пополнить", callback_data=f"refill_{pill_id}"),
            types.InlineKeyboardButton(text="✏️ Время", callback_data=f"edittime_{pill_id}")
        )
        builder.row(types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{pill_id}"))
       
        text = f"📌 **{name}**\n🔹 Остаток: {count}\n🗓 Дни: {days}\n⏰ Время: {times}"
        await message.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("delete_"))
async def delete_pill(callback: types.CallbackQuery):
    pill_id = callback.data.split("_")[1]
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM medicines WHERE id = ?', (pill_id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text("🗑 Удалено.")

@dp.callback_query(F.data.startswith("taken_"))
async def pill_taken(callback: types.CallbackQuery):
    pill_id = callback.data.split("_")[1]
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, count FROM medicines WHERE id = ?', (pill_id,))
    res = cursor.fetchone()
    if res:
        name, count = res
        new_count = max(0, count - 1)
        cursor.execute('UPDATE medicines SET count = ? WHERE id = ?', (new_count, pill_id))
        cursor.execute('INSERT INTO history (user_id, name, timestamp, status) VALUES (?, ?, ?, ?)', 
                       (callback.from_user.id, name, datetime.now().strftime("%Y-%m-%d %H:%M"), "Принято"))
        conn.commit()
        await callback.message.edit_text(f"✅ Принято: {name}. Осталось: {new_count}")
        if new_count <= 5 and new_count > 0:
            await callback.message.answer(f"⚠️ Мало лекарства {name}!")
    conn.close()

# 1. Показываем меню с кнопками причин
@dp.callback_query(F.data.startswith("skip_"))
async def skip_reason_menu(callback: types.CallbackQuery):
    pill_id = callback.data.split("_")[1]
    builder = InlineKeyboardBuilder()
    reasons = ["Нет с собой", "Забыла", "Другое"]
    
    for r in reasons:
        # Важно: передаем ID таблетки и саму причину в callback_data
        builder.row(types.InlineKeyboardButton(text=r, callback_data=f"reason_{pill_id}_{r}"))
    
    await callback.message.edit_text("Выберите причину пропуска:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("reason_"))
async def save_skip(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    pill_id = parts[1]
    reason = parts[2]
    
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    
    # Узнаем название лекарства
    cursor.execute('SELECT name FROM medicines WHERE id = ?', (pill_id,))
    res = cursor.fetchone()
    
    if res:
        name = res[0]
        # Записываем с полной датой для еженедельного отчета
        current_dt = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        cursor.execute('INSERT INTO history (user_id, name, timestamp, status) VALUES (?, ?, ?, ?)',
                       (callback.from_user.id, name, current_dt, f"Пропуск: {reason}"))
        conn.commit()
        await callback.message.edit_text(f"❌ Пропуск **{name}** отмечен. Причина: {reason}", parse_mode="Markdown")
    
    conn.close()

@dp.callback_query(F.data.startswith("skip_"))
async def skip_reason_menu(callback: types.CallbackQuery):
    pill_id = callback.data.split("_")[1]
    builder = InlineKeyboardBuilder()
    reasons = ["Нет с собой", "Забыла", "Другое"]
    for r in reasons:
        builder.row(types.InlineKeyboardButton(text=r, callback_data=f"reason_{pill_id}_{r}"))
    
    await callback.message.edit_text("Выберите причину пропуска:", reply_markup=builder.as_markup())

# 2. Сохраняем выбранную причину в историю
@dp.callback_query(F.data.startswith("reason_"))
async def save_skip(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    pill_id = parts[1]
    reason = parts[2]
    
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    
    # Узнаем название лекарства
    cursor.execute('SELECT name FROM medicines WHERE id = ?', (pill_id,))
    res = cursor.fetchone()
    
    if res:
        name = res[0]
        # Используем полную дату для корректных еженедельных отчетов
        current_dt = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        cursor.execute('INSERT INTO history (user_id, name, timestamp, status) VALUES (?, ?, ?, ?)',
                       (callback.from_user.id, name, current_dt, f"Пропуск: {reason}"))
        conn.commit()
        await callback.message.edit_text(f"❌ Пропуск **{name}** отмечен. Причина: {reason}", parse_mode="Markdown")
    
    conn.close()

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('SELECT timestamp, name, status FROM history WHERE user_id = ? ORDER BY rowid DESC LIMIT 5', (message.from_user.id,))
    rows = cursor.fetchall()
    conn.close()
    if not rows: return await message.answer("История пуста.")
    res = "📊 **Последние действия:**\n\n" + "\n".join([f"• {t} — {n}: {s}" for t, n, s in rows])
    await message.answer(res, parse_mode="Markdown")

# --- ПОПОЛНЕНИЕ ОСТАТКА ---
@dp.callback_query(F.data.startswith("refill_"))
async def refill_start(callback: types.CallbackQuery, state: FSMContext):
    pill_id = callback.data.split("_")[1]
    await state.update_data(edit_pill_id=pill_id)
    await callback.message.answer("Сколько таблеток добавить в аптечку?", reply_markup=cancel_keyboard())
    await state.set_state(EditMedicine.waiting_for_new_count)

@dp.message(EditMedicine.waiting_for_new_count)
async def refill_finish(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠ Введите число цифрами!")
    
    data = await state.get_data()
    pill_id = data['edit_pill_id']
    add_count = int(message.text)
    
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE medicines SET count = count + ? WHERE id = ?', (add_count, pill_id))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Аптечка пополнена на {add_count} шт.", reply_markup=main_menu())
    await state.clear()

# --- РЕДАКТИРОВАНИЕ ВРЕМЕНИ ---
@dp.callback_query(F.data.startswith("edittime_"))
async def edit_time_start(callback: types.CallbackQuery, state: FSMContext):
    pill_id = callback.data.split("_")[1]
    await state.update_data(edit_pill_id=pill_id)
    await callback.message.answer("Введите новое время (напр: `09:00, 21:00`):", parse_mode="Markdown", reply_markup=cancel_keyboard())
    await state.set_state(EditMedicine.waiting_for_new_times)

@dp.message(EditMedicine.waiting_for_new_times)
async def edit_time_finish(message: types.Message, state: FSMContext):
    times = message.text.replace(".", ":").strip()
    if not re.search(r'\d{1,2}:\d{2}', times):
        return await message.answer("⚠ Формат должен быть ЧЧ:ММ")
    
    data = await state.get_data()
    pill_id = data['edit_pill_id']
    
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE medicines SET times = ? WHERE id = ?', (times, pill_id))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Новое время установлено: {times}", reply_markup=main_menu())
    await state.clear()

async def send_weekly_report():
    conn = sqlite3.connect('pills.db')
    cursor = conn.cursor()
    
    # Берем данные за последние 7 дней
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    # Получаем список всех пользователей, у которых есть история
    cursor.execute('SELECT DISTINCT user_id FROM history')
    users = cursor.fetchall()
    
    for (user_id,) in users:
        # Считаем принятые
        cursor.execute('SELECT COUNT(*) FROM history WHERE user_id = ? AND status = "Принято"', (user_id,))
        taken_count = cursor.fetchone()[0]
        
        # Считаем пропуски
        cursor.execute('SELECT COUNT(*) FROM history WHERE user_id = ? AND status LIKE "Пропуск%"', (user_id,))
        skipped_count = cursor.fetchone()[0]
        
        total = taken_count + skipped_count
        if total > 0:
            percent = round((taken_count / total) * 100)
            
            report = (
                f"📊 **Твой еженедельный отчет**\n\n"
                f"✅ Принято: {taken_count} раз\n"
                f"❌ Пропущено: {skipped_count} раз\n"
                f"📈 Дисциплина: {percent}%\n\n"
            )
            
            if percent >= 90:
                report += "Идеально! Ты настоящий герой дисциплины! 🏆"
            elif percent >= 70:
                report += "Хороший результат, но старайся не забывать! 💪"
            else:
                report += "Нужно подтянуться. Твое здоровье — в твоих руках! ❤️"
            
            try:
                await bot.send_message(user_id, report, parse_mode="Markdown")
            except Exception as e:
                print(f"Не удалось отправить отчет {user_id}: {e}")
                
    conn.close()

# --- ЗАПУСК С МАЯЧКАМИ ---
async def main():
    print("\n" + "="*40)
    print("🚀 ИНИЦИАЛИЗАЦИЯ БОТА...")
    print("="*40)
    
    try:
        init_db()
        
        scheduler.add_job(check_reminders, "interval", minutes=1)
        scheduler.start()
        print("✅ 2. Планировщик запущен (каждую 1 мин).")
        
        bot_info = await bot.get_me()
        print(f"✅ 3. Связь с Telegram установлена!")
        print(f"🤖 Бот активен как: @{bot_info.username}")
        print("="*40)
        print("📡 ОЖИДАНИЕ СООБЩЕНИЙ...")
        
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        print(f"❌ ОШИБКА ЗАПУСКА: {e}")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот выключен пользователем.")
async def main():
    print("\n" + "="*40)
    print("🚀 ИНИЦИАЛИЗАЦИЯ БОТА...")
    print("="*40)
    
    try:
        init_db()
        
        # Проверка напоминаний каждую минуту
        scheduler.add_job(check_reminders, "interval", minutes=1)
        
        # НОВАЯ ЗАДАЧА: Еженедельный отчет в воскресенье в 21:00
        scheduler.add_job(send_weekly_report, "cron", day_of_week='sun', hour=21, minute=0)
        
        scheduler.start()
        print("✅ 2. Планировщик запущен (Напоминания + Отчеты).")
        
        bot_info = await bot.get_me()
        print(f"✅ 3. Связь с Telegram установлена!")
        print(f"🤖 Бот активен как: @{bot_info.username}")
        print("="*40)
        
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        print(f"❌ ОШИБКА ЗАПУСКА: {e}")


        





