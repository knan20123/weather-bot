import os
import asyncio
import aiohttp
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import pytz

# ========== الإعدادات ==========
TOKEN = os.getenv("TELEGRAM_TOKEN", "8244290417:AAFyZ2lK7fMEOxvW5wv98HfK8M8gRnUKyo4")
API_KEY = os.getenv("API_KEY", "70db0e7c65784b59b8d24440260207")
BASE_URL = "https://api.weatherapi.com/v1"

# معرف القناة التي سيتم ارسال اشعارات المستخدمين الجدد اليها
# يجب ان يكون البوت مشرفاً (Admin) في هذه القناة حتى يستطيع الارسال اليها
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003554303588")

# ========== قاعدة البيانات (SQLite منفصلة) ==========
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

def save_user_data(user_id, username, first_name, last_name):
    """يحفظ او يحدث بيانات المستخدم. يرجع True لو كان مستخدماً جديداً (لاول مرة)"""
    user_id = str(user_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    is_new = row is None

    if is_new:
        cur.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen, total_requests) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (user_id, username or "", first_name or "", last_name or "", now, now)
        )
    else:
        cur.execute(
            "UPDATE users SET username=?, first_name=?, last_name=?, last_seen=?, total_requests=total_requests+1 "
            "WHERE user_id=?",
            (username or row["username"], first_name or row["first_name"], last_name or row["last_name"], now, user_id)
        )
    conn.commit()
    conn.close()
    return is_new

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

# ========== المناطق الزمنية (احتياطية فقط - نستخدم tz_id من الـ API عند توفره) ==========
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
    """يحسب الوقت المحلي والزمني بدقة باستخدام tz_id (IANA) - الاعتماد الاول على المعلومة القادمة من الـ API نفسها"""
    try:
        tz = pytz.timezone(tz_id)
        now = datetime.now(tz)
        return now.strftime('%I:%M %p'), now.strftime('%A')
    except Exception:
        now = datetime.now()
        return now.strftime('%I:%M %p'), now.strftime('%A')

def get_naive_local_now(tz_id: str) -> datetime:
    """يرجع الوقت المحلي الحالي (بدون معلومة المنطقة الزمنية) لمقارنته بأوقات الـ API"""
    try:
        tz = pytz.timezone(tz_id)
        return datetime.now(tz).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()

# ========== حالات المحادثة ==========
COMPARE_CITY1, COMPARE_CITY2 = range(2)

# ========== ترجمة الحالات ==========
CONDITION_MAP = {
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
}

UV_LEVELS = {
    0: ("⚪", "منعدم", "لا حاجة للحماية"),
    1: ("🟢", "منخفض", "لا حاجة للحماية"),
    2: ("🟢", "منخفض", "لا حاجة للحماية"),
    3: ("🟡", "متوسط", "واقي شمس SPF 15+"),
    4: ("🟡", "متوسط", "واقي شمس SPF 15+"),
    5: ("🟡", "متوسط", "واقي شمس SPF 30+"),
    6: ("🟠", "عالي", "تجنب الشمس 10ص-4م"),
    7: ("🟠", "عالي", "تجنب الشمس 10ص-4م"),
    8: ("🔴", "عالي جداً", "حماية قصوى ضرورية"),
    9: ("🔴", "عالي جداً", "حماية قصوى ضرورية"),
    10: ("🟣", "خطير", "لا تخرج للضرورة"),
    11: ("🟣", "خطير جداً", "لا تخرج مطلقاً"),
}

def get_aqi_info(pm25):
    if pm25 <= 12: return "🟢", "ممتاز", "هواء نقي وصحي"
    elif pm25 <= 35: return "🟡", "جيد", "جودة هواء مقبولة"
    elif pm25 <= 55: return "🟠", "معتدل", "قد يؤثر على الحساسين"
    elif pm25 <= 150: return "🔴", "غير صحي", "تجنب التعرض الطويل"
    else: return "🟣", "خطير", "تجنب الخروج نهائياً"

