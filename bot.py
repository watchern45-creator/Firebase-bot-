import os
import logging
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# ─── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── ENV VARS ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(",")))  # comma separated
CHANNELS = os.environ.get("CHANNELS", "").split(",")  # e.g. @chan1,@chan2,@chan3
REFERRAL_REWARD = int(os.environ.get("REFERRAL_REWARD", 2))  # ₹2 default

# ─── FIREBASE INIT ─────────────────────────────────────────────────────────────
cred = credentials.Certificate("firebase_key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# ─── CONVERSATION STATES ───────────────────────────────────────────────────────
WAITING_REDEEM_AMOUNT = 1
ADMIN_REPLY_REDEEM = 2

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def check_joined_all(bot, user_id: int) -> bool:
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(channel.strip(), user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except Exception:
            return False
    return True


def get_user_ref(user_id: int):
    return db.collection("users").document(str(user_id))


def get_user(user_id: int):
    doc = get_user_ref(user_id).get()
    return doc.to_dict() if doc.exists else None


def save_user(user_id: int, data: dict):
    get_user_ref(user_id).set(data, merge=True)


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Check Balance", callback_data="balance"),
         InlineKeyboardButton("🔗 Refer", callback_data="refer")],
        [InlineKeyboardButton("👥 My Referrals", callback_data="my_referrals"),
         InlineKeyboardButton("🎁 Get Redeem", callback_data="get_redeem")],
    ])


