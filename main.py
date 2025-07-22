

import os
import logging
import asyncio
from typing import Optional
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

from database import init_db, save_guide, get_all_guides, get_guides_count

# הגדרת לוגים
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# הגדרות
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_USERNAME = '@AndroidAndAI'  # שם הערוץ
PORT = int(os.getenv('PORT', '8000'))
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

# טקסט ערכת התחלה
START_TEXT = """👋 שלום וברוך הבא לערוץ!

אם זו הפעם הראשונה שלך פה – הכנתי לך ערכת התחלה מסודרת 🎁

מה תמצא כאן?
📌 מדריכים שימושיים בעברית
🧰 כלים מומלצים (AI, מדריכים לאנדרואיד, בוטים)
💡 רעיונות לפרויקטים אמיתיים
📥 טופס לשיתוף אנונימי של כלים או מחשבות

בחר מה שתרצה מתוך הכפתורים למטה ⬇️"""

# כפתורי ערכת התחלה
START_BUTTONS = [
    [InlineKeyboardButton("🧹 מדריך ניקוי מטמון (סמסונג)", url="https://t.me/AndroidAndAI/17")],
    [InlineKeyboardButton("🧠 מה ChatGPT באמת זוכר עליכם?", url="https://t.me/AndroidAndAI/20")],
    [InlineKeyboardButton("💸 טריק להנחה ל-GPT", url="https://t.me/AndroidAndAI/23")],
    [InlineKeyboardButton("📝 טופס שיתוף אנונימי", url="https://oa379okv.forms.app/untitled-form")],
    [InlineKeyboardButton("📚 כל המדריכים", callback_data="show_guides")]
]

