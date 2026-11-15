"""Telegram training bot. Run a single polling instance per token."""
import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import CommandStart
from aiogram.types import (Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           FSInputFile, InputMediaPhoto, BotCommand, ErrorEvent)
from dotenv import load_dotenv
from core import ROOT, Store, ActionError, transition, view

load_dotenv(ROOT/'.env')
logger=logging.getLogger(__name__)
dp=Dispatcher()
locks={}
image_ids={}
store=None

def lock(uid):
    # Telegram handlers are serialized per learner, even on rapid double taps.
    return locks.setdefault(uid,asyncio.Lock())

def markup(rows,rev):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t,callback_data=f'{rev}|{a}') for t,a in row
    ] for row in rows])

async def api(method, **kwargs):
    for attempt in range(4):
        try:return await method(**kwargs)
        except TelegramRetryAfter as e:
            if attempt==3:raise
            await asyncio.sleep(e.retry_after+0.1)
        except TelegramNetworkError:
            if attempt==3:raise
            await asyncio.sleep(1+attempt)

async def render(bot,chat,s):
    image,text,rows=view(s);keyboard=markup(rows,s['rev'])
    media=image_ids.get(image) or FSInputFile(ROOT/image)
    if s.get('photo_id') and s.get('text_id'):
        try:
            try:
                photo=await api(bot.edit_message_media,chat_id=chat,message_id=s['photo_id'],
                                media=InputMediaPhoto(media=media))
                if isinstance(photo,Message) and photo.photo:image_ids[image]=photo.photo[-1].file_id
            except TelegramBadRequest as e:
                if 'message is not modified' not in str(e):raise
            try:
                await api(bot.edit_message_text,chat_id=chat,message_id=s['text_id'],text=text,reply_markup=keyboard)
            except TelegramBadRequest as e:
                if 'message is not modified' not in str(e):raise
            return
        except TelegramBadRequest as e:
            if not any(x in str(e) for x in ('message to edit not found',"message can't be edited")):raise
            # The learner may have manually deleted an old message.
    photo=await api(bot.send_photo,chat_id=chat,photo=media)
    if photo.photo:image_ids[image]=photo.photo[-1].file_id
    msg=await api(bot.send_message,chat_id=chat,text=text,reply_markup=keyboard)
    s.update(photo_id=photo.message_id,text_id=msg.message_id)

@dp.message(CommandStart(), F.chat.type==ChatType.PRIVATE)
async def start(message:Message,bot:Bot):
    uid=message.from_user.id
    async with lock(uid):
        s=store.get(uid)
        s=transition(s,'menu')
        if s.get('text_id'):
            with suppress(TelegramBadRequest):
                await bot.edit_message_reply_markup(chat_id=message.chat.id,message_id=s['text_id'],reply_markup=None)
        s.update(photo_id=None,text_id=None)
        # Commit before network calls: retries never erase course progress.
        store.put(uid,s)
        await render(bot,message.chat.id,s)
        store.put(uid,s)

@dp.callback_query(F.data)
async def callback(query:CallbackQuery,bot:Bot):
    if not isinstance(query.message,Message) or query.message.chat.type!=ChatType.PRIVATE:
        await query.answer();return
    uid=query.from_user.id
    async with lock(uid):
        s=store.get(uid)
        try:
            rev,action=query.data.split('|',1)
            if int(rev)!=s['rev'] or query.message.message_id!=s.get('text_id'):
                await query.answer('Эта кнопка устарела. Используйте текущий экран или /start.');return
            new=transition(s,action)
        except (ValueError,IndexError,KeyError) as e:
            await query.answer(str(e) if isinstance(e,ActionError) else 'Откройте /start.',show_alert=True);return
        await query.answer()
        store.put(uid,new)
        await render(bot,query.message.chat.id,new)
        store.put(uid,new)

@dp.message(F.chat.type==ChatType.PRIVATE)
async def other(message:Message):
    await message.answer('Для обучения используйте кнопки под уроком. Главное меню: /start.')

@dp.errors()
async def on_error(event:ErrorEvent):
    # Do not log Telegram update bodies or learner messages.
    logger.error('Request failed (%s).',type(event.exception).__name__)
    message=event.update.message or (event.update.callback_query.message if event.update.callback_query else None)
    if isinstance(message,Message):
        with suppress(Exception):await message.answer('Не удалось обновить экран. Нажмите /start — пройденные уроки и ответы сохранены.')
    return True

async def main():
    global store
    token=os.getenv('BOT_TOKEN','').strip()
    if not token or token=='PASTE_TOKEN_HERE':
        raise SystemExit('Укажите BOT_TOKEN в файле .env. Инструкция: README.md')
    db_path=Path(os.getenv('DATABASE_PATH',str(ROOT/'data/progress.sqlite3')))
    db_path.parent.mkdir(parents=True,exist_ok=True)
    store=Store(db_path)
    bot=Bot(token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.set_my_commands([BotCommand(command='start',description='Обучение · Главное меню')])
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot,allowed_updates=['message','callback_query'],close_bot_session=False)
    finally:
        store.close()
        await bot.session.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.WARNING,format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(main())
