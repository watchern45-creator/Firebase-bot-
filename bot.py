import os
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── ENV VARS ─────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", "0")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]
CHANNELS_RAW = os.environ.get("CHANNELS", "")
CHANNELS = [c.strip() for c in CHANNELS_RAW.split(",") if c.strip()]
REFERRAL_REWARD = int(os.environ.get("REFERRAL_REWARD", "2"))

# ── FIREBASE INIT ─────────────────────────────────────────────────────────────
import json

def init_firebase():
    firebase_key_json = os.environ.get("FIREBASE_KEY_JSON")
    if firebase_key_json:
        key_data = json.loads(firebase_key_json)
        cred = credentials.Certificate(key_data)
    else:
        cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)

init_firebase()
db = firestore.client()

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def check_joined_all(bot, user_id: int) -> bool:
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except Exception:
            return False
    return True

def get_user(user_id: int):
    doc = db.collection("users").document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None

def save_user(user_id: int, data: dict):
    db.collection("users").document(str(user_id)).set(data, merge=True)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance"),
         InlineKeyboardButton("🔗 Refer", callback_data="refer")],
        [InlineKeyboardButton("👥 My Referrals", callback_data="my_referrals"),
         InlineKeyboardButton("🎁 Get Redeem", callback_data="get_redeem")],
    ])

def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Redeem Requests", callback_data="admin_redeem")],
        [InlineKeyboardButton("👤 User Info", callback_data="admin_users")],
        [InlineKeyboardButton("🔗 User Referrals", callback_data="admin_referrals")],
    ])

