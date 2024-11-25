import telebot
from telebot import types
import random
import json
from question_manager import QuestionManager
from dotenv import load_dotenv
import os
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

question_manager = QuestionManager()
AUDIO_DIR = Path('audio')  # или укажите правильный путь к вашей директории с аудио

def check_audio_files():
    """Check if all audio files exist"""
    missing_files = []
    for question in question_manager.questions:
        for audio_path in question.get('audio_paths', []):
            full_path = AUDIO_DIR / audio_path
            if not full_path.exists():
                missing_files.append(audio_path)
    
    if missing_files:
        logger.warning(f"Missing audio files: {missing_files}")
    return len(missing_files) == 0

@bot.message_handler(commands=['start'])
def send_welcome(message):
    logger.info(f"New user started the bot: {message.from_user.id}")
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🎯 Начать тестирование", callback_data="next_question"))
    bot.reply_to(message, "Добро пожаловать! Нажмите кнопку, чтобы начать тестирование.", reply_markup=markup)

@bot.message_handler(commands=['question'])
def send_question(message):
    logger.info(f"User {message.from_user.id} requested a question")
    question_data = question_manager.get_random_question(message.from_user.id)
    
    if not question_data:
        bot.reply_to(message, "К сожалению, вопросы не найдены.")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    options = question_data['options']
    random.shuffle(options)
    
    question_manager.store_question_options(question_data['id'], options)
    
    # Add each option as a simple button
    for i, option in enumerate(options):
        callback_data = f"{question_data['id']}:{i}"
        markup.add(types.InlineKeyboardButton(option, callback_data=callback_data))
    
    logger.info(f"Sending question {question_data['id']} to user {message.from_user.id}")
    
    # Select random audio file from available paths
    audio_paths = question_data.get('audio_paths', [])
    if not audio_paths:
        logger.warning(f"No audio files available for question {question_data['id']}")
        bot.send_message(message.chat.id, "К сожалению, аудио файлы не найдены, но вы можете продолжить отвечать на вопрос.")
    else:
        selected_audio = random.choice(audio_paths)
        audio_path = AUDIO_DIR / selected_audio  # создаем полный путь к файлу
        logger.info(f"Selected audio file: {audio_path}")
        
        try:
            with open(audio_path, 'rb') as audio:
                try:
                    # Сначала пробуем отправить как голосовое сообщение
                    bot.send_voice(message.chat.id, audio)
                except telebot.apihelper.ApiTelegramException as e:
                    if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                        logger.info(f"Voice messages forbidden for user {message.from_user.id}, sending as audio file")
                        bot.send_message(message.chat.id, "Вам запрещено присылать голосовые сообщения, отправляю файл.")
                        # Перемотаем файл в начало и отправим как обычный аудио файл
                        audio.seek(0)
                        bot.send_audio(message.chat.id, audio)
                    else:
                        raise  # Если это другая ошибка API, пробросим её дальше
        except FileNotFoundError:
            logger.warning(f"Audio file not found: {audio_path}")
            bot.send_message(message.chat.id, "Выбранный аудио файл не найден, но вы можете продолжить отвечать на вопрос.")
    
    bot.send_message(message.chat.id, f"❓ {question_data['text']}", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_answer(call):
    if call.data in ["next_question", "show_stats", "reset_stats"]:
        handle_post_answer_buttons(call)
        return

    question_id, option_index = call.data.split(':')
    question_id = int(question_id)
    option_index = int(option_index)
    
    selected_answer = question_manager.get_stored_option(question_id, option_index)
    is_correct = question_manager.check_answer(question_id, selected_answer)
    
    logger.info(
        f"User {call.from_user.id} answered question {question_id}. "
        f"Answer: {selected_answer}. Correct: {is_correct}"
    )
    
    # Get detailed answer message
    answer_message = question_manager.get_answer_message(question_id, selected_answer)
    
    # Show brief response in popup
    response = "Правильно! ✅" if is_correct else "Неправильно! ❌"
    bot.answer_callback_query(call.id, response)
    
    # Create markup with "Next Question" and "Statistics" buttons
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔄 Следующий вопрос", callback_data="next_question"),
        types.InlineKeyboardButton("📊 Моя статистика", callback_data="show_stats")
    )
    
    # Send detailed explanation with new buttons
    bot.send_message(
        call.message.chat.id, 
        answer_message, 
        parse_mode='Markdown',
        reply_markup=markup
    )
    
    # Update user's statistics
    question_manager.update_statistics(call.from_user.id, question_id, is_correct)

@bot.callback_query_handler(func=lambda call: call.data in ["next_question", "show_stats", "reset_stats"])
def handle_post_answer_buttons(call):
    if call.data == "next_question":
        send_question(call.message)
    elif call.data == "show_stats":
        stats_message = question_manager.get_user_statistics(call.from_user.id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔄 Следующий вопрос", callback_data="next_question"),
            types.InlineKeyboardButton("🎯 Сбросить статистику", callback_data="reset_stats")
        )
        bot.send_message(
            call.message.chat.id,
            stats_message,
            parse_mode='Markdown',
            reply_markup=markup
        )
    elif call.data == "reset_stats":
        question_manager.reset_user_statistics(call.from_user.id)
        bot.answer_callback_query(call.id, "Статистика сброшена!")
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔄 Начать заново", callback_data="next_question"))
        bot.send_message(
            call.message.chat.id,
            "✨ Ваша статистика была успешно сброшена. Можете начать тестирование заново!",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

if __name__ == '__main__':
    logger.info("Bot started")
    if not AUDIO_DIR.exists():
        logger.error(f"Audio directory not found: {AUDIO_DIR}")
    else:
        check_audio_files()
    bot.infinity_polling() 