# ========== تصحيح أسماء المدن اليمنية ==========
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

def translate_condition(condition: str) -> str:
    return CONDITION_MAP.get(condition, f"🌡️ {condition}")

def get_temp_bar(temp: float) -> str:
    if temp <= 0: return "🔵" * 5
    elif temp <= 10: return "🔵" * 4 + "⚪"
    elif temp <= 20: return "🟢" * 3 + "⚪" * 2
    elif temp <= 30: return "🟡" * 3 + "⚪" * 2
    elif temp <= 40: return "🟠" * 4 + "⚪"
    else: return "🔴" * 5

def get_weather_advice(temp: float, rain: int, uv: float, wind: float) -> str:
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

def get_weather_icon(code: int, is_day: bool) -> str:
    if code == 1000: return "☀️" if is_day else "🌙"
    elif code in [1003, 1006, 1009]: return "🌤️" if is_day else "☁️"
    elif code in [1063, 1150, 1153, 1180, 1183, 1186, 1189, 1192, 1195, 1240, 1243, 1246]: return "🌧️"
    elif code in [1087, 1273, 1276, 1279, 1282]: return "⛈️"
    elif code in [1066, 1114, 1210, 1213, 1216, 1219, 1222, 1225, 1255, 1258]: return "🌨️"
    elif code in [1030, 1135, 1147]: return "🌫️"
    else: return "🌡️"

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

def format_current_weather(data: dict) -> tuple:
    c = data["current"]["current"]
    f = data["forecast"]["forecast"]["forecastday"][0]
    astro = f["astro"]
    location = data["current"]["location"]
    city = location['name']

    tz_id = location.get('tz_id') or TIMEZONES.get(city, 'Asia/Aden')
    local_time, day_name_ar = get_time_by_tzid(tz_id)

    condition = translate_condition(c["condition"]["text"])
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
        emoji, level, desc = get_aqi_info(pm25)
        aqi_text = f"{emoji} *جودة الهواء:* `{level}` - {desc}\n"

    uv_int = int(uv)
    uv_emoji, uv_level, uv_advice = UV_LEVELS.get(uv_int, ("⚪", "غير معروف", ""))

    wind_dir_ar = {
        "N": "⬆️ شمال", "S": "⬇️ جنوب", "E": "➡️ شرق", "W": "⬅️ غرب",
        "NE": "↗️ شمال شرق", "NW": "↖️ شمال غرب", "SE": "↘️ جنوب شرق", "SW": "↙️ جنوب غرب",
    }
    wind_dir = wind_dir_ar.get(c.get("wind_dir", ""), c.get("wind_dir", ""))

    advice = get_weather_advice(temp, rain_chance, uv, wind)

    msg = f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  {big_icon} *{location['name'].upper()}*
┃  📍 {location['country']}
┃  📅 {day_name_ar} | 🕐 {local_time}
╰━━━━━━━━━━━━━━━━━━━━━━╯

✨ *الحالة:* {condition}

🌡️ *درجة الحرارة:*
{temp_bar}
┣ 📊 *الحالية:* `{temp:.1f}°C`
┣ 🤔 *المحسوسة:* `{feels_like:.1f}°C`
┣ ⬆️ *العظمى:* `{f['day']['maxtemp_c']:.1f}°C`
┗ ⬇️ *الصغرى:* `{f['day']['mintemp_c']:.1f}°C`

╭───────── 📊 *تفاصيل* ─────────╮
┃ 💧 الرطوبة: `{humidity}%`
┃ 💨 الرياح: `{wind:.0f} كم/س` {wind_dir}
┃ 🌧️ فرصة الأمطار: `{rain_chance}%`
┃ ☀️ مؤشر UV: {uv_emoji} `{uv:.0f}` ({uv_level})
╰──────────────────────────────╯

╭─────── 🏭 *جودة الهواء* ───────╮
┃ {aqi_text if aqi_text else '🟢 *جودة الهواء:* `ممتاز` - هواء نقي وصحي'}
╰──────────────────────────────╯

