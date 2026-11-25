from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
from dataclasses import dataclass

import certifi
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from glowme_client import DEFAULT_GLOWME_URL, GlowMeClient, Product, find_products
from storage import Storage

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BTN_OUT_OF_STOCK = "❌ Нет в наличии"
BTN_IN_STOCK = "✅ В наличии"
BTN_MY_SUBS = "🔔 Мои подписки"
BTN_WATCH_ALL = "🌐 Все поступления"

PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    chat_id: int
    product: Product
    reason: str  # "subscription" | "watch_all_restock" | "watch_all_new"


def evaluate_catalog_updates(
    products: list[Product],
    storage: Storage,
) -> list[NotificationEvent]:
    """Сравнивает текущий каталог с сохранённым состоянием и возвращает уведомления."""
    events: list[NotificationEvent] = []
    by_uid = {p.uid: p for p in products}

    for sub in storage.list_all_subscriptions():
        product = by_uid.get(sub.product_uid)
        if product is None:
            continue
        if sub.last_in_stock is False and product.in_stock:
            events.append(
                NotificationEvent(
                    chat_id=sub.chat_id,
                    product=product,
                    reason="subscription",
                )
            )
        if sub.last_in_stock != product.in_stock:
            storage.update_subscription_state(
                sub.chat_id, sub.product_uid, product.in_stock
            )

    prev_states = storage.get_catalog_states()
    watch_all_chats = storage.list_watch_all_chats()
    if prev_states and watch_all_chats:
        for product in products:
            was_in_stock = prev_states.get(product.uid)
            if was_in_stock is False and product.in_stock:
                reason = "watch_all_restock"
            elif was_in_stock is None and product.in_stock:
                reason = "watch_all_new"
            else:
                continue
            for chat_id in watch_all_chats:
                events.append(
                    NotificationEvent(
                        chat_id=chat_id,
                        product=product,
                        reason=reason,
                    )
                )

    storage.save_catalog_states([(p.uid, p.title, p.in_stock) for p in products])
    return events


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_OUT_OF_STOCK), KeyboardButton(BTN_IN_STOCK)],
            [KeyboardButton(BTN_MY_SUBS), KeyboardButton(BTN_WATCH_ALL)],
        ],
        resize_keyboard=True,
    )


def format_product_card(product: Product) -> str:
    title = html.escape(product.title)
    url = html.escape(product.url)
    return (
        f"<b>{title}</b>\n"
        f"Статус: {product.stock_label}\n"
        f"Цена: {product.price_label}\n"
        f'<a href="{url}">Открыть на glowme.ru</a>'
    )


def format_notification_message(event: NotificationEvent) -> str:
    if event.reason == "watch_all_new":
        header = "🆕 <b>Новый товар в наличии на glowme.ru!</b>"
    else:
        header = "🎉 <b>Товар появился в наличии!</b>"
    return f"{header}\n\n{format_product_card(event.product)}"


def build_products_keyboard(
    products: list[Product],
    chat_id: int,
    storage: Storage,
    page: int = 0,
    list_kind: str | None = None,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    start = page * PAGE_SIZE
    slice_items = products[start : start + PAGE_SIZE]

    for p in slice_items:
        subscribed = storage.is_subscribed(chat_id, p.uid)
        action = "unsub" if subscribed else "sub"
        prefix = "🔕 Отписаться" if subscribed else "🔔 Отслеживать"
        short_title = p.title if len(p.title) <= 28 else f"{p.title[:25]}..."
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{prefix}: {short_title}",
                    callback_data=f"{action}:{p.uid}",
                )
            ]
        )

    if list_kind is not None and len(products) > PAGE_SIZE:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton("⬅️ Назад", callback_data=f"page:{list_kind}:{page - 1}")
            )
        if start + PAGE_SIZE < len(products):
            nav_row.append(
                InlineKeyboardButton("Вперёд ➡️", callback_data=f"page:{list_kind}:{page + 1}")
            )
        if nav_row:
            buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


def _get_deps(context: ContextTypes.DEFAULT_TYPE) -> tuple[GlowMeClient, Storage]:
    return context.bot_data["client"], context.bot_data["storage"]


async def _load_catalog(context: ContextTypes.DEFAULT_TYPE) -> list[Product]:
    client, _ = _get_deps(context)
    products = await client.fetch_products()
    context.bot_data["last_catalog"] = {p.uid: p for p in products}
    return products


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    text = (
        "Привет! Я отслеживаю наличие товаров на <b>glowme.ru</b>.\n\n"
        "Что можно сделать:\n"
        "• Отправь <b>название товара</b> или <b>ссылку</b> с glowme.ru — покажу наличие и кнопку отслеживания.\n"
        f"• Нажми <b>{BTN_OUT_OF_STOCK}</b> — чтобы выбрать товары, которых сейчас нет.\n"
        f"• Нажми <b>{BTN_IN_STOCK}</b> — чтобы посмотреть, что можно купить прямо сейчас.\n"
        f"• Нажми <b>{BTN_MY_SUBS}</b> — список твоих отслеживаемых товаров.\n"
        f"• Нажми <b>{BTN_WATCH_ALL}</b> — уведомления о появлении любых товаров."
    )
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