def join_keyboard():
    buttons = []
    for ch in CHANNELS:
        name = ch.lstrip("@")
        buttons.append([InlineKeyboardButton(f"Join {ch}", url=f"https://t.me/{name}")])
    buttons.append([InlineKeyboardButton("✅ Joined — Check Karo", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

# ══════════════════════════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    args = context.args

    if is_admin(user_id):
        await update.message.reply_text(
            f"👑 *Admin Panel*\nWelcome {user.first_name}!",
            parse_mode="Markdown",
            reply_markup=admin_menu_keyboard()
        )
        return

    referrer_id = None
    if args and args[0].startswith("ref_"):
        ref_str = args[0].replace("ref_", "")
        if ref_str.isdigit():
            referrer_id = ref_str

    user_data = get_user(user_id)
    if not user_data:
        new_data = {
            "user_id": user_id,
            "name": user.full_name,
            "username": user.username or "",
            "balance": 0,
            "referrer": referrer_id,
            "referred_users": [],
            "joined": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        save_user(user_id, new_data)
        user_data = new_data
        await notify_admins_new_user(context.bot, user, referrer_id)

    joined = await check_joined_all(context.bot, user_id)
    if not joined:
        await update.message.reply_text(
            "👋 *Welcome!*\n\nPehle in channels ko join karo:",
            parse_mode="Markdown",
            reply_markup=join_keyboard()
        )
        return

    if not user_data.get("joined"):
        save_user(user_id, {"joined": True})
        await credit_referrer(context.bot, user_id, user_data.get("referrer"))

    await update.message.reply_text(
        f"✅ *Welcome, {user.first_name}!*\n\nKya karna chahte ho?",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def credit_referrer(bot, new_user_id: int, referrer_id):
    if not referrer_id:
        return
    referrer_data = get_user(int(referrer_id))
    if not referrer_data:
        return
    referred_list = referrer_data.get("referred_users", [])
    if new_user_id in referred_list:
        return
    referred_list.append(new_user_id)
    new_balance = referrer_data.get("balance", 0) + REFERRAL_REWARD
    save_user(int(referrer_id), {"referred_users": referred_list, "balance": new_balance})
    try:
        await bot.send_message(
            int(referrer_id),
            f"🎉 *Referral Bonus!*\n\nKisi ne tumhara link use kiya!\n"
            f"₹{REFERRAL_REWARD} balance mein add ho gaye!\n"
            f"💰 New Balance: ₹{new_balance}",
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def notify_admins_new_user(bot, user, referrer_id):
    text = (
        f"🆕 *New User!*\n\n"
        f"👤 Name: {user.full_name}\n"
        f"🆔 Chat ID: `{user.id}`\n"
        f"📛 Username: @{user.username or 'N/A'}\n"
        f"🔗 Referred By: `{referrer_id or 'Direct'}`\n"
        f"🕐 Time: {datetime.utcnow().strftime('%d %b %Y, %H:%M')} UTC"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id

    joined = await check_joined_all(context.bot, user_id)
    if not joined:
        await query.edit_message_text(
            "❌ Abhi bhi sab channels join nahi kiye!\nSab join karo phir check karo.",
            reply_markup=join_keyboard()
        )
        return

    user_data = get_user(user_id) or {}
    if not user_data.get("joined"):
        save_user(user_id, {"joined": True})
        await credit_referrer(context.bot, user_id, user_data.get("referrer"))

    await query.edit_message_text(
        f"✅ *Welcome, {user.first_name}!*\n\nKya karna chahte ho?",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data = get_user(query.from_user.id) or {}
    bal = user_data.get("balance", 0)
    await query.edit_message_text(
        f"💰 *Your Balance*\n\n₹{bal}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]])
    )

async def refer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    bot_me = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user_id}"
    await query.edit_message_text(
        f"🔗 *Tumhara Referral Link*\n\n`{ref_link}`\n\n"
        f"Har naye join pe tumhe ₹{REFERRAL_REWARD} milenge!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]])
    )

async def my_referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data = get_user(query.from_user.id) or {}
    referred = user_data.get("referred_users", [])
    if not referred:
        text = "👥 *My Referrals*\n\nAbhi tak kisi ko refer nahi kiya."
    else:
        lines = []
        for rid in referred:
            rdata = get_user(int(rid)) or {}
            name = rdata.get("name", f"User {rid}")
            lines.append(f"• {name}")
        text = f"👥 *My Referrals* ({len(referred)})\n\n" + "\n".join(lines)
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]])
    )

async def get_redeem_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data = get_user(query.from_user.id) or {}
    bal = user_data.get("balance", 0)
    context.user_data["waiting_redeem"] = True
    await query.edit_message_text(
        f"🎁 *Get Redeem Code*\n\n"
        f"💰 Balance: ₹{bal}\n\n"
        f"Kitne ka redeem chahiye? Amount type karo:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="back_main")]])
    )

async def back_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "🏠 *Main Menu*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ══════════════════════════════════════════════════════════════════════════════
#  MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    # Admin reply flow
    if is_admin(user_id) and context.user_data.get("replying_to"):
        target_id = context.user_data["replying_to"]["user_id"]
        req_id = context.user_data["replying_to"].get("req_id", "direct")
        msg = update.message
        try:
            if msg.text:
                await context.bot.send_message(target_id, f"🎁 *Admin:*\n\n{msg.text}", parse_mode="Markdown")
            elif msg.photo:
                await context.bot.send_photo(target_id, msg.photo[-1].file_id, caption=msg.caption or "")
            elif msg.video:
                await context.bot.send_video(target_id, msg.video.file_id, caption=msg.caption or "")
            if req_id != "direct":
                db.collection("redeem_requests").document(req_id).update({"status": "fulfilled"})
            await update.message.reply_text("✅ Message bhej diya!", reply_markup=admin_menu_keyboard())
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        context.user_data.clear()
        return

    # User redeem amount
    if context.user_data.get("waiting_redeem"):
        if not text.isdigit():
            await update.message.reply_text("❌ Sirf number likho (e.g. 50)")
            return
        amount = int(text)
        user_data = get_user(user_id) or {}
        bal = user_data.get("balance", 0)
        if amount <= 0:
            await update.message.reply_text("❌ Amount 0 se zyada hona chahiye.")
            return
        if bal < amount:
            await update.message.reply_text(
                f"❌ *Balance Kam Hai!*\n\nBalance: ₹{bal}\nManga: ₹{amount}\n\nPehle refer karo!",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
            context.user_data.clear()
            return
        save_user(user_id, {"balance": bal - amount})
        req_ref = db.collection("redeem_requests").add({
            "user_id": user_id,
            "name": update.effective_user.full_name,
            "amount": amount,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        })
        req_id = req_ref[1].id
        await update.message.reply_text(
            f"✅ *Request Bhej Di!*\n\nAmount: ₹{amount}\nThoda wait karo, admin code bhejenge!",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
        context.user_data.clear()
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🎁 *New Redeem Request!*\n\n"
                    f"👤 {update.effective_user.full_name}\n"
                    f"🆔 `{user_id}`\n"
                    f"💰 ₹{amount}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📩 Reply", callback_data=f"adminreply_{user_id}_{req_id}")
                    ]])
                )
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN CALLBACKS
# ══════════════════════════════════════════════════════════════════════════════

