# flake8: noqa
# pylint: disable=broad-exception-raised, raise-missing-from, too-many-arguments, redefined-outer-name
# pylance: disable=reportMissingImports, reportMissingModuleSource, reportGeneralTypeIssues
# type: ignore

import threading
import random
import json
import logging
import signal
import os
import sys
from pathlib import Path
import glob
import time

from typing import List, Dict, Any
import telebot
from telebot import types
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

bot_token = os.getenv('BOT_TOKEN')
if not bot_token:
    logger.error("BOT_TOKEN environment variable is not set.")
    sys.exit("Error: BOT_TOKEN environment variable is not set.")
logger.info(f"Bot init, token: {bot_token}")
bot = telebot.TeleBot(bot_token)


# Global dictionary to store sessions
sessions = {}
sessions_lock = threading.Lock()

def get_position_emoji(position: int) -> str:
    emojis = ["", "🥇", "🥈", "🥉"]
    return emojis[position] if 1 <= position <= 3 else ""

def get_number_emoji(number: int) -> str:
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    return emojis[number - 1] if 1 <= number <= 10 else str(number)


def get_user_info(user) -> str:
    """Helper function to get user info for logs"""
    if user.username:
        return f"@{user.username} ({user.id})"
    return f"{user.first_name} ({user.id})"

class UserSession:
    SESSIONS_DIR = Path('data/user_sessions')

    def __init__(self, user):
        self.user_info = get_user_info(user)
        self.user_id = user.id
        self.user_name = user.username or user.first_name
        self.lock = threading.Lock()

        # Create all necessary directories
        self.SESSIONS_DIR.parent.mkdir(exist_ok=True)  # Create 'data' directory
        self.SESSIONS_DIR.mkdir(exist_ok=True)  # Create 'user_sessions' directory

        # Define session file path
        self.session_file = self.SESSIONS_DIR / f"user_{self.user_id}.json"

        # Try to load existing session
        if self.session_file.exists():
            logger.info(f"Loading existing session for user {self.user_info}")
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                logger.info(f"Loaded session data for user {self.user_info}")
            except Exception as e:
                logger.error(f"Failed to load session for user {self.user_info}: {e}")
                self.data = {}
        else:
            logger.info(f"Creating new session for user {self.user_info}")
            self.data = {}

        # Always update user info in data
        self.data.update({ 'user_id': self.user_id, 'user_name': self.user_name, 'last_update': time.time() })
        self.save_session()  # Save immediately to ensure user data is stored

        try:
            self.question_selector = QuestionSelector()
            logger.info(f"QuestionSelector initialized for user {self.user_info}")
        except Exception as e:
            logger.error(f"Failed to initialize QuestionSelector for user {self.user_info}: {e}")
            raise

    def save_session(self):
        """Save session data to file"""
        try:
            # Ensure directory exists
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved session data for user {self.user_info}")
        except Exception as e:
            logger.error(f"Failed to save session for user {self.user_info}: {e}")

    def set_last_question(self, question: Dict[str, Any]):
        with self.lock:
            self.data['last_question'] = question
            logger.info(f"Set last question for user {self.user_info}: Question ID {question['question_id']}")
            self.save_session()

    def get_last_question(self) -> Dict[str, Any]:
        with self.lock:
            return self.data.get('last_question')

    def clear_last_question(self):
        with self.lock:
            if 'last_question' in self.data:
                del self.data['last_question']
                logger.info(f"Cleared last_question for user {self.user_info}")
                self.save_session()
    def reset_session(self):
        with self.lock:            
            # Delete session file
            try:
                if self.session_file.exists():
                    self.session_file.unlink()
                    logger.info(f"Deleted session file for user {self.user_info}")
            except Exception as e:
                logger.error(f"Failed to delete session file for user {self.user_info}: {e}")

        # Reset session data
        self.data = {}
        logger.info(f"Session reset for user {self.user_info}")
        self.save_session()

    def update_question_stats(self, question_id: int, is_correct: bool, theme: str):
        """Update statistics for the given question"""
        with self.lock:
            if 'theme_stats' not in self.data:
                self.data['theme_stats'] = {}
            
            # Initialize theme stats if not exists
            if theme not in self.data['theme_stats']:
                self.data['theme_stats'][theme] = { 'question_stats': {}, 'total': 0, 'correct': 0 }
            
            theme_stats = self.data['theme_stats'][theme]
            
            # Update question-specific stats
            q_id = str(question_id)
            if q_id not in theme_stats['question_stats']:
                theme_stats['question_stats'][q_id] = {'total': 0, 'correct': 0}
            
            theme_stats['question_stats'][q_id]['total'] += 1
            theme_stats['total'] += 1
            
            if is_correct:
                theme_stats['question_stats'][q_id]['correct'] += 1
                theme_stats['correct'] += 1
            
            logger.info(
                f"Updated stats for user {self.user_info}, theme {theme}, question {question_id}: "
                f"total={theme_stats['question_stats'][q_id]['total']}, "
                f"correct={theme_stats['question_stats'][q_id]['correct']}"
            )
            self.save_session()

    def get_statistics(self):
        """Get detailed statistics for all themes"""
        with self.lock:
            theme_stats = self.data.get('theme_stats', {})
            
            # Calculate per-theme statistics
            themes_stats = []
            for theme_tag, theme_data in self.question_selector.themes.items():
                theme_name = theme_data['name']
                stats = theme_stats.get(theme_tag, {'total': 0, 'correct': 0, 'question_stats': {}})
                
                if stats['total'] > 0:
                    theme_percentage = (stats['correct'] / stats['total']) * 100 if stats['total'] > 0 else 0
                    
                    # Get per-question stats for this theme
                    question_stats = []
                    for q in theme_data['questions']:
                        q_id = str(q['id'])
                        q_stats = stats['question_stats'].get(q_id, {'total': 0, 'correct': 0})
                        
                        if q_stats['total'] > 0:
                            percentage = (q_stats['correct'] / q_stats['total']) * 100
                            question_stats.append({
                                'question': q['correct_answer'],
                                'total': q_stats['total'],
                                'correct': q_stats['correct'],
                                'percentage': percentage
                            })
                    
                    themes_stats.append({
                        'theme_name': theme_name,
                        'theme_tag': theme_tag,
                        'total': stats['total'],
                        'correct': stats['correct'],
                        'percentage': theme_percentage,
                        'questions': sorted(question_stats, key=lambda x: x['percentage'], reverse=True)
                    })
            
            return {
                'themes': sorted(themes_stats, key=lambda x: x['percentage'], reverse=True)
            }

    def set_theme(self, theme: str):
        """Set current theme in user data"""
        with self.lock:
            self.data['current_theme'] = theme
            logger.info(f"Set theme for user {self.user_info}: {theme}")
            self.save_session()

    def get_theme(self) -> str:
        """Get current theme from user data"""
        with self.lock:
            return self.data.get('current_theme')

