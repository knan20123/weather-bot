import os
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN", "8244290417:AAFyZ2lK7fMEOxvW5wv98HfK8M8gRnUKyo4")
API_KEY = os.getenv("API_KEY", "70db0e7c65784b59b8d24440260207")
BASE_URL = "https://api.weatherapi.com/v1"

CONDITION_MAP = {
    "Sunny": "☀️ مشمس", "Clear": "🌙 صافي",
    "Partly cloudy": "🌤️ غائم جزئياً", "Partly Cloudy": "🌤️ غائم جزئياً",
    "Cloudy": "☁️ غائم", "Overcast": "☁️ غائم كلياً",
    "Mist": "🌫️ ضباب خفيف", "Fog": "🌫️ ضباب",
    "Patchy rain possible": "🌦️ أمطار متفرقة محتملة",
    "Light rain": "🌧️ أمطار خفيفة", "Moderate rain": "🌧️ أمطار متوسطة",
    "Heavy rain": "🌧️ أمطار غزيرة", "Light drizzle": "🌦️ رذاذ خفيف",
    "Light rain shower": "🌧️ زخات مطر خفيفة",
    "Thunderstorm": "⛈️ عاصفة رعدية",
    "Light snow": "🌨️ ثلج خفيف", "Heavy snow": "❄️ ثلج كثيف",
    "Blizzard": "🌨️ عاصفة ثلجية", "Sandstorm": "🌪️ عاصفة رملية",
}

def translate_condition(condition: str) -> str:
    return CONDITION_MAP.get(condition, f"🌡️ {condition}")

async def fetch_weather(city: str) -> dict | None:
    async with aiohttp.ClientSession() as session:
        try:
            params = {"key": API_KEY, "q": city, "aqi": "no", "lang": "ar"}
            async with session.get(f"{BASE_URL}/current.json", params=params, timeout=10) as resp:
                if resp.status == 200:
                    current_data = await resp.json()
                else:
                    return None
            params2 = {"key": API_KEY, "q": city, "days": 3, "aqi": "no", "lang": "ar"}
            async with session.get(f"{BASE_URL}/forecast.json", params=params2, timeout=10) as resp:
                if resp.status == 200:
                    forecast_data = await resp.json()
                else:
                    forecast_data = None
            return {"current": current_data, "forecast": forecast_data}
        except Exception:
            return None

def format_current_weather(data: dict) -> str:
    c = data["current"]["current"]
    f = data["forecast"]["forecast"]["forecastday"][0]
    astro = f["astro"]
    condition = translate_condition(c["condition"]["text"])
    return f"""
🏙️ *{data["current"]["location"]["name"].upper()}*، {data["current"]["location"]["country"]}

{condition}
🌡️ *الحرارة:* `{c["temp_c"]:.1f}°C`
🤔 *المحسوسة:* `{c["feelslike_c"]:.1f}°C`
💧 *الرطوبة:* `{c["humidity"]}%`
💨 *الرياح:* `{c["wind_kph"]:.0f} كم/س`

📊 *اليوم:*
⬆️ العظمى: `{f["day"]["maxtemp_c"]:.1f}°C`
⬇️ الصغرى: `{f["day"]["mintemp_c"]:.1f}°C`
🌧️ الأمطار: `{f["day"]["daily_chance_of_rain"]}%`

🌅 *الشروق:* `{astro["sunrise"]}`
🌇 *الغروب:* `{astro["sunset"]}`
"""

def format_forecast(data: dict) -> str:
    city = data["forecast"]["location"]["name"]
    days = data["forecast"]["forecast"]["forecastday"]
    msg = f"📅 *توقعات {city.upper()}* | 3 أيام\n\n"
    days_ar = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    for i, day in enumerate(days):
        date = datetime.strptime(day["date"], "%Y-%m-%d")
        day_name = days_ar[date.weekday()]
        condition = translate_condition(day["day"]["condition"]["text"])
        msg += f"*{day_name}* {date.strftime('%d/%m')}\n{condition}\n"
        msg += f"⬆️ `{day['day']['maxtemp_c']:.1f}°C` ⬇️ `{day['day']['mintemp_c']:.1f}°C` | 🌧️ `{day['day']['daily_chance_of_rain']}%`\n\n"
    return msg

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇸🇦 مدن السعودية", callback_data="sa")],
        [InlineKeyboardButton("🌍 مدن عربية", callback_data="arab")],
        [InlineKeyboardButton("🌎 مدن عالمية", callback_data="world")],
    ]
    await update.message.reply_text(
        "🌤️ *بوت الطقس الاحترافي*\n\n🔍 اكتب اسم مدينتك مباشرة\nمثال: _الرياض_ أو _دبي_ أو _London_",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
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
    msg = format_current_weather(data)
    keyboard = [[InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
            msg = format_current_weather(weather_data)
            keyboard = [[InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    elif data in ["sa", "arab", "world"]:
        groups = {
            "sa": ("🇸🇦 السعودية", ["الرياض", "جدة", "مكة", "المدينة", "الدمام", "أبها", "تبوك", "بريدة"]),
            "arab": ("🌍 عربية", ["القاهرة", "دبي", "الدوحة", "مسقط", "بغداد", "عمّان", "بيروت", "الخرطوم"]),
            "world": ("🌎 عالمية", ["لندن", "باريس", "نيويورك", "طوكيو", "برلين", "روما", "مدريد", "موسكو"])
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
            msg = format_current_weather(weather_data)
            keyboard = [[InlineKeyboardButton("📅 توقعات 3 أيام", callback_data=f"fc:{city}")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    elif data == "menu":
        keyboard = [
            [InlineKeyboardButton("🇸🇦 مدن السعودية", callback_data="sa")],
            [InlineKeyboardButton("🌍 مدن عربية", callback_data="arab")],
            [InlineKeyboardButton("🌎 مدن عالمية", callback_data="world")],
        ]
        await query.edit_message_text("🏠 *القائمة الرئيسية*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
