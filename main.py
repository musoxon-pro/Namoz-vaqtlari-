import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict
import re

import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from hijri_converter import Gregorian

# -------------------- SOZLAMALAR --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
GEOCODE_API_KEY = os.getenv("GEOCODE_API_KEY", "")  # OpenCage yoki boshqa geocoding API kaliti

# Foydalanuvchi sozlamalari uchun lug'at (oddiy saqlash)
# Haqiqiy loyihada ma'lumotlar bazasi ishlatish tavsiya etiladi
user_settings: Dict[int, Dict] = {}

# -------------------- HOLATLAR --------------------
class LocationStates(StatesGroup):
    waiting_for_location = State()
    waiting_for_city_name = State()

# -------------------- YORDAMCHI FUNKSIYALAR --------------------

def get_timezone_by_coordinates(lat: float, lng: float) -> str:
    """
    Koordinatalar bo'yicha vaqt mintaqasini aniqlaydi.
    TimezoneDB yoki geonames API dan foydalanish mumkin.
    Agar aniqlanmasa, UTC qaytariladi.
    """
    try:
        import requests
        # TimezoneDB API (bepul kalit olish kerak)
        # url = f"http://api.timezonedb.com/v2.1/get-time-zone?key={TIMEZONE_API_KEY}&format=json&by=position&lat={lat}&lng={lng}"
        # resp = requests.get(url).json()
        # return resp.get('zoneName', 'UTC')
        
        # Agar API bo'lmasa, taxminiy vaqt mintaqasi
        # Bu oddiy hisoblash - haqiqiy loyihada API ishlating
        offset = round(lng / 15)  # Har 15° uzunlik = 1 soat farq
        if offset > 12:
            offset = 12
        elif offset < -12:
            offset = -12
        
        timezone_str = f"Etc/GMT{'+' if offset <= 0 else '-'}{abs(offset)}"
        return timezone_str
    except:
        return "UTC"

def get_city_from_coordinates(lat: float, lng: float) -> Optional[str]:
    """
    Koordinatalar bo'yicha shahar nomini aniqlaydi (Reverse Geocoding).
    """
    if not GEOCODE_API_KEY:
        return None
    
    try:
        import requests
        # OpenCage Geocoder API
        url = f"https://api.opencagedata.com/geocode/v1/json?q={lat}+{lng}&key={GEOCODE_API_KEY}&language=uz"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        
        if data['results']:
            components = data['results'][0]['components']
            city = (components.get('city') or 
                   components.get('town') or 
                   components.get('village') or 
                   components.get('state') or 
                   "Noma'lum shahar")
            country = components.get('country', '')
            return f"{city}, {country}"
    except:
        pass
    return None

def get_coordinates_from_city(city_name: str) -> Optional[Tuple[float, float, str]]:
    """
    Shahar nomi bo'yicha koordinatalarni aniqlaydi (Geocoding).
    Qaytaradi: (latitude, longitude, to'liq manzil)
    """
    if not GEOCODE_API_KEY:
        return None
    
    try:
        import requests
        url = f"https://api.opencagedata.com/geocode/v1/json?q={city_name}&key={GEOCODE_API_KEY}&limit=1&language=uz"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        
        if data['results']:
            result = data['results'][0]
            lat = result['geometry']['lat']
            lng = result['geometry']['lng']
            formatted = result['formatted']
            return (lat, lng, formatted)
    except Exception as e:
        print(f"Geocoding xatosi: {e}")
    return None

def get_namaz_times(date: datetime, lat: float, lng: float, method: int = 2) -> Optional[dict]:
    """
    Butun dunyo uchun namoz vaqtlarini qaytaradi.
    Aladhan API dan foydalanadi.
    method: 2 - ISNA (Shimoliy Amerika), 3 - Musulmonlar Ligasi, 5 - Jafari, 8 - Qatar, 12 - Umm Al-Qura
    """
    try:
        import requests
        date_str = date.strftime('%d-%m-%Y')
        url = f"http://api.aladhan.com/v1/timings/{date_str}?latitude={lat}&longitude={lng}&method={method}"
        
        resp = requests.get(url, timeout=10)
        data = resp.json()
        
        if data['code'] == 200:
            timings = data['data']['timings']
            return {
                "fajr": timings['Fajr'][:5],  "sunrise": timings['Sunrise'][:5],
                "dhuhr": timings['Dhuhr'][:5], "asr": timings['Asr'][:5],
                "maghrib": timings['Maghrib'][:5], "isha": timings['Isha'][:5]
            }
    except Exception as e:
        print(f"Namoz vaqtlari API xatosi: {e}")
    return None