def validate_theme_data(theme_data):
    """Validate theme data structure"""
    # Check required top-level fields
    required_fields = ['tag', 'name', 'questions']
    for field in required_fields:
        if field not in theme_data:
            raise ValueError(f"Missing required field '{field}' in theme data")
            
    # Validate questions array
    if not isinstance(theme_data['questions'], list):
        raise ValueError("'questions' must be an array")
        
    # Validate each question
    for question in theme_data['questions']:
        # Check required question fields
        required_question_fields = ['id', 'text', 'correct_answer']
        for field in required_question_fields:
            if field not in question:
                raise ValueError(f"Question missing required field '{field}'")
                
        # Validate ID is integer
        if not isinstance(question['id'], int):
            raise ValueError(f"Question ID must be an integer, got {type(question['id'])}")
            
        # Validate text and correct_answer are strings
        if not isinstance(question['text'], str):
            raise ValueError(f"Question text must be a string, got {type(question['text'])}")
        if not isinstance(question['correct_answer'], str):
            raise ValueError(f"Question correct_answer must be a string, got {type(question['correct_answer'])}")
            
        # Validate audio_paths if present
        if 'audio_paths' in question:
            if not isinstance(question['audio_paths'], list):
                raise ValueError("audio_paths must be an array")
            for path in question['audio_paths']:
                if not isinstance(path, str):
                    raise ValueError(f"Audio path must be a string, got {type(path)}")
                    
        # Validate explanation if present
        if 'explanation' in question:
            if not isinstance(question['explanation'], list):
                raise ValueError("explanation must be an array")
            for exp in question['explanation']:
                if not isinstance(exp, str):
                    raise ValueError(f"Explanation must be a string, got {type(exp)}")

