import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Tuple, Optional

import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from hijri_converter import Gregorian

# -------------------- SOZLAMALAR (MUHIT O'ZGARUVCHILARI) --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # BotFather dan olingan token
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")  # ixtiyoriy
TASHKENT_TIMEZONE = pytz.timezone("Asia/Tashkent")

# -------------------- YORDAMCHI FUNKSIYALAR --------------------

def get_current_time() -> datetime:
    """Toshkent vaqtidagi hozirgi vaqtni qaytaradi."""
    return datetime.now(TASHKENT_TIMEZONE)

def get_namaz_times(date: datetime) -> dict:
    """
    Toshkent uchun namoz vaqtlarini qaytaradi.
    Haqiqiy loyihada API (masalan, Aladhan) dan olinadi.
    Bu yerda misol sifatida soxta ma'lumot (o'zgartirish mumkin).
    """
    # SOXTA MA'LUMOTLAR – REAL API BILAN ALMASHTIRISH TAVSIYA ETILADI
    return {
        "fajr": "05:23",
        "sunrise": "06:55",
        "dhuhr": "12:30",
        "asr": "15:45",
        "maghrib": "18:15",
        "isha": "19:40"
    }

def get_next_namaz(current_time: datetime, namaz_times: dict) -> Tuple[str, datetime]:
    """
    Hozirgi vaqtdan keyingi namoz nomi va vaqtini qaytaradi.
    Vaqtlarni datetime obyektiga aylantirib solishtiradi.
    """
    # Vaqtni bugungi sanaga moslab datetime yaratamiz
    today = current_time.date()
    time_format = "%H:%M"

    # Namoz vaqtlarini datetime obyektiga o'tkazamiz
    namaz_datetimes = {}
    for name, time_str in namaz_times.items():
        dt = datetime.strptime(time_str, time_format)
        namaz_datetimes[name] = datetime.combine(today, dt.time(), tzinfo=TASHKENT_TIMEZONE)

    # Hozirgi vaqtni timezone-aware qilamiz
    current_dt = current_time

    # Namozlarni belgilangan tartibda tekshiramiz
    order = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"]
    for name in order:
        if namaz_datetimes[name] > current_dt:
            return name, namaz_datetimes[name]

    # Agar bugungi namozlar tugagan bo'lsa, ertangi bomdodni olamiz
    tomorrow = today + timedelta(days=1)
    fajr_tomorrow = datetime.combine(tomorrow, namaz_datetimes["fajr"].time(), tzinfo=TASHKENT_TIMEZONE)
    return "fajr", fajr_tomorrow

def get_weather(city: str = "Tashkent") -> Optional[str]:
    """
    OpenWeatherMap orqali ob-havo ma'lumotini oladi.
    Agar API kaliti bo'lmasa, soxta ma'lumot qaytaradi.
    """
    if not OPENWEATHER_API_KEY:
        return "Toshkentda ertaga ertalab 5° sovuq"  # soxta ma'lumot

    import requests
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric&lang=uz"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        if resp.status_code == 200:
            temp = data['main']['temp']
            desc = data['weather'][0]['description']
            return f"{city}da {temp:.1f}°C, {desc}"
        else:
            return "Ob-havo ma'lumoti olinmadi"
    except Exception:
        return "Ob-havo xizmati vaqtincha ishlamayapti"

def get_daily_ayah() -> str:
    """Bugungi oyat nomeri va ma'nosini qaytaradi."""
    # REAL API UCHUN – https://alquran.cloud/api
    return "Baqara 286-oyat: \"Allah hech kimga toqatidan tashqari yuklamaydi...\""

def get_charity_channel() -> str:
    """Sadaqa berish uchun kanal linki."""
    return "@mehribonlik_kanali"

def get_iftar_recipe() -> str:
    """Bugungi iftorlik taomi retsepti kanali."""
    return "@oshxona_kanalida sho'rva retsepti"

def is_ramadan(date: datetime) -> bool:
    """
    Berilgan sana Ramazon oyiga to'g'ri keladimi?
    Hijriy sanani hisoblab tekshiradi.
    """
    hijri = Gregorian(date.year, date.month, date.day).to_hijri()
    return hijri.month == 9

