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

question_manager = QuestionManager(bot)
AUDIO_DIR = Path('audio')  

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

def get_user_info(user):
    """Helper function to get user info for logs"""
    # Skip bot's own messages
    if user.is_bot:
        return f"BOT ({user.id})"
    return f"@{user.username}" if user.username else f"{user.first_name} ({user.id})"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    logger.info(f"New user started the bot: {message.from_user.id}")
    bot.reply_to(message, "Добро пожаловать! Начинаем.")
    send_question(message)

@bot.message_handler(commands=['question'])
def send_question(message):
    user_info = get_user_info(message.from_user)
    logger.info(f"User {user_info} requested a question")
    question_data = question_manager.get_random_question(message.from_user.id)
    
    if not question_data:
        bot.reply_to(message, "Вопросы не найдены.")
        return
    
    options = question_data['options']
    random.shuffle(options)
    
    question_manager.store_question_options(question_data['id'], options)
    
    # Формируем текст с вариантами ответов
    options_text = "\n".join([f"{i+1}️⃣ {option}" for i, option in enumerate(options)])
    
    # Создаем кнопки с номерами и эмодзи
    markup = types.InlineKeyboardMarkup(row_width=len(options))  # все кнопки в одну строку
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    buttons = []
    for i in range(len(options)):
        callback_data = f"{question_data['id']}:{i}"
        buttons.append(types.InlineKeyboardButton(number_emojis[i], callback_data=callback_data))
    markup.add(*buttons) 
    
    logger.info(f"Sending question {question_data['id']} to user {user_info}")
    
    # Отправляем аудио
    audio_paths = question_data.get('audio_paths', [])
    if not audio_paths:
        logger.warning(f"No audio files available for question {question_data['id']}")
        bot.send_message(message.chat.id, "Аудио файлы не найдены!")
    else:
        selected_audio = random.choice(audio_paths)
        audio_path = AUDIO_DIR / selected_audio
        logger.info(f"Selected audio file: {audio_path}")
        
        try:
            with open(audio_path, 'rb') as audio:
                try:
                    bot.send_voice(message.chat.id, audio)
                except telebot.apihelper.ApiTelegramException as e:
                    error_msg = str(e).lower()
                    if any(restriction in error_msg for restriction in 
                          ["voice_messages_forbidden", "video messages", "restricted"]):
                        logger.info(f"Media messages restricted for user {user_info}, sending as audio file")
                        bot.send_message(
                            message.chat.id, 
                            "⚠️ Похоже, у вас ограничен прием медиа-сообщений. "
                            "Отправляю файл в другом формате."
                        )
                        audio.seek(0)
                        try:
                            bot.send_audio(message.chat.id, audio)
                        except telebot.apihelper.ApiTelegramException as audio_e:
                            logger.error(f"Failed to send audio file to user {user_info}: {audio_e}")
                            bot.send_message(
                                message.chat.id,
                                "❌ К сожалению, не удалось отправить аудио. "
                                "Пожалуйста, проверьте настройки конфиденциальности в Telegram."
                            )
                    else:
                        raise
        except FileNotFoundError:
            logger.warning(f"Audio file not found: {audio_path}")
            bot.send_message(message.chat.id, f"Аудио файл не найден")
    
    # Отправляем вопрос и варианты ответов
    message_text = f"❓ {question_data['text']}\n\n{options_text}"
    bot.send_message(message.chat.id, message_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_answer(call):
    # First check if this is a service button (stats, next question, etc)
    if call.data in ["next_question", "show_stats", "reset_stats", "show_global_stats"]:
        handle_post_answer_buttons(call)
        return

    # Then handle answer buttons
    try:
        question_id, option_index = call.data.split(':')
        question_id = int(question_id)
        option_index = int(option_index)
        
        selected_answer = question_manager.get_stored_option(question_id, option_index)
        is_correct = question_manager.check_answer(question_id, selected_answer)
        
        user_info = get_user_info(call.from_user)
        logger.info(
            f"User {user_info} answered question {question_id}. "
            f"Answer: {selected_answer}. Correct: {is_correct}"
        )
        
        # Get detailed answer message
        answer_data = question_manager.get_answer_message(question_id, selected_answer)
        
        # Show brief response in popup
        response = "Правильно! ✅" if is_correct else "Неправильно! ❌"
        bot.answer_callback_query(call.id, response)
        
        # Создаем разметку с кнопками в одну строку
        markup = types.InlineKeyboardMarkup(row_width=2)  # Changed to 2 for buttons in one row
        markup.add(
            types.InlineKeyboardButton("🔄 Следующий вопрос", callback_data="next_question"),
            types.InlineKeyboardButton("📊 Моя статистика", callback_data="show_stats")
        )
        
        # Отправляем первое сообщение с ответом и правильным описанием
        first_message = answer_data['first_text']
        should_show_markup = is_correct or answer_data.get('show_next_button', False)
        
        bot.send_message(
            call.message.chat.id, 
            first_message, 
            parse_mode='Markdown',
            reply_markup=markup if should_show_markup else None
        )
        
        # Если есть аудио неправильного ответа, отправляем его
        if answer_data['audio_paths']:
            selected_audio = random.choice(answer_data['audio_paths'])
            audio_path = AUDIO_DIR / selected_audio
            try:
                with open(audio_path, 'rb') as audio:
                    try:
                        bot.send_voice(call.message.chat.id, audio)
                    except telebot.apihelper.ApiTelegramException as e:
                        error_msg = str(e).lower()
                        if any(restriction in error_msg for restriction in 
                              ["voice_messages_forbidden", "video messages", "restricted"]):
                            logger.info(f"Media messages restricted for user {get_user_info(call.from_user)}, sending as audio file")
                            bot.send_message(
                                call.message.chat.id,
                                "⚠️ Похоже, у вас ограничен прием медиа-сообщений. "
                                "Отправляю файл в другом формате."
                            )
                            audio.seek(0)
                            try:
                                bot.send_audio(call.message.chat.id, audio)
                            except telebot.apihelper.ApiTelegramException as audio_e:
                                logger.error(f"Failed to send audio file: {audio_e}")
                                bot.send_message(
                                    call.message.chat.id,
                                    "❌ К сожалению, не удалось отправить аудио. "
                                    "Пожалуйста, проверьте настройки конфиденциальности в Telegram."
                                )
                        else:
                            raise
            except FileNotFoundError:
                logger.warning(f"Audio file not found: {audio_path}")
        
        # Отправляем второе сообщение с описанием неправильного ответа и кнопками
        if answer_data['second_text']:
            bot.send_message(
                call.message.chat.id,
                answer_data['second_text'],
                parse_mode='Markdown',
                reply_markup=markup  # Для неправильного ответа добавляем кнопки к последнему сообщению
            )
        
        # Update user's statistics only if we have a valid answer
        if selected_answer is not None:
            question_manager.update_statistics(call.from_user.id, question_id, is_correct)
            
    except ValueError as e:
        logger.error(f"Invalid callback data format: {call.data}")
        bot.answer_callback_query(call.id, "Произошла ошибка. Попробуйте еще раз.")
        return

@bot.message_handler(commands=['stats'])
def send_global_stats(message):
    """Send global statistics"""
    stats_message = question_manager.get_global_statistics()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔄 К вопросам", callback_data="next_question")
    )
    bot.send_message(
        message.chat.id,
        stats_message,
        parse_mode='Markdown',
        reply_markup=markup
    )