class QuestionSelector:
    def __init__(self):
        self.themes = {}
        self.current_theme = None
        folder = "questions"
        logger.info(f"Loading questions from {os.path.abspath(folder)}")
        theme_files = glob.glob(os.path.join(folder, '*.json'))
        logger.info(f"Found {len(theme_files)} theme files")
        
        # Add audio folder check
        audio_folder = Path("audio")
        if not audio_folder.exists():
            logger.error(f"Audio folder not found at {audio_folder.absolute()}")
            raise ValueError(f"Audio folder not found at {audio_folder.absolute()}")
        
        for file_path in theme_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    theme_data = json.load(f)
                    
                    # Validate theme data structure
                    validate_theme_data(theme_data)
                    
                    theme_tag = theme_data.get('tag')
                    if not theme_tag:
                        logger.warning(f"No tag found in {file_path}, using filename")
                        theme_tag = Path(file_path).stem
                    
                    # Check audio files before adding theme
                    missing_files = self._check_audio_files(theme_data.get('questions', []))
                    if missing_files:
                        files_str = "\n".join(f"- {f}" for f in missing_files)
                        logger.error(f"Missing audio files for theme '{theme_tag}':\n{files_str}")
                        raise ValueError(f"Missing audio files for theme '{theme_tag}':\n{files_str}")

                    # Check for duplicate question IDs within this theme
                    questions = theme_data.get('questions', [])
                    seen_question_ids = set()
                    for question in questions:
                        question_id = question.get('id')
                        if not question_id:
                            logger.error(f"Question ID missing in theme '{theme_tag}'")
                            raise ValueError(f"Question ID missing in theme '{theme_tag}'")
                        
                        if question_id in seen_question_ids:
                            logger.error(f"Duplicate question ID {question_id} found within theme '{theme_tag}'")
                            raise ValueError(f"Duplicate question ID {question_id} found within theme '{theme_tag}'")
                        seen_question_ids.add(question_id)
                        
                    self.themes[theme_tag] = {
                        'name': theme_data.get('name', theme_tag),
                        'questions': questions
                    }
                logger.info(f"Loaded {len(self.themes[theme_tag]['questions'])} questions for theme '{theme_tag}' ({self.themes[theme_tag]['name']})")
            except Exception as e:
                logger.error(f"Failed to load questions from {file_path}: {e}")
                raise

    def _check_audio_files(self, questions) -> list:
        """Check if all required audio files exist"""
        missing_files = []
        for question in questions:
            if 'audio_paths' in question:
                for audio_path in question['audio_paths']:
                    full_path = Path('audio') / audio_path
                    if not full_path.exists():
                        missing_files.append(audio_path)
        return missing_files

    def set_theme(self, theme_tag: str) -> bool:
        if theme_tag in self.themes:
            self.current_theme = theme_tag
            logger.info(f"Set current theme to '{theme_tag}' ({self.themes[theme_tag]['name']})")
            return True
        logger.warning(f"Theme '{theme_tag}' not found")
        return False

    def get_current_theme(self) -> Dict[str, str]:
        if self.current_theme:
            return { 'tag': self.current_theme, 'name': self.themes[self.current_theme]['name'] }
        return None

    def get_random_question(self, num_options: int = 4) -> Dict[str, Any]:
        if not self.themes:
            logger.error("No themes available")
            raise ValueError("No themes available.")

        if not self.current_theme:
            logger.error("No theme selected")
            raise ValueError("Тематика не выбрана. Пожалуйста, нажмите /start")
            
        theme_data = self.themes[self.current_theme]
        questions = theme_data['questions']

        if not questions:
            logger.error(f"No questions available in theme '{self.current_theme}'")
            raise ValueError(f"No questions available in theme '{self.current_theme}'.")

        question = random.choice(questions)
        logger.debug(f"Selected question ID: {question['id']} from theme '{self.current_theme}'")
        correct_answer = question['correct_answer']

        # Randomly select one audio file if multiple are available
        audio_file = None
        if question.get('audio_paths'):
            audio_file = random.choice(question['audio_paths'])
            logger.debug(f"Selected audio file {audio_file} for question {question['id']}")

        # Get all other answers from current theme only
        other_answers = [ q['correct_answer'] for q in theme_data['questions'] 
                        if q['id'] != question['id']]
        
        if len(other_answers) < num_options - 1:
            num_options = len(other_answers) + 1  # Reduce options if not enough questions
            logger.warning(f"Not enough questions in theme '{self.current_theme}'. Reducing options to {num_options}")
        
        wrong_answers = random.sample(other_answers, num_options - 1)
        options = wrong_answers + [correct_answer]
        random.shuffle(options)

        correct_option = options.index(correct_answer) + 1  # 1-based indexing

        logger.debug(f"Generated options for question {question['id']}: {options}")
        return {
            'question_id': question['id'],
            'text': question['text'],
            'audio_file': audio_file,
            'options': options,
            'correct_option': correct_option,
            'theme': self.current_theme,
            'theme_name': theme_data['name']
        }

    def get_themes(self) -> List[Dict[str, str]]:
        """Returns a list of dictionaries containing theme information"""
        return [
            {'tag': tag, 'name': data['name']} 
            for tag, data in self.themes.items()
        ]

    def get_theme_info(self) -> Dict[str, Dict[str, Any]]:
        """Returns detailed information about each theme"""
        return {
            tag: { 'name': data['name'], 'question_count': len(data['questions']) }
            for tag, data in self.themes.items()
        }

