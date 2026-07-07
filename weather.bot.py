import os
import asyncio
import aiohttp
import sqlite3
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import pytz

# ========== الإعدادات ==========
TOKEN = os.getenv("TELEGRAM_TOKEN", "8244290417:AAFyZ2lK7fMEOxvW5wv98HfK8M8gRnUKyo4")
API_KEY = os.getenv("API_KEY", "70db0e7c65784b59b8d24440260207")
BASE_URL = "https://api.weatherapi.com/v1"
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003554303588")

# ========== قاعدة البيانات ==========
DB_FILE = "bot_database.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        language TEXT DEFAULT 'ar',
        first_seen TEXT,
        last_seen TEXT,
        total_requests INTEGER DEFAULT 0
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS cities_searched (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        city TEXT,
        searched_at TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        city TEXT,
        UNIQUE(user_id, city)
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS stats (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()
    conn.close()
    print("✅ تم تجهيز قاعدة البيانات: " + DB_FILE)

def save_user_data(user_id, username, first_name, last_name, language=None):
    user_id = str(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    is_new = row is None

    if is_new:
        cur.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, language, first_seen, last_seen, total_requests) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (user_id, username or "", first_name or "", last_name or "", language or "ar", now, now)
        )
    else:
        if language:
            cur.execute("UPDATE users SET language=? WHERE user_id=?", (language, user_id))
        cur.execute(
            "UPDATE users SET username=?, first_name=?, last_name=?, last_seen=?, total_requests=total_requests+1 "
            "WHERE user_id=?",
            (username or row["username"], first_name or row["first_name"], last_name or row["last_name"], now, user_id)
        )
    conn.commit()
    conn.close()
    return is_new

def get_user_language(user_id):
    user_id = str(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["language"] if row else "ar"

def set_user_language(user_id, lang):
    user_id = str(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()

def save_city_search(user_id, city):
    user_id = str(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM cities_searched WHERE user_id=? AND city=?", (user_id, city))
    if not cur.fetchone():
        cur.execute("INSERT INTO cities_searched (user_id, city, searched_at) VALUES (?, ?, ?)", (user_id, city, now))
        conn.commit()
    conn.close()

def get_stat_raw(key, default=""):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM stats WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default

def get_stat_int(key, default=0):
    val = get_stat_raw(key, None)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

def set_stat(key, value):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO stats (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )
    conn.commit()
    conn.close()

def update_stats(action):
    if action == "weather":
        set_stat("total_weather_requests", get_stat_int("total_weather_requests") + 1)
    elif action == "start":
        set_stat("start_count", get_stat_int("start_count") + 1)
    elif action == "user":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM users")
        count = cur.fetchone()["c"]
        conn.close()
        set_stat("total_users", count)
    elif action == "favorite":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM favorites")
        count = cur.fetchone()["c"]
        conn.close()
        set_stat("total_favorites", count)
    set_stat("last_updated", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

def load_favorites_for_user(user_id):
    user_id = str(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT city FROM favorites WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [r["city"] for r in rows]

def add_favorite(user_id, city):
    user_id = str(user_id)
    conn = get_db()
    cur = conn.cursor()
    added = False
    try:
        cur.execute("INSERT INTO favorites (user_id, city) VALUES (?, ?)", (user_id, city))
        conn.commit()
        added = True
    except sqlite3.IntegrityError:
        added = False
    conn.close()
    if added:
        update_stats("favorite")
    return added

def remove_favorite(user_id, city):
    user_id = str(user_id)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM favorites WHERE user_id=? AND city=?", (user_id, city))
    removed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return removed

# ========== قاموس الترجمة الكامل (3 لغات) ==========
T = {
    "choose_language": {
        "ar": "🌐 اختر لغتك",
        "en": "🌐 Choose Your Language",
        "fa": "🌐 زبان خود را انتخاب کنید"
    },
    "lang_arabic": {"ar": "🇸🇦 العربية", "en": "🇸🇦 العربية (Arabic)", "fa": "🇸🇦 عربی"},
    "lang_english": {"ar": "🇬🇧 English", "en": "🇬🇧 English", "fa": "🇬🇧 انگلیسی"},
    "lang_persian": {"ar": "🇮🇷 فارسی", "en": "🇮🇷 فارسی (Persian)", "fa": "🇮🇷 فارسی"},
    
    "welcome_title": {
        "ar": "🌤️ *بوت الطقس الشامل*",
        "en": "🌤️ *Comprehensive Weather Bot*",
        "fa": "🌤️ *ربات جامع آب و هوا*"
    },
    "welcome_features_title": {
        "ar": "📌 *المميزات:*",
        "en": "📌 *Features:*",
        "fa": "📌 *ویژگی‌ها:*"
    },
    "welcome_features": {
        "ar": "• 🏙️ طقس دقيق لجميع مدن العالم\n• 📅 توقعات 3 أيام\n• ⏰ توقعات الساعة\n• ☀️ مؤشر UV وجودة الهواء\n• ⭐ حفظ المفضلة\n• 💡 نصائح ذكية",
        "en": "• 🏙️ Accurate weather for all cities\n• 📅 3-day forecast\n• ⏰ Hourly forecast\n• ☀️ UV index & air quality\n• ⭐ Save favorites\n• 💡 Smart tips",
        "fa": "• 🏙️ آب و هوای دقیق تمام شهرها\n• 📅 پیش‌بینی ۳ روزه\n• ⏰ پیش‌بینی ساعتی\n• ☀️ شاخص UV و کیفیت هوا\n• ⭐ ذخیره علاقه‌مندی‌ها\n• 💡 نکات هوشمند"
    },
    "welcome_prompt": {
        "ar": "🔍 *اكتب اسم مدينتك مباشرة*",
        "en": "🔍 *Type your city name directly*",
        "fa": "🔍 *نام شهر خود را مستقیماً تایپ کنید*"
    },
    "welcome_doctor": {
        "ar": "👨‍⚕️ *د/ عاصم النجار*",
        "en": "👨‍⚕️ *Dr. Asem Al-Najjar*",
        "fa": "👨‍⚕️ *دکتر عاصم النجار*"
    },
    "language_set": {
        "ar": "✅ تم تعيين اللغة العربية",
        "en": "✅ Language set to English",
        "fa": "✅ زبان فارسی تنظیم شد"
    },
    
    "btn_arab": {"ar": "🌍 مدن عربية", "en": "🌍 Arab Cities", "fa": "🌍 شهرهای عربی"},
    "btn_world": {"ar": "🌎 مدن عالمية", "en": "🌎 Global Cities", "fa": "🌎 شهرهای جهانی"},
    "btn_favorites": {"ar": "⭐ المفضلة", "en": "⭐ Favorites", "fa": "⭐ علاقه‌مندی‌ها"},
    "btn_advanced": {"ar": "📋 أوامر متقدمة", "en": "📋 Advanced", "fa": "📋 دستورات پیشرفته"},
    "btn_language": {"ar": "🌐 اللغات", "en": "🌐 Languages", "fa": "🌐 زبان‌ها"},
    "btn_back_main": {"ar": "🔙 الرئيسية", "en": "🔙 Main Menu", "fa": "🔙 منوی اصلی"},
    "btn_back_current": {"ar": "🔙 الطقس الحالي", "en": "🔙 Current Weather", "fa": "🔙 وضعیت فعلی"},
    "choose_city": {"ar": "اختر مدينة:", "en": "Choose a city:", "fa": "یک شهر انتخاب کنید:"},
    
    "btn_forecast_3": {"ar": "📅 توقعات 3 أيام", "en": "📅 3-Day Forecast", "fa": "📅 پیش‌بینی ۳ روزه"},
    "btn_hourly": {"ar": "⏰ طقس بالساعة", "en": "⏰ Hourly Weather", "fa": "⏰ آب و هوای ساعتی"},
    "btn_add_fav": {"ar": "⭐ إضافة مفضلة", "en": "⭐ Add to Favorites", "fa": "⭐ افزودن به علاقه‌مندی‌ها"},
    
    "fetching_forecast": {"ar": "⏳ جاري جلب التوقعات...", "en": "⏳ Fetching forecast...", "fa": "⏳ در حال دریافت پیش‌بینی..."},
    "fetching_hourly": {"ar": "⏳ جاري جلب توقعات الساعات...", "en": "⏳ Fetching hourly forecast...", "fa": "⏳ در حال دریافت پیش‌بینی ساعتی..."},
    "fetching_weather": {"ar": "⏳ جاري جلب طقس", "en": "⏳ Fetching weather for", "fa": "⏳ در حال دریافت آب و هوای"},
    "weather_not_found": {"ar": "❌ المدينة غير موجودة، أو تعذر جلب بيانات التوقعات حاليًا. حاول مرة اخرى.", "en": "❌ City not found or unable to fetch forecast data. Please try again.", "fa": "❌ شهر یافت نشد یا دریافت داده‌ها ممکن نیست. دوباره تلاش کنید."},
    "forecast_not_available": {"ar": "❌ تعذر جلب التوقعات حاليًا، حاول لاحقًا", "en": "❌ Unable to fetch forecast now, try later", "fa": "❌ دریافت پیش‌بینی ممکن نیست، بعداً تلاش کنید"},
    "hourly_not_available": {"ar": "❌ تعذر جلب توقعات الساعات حاليًا", "en": "❌ Unable to fetch hourly forecast now", "fa": "❌ دریافت پیش‌بینی ساعتی ممکن نیست"},
    "city_not_available": {"ar": "❌ بيانات المدينة غير متوفرة حالياً", "en": "❌ City data not available right now", "fa": "❌ داده‌های شهر در حال حاضر موجود نیست"},
    
    "forecast_title": {"ar": "📅 *توقعات", "en": "📅 *Forecast for", "fa": "📅 *پیش‌بینی"},
    "forecast_days": {"ar": "3 أيام قادمة", "en": "Next 3 Days", "fa": "۳ روز آینده"},
    "hourly_title": {"ar": "⏰ *طقس", "en": "⏰ *Weather for", "fa": "⏰ *آب و هوای"},
    "hourly_local_time": {"ar": "🕐 التوقيت المحلي:", "en": "🕐 Local Time:", "fa": "🕐 زمان محلی:"},
    
    "condition": {"ar": "الحالة", "en": "Condition", "fa": "وضعیت"},
    "temperature": {"ar": "درجة الحرارة", "en": "Temperature", "fa": "دما"},
    "current_temp": {"ar": "الحالية", "en": "Current", "fa": "فعلی"},
    "feels_like": {"ar": "المحسوسة", "en": "Feels Like", "fa": "احساس می‌شود"},
    "max_temp": {"ar": "العظمى", "en": "High", "fa": "بیشینه"},
    "min_temp": {"ar": "الصغرى", "en": "Low", "fa": "کمینه"},
    "details": {"ar": "تفاصيل", "en": "Details", "fa": "جزئیات"},
    "humidity": {"ar": "الرطوبة", "en": "Humidity", "fa": "رطوبت"},
    "wind": {"ar": "الرياح", "en": "Wind", "fa": "باد"},
    "rain_chance": {"ar": "فرصة الأمطار", "en": "Rain Chance", "fa": "احتمال بارش"},
    "uv_index": {"ar": "مؤشر UV", "en": "UV Index", "fa": "شاخص UV"},
    "air_quality": {"ar": "جودة الهواء", "en": "Air Quality", "fa": "کیفیت هوا"},
    "sun": {"ar": "الشمس", "en": "Sun", "fa": "خورشید"},
    "sunrise": {"ar": "الشروق", "en": "Sunrise", "fa": "طلوع"},
    "sunset": {"ar": "الغروب", "en": "Sunset", "fa": "غروب"},
    "tips": {"ar": "نصائح", "en": "Tips", "fa": "نکات"},
    "kmh": {"ar": "كم/س", "en": "km/h", "fa": "کیلومتر/ساعت"},
    
    "fav_added": {"ar": "✅ تمت إضافة", "en": "✅ Added", "fa": "✅ افزوده شد"},
    "fav_exists": {"ar": "⭐ موجودة مسبقاً في المفضلة", "en": "⭐ Already in favorites", "fa": "⭐ قبلاً در علاقه‌مندی‌ها موجود است"},
    "fav_removed": {"ar": "🗑️ تم حذف", "en": "🗑️ Removed", "fa": "🗑️ حذف شد"},
    "fav_not_found": {"ar": "❌ غير موجودة في المفضلة", "en": "❌ Not in favorites", "fa": "❌ در علاقه‌مندی‌ها نیست"},
    "fav_empty": {"ar": "⭐ لا توجد مدن مفضلة\nاستخدم `/addfav المدينة` للإضافة", "en": "⭐ No favorite cities\nUse `/addfav city` to add", "fa": "⭐ شهر مورد علاقه‌ای نیست\nاز `/addfav شهر` استفاده کنید"},
    "fav_yours": {"ar": "⭐ *مدنك المفضلة:*", "en": "⭐ *Your Favorite Cities:*", "fa": "⭐ *شهرهای مورد علاقه شما:*"},
    "fav_for": {"ar": "للمفضلة", "en": "to favorites", "fa": "به علاقه‌مندی‌ها"},
    "fav_from": {"ar": "من المفضلة", "en": "from favorites", "fa": "از علاقه‌مندی‌ها"},
    
    "advanced_text": {
        "ar": "📋 *أوامر متقدمة:*\n\n`/hourly المدينة` - طقس كل ساعة\n`/addfav المدينة` - إضافة مفضلة\n`/delfav المدينة` - حذف مفضلة\n`/fav` - عرض المفضلة\n`/stats` - إحصائيات البوت\n`/language` - تغيير اللغة",
        "en": "📋 *Advanced Commands:*\n\n`/hourly city` - Hourly weather\n`/addfav city` - Add favorite\n`/delfav city` - Remove favorite\n`/fav` - Show favorites\n`/stats` - Bot statistics\n`/language` - Change language",
        "fa": "📋 *دستورات پیشرفته:*\n\n`/hourly شهر` - آب و هوای ساعتی\n`/addfav شهر` - افزودن علاقه‌مندی\n`/delfav شهر` - حذف علاقه‌مندی\n`/fav` - نمایش علاقه‌مندی‌ها\n`/stats` - آمار ربات\n`/language` - تغییر زبان"
    },
    "hourly_usage": {"ar": "⚠️ استخدم: `/hourly الرياض`", "en": "⚠️ Use: `/hourly London`", "fa": "⚠️ استفاده: `/hourly تهران`"},
    "addfav_usage": {"ar": "⚠️ استخدم: `/addfav الرياض`", "en": "⚠️ Use: `/addfav London`", "fa": "⚠️ استفاده: `/addfav تهران`"},
    "delfav_usage": {"ar": "⚠️ استخدم: `/delfav الرياض`", "en": "⚠️ Use: `/delfav London`", "fa": "⚠️ استفاده: `/delfav تهران`"},
    "city_not_found": {"ar": "❌ المدينة غير موجودة", "en": "❌ City not found", "fa": "❌ شهر یافت نشد"},
    
    "stats_title": {"ar": "📊 *إحصائيات البوت*", "en": "📊 *Bot Statistics*", "fa": "📊 *آمار ربات*"},
    "stats_users": {"ar": "👥 المستخدمين:", "en": "👥 Users:", "fa": "👥 کاربران:"},
    "stats_weather": {"ar": "🔍 طلبات الطقس:", "en": "🔍 Weather Requests:", "fa": "🔍 درخواست‌های آب و هوا:"},
    "stats_favorites": {"ar": "⭐ المفضلة:", "en": "⭐ Favorites:", "fa": "⭐ علاقه‌مندی‌ها:"},
    "stats_starts": {"ar": "🚀 مرات التشغيل:", "en": "🚀 Bot Starts:", "fa": "🚀 دفعات شروع:"},
    "stats_updated": {"ar": "🕐 آخر تحديث:", "en": "🕐 Last Updated:", "fa": "🕐 آخرین به‌روزرسانی:"},
    
    "new_user_notify": {
        "ar": "🆕 *مستخدم جديد انضم للبوت*\n\n👤 الاسم: {name}\n{username}\n🆔 ID: `{uid}`\n👥 اجمالي المستخدمين الآن: {total}",
        "en": "🆕 *New User Joined the Bot*\n\n👤 Name: {name}\n{username}\n🆔 ID: `{uid}`\n👥 Total users now: {total}",
        "fa": "🆕 *کاربر جدید به ربات پیوست*\n\n👤 نام: {name}\n{username}\n🆔 ID: `{uid}`\n👥 کل کاربران: {total}"
    },
}

def t(lang, key, **kwargs):
    if key in T:
        text = T[key].get(lang, T[key].get("ar", key))
        if kwargs:
            text = text.format(**kwargs)
        return text
    return key

# ========== المناطق الزمنية ==========
TIMEZONES = {
    "صنعاء": "Asia/Aden", "عدن": "Asia/Aden", "تعز": "Asia/Aden",
    "الحديدة": "Asia/Aden", "إب": "Asia/Aden", "المكلا": "Asia/Aden",
    "سيئون": "Asia/Aden", "ذمار": "Asia/Aden", "عمران": "Asia/Aden",
    "صعدة": "Asia/Aden", "البيضاء": "Asia/Aden", "مأرب": "Asia/Aden",
    "الرياض": "Asia/Riyadh", "جدة": "Asia/Riyadh", "مكة": "Asia/Riyadh",
    "المدينة": "Asia/Riyadh", "الدمام": "Asia/Riyadh",
    "القاهرة": "Africa/Cairo", "الإسكندرية": "Africa/Cairo",
    "دبي": "Asia/Dubai", "أبوظبي": "Asia/Dubai",
    "الدوحة": "Asia/Qatar", "مسقط": "Asia/Muscat",
    "الكويت": "Asia/Kuwait", "بغداد": "Asia/Baghdad",
    "عمّان": "Asia/Amman", "بيروت": "Asia/Beirut",
    "الخرطوم": "Africa/Khartoum", "تونس": "Africa/Tunis",
    "لندن": "Europe/London", "باريس": "Europe/Paris",
    "نيويورك": "America/New_York", "طوكيو": "Asia/Tokyo",
    "برلين": "Europe/Berlin", "روما": "Europe/Rome",
    "مدريد": "Europe/Madrid", "موسكو": "Europe/Moscow",
    "إسطنبول": "Europe/Istanbul", "كوالالمبور": "Asia/Kuala_Lumpur",
    "جاكرتا": "Asia/Jakarta", "سيدني": "Australia/Sydney",
}

def get_time_by_tzid(tz_id: str) -> tuple:
    try:
        tz = pytz.timezone(tz_id)
        now = datetime.now(tz)
        return now.strftime('%I:%M %p'), now.strftime('%A')
    except Exception:
        now = datetime.now()
        return now.strftime('%I:%M %p'), now.strftime('%A')

def get_naive_local_now(tz_id: str) -> datetime:
    try:
        tz = pytz.timezone(tz_id)
        return datetime.now(tz).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)

# ========== خرائط الترجمة للظروف الجوية ==========
CONDITION_MAP = {
    "ar": {
        "Sunny": "☀️ مشمس", "Clear": "🌙 صافي",
        "Partly cloudy": "🌤️ غائم جزئياً", "Partly Cloudy": "🌤️ غائم جزئياً",
        "Cloudy": "☁️ غائم", "Overcast": "☁️ غائم كلياً",
        "Mist": "🌫️ ضباب خفيف", "Fog": "🌫️ ضباب",
        "Freezing fog": "🌫️ ضباب متجمد",
        "Patchy rain possible": "🌦️ أمطار متفرقة محتملة",
        "Patchy rain nearby": "🌦️ أمطار متفرقة",
        "Light rain": "🌧️ أمطار خفيفة", "Moderate rain": "🌧️ أمطار متوسطة",
        "Heavy rain": "🌧️ أمطار غزيرة", "Torrential rain shower": "⛈️ أمطار طوفانية",
        "Light drizzle": "🌦️ رذاذ خفيف",
        "Light rain shower": "🌧️ زخات مطر خفيفة",
        "Moderate rain at times": "🌧️ أمطار متوسطة أحياناً",
        "Heavy rain at times": "🌧️ أمطار غزيرة أحياناً",
        "Thunderstorm": "⛈️ عاصفة رعدية",
        "Patchy light snow": "🌨️ ثلج خفيف", "Light snow": "🌨️ ثلج خفيف",
        "Moderate snow": "🌨️ ثلج متوسط", "Heavy snow": "❄️ ثلج كثيف",
        "Blizzard": "🌨️ عاصفة ثلجية", "Ice pellets": "🧊 كريات جليدية",
        "Light sleet": "🌨️ صقيع خفيف", "Moderate or heavy sleet": "🌨️ صقيع كثيف",
        "Sandstorm": "🌪️ عاصفة رملية", "Dust": "🌪️ غبار",
    },
    "en": {
        "Sunny": "☀️ Sunny", "Clear": "🌙 Clear",
        "Partly cloudy": "🌤️ Partly Cloudy", "Partly Cloudy": "🌤️ Partly Cloudy",
        "Cloudy": "☁️ Cloudy", "Overcast": "☁️ Overcast",
        "Mist": "🌫️ Mist", "Fog": "🌫️ Fog",
        "Freezing fog": "🌫️ Freezing Fog",
        "Patchy rain possible": "🌦️ Patchy Rain Possible",
        "Patchy rain nearby": "🌦️ Patchy Rain Nearby",
        "Light rain": "🌧️ Light Rain", "Moderate rain": "🌧️ Moderate Rain",
        "Heavy rain": "🌧️ Heavy Rain", "Torrential rain shower": "⛈️ Torrential Rain",
        "Light drizzle": "🌦️ Light Drizzle",
        "Light rain shower": "🌧️ Light Rain Shower",
        "Moderate rain at times": "🌧️ Moderate Rain at Times",
        "Heavy rain at times": "🌧️ Heavy Rain at Times",
        "Thunderstorm": "⛈️ Thunderstorm",
        "Patchy light snow": "🌨️ Patchy Light Snow", "Light snow": "🌨️ Light Snow",
        "Moderate snow": "🌨️ Moderate Snow", "Heavy snow": "❄️ Heavy Snow",
        "Blizzard": "🌨️ Blizzard", "Ice pellets": "🧊 Ice Pellets",
        "Light sleet": "🌨️ Light Sleet", "Moderate or heavy sleet": "🌨️ Moderate/Heavy Sleet",
        "Sandstorm": "🌪️ Sandstorm", "Dust": "🌪️ Dust",
    },
    "fa": {
        "Sunny": "☀️ آفتابی", "Clear": "🌙 صاف",
        "Partly cloudy": "🌤️ نیمه ابری", "Partly Cloudy": "🌤️ نیمه ابری",
        "Cloudy": "☁️ ابری", "Overcast": "☁️ تمام ابری",
        "Mist": "🌫️ مه خفیف", "Fog": "🌫️ مه",
        "Freezing fog": "🌫️ مه یخ‌زده",
        "Patchy rain possible": "🌦️ بارش پراکنده محتمل",
        "Patchy rain nearby": "🌦️ بارش پراکنده",
        "Light rain": "🌧️ باران سبک", "Moderate rain": "🌧️ باران متوسط",
        "Heavy rain": "🌧️ باران شدید", "Torrential rain shower": "⛈️ باران سیل‌آسا",
        "Light drizzle": "🌦️ نم‌نم باران",
        "Light rain shower": "🌧️ رگبار سبک",
        "Moderate rain at times": "🌧️ باران متوسط گاهی",
        "Heavy rain at times": "🌧️ باران شدید گاهی",
        "Thunderstorm": "⛈️ طوفان تندری",
        "Patchy light snow": "🌨️ برف سبک", "Light snow": "🌨️ برف سبک",
        "Moderate snow": "🌨️ برف متوسط", "Heavy snow": "❄️ برف سنگین",
        "Blizzard": "🌨️ کولاک", "Ice pellets": "🧊 تگرگ ریز",
        "Light sleet": "🌨️ برف‌باران سبک", "Moderate or heavy sleet": "🌨️ برف‌باران شدید",
        "Sandstorm": "🌪️ طوفان شن", "Dust": "🌪️ گرد و غبار",
    }
}

UV_LEVELS = {
    "ar": {0: ("⚪", "منعدم", "لا حاجة للحماية"), 1: ("🟢", "منخفض", "لا حاجة للحماية"), 2: ("🟢", "منخفض", "لا حاجة للحماية"), 3: ("🟡", "متوسط", "واقي شمس SPF 15+"), 4: ("🟡", "متوسط", "واقي شمس SPF 15+"), 5: ("🟡", "متوسط", "واقي شمس SPF 30+"), 6: ("🟠", "عالي", "تجنب الشمس 10ص-4م"), 7: ("🟠", "عالي", "تجنب الشمس 10ص-4م"), 8: ("🔴", "عالي جداً", "حماية قصوى ضرورية"), 9: ("🔴", "عالي جداً", "حماية قصوى ضرورية"), 10: ("🟣", "خطير", "لا تخرج للضرورة"), 11: ("🟣", "خطير جداً", "لا تخرج مطلقاً")},
    "en": {0: ("⚪", "None", "No protection needed"), 1: ("🟢", "Low", "No protection needed"), 2: ("🟢", "Low", "No protection needed"), 3: ("🟡", "Moderate", "Sunscreen SPF 15+"), 4: ("🟡", "Moderate", "Sunscreen SPF 15+"), 5: ("🟡", "Moderate", "Sunscreen SPF 30+"), 6: ("🟠", "High", "Avoid sun 10am-4pm"), 7: ("🟠", "High", "Avoid sun 10am-4pm"), 8: ("🔴", "Very High", "Maximum protection needed"), 9: ("🔴", "Very High", "Maximum protection needed"), 10: ("🟣", "Extreme", "Do not go out"), 11: ("🟣", "Very Extreme", "Do not go out at all")},
    "fa": {0: ("⚪", "هیچ", "نیاز به محافظت نیست"), 1: ("🟢", "کم", "نیاز به محافظت نیست"), 2: ("🟢", "کم", "نیاز به محافظت نیست"), 3: ("🟡", "متوسط", "کرم ضد آفتاب SPF 15+"), 4: ("🟡", "متوسط", "کرم ضد آفتاب SPF 15+"), 5: ("🟡", "متوسط", "کرم ضد آفتاب SPF 30+"), 6: ("🟠", "زیاد", "اجتناب از آفتاب ۱۰ص-۴ع"), 7: ("🟠", "زیاد", "اجتناب از آفتاب ۱۰ص-۴ع"), 8: ("🔴", "بسیار زیاد", "محافظت حداکثری ضروری"), 9: ("🔴", "بسیار زیاد", "محافظت حداکثری ضروری"), 10: ("🟣", "خطرناک", "خارج نشوید"), 11: ("🟣", "بسیار خطرناک", "اصلاً خارج نشوید")}
}

def get_aqi_info(lang, pm25):
    if lang == "ar":
        if pm25 <= 12: return "🟢", "ممتاز", "هواء نقي وصحي"
        elif pm25 <= 35: return "🟡", "جيد", "جودة هواء مقبولة"
        elif pm25 <= 55: return "🟠", "معتدل", "قد يؤثر على الحساسين"
        elif pm25 <= 150: return "🔴", "غير صحي", "تجنب التعرض الطويل"
        else: return "🟣", "خطير", "تجنب الخروج نهائياً"
    elif lang == "fa":
        if pm25 <= 12: return "🟢", "عالی", "هوای پاک و سالم"
        elif pm25 <= 35: return "🟡", "خوب", "کیفیت هوا قابل قبول"
        elif pm25 <= 55: return "🟠", "متوسط", "ممکن است بر حساسان تأثیر بگذارد"
        elif pm25 <= 150: return "🔴", "ناسالم", "از قرار گرفتن طولانی اجتناب کنید"
        else: return "🟣", "خطرناک", "اصلاً خارج نشوید"
    else:
        if pm25 <= 12: return "🟢", "Excellent", "Clean & healthy air"
        elif pm25 <= 35: return "🟡", "Good", "Acceptable air quality"
        elif pm25 <= 55: return "🟠", "Moderate", "May affect sensitive people"
        elif pm25 <= 150: return "🔴", "Unhealthy", "Avoid prolonged exposure"
        else: return "🟣", "Hazardous", "Do not go out at all"

def get_wind_dir(lang, direction):
    dirs = {
        "ar": {"N": "⬆️ شمال", "S": "⬇️ جنوب", "E": "➡️ شرق", "W": "⬅️ غرب", "NE": "↗️ شمال شرق", "NW": "↖️ شمال غرب", "SE": "↘️ جنوب شرق", "SW": "↙️ جنوب غرب"},
        "en": {"N": "⬆️ North", "S": "⬇️ South", "E": "➡️ East", "W": "⬅️ West", "NE": "↗️ NE", "NW": "↖️ NW", "SE": "↘️ SE", "SW": "↙️ SW"},
        "fa": {"N": "⬆️ شمال", "S": "⬇️ جنوب", "E": "➡️ شرق", "W": "⬅️ غرب", "NE": "↗️ شمال شرق", "NW": "↖️ شمال غرب", "SE": "↘️ جنوب شرق", "SW": "↙️ جنوب غرب"},
    }
    return dirs.get(lang, dirs["ar"]).get(direction, direction)

DAYS_AR = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه"]

def get_day_name(lang, date):
    weekday = date.weekday()
    if lang == "en": return DAYS_EN[weekday]
    elif lang == "fa": return DAYS_FA[weekday]
    return DAYS_AR[weekday]

def translate_condition(lang, condition):
    return CONDITION_MAP.get(lang, CONDITION_MAP["ar"]).get(condition, f"🌡️ {condition}")

def get_temp_bar(temp):
    if temp <= 0: return "🔵" * 5
    elif temp <= 10: return "🔵" * 4 + "⚪"
    elif temp <= 20: return "🟢" * 3 + "⚪" * 2
    elif temp <= 30: return "🟡" * 3 + "⚪" * 2
    elif temp <= 40: return "🟠" * 4 + "⚪"
    else: return "🔴" * 5

def get_weather_advice(lang, temp, rain, uv, wind):
    if lang == "en":
        tips = []
        if rain >= 80: tips.append("🌂 Rain almost certain - don't forget umbrella")
        elif rain >= 50: tips.append("🌂 High chance of rain - take your umbrella")
        elif rain >= 30: tips.append("🌦️ Chance of rain - be prepared")
        if temp >= 45: tips.append("🔥 Dangerous heat - avoid going out during day")
        elif temp >= 40: tips.append("☀️ Extreme heat - drink plenty of water")
        elif temp >= 35: tips.append("🌡️ Hot - reduce sun exposure")
        elif temp >= 20: tips.append("🌸 Pleasant weather - great for outdoors")
        elif temp >= 10: tips.append("🍂 Relatively cold - take a light jacket")
        elif temp >= 0: tips.append("🥶 Cold - wear warm clothes")
        else: tips.append("❄️ Very cold - full protection needed")
        if uv >= 8: tips.append("🧴 Sunscreen essential")
        if wind >= 40: tips.append("💨 Strong winds - be careful")
        if temp >= 20 and rain < 30 and wind < 25: tips.append("🏃 Great conditions for sports")
        return "\n".join(f"• {tip}" for tip in tips)
    elif lang == "fa":
        tips = []
        if rain >= 80: tips.append("🌂 باران تقریباً قطعی - چتر فراموش نشود")
        elif rain >= 50: tips.append("🌂 احتمال زیاد باران - چتر بردارید")
        elif rain >= 30: tips.append("🌦️ احتمال باران - آماده باشید")
        if temp >= 45: tips.append("🔥 گرمای خطرناک - در روز خارج نشوید")
        elif temp >= 40: tips.append("☀️ گرمای شدید - آب زیاد بنوشید")
        elif temp >= 35: tips.append("🌡️ گرم - کمتر در آفتاب بمانید")
        elif temp >= 20: tips.append("🌸 هوای مطبوع - عالی برای بیرون رفتن")
        elif temp >= 10: tips.append("🍂 نسبتاً سرد - ژاکت سبک بردارید")
        elif temp >= 0: tips.append("🥶 سرد - لباس گرم بپوشید")
        else: tips.append("❄️ بسیار سرد - محافظت کامل لازم است")
        if uv >= 8: tips.append("🧴 کرم ضد آفتاب ضروری است")
        if wind >= 40: tips.append("💨 باد شدید - مراقب باشید")
        if temp >= 20 and rain < 30 and wind < 25: tips.append("🏃 شرایط عالی برای ورزش")
        return "\n".join(f"• {tip}" for tip in tips)
    else:
        tips = []
        if rain >= 80: tips.append("🌂 أمطار شبه مؤكدة - لا تنسَ المظلة")
        elif rain >= 50: tips.append("🌂 احتمال كبير للأمطار - خذ مظلتك")
        elif rain >= 30: tips.append("🌦️ فرصة أمطار - كن مستعداً")
        if temp >= 45: tips.append("🔥 حرارة خطيرة - تجنب الخروج نهاراً")
        elif temp >= 40: tips.append("☀️ حرارة شديدة - اشرب ماء بكثرة")
        elif temp >= 35: tips.append("🌡️ حار - قلل التعرض للشمس")
        elif temp >= 20: tips.append("🌸 جو معتدل - مثالي للخروج")
        elif temp >= 10: tips.append("🍂 بارد نسبياً - خذ سترة خفيفة")
        elif temp >= 0: tips.append("🥶 بارد - ارتدِ ملابس دافئة")
        else: tips.append("❄️ شديد البرودة - حماية كاملة")
        if uv >= 8: tips.append("🧴 واقي شمس ضروري جداً")
        if wind >= 40: tips.append("💨 رياح قوية - انتبه")
        if temp >= 20 and rain < 30 and wind < 25: tips.append("🏃 ظروف ممتازة للرياضة")
        return "\n".join(f"• {tip}" for tip in tips)

def get_weather_icon(code, is_day):
    if code == 1000: return "☀️" if is_day else "🌙"
    elif code in [1003, 1006, 1009]: return "🌤️" if is_day else "☁️"
    elif code in [1063, 1150, 1153, 1180, 1183, 1186, 1189, 1192, 1195, 1240, 1243, 1246]: return "🌧️"
    elif code in [1087, 1273, 1276, 1279, 1282]: return "⛈️"
    elif code in [1066, 1114, 1210, 1213, 1216, 1219, 1222, 1225, 1255, 1258]: return "🌨️"
    elif code in [1030, 1135, 1147]: return "🌫️"
    else: return "🌡️"

# ========== قوائم المدن ==========
ARAB_CITIES = [
    "القاهرة", "الإسكندرية", "دبي", "أبوظبي", "الشارقة",
    "الدوحة", "مسقط", "الكويت", "بغداد", "عمّان",
    "بيروت", "الخرطوم", "تونس", "الجزائر", "الدار البيضاء",
    "الرباط", "طرابلس", "نواكشوط", "جيبوتي", "مقديشو",
    "الرياض", "جدة", "مكة", "المدينة", "الدمام",
    "صنعاء", "عدن", "تعز", "الحديدة", "المكلا"
]

WORLD_CITIES = [
    "لندن", "باريس", "نيويورك", "طوكيو", "برلين",
    "روما", "مدريد", "موسكو", "إسطنبول", "كوالالمبور",
    "جاكرتا", "سيدني", "تورونتو", "ساو باولو", "مكسيكو سيتي",
    "سيول", "بكين", "شنغهاي", "مومباي", "بانكوك",
    "أمستردام", "فيينا", "براغ", "لشبونة", "أثينا",
    "ستوكهولم", "أوسلو", "هلسنكي", "وارسو", "كييف"
]

YEMEN_COORDS = {
    "صنعاء": ("15.3694,44.1910", "صنعاء، اليمن"),
    "عدن": ("12.7855,45.0187", "عدن، اليمن"),
    "تعز": ("13.5765,44.0177", "تعز، اليمن"),
    "الحديدة": ("14.7978,42.9545", "الحديدة، اليمن"),
    "إب": ("13.9667,44.1833", "إب، اليمن"),
    "المكلا": ("14.5300,49.1300", "المكلا، اليمن"),
    "سيئون": ("15.9667,48.7833", "سيئون، اليمن"),
    "ذمار": ("14.5500,44.4017", "ذمار، اليمن"),
    "عمران": ("15.6594,43.9439", "عمران، اليمن"),
    "صعدة": ("16.9400,43.7593", "صعدة، اليمن"),
    "البيضاء": ("13.9858,45.5728", "البيضاء، اليمن"),
    "مأرب": ("15.4667,45.3333", "مأرب، اليمن"),
}

# ========== دوال الـ API ==========
async def fetch_weather(city: str) -> dict | None:
    if city in YEMEN_COORDS:
        search_query = YEMEN_COORDS[city][0]
    else:
        search_query = city

    async with aiohttp.ClientSession() as session:
        try:
            params = {"key": API_KEY, "q": search_query, "aqi": "yes", "lang": "ar"}
            async with session.get(f"{BASE_URL}/current.json", params=params, timeout=10) as resp:
                if resp.status == 200: current_data = await resp.json()
                else: return None

            params2 = {"key": API_KEY, "q": search_query, "days": 3, "aqi": "yes", "lang": "ar"}
            async with session.get(f"{BASE_URL}/forecast.json", params=params2, timeout=10) as resp:
                if resp.status == 200: forecast_data = await resp.json()
                else: forecast_data = None

            if city in YEMEN_COORDS:
                current_data["location"]["name"] = city
                current_data["location"]["country"] = "اليمن"
                if forecast_data:
                    forecast_data["location"]["name"] = city
                    forecast_data["location"]["country"] = "اليمن"

            return {"current": current_data, "forecast": forecast_data}
        except Exception:
            return None

async def fetch_hourly(city: str) -> dict | None:
    if city in YEMEN_COORDS: search_query = YEMEN_COORDS[city][0]
    else: search_query = city

    async with aiohttp.ClientSession() as session:
        try:
            params = {"key": API_KEY, "q": search_query, "hours": 12, "aqi": "no", "lang": "ar"}
            async with session.get(f"{BASE_URL}/forecast.json", params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if city in YEMEN_COORDS:
                        data["location"]["name"] = city
                        data["location"]["country"] = "اليمن"
                    return data
                return None
        except Exception:
            return None

def format_current_weather(data: dict, lang: str) -> tuple:
    c = data["current"]["current"]
    f = data["forecast"]["forecast"]["forecastday"][0]
    astro = f["astro"]
    location = data["current"]["location"]
    city = location['name']

    tz_id = location.get('tz_id') or TIMEZONES.get(city, 'Asia/Aden')
    local_time, day_name_ar = get_time_by_tzid(tz_id)

    condition = translate_condition(lang, c["condition"]["text"])
    temp = float(c["temp_c"])
    feels_like = float(c["feelslike_c"])
    humidity = c["humidity"]
    wind = float(c["wind_kph"])
    uv = float(c.get("uv", 0))
    rain_chance = f["day"]["daily_chance_of_rain"]
    is_day = c.get("is_day", 1) == 1

    big_icon = get_weather_icon(c["condition"]["code"], is_day)
    temp_bar = get_temp_bar(temp)

    aqi_text = ""
    aqi = c.get("air_quality", {})
    if aqi and aqi.get("pm2_5", 0) > 0:
        pm25 = aqi["pm2_5"]
        emoji, level, desc = get_aqi_info(lang, pm25)
        aqi_text = f"{emoji} *{t(lang, 'air_quality')}:* `{level}` - {desc}\n"

    uv_int = int(uv)
    uv_data = UV_LEVELS.get(lang, UV_LEVELS["ar"]).get(uv_int, ("⚪", "?", ""))
    uv_emoji, uv_level, uv_advice = uv_data

    wind_dir = get_wind_dir(lang, c.get("wind_dir", ""))

    advice = get_weather_advice(lang, temp, rain_chance, uv, wind)

    msg = f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  {big_icon} *{location['name'].upper()}*
┃  📍 {location['country']}
┃  📅 {day_name_ar} | 🕐 {local_time}
╰━━━━━━━━━━━━━━━━━━━━━━╯

✨ *{t(lang, 'condition')}:* {condition}

🌡️ *{t(lang, 'temperature')}:*
{temp_bar}
┣ 📊 *{t(lang, 'current_temp')}:* `{temp:.1f}°C`
┣ 🤔 *{t(lang, 'feels_like')}:* `{feels_like:.1f}°C`
┣ ⬆️ *{t(lang, 'max_temp')}:* `{f['day']['maxtemp_c']:.1f}°C`
┗ ⬇️ *{t(lang, 'min_temp')}:* `{f['day']['mintemp_c']:.1f}°C`

╭───────── 📊 *{t(lang, 'details')}* ─────────╮
┃ 💧 {t(lang, 'humidity')}: `{humidity}%`
┃ 💨 {t(lang, 'wind')}: `{wind:.0f} {t(lang, 'kmh')}` {wind_dir}
┃ 🌧️ {t(lang, 'rain_chance')}: `{rain_chance}%`
┃ ☀️ {t(lang, 'uv_index')}: {uv_emoji} `{uv:.0f}` ({uv_level})
╰──────────────────────────────╯

╭─────── 🏭 *{t(lang, 'air_quality')}* ───────╮
┃ {aqi_text if aqi_text else '🟢 *' + t(lang, 'air_quality') + ':* `' + get_aqi_info(lang, 0)[1] + '`'}
╰──────────────────────────────╯

╭─────── 🌅 *{t(lang, 'sun')}* ───────╮
┃ 🌅 {t(lang, 'sunrise')}: `{astro['sunrise']}`
┃ 🌇 {t(lang, 'sunset')}: `{astro['sunset']}`
╰──────────────────────────╯

╭─────── 💡 *{t(lang, 'tips')}* ───────╮
{advice}
╰──────────────────────────╯
"""
    return msg, temp, rain_chance

def format_forecast(data: dict, lang: str) -> str:
    location = data["forecast"]["location"]
    days = data["forecast"]["forecast"]["forecastday"]
    msg = f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  {t(lang, 'forecast_title')} {location['name'].upper()}*
┃  {t(lang, 'forecast_days')}
╰━━━━━━━━━━━━━━━━━━━━━━╯

"""
    for day in days:
        date = datetime.strptime(day["date"], "%Y-%m-%d")
        day_name = get_day_name(lang, date)
        d = day["day"]
        condition = translate_condition(lang, d["condition"]["text"])
        uv = d.get("uv", 0)
        uv_data = UV_LEVELS.get(lang, UV_LEVELS["ar"]).get(int(uv), ("⚪",))
        uv_emoji = uv_data[0]

        msg += f"""
┏━━ *{day_name}* | {date.strftime('%d/%m')} ━━┓
┃ ✨ {condition}
┃ 🌡️ `{d['maxtemp_c']:.1f}°` / `{d['mintemp_c']:.1f}°`
┃ 💧 {t(lang, 'humidity')}: `{d['avghumidity']}%`
┃ 🌧️ {t(lang, 'rain_chance')}: `{d['daily_chance_of_rain']}%`
┃ ☀️ UV: {uv_emoji} `{uv:.0f}`
┗━━━━━━━━━━━━━━━━━━━━━━━━┛
"""
    return msg

def format_hourly(data: dict, lang: str) -> str:
    location = data["location"]
    hours = data["forecast"]["forecastday"][0]["hour"]
    city = location['name']

    tz_id = location.get('tz_id') or TIMEZONES.get(city, 'Asia/Aden')
    local_time, _ = get_time_by_tzid(tz_id)
    now_local = get_naive_local_now(tz_id)

    msg = f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  {t(lang, 'hourly_title')} {location['name'].upper()}*
┃  {t(lang, 'hourly_local_time')} {local_time}
╰━━━━━━━━━━━━━━━━━━━━━━╯

"""
    count = 0
    for hour in hours:
        h_time = datetime.strptime(hour["time"], "%Y-%m-%d %H:%M")
        if h_time >= now_local and count < 8:
            condition = translate_condition(lang, hour["condition"]["text"])
            temp = hour['temp_c']
            temp_icon = "🔥" if temp > 35 else "☀️" if temp > 25 else "🌤️" if temp > 15 else "❄️"
            msg += f"┃ `{h_time.strftime('%H:%M')}` {temp_icon} `{temp:.1f}°` | {condition} | 💧`{hour['humidity']}%`\n"
            count += 1
    return msg

# ========== إشعار القناة ==========
async def notify_channel_new_user(context: ContextTypes.DEFAULT_TYPE, user, lang):
    username_line = f"🔗 @{user.username}" if user.username else "🔗 لا يوجد"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    notify_text = t(lang, "new_user_notify", name=full_name, username=username_line, uid=user.id, total=get_stat_int('total_users', 0))
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=notify_text, parse_mode='Markdown')
    except Exception as ex:
        print(f"⚠️ خطأ في ارسال اشعار القناة: {ex}")

# ========== شاشة اختيار اللغة ==========
def get_language_keyboard():
    keyboard = [
        [InlineKeyboardButton(t("ar", "lang_arabic"), callback_data="lang:ar")],
        [InlineKeyboardButton(t("en", "lang_english"), callback_data="lang:en")],
        [InlineKeyboardButton(t("fa", "lang_persian"), callback_data="lang:fa")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def send_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f"{t('ar', 'choose_language')}\n{t('en', 'choose_language')}\n{t('fa', 'choose_language')}"
    await update.message.reply_text(text, reply_markup=get_language_keyboard())

def get_welcome_keyboard(lang):
    return [
        [InlineKeyboardButton(t(lang, "btn_arab"), callback_data="arab")],
        [InlineKeyboardButton(t(lang, "btn_world"), callback_data="world")],
        [InlineKeyboardButton(t(lang, "btn_favorites"), callback_data="fav_list")],
        [InlineKeyboardButton(t(lang, "btn_advanced"), callback_data="advanced"), InlineKeyboardButton(t(lang, "btn_language"), callback_data="change_lang")],
    ]

def get_welcome_text(lang):
    return (
        f"╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        f"┃  {t(lang, 'welcome_title')}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"{t(lang, 'welcome_features_title')}\n"
        f"{t(lang, 'welcome_features')}\n\n"
        f"{t(lang, 'welcome_prompt')}\n\n"
        f"{t(lang, 'welcome_doctor')}"
    )

# ========== أوامر البوت ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if row is None:
        await send_language_selection(update, context)
    else:
        lang = row["language"] or "ar"
        is_new = save_user_data(user.id, user.username, user.first_name, user.last_name)
        update_stats("start")
        update_stats("user")
        if is_new:
            await notify_channel_new_user(context, user, lang)
        await show_welcome(update, lang)

async def show_welcome(update: Update, lang: str):
    keyboard = get_welcome_keyboard(lang)
    welcome_text = get_welcome_text(lang)
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_language_selection(update, context)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(update.effective_user.id)
    total_users = get_stat_int("total_users", 0)
    total_weather = get_stat_int("total_weather_requests", 0)
    total_favorites = get_stat_int("total_favorites", 0)
    start_count = get_stat_int("start_count", 0)
    last_updated = get_stat_raw("last_updated", "--")

    await update.message.reply_text(
        f"{t(lang, 'stats_title')}\n\n"
        f"{t(lang, 'stats_users')} `{total_users}`\n"
        f"{t(lang, 'stats_weather')} `{total_weather}`\n"
        f"{t(lang, 'stats_favorites')} `{total_favorites}`\n"
        f"{t(lang, 'stats_starts')} `{start_count}`\n"
        f"{t(lang, 'stats_updated')} `{last_updated}`",
        parse_mode='Markdown'
    )

async def advanced_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(update.effective_user.id)
    await update.message.reply_text(t(lang, "advanced_text"), parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    city = update.message.text.strip()
    lang = get_user_language(user.id)

    if len(city) < 2 or len(city) > 60:
        return

    save_user_data(user.id, user.username, user.first_name, user.last_name)
    save_city_search(user.id, city)
    update_stats("weather")

    await update.message.chat.send_action('typing')
    data = await fetch_weather(city)

    if not data or not data.get("current") or not data.get("forecast"):
        await update.message.reply_text(t(lang, "weather_not_found"))
        return

    msg, temp, rain = format_current_weather(data, lang)

    keyboard = [
        [InlineKeyboardButton(t(lang, "btn_forecast_3"), callback_data=f"fc:{city}")],
        [InlineKeyboardButton(t(lang, "btn_hourly"), callback_data=f"hr:{city}")],
        [InlineKeyboardButton(t(lang, "btn_add_fav"), callback_data=f"addfav:{city}")],
    ]

    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def hourly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(update.effective_user.id)
    if not context.args:
        await update.message.reply_text(t(lang, "hourly_usage"))
        return

    city = ' '.join(context.args)
    await update.message.chat.send_action('typing')
    data = await fetch_hourly(city)

    if data:
        msg = format_hourly(data, lang)
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(t(lang, "city_not_found"))

async def add_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(update.effective_user.id)
    if not context.args:
        await update.message.reply_text(t(lang, "addfav_usage"))
        return

    city = ' '.join(context.args)
    user_id = update.effective_user.id

    if add_favorite(user_id, city):
        await update.message.reply_text(f"{t(lang, 'fav_added')} *{city}* {t(lang, 'fav_for')}", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⭐ {city} {t(lang, 'fav_exists')}")

async def del_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(update.effective_user.id)
    if not context.args:
        await update.message.reply_text(t(lang, "delfav_usage"))
        return

    city = ' '.join(context.args)
    user_id = update.effective_user.id

    if remove_favorite(user_id, city):
        await update.message.reply_text(f"{t(lang, 'fav_removed')} *{city}* {t(lang, 'fav_from')}", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ {city} {t(lang, 'fav_not_found')}")

async def show_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_language(update.effective_user.id)
    user_id = update.effective_user.id
    favs = load_favorites_for_user(user_id)

    if not favs:
        await update.message.reply_text(t(lang, "fav_empty"))
        return

    keyboard = []
    for city in favs:
        keyboard.append([InlineKeyboardButton(f"🏙️ {city}", callback_data=f"city:{city}")])

    await update.message.reply_text(t(lang, "fav_yours"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # اختيار اللغة
    if data.startswith("lang:"):
        new_lang = data.split(":")[1]
        user = query.from_user
        is_new = save_user_data(user.id, user.username, user.first_name, user.last_name, new_lang)
        set_user_language(user.id, new_lang)
        update_stats("start")
        update_stats("user")
        if is_new:
            await notify_channel_new_user(context, user, new_lang)
        
        # عرض الترحيب مباشرة باللغة الجديدة
        keyboard = get_welcome_keyboard(new_lang)
        welcome_text = get_welcome_text(new_lang)
        await query.edit_message_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    lang = get_user_language(query.from_user.id)

    # تغيير اللغة من القائمة
    if data == "change_lang":
        text = f"{t('ar', 'choose_language')}\n{t('en', 'choose_language')}\n{t('fa', 'choose_language')}"
        await query.edit_message_text(text, reply_markup=get_language_keyboard())
        return

    # عرض المفضلة
    if data == "fav_list":
        user_id = query.from_user.id
        favs = load_favorites_for_user(user_id)
        if not favs:
            await query.edit_message_text(t(lang, "fav_empty"), parse_mode='Markdown')
        else:
            keyboard = []
            for city in favs:
                keyboard.append([InlineKeyboardButton(f"🏙️ {city}", callback_data=f"city:{city}")])
            keyboard.append([InlineKeyboardButton(t(lang, "btn_back_main"), callback_data="menu")])
            await query.edit_message_text(t(lang, "fav_yours"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # أوامر متقدمة
    if data == "advanced":
        await query.edit_message_text(t(lang, "advanced_text"), parse_mode='Markdown')
        return

    # توقعات 3 أيام
    if data.startswith("fc:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text(t(lang, "fetching_forecast"))
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("forecast"):
            msg = format_forecast(weather_data, lang)
            back = [[InlineKeyboardButton(t(lang, "btn_back_current"), callback_data=f"now:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(back), parse_mode='Markdown')
        else:
            await query.edit_message_text(t(lang, "forecast_not_available"))
        return

    # الطقس الحالي
    if data.startswith("now:"):
        city = data.split(":", 1)[1]
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("current") and weather_data.get("forecast"):
            msg, _, _ = format_current_weather(weather_data, lang)
            keyboard = [
                [InlineKeyboardButton(t(lang, "btn_forecast_3"), callback_data=f"fc:{city}")],
                [InlineKeyboardButton(t(lang, "btn_hourly"), callback_data=f"hr:{city}")],
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await query.edit_message_text(t(lang, "city_not_available"))
        return

    # توقعات ساعة بساعة
    if data.startswith("hr:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text(t(lang, "fetching_hourly"))
        weather_data = await fetch_hourly(city)
        if weather_data:
            msg = format_hourly(weather_data, lang)
            back = [[InlineKeyboardButton(t(lang, "btn_back_current"), callback_data=f"now:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(back), parse_mode='Markdown')
        else:
            await query.edit_message_text(t(lang, "hourly_not_available"))
        return

    # إضافة مفضلة
    if data.startswith("addfav:"):
        city = data.split(":", 1)[1]
        user_id = query.from_user.id
        if add_favorite(user_id, city):
            await query.answer(f"{t(lang, 'fav_added')} {city} {t(lang, 'fav_for')}")
        else:
            await query.answer(f"⭐ {city} {t(lang, 'fav_exists')}")
        return

    # مدن عربية
    if data == "arab":
        keyboard = build_city_keyboard(ARAB_CITIES)
        keyboard.append([InlineKeyboardButton(t(lang, "btn_back_main"), callback_data="menu")])
        await query.edit_message_text(f"*{t(lang, 'btn_arab')}*\n{t(lang, 'choose_city')}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # مدن عالمية
    if data == "world":
        keyboard = build_city_keyboard(WORLD_CITIES)
        keyboard.append([InlineKeyboardButton(t(lang, "btn_back_main"), callback_data="menu")])
        await query.edit_message_text(f"*{t(lang, 'btn_world')}*\n{t(lang, 'choose_city')}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    # اختيار مدينة
    if data.startswith("city:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text(f"{t(lang, 'fetching_weather')} *{city}*...", parse_mode='Markdown')
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("current") and weather_data.get("forecast"):
            msg, _, _ = format_current_weather(weather_data, lang)
            keyboard = [
                [InlineKeyboardButton(t(lang, "btn_forecast_3"), callback_data=f"fc:{city}")],
                [InlineKeyboardButton(t(lang, "btn_hourly"), callback_data=f"hr:{city}")],
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await query.edit_message_text(t(lang, "city_not_available"))
        return

    # القائمة الرئيسية
    if data == "menu":
        await show_welcome(update, lang)
        return

def build_city_keyboard(cities):
    keyboard = []
    row = []
    for i, city in enumerate(cities):
        row.append(InlineKeyboardButton(city, callback_data=f"city:{city}"))
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    return keyboard

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ حدث خطأ غير متوقع: {context.error}")
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("❌ حدث خطأ غير متوقع، حاول مرة اخرى")
    except Exception:
        pass

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("hourly", hourly_cmd))
    app.add_handler(CommandHandler("addfav", add_fav))
    app.add_handler(CommandHandler("delfav", del_fav))
    app.add_handler(CommandHandler("fav", show_fav))
    app.add_handler(CommandHandler("help", advanced_commands))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("✅ البوت يعمل بـ 3 لغات (عربي - English - فارسی)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
