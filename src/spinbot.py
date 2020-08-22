# -*- coding: utf-8 -*-
import logging
import os
from dataclasses import dataclass, field
import datetime
import pytz
import random
import traceback
from typing import List, Dict, Set
import asyncio

import psycopg2
import urllib.parse as urlparse

from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.filters import BoundFilter


# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)
logger = logging.getLogger(__name__)


# Getting configurations
BOT_NAME = os.environ['BOT_NAME']
BOT_TOKEN = os.environ['BOT_TOKEN']

HEROKU : bool = 'HEROKU' in os.environ

if HEROKU:
    WEBHOOK_HOST = os.environ['WEBHOOK_HOST']
    WEBHOOK_PATH = '/webhook/'
    WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

    WEBAPP_HOST = '0.0.0.0'
    WEBAPP_PORT = os.environ['PORT']


TIME_ZONE = pytz.timezone(os.environ['LOCATION'])


# Connect to PostgresSQL
def make_db_connection():
    url = urlparse.urlparse(os.environ['DATABASE_URL'])
    dbname = url.path[1:]
    user = url.username
    password = url.password
    host = url.hostname
    port = url.port

    return psycopg2.connect(
        dbname=dbname, user=user,
        password=password,
        host=host,
        port=port)

db = make_db_connection()


# Define filters
class AdminFilter(BoundFilter):
    key = 'is_admin'

    def __init__(self, is_admin):
        self.is_admin = is_admin

    async def check(self, message: types.Message):
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.is_chat_admin()

@dataclass
class UserDef:
    username : str
    won_times : int = 0


@dataclass
class ChatContext:
    wheel : str = 'пижма'
    action : str = 'запутать'
    last_winner_id : int = None
    last_wheel : str = None
    last_spin : datetime.datetime = None
    user_ids : List[int] = field(default_factory=list)
    users : Dict[int, UserDef] = field(default_factory=dict)

chats : Dict[str, ChatContext] = {}


# PostgreSQL workloads

with db.cursor() as cur:
    cur.execute('''
        CREATE TABLE IF NOT EXISTS chat_contexts (
            chat_id BIGINT NOT NULL,
            wheel TEXT NOT NULL DEFAULT 'пижма',
            action TEXT NOT NULL DEFAULT 'запутать',
            last_winner_id BIGINT NULL,
            last_wheel TEXT NULL,
            last_spin TIMESTAMPTZ NULL,
            PRIMARY KEY(chat_id)
        );''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS chat_users (
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            username TEXT,
            won_times INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        );''')

    cur.execute('CREATE INDEX IF NOT EXISTS uchats_index ON chat_users (chat_id);')
    
    db.commit()


def load_chat(chat_id: int):
    ctx = ChatContext()

    with db.cursor() as cur:
        GET_CHAT_INFO = '''
            SELECT wheel, action, last_winner_id, last_wheel, last_spin
            FROM chat_contexts WHERE chat_id=%s;
        '''

        cur.execute(GET_CHAT_INFO, (chat_id,))
        ret = cur.fetchmany(1)
        if len(ret) == 0:
            return ctx

        ctx.wheel, ctx.action, ctx.last_winner_id, ctx.last_wheel, ctx.last_spin = ret[0]

        GET_CHAT_USERS = '''
            SELECT user_id, username, won_times
            FROM chat_users
            WHERE chat_id = %s;
        '''

        cur.execute(GET_CHAT_USERS, (chat_id,))
        user_defs = cur.fetchall()

        ctx.user_ids = [u[0] for u in user_defs]
        ctx.users = {u[0]: UserDef(username=u[1], won_times=u[2]) for u in user_defs}

        logger.info(f'Loaded chat {chat_id}')

    return ctx


def load_chats():
        with db.cursor() as cur:
            GET_CHAT_IDS = 'SELECT chat_id FROM chat_contexts;'
            cur.execute(GET_CHAT_IDS)
            chat_ids = cur.fetchall()
            for chat_tuple in chat_ids:
                chat_id = chat_tuple[0]
                chats[chat_id] = load_chat(chat_id)


def upsert_chat(chat_id: int, ctx: ChatContext):
    QUERY = '''
        INSERT INTO chat_contexts
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (chat_id)
        DO UPDATE SET
            wheel = EXCLUDED.wheel,
            action = EXCLUDED.action,
            last_winner_id = EXCLUDED.last_winner_id,
            last_wheel = EXCLUDED.last_wheel,
            last_spin = EXCLUDED.last_spin;
    '''
    with db.cursor() as cur:
        cur.execute(QUERY, (chat_id, ctx.wheel, ctx.action, ctx.last_winner_id, ctx.last_wheel, ctx.last_spin))
        db.commit()