def get_session(user) -> UserSession:
    with sessions_lock:
        if user is None:
            logger.error("User object is None")
            raise ValueError("User object cannot be None")
        
        if user.id not in sessions:
            logger.info(f"Creating/loading session for user {get_user_info(user)}")
            session = UserSession(user)
            
            # Restore theme from user profile if exists
            saved_theme = session.get_theme()
            if saved_theme:
                session.question_selector.set_theme(saved_theme)
                logger.info(f"Restored theme {saved_theme} for user {get_user_info(user)}")
            
            sessions[user.id] = session
        return sessions[user.id]

def signal_handler(_signum, _frame, bot):
    """Handle shutdown signals"""
    logger.info("Received shutdown signal")
    logger.info("Stopping bot...")
    bot.stop_polling()
    sys.exit(0)

signal.signal(signal.SIGINT, lambda signum, frame: signal_handler(signum, frame, bot))
signal.signal(signal.SIGTERM, lambda signum, frame: signal_handler(signum, frame, bot))

def get_question_from_themes(themes, search_key, search_value):
    iterator = (q for theme in themes.values() 
                for q in theme['questions'] 
                if q[search_key] == search_value
        )
    return next(iterator, None)

def generate_and_send_question(session, chat_id, user_info):
    """Helper function to generate and send a question to user"""
    try:
        # Check if theme is selected
        current_theme = session.question_selector.current_theme
        if not current_theme:
            # Create keyboard with theme buttons
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            themes = session.question_selector.get_themes()
            for theme in themes:
                button = types.InlineKeyboardButton(
                    text=theme['name'],
                    callback_data=f"theme:{theme['tag']}"
                )
                keyboard.add(button)
            bot.send_message(chat_id, "Тематика не выбрана. Выберите своего бойца:", reply_markup=keyboard)
            return False
            
        question = session.question_selector.get_random_question()
        logger.info(f"Generated question {question['question_id']} for user {user_info}")

        # Try to send audio first to check permissions
        if question.get('audio_file'):
            audio_path = os.path.join('audio', question['audio_file'])
            if not os.path.exists(audio_path):
                logger.error(f"Audio file not found: {audio_path}")
                bot.send_message(chat_id, "Не удалось найти аудиофайл")
                return False
            try:
                with open(audio_path, 'rb') as audio:
                    bot.send_voice(chat_id, audio)
                logger.info(f"Sent audio file for user {user_info}: {audio_path}")
            except telebot.apihelper.ApiTelegramException as e:
                if "VOICE_MESSAGES_FORBIDDEN" in str(e) or "user restricted receiving of video messages" in str(e):
                    error_message = (
                        "❌ Вам запрещено присылать голосовые сообщения.\n"
                        "Для того, чтобы мы могли отправить примеры аускультаций, необходимо разрешить отправку вам голосовых сообщений:\n"
                        "1. Откройте настройки телеграма\n"
                        "2. Перейдите в раздел «Конфиденциальность»\n"
                        "3. Выберите пункт «Голосовые сообщения»\n"
                        "4. Добавьте бота в список исключений или разрешите отправку всем пользователям\n"
                    )
                    logger.error(f"Voice messages forbidden for user {user_info}")
                    keyboard = types.InlineKeyboardMarkup()
                    next_button = types.InlineKeyboardButton(text="Я разрешил ✅", callback_data="next")
                    keyboard.add(next_button)
                    bot.send_message(chat_id, error_message, reply_markup=keyboard)
                    return False
                else:
                    logger.error(f"Failed to send audio file {audio_path} for user {user_info}: {e}")
                    bot.send_message(chat_id, "Не удалось отправить аудиофайл")
                    return False

        bot.send_message(chat_id, f"{question['text']}\n\n")

        # Send options with keyboard
        options = question['options']
        options_text = "\n".join([f"{get_number_emoji(i)} {option}" for i, option in enumerate(options, 1)])
        options_message = (
            f"Варианты ответов:\n{options_text}\n\n"
            f"Выберите номер правильного варианта."
        )

        keyboard = types.InlineKeyboardMarkup(row_width=len(options))
        buttons = [
            types.InlineKeyboardButton( text=get_number_emoji(idx),  callback_data=f"answer:{question['question_id']}:{idx}" )
            for idx in range(1, len(options)+1)
        ]
        keyboard.add(*buttons)

        bot.send_message(chat_id, options_message, reply_markup=keyboard)

        logger.info(f"Sent question with buttons to user {user_info}")
        session.set_last_question(question)
        return True
    except ValueError as e:
        error_message = f"Ошибка при генерации вопроса: {e}"
        logger.error(f"Error generating question for user {user_info}: {e}")
        bot.send_message(chat_id, error_message)
        return False
    
