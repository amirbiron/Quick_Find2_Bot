import logging
import os
import asyncio

# --- Imports for the Web Server ---
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

# --- Imports for the Bot ---
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ChatMemberHandler
from pymongo import MongoClient

# --- Load Environment Variables ---
# Make sure you have a .env file locally for testing, Render will use its own environment variables
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL") # Render provides this automatically

# --- Basic Setup ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- Database Setup ---
try:
    client = MongoClient(MONGO_URI)
    db = client.get_database("QuickFind2BotDB")
    users_collection = db.get_collection("users")
    guides_collection = db.get_collection("guides")
    logging.info("Database initialized successfully")
except Exception as e:
    logging.error(f"Failed to initialize database: {e}")
    # You might want to exit here if the DB is critical
    # exit(1)

# =========================================================================
# Your Bot's Functions (start_command, guides_command, etc.)
# =========================================================================
# Copy your bot functions here exactly as they were
async def start_command(update: Update, context) -> None:
    user = update.effective_user
    users_collection.update_one(
        {"user_id": user.id},
        {"$set": {"first_name": user.first_name, "last_name": user.last_name}},
        upsert=True,
    )
    await update.message.reply_text("ברוך הבא לבוט המדריכים!")

async def guides_command(update: Update, context) -> None:
    await update.message.reply_text("זוהי רשימת המדריכים:")

async def button_callback(update: Update, context) -> None:
    pass # Your logic here

async def handle_message(update: Update, context) -> None:
    pass # Your logic here

async def welcome(update: Update, context) -> None:
    pass # Your logic here

# =========================================================================
# Telegram Bot Application Setup
# =========================================================================
ptb_application = Application.builder().token(BOT_TOKEN).build()

# Register your command and message handlers
ptb_application.add_handler(CommandHandler("start", start_command))
ptb_application.add_handler(CommandHandler("guides", guides_command))
ptb_application.add_handler(CallbackQueryHandler(button_callback))
ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_application.add_handler(ChatMemberHandler(welcome, ChatMemberHandler.CHAT_MEMBER))

# =========================================================================
# Web Server (Starlette) Setup
# This is the "translator" between the web and your bot
# =========================================================================
async def on_startup():
    # This function is called by Uvicorn when the server starts.
    # We use it to set the webhook.
    webhook_path = f"/{BOT_TOKEN.split(':')[-1]}"
    url = f"{WEBHOOK_URL}{webhook_path}"
    await ptb_application.bot.set_webhook(url=url)
    logging.info(f"Webhook set to {url}")

async def on_shutdown():
    # This is called when the server shuts down.
    await ptb_application.bot.delete_webhook()
    logging.info("Webhook deleted")

# This is the main web application instance that Uvicorn will run
app = Starlette(on_startup=[on_startup], on_shutdown=[on_shutdown])

@app.route(f"/{BOT_TOKEN.split(':')[-1]}", methods=["POST"])
async def telegram_webhook(request: Request) -> Response:
    # This function receives the update from Telegram
    data = await request.json()
    update = Update.de_json(data, ptb_application.bot)
    
    # Process the update with your bot
    await ptb_application.process_update(update)
    
    # Return a 200 OK response to Telegram
    return Response(status_code=200)
