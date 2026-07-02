import os
import asyncio
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import json

# ========== الإعدادات ==========
TOKEN = os.getenv("TELEGRAM_TOKEN", "8244290417:AAFyZ2lK7fMEOxvW5wv98HfK8M8gRnUKyo4")
API_KEY = os.getenv("API_KEY", "70db0e7c65784b59b8d24440260207")
BASE_URL = "https://api.weatherapi.com/v1"

# ========== حالات المحادثة ==========
FAVORITE_ADD, COMPARE_CITY1, COMPARE_CITY2, ALERT_CITY = range(4)

# ========== ملف المفضلة ==========
FAV_FILE = "favorites.json"

def load_favorites():
    try:
        with open(FAV_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_favorites(data):
    with open(FAV_FILE, "w") as f:
        json.dump(data, f)

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
    "Light drizzle": "🌦️ رذاذ خفيف", "Patchy light drizzle": "🌦️ رذاذ خفيف متفرق",
    "Light rain shower": "🌧️ زخات مطر خفيفة",
    "Moderate rain at times": "🌧️ أمطار متوسطة أحياناً",
    "Heavy rain at times": "🌧️ أمطار غزيرة أحياناً",
    "Thunderstorm": "⛈️ عاصفة رعدية",
    "Patchy light snow": "🌨️ ثلج خفيف متفرق",
    "Light snow": "🌨️ ثلج خفيف", "Moderate snow": "🌨️ ثلج متوسط",
    "Heavy snow": "❄️ ثلج كثيف", "Blizzard": "🌨️ عاصفة ثلجية",
    "Ice pellets": "🧊 كريات جليدية", "Light sleet": "🌨️ صقيع خفيف",
    "Moderate or heavy sleet": "🌨️ صقيع كثيف",
    "Sandstorm": "🌪️ عاصفة رملية", "Dust": "🌪️ غبار",
}

UV_LEVELS = {1: "🟢 منخفض", 2: "🟢 منخفض", 3: "🟡 متوسط", 4: "🟡 متوسط", 5: "🟡 متوسط", 6: "🟠 عالي", 7: "🟠 عالي", 8: "🔴 عالي جداً", 9: "🔴 عالي جداً", 10: "🟣 خطير", 11: "🟣 خطير"}
UV_ADVICE = {1: "لا حاجة لحماية", 3: "استخدم واقي شمس", 6: "تجنب الشمس وقت الظهيرة", 8: "حماية قصوى ضرورية", 11: "لا تخرج إلا للضرورة"}

def translate_condition(condition: str) -> str:
    return CONDITION_MAP.get(condition, f"🌡️ {condition}")

def get_weather_advice(code: int, temp: float, rain: int, uv: float) -> str:
    tips = []
    if rain > 70: tips.append("🌂 احمل مظلة")
    elif rain > 30: tips.append("🌂 قد تحتاج مظلة")
    if temp > 40: tips.append("🔥 حرارة شديدة - اشرب ماء بكثرة")
    elif temp > 35: tips.append("☀️ حار - تجنب التعرض المباشر للشمس")
    elif temp < 5: tips.append("🥶 بارد جداً - ارتدِ ملابس ثقيلة")
    elif temp < 15: tips.append("🧥 الجو بارد - خذ معطفاً")
    else: tips.append("🌤️ جو معتدل ومناسب للخروج")
    if uv >= 6: tips.append("🧴 استخدم واقي شمس")
    if rain < 20 and 15 <= temp <= 30: tips.append("🏃 جو مناسب للرياضة")
    return " | ".join(tips)

# ========== دوال جلب البيانات ==========
async def fetch_weather(city: str) -> dict | None:
    async with aiohttp.ClientSession() as session:
        try:
            params = {"key": API_KEY, "q": city, "aqi": "yes", "lang": "ar"}
            async with session.get(f"{BASE_URL}/current.json", params=params, timeout=10) as resp:
                if resp.status == 200:
                    current_data = await resp.json()
                else:
                    return None
            
            params2 = {"key": API_KEY, "q": city, "days": 3, "aqi": "yes", "lang": "ar"}
            async with session.get(f"{BASE_URL}/forecast.json", params=params2, timeout=10) as resp:
                if resp.status == 200:
                    forecast_data = await resp.json()
                else:
                    forecast_data = None
            
            return {"current": current_data, "forecast": forecast_data}
        except:
            return None