def get_next_namaz(current_time: datetime, namaz_times: dict, timezone: pytz.timezone) -> Tuple[str, datetime]:
    """
    Hozirgi vaqtdan keyingi namoz nomi va vaqtini qaytaradi.
    """
    today = current_time.date()
    
    # Namoz vaqtlarini datetime obyektiga o'tkazamiz
    namaz_datetimes = {}
    for name, time_str in namaz_times.items():
        hour, minute = map(int, time_str.split(':'))
        namaz_datetimes[name] = timezone.localize(datetime(today.year, today.month, today.day, hour, minute))
    
    # Namozlarni tartibda tekshiramiz
    order = ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"]
    for name in order:
        if namaz_datetimes[name] > current_time:
            return name, namaz_datetimes[name]
    
    # Agar bugungi namozlar tugagan bo'lsa, ertangi bomdod
    tomorrow = today + timedelta(days=1)
    fajr_tomorrow = timezone.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, 
                                               namaz_datetimes["fajr"].hour, 
                                               namaz_datetimes["fajr"].minute))
    return "fajr", fajr_tomorrow

def get_weather(lat: float, lng: float, city_name: str = None) -> str:
    """
    OpenWeatherMap orqali ob-havo ma'lumotini oladi.
    """
    if not OPENWEATHER_API_KEY:
        return f"{city_name or 'Shahringiz'}da ob-havo ma'lumoti olinmadi"
    
    try:
        import requests
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lng}&appid={OPENWEATHER_API_KEY}&units=metric&lang=uz"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        
        if resp.status_code == 200:
            temp = data['main']['temp']
            feels_like = data['main']['feels_like']
            description = data['weather'][0]['description']
            humidity = data['main']['humidity']
            wind = data['wind']['speed']
            
            location = city_name or data.get('name', 'Shahringiz')
            return (f"{location}da {temp:.1f}°C ({feels_like:.1f}°C hissiyot), {description}\n"
                   f"💧 Namlik: {humidity}% | 💨 Shamol: {wind} m/s")
        else:
            return f"{city_name or 'Shahringiz'}da ob-havo ma'lumoti olinmadi"
    except Exception as e:
        print(f"Ob-havo xatosi: {e}")
        return "Ob-havo xizmati vaqtincha ishlamayapti"

def get_daily_ayah() -> str:
    """Bugungi oyat - Qur'on API dan olinadi."""
    try:
        import requests
        # Alquran.cloud API - random oyat
        url = "https://api.alquran.cloud/v1/ayah/random/editions/uz.sodik,en.sahih"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        
        if data['code'] == 200:
            ayah_data = data['data'][0]  # O'zbekcha tarjima
            surah = ayah_data['surah']['englishName']
            ayah_num = ayah_data['numberInSurah']
            text = ayah_data['text']
            
            # Inglizcha tarjima
            en_data = data['data'][1]
            en_text = en_data['text']
            
            return f"{surah} {ayah_num}-oyat:\n\"{text}\"\n\n{en_text}"
    except:
        pass
    
    # Agar API ishlamasa, soxta ma'lumot
    return "Baqara 286-oyat: \"Allah hech kimga toqatidan tashqari yuklamaydi...\""

def get_charity_channels() -> list:
    """Turli mamlakatlar uchun sadaqa kanallari."""
    return [
        "@mehribonlik_kanali",  # O'zbekiston
        "@helping_hands",        # Xalqaro
        "@zakat_foundation"      # Umumiy
    ]

def get_iftar_recipes() -> dict:
    """Turli mamlakatlar oshxonalaridan iftorlik retseptlari."""
    return {
        "uz": "@oshxona_kanalida sho'rva retsepti",
        "turkey": "@turkish_kitchen da iftar menyusi",
        "arab": "@arabic_foods da machbous retsepti",
        "international": "@ramadan_recipes da turli taomlar"
    }

def is_ramadan(date: datetime) -> bool:
    """
    Berilgan sana Ramazon oyiga to'g'ri keladimi?
    """
    hijri = Gregorian(date.year, date.month, date.day).to_hijri()
    return hijri.month == 9