async def admin_redeem_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    requests = db.collection("redeem_requests").where("status", "==", "pending").stream()
    req_list = [(r.id, r.to_dict()) for r in requests]
    if not req_list:
        await query.edit_message_text("📭 Koi pending request nahi.", reply_markup=admin_menu_keyboard())
        return
    buttons = []
    lines = ["🎁 *Pending Requests*\n"]
    for req_id, data in req_list[:10]:
        lines.append(f"• {data.get('name')} — ₹{data.get('amount')}")
        buttons.append([InlineKeyboardButton(
            f"📩 {data.get('name')} ₹{data.get('amount')}",
            callback_data=f"adminreply_{data.get('user_id')}_{req_id}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def admin_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    parts = query.data.split("_")
    target_user_id = int(parts[1])
    req_id = parts[2]
    context.user_data["replying_to"] = {"user_id": target_user_id, "req_id": req_id}
    await query.edit_message_text(
        f"📩 User `{target_user_id}` ko reply karo\n(text/photo/video)\n/cancel se cancel karo.",
        parse_mode="Markdown"
    )

async def admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    users = list(db.collection("users").order_by("created_at", direction=firestore.Query.DESCENDING).limit(20).stream())
    user_list = [u.to_dict() for u in users]
    if not user_list:
        await query.edit_message_text("No users yet.", reply_markup=admin_menu_keyboard())
        return
    context.user_data["user_list"] = user_list
    context.user_data["user_index"] = 0
    await show_user_card(query, context)

async def show_user_card(query, context):
    user_list = context.user_data.get("user_list", [])
    idx = context.user_data.get("user_index", 0)
    u = user_list[idx]
    text = (
        f"👤 *User* ({idx+1}/{len(user_list)})\n\n"
        f"📛 Name: {u.get('name')}\n"
        f"🆔 ID: `{u.get('user_id')}`\n"
        f"👤 @{u.get('username') or 'N/A'}\n"
        f"💰 Balance: ₹{u.get('balance', 0)}\n"
        f"🔗 Referrer: `{u.get('referrer') or 'Direct'}`\n"
        f"👥 Referrals: {len(u.get('referred_users', []))}\n"
        f"✅ Joined: {u.get('joined', False)}\n"
        f"📅 {u.get('created_at', '')[:10]}"
    )
    uid = u.get("user_id")
    buttons = [
        [InlineKeyboardButton("⬅️ Prev", callback_data="user_prev"),
         InlineKeyboardButton("Next ➡️", callback_data="user_next")],
        [InlineKeyboardButton("📩 Message User", callback_data=f"adminreply_{uid}_direct")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def user_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_list = context.user_data.get("user_list", [])
    idx = context.user_data.get("user_index", 0)
    idx = (idx + (1 if query.data == "user_next" else -1)) % len(user_list)
    context.user_data["user_index"] = idx
    await show_user_card(query, context)

async def admin_referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    users = db.collection("users").stream()
    lines = ["🔗 *Referral Map*\n"]
    for u in users:
        d = u.to_dict()
        if d.get("referrer"):
            ref_data = get_user(int(d["referrer"])) or {}
            ref_name = ref_data.get("name", f"ID:{d['referrer']}")
            lines.append(f"• {d.get('name')} ← {ref_name}")
    if len(lines) == 1:
        lines.append("Koi referral nahi hua abhi tak.")
    await query.edit_message_text(
        "\n".join(lines[:30]), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]])
    )

async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("👑 *Admin Panel*", parse_mode="Markdown", reply_markup=admin_menu_keyboard())

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))

    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(balance_callback, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(refer_callback, pattern="^refer$"))
    app.add_handler(CallbackQueryHandler(my_referrals_callback, pattern="^my_referrals$"))
    app.add_handler(CallbackQueryHandler(get_redeem_callback, pattern="^get_redeem$"))
    app.add_handler(CallbackQueryHandler(back_main_callback, pattern="^back_main$"))

    app.add_handler(CallbackQueryHandler(admin_redeem_callback, pattern="^admin_redeem$"))
    app.add_handler(CallbackQueryHandler(admin_reply_callback, pattern="^adminreply_"))
    app.add_handler(CallbackQueryHandler(admin_users_callback, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(user_nav_callback, pattern="^user_(next|prev)$"))
    app.add_handler(CallbackQueryHandler(admin_referrals_callback, pattern="^admin_referrals$"))
    app.add_handler(CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