# הודעה במקרה שאין מדריכים
NO_GUIDES_TEXT = "🤷‍♂️ לא נמצאו מדריכים כרגע.\n\nנסה מאוחר יותר, או תבדוק אם פורסמו פוסטים בערוץ."


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """פקודת /start - שליחת ערכת התחלה"""
    keyboard = InlineKeyboardMarkup(START_BUTTONS)
    
    await update.message.reply_text(
        START_TEXT,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


async def guides_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """פקודת /מדריכים - הצגת רשימת מדריכים"""
    guides = await get_all_guides()
    
    if not guides:
        await update.message.reply_text(NO_GUIDES_TEXT)
        return
    
    # בניית רשימת המדריכים
    guides_text = "📚 **כל המדריכים שלנו:**\n\n"
    
    for guide in guides:
        # חיתוך כותרת אם היא ארוכה מדי
        title = guide['title'][:60] + "..." if len(guide['title']) > 60 else guide['title']
        guides_text += f"• {title}\n🔗 https://t.me/AndroidAndAI/{guide['message_id']}\n\n"
    
    guides_text += f"📊 **סה\"כ {len(guides)} מדריכים**"
    
    await update.message.reply_text(
        guides_text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """טיפול בלחיצות על כפתורים"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "show_guides":
        guides = await get_all_guides()
        
        if not guides:
            await query.edit_message_text(NO_GUIDES_TEXT)
            return
        
        # בניית רשימת המדריכים
        guides_text = "📚 **כל המדריכים שלנו:**\n\n"
        
        for guide in guides:
            # חיתוך כותרת אם היא ארוכה מדי
            title = guide['title'][:60] + "..." if len(guide['title']) > 60 else guide['title']
            guides_text += f"• {title}\n🔗 https://t.me/AndroidAndAI/{guide['message_id']}\n\n"
        
        guides_text += f"📊 **סה\"כ {len(guides)} מדריכים**"
        
        # כפתור חזרה
        back_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ חזרה לערכת התחלה", callback_data="back_to_start")]
        ])
        
        await query.edit_message_text(
            guides_text,
            reply_markup=back_button,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    
    elif query.data == "back_to_start":
        keyboard = InlineKeyboardMarkup(START_BUTTONS)
        await query.edit_message_text(
            START_TEXT,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


def should_save_post(message) -> bool:
    """בדיקה האם לשמור את הפוסט"""
    
    # דילוג על סקרים
    if message.poll:
        return False
    
    # דילוג על פוסטים עם #skip
    text = message.text or message.caption or ""
    if "#skip" in text.lower():
        return False
    
    # דילוג על פוסטים קצרים מדי
    if len(text.strip()) < 50:
        return False
    
    # דילוג על פורוורדים גרידא מערוצים אחרים
    if message.forward_from_chat and not text.strip():
        return False
    
    return True


def extract_title_from_message(message) -> str:
    """חילוץ כותרת מהודעה"""
    text = message.text or message.caption or ""
    
    # ניקוי הטקסט מאמוג'י ותגיות
    lines = text.strip().split('\n')
    
    for line in lines:
        # נקה אמוג'י מהתחלה
        clean_line = ''.join(char for char in line if not char.encode('utf-8').startswith(b'\xf0\x9f'))
        clean_line = clean_line.strip()
        
        # אם השורה לא ריקה ולא מתחילה ב-# או @
        if clean_line and not clean_line.startswith(('#', '@', 'https://', 'http://')):
            return clean_line[:100]  # מקסימום 100 תווים לכותרת
    
    # אם לא נמצאה כותרת מתאימה
    return text[:50] + "..." if len(text) > 50 else text


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """טיפול בפוסטים חדשים בערוץ"""
    message = update.channel_post
    
    if not message:
        return
    
    # בדיקה האם הפוסט מהערוץ הנכון
    if message.chat.username != CHANNEL_USERNAME.replace('@', ''):
        return
    
    # בדיקה האם לשמור את הפוסט
    if not should_save_post(message):
        logger.info(f"מתעלם מפוסט {message.message_id} (לא עומד בקריטריונים)")
        return
    
    # חילוץ כותרת
    title = extract_title_from_message(message)
    
    if not title:
        logger.info(f"לא ניתן לחלץ כותרת מפוסט {message.message_id}")
        return
    
    # שמירה במסד הנתונים
    try:
        await save_guide(
            message_id=message.message_id,
            title=title,
            date_created=message.date
        )
        logger.info(f"נשמר מדריך חדש: {title} (ID: {message.message_id})")
        
        # הודעה לוג (אופציונלי)
        guides_count = await get_guides_count()
        logger.info(f"סה\"כ מדריכים במערכת: {guides_count}")
        
    except Exception as e:
        logger.error(f"שגיאה בשמירת מדריך {message.message_id}: {e}")


async def scan_existing_posts(application: Application) -> None:
    """סריקה ראשונית של פוסטים קיימים בערוץ (אופציונלי)"""
    logger.info("מתחיל סריקת פוסטים קיימים...")
    
    # כאן אפשר להוסיף קוד לסריקת היסטוריית הערוץ
    # אבל זה דורש הרשאות מיוחדות וייתכן שלא נצטרך
    
    logger.info("סריקת פוסטים קיימים הושלמה")


async def setup_webhook(application: Application) -> None:
    """הגדרת webhook לRender"""
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook מוגדר ל: {webhook_url}")
    else:
        logger.warning("WEBHOOK_URL לא מוגדר - הבוט יפעל במוד polling")


def main():
    """הפעלת הבוט"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN לא מוגדר!")
        return
    
    # יצירת האפליקציה
    application = Application.builder().token(BOT_TOKEN).build()
    
    # רישום handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("מדריכים", guides_command))
    application.add_handler(CommandHandler("guides", guides_command))  # גם באנגלית
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # handler לפוסטים בערוץ
    application.add_handler(MessageHandler(
        filters.ChatType.CHANNEL, 
        handle_channel_post
    ))
    
    # אתחול מסד הנתונים
    asyncio.run(init_db())
    
    if WEBHOOK_URL:
        # הרצה עם webhook (לRender)
        asyncio.run(setup_webhook(application))
        logger.info(f"מתחיל webhook server על פורט {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="/webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook"
        )
    else:
        # הרצה עם polling (לפיתוח מקומי)
        logger.info("מתחיל בוט במוד polling...")
        application.run_polling(allowed_updates=['message', 'callback_query', 'channel_post'])


if __name__ == '__main__':
    main()