def get_ramadan_times(date: datetime, lat: float, lng: float, method: int = 2) -> Optional[dict]:
    """
    Ramazon vaqtlarini qaytaradi.
    """
    namaz_times = get_namaz_times(date, lat, lng, method)
    if not namaz_times:
        return None
    
    return {
        "imsak": namaz_times["fajr"],      # saharlik (og'iz yopish)
        "iftar": namaz_times["maghrib"]     # iftorlik
    }

def format_time_left(seconds: int) -> str:
    """Sekundlarni formatlaydi."""
    if seconds < 0:
        return "0 daqiqa"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if hours > 24:
        days = hours // 24
        hours = hours % 24
        return f"{days} kun {hours} soat {minutes} daqiqa"
    elif hours > 0:
        return f"{hours} soat {minutes} daqiqa"
    else:
        return f"{minutes} daqiqa"

# -------------------- BOT INTERFEYSI --------------------

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Klaviaturalar
def get_location_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Joylashuvimni yuborish", request_location=True)],
            [KeyboardButton(text="🏙 Shahar nomini kiritish")]
        ],
        resize_keyboard=True
    )
    return keyboard

# -------------------- BOT KOMANDALARI --------------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Foydalanuvchini sozlashlarini tekshirish
    if user_id not in user_settings:
        await message.answer(
            "Assalomu alaykum! Diniy botga xush kelibsiz.\n\n"
            "Bot butun dunyo bo'ylab ishlaydi. Iltimos, joylashuvingizni yuboring yoki shahar nomini kiriting.",
            reply_markup=get_location_keyboard()
        )
        await LocationStates.waiting_for_location.set()
    else:
        await message.answer(
            "Assalomu alaykum! Diniy botga xush kelibsiz.\n\n"
            "Mavjud buyruqlar:\n"
            "/namoz - Namoz vaqtlari va qo'shimcha ma'lumotlar\n"
            "/roza - Ro'za tutish haqida ma'lumot (Ramazonda)\n"
            "/location - Joylashuvni o'zgartirish\n"
            "/help - Yordam"
        )

