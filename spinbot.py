# -*- coding: utf-8 -*-
import logging
import os
from dataclasses import dataclass
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
    last_winner : str
    last_spin : datetime.date
    users : Set[str] = set()

chats : Dict[str, ChatContext] = {}


def get_pretty_username(user: types.User):
    if user.username:
        return f'@{user.username}'
    if user.last_name:
        return f'{user.first_name} {user.last_name}'
    return user.first_name


async def context_filter(message: types.Message):
    context = chats.setdefault(message.chat.id, ChatContext())    
    name = get_pretty_username(message.from_user)
    context.users.add(name)
    return {'context': context, 'name': name}


@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Я пижма весёлая, спелая, садовая!")


TEMPLATES = [
    [
        'Итак, кто же сегодня *{wheel} дня*?',
        'Хмм, интересно...',
        '*АГА*!',
        'Сегодня ты *{wheel} дня*, {user}!'
    ],
    [
        'Эмм... Ты уверен?',
        'Ты *точно* уверен?',
        'Хотя ладно, процесс уже необратим',
        'Сегодня я назначаю тебе должность *{wheel} дня*, {user}!'
    ],
    [
        'Ищем рандомного кота на улице...',
        'Ищем палку...',
        'Ищем шапку...',
        'Рисуем ASCII-арт...',
        'Готово!',
        "```"
        ".∧＿∧"
        "( ･ω･｡)つ━☆・*。"
        "⊂　 ノ 　　　・゜+."
        "しーＪ　　　°。+ *´¨)"
        "　　　　　　　　　.· ´¸.·*´¨) ¸.·*¨)"
        "　　　　　　　　　　(¸.·´ (¸.·'* ☆ "
        "       ВЖУХ, И ТЫ {wheel} ДНЯ, {user}"
        "```"
    ],
    [
        'Кручу-верчу, *{action}* хочу',
        'Сегодня ты *{wheel} дня*, {bot}',
        '(нет)',
        'На самом деле, это {user}'
    ],
    [
        '*Колесо Сансары запущено!*',
        '*Что за дичь?!_',
        'Ну ок...',
        'Поздравляю, ты *{wheel} дня*, {user}'
    ]

]


async def spin_the_wheel(message: types.Message, context: ChatContext):
    user = random.choice(context.users)
    context.last_winner = user
    context.last_spin = datetime.datetime.today().date()
    lines : List[str] = random.choice(TEMPLATES)
    for line in lines:
        await bot.send_chat_action(chat_id=message.chat_id,
                                   action=types.ChatActions.TYPING)
        await asyncio.sleep(2)
        await message.answer(text=line.format(user=user, bot=BOT_NAME,
                                              action=context.action,
                                              wheel=context.wheel),
                             parse_mode='MarkdownV2')
        await asyncio.sleep(1)


@dp.message_handler(commands=['spin'])
@dp.message_handler(context_filter)
async def spin(message: types.Message, context: ChatContext):
    if context.last_spin == datetime.datetime.today().date():
        await message.answer(
            f'Согласно сегодняшнему розыгрышу, '
            f'*{context.wheel} дня* — `@{context.last_winner}`',
            parse_mode='MarkdownV2')
    else:
        spin_the_wheel(message, context)


@dp.message_handler(commands=['force_spin'])
@dp.message_handler(context_filter)
async def force_spin(message: types.Message, context: ChatContext):
    await spin_the_wheel(message, context)


@dp.message_handler(commands=['setname'])
@dp.message_handler(is_admin=True)
@dp.message_handler(context_filter)
async def set_wheel_name(message: types.Message, context: ChatContext):
    context.name_of_the_day = message.text
    await message.reply(f"Текст розыгрыша изменён на {context.name_of_the_day}")


@dp.message_handler(commands=['setaction'])
@dp.message_handler(is_admin=True)
@dp.message_handler(context_filter)
async def set_action_name(message: types.Message, context: ChatContext):
    context.action = message.text
    await message.reply(f"Ты хочешь меня {context.action}?")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