def join_keyboard():
    buttons = []
    for ch in CHANNELS:
        ch = ch.strip()
        buttons.append([InlineKeyboardButton(f"Join {ch}", url=f"https://t.me/{ch.lstrip('@')}")])
    buttons.append([InlineKeyboardButton("✅ I Joined - Check", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)


def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Redeem Requests", callback_data="admin_redeem")],
        [InlineKeyboardButton("👤 User Info", callback_data="admin_users")],
        [InlineKeyboardButton("🔗 User Referrals", callback_data="admin_referrals")],
    ])


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ══════════════════════════════════════════════════════════════════════════════
#  /start COMMAND
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    args = context.args  # referral param

    # ── ADMIN FLOW ──
    if is_admin(user_id):
        await update.message.reply_text(
            f"👑 *Admin Panel*\nWelcome, {user.first_name}!",
            parse_mode="Markdown",
            reply_markup=admin_menu_keyboard()
        )
        return

    # ── USER: fetch or create ──
    user_data = get_user(user_id)
    referrer_id = args[0].replace("ref_", "") if args and args[0].startswith("ref_") else None

    if not user_data:
        # New user
        new_data = {
            "user_id": user_id,
            "name": user.full_name,
            "username": user.username or "",
            "balance": 0,
            "referrer": referrer_id,  # who referred this user
            "referred_users": [],     # users this person referred
            "joined": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        save_user(user_id, new_data)
        user_data = new_data

        # Notify admins about new user
        await notify_admins_new_user(context.bot, user, referrer_id)

    # ── CHECK CHANNELS ──
    joined = await check_joined_all(context.bot, user_id)
    if not joined:
        save_user(user_id, {"joined": False})
        await update.message.reply_text(
            "👋 *Welcome!*\n\nPehle in channels ko join karo, phir continue karo:",
            parse_mode="Markdown",
            reply_markup=join_keyboard()
        )
        return

    # Channels joined - mark and credit referrer if not already done
    if not user_data.get("joined"):
        save_user(user_id, {"joined": True})
        await credit_referrer(context.bot, user_id, referrer_id, user_data)

    await update.message.reply_text(
        f"✅ *Welcome back, {user.first_name}!*\n\nKya karna chahte ho?",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )


async def credit_referrer(bot, new_user_id: int, referrer_id, user_data: dict):
    """Credit referrer ₹REFERRAL_REWARD only once."""
    if not referrer_id:
        return
    referrer_id_int = int(referrer_id)
    referrer_data = get_user(referrer_id_int)
    if not referrer_data:
        return

    referred_list = referrer_data.get("referred_users", [])
    if new_user_id in referred_list:
        return  # already credited

    # Add to referrer's list and credit balance
    referred_list.append(new_user_id)
    new_balance = referrer_data.get("balance", 0) + REFERRAL_REWARD
    save_user(referrer_id_int, {
        "referred_users": referred_list,
        "balance": new_balance
    })

    # Notify referrer
    try:
        await bot.send_message(
            referrer_id_int,
            f"🎉 *Referral Bonus!*\n\nKisi ne tumhara link use kiya!\n"
            f"₹{REFERRAL_REWARD} tumhare balance mein add ho gaye!\n"
            f"💰 New Balance: ₹{new_balance}",
            parse_mode="Markdown"
        )
    except Exception:
        pass


async def notify_admins_new_user(bot, user, referrer_id):
    """Send new user info to all admins."""
    text = (
        f"🆕 *New User Joined!*\n\n"
        f"👤 Name: {user.full_name}\n"
        f"🆔 Chat ID: `{user.id}`\n"
        f"📛 Username: @{user.username or 'N/A'}\n"
        f"🔗 Referred By ID: `{referrer_id or 'Direct'}`\n"
        f"🕐 Time: {datetime.utcnow().strftime('%d %b %Y, %H:%M')} UTC"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
#  CHECK JOIN CALLBACK
# ══════════════════════════════════════════════════════════════════════════════

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id

    joined = await check_joined_all(context.bot, user_id)
    if not joined:
        await query.edit_message_text(
            "❌ Abhi bhi kuch channels join nahi kiye!\nSab join karo phir check karo.",
            reply_markup=join_keyboard()
        )
        return

    user_data = get_user(user_id)
    if not user_data.get("joined"):
        save_user(user_id, {"joined": True})
        referrer_id = user_data.get("referrer")
        await credit_referrer(context.bot, user_id, referrer_id, user_data)

    await query.edit_message_text(
        f"✅ *Welcome, {user.first_name}!*\n\nKya karna chahte ho?",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ══════════════════════════════════════════════════════════════════════════════
#  USER BUTTONS
# ══════════════════════════════════════════════════════════════════════════════

async def balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = get_user(user_id) or {}
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
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    await query.edit_message_text(
        f"🔗 *Your Referral Link*\n\n`{ref_link}`\n\n"
        f"Is link se jo join karega, tumhe ₹{REFERRAL_REWARD} milenge!\n"
        f"_(Sirf naye users ke liye, ek baar hi milega)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]])
    )


async def my_referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = get_user(user_id) or {}
    referred = user_data.get("referred_users", [])

    if not referred:
        text = "👥 *My Referrals*\n\nAbhi tak kisi ko refer nahi kiya."
    else:
        lines = []
        for rid in referred:
            rdata = get_user(int(rid)) or {}
            name = rdata.get("name", f"User {rid}")
            lines.append(f"• {name} (`{rid}`)")
        text = f"👥 *My Referrals* ({len(referred)} total)\n\n" + "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_main")]])
    )


async def get_redeem_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data = get_user(user_id) or {}
    bal = user_data.get("balance", 0)
    await query.edit_message_text(
        f"🎁 *Get Redeem Code*\n\n"
        f"💰 Current Balance: ₹{bal}\n\n"
        f"Kitne ka redeem code chahiye? Amount type karo:\n"
        f"_(Balance se zyada nahi hona chahiye)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="back_main")]])
    )
    context.user_data["waiting_redeem"] = True


async def back_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "🏠 *Main Menu*\n\nKya karna chahte ho?",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