def get_ramadan_times(date: datetime) -> dict:
    """
    Ramazondagi saharlik (og'iz yopish) va iftorlik vaqtlarini qaytaradi.
    Saharlik — bomdod vaqti, iftorlik — shom vaqti.
    """
    namaz_times = get_namaz_times(date)
    return {
        "imsak": namaz_times["fajr"],
        "iftar": namaz_times["maghrib"]
    }

def format_time_left(seconds: int) -> str:
    """Sekundlarni 'soat daqiqa' formatiga o'tkazadi."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours} soat {minutes} daqiqa"
    return f"{minutes} daqiqa"

# -------------------- BOT LOGIKASI --------------------

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Assalomu alaykum! Diniy botga xush kelibsiz.\n\n"
        "Mavjud buyruqlar:\n"
        "/namoz - Namoz vaqtlari va qo'shimcha ma'lumotlar\n"
        "/roza - Ro'za tutish haqida ma'lumot (Ramazonda ishlaydi)"
    )

@dp.message(Command("namoz"))
async def cmd_namaz(message: Message):
    now = get_current_time()
    namaz_times = get_namaz_times(now)

    next_name, next_dt = get_next_namaz(now, namaz_times)
    time_left_seconds = int((next_dt - now).total_seconds())
    time_left_str = format_time_left(time_left_seconds)

    # Namoz nomini o'zbekchalashtirish
    namaz_names = {
        "fajr": "Bomdod",
        "sunrise": "Quyosh chiqishi",
        "dhuhr": "Peshin",
        "asr": "Asr",
        "maghrib": "Shom",
        "isha": "Xufton"
    }
    next_name_uz = namaz_names.get(next_name, next_name.capitalize())

    weather = get_weather()
    ayah = get_daily_ayah()
    charity = get_charity_channel()

    response = (
        f"{next_name_uz}ga {time_left_str} qoldi.\n"
        f"Toshkentda {weather}, issiq kiyining.\n\n"
        f"Bugungi oyat: {ayah}\n"
        f"Sadaqa berish: {charity}"
    )
    await message.answer(response)

@dp.message(Command("roza"))
async def cmd_roza(message: Message):
    now = get_current_time()

    if not is_ramadan(now):
        await message.answer("Hozir Ramazon oyi emas. Bot faqat Ramazonda ishlaydi.")
        return

    times = get_ramadan_times(now)
    imsak_str = times["imsak"]
    iftar_str = times["iftar"]

    # Vaqtlarni datetime obyektiga aylantirish
    today = now.date()
    imsak_dt = datetime.combine(today, datetime.strptime(imsak_str, "%H:%M").time(), tzinfo=TASHKENT_TIMEZONE)
    iftar_dt = datetime.combine(today, datetime.strptime(iftar_str, "%H:%M").time(), tzinfo=TASHKENT_TIMEZONE)

    # Agar hozir bomdoddan oldin bo'lsa – saharlikkacha vaqt
    if now < imsak_dt:
        target = imsak_dt
        event = "Saharlikka"
    # Agar hozir bomdod va iftor oralig'ida bo'lsa – iftorgacha vaqt
    elif now < iftar_dt:
        target = iftar_dt
        event = "Iftorga"
    else:
        # Iftor bo'lgan – ertangi saharlik
        tomorrow = today + timedelta(days=1)
        target = datetime.combine(tomorrow, imsak_dt.time(), tzinfo=TASHKENT_TIMEZONE)
        event = "Ertangi saharlikka"

    time_left_seconds = int((target - now).total_seconds())
    time_left_str = format_time_left(time_left_seconds)

    # Ro'za davomiyligi
    fast_duration = iftar_dt - imsak_dt
    fast_hours = fast_duration.seconds // 3600
    fast_minutes = (fast_duration.seconds % 3600) // 60
    fast_duration_str = f"{fast_hours} soat {fast_minutes} daqiqa"

    recipe = get_iftar_recipe()

    response = (
        f"{event} {time_left_str} qoldi.\n"
        f"Og'iz yopish: {imsak_str}\n"
        f"Iftorlik: {iftar_str} ({fast_duration_str})\n\n"
        f"Bugungi iftorlik taom: {recipe}"
    )
    await message.answer(response)

async def main():
    logging.basicConfig(level=logging.INFO)
    # Bot ishga tushganini bildirish
    await bot.send_message(chat_id=123456789, text="Bot ishga tushdi!")  # O'z ID'ingizni qo'ying (ixtiyoriy)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
