# -*- coding: utf-8 -*-
import logging
import os
from dataclasses import dataclass, field
import datetime
import random
from typing import List, Dict, Set
import asyncio

from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.filters import BoundFilter

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

class AdminFilter(BoundFilter):
    key = 'is_admin'

    def __init__(self, is_admin):
        self.is_admin = is_admin

    async def check(self, message: types.Message):
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.is_chat_admin()


# Initialize bot and dispatcher
BOT_NAME = os.environ['BOT_NAME']
BOT_TOKEN = os.environ['BOT_TOKEN']

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

dp.filters_factory.bind(AdminFilter)


@dataclass
class ChatContext:
    wheel : str = 'пижма'
    action : str = 'запутать'
    last_winner : str = None
    last_spin : datetime.date = None
    users : List[str] = field(default_factory=list)
    users_set : Set[str] = field(default_factory=set)

chats : Dict[str, ChatContext] = {}


def get_pretty_username(user: types.User):
    if user.username:
        return f'@{user.username}'
    if user.last_name:
        return f'{user.first_name} {user.last_name}'
    return user.first_name


async def context_filter(message: types.Message):
    if message.chat.id not in chats:
        chats[message.chat.id] = ChatContext()
        logger.info(f'Added new chat {message.chat.title}[{message.chat.id}]')
    context = chats[message.chat.id]
    name = get_pretty_username(message.from_user)
    if name not in context.users_set:
        context.users.append(name)
        context.users_set.add(name)
        logger.info(f'Added user f{name}[{message.from_user.id}] '
                    f'to chat {message.chat.title}[{message.chat.id}]')
    return {'context': context, 'name': name}


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


async def spin_the_wheel(message: types.Message, context: ChatContext):
    user = random.choice(context.users)
    context.last_winner = user
    context.last_spin = datetime.datetime.today().date()
    logger.info(f'Winner: user f{user}[{message.from_user.id}] '
                f'in chat {message.chat.title}[{message.chat.id}]')

    lines : List[str] = random.choice(TEMPLATES)
    for line in lines:
        await bot.send_chat_action(chat_id=message.chat.id,
                                   action=types.ChatActions.TYPING)
        await asyncio.sleep(2)
        await message.answer(text=line.format(user=user, bot=BOT_NAME,
                                              action=context.action,
                                              wheel=context.wheel),
                             parse_mode='HTML')
        await asyncio.sleep(1)


@dp.message_handler(context_filter, commands=['spin'])
async def spin(message: types.Message, context: ChatContext):
    if context.last_spin == datetime.datetime.today().date() and context.last_winner:
        await message.answer(
            f'Согласно сегодняшнему розыгрышу, '
            f'<b>{context.wheel} дня</b> — <code>{context.last_winner}</code>',
            parse_mode='HTML')
    else:
        await spin_the_wheel(message, context)


@dp.message_handler(context_filter, commands=['force_spin'])
async def force_spin(message: types.Message, context: ChatContext):
    await spin_the_wheel(message, context)


def canonize(name: str):
    return name.\
        replace('&', '&amp;').\
        replace('<', '&lt;').\
        replace('>', '&gt;')


@dp.message_handler(context_filter, commands=['setname'], is_admin=True)
async def set_wheel_name(message: types.Message, context: ChatContext):
    if not message.get_args():
        await message.reply("Я программист, меня не обманешь!")
        return
    context.wheel = canonize(message.get_args())
    await message.reply(f"Текст розыгрыша изменён на {context.wheel}",
                        parse_mode='HTML')


@dp.message_handler(context_filter, commands=['setaction'], is_admin=True)
async def set_action_name(message: types.Message, context: ChatContext):
    if not message.get_args():
        await message.reply("Ну уж нет!")
        return
    context.action = canonize(message.get_args())
    await message.reply(f"Ты хочешь меня {context.action}?",
                        parse_mode='HTML')


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