# ══════════════════════════════════════════════════════════════════════════════
#  REDEEM AMOUNT MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Admin reply to a redeem request
    if is_admin(user_id) and context.user_data.get("replying_to_redeem"):
        await handle_admin_reply(update, context)
        return

    # User redeem amount
    if context.user_data.get("waiting_redeem"):
        if not text.isdigit():
            await update.message.reply_text("❌ Sirf number likhو (e.g. 50)")
            return
        amount = int(text)
        user_data = get_user(user_id) or {}
        bal = user_data.get("balance", 0)

        if amount <= 0:
            await update.message.reply_text("❌ Amount 0 se zyada hona chahiye.")
            return

        if bal < amount:
            await update.message.reply_text(
                f"❌ *Insufficient Balance!*\n\n"
                f"Tumhara balance: ₹{bal}\n"
                f"Requested: ₹{amount}\n\n"
                f"Refer karo aur balance badhao! 🔗",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
            context.user_data.clear()
            return

        # Deduct balance and create request
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
            f"✅ *Redeem Request Sent!*\n\n"
            f"Amount: ₹{amount}\n"
            f"Request ID: `{req_id}`\n\n"
            f"Thoda wait karo, admin code bhejenge! 🎁",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
        context.user_data.clear()

        # Notify admins
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🎁 *New Redeem Request!*\n\n"
                    f"👤 Name: {update.effective_user.full_name}\n"
                    f"🆔 User ID: `{user_id}`\n"
                    f"💰 Amount: ₹{amount}\n"
                    f"📋 Req ID: `{req_id}`\n\n"
                    f"Reply karo redeem code/message bhejne ke liye.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"📩 Reply to {update.effective_user.first_name}", callback_data=f"admin_reply_{user_id}_{req_id}")
                    ]])
                )
            except Exception:
                pass
        return

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
        await query.edit_message_text("📭 Koi pending redeem request nahi hai.",
                                      reply_markup=admin_menu_keyboard())
        return

    buttons = []
    text_lines = ["🎁 *Pending Redeem Requests*\n"]
    for req_id, data in req_list[:10]:  # show max 10
        text_lines.append(
            f"• {data.get('name')} | ₹{data.get('amount')} | ID: `{req_id[:8]}...`"
        )
        buttons.append([InlineKeyboardButton(
            f"📩 {data.get('name')} - ₹{data.get('amount')}",
            callback_data=f"admin_reply_{data.get('user_id')}_{req_id}"
        )])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    await query.edit_message_text(
        "\n".join(text_lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    parts = query.data.split("_")
    # format: admin_reply_{user_id}_{req_id}
    target_user_id = int(parts[2])
    req_id = parts[3]

    context.user_data["replying_to_redeem"] = {
        "user_id": target_user_id,
        "req_id": req_id
    }
    await query.edit_message_text(
        f"📩 *Reply to User `{target_user_id}`*\n\n"
        f"Ab kuch bhi bhejo (text/photo/video) — wo directly us user ko jayega.\n"
        f"Cancel karne ke liye /cancel likho.",
        parse_mode="Markdown"
    )


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_data = context.user_data.get("replying_to_redeem")
    if not reply_data:
        return

    target_id = reply_data["user_id"]
    req_id = reply_data["req_id"]
    msg = update.message

    try:
        if msg.text:
            await context.bot.send_message(target_id, f"🎁 *Admin Message:*\n\n{msg.text}", parse_mode="Markdown")
        elif msg.photo:
            await context.bot.send_photo(target_id, msg.photo[-1].file_id, caption=msg.caption or "🎁 Admin ne bheja!")
        elif msg.video:
            await context.bot.send_video(target_id, msg.video.file_id, caption=msg.caption or "🎁 Admin ne bheja!")
        elif msg.document:
            await context.bot.send_document(target_id, msg.document.file_id, caption=msg.caption or "")

        # Mark request done
        db.collection("redeem_requests").document(req_id).update({"status": "fulfilled"})
        await update.message.reply_text("✅ Message bhej diya! Request fulfilled.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

    context.user_data.clear()


async def admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    users = db.collection("users").order_by("created_at", direction=firestore.Query.DESCENDING).limit(10).stream()
    user_list = [u.to_dict() for u in users]

    if not user_list:
        await query.edit_message_text("No users yet.", reply_markup=admin_menu_keyboard())
        return

    context.user_data["user_list"] = user_list
    context.user_data["user_index"] = 0
    await show_user_card(query, context, edit=True)


async def show_user_card(query_or_update, context, edit=False):
    user_list = context.user_data.get("user_list", [])
    idx = context.user_data.get("user_index", 0)
    if idx >= len(user_list):
        idx = 0
    u = user_list[idx]

    text = (
        f"👤 *User Info* ({idx+1}/{len(user_list)})\n\n"
        f"📛 Name: {u.get('name')}\n"
        f"🆔 Chat ID: `{u.get('user_id')}`\n"
        f"👤 Username: @{u.get('username') or 'N/A'}\n"
        f"💰 Balance: ₹{u.get('balance', 0)}\n"
        f"🔗 Referrer ID: `{u.get('referrer') or 'Direct'}`\n"
        f"✅ Joined Channels: {u.get('joined', False)}\n"
        f"👥 Referrals: {len(u.get('referred_users', []))}\n"
        f"📅 Joined: {u.get('created_at', '')[:10]}"
    )

    uid = u.get("user_id")
    buttons = [
        [InlineKeyboardButton("⬅️ Prev", callback_data="user_prev"),
         InlineKeyboardButton("Next ➡️", callback_data="user_next")],
        [InlineKeyboardButton(f"📩 Reply to User", callback_data=f"admin_msg_{uid}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")],
    ]

    if edit and hasattr(query_or_update, 'edit_message_text'):
        await query_or_update.edit_message_text(text, parse_mode="Markdown",
                                                reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await query_or_update.message.reply_text(text, parse_mode="Markdown",
                                                  reply_markup=InlineKeyboardMarkup(buttons))


async def user_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    direction = 1 if query.data == "user_next" else -1
    user_list = context.user_data.get("user_list", [])
    idx = context.user_data.get("user_index", 0) + direction
    idx = idx % len(user_list) if user_list else 0
    context.user_data["user_index"] = idx
    await show_user_card(query, context, edit=True)


async def admin_msg_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split("_")[2])
    context.user_data["replying_to_redeem"] = {"user_id": uid, "req_id": "direct"}
    await query.edit_message_text(
        f"📩 User `{uid}` ko message bhejo (text/photo/video):\n/cancel se cancel karo.",
        parse_mode="Markdown"
    )


async def admin_referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    users = db.collection("users").stream()
    lines = ["🔗 *User Referral Map*\n"]
    for u in users:
        d = u.to_dict()
        if d.get("referrer"):
            referrer_data = get_user(int(d["referrer"])) or {}
            ref_name = referrer_data.get("name", f"ID:{d['referrer']}")
            lines.append(f"• {d.get('name')} ← {ref_name}")

    if len(lines) == 1:
        lines.append("Koi referral nahi hua abhi tak.")

    await query.edit_message_text(
        "\n".join(lines[:30]),  # max 30 lines
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]])
    )


async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "👑 *Admin Panel*",
        parse_mode="Markdown",
        reply_markup=admin_menu_keyboard()
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(balance_callback, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(refer_callback, pattern="^refer$"))
    app.add_handler(CallbackQueryHandler(my_referrals_callback, pattern="^my_referrals$"))
    app.add_handler(CallbackQueryHandler(get_redeem_callback, pattern="^get_redeem$"))
    app.add_handler(CallbackQueryHandler(back_main_callback, pattern="^back_main$"))

    # Admin callbacks
    app.add_handler(CallbackQueryHandler(admin_redeem_callback, pattern="^admin_redeem$"))
    app.add_handler(CallbackQueryHandler(admin_reply_callback, pattern="^admin_reply_"))
    app.add_handler(CallbackQueryHandler(admin_users_callback, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(user_nav_callback, pattern="^user_(next|prev)$"))
    app.add_handler(CallbackQueryHandler(admin_msg_user_callback, pattern="^admin_msg_"))
    app.add_handler(CallbackQueryHandler(admin_referrals_callback, pattern="^admin_referrals$"))
    app.add_handler(CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"))

    # Message handler (must be last)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