@bot.message_handler(commands=['start'])
def handle_start(message):
    user = message.from_user
    user_info = get_user_info(user)
    logger.info(f"Received /start command from user {user_info}")
    try:
        session = get_session(user)
        session.reset_session()
        
        # Create theme selection keyboard
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for theme_tag, theme_data in session.question_selector.themes.items():
            button = types.InlineKeyboardButton( text=theme_data['name'], callback_data=f"theme:{theme_tag}" )
            keyboard.add(button)
        
        welcome_text = (
            "Добро пожаловать!\n\n"
            "Выберите тематику вопросов:"
        )
        bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard)
        logger.info(f"Session reset and theme selection sent for user {user_info}")
        
    except Exception as e:
        logger.error(f"Failed to reset session for user {user_info}: {e}", exc_info=True)
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    user = message.from_user
    user_info = get_user_info(user)
    logger.info(f"Received message from user {user_info}: {message.text}")

    try:
        session = get_session(user)
        last_question = session.get_last_question()
        if last_question and 'correct_option' in last_question:
            logger.info(f"Awaiting answer via buttons from user {user_info}")
            bot.send_message(message.chat.id, "Используйте кнопки для ответа на вопрос.")
        else:
            generate_and_send_question(session, message.chat.id, user_info)
    except Exception as e:
        logger.error(f"Unexpected error handling message from user {user_info}: {e}", exc_info=True)
        bot.send_message(message.chat.id, f"Произошла непредвиденная ошибка: {e}")


        
def get_global_stats(theme: str = None):
    """Get global statistics across all users for specific theme or all themes"""
    user_stats = []
    
    # Scan all user session files
    for session_file in glob.glob(os.path.join(UserSession.SESSIONS_DIR, "*.json")):
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                user_data = json.load(f)
                
            theme_stats = user_data.get('theme_stats', {})
            user_name = user_data.get('user_name', 'Неизвестный')
            user_id = Path(session_file).stem.replace('user_', '')
            
            # If theme specified, get stats only for that theme
            if theme:
                stats = theme_stats.get(theme, {'total': 0, 'correct': 0})
                if stats['total'] > 0:
                    user_stats.append({ 'user_id': user_id, 'user_name': user_name, 'total': stats['total'], 'correct': stats['correct'] })
            # Otherwise, sum up stats for all themes
            else:
                total = 0
                correct = 0
                for t_stats in theme_stats.values():
                    total += t_stats.get('total', 0)
                    correct += t_stats.get('correct', 0)
                if total > 0:
                    percentage = (correct / total) * 100 if total > 0 else 0
                    user_stats.append({ 'user_id': user_id, 'user_name': user_name, 'total': total, 'correct': correct, 'percentage': percentage })
                    
        except Exception as e:
            logger.error(f"Failed to load stats from {session_file}: {e}")
            continue
    
    # Sort users by correct answers (desc) and then by total answers (desc)
    return sorted( user_stats, key=lambda x: (x['correct'], x['total']), reverse=True )

