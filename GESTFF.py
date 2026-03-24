import sqlite3
import json
import io
import asyncio
import httpx
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- CONFIGURATION ---
TOKEN = '8184247502:AAGsLkJUALJ4Q6KW5KG1OijuwUmTeHTrbh0'
ADMIN_ID = 6328650912 
API_URL = "https://ffgestapisrc.vercel.app/gen"
CHANNELS = ["@KAMOD_CODEX", "@KAMOD_CODEX_BACKUP", "@tufan95aura"]

# Conversation States
(GEN_REGION, GEN_NAME, GEN_COUNT, REDEEM_INP, 
 AD_BAL_ID, AD_BAL_AMT, 
 AD_CODE_NAME, AD_CODE_VAL, AD_CODE_LIM, 
 AD_BROADCAST) = range(10)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('kamod_bot.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    c.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 20, ref_by INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, value INTEGER, uses_left INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS redeemed (user_id INTEGER, code TEXT, PRIMARY KEY(user_id, code))')
    conn.commit()
    conn.close()

def db_query(query, params=(), fetchone=False, fetchall=False):
    try:
        conn = sqlite3.connect('kamod_bot.db', check_same_thread=False)
        c = conn.cursor()
        c.execute(query, params)
        res = None
        if fetchone: res = c.fetchone()
        elif fetchall: res = c.fetchall()
        conn.commit()
        conn.close()
        return res
    except Exception as e:
        print(f"Database Error: {e}")
        return None

# --- RENDER KEEP ALIVE ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running!")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- UTILITY ---
async def is_subscribed(bot, user_id):
    if user_id == ADMIN_ID: return True
    for ch in CHANNELS:
        try:
            m = await bot.get_chat_member(ch, user_id)
            if m.status in ['left', 'kicked']: return False
        except: return False
    return True

def get_main_kb(uid):
    kb = [["🔥 GENERATE ACCOUNTS"], ["💰 BALANCE", "🎁 REDEEM"], ["👤 OWNER", "👥 REFER"]]
    if uid == ADMIN_ID: kb.append(["🛠 ADMIN PANEL"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    # Check User in DB
    user = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)
    if not user:
        args = context.args
        ref_id = int(args[0]) if args and args[0].isdigit() and int(args[0]) != uid else None
        db_query("INSERT INTO users (user_id, balance, ref_by) VALUES (?, 20, ?)", (uid, 20, ref_id))
        if ref_id:
            db_query("UPDATE users SET balance = balance + 20 WHERE user_id=?", (ref_id,))
            try: await context.bot.send_message(ref_id, "🎁 તમારા રેફરલ લિંકથી કોઈ જોડાયું! +20 Coins મળ્યા.")
            except: pass
        bal = 20
    else:
        bal = user[0]

    if not await is_subscribed(context.bot, uid):
        btns = [[InlineKeyboardButton(f"Join {c}", url=f"https://t.me/{c[1:]}")] for c in CHANNELS]
        btns.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
        await update.message.reply_text("❌ બોટ વાપરવા માટે ચેનલ જોઈન કરો!", reply_markup=InlineKeyboardMarkup(btns))
        return
    
    await update.message.reply_text(f"👋 નમસ્તે!\nતમારી પાસે `{bal}` Coins છે.", reply_markup=get_main_kb(uid))

async def gen_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)
    
    if not user or user[0] < 1:
        await update.message.reply_text("❌ તમારી પાસે પૂરતા કોઈન્સ નથી!")
        return ConversationHandler.END
        
    await update.message.reply_text("🌍 કયો દેશ? (દા.ત. IND, BRA, ID):")
    return GEN_REGION

async def gen_get_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['g_region'] = update.message.text
    await update.message.reply_text("👤 નામ લખો:")
    return GEN_NAME

async def gen_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['g_name'] = update.message.text
    await update.message.reply_text("🔢 સંખ્યા લખો (દા.ત. 1):")
    return GEN_COUNT

async def gen_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        count = int(update.message.text)
        user = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)
        if count > user[0] or count <= 0:
            await update.message.reply_text("❌ ખોટી સંખ્યા!")
            return ConversationHandler.END
        
        msg = await update.message.reply_text("⏳ જનરેટ થઈ રહ્યું છે...")
        results = []
        async with httpx.AsyncClient() as client:
            for _ in range(count):
                res = await client.get(API_URL, params={'name': context.user_data['g_name'], 'region': context.user_data['g_region']}, timeout=15)
                if res.status_code == 200: results.append(res.json())
        
        if results:
            db_query("UPDATE users SET balance = balance - ? WHERE user_id=?", (len(results), uid))
            file_io = io.BytesIO(json.dumps(results, indent=4).encode())
            file_io.name = "accounts.json"
            await update.message.reply_document(document=file_io, caption=f"✅ {len(results)} એકાઉન્ટ્સ સફળ!")
        else:
            await update.message.reply_text("❌ સર્વર એરર!")
        await msg.delete()
    except:
        await update.message.reply_text("❌ કાંઈક ભૂલ થઈ!")
    return ConversationHandler.END

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text
    
    if txt == "💰 BALANCE":
        user = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)
        bal = user[0] if user else 0
        await update.message.reply_text(f"💰 તમારું બેલેન્સ: `{bal}` Coins")
    elif txt == "👤 OWNER":
        await update.message.reply_text("👤 માલિક: @kamod90")
    elif txt == "👥 REFER":
        bot = await context.bot.get_me()
        await update.message.reply_text(f"🔗 રેફરલ લિંક: https://t.me/{bot.username}?start={uid}")
    elif txt == "🎁 REDEEM":
        await update.message.reply_text("🎁 પ્રોમો કોડ વાપરવા માટે /redeem લખો.")

# --- MAIN ---
async def run_bot():
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    
    # 1. Conversation Handler (સૌથી પહેલા)
    gen_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔥 GENERATE ACCOUNTS$'), gen_start)],
        states={
            GEN_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_get_region)],
            GEN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_get_name)],
            GEN_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_process)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        allow_reentry=True
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(start, pattern="verify"))
    app.add_handler(gen_conv) # આને handle_text પહેલા રાખવો જરૂરી છે
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot started...")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        while True: await asyncio.sleep(10)

if __name__ == '__main__':
    asyncio.run(run_bot())