def upsert_user(chat_id: int, user_id: int, udef: UserDef):
    QUERY = '''
        INSERT INTO chat_users
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chat_id, user_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            won_times = EXCLUDED.won_times;
    '''

    with db.cursor() as cur:
        cur.execute(QUERY, (chat_id, user_id, udef.username, udef.won_times))
        db.commit()


def select_non_users(chat_id: int):
    QUERY = '''
        SELECT DISTINCT user_id
        FROM chat_users
        WHERE user_id NOT IN (
            SELECT user_id
            FROM chat_users
            WHERE chat_id = %s
        )
        ;
    '''

    with db.cursor() as cur:
        cur.execute(QUERY, (chat_id,))
        return cur.fetchall()


def get_pretty_username(user: types.User):
    if user.username:
        return f'@{user.username}'
    if user.last_name:
        return f'{user.first_name} {user.last_name}'
    return user.first_name


def update_user_def(message: types.Message, context: ChatContext, user: types.User):
    chat_id = message.chat.id
    user_id = user.id
    name = get_pretty_username(user)
    if user_id not in context.users:
        udef = UserDef(username=name)
        context.users[user_id] = udef
        context.user_ids.append(user_id)
        upsert_user(chat_id, user_id, udef)
        logger.info(f'Added user f{name}[{user_id}] '
            f'to chat {message.chat.title}[{chat_id}]')
    else:
        udef = context.users[user_id]
        udef.username = name
        upsert_user(chat_id, user_id, udef)
        logger.info(f'Updated info for user {name}[{user_id}]')


