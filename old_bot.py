import json
import logging
import os
import random
import string
from pathlib import Path

import telebot
from dotenv import load_dotenv
from telebot import types

import random as random_lib

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

AUDIO_DIR = Path('audio')  
AUDIO_ORIG_DIR = Path('audio/orig')  # Directory with original mp3 files

class QuestionManager:
    def __init__(self, bot):
        logger.info("Initializing QuestionManager")
        self.bot = bot
        self.questions = self._load_questions()
        self.stats = self._load_statistics()
        self.all_answers = [q['correct_answer'] for q in self.questions]
        self.current_options = {}
        self.current_user_id = None  # Store current user ID for global stats
    
    def _load_questions(self):
        try:
            logger.info("Loading questions from questions.json")
            with open('questions.json', 'r', encoding='utf-8') as f:
                questions = json.load(f)
                logger.info(f"Loaded {len(questions)} questions")
                return questions
        except FileNotFoundError:
            logger.warning("questions.json not found. Using empty question list")
            return []
        except json.JSONDecodeError:
            logger.warning("questions.json is not valid JSON. Using empty question list")
            return []
    
    def _load_statistics(self):
        stats_file = Path('data/statistics.json')
        try:
            if stats_file.exists():
                logger.info("Loading existing statistics")
                with open(stats_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Failed to load statistics.json. Creating new statistics")
        
        logger.info("Creating new statistics file")
        stats_file.parent.mkdir(parents=True, exist_ok=True)
        return {}
    
    def _get_user_stats(self, user_id):
        """Get or create statistics for specific user"""
        user_id = str(user_id)
        if user_id not in self.stats:
            # Create new stats for user
            self.stats[user_id] = {}
        
        # Check and add missing question stats
        for question in self.questions:
            q_id = str(question['id'])
            if q_id not in self.stats[user_id]:
                self.stats[user_id][q_id] = {'correct': 0, 'total': 0}
        
        self._save_statistics()  # Save updated statistics
        return self.stats[user_id]
    
    def _save_statistics(self):
        try:
            logger.info("Saving statistics to file")
            stats_file = Path('data/statistics.json')
            stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(stats_file, 'w', encoding='utf-8') as f:
                json.dump(
                    self.stats,
                    f,
                    ensure_ascii=False,
                    indent=4,
                    sort_keys=True
                )
        except IOError:
            logger.warning("Failed to save statistics.json")
    
    def get_random_question(self, user_id):
        if not self.questions:
            logger.warning("No questions available")
            return None
        
        # Calculate weights based on user's error rate
        weights = []
        user_stats = self._get_user_stats(user_id)
        
        for question in self.questions:
            stats = user_stats[str(question['id'])]
            if stats['total'] == 0:
                weights.append(1.0)
            else:
                error_rate = 1 - (stats['correct'] / stats['total'])
                weights.append(0.2 + 0.8 * error_rate)
        
        selected_question = random.choices(self.questions, weights=weights)[0]
        
        # Get all answers with the same tag
        same_tag_answers = [
            q['correct_answer'] 
            for q in self.questions 
            if q['tag'] == selected_question['tag'] and q['id'] != selected_question['id']
        ]
        
        # Generate random options from answers with the same tag
        correct_answer = selected_question['correct_answer']
        
        if same_tag_answers:
            num_wrong_answers = min(3, len(same_tag_answers))
            random_options = random.sample(same_tag_answers, k=num_wrong_answers)
        else:
            logger.warning(f"No other answers found with tag {selected_question['tag']}")
            random_options = []
        
        options = random_options + [correct_answer]
        
        question_data = selected_question.copy()
        question_data['options'] = options
        
        logger.info(
            f"Selected question {question_data['id']} for user {user_id} with "
            f"tag {selected_question['tag']}, "
            f"stats: {user_stats[str(question_data['id'])]}. "
            f"Generated {len(options)} options: {options}"
        )
        return question_data
    
    def update_statistics(self, user_id, question_id, is_correct):
        user_stats = self._get_user_stats(user_id)
        stats = user_stats[str(question_id)]
        old_stats = dict(stats)
        stats['total'] += 1
        if is_correct:
            stats['correct'] += 1
        
        user_info = self.get_user_info(user_id)
        logger.info(
            f"Updated stats for user {user_info}, question {question_id}: "
            f"from {old_stats} to {stats}"
        )
        self._save_statistics()
    
    def get_answer_message(self, question_id, selected_answer):
        """Generate message about user's answer"""
        question = next(q for q in self.questions if q['id'] == question_id)
        correct_answer = question['correct_answer']
        
        # If selected_answer is None, question state was lost
        if selected_answer is None:
            return {
                'first_text': "Состояние вопроса было потеряно. Пожалуйста, начните заново",
                'second_text': None,
                'audio_paths': [],
                'show_next_button': True
            }
        
        is_correct = selected_answer == correct_answer
        
        if is_correct:
            return {
                'first_text': (f"✅ Правильно, *{selected_answer.lower()}*! ✅\n\n"
                                f"{question['explanation']['detailed_text']}"),
                'second_text': None,
                'audio_paths': [],
                'show_next_button': True
            }
        else:
            # Find question that contains selected answer as correct one
            wrong_question = next(
                (q for q in self.questions if q['correct_answer'] == selected_answer),
                None
            )
            
            first_text = (f"❌ *{selected_answer}* — неправильный ответ ❌\n"
                        f"Правильный ответ — *{correct_answer.lower()}*.\n\n"
                        f"{question['explanation']['detailed_text']}\n\n"
                        f"А вот как звучит *{selected_answer.lower()}*:")
            
            second_text = None
            wrong_audio_paths = []
            if wrong_question:
                second_text = wrong_question['explanation']['detailed_text']
                wrong_audio_paths = wrong_question.get('audio_paths', [])
            
            return {
                'first_text': first_text,
                'second_text': second_text,
                'audio_paths': wrong_audio_paths,
                'show_next_button': False
            }
    
    def store_question_options(self, question_id, options):
        """Store the current options for a question"""
        self.current_options[question_id] = options
    
    def get_stored_option(self, question_id, option_index):
        """Get the actual answer text by its index"""
        try:
            return self.current_options[question_id][option_index]
        except (KeyError, IndexError):
            logger.error(f"Failed to get option {option_index} for question {question_id}")
            return None
    
    def check_answer(self, question_id, selected_answer):
        """Check if the selected answer is correct"""
        try:
            question = next(q for q in self.questions if q['id'] == question_id)
            return selected_answer == question['correct_answer']
        except StopIteration:
            logger.error(f"Question with id {question_id} not found")
            return False
    
    def get_user_statistics(self, user_id):
        """Generate statistics message for user"""
        user_stats = self._get_user_stats(user_id)
        
        total_questions = 0
        total_correct = 0
        details = []
        
        # Calculate totals and prepare details for each question
        for question in self.questions:
            q_stats = user_stats[str(question['id'])]
            total_questions += q_stats['total']
            total_correct += q_stats['correct']
            
            if q_stats['total'] > 0:
                percentage = (q_stats['correct'] / q_stats['total']) * 100
                details.append(
                    f"*{question['correct_answer']}*: "
                    f"{q_stats['correct']}/{q_stats['total']} ({percentage:.1f}%)"
                )
        
        # Calculate overall percentage for current user
        user_percentage = (total_correct / total_questions * 100) if total_questions > 0 else 0
        
        # Get all users statistics for ranking
        all_users_stats = {}
        for uid, stats in self.stats.items():
            user_total = 0
            user_correct = 0
            for q_stats in stats.values():
                user_total += q_stats['total']
                user_correct += q_stats['correct']
            
            if user_total > 0:  # Include only users with answers
                percentage = (user_correct / user_total * 100) if user_total > 0 else 0
                try:
                    user = self.bot.get_chat(uid)
                    user_name = f"@{user.username}" if user.username else user.first_name
                    all_users_stats[uid] = {
                        'name': user_name,
                        'percentage': percentage,
                        'total': user_total,
                        'correct': user_correct
                    }
                except Exception as e:
                    logger.error(f"Failed to get user info for {uid}: {e}")
                    continue
        
        # Sort users by number of correct answers, then by total answers
        sorted_users = sorted(
            all_users_stats.items(),
            key=lambda x: (x[1]['correct'], x[1]['total']),
            reverse=True
        )
        
        # Find current user's position and the leader
        user_position = next(
            (i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == str(user_id)),
            len(sorted_users)
        )
        
        leader_info = ""
        if sorted_users:
            leader_id, leader_data = sorted_users[0]
            if leader_id == str(user_id):
                leader_info = "\n🏆 Поздравляем! Вы лидер рейтинга!"
            else:
                leader_info = (f"\n👑 Leader: {leader_data['name']} "
                                f"({leader_data['correct']} correct answers, "
                                f"{leader_data['percentage']:.1f}%)")
        
        # Construct message
        message = [
            "*Ваша статистика:*\n",
            f"Всего ответов: {total_questions}",
            f"Правильных ответов: {total_correct} ({user_percentage:.1f}%)",
            f"Ваше место в рейтинге: {user_position} из {len(sorted_users)}{leader_info}\n",
            "*Статистика по вопросам:*"
        ]
        
        message.extend(details)
        
        return "\n".join(message)
    
    def reset_user_statistics(self, user_id):
        """Reset statistics for specific user"""
        user_id = str(user_id)
        user_info = self.get_user_info(user_id)
        logger.info(f"Resetting statistics for user {user_info}")
        
        # Create new empty statistics for all questions
        self.stats[user_id] = {
            str(q['id']): {'correct': 0, 'total': 0} 
            for q in self.questions
        }
        
        # Save updated statistics
        self._save_statistics()
        logger.info(f"Statistics reset completed for user {user_info}")
    
    def get_global_statistics(self):
        """Generate global statistics message"""
        user_stats = {}
        
        # Collect statistics for each user
        for user_id, stats in self.stats.items():
            total_questions = 0
            total_correct = 0
            
            for q_stats in stats.values():
                total_questions += q_stats['total']
                total_correct += q_stats['correct']
            
            if total_questions > 0:  # Skip users without answers
                percentage = (total_correct / total_questions) * 100
                try:
                    user = self.bot.get_chat(user_id)
                    # Use username if available, otherwise first_name
                    user_name = f"@{user.username}" if user.username else user.first_name
                    user_stats[user_id] = {
                        'name': user_name,
                        'total': total_questions,
                        'correct': total_correct,
                        'percentage': percentage
                    }
                except Exception as e:
                    logger.error(f"Failed to get user info for {user_id}: {e}")
                    continue
        
        # Sort first by number of correct answers, then by total answers
        sorted_stats = sorted(
            user_stats.items(),
            key=lambda x: (x[1]['correct'], x[1]['total']),
            reverse=True
        )
        
        # Limit list to top 20 users
        sorted_stats = sorted_stats[:20]
        
        # Form message
        message = ["*Рейтинг пользователей:*\n"]
        
        # Dictionary for medals (only first three places)
        medals = {
            1: "🥇 Золото",
            2: "🥈 Серебро",
            3: "🥉 Бронза"
        }
        
        for index, (user_id, stats) in enumerate(sorted_stats, 1):
            if index in medals:
                place = f"{medals[index]}: "
            else:
                place = f"{index}-е место: "
            
            # Add "(this is you)" note for the current user
            current_user = " _(this is you)_" if user_id == str(self.current_user_id) else ""
            
            message.append(
                f"{place}*{stats['name']}*{current_user}: всего отвеченных вопросов: {stats['total']}, "
                f"правильно из них: {stats['correct']} ({stats['percentage']:.0f}%)"
            )
        
        if not sorted_stats:
            message.append("Пока нет статистики")
        
        return "\n".join(message)
    
    def get_user_info(self, user_id):
        """Helper function to get user info for logs"""
        try:
            user = self.bot.get_chat(user_id)
            # Skip bot's own messages
            if user.is_bot:
                return f"BOT ({user_id})"
            return f"@{user.username}" if user.username else f"{user.first_name} ({user_id})"
        except Exception as e:
            logger.error(f"Failed to get user info for {user_id}: {e}")
            return str(user_id)

question_manager = QuestionManager(bot)

def check_audio_files():
    """Check if all audio files exist"""
    missing_files = []
    for question in question_manager.questions:
        for audio_path in question.get('audio_paths', []):
            ogg_path = AUDIO_DIR / audio_path
            mp3_path = AUDIO_ORIG_DIR / audio_path.replace('.ogg', '.mp3')
            
            if not ogg_path.exists():
                missing_files.append(f"OGG: {audio_path}")
            if not mp3_path.exists():
                missing_files.append(f"MP3: {audio_path.replace('.ogg', '.mp3')}")
    
    if missing_files:
        logger.warning(f"Missing audio files: {missing_files}")
    return len(missing_files) == 0

def get_user_info(user):
    """Helper function to get user info for logs"""
    # Skip bot's own messages
    if user.is_bot:
        return f"BOT ({user.id})"
    return f"@{user.username}" if user.username else f"{user.first_name} ({user.id})"

def generate_random_filename(original_filename):
    """Generate random filename while preserving extension"""
    # Get the file extension
    extension = Path(original_filename).suffix
    # Generate random string for filename (10 characters)
    letters = string.ascii_lowercase + string.digits
    random_name = ''.join(random_lib.choice(letters) for _ in range(10))
    return f"audio_{random_name}{extension}"

# Function for sending audio with error handling
def send_audio_with_fallback(chat_id, audio_path, user_info):
    """Send audio with fallback to document if voice messages are restricted"""
    try:
        # Remove audio/ prefix if exists
        audio_path = str(audio_path).replace('audio/', '')
        ogg_full_path = AUDIO_DIR / audio_path
        mp3_full_path = AUDIO_ORIG_DIR / audio_path.replace('.ogg', '.mp3')
        
        if not ogg_full_path.exists():
            logger.error(f"Audio file not found: {ogg_full_path}")
            bot.send_message(chat_id, f"Аудио файл не найден")
            return

        with open(ogg_full_path, 'rb') as audio:
            success = False
            try:
                bot.send_voice(chat_id, audio)
                success = True
            except telebot.apihelper.ApiTelegramException as e:
                error_msg = str(e).lower()
                if any(restriction in error_msg for restriction in 
                        ["voice_messages_forbidden", "video messages", "restricted"]):
                    logger.info(f"Voice messages restricted for user {user_info}, trying document")
                    
                    if not mp3_full_path.exists():
                        logger.error(f"Original MP3 file not found: {mp3_full_path}")
                        bot.send_message(chat_id, f"Аудио файл не найден")
                        return
                        
                    try:
                        with open(mp3_full_path, 'rb') as mp3_audio:
                            random_filename = generate_random_filename(mp3_full_path.name)
                            bot.send_document(
                                chat_id, 
                                mp3_audio,
                                visible_file_name=random_filename
                            )
                            success = True
                    except telebot.apihelper.ApiTelegramException as doc_e:
                        logger.error(f"Failed to send document to user {user_info}: {doc_e}")
                else:
                    logger.error(f"Unexpected error for user {user_info}: {e}")
                    raise

            if not success:
                bot.send_message(
                    chat_id,
                    "❌ К сожалению, не удалось отправить аудио. "
                    "Пожалуйста, проверьте настройки конфиденциальности в Telegram."
                )

    except FileNotFoundError as e:
        logger.error(f"File operation error: {e}")
        bot.send_message(chat_id, f"Ошибка при работе с аудио файлом")

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
    
    # Format text with answer options
    options_text = "\n".join([f"{i+1}️⃣ {option}" for i, option in enumerate(options)])
    
    # Create buttons with numbers and emojis
    markup = types.InlineKeyboardMarkup(row_width=len(options))  # All buttons in one row
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    buttons = []
    for i in range(len(options)):
        callback_data = f"{question_data['id']}:{i}"
        buttons.append(types.InlineKeyboardButton(number_emojis[i], callback_data=callback_data))
    markup.add(*buttons) 
    
    logger.info(f"Sending question {question_data['id']} to user {user_info}")
    
    # Send audio files
    audio_paths = question_data.get('audio_paths', [])
    if not audio_paths:
        logger.warning(f"No audio files available for question {question_data['id']}")
        bot.send_message(message.chat.id, "Аудио файлы не найдены!")
    else:
        selected_audio = random.choice(audio_paths)
        logger.info(f"Selected audio file: {selected_audio}")
        send_audio_with_fallback(message.chat.id, selected_audio, user_info)
    
    # Send question and answer options
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
        
        # Create markup with buttons in one row
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🔄 Следующий вопрос", callback_data="next_question"),
            types.InlineKeyboardButton("📊 Моя статистика", callback_data="show_stats")
        )
        
        # Send first message with answer and correct description
        first_message = answer_data['first_text']
        should_show_markup = is_correct or answer_data.get('show_next_button', False)
        
        bot.send_message(
            call.message.chat.id, 
            first_message, 
            parse_mode='Markdown',
            reply_markup=markup if should_show_markup else None
        )
        
        # If there is audio for wrong answer, send it
        if answer_data['audio_paths']:
            selected_audio = random.choice(answer_data['audio_paths'])
            audio_path = AUDIO_DIR / selected_audio
            send_audio_with_fallback(call.message.chat.id, audio_path, user_info)
        
        # Send second message with wrong answer description and buttons
        if answer_data['second_text']:
            bot.send_message(
                call.message.chat.id,
                answer_data['second_text'],
                parse_mode='Markdown',
                reply_markup=markup  # Add buttons to last message for wrong answer
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
        markup = types.InlineKeyboardMarkup(row_width=2)
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
        # Сохраняем ID текущего пользователя перед вызовом метода
        question_manager.current_user_id = call.from_user.id
        stats_message = question_manager.get_global_statistics()
        markup = types.InlineKeyboardMarkup(row_width=2)
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