async def fetch_hourly(city: str) -> dict | None:
    async with aiohttp.ClientSession() as session:
        try:
            params = {"key": API_KEY, "q": city, "hours": 12, "aqi": "no", "lang": "ar"}
            async with session.get(f"{BASE_URL}/forecast.json", params=params, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except:
            return None

# ========== تنسيق الرسائل ==========
def format_current_weather(data: dict) -> tuple:
    c = data["current"]["current"]
    f = data["forecast"]["forecast"]["forecastday"][0]
    astro = f["astro"]
    condition = translate_condition(c["condition"]["text"])
    
    aqi = data["current"]["current"].get("air_quality", {})
    aqi_text = ""
    if aqi:
        pm25 = aqi.get("pm2_5", 0)
        if pm25 <= 50: aqi_emoji, aqi_level = "🟢", "جيد"
        elif pm25 <= 100: aqi_emoji, aqi_level = "🟡", "معتدل"
        elif pm25 <= 150: aqi_emoji, aqi_level = "🟠", "غير صحي"
        else: aqi_emoji, aqi_level = "🔴", "خطير"
        aqi_text = f"\n🏭 *جودة الهواء:* {aqi_emoji} `{aqi_level}` (PM2.5: {pm25})"
    
    uv = float(c.get("uv", 0))
    uv_level = UV_LEVELS.get(int(uv), "⚪ غير معروف")
    uv_advice = ""
    for threshold, advice in UV_ADVICE.items():
        if uv <= threshold:
            uv_advice = advice
            break
    
    advice = get_weather_advice(c["condition"]["code"], c["temp_c"], f["day"]["daily_chance_of_rain"], uv)
    
    msg = f"""
🏙️ *{data["current"]["location"]["name"].upper()}*، {data["current"]["location"]["country"]}

{condition}
🌡️ *الحرارة:* `{c["temp_c"]:.1f}°C` | 🤔 *المحسوسة:* `{c["feelslike_c"]:.1f}°C`
💧 *الرطوبة:* `{c["humidity"]}%` | 💨 *الرياح:* `{c["wind_kph"]:.0f} كم/س`

📊 *اليوم:*
⬆️ العظمى: `{f["day"]["maxtemp_c"]:.1f}°C` ⬇️ الصغرى: `{f["day"]["mintemp_c"]:.1f}°C`
🌧️ الأمطار: `{f["day"]["daily_chance_of_rain"]}%`

☀️ *مؤشر UV:* `{uv}` ({uv_level}) - _{uv_advice}_
{aqi_text}

🌅 *الشروق:* `{astro["sunrise"]}` | 🌇 *الغروب:* `{astro["sunset"]}`

💡 *{advice}*
"""
    return msg, float(c["temp_c"]), f["day"]["daily_chance_of_rain"]

def format_forecast(data: dict) -> str:
    city = data["forecast"]["location"]["name"]
    days = data["forecast"]["forecast"]["forecastday"]
    msg = f"📅 *توقعات {city.upper()}* | 3 أيام\n\n"
    days_ar = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    
    for day in days:
        date = datetime.strptime(day["date"], "%Y-%m-%d")
        day_name = days_ar[date.weekday()]
        condition = translate_condition(day["day"]["condition"]["text"])
        uv = day["day"].get("uv", 0)
        msg += f"*{day_name}* {date.strftime('%d/%m')}\n{condition}\n"
        msg += f"⬆️ `{day['day']['maxtemp_c']:.1f}°C` ⬇️ `{day['day']['mintemp_c']:.1f}°C`"
        msg += f" | 🌧️ `{day['day']['daily_chance_of_rain']}%` | ☀️ UV: `{uv}`\n\n"
    
    return msg

def format_hourly(data: dict) -> str:
    city = data["location"]["name"]
    hours = data["forecast"]["forecastday"][0]["hour"]
    now = datetime.now()
    
    msg = f"⏰ *طقس {city.upper()}* | الساعات القادمة\n\n"
    
    count = 0
    for hour in hours:
        h_time = datetime.strptime(hour["time"], "%Y-%m-%d %H:%M")
        if h_time >= now and count < 8:
            condition = translate_condition(hour["condition"]["text"])
            msg += f"*{h_time.strftime('%H:%M')}* | {condition} | `{hour['temp_c']:.1f}°C`"
            msg += f" | 🌧️ `{hour['chance_of_rain']}%`\n"
            count += 1
    
    return msg

def format_compare(data1: dict, data2: dict) -> str:
    c1 = data1["current"]["current"]
    c2 = data2["current"]["current"]
    n1 = data1["current"]["location"]["name"]
    n2 = data2["current"]["location"]["name"]
    
    return f"""
⚖️ *مقارنة الطقس*

🏙️ *{n1}*  |  🏙️ *{n2}*
{translate_condition(c1['condition']['text'])}  |  {translate_condition(c2['condition']['text'])}
🌡️ `{c1['temp_c']:.1f}°C`  |  🌡️ `{c2['temp_c']:.1f}°C`
💧 `{c1['humidity']}%`  |  💧 `{c2['humidity']}%`
💨 `{c1['wind_kph']:.0f} كم/س`  |  💨 `{c2['wind_kph']:.0f} كم/س`
"""

# ========== أوامر البوت ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇸🇦 مدن السعودية", callback_data="sa")],
        [InlineKeyboardButton("🇾🇪 مدن اليمن", callback_data="yemen")],
        [InlineKeyboardButton("🌍 مدن عربية", callback_data="arab")],
        [InlineKeyboardButton("🌎 مدن عالمية", callback_data="world")],
        [InlineKeyboardButton("⭐ مدني المفضلة", callback_data="fav_list")],
        [InlineKeyboardButton("⚖️ مقارنة مدن", callback_data="compare_start")],
        [InlineKeyboardButton("🔔 تنبيه الأمطار", callback_data="alert_start")],
        [InlineKeyboardButton("📋 أوامر متقدمة", callback_data="advanced")]
    ]
    await update.message.reply_text(
        "🌤️ *مرحباً بك في بوت الطقس الشامل!*\n\n"
        "📌 *ماذا يقدم لك البوت؟*\n"
        "• 🏙️ طقس دقيق لجميع مدن العالم\n"
        "• 📅 توقعات 3 أيام قادمة\n"
        "• ⏰ توقعات كل ساعة\n"
        "• 🗺️ رادار طقس متحرك\n"
        "• ☀️ مؤشر الأشعة فوق البنفسجية\n"
        "• 🏭 جودة الهواء ومستوى التلوث\n"
        "• ⚖️ مقارنة بين مدينتين\n"
        "• ⭐ حفظ المدن المفضلة\n"
        "• 🌧️ تنبيهات فرص الأمطار\n"
        "• 💡 نصائح ذكية يومية\n\n"
        "🔍 *طريقة الاستخدام:*\n"
        "• اكتب اسم مدينتك مباشرة\n"
        "• استخدم الأزرار للتنقل السريع\n"
        "• تصفح الأوامر المتقدمة للمزيد\n\n"
        "📋 *أوامر سريعة:*\n"
        "`/hourly المدينة` - طقس بالساعة\n"
        "`/compare` - مقارنة مدينتين\n"
        "`/radar المدينة` - خريطة الطقس\n"
        "`/addfav المدينة` - إضافة مفضلة\n"
        "`/delfav المدينة` - حذف مفضلة\n"
        "`/fav` - عرض المفضلة\n\n"
        "🤖 *تم تطوير هذا البوت بواسطة:*\n"
        "👨‍⚕️ *د/ عاصم النجار*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def advanced_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *أوامر متقدمة:*\n\n"
        "`/hourly الرياض` - طقس كل ساعة\n"
        "`/compare` - مقارنة مدينتين\n"
        "`/radar دبي` - رادار الطقس\n"
        "`/addfav مكة` - إضافة للمفضلة\n"
        "`/delfav مكة` - حذف من المفضلة\n"
        "`/fav` - عرض المفضلة\n"
        "`/units` - تغيير الوحدات",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    if len(city) < 2 or len(city) > 60:
        return
    
    await update.message.chat.send_action('typing')
    data = await fetch_weather(city)
    
    if not data or not data.get("current"):
        await update.message.reply_text("❌ المدينة غير موجودة")
        return
    
    msg, temp, rain = format_current_weather(data)
    
    keyboard = [
        [InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")],
        [InlineKeyboardButton("⏰ طقس بالساعة", callback_data=f"hr:{city}")],
        [InlineKeyboardButton("🗺️ رادار الطقس", callback_data=f"radar:{city}")],
        [InlineKeyboardButton("⭐ إضافة مفضلة", callback_data=f"addfav:{city}")],
    ]
    
    if rain > 50:
        keyboard.insert(0, [InlineKeyboardButton("🔔 تنبيهني عند المطر", callback_data=f"alert:{city}")])
    
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

async def radar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: `/radar الرياض`")
        return
    
    city = ' '.join(context.args)
    data = await fetch_weather(city)
    
    if data:
        lat = data["current"]["location"]["lat"]
        lon = data["current"]["location"]["lon"]
        radar_url = f"https://www.ventusky.com/?{lat};{lon};10&winds"
        await update.message.reply_text(
            f"🗺️ *رادار {city}*\n\n[🔗 اضغط لفتح الخريطة]({radar_url})",
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
    else:
        await update.message.reply_text("❌ المدينة غير موجودة")

async def add_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: `/addfav الرياض`")
        return
    
    city = ' '.join(context.args)
    user_id = str(update.effective_user.id)
    favs = load_favorites()
    
    if user_id not in favs:
        favs[user_id] = []
    
    if city in favs[user_id]:
        await update.message.reply_text(f"⭐ {city} موجودة مسبقاً في المفضلة")
    else:
        favs[user_id].append(city)
        save_favorites(favs)
        await update.message.reply_text(f"✅ تمت إضافة *{city}* للمفضلة", parse_mode='Markdown')

async def del_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ استخدم: `/delfav الرياض`")
        return
    
    city = ' '.join(context.args)
    user_id = str(update.effective_user.id)
    favs = load_favorites()
    
    if user_id in favs and city in favs[user_id]:
        favs[user_id].remove(city)
        save_favorites(favs)
        await update.message.reply_text(f"🗑️ تم حذف *{city}* من المفضلة", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ {city} غير موجودة في المفضلة")

async def show_fav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    favs = load_favorites()
    
    if user_id not in favs or not favs[user_id]:
        await update.message.reply_text("⭐ لا توجد مدن مفضلة\nاستخدم `/addfav المدينة` للإضافة")
        return
    
    keyboard = []
    for city in favs[user_id]:
        keyboard.append([InlineKeyboardButton(f"🏙️ {city}", callback_data=f"city:{city}")])
    
    await update.message.reply_text(
        "⭐ *مدنك المفضلة:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def compare_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚖️ *مقارنة مدينتين*\n\n"
        "أرسل اسم *المدينة الأولى:*",
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
    
    if data1 and data2:
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
    
    elif data.startswith("now:"):
        city = data.split(":", 1)[1]
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("current"):
            msg, _, _ = format_current_weather(weather_data)
            keyboard = [
                [InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")],
                [InlineKeyboardButton("⏰ طقس بالساعة", callback_data=f"hr:{city}")],
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith("hr:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text("⏳ جاري جلب توقعات الساعات...")
        weather_data = await fetch_hourly(city)
        if weather_data:
            msg = format_hourly(weather_data)
            back = [[InlineKeyboardButton("🔙 الطقس الحالي", callback_data=f"now:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(back), parse_mode='Markdown')
    
    elif data.startswith("radar:"):
        city = data.split(":", 1)[1]
        weather_data = await fetch_weather(city)
        if weather_data:
            lat = weather_data["current"]["location"]["lat"]
            lon = weather_data["current"]["location"]["lon"]
            radar_url = f"https://www.ventusky.com/?{lat};{lon};10&winds"
            await query.edit_message_text(
                f"🗺️ *رادار {city}*\n\n[🔗 اضغط لفتح الخريطة المتحركة]({radar_url})",
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
    
    elif data.startswith("addfav:"):
        city = data.split(":", 1)[1]
        user_id = str(query.from_user.id)
        favs = load_favorites()
        if user_id not in favs:
            favs[user_id] = []
        if city not in favs[user_id]:
            favs[user_id].append(city)
            save_favorites(favs)
            await query.answer(f"✅ تمت إضافة {city} للمفضلة")
        else:
            await query.answer(f"⭐ {city} موجودة مسبقاً")
    
    elif data == "fav_list":
        user_id = str(query.from_user.id)
        favs = load_favorites()
        if user_id not in favs or not favs[user_id]:
            await query.edit_message_text("⭐ لا توجد مدن مفضلة\nاستخدم `/addfav المدينة` للإضافة", parse_mode='Markdown')
        else:
            keyboard = []
            for city in favs[user_id]:
                keyboard.append([InlineKeyboardButton(f"🏙️ {city}", callback_data=f"city:{city}")])
            keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="menu")])
            await query.edit_message_text("⭐ *مدنك المفضلة:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data == "advanced":
        await query.edit_message_text(
            "📋 *أوامر متقدمة:*\n\n"
            "`/hourly الرياض` - طقس كل ساعة\n"
            "`/compare` - مقارنة مدينتين\n"
            "`/radar دبي` - رادار الطقس\n"
            "`/addfav مكة` - إضافة للمفضلة\n"
            "`/delfav مكة` - حذف من المفضلة\n"
            "`/fav` - عرض المفضلة",
            parse_mode='Markdown'
        )
    
    elif data == "yemen":
        cities = ["صنعاء", "عدن", "تعز", "الحديدة", "إب", "المكلا", "سيئون", "ذمار", "عمران", "صعدة", "البيضاء", "مأرب", "الجوف", "شبوة", "لحج", "الضالع", "أبين", "المهرة", "حضرموت", "سقطرى"]
        title = "🇾🇪 المحافظات اليمنية"
        keyboard = []
        row = []
        for i, city in enumerate(cities):
            row.append(InlineKeyboardButton(city, callback_data=f"city:{city}"))
            if (i + 1) % 2 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="menu")])
        await query.edit_message_text(f"*{title}*\nاختر مدينة:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data in ["sa", "arab", "world", "yemen"]:
        groups = {
            "sa": ("🇸🇦 السعودية", ["الرياض", "جدة", "مكة", "المدينة", "الدمام", "أبها", "تبوك", "بريدة"]),
            "arab": ("🌍 عربية", ["القاهرة", "دبي", "الدوحة", "مسقط", "بغداد", "عمّان", "بيروت", "الخرطوم"]),
            "world": ("🌎 عالمية", ["لندن", "باريس", "نيويورك", "طوكيو", "برلين", "روما", "مدريد", "موسكو"]),
            "yemen": ("🇾🇪 اليمن", ["صنعاء", "عدن", "تعز", "الحديدة", "إب", "المكلا", "سيئون", "ذمار", "عمران", "صعدة", "البيضاء", "مأرب", "الجوف", "شبوة", "لحج", "الضالع", "أبين", "المهرة", "حضرموت", "سقطرى"])
        }
        title, cities = groups[data]
        keyboard = []
        row = []
        for i, city in enumerate(cities):
            row.append(InlineKeyboardButton(city, callback_data=f"city:{city}"))
            if (i + 1) % 2 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 الرئيسية", callback_data="menu")])
        await query.edit_message_text(f"*{title}*\nاختر مدينة:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith("city:"):
        city = data.split(":", 1)[1]
        await query.edit_message_text(f"⏳ جاري جلب طقس *{city}*...", parse_mode='Markdown')
        weather_data = await fetch_weather(city)
        if weather_data and weather_data.get("current"):
            msg, _, _ = format_current_weather(weather_data)
            keyboard = [
                [InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")],
                [InlineKeyboardButton("⏰ طقس بالساعة", callback_data=f"hr:{city}")],
                [InlineKeyboardButton("🗺️ رادار الطقس", callback_data=f"radar:{city}")],
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data == "menu":
        keyboard = [
            [InlineKeyboardButton("🇸🇦 مدن السعودية", callback_data="sa")],
            [InlineKeyboardButton("🇾🇪 مدن اليمن", callback_data="yemen")],
            [InlineKeyboardButton("🌍 مدن عربية", callback_data="arab")],
            [InlineKeyboardButton("🌎 مدن عالمية", callback_data="world")],
            [InlineKeyboardButton("⭐ المفضلة", callback_data="fav_list")],
        ]
        await query.edit_message_text("🏠 *القائمة الرئيسية*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def main():
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
    app.add_handler(CommandHandler("hourly", hourly_cmd))
    app.add_handler(CommandHandler("radar", radar_cmd))
    app.add_handler(CommandHandler("addfav", add_fav))
    app.add_handler(CommandHandler("delfav", del_fav))
    app.add_handler(CommandHandler("fav", show_fav))
    app.add_handler(CommandHandler("help", advanced_commands))
    app.add_handler(compare_conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ البوت الشامل يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