# И добавим кнопку общей статиски в существующие меню
def get_stats_markup():
    """Create markup with statistics buttons"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔄 Следующий вопрос", callback_data="next_question"),
        types.InlineKeyboardButton("📊 Моя статистика", callback_data="show_stats"),
        types.InlineKeyboardButton("🌍 Общая статистика", callback_data="show_global_stats")
    )
    return markup

@bot.callback_query_handler(func=lambda call: call.data in ["next_question", "show_stats", "reset_stats", "show_global_stats"])
def handle_post_answer_buttons(call):
    if call.data == "next_question":
        send_question(call.message)
    elif call.data == "show_stats":
        stats_message = question_manager.get_user_statistics(call.from_user.id)
        markup = types.InlineKeyboardMarkup(row_width=2)  # Changed to 2 for one row
        markup.add(
            types.InlineKeyboardButton("🔄 Следующий вопрос", callback_data="next_question"),
            types.InlineKeyboardButton("🌍 Общая статистика", callback_data="show_global_stats"),
            types.InlineKeyboardButton("🎯 Сбросить статистику", callback_data="reset_stats")
        )
        bot.send_message(
            call.message.chat.id,
            stats_message,
            parse_mode='Markdown',
            reply_markup=markup
        )
    elif call.data == "show_global_stats":
        stats_message = question_manager.get_global_statistics()
        markup = types.InlineKeyboardMarkup(row_width=2)  # Changed to 2 for one row
        markup.add(
            types.InlineKeyboardButton("🔄 К вопросам", callback_data="next_question"),
            types.InlineKeyboardButton("📊 Моя статистика", callback_data="show_stats")
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
            "✨ Cтатистика сброшена.",
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