async def context_filter(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in chats:
        ctx = load_chat(chat_id)
        chats[chat_id] = ctx
        upsert_chat(chat_id, ctx)
        logger.info(f'Added new chat {message.chat.title}[{chat_id}]')
    context = chats[chat_id]

    update_user_def(message, context, message.from_user)

    return {'context': context}


# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

dp.filters_factory.bind(AdminFilter)


@dp.message_handler(context_filter, commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("You spin me right round, baby\n"
                        "Right round like a record, baby\n"
                        "Right round round round!")


TEMPLATES = [
    [
        'Итак, кто же сегодня <b>{wheel} дня</b>?',
        'Хмм, интересно...',
        '<b>АГА</b>!',
        'Сегодня ты <b>{wheel} дня</b>, {user}!'
    ],
    [
        'Эмм... Ты уверен?',
        'Ты <b>точно</b> уверен?',
        'Хотя ладно, процесс уже необратим',
        'Сегодня я назначаю тебе должность <b>{wheel} дня</b>, {user}!'
    ],
    [
        'Ищем рандомного кота на улице...',
        'Ищем палку...',
        'Ищем шапку...',
        'Рисуем ASCII-арт...',
        'Готово!',
        "<pre>"
        ".∧＿∧\n"
        "( ･ω･｡)つ━☆・*。\n"
        "⊂　 ノ 　　　・゜+.\n"
        "しーＪ　　　°。+ *´¨)\n"
        "　　　　　　　　　.· ´¸.·*´¨) ¸.·*¨)\n"
        "　　　　　　　　　　(¸.·´ (¸.·'* ☆ \n"
        "    ВЖУХ, И ТЫ {wheel} ДНЯ, {user}\n"
        "</pre>"
    ],
    [
        'Кручу-верчу, <b>{action}</b> хочу',
        'Сегодня ты <b>{wheel} дня</b>, @{bot}',
        '(нет)',
        'На самом деле, это {user}'
    ],
    [
        '<b>Колесо Сансары запущено!</b>',
        '<i>Что за дичь?!</i>',
        'Ну ок...',
        'Поздравляю, ты <b>{wheel} дня</b>, {user}'
    ]

]


def here_now():
    now = datetime.datetime.utcnow()
    return TIME_ZONE.localize(now)


async def spin_the_wheel(chat: types.Chat, context: ChatContext):
    user_id = random.choice(context.user_ids)
    user_def = context.users[user_id]
    user_def.won_times += 1
    user = user_def.username
    context.last_winner_id = user_id
    context.last_spin = here_now()
    context.last_wheel = context.wheel
    upsert_user(chat.id, user_id, user_def)
    upsert_chat(chat.id, context)
    logger.info(f'Winner: user f{user}[{user_id}] '
                f'in chat {chat.title}[{chat.id}]')

    lines : List[str] = random.choice(TEMPLATES)
    try:
        for line in lines:
            await bot.send_chat_action(chat_id=chat.id,
                                    action=types.ChatActions.TYPING)
            await asyncio.sleep(2)
            await bot.send_message(
                chat_id=chat.id,
                text=line.format(user=user, bot=BOT_NAME,
                                action=context.action,
                                wheel=context.wheel),
                parse_mode='HTML'
            )
            await asyncio.sleep(1)
    except Exception as e:
        logger.warning(f'Error during spinning in chat [{chat.id}]: {e}')


@dp.message_handler(context_filter, commands=['spin'])
async def spin(message: types.Message, context: ChatContext):
    if context.last_spin\
        and context.last_spin.date() == here_now().date()\
        and context.last_winner_id:
        last_user = context.users[context.last_winner_id].username
        await message.answer(
            f'Согласно сегодняшнему розыгрышу, '
            f'<b>{context.wheel} дня</b> — <code>{last_user}</code>',
            parse_mode='HTML')
    else:
        await spin_the_wheel(message.chat, context)


throttle = datetime.timedelta(minutes=10)
last_daily_spin = None

async def daily_spin():
    global last_daily_spin
    now = here_now()
    if last_daily_spin is not None and (now - last_daily_spin) < throttle:
        return
    if here_now().hour < 8:
        return
    logger.info('Running daily spinners!')
    last_daily_spin = now
    for chat_id, context in chats.items():
        ctx : ChatContext = context
        now.date
        if ctx.last_spin and (ctx.last_spin.date == now.date):
            continue
        logger.info(f'Daily spinning for chat [{chat_id}]')
        try:
            chat = await bot.get_chat(chat_id)
            await bot.get_chat_administrators(chat_id)
            await spin(chat, context)
        except Exception as e:
            logger.warning(f'Error during daily spinning in chat [{chat_id}]: {e}')


@dp.message_handler(context_filter, commands=['force_spin'], is_admin=True)
async def force_spin(message: types.Message, context: ChatContext):
    await spin_the_wheel(message.chat, context)
    await daily_spin()


@dp.message_handler(context_filter, commands=['reset_daily'], is_admin=True)
async def reset_daily(message: types.Message, context: ChatContext):
    context.last_spin = None
    upsert_chat(message.chat.id, context)


def html_escape(name: str):
    return name.\
        replace('&', '&amp;').\
        replace('<', '&lt;').\
        replace('>', '&gt;')


@dp.message_handler(context_filter, commands=['setname'], is_admin=True)
async def set_wheel_name(message: types.Message, context: ChatContext):
    if not message.get_args():
        await message.reply("Я программист, меня не обманешь!")
        return
    context.wheel = html_escape(message.get_args())
    upsert_chat(message.chat.id, context)
    await message.reply(f"Текст розыгрыша изменён на {context.wheel}",
                        parse_mode='HTML')


@dp.message_handler(context_filter, commands=['setaction'], is_admin=True)
async def set_action_name(message: types.Message, context: ChatContext):
    if not message.get_args():
        await message.reply("Ну уж нет!")
        return
    context.action = html_escape(message.get_args())
    upsert_chat(message.chat.id, context)
    await message.reply(f"Ты хочешь меня {context.action}?",
                        parse_mode='HTML')


@dp.message_handler(context_filter, commands=['scan'])
async def scan_chat_users(message: types.Message, context: ChatContext):
    logger.debug('Scanning new users')
    new_users = 0
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        for member in admins:
            if member.user.id not in context.users:
                update_user_def(message, context, member.user)
                new_users += 1
        for user_id in select_non_users(message.chat.id):
            try:
                member = await bot.get_chat_member(message.chat.id, user_id)
                update_user_def(message, context, member.user)
                new_users += 1
            except Exception as e:
                logger.warning(f'Error during checking {user_id} in [{message.chat.id}]: {e}')
    except Exception as e:
        logger.warning(f'Error during for users in chat [{message.chat.id}]: {e}')

    await message.answer(f'Found {new_users} new users')


@dp.message_handler(context_filter, commands=['winstats'])
async def win_stats(message: types.Message, context: ChatContext):
    def won_key(user: UserDef):
        return user.won_times
    
    users : List[UserDef] = context.users.values()
    template = '<code>{username}</code>:  {won_times}'
    msg = '\n'.join([
        template.format(username=user.username, won_times=user.won_times) 
        for user in sorted(users, key=won_key, reverse=True)
    ])
    await message.reply(msg, parse_mode='HTML')


@dp.message_handler(commands=['log_level'])
async def log_level(message: types.Message):
    logging.getLogger().setLevel({
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR
        }.get(message.get_args().upper(), logging.INFO)
    )


@dp.message_handler(context_filter, commands=['now'])
async def time_o_clock(message: types.Message):
    await message.reply(f'Now {here_now()}')


@dp.message_handler()
async def any_trigger(message: types.Message):
    await daily_spin()


async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    load_chats()
    # await daily_spin()


async def on_shutdown(dp):
    db.close()


if __name__ == '__main__':
    if HEROKU:
        executor.start_webhook(
            dispatcher=dp,
            webhook_path=WEBHOOK_PATH,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            host=WEBAPP_HOST,
            port=WEBAPP_PORT,
         )
    else:
        executor.start_polling(dp, skip_updates=True)