@dp.message(LocationStates.waiting_for_location)
async def handle_location_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if message.location:
        # Foydalanuvchi joylashuv yubordi
        lat = message.location.latitude
        lng = message.location.longitude
        
        # Shahar nomini aniqlash
        city_info = get_city_from_coordinates(lat, lng)
        if city_info:
            city_name = city_info
        else:
            city_name = f"Koordinatalar: {lat:.2f}, {lng:.2f}"
        
        # Vaqt mintaqasini aniqlash
        timezone_str = get_timezone_by_coordinates(lat, lng)
        try:
            timezone = pytz.timezone(timezone_str)
        except:
            timezone = pytz.UTC
        
        # Foydalanuvchi sozlamalarini saqlash
        user_settings[user_id] = {
            "lat": lat,
            "lng": lng,
            "city": city_name,
            "timezone": timezone,
            "method": 2  # Standart hisoblash usuli
        }
        
        await state.clear()
        await message.answer(
            f"✅ Joylashuvingiz saqlandi: {city_name}\n\n"
            f"Endi /namoz va /roza buyruqlaridan foydalanishingiz mumkin.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif message.text and message.text == "🏙 Shahar nomini kiritish":
        await message.answer(
            "Iltimos, shahar nomini kiriting (masalan: Toshkent, London, Makka):",
            reply_markup=ReplyKeyboardRemove()
        )
        await LocationStates.waiting_for_city_name.set()
    
    else:
        await message.answer(
            "Iltimos, joylashuvingizni yuborish uchun pastdagi tugmalardan foydalaning.",
            reply_markup=get_location_keyboard()
        )

@dp.message(LocationStates.waiting_for_city_name)
async def handle_city_name(message: Message, state: FSMContext):
    city_name = message.text.strip()
    
    # Shahar nomidan koordinatalarni aniqlash
    coords = get_coordinates_from_city(city_name)
    
    if coords:
        lat, lng, full_address = coords
        
        # Vaqt mintaqasini aniqlash
        timezone_str = get_timezone_by_coordinates(lat, lng)
        try:
            timezone = pytz.timezone(timezone_str)
        except:
            timezone = pytz.UTC
        
        # Foydalanuvchi sozlamalarini saqlash
        user_settings[message.from_user.id] = {
            "lat": lat,
            "lng": lng,
            "city": full_address,
            "timezone": timezone,
            "method": 2
        }
        
        await state.clear()
        await message.answer(
            f"✅ Shahar saqlandi: {full_address}\n\n"
            f"Endi /namoz va /roza buyruqlaridan foydalanishingiz mumkin.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await message.answer(
            "❌ Shahar topilmadi. Iltimos, qaytadan kiriting yoki joylashuvingizni yuboring:",
            reply_markup=get_location_keyboard()
        )

@dp.message(Command("location"))
async def cmd_change_location(message: Message, state: FSMContext):
    await message.answer(
        "Iltimos, yangi joylashuvingizni yuboring yoki shahar nomini kiriting:",
        reply_markup=get_location_keyboard()
    )
    await LocationStates.waiting_for_location.set()

@dp.message(Command("namoz"))
async def cmd_namaz(message: Message):
    user_id = message.from_user.id
    
    # Foydalanuvchi sozlamalarini tekshirish
    if user_id not in user_settings:
        await message.answer(
            "Iltimos, avval joylashuvingizni sozlang: /start",
            reply_markup=get_location_keyboard()
        )
        await LocationStates.waiting_for_location.set()
        return
    
    settings = user_settings[user_id]
    now = datetime.now(settings['timezone'])
    
    # Namoz vaqtlarini olish
    namaz_times = get_namaz_times(now, settings['lat'], settings['lng'], settings['method'])
    
    if not namaz_times:
        await message.answer("Namoz vaqtlarini olishda xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return
    
    # Keyingi namozni aniqlash
    next_name, next_dt = get_next_namaz(now, namaz_times, settings['timezone'])
    time_left_seconds = int((next_dt - now).total_seconds())
    time_left_str = format_time_left(time_left_seconds)
    
    # Namoz nomlarini tarjima qilish
    namaz_names = {
        "fajr": "Bomdod", "sunrise": "Quyosh chiqishi",
        "dhuhr": "Peshin", "asr": "Asr",
        "maghrib": "Shom", "isha": "Xufton"
    }
    next_name_uz = namaz_names.get(next_name, next_name.capitalize())
    
    # Ob-havo
    weather = get_weather(settings['lat'], settings['lng'], settings['city'])
    
    # Oyat
    ayah = get_daily_ayah()
    
    # Sadaqa kanali
    charities = get_charity_channels()
    charity = charities[0]  # Oddiylik uchun birinchisi
    
    response = (
        f"📍 {settings['city']}\n"
        f"🕌 {next_name_uz}ga {time_left_str} qoldi.\n\n"
        f"🌤 {weather}\n\n"
        f"📖 Bugungi oyat:\n{ayah}\n\n"
        f"🤲 Sadaqa berish: {charity}\n\n"
        f"⏱ Namoz vaqtlari:\n"
        f"Bomdod: {namaz_times['fajr']} | Quyosh: {namaz_times['sunrise']}\n"
        f"Peshin: {namaz_times['dhuhr']} | Asr: {namaz_times['asr']}\n"
        f"Shom: {namaz_times['maghrib']} | Xufton: {namaz_times['isha']}"
    )
    
    await message.answer(response)

@dp.message(Command("roza"))
async def cmd_roza(message: Message):
    user_id = message.from_user.id
    
    # Foydalanuvchi sozlamalarini tekshirish
    if user_id not in user_settings:
        await message.answer(
            "Iltimos, avval joylashuvingizni sozlang: /start",
            reply_markup=get_location_keyboard()
        )
        await LocationStates.waiting_for_location.set()
        return
    
    settings = user_settings[user_id]
    now = datetime.now(settings['timezone'])
    
    # Ramazon oyini tekshirish
    if not is_ramadan(now):
        # Hijriy sanani ko'rsatish
        hijri = Gregorian(now.year, now.month, now.day).to_hijri()
        await message.answer(
            f"📅 Hozir Ramazon oyi emas.\n"
            f"Hijriy sana: {hijri.month}-oy, {hijri.day}-kun\n"
            f"Ramazon boshlanishiga {30 - hijri.day if hijri.month == 8 else '?'} kun qoldi."
        )
        return
    
    # Ramazon vaqtlarini olish
    ramadan_times = get_ramadan_times(now, settings['lat'], settings['lng'], settings['method'])
    
    if not ramadan_times:
        await message.answer("Vaqtlarni olishda xatolik yuz berdi.")
        return
    
    imsak_str = ramadan_times["imsak"]
    iftar_str = ramadan_times["iftar"]
    
    # Vaqtlarni datetime obyektiga aylantirish
    today = now.date()
    imsak_h, imsak_m = map(int, imsak_str.split(':'))
    iftar_h, iftar_m = map(int, iftar_str.split(':'))
    
    imsak_dt = settings['timezone'].localize(datetime(today.year, today.month, today.day, imsak_h, imsak_m))
    iftar_dt = settings['timezone'].localize(datetime(today.year, today.month, today.day, iftar_h, iftar_m))
    
    # Qolgan vaqtni hisoblash
    if now < imsak_dt:
        target = imsak_dt
        event = "Saharlikka"
    elif now < iftar_dt:
        target = iftar_dt
        event = "Iftorga"
    else:
        tomorrow = today + timedelta(days=1)
        target = settings['timezone'].localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, imsak_h, imsak_m))
        event = "Ertangi saharlikka"
    
    time_left_seconds = int((target - now).total_seconds())
    time_left_str = format_time_left(time_left_seconds)
    
    # Ro'za davomiyligi
    fast_duration = iftar_dt - imsak_dt
    fast_hours = fast_duration.seconds // 3600
    fast_minutes = (fast_duration.seconds % 3600) // 60
    fast_duration_str = f"{fast_hours} soat {fast_minutes} daqiqa"
    
    # Iftorlik retsepti (mintaqaga qarab)
    recipes = get_iftar_recipes()
    if "uz" in settings['city'].lower():
        recipe = recipes['uz']
    elif "turkey" in settings['city'].lower() or "istanbul" in settings['city'].lower():
        recipe = recipes['turkey']
    elif any(arab in settings['city'].lower() for arab in ['dubai', 'makkah', 'cairo', 'riyadh']):
        recipe = recipes['arab']
    else:
        recipe = recipes['international']
    
    response = (
        f"📍 {settings['city']}\n"
        f"🌙 Ramazon - {event} {time_left_str} qoldi.\n\n"
        f"🤲 Og'iz yopish: {imsak_str}\n"
        f"🍽 Iftorlik: {iftar_str} ({fast_duration_str})\n\n"
        f"🍲 Bugungi iftorlik taom: {recipe}"
    )
    
    await message.answer(response)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🤖 *Diniy Bot - Yordam*\n\n"
        "*Buyruqlar:*\n"
        "/start - Botni ishga tushirish va joylashuv sozlash\n"
        "/namoz - Namoz vaqtlari va qo'shimcha ma'lumotlar\n"
        "/roza - Ro'za tutish haqida ma'lumot (Ramazonda)\n"
        "/location - Joylashuvni o'zgartirish\n"
        "/method - Namoz vaqtlarini hisoblash usulini o'zgartirish\n"
        "/help - Yordam\n\n"
        "*Hisoblash usullari:*\n"
        "2 - ISNA (Shimoliy Amerika)\n"
        "3 - Musulmonlar Ligasi\n"
        "4 - Umm Al-Qura\n"
        "5 - Qohira\n"
        "8 - Qatar\n"
        "12 - Tehron\n\n"
        "Bot butun dunyo bo'ylab ishlaydi. Joylashuvingizni yuboring yoki shahar nomini kiriting!"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("method"))
async def cmd_change_method(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_settings:
        await message.answer("Avval joylashuvingizni sozlang: /start")
        return
    
    # Hisoblash usullari haqida ma'lumot
    await message.answer(
        "Namoz vaqtlarini hisoblash usulini tanlang:\n\n"
        "2 - ISNA (Shimoliy Amerika)\n"
        "3 - Musulmonlar Ligasi\n"
        "4 - Umm Al-Qura (Saudiya)\n"
        "5 - Qohira (Misr)\n"
        "8 - Qatar\n"
        "12 - Tehron (Eron)\n\n"
        "Raqamni yuboring (masalan: 2):"
    )

@dp.message()
async def handle_method_change(message: Message):
    user_id = message.from_user.id
    
    if user_id in user_settings and message.text.isdigit():
        method = int(message.text)
        if method in [2, 3, 4, 5, 8, 12]:
            user_settings[user_id]['method'] = method
            await message.answer(f"✅ Hisoblash usuli o'zgartirildi: {method}")
        else:
            await message.answer("❌ Noto'g'ri usul. Qaytadan urinib ko'ring.")

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Bot ishga tushganini bildirish
    logging.info("Bot ishga tushdi!")
    
    # Pollingni boshlash
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