@bot.callback_query_handler(func=lambda call: call.data == "global_stats")
def handle_global_stats_callback(call):
    user = call.from_user
    user_info = get_user_info(user)
    logger.info(f"Received global stats callback from user {user_info}")
    
    try:
        session = get_session(user)
        current_theme = session.get_theme()
        stats = get_global_stats(current_theme)
        
        if not stats:
            bot.send_message(call.message.chat.id, "Нет статистики тематики")
            bot.answer_callback_query(call.id)
            return
            
        theme_name = session.question_selector.themes[current_theme]['name']
        response = f"🏆 Рейтинг по тематике {theme_name}\n\n"
        
        # Add stats for each user
        for i, stat in enumerate(stats, 1):
            position_mark = get_position_emoji(i)
            if str(user.id) == stat['user_id']:
                user_mark = f"@{stat['user_name']} 👤"
            else:
                user_mark = f"@{stat['user_name']}"
            percentage = (stat['correct']/stat['total']*100)
            response += (
                f"{i}. {position_mark}{user_mark}: {stat['correct']}/{stat['total']} "
                f"({percentage:.1f}%)\n"
            )
        
        # Create keyboard with return button
        keyboard = types.InlineKeyboardMarkup()
        next_button = types.InlineKeyboardButton(text="Вернуться к вопросам ➡️", callback_data="next")
        keyboard.add(next_button)
        
        bot.send_message( call.message.chat.id, response, reply_markup=keyboard)
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"Error showing global stats for user {user_info}: {e}")
        bot.answer_callback_query(call.id, "Ошибка при показе глобальной статистики")

@bot.callback_query_handler(func=lambda call: call.data == "stats")
def handle_stats_callback(call):
    user = call.from_user
    user_info = get_user_info(user)
    logger.info(f"Received stats callback from user {user_info}")
    try:
        session = get_session(user)
        stats = session.get_statistics()
        current_theme = session.get_theme()
        
        global_stats = get_global_stats(current_theme)
        user_position = next( (i for i, stat in enumerate(global_stats, 1)  if str(user.id) == stat['user_id']), None )
        
        # Find stats for current theme
        theme_stats = None
        for theme in stats['themes']:
            if theme['theme_tag'] == current_theme:
                theme_stats = theme
                break
        
        if theme_stats:
            position_mark = get_position_emoji(user_position) if user_position else ""
            percentage_str = f"{theme_stats['percentage']:.1f}%"
            response = (
                f"📊 *Статистика по тематике {theme_stats['theme_name']}*\n"
                f"🏆 Место в рейтинге: {user_position} из {len(global_stats)} {position_mark}\n\n"
                f"Всего ответов {theme_stats['total']}, из них правильных: {theme_stats['correct']} ({percentage_str})\n\n"
                f"Вопросы:\n"
            )
            
            for q_stat in theme_stats['questions']:
                response += (
                    f"{q_stat['total']}/{q_stat['correct']}"
                    f"({q_stat['percentage']:.1f}%): *{q_stat['question']}*\n"
                )
        else:
            response = "По текущей тематике пока нет статистики"
        
        # Create keyboard with return and global stats buttons
        keyboard = types.InlineKeyboardMarkup()
        next_button = types.InlineKeyboardButton(text="Вопрос ➡️", callback_data="next")
        global_stats_button = types.InlineKeyboardButton(text="Общий рейтинг 🏆", callback_data="global_stats")
        keyboard.add(next_button, global_stats_button)
        
        bot.send_message(call.message.chat.id, response, reply_markup=keyboard, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Error showing statistics for user {user_info}: {e}")
        bot.answer_callback_query(call.id, "Ошибка при показе статистики")


@bot.callback_query_handler(func=lambda call: call.data == "change_theme")
def handle_change_theme_callback(call):
    user = call.from_user
    user_info = get_user_info(user)
    session = get_session(user)
    logger.info(f"Received change theme callback from user {user_info}")
    
    # Create keyboard with theme options
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    themes = session.question_selector.get_themes()
    
    # Add button for each theme
    theme_buttons = [
        types.InlineKeyboardButton( text=theme['name'],  callback_data=f"theme:{theme['tag']}" )
        for theme in themes
    ]
    keyboard.add(*theme_buttons)
    
    bot.send_message( call.message.chat.id, "Выберите тематику вопросов:", reply_markup=keyboard )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "next")