╭─────── 🌅 *الشمس* ───────╮
┃ 🌅 الشروق: `{astro['sunrise']}`
┃ 🌇 الغروب: `{astro['sunset']}`
╰──────────────────────────╯

╭─────── 💡 *نصائح* ───────╮
{advice}
╰──────────────────────────╯
"""
    return msg, temp, rain_chance

def format_forecast(data: dict) -> str:
    location = data["forecast"]["location"]
    days = data["forecast"]["forecast"]["forecastday"]
    msg = f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  📅 *توقعات {location['name'].upper()}*
┃  3 أيام قادمة
╰━━━━━━━━━━━━━━━━━━━━━━╯

"""
    days_ar = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

    for day in days:
        date = datetime.strptime(day["date"], "%Y-%m-%d")
        day_name = days_ar[date.weekday()]
        d = day["day"]
        condition = translate_condition(d["condition"]["text"])
        uv = d.get("uv", 0)
        uv_emoji = UV_LEVELS.get(int(uv), ("⚪",))[0]

        msg += f"""
┏━━ *{day_name}* | {date.strftime('%d/%m')} ━━┓
┃ ✨ {condition}
┃ 🌡️ `{d['maxtemp_c']:.1f}°` / `{d['mintemp_c']:.1f}°`
┃ 💧 رطوبة: `{d['avghumidity']}%`
┃ 🌧️ أمطار: `{d['daily_chance_of_rain']}%`
┃ ☀️ UV: {uv_emoji} `{uv:.0f}`
┗━━━━━━━━━━━━━━━━━━━━━━━━┛
"""
    return msg

def format_hourly(data: dict) -> str:
    location = data["location"]
    hours = data["forecast"]["forecastday"][0]["hour"]
    city = location['name']

    tz_id = location.get('tz_id') or TIMEZONES.get(city, 'Asia/Aden')
    local_time, _ = get_time_by_tzid(tz_id)
    now_local = get_naive_local_now(tz_id)

    msg = f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  ⏰ *طقس {location['name'].upper()}*
┃  🕐 التوقيت المحلي: {local_time}
╰━━━━━━━━━━━━━━━━━━━━━━╯

"""

    count = 0
    for hour in hours:
        h_time = datetime.strptime(hour["time"], "%Y-%m-%d %H:%M")
        if h_time >= now_local and count < 8:
            condition = translate_condition(hour["condition"]["text"])
            temp = hour['temp_c']
            temp_icon = "🔥" if temp > 35 else "☀️" if temp > 25 else "🌤️" if temp > 15 else "❄️"
            msg += f"┃ `{h_time.strftime('%H:%M')}` {temp_icon} `{temp:.1f}°` | {condition} | 💧`{hour['humidity']}%`\n"
            count += 1

    return msg

def format_compare(data1: dict, data2: dict) -> str:
    c1 = data1["current"]["current"]
    c2 = data2["current"]["current"]
    n1 = data1["current"]["location"]["name"]
    n2 = data2["current"]["location"]["name"]

    return f"""
╭━━━━━━━━━━━━━━━━━━━━━━╮
┃  ⚖️ *مقارنة الطقس*
╰━━━━━━━━━━━━━━━━━━━━━━╯

┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃          ┃ 🏙️ *{n1[:8]}* ┃ 🏙️ *{n2[:8]}* ┃
┣━━━━━━━━━━╋━━━━━━━━━━╋━━━━━━━━━━┫
┃ ✨ الحالة ┃ {translate_condition(c1['condition']['text'])[:12]} ┃ {translate_condition(c2['condition']['text'])[:12]} ┃
┃ 🌡️ الحرارة ┃ `{c1['temp_c']:.1f}°C` ┃ `{c2['temp_c']:.1f}°C` ┃
┃ 🤔 محسوسة ┃ `{c1['feelslike_c']:.1f}°C` ┃ `{c2['feelslike_c']:.1f}°C` ┃
┃ 💧 رطوبة ┃ `{c1['humidity']}%` ┃ `{c2['humidity']}%` ┃
┃ 💨 رياح ┃ `{c1['wind_kph']:.0f}` ┃ `{c2['wind_kph']:.0f}` ┃
┗━━━━━━━━━━┻━━━━━━━━━━┻━━━━━━━━━━┛
"""

ADVANCED_TEXT = (
    "📋 *أوامر متقدمة:*\n\n"
    "`/hourly المدينة` - طقس كل ساعة\n"
    "`/compare` - مقارنة مدينتين\n"
    "`/addfav المدينة` - إضافة مفضلة\n"
    "`/delfav المدينة` - حذف مفضلة\n"
    "`/fav` - عرض المفضلة\n"
    "`/stats` - إحصائيات البوت"
)

async def notify_channel_new_user(context: ContextTypes.DEFAULT_TYPE, user):
    """يرسل اشعاراً للقناة عند انضمام مستخدم جديد لاول مرة"""
    username_line = f"🔗 المعرف: @{user.username}" if user.username else "🔗 المعرف: لا يوجد"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    notify_text = (
        "🆕 *مستخدم جديد انضم للبوت*\n\n"
        f"👤 الاسم: {full_name}\n"
        f"{username_line}\n"
        f"🆔 ID: `{user.id}`\n"
        f"👥 اجمالي المستخدمين الآن: {get_stat_int('total_users', 0)}"
    )
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=notify_text, parse_mode='Markdown')
    except Exception as ex:
        print(f"⚠️ خطأ في ارسال اشعار القناة (تأكد ان البوت مشرف في القناة): {ex}")

# ========== أوامر البوت ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = save_user_data(user.id, user.username, user.first_name, user.last_name)
    update_stats("start")
    update_stats("user")

    if is_new:
        await notify_channel_new_user(context, user)

    keyboard = [
        [InlineKeyboardButton("🇸🇦 السعودية", callback_data="sa"), InlineKeyboardButton("🇾🇪 اليمن", callback_data="yemen")],
        [InlineKeyboardButton("🌍 عربية", callback_data="arab"), InlineKeyboardButton("🌎 عالمية", callback_data="world")],
        [InlineKeyboardButton("⭐ المفضلة", callback_data="fav_list"), InlineKeyboardButton("⚖️ مقارنة", callback_data="compare_start")],
        [InlineKeyboardButton("📋 أوامر متقدمة", callback_data="advanced")]
    ]
    await update.message.reply_text(
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "┃  🌤️ *بوت الطقس الشامل*\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "📌 *المميزات:*\n"
        "• 🏙️ طقس دقيق لجميع مدن العالم\n"
        "• 📅 توقعات 3 أيام\n"
        "• ⏰ توقعات الساعة\n"
        "• ☀️ مؤشر UV وجودة الهواء\n"
        "• ⚖️ مقارنة بين مدينتين\n"
        "• ⭐ حفظ المفضلة\n"
        "• 💡 نصائح ذكية\n\n"
        "🔍 *اكتب اسم مدينتك مباشرة*\n\n"
        "👨‍⚕️ *د/ عاصم النجار*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = get_stat_int("total_users", 0)
    total_weather = get_stat_int("total_weather_requests", 0)
    total_favorites = get_stat_int("total_favorites", 0)
    start_count = get_stat_int("start_count", 0)
    last_updated = get_stat_raw("last_updated", "--")

    await update.message.reply_text(
        f"📊 *إحصائيات البوت*\n\n"
        f"👥 المستخدمين: `{total_users}`\n"
        f"🔍 طلبات الطقس: `{total_weather}`\n"
        f"⭐ المفضلة: `{total_favorites}`\n"
        f"🚀 مرات التشغيل: `{start_count}`\n"
        f"🕐 آخر تحديث: `{last_updated}`",
        parse_mode='Markdown'
    )

async def advanced_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ADVANCED_TEXT, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    city = update.message.text.strip()

    if len(city) < 2 or len(city) > 60:
        return

    save_user_data(user.id, user.username, user.first_name, user.last_name)
    save_city_search(user.id, city)
    update_stats("weather")

    await update.message.chat.send_action('typing')
    data = await fetch_weather(city)

    if not data or not data.get("current") or not data.get("forecast"):
        await update.message.reply_text("❌ المدينة غير موجودة، أو تعذر جلب بيانات التوقعات حاليًا. حاول مرة اخرى.")
        return

    msg, temp, rain = format_current_weather(data)

    keyboard = [
        [InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")],
        [InlineKeyboardButton("⏰ طقس بالساعة", callback_data=f"hr:{city}")],
        [InlineKeyboardButton("⭐ إضافة مفضلة", callback_data=f"addfav:{city}")],
    ]

    await update.message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def hourly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: `/hourly الرياض`")
        return

    city = ' '.join(context.args)
    await update.message.chat.send_action('typing')
    data = await fetch_hourly(city)

    if data:
        msg = format_hourly(data)
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ المدينة غير موجودة")

async def add_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: `/addfav الرياض`")
        return

    city = ' '.join(context.args)
    user_id = update.effective_user.id

    if add_favorite(user_id, city):
        await update.message.reply_text(f"✅ تمت إضافة *{city}* للمفضلة", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⭐ {city} موجودة مسبقاً في المفضلة")

async def del_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: `/delfav الرياض`")
        return

    city = ' '.join(context.args)
    user_id = update.effective_user.id

    if remove_favorite(user_id, city):
        await update.message.reply_text(f"🗑️ تم حذف *{city}* من المفضلة", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ {city} غير موجودة في المفضلة")

async def show_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    favs = load_favorites_for_user(user_id)

    if not favs:
        await update.message.reply_text("⭐ لا توجد مدن مفضلة\nاستخدم `/addfav المدينة` للإضافة")
        return

    keyboard = []
    for city in favs:
        keyboard.append([InlineKeyboardButton(f"🏙️ {city}", callback_data=f"city:{city}")])

    await update.message.reply_text(
        "⭐ *مدنك المفضلة:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def compare_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚖️ *مقارنة مدينتين*\n\nأرسل اسم *المدينة الأولى:*",
        parse_mode='Markdown'
    )
    return COMPARE_CITY1

async def compare_city1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['compare_city1'] = update.message.text.strip()
    await update.message.reply_text("✅ الآن أرسل اسم *المدينة الثانية:*", parse_mode='Markdown')
    return COMPARE_CITY2

async def compare_city2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city1 = context.user_data['compare_city1']
    city2 = update.message.text.strip()

    await update.message.chat.send_action('typing')
    data1 = await fetch_weather(city1)
    data2 = await fetch_weather(city2)

    if data1 and data1.get("current") and data2 and data2.get("current"):
        msg = format_compare(data1, data2)
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ إحدى المدينتين غير موجودة")

    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("fc:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text("⏳ جاري جلب التوقعات...")
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("forecast"):
            msg = format_forecast(weather_data)
            back = [[InlineKeyboardButton("🔙 الطقس الحالي", callback_data=f"now:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(back), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ تعذر جلب التوقعات حاليًا، حاول لاحقًا")

    elif data.startswith("now:"):
        city = data.split(":", 1)[1]
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("current") and weather_data.get("forecast"):
            msg, _, _ = format_current_weather(weather_data)
            keyboard = [
                [InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")],
                [InlineKeyboardButton("⏰ طقس بالساعة", callback_data=f"hr:{city}")],
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ بيانات المدينة غير متوفرة حالياً")

    elif data.startswith("hr:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text("⏳ جاري جلب توقعات الساعات...")
        weather_data = await fetch_hourly(city)
        if weather_data:
            msg = format_hourly(weather_data)
            back = [[InlineKeyboardButton("🔙 الطقس الحالي", callback_data=f"now:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(back), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ تعذر جلب توقعات الساعات حاليًا")

    elif data.startswith("addfav:"):
        city = data.split(":", 1)[1]
        user_id = query.from_user.id
        if add_favorite(user_id, city):
            await query.answer(f"✅ تمت إضافة {city} للمفضلة")
        else:
            await query.answer(f"⭐ {city} موجودة مسبقاً")

    elif data == "fav_list":
        user_id = query.from_user.id
        favs = load_favorites_for_user(user_id)
        if not favs:
            await query.edit_message_text("⭐ لا توجد مدن مفضلة", parse_mode='Markdown')
        else:
            keyboard = []
            for city in favs:
                keyboard.append([InlineKeyboardButton(f"🏙️ {city}", callback_data=f"city:{city}")])
            keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="menu")])
            await query.edit_message_text("⭐ *مدنك المفضلة:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "advanced":
        await query.edit_message_text(ADVANCED_TEXT, parse_mode='Markdown')

    elif data in ["sa", "arab", "world", "yemen"]:
        groups = {
            "sa": ("🇸🇦 السعودية", ["الرياض", "جدة", "مكة", "المدينة", "الدمام", "أبها", "تبوك", "بريدة", "حائل", "نجران", "الطائف", "ينبع"]),
            "arab": ("🌍 عربية", ["القاهرة", "الإسكندرية", "دبي", "أبوظبي", "الدوحة", "مسقط", "الكويت", "بغداد", "عمّان", "بيروت", "الخرطوم", "تونس"]),
            "world": ("🌎 عالمية", ["لندن", "باريس", "نيويورك", "طوكيو", "برلين", "روما", "مدريد", "موسكو", "إسطنبول", "كوالالمبور", "جاكرتا", "سيدني"]),
            "yemen": ("🇾🇪 اليمن", ["صنعاء", "عدن", "تعز", "الحديدة", "إب", "المكلا", "ذمار", "عمران", "صعدة", "البيضاء", "مأرب", "سيئون"])
        }
        title, cities = groups[data]
        keyboard = []
        row = []
        for i, city in enumerate(cities):
            row.append(InlineKeyboardButton(city, callback_data=f"city:{city}"))
            if (i + 1) % 2 == 0:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="menu")])
        await query.edit_message_text(f"*{title}*\nاختر مدينة:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data.startswith("city:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text(f"⏳ جاري جلب طقس *{city}*...", parse_mode='Markdown')
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("current") and weather_data.get("forecast"):
            msg, _, _ = format_current_weather(weather_data)
            keyboard = [
                [InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")],
                [InlineKeyboardButton("⏰ طقس بالساعة", callback_data=f"hr:{city}")],
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ بيانات المدينة غير متوفرة حالياً")

    elif data == "menu":
        keyboard = [
            [InlineKeyboardButton("🇸🇦 السعودية", callback_data="sa"), InlineKeyboardButton("🇾🇪 اليمن", callback_data="yemen")],
            [InlineKeyboardButton("🌍 عربية", callback_data="arab"), InlineKeyboardButton("🌎 عالمية", callback_data="world")],
            [InlineKeyboardButton("⭐ المفضلة", callback_data="fav_list")],
        ]
        await query.edit_message_text("🏠 *القائمة الرئيسية*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    compare_conv = ConversationHandler(
        entry_points=[CommandHandler("compare", compare_start), CallbackQueryHandler(compare_start, pattern="^compare_start$")],
        states={
            COMPARE_CITY1: [MessageHandler(filters.TEXT & ~filters.COMMAND, compare_city1)],
            COMPARE_CITY2: [MessageHandler(filters.TEXT & ~filters.COMMAND, compare_city2)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("hourly", hourly_cmd))
    app.add_handler(CommandHandler("addfav", add_fav))
    app.add_handler(CommandHandler("delfav", del_fav))
    app.add_handler(CommandHandler("fav", show_fav))
    app.add_handler(CommandHandler("help", advanced_commands))
    app.add_handler(compare_conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