async def show_catalog_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    in_stock: bool,
    page: int = 0,
    edit_message: bool = False,
) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    _, storage = _get_deps(context)

    try:
        products = await _load_catalog(context)
    except Exception as exc:
        logger.exception("Ошибка загрузки каталога")
        err_text = f"Не удалось загрузить каталог glowme.ru: {html.escape(str(exc))}"
        if update.effective_message:
            await update.effective_message.reply_text(err_text, parse_mode=ParseMode.HTML)
        return

    filtered = [p for p in products if p.in_stock is in_stock]
    list_kind = "in" if in_stock else "out"
    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    if not filtered:
        msg = (
            "Сейчас нет товаров в наличии."
            if in_stock
            else "Все товары из каталога сейчас в наличии! 🎉"
        )
        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.effective_message:
            await update.effective_message.reply_text(msg)
        return

    header = (
        f"<b>✅ Товары в наличии ({len(filtered)})</b>"
        if in_stock
        else f"<b>❌ Нет в наличии ({len(filtered)})</b>"
    )
    slice_items = filtered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [f"{header} — стр. {page + 1}/{total_pages}\n"]
    for idx, p in enumerate(slice_items, start=page * PAGE_SIZE + 1):
        stock_info = f" ({p.quantity} шт.)" if p.in_stock and p.quantity is not None else ""
        lines.append(
            f'{idx}. <a href="{html.escape(p.url)}">{html.escape(p.title)}</a> — {p.price_label}{stock_info}'
        )

    text = "\n".join(lines)
    markup = build_products_keyboard(filtered, chat.id, storage, page=page, list_kind=list_kind)

    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None:
        return
    _, storage = _get_deps(context)
    subs = storage.list_subscriptions(chat.id)
    if not subs:
        await msg.reply_text(
            "У тебя пока нет отслеживаемых товаров.\n"
            "Отправь название товара или ссылку с glowme.ru, чтобы добавить!",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = [f"<b>🔔 Отслеживаемые товары ({len(subs)}):</b>\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for idx, sub in enumerate(subs, start=1):
        status = "✅ в наличии" if sub.last_in_stock else "❌ нет в наличии"
        lines.append(
            f'{idx}. <a href="{html.escape(sub.product_url)}">{html.escape(sub.product_title)}</a> ({status})'
        )
        short = (
            sub.product_title
            if len(sub.product_title) <= 28
            else f"{sub.product_title[:25]}..."
        )
        buttons.append(
            [InlineKeyboardButton(f"🔕 Удалить: {short}", callback_data=f"unsub_list:{sub.product_uid}")]
        )

    buttons.append([InlineKeyboardButton("🔄 Проверить сейчас", callback_data="check_now")])
    await msg.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=True,
    )


async def toggle_watch_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    msg = update.effective_message
    if chat is None or msg is None:
        return
    _, storage = _get_deps(context)
    enabled = not storage.is_watch_all(chat.id)
    storage.set_watch_all(chat.id, enabled)

    if enabled and not storage.get_catalog_states():
        try:
            products = await _load_catalog(context)
            storage.save_catalog_states([(p.uid, p.title, p.in_stock) for p in products])
        except Exception as exc:
            logger.warning("Не удалось сохранить начальный снимок каталога: %s", exc)

    if enabled:
        text = (
            "🌐 <b>Отслеживание всех поступлений включено!</b>\n"
            "Я пришлю уведомление, как только любой отсутствующий товар вернётся в наличие "
            "или появится новый товар на glowme.ru."
        )
    else:
        text = "🔕 Отслеживание всех поступлений выключено."

    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None or not msg.text:
        return

    query = msg.text.strip()
    if query == BTN_OUT_OF_STOCK:
        await show_catalog_list(update, context, in_stock=False, page=0)
        return
    if query == BTN_IN_STOCK:
        await show_catalog_list(update, context, in_stock=True, page=0)
        return
    if query == BTN_MY_SUBS:
        await show_subscriptions(update, context)
        return
    if query == BTN_WATCH_ALL:
        await toggle_watch_all(update, context)
        return

    _, storage = _get_deps(context)
    try:
        products = await _load_catalog(context)
    except Exception as exc:
        logger.exception("Ошибка поиска в каталоге")
        await msg.reply_text(
            f"Не удалось получить каталог glowme.ru: {html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    matches = find_products(products, query, limit=PAGE_SIZE)
    if not matches:
        await msg.reply_text(
            "Ничего не нашёл по этому запросу 😕\n"
            "Попробуй другое слово (например: <code>масло</code>, <code>адвент</code>, <code>бальзам</code>) "
            "или вставь прямую ссылку на товар с glowme.ru.",
            parse_mode=ParseMode.HTML,
        )
        return

    cards = "\n\n".join(format_product_card(p) for p in matches)
    markup = build_products_keyboard(matches, chat.id, storage)
    await msg.reply_text(
        cards,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if query is None or chat is None or not query.data:
        return

    _, storage = _get_deps(context)
    data = query.data

    if data.startswith("page:"):
        _, list_kind, page_str = data.split(":", 2)
        await query.answer()
        await show_catalog_list(
            update,
            context,
            in_stock=(list_kind == "in"),
            page=int(page_str),
            edit_message=True,
        )
        return

    if data == "check_now":
        await query.answer("Проверяю наличие...")
        try:
            products = await _load_catalog(context)
        except Exception as exc:
            await query.message.reply_text(f"Ошибка проверки: {exc}")
            return

        by_uid = {p.uid: p for p in products}
        subs = storage.list_subscriptions(chat.id)
        if not subs:
            await query.message.reply_text("Список отслеживания пуст.")
            return

        lines = ["<b>🔍 Текущее наличие по твоим подпискам:</b>\n"]
        for sub in subs:
            p = by_uid.get(sub.product_uid)
            if p is None:
                lines.append(f"• {html.escape(sub.product_title)} — не найден в каталоге")
            else:
                storage.update_subscription_state(chat.id, p.uid, p.in_stock)
                lines.append(
                    f'• <a href="{html.escape(p.url)}">{html.escape(p.title)}</a>: {p.stock_label} ({p.price_label})'
                )
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if data.startswith(("sub:", "unsub:", "unsub_list:")):
        action, uid = data.split(":", 1)
        if action == "sub":
            catalog_cache: dict[str, Product] = context.bot_data.get("last_catalog", {})
            product = catalog_cache.get(uid)
            if product is None:
                try:
                    products = await _load_catalog(context)
                    product = next((p for p in products if p.uid == uid), None)
                except Exception:
                    product = None
            if product is None:
                await query.answer("Товар не найден, попробуй обновить список.", show_alert=True)
                return

            storage.add_subscription(
                chat_id=chat.id,
                product_uid=product.uid,
                product_title=product.title,
                product_url=product.url,
                in_stock=product.in_stock,
            )
            if product.in_stock:
                await query.answer("Товар уже в наличии! Добавил в отслеживание.", show_alert=True)
            else:
                await query.answer("Добавлено в отслеживание! Напишу, когда появится.")
            if query.message:
                await query.message.reply_text(
                    f"🔔 Отслеживаю: <b>{html.escape(product.title)}</b>\n"
                    f"Текущий статус: {product.stock_label}",
                    parse_mode=ParseMode.HTML,
                )
            return

        removed = storage.remove_subscription(chat.id, uid)
        if removed:
            await query.answer("Удалено из отслеживания.")
        else:
            await query.answer("Подписка уже удалена.")
        if action == "unsub_list" and query.message:
            await show_subscriptions(update, context)
        return

    await query.answer()


async def poll_catalog_loop(app: Application) -> None:
    """Фоновый цикл проверки наличия товаров на glowme.ru."""
    client: GlowMeClient = app.bot_data["client"]
    storage: Storage = app.bot_data["storage"]
    interval: int = app.bot_data["check_interval"]

    logger.info("Запущен мониторинг glowme.ru (интервал: %s сек.)", interval)
    while True:
        try:
            products = await client.fetch_products()
            app.bot_data["last_catalog"] = {p.uid: p for p in products}
            events = evaluate_catalog_updates(products, storage)

            for event in events:
                try:
                    await app.bot.send_message(
                        chat_id=event.chat_id,
                        text=format_notification_message(event),
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )
                except Forbidden:
                    logger.info("Пользователь %s заблокировал бота, удаляем подписки", event.chat_id)
                    storage.set_watch_all(event.chat_id, False)
                    for sub in storage.list_subscriptions(event.chat_id):
                        storage.remove_subscription(event.chat_id, sub.product_uid)
                except TelegramError as exc:
                    logger.warning("Ошибка отправки сообщения в чат %s: %s", event.chat_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Ошибка при проверке каталога glowme.ru: %s", exc)

        await asyncio.sleep(interval)


async def post_init(app: Application) -> None:
    app.bot_data["poll_task"] = asyncio.create_task(poll_catalog_loop(app))


async def post_shutdown(app: Application) -> None:
    task: asyncio.Task | None = app.bot_data.get("poll_task")
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def main() -> None:
    load_dotenv()
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "your_telegram_bot_token_here":
        raise SystemExit(
            "Укажите токен бота в переменной окружения BOT_TOKEN или в файле .env"
        )

    check_interval = max(15, int(os.getenv("CHECK_INTERVAL", "60")))
    glowme_url = os.getenv("GLOWME_URL", DEFAULT_GLOWME_URL).strip() or DEFAULT_GLOWME_URL
    db_path = os.getenv("DB_PATH", "bot_data.db").strip() or "bot_data.db"

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["client"] = GlowMeClient(site_url=glowme_url)
    app.bot_data["storage"] = Storage(db_path=db_path)
    app.bot_data["check_interval"] = check_interval

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("list", show_subscriptions))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
