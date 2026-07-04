import os
import json
from datetime import datetime
from telegram import Bot

TOKEN = os.getenv("TELEGRAM_TOKEN", "8244290417:AAFyZ2lK7fMEOxvW5wv98HfK8M8gRnUKyo4")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003554303588")

async def send_notification(user_data, total_users):
    """إرسال إشعار للقناة - يعمل بشكل مستقل وآمن"""
    try:
        bot = Bot(token=TOKEN)
        first_name = user_data.get("first_name", "غير معروف")
        last_name = user_data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip()
        user_id = user_data.get("id", "غير معروف")
        username = user_data.get("username", "")
        username_display = f"@{username}" if username else "بدون يوزر"
        
        message = f"""
🔔 *مستخدم جديد دخل البوت!*

👤 *الاسم:* {full_name}
🆔 *ID:* `{user_id}`
📛 *اليوزر:* {username_display}
🕐 *الوقت:* {datetime.now().strftime('%Y-%m-%d %I:%M %p')}
📊 *إجمالي المستخدمين:* {total_users}
"""
        await bot.send_message(chat_id=CHANNEL_ID, text=message, parse_mode='Markdown')
        print(f"✅ إشعار للقناة: {full_name}")
        return True
    except Exception as e:
        print(f"⚠️ فشل الإشعار (البوت مستمر): {e}")
        return False