def handle_next_callback(call):
    user = call.from_user
    user_info = get_user_info(user)
    session = get_session(user)
    if generate_and_send_question(session, call.message.chat.id, user_info):
        bot.answer_callback_query(call.id)
    return

@bot.callback_query_handler(func=lambda call: call.data.startswith("theme:"))
def handle_theme_callback(call):
    try:
        user = call.from_user
        user_info = get_user_info(user)
        session = get_session(user)
        
        theme_parts = call.data.split(":")
        if len(theme_parts) != 2:
            logger.error(f"Invalid theme callback data format: {call.data}")
            bot.answer_callback_query(call.id, "Неверный формат данных")
            return
            
        theme = theme_parts[1]

        if session.question_selector.set_theme(theme):
            session.set_theme(theme)  # Save theme to user profile
            session.data.update({ 'user_id': user.id, 'user_name': user.username or user.first_name, 'last_update': time.time() })
            session.save_session()
            
            theme_info = session.question_selector.get_current_theme()
            response = f"Выбрана {theme_info['name'].lower()}."
        else:
            response = f"Ошибка при выборе тематики: {theme} не найдена"

        bot.send_message(call.message.chat.id, response)
        # Generate new question after theme change
        generate_and_send_question(session, call.message.chat.id, user_info)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Error in theme callback: {e}")
        bot.answer_callback_query(call.id, "Произошла ошибка при выборе темы")


