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
TOKEN = '8184247502:AAGvaZ6dwmuyEdb_qMs_BBDmlDiq98U7Y7M'
ADMIN_ID = 6328650912 
API_URL = "https://ffgestapisrc.vercel.app/gen"
CHANNELS = [ "@tufan95aura"]

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
    conn = sqlite3.connect('kamod_bot.db')
    c = conn.cursor()
    c.execute(query, params)
    res = None
    if fetchone: res = c.fetchone()
    elif fetchall: res = c.fetchall()
    conn.commit()
    conn.close()
    return res

# --- RENDER PORT BINDING (Keep Alive) ---
# Render ને લાગે કે આ એક વેબસાઈટ છે, એટલે તે બોટને બંધ નહીં કરે.
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running!")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- UTILITY FUNCTIONS ---
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

def get_admin_kb():
    return ReplyKeyboardMarkup([
        ["🎟 Generate Promo", "💰 Add Balance"],
        ["📊 Stats", "📢 Broadcast"],
        ["🔙 Back to Menu"]
    ], resize_keyboard=True)

# --- BOT LOGIC ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    ref_id = int(args[0]) if args and args[0].isdigit() and int(args[0]) != uid else None
    
    if not db_query("SELECT user_id FROM users WHERE user_id=?", (uid,), fetchone=True):
        db_query("INSERT INTO users (user_id, balance, ref_by) VALUES (?, 20, ?)", (uid, 20, ref_id))
        if ref_id:
            db_query("UPDATE users SET balance = balance + 20 WHERE user_id=?", (ref_id,))
            try: await context.bot.send_message(ref_id, "🎁 તમારા રેફરલ લિંકથી કોઈ જોડાયું! +20 Coins મળ્યા.")
            except: pass

    if not await is_subscribed(context.bot, uid):
        btns = [[InlineKeyboardButton(f"Join {c}", url=f"https://t.me/{c[1:]}")] for c in CHANNELS]
        btns.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
        await update.message.reply_text("❌ બોટ વાપરવા માટે બધી ચેનલ જોઈન કરો!", reply_markup=InlineKeyboardMarkup(btns))
        return
    
    bal = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)[0]
    await update.message.reply_text(f"👋 નમસ્તે {update.effective_user.first_name}!\nતમારી પાસે `{bal}` Coins છે.", reply_markup=get_main_kb(uid))

async def verify_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await is_subscribed(context.bot, q.from_user.id):
        await q.answer("✅ સફળ!")
        await q.message.delete()
        await start(q, context)
    else:
        await q.answer("❌ હજુ બધી ચેનલ જોઈન નથી કરી!", show_alert=True)

# --- ACCOUNT GENERATION ---
async def gen_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bal = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)[0]
    if bal < 1:
        await update.message.reply_text("❌ તમારી પાસે કોઈન્સ નથી!")
        return ConversationHandler.END
    await update.message.reply_text("🌍 કયો દેશ? (દા.ત. IND, BRA, ID):")
    return GEN_REGION

async def gen_get_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['g_region'] = update.message.text
    await update.message.reply_text("👤 એકાઉન્ટ માટે નામ લખો:")
    return GEN_NAME

async def gen_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['g_name'] = update.message.text
    await update.message.reply_text("🔢 કેટલા એકાઉન્ટ્સ જોઈએ છે? (નંબરમાં લખો):")
    return GEN_COUNT

async def gen_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    count = int(update.message.text) if update.message.text.isdigit() else 0
    bal = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)[0]
    
    if count > bal or count <= 0:
        await update.message.reply_text("❌ ખોટી સંખ્યા અથવા ઓછું બેલેન્સ!")
        return ConversationHandler.END

    msg = await update.message.reply_text("⏳ જનરેટ થઈ રહ્યું છે...")
    results = []
    
    async with httpx.AsyncClient() as client:
        for _ in range(count):
            try:
                res = await client.get(API_URL, params={'name': context.user_data['g_name'], 'region': context.user_data['g_region']}, timeout=20)
                if res.status_code == 200: results.append(res.json())
            except: pass
    
    if results:
        db_query("UPDATE users SET balance = balance - ? WHERE user_id=?", (len(results), uid))
        file_io = io.BytesIO(json.dumps(results, indent=4).encode())
        file_io.name = f"accounts_{uid}.json"
        await update.message.reply_document(document=file_io, caption=f"✅ સફળ! {len(results)} એકાઉન્ટ્સ.")
    else:
        await update.message.reply_text("❌ સર્વર એરર!")
    await msg.delete()
    return ConversationHandler.END

# --- REDEEM & ADMIN HANDLERS ---
async def redeem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎁 પ્રોમો કોડ લખો:")
    return REDEEM_INP

async def redeem_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    uid = update.effective_user.id
    res = db_query("SELECT value, uses_left FROM promo_codes WHERE code=?", (code,), fetchone=True)
    
    if res and res[1] > 0:
        if not db_query("SELECT 1 FROM redeemed WHERE user_id=? AND code=?", (uid, code), fetchone=True):
            db_query("UPDATE promo_codes SET uses_left = uses_left - 1 WHERE code=?", (code,))
            db_query("INSERT INTO redeemed VALUES (?, ?)", (uid, code))
            db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (res[0], uid))
            await update.message.reply_text(f"✅ સફળ! +{res[0]} કોઈન્સ મળ્યા.")
        else: await update.message.reply_text("❌ તમે આ કોડ વાપરી લીધો છે!")
    else: await update.message.reply_text("❌ કોડ ખોટો છે અથવા પૂરો થઈ ગયો છે.")
    return ConversationHandler.END

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text
    if txt == "💰 BALANCE":
        bal = db_query("SELECT balance FROM users WHERE user_id=?", (uid,), fetchone=True)[0]
        await update.message.reply_text(f"💰 બેલેન્સ: `{bal}` Coins")
    elif txt == "👤 OWNER":
        await update.message.reply_text("👤 માલિક: @kamod90")
    elif txt == "👥 REFER":
        bot = await context.bot.get_me()
        await update.message.reply_text(f"🔗 લિંક: https://t.me/{bot.username}?start={uid}\nદરેક રેફરલ પર 20 કોઈન્સ!")
    elif txt == "🛠 ADMIN PANEL" and uid == ADMIN_ID:
        await update.message.reply_text("🛠 એડમિન મેનુ:", reply_markup=get_admin_kb())
    elif txt == "🔙 Back to Menu":
        await update.message.reply_text("મેનુ:", reply_markup=get_main_kb(uid))

# --- MAIN ASYNC FUNCTION ---
async def run_bot():
    init_db()
    
    # Render Keep-Alive Server ને અલગ થ્રેડમાં ચલાવો
    threading.Thread(target=run_health_server, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^🔥 GENERATE ACCOUNTS$'), gen_start),
            MessageHandler(filters.Regex('^🎁 REDEEM$'), redeem_start),
        ],
        states={
            GEN_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_get_region)],
            GEN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_get_name)],
            GEN_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, gen_process)],
            REDEEM_INP: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_process)],
        },
        fallbacks=[MessageHandler(filters.Regex('^🔙 Back to Menu$'), lambda u,c: ConversationHandler.END)],
        allow_reentry=True
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(verify_cb, pattern="verify"))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("બોટ સફળતાપૂર્વક ચાલુ થઈ ગયો છે...")
    
    # Python 3.12+ અને Render માટે આ પદ્ધતિ સૌથી બેસ્ટ છે
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        # બોટને કાયમ માટે ચાલુ રાખવા માટે
        while True:
            await asyncio.sleep(10)

if __name__ == '__main__':
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        pass