@bot.callback_query_handler(func=lambda call: call.data.startswith("answer:"))
def handle_answer_callback(call):
    user = call.from_user
    user_info = get_user_info(user)
    logger.info(f"Received answer callback from user {user_info}: {call.data}")

    try:
        session = get_session(user)
        
        # Parse question_id and selected_option
        try:
            _, question_id, selected_option = call.data.split(':')
            question_id = int(question_id)
            selected_option = int(selected_option)
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid callback data format: {call.data}, error: {e}")
            bot.answer_callback_query(call.id, f"Неверный формат данных: {e}")
            return

        last_question = session.get_last_question()
        if not last_question or last_question.get('question_id') != question_id:
            logger.warning(f"No matching question found for callback from user {user_info}")
            bot.answer_callback_query(call.id, "Вы уже ответили на этот вопрос")
            return

        # Determine if answer is correct and get answers text
        correct_option = last_question.get('correct_option')
        is_correct = selected_option == correct_option
        options = last_question.get('options', [])
        selected_answer = options[selected_option - 1]
        correct_answer = options[correct_option - 1]

        # Get question data for explanations
        themes = session.question_selector.themes
        question_data = get_question_from_themes( themes,  'id',  question_id )
        selected_answer_data = get_question_from_themes( themes, 'correct_answer', selected_answer )

        # Mark selected button with ✅ or ❌ and show correct answer
        options = last_question.get('options', [])
        keyboard = types.InlineKeyboardMarkup(row_width=len(options))
        buttons = []
        
        # Prepare new options text with marks
        options_text = []
        for idx in range(1, len(options)+1):
            option = options[idx - 1]
            if idx == selected_option:
                mark = "✅" if is_correct else "❌"
                button_text = mark
                options_text.append(f"{mark} {option}")
            elif idx == correct_option and not is_correct:
                mark = "✅"
                button_text = mark
                options_text.append(f"{mark} {option}")
            else:
                button_text = get_number_emoji(idx)
                options_text.append(f"{get_number_emoji(idx)} {option}")
            
            buttons.append(
                types.InlineKeyboardButton(
                    text=button_text, 
                    callback_data=f"answer:{question_id}:{idx}"
                )
            )
        keyboard.add(*buttons)
        
        # Update message text with new marks
        new_text = (
            f"Варианты ответов:\n"
            f"{chr(10).join(options_text)}\n\n"
            f"Выберите номер правильного варианта."
        )
        
        try:
            # Update both keyboard and text
            bot.edit_message_text(
                text=new_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Failed to update message for user {user_info}: {e}")

        # Create keyboard with multiple buttons
        keyboard = types.InlineKeyboardMarkup(row_width=3)
        next_button = types.InlineKeyboardButton(text="Дальше ➡️", callback_data="next")
        stats_button = types.InlineKeyboardButton(text="📊", callback_data="stats")
        theme_button = types.InlineKeyboardButton(text="Тема 🔄", callback_data="change_theme")
        keyboard.add(next_button, stats_button, theme_button)

        # Prepare and send responses based on correctness
        if is_correct:
            response = f"✅ Правильно, *{selected_answer.lower()}*! ✅\n\n"
            logger.info(f"User {user_info} answered correctly: {selected_answer}")
            
            if question_data and 'explanation' in question_data:
                response += "\n".join(question_data['explanation']) + "\n\n"

            bot.send_message(call.message.chat.id, response, reply_markup=keyboard, parse_mode="Markdown")
        else:
            # Send initial wrong answer message
            response = (
                f"❌ *{selected_answer}* — неправильный ответ ❌\n\n"
                f"Правильный ответ — *{correct_answer.lower()}*.\n\n"
            )
            
            # Send correct answer explanation with audio hint
            if question_data and 'explanation' in question_data:
                response += (
                    f"{chr(10).join(question_data['explanation'])}\n\n"
                    f"А вот как звучит *{selected_answer.lower()}*:"
                )
                
            bot.send_message(call.message.chat.id, response, parse_mode="Markdown")

            # Send audio of the wrong answer
            if selected_answer_data and selected_answer_data.get('audio_paths'):
                audio_file_name = selected_answer_data['audio_paths'][0]
                audio_path = os.path.join('audio', audio_file_name)
                if os.path.exists(audio_path):
                    try:
                        with open(audio_path, 'rb') as audio:
                            bot.send_voice(call.message.chat.id, audio)
                    except Exception as e:
                        logger.error(f"Failed to send audio file {audio_path} for user {user_info}: {e}")
                        bot.send_message(call.message.chat.id, "Не удалось отправить аудиофайл.")
                else:
                    logger.error(f"Audio file does not exist: {audio_path}")
                    bot.send_message(call.message.chat.id, "Аудиофайл не найден.")

            # Send wrong answer explanation with buttons
            if selected_answer_data and 'explanation' in selected_answer_data:
                wrong_explanation = chr(10).join(selected_answer_data['explanation'])
                bot.send_message(call.message.chat.id, wrong_explanation, reply_markup=keyboard)

        bot.answer_callback_query(call.id)

        # Update statistics and clear last question
        session.update_question_stats( question_id=question_id,  is_correct=is_correct, theme=last_question['theme'] )
        session.clear_last_question()

    except Exception as e:
        logger.error(f"Error handling answer callback from user {user_info}: {e}", exc_info=True)
        bot.send_message(call.message.chat.id, f"Произошла чудовищная ошибка: {e}")
        bot.answer_callback_query(call.id, "Произошла чудовищная ошибка.")

class CodeChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_modified = time.time()
        
    def on_modified(self, event):
        # Check if the modified file is either the bot code or a question file
        is_bot_code = event.src_path.endswith('bot.py')
        is_question_file = event.src_path.endswith('.json') and 'questions' in event.src_path
        
        if is_bot_code or is_question_file:
            current_time = time.time()
            if current_time - self.last_modified > 1:  # Prevent multiple reloads
                self.last_modified = current_time
                logger.info(f"Change detected in {event.src_path}. Restarting bot...")
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception as e:
                    logger.error(f"Failed to restart bot: {e}")

if __name__ == '__main__':
    logger.info("Starting bot...")

    
    # Set up file watcher
    event_handler = CodeChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path='.', recursive=False)
    observer.schedule(event_handler, path='questions', recursive=False)
    observer.start()
    
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
    finally:
        observer.stop()
        observer.join()
        logger.info("Bot stopped") 