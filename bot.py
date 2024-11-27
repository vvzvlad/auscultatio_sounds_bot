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

import requests.exceptions
from urllib3.exceptions import NewConnectionError

import telebot
from telebot import types
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from json.decoder import JSONDecodeError

# Constants
QUESTIONS_DIR = Path('questions')
SESSIONS_DIR = Path('data/user_sessions')

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


sessions = {} # Global dictionary to store sessions
sessions_lock = threading.RLock() # Global lock for sessions
file_id_cache = {} # tg file_id cache

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
    def __init__(self, user):
        self.user_info = get_user_info(user)
        self.user_id = user.id
        self.user_name = user.username or user.first_name
        self.lock = threading.RLock()
        self.sessions_dir = SESSIONS_DIR

        # Create all necessary directories
        self.sessions_dir.parent.mkdir(exist_ok=True)  # Create 'data' directory
        self.sessions_dir.mkdir(exist_ok=True)  # Create 'user_sessions' directory

        # Define session file path
        self.session_file = self.sessions_dir / f"user_{self.user_id}.json"

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

    def smart_get_question(self, num_options: int = 4) -> Dict[str, Any]:
        """Return the question with the fewest correct answers for the user."""
        with self.lock:
            
            # Get current theme
            current_theme = self.get_theme()
            if not current_theme:
                raise ValueError("Theme not selected.")

            theme_data = self.question_selector.themes.get(current_theme)
            if not theme_data:
                raise ValueError(f"Theme '{current_theme}' not found.")

            questions = theme_data['questions']
            if not questions:
                raise ValueError(f"No questions available in theme '{current_theme}'.")

            # Get user stats for this theme
            theme_stats = self.data.get('theme_stats', {}).get(current_theme, {})
            question_stats = theme_stats.get('question_stats', {})

            # Group questions by number of correct answers
            questions_by_correct = {}
            for q in questions:
                correct = question_stats.get(str(q['id']), {}).get('correct', 0)
                if correct not in questions_by_correct:
                    questions_by_correct[correct] = []
                questions_by_correct[correct].append(q)

            # Get questions with minimum correct answers
            min_correct = min(questions_by_correct.keys()) if questions_by_correct else 0
            candidate_questions = questions_by_correct.get(min_correct, questions)
            
            # Randomly select from the questions with minimum correct answers
            question = random.choice(candidate_questions)
            correct_answer = question['correct_answer']

            # Handle 'wrong_answers' if present in the question
            if 'wrong_answers' in question:
                wrong_answers = question['wrong_answers']
                if len(wrong_answers) >= num_options - 1:
                    # Use provided wrong_answers, selecting only (num_options-1) if more are provided
                    selected_wrong = random.sample(wrong_answers, num_options - 1)
                else:
                    # Use provided wrong_answers and supplement with random ones
                    other_answers = [q['correct_answer'] for q in questions if q['id'] != question['id']]
                    needed = (num_options - 1) - len(wrong_answers)
                    random_wrong = random.sample(other_answers, needed) if needed > 0 else []
                    selected_wrong = wrong_answers + random_wrong
                options = selected_wrong + [correct_answer]
            else:
                other_answers = [q['correct_answer'] for q in questions if q['id'] != question['id']]
                wrong_answers = random.sample(other_answers, min(len(other_answers), num_options - 1))
                options = wrong_answers + [correct_answer]
            
            random.shuffle(options)
            correct_option = options.index(correct_answer) + 1  # 1-based indexing

            # Handle files if present
            file = None
            if question.get('files'):
                file = random.choice(question['files'])
                logger.info(f"Selected file {file} for question {question['id']}")

            return {
                'question_id': question['id'],
                'text': question['text'],
                'file': file,
                'options': options,
                'correct_option': correct_option,
                'theme': current_theme,
                'theme_name': theme_data['name']
            }

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
            
        # Validate files if present
        if 'files' in question:
            if not isinstance(question['files'], list):
                raise ValueError("files must be an array")
            for path in question['files']:
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
                    
                    # Check questions files before adding theme
                    logger.info(f"Checking theme '{theme_tag}' for missing files")
                    missing_files = self._check_questions_files(theme_data.get('questions', []))
                    if missing_files:
                        files_str = "\n".join(f"- {f}" for f in missing_files)
                        logger.error(f"Missing questions files for theme '{theme_tag}':\n{files_str}")
                        raise ValueError(f"Missing questions files for theme '{theme_tag}':\n{files_str}")

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

    def _check_questions_files(self, questions) -> list:
        """Check if all required questions files exist"""
        missing_files = []
        for question in questions:
            if 'files' in question:
                for file_path in question['files']:
                    # Convert string path to Path object and check relative to directory
                    full_path = Path('questions') / file_path
                    logger.debug(f"Checking file {full_path}")
                    if not full_path.exists():
                        missing_files.append(str(full_path))
        return missing_files

    def set_theme(self, theme_tag: str) -> bool:
        if theme_tag in self.themes:
            self.current_theme = theme_tag
            return True
        logger.warning(f"Theme '{theme_tag}' not found")
        return False

    def get_current_theme(self) -> Dict[str, str]:
        if self.current_theme:
            return { 'tag': self.current_theme, 'name': self.themes[self.current_theme]['name'] }
        return None

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
            logger.info(f"Theme not selected for user {user_info}, sending theme selection")
            # Create keyboard with theme buttons
            keyboard = types.InlineKeyboardMarkup(row_width=1)
            themes = session.question_selector.get_themes()
            for theme in themes:
                button = types.InlineKeyboardButton(
                    text=theme['name'],
                    callback_data=f"theme:{theme['tag']}"
                )
                keyboard.add(button)
            bot.send_message(chat_id, "Тема не выбрана. Выберите своего бойца:", reply_markup=keyboard)
            return False
            
        # Use the new method to get the least correct question
        
        question = session.smart_get_question()
        
        logger.info(f"Generated question {question['question_id']} for user {user_info}")

        bot.send_message(chat_id, f"{question['text']}\n\n")

        if question.get('file'):
            file_path = os.path.join('questions', question['file'])
            if not send_file(bot, chat_id, file_path):
                return False
        
        # Send options with keyboard
        options = question['options']
        options_text = "\n".join([f"{get_number_emoji(i)} {option}" for i, option in enumerate(options, 1)])
        options_message = ( f"{options_text}\n\n" )

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
            "Выберите тему:"
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
        bot.send_message(message.chat.id, f"Произошла непредвиденная шибка: {e}")


        
def get_global_stats(theme: str = None):
    """Get global statistics across all users for specific theme or all themes"""
    user_stats = []
    
    # Scan all user session files
    for session_file in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                user_data = json.load(f)
                
            theme_stats = user_data.get('theme_stats', {})
            user_name = user_data.get('user_name', 'Незвестный')
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
            bot.send_message(call.message.chat.id, "Нет статистики темы")
            bot.answer_callback_query(call.id)
            return
            
        theme_name = session.question_selector.themes[current_theme]['name']
        response = f"🏆 Рейтинг по теме {theme_name}\n\n"
        
        # Add stats for each user
        for i, stat in enumerate(stats, 1):
            position_mark = get_position_emoji(i)
            if str(user.id) == stat['user_id']:
                user_mark = f"@{stat['user_name']} (это вы)"
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
        bot.answer_callback_query(call.id, "Ошибка при показе рейтинга")

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
                f"📊 *Статистика по теме {theme_stats['theme_name']}*\n"
                f"🏆 Место в рейтинге: {user_position} из {len(global_stats)} {position_mark}\n\n"
                f"Всего ответов {theme_stats['total']}, из них правильных: {theme_stats['correct']} ({percentage_str})\n\n"
                f"Вопросы:\n"
            )
            
            response_lines = []
            for q_stat in theme_stats['questions']:
                response_lines.append(f"{q_stat['total']}/{q_stat['correct']} ({q_stat['percentage']:.1f}%): *{q_stat['question']}*\n")
                
            # Join lines and truncate if over 4000 chars
            response_text = "".join(response_lines)
            max_length = 3000
            if len(response_text) > max_length:
                response_text = response_text[:max_length] + "..."
            response += response_text
        else:
            response = "По теме нет статистики"
        
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
    logger.info(f"Received change theme callback from user {get_user_info(call.from_user)}")
    user = call.from_user
    session = get_session(user)
    
    # Create keyboard with theme options
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    themes = session.question_selector.get_themes()
    
    # Add button for each theme
    theme_buttons = [
        types.InlineKeyboardButton( text=theme['name'],  callback_data=f"theme:{theme['tag']}" )
        for theme in themes
    ]
    keyboard.add(*theme_buttons)
    
    bot.send_message( call.message.chat.id, "Выберите тему вопросов:", reply_markup=keyboard )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "next")
def handle_next_callback(call):
    logger.info(f"Received next callback from user {get_user_info(call.from_user)}")
    user = call.from_user
    user_info = get_user_info(user)
    session = get_session(user)
    if generate_and_send_question(session, call.message.chat.id, user_info):
        bot.answer_callback_query(call.id)
    return

@bot.callback_query_handler(func=lambda call: call.data.startswith("theme:"))
def handle_theme_callback(call):
    logger.info(f"Received theme callback from user {get_user_info(call.from_user)}: {call.data}")
    try:
        user = call.from_user
        user_info = get_user_info(user)
        session = get_session(user)
        
        theme_parts = call.data.split(":")
        if len(theme_parts) != 2:
            logger.error(f"Invalid theme callback data format: {call.data}")
            bot.answer_callback_query(call.id, "Неверный формат данны")
            return
            
        theme = theme_parts[1]

        if session.question_selector.set_theme(theme):
            session.set_theme(theme)  # Save theme to user profile
            session.data.update({ 'user_id': user.id, 'user_name': user.username or user.first_name, 'last_update': time.time() })
            session.save_session()
            
            theme_info = session.question_selector.get_current_theme()
            response = f"Выбрана тема {theme_info['name'].lower()}"
        else:
            response = f"Не найдена тема {theme}"

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
        current_theme = session.question_selector.current_theme
        
        # Get data for current question from current theme
        question_data = next(
            (q for q in themes[current_theme]['questions'] 
            if q['id'] == question_id),
            None
        )
        
        # Find explanation for selected answer ONLY in current theme
        selected_answer_data = next(
            (q for q in themes[current_theme]['questions'] 
            if q['correct_answer'] == selected_answer and q['id'] != question_id),
            None
        )

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
            response = ""
        else:
            # Send initial wrong answer message
            response = (
                f"❌ *{selected_answer}* — неправильный ответ ❌\n\n" #Неправильный ответ
                f"Правильный — *{correct_answer.lower()}*.\n\n" #Правильный ответ
            )
            if question_data and 'explanation' in question_data:   #Обьяснение правильного ответа
                response += f"{chr(10).join(question_data['explanation'])}\n\n"

            if 'files' not in selected_answer_data:
                bot.send_message(call.message.chat.id, response, parse_mode="Markdown", reply_markup=keyboard)
                response = ""
            
            if selected_answer_data and 'files' in selected_answer_data:
                wrong_answer_file_path = os.path.join('questions', selected_answer_data['files'][0])
                if "mp3" in wrong_answer_file_path or "ogg" in wrong_answer_file_path:
                    response += f"\nА вот как звучит *{selected_answer.lower()}*:" #А вот как звучит неправильный ответ
                    bot.send_message(call.message.chat.id, response, parse_mode="Markdown")
                    response = ""
                    send_file(bot, call.message.chat.id, wrong_answer_file_path) #Неправильный ответ аудио

                if "jpg" in wrong_answer_file_path or "png" in wrong_answer_file_path:
                    wrong_question = selected_answer_data['text'].lower()
                    wrong_answer = selected_answer.lower()
                    response += f"\nА вот как выглядит *{wrong_answer}* ({wrong_question}):" #А вот как выглядит неправильный ответ
                    bot.send_message(call.message.chat.id, response, parse_mode="Markdown")
                    response = ""
                    send_file(bot, call.message.chat.id, wrong_answer_file_path) #Неправильный ответ картинка
                
            if selected_answer_data and 'explanation' in selected_answer_data:  #Обьяснение неправильного ответа, если есть
                response = ""
                response += f"\n{chr(10).join(selected_answer_data['explanation'])}" 
                bot.send_message(call.message.chat.id, response, parse_mode="Markdown", reply_markup=keyboard)

        bot.answer_callback_query(call.id)

        # Update statistics and clear last question
        session.update_question_stats( question_id=question_id,  is_correct=is_correct, theme=last_question['theme'] )
        session.clear_last_question()

    except Exception as e:
        logger.error(f"Error handling answer callback from user {user_info}: {e}", exc_info=True)
        bot.send_message(call.message.chat.id, f"Произошла чудовищная ошибка: {e}")
        bot.answer_callback_query(call.id, "Произошла чудовищная ошибка!")

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

def validate_json_files(directory: Path, description: str = "JSON files"):
    """Validate all JSON files in specified directory"""
    logger.info(f"Validating {description} in {directory}")
    
    if not directory.exists():
        logger.info(f"Directory {directory} does not exist yet")
        return
        
    json_files = list(directory.glob('*.json'))
    if not json_files:
        logger.info(f"No JSON files found in {directory}")
        return
        
    for file_path in json_files:
        try:
            logger.info(f"Validating {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                json.load(f)
        except JSONDecodeError as e:
            error_msg = f"Invalid JSON in {file_path}: {str(e)}"
            logger.error(error_msg)
            raise
            
    logger.info(f"All {description} are valid")

def send_file(bot, chat_id, file_path):
    """Helper function to send files with caching file_ids
    Returns: True if successful, False otherwise"""
    try:
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            bot.send_message(chat_id, f"Не найден файл {file_path}")
            return False

        # Determine file type and send accordingly
        if file_path.endswith(('.mp3', '.ogg', '.wav')):
            try:
                if file_path in file_id_cache:
                    logger.info(f"Sending cached audio file: {file_path}")
                    message = bot.send_voice(chat_id, file_id_cache[file_path])
                else:
                    with open(file_path, 'rb') as file:
                        message = bot.send_voice(chat_id, file)
                        file_id_cache[file_path] = message.voice.file_id
                        logger.info(f"Sending audio file: {file_path}, stored in cache: {file_id_cache[file_path]}")
                logger.info(f"Sent audio file: {file_path}")
                return True
            except telebot.apihelper.ApiTelegramException as e:
                if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                    error_message = (
                        "❌ Вам запрещено присылать голосовые сообщения.\n"
                        "Для того, чтобы мы могли отправить аудио, необходимо разрешить отправку вам голосовых сообщений:\n"
                        "1. Откройте настройки телеграма\n"
                        "2. Перейдите в раздел «Конфиденциальность»\n"
                        "3. Выберите пункт «Голосовые сообщения»\n"
                        "4. Добавьте бота в список исключений или разрешите отправку всем пользователям\n"
                    )
                    logger.error(f"Voice messages forbidden for {chat_id}")
                    keyboard = types.InlineKeyboardMarkup()
                    next_button = types.InlineKeyboardButton(text="Я разрешил ✅", callback_data="next")
                    keyboard.add(next_button)
                    bot.send_message(chat_id, error_message, reply_markup=keyboard)
                    return False
                raise

        elif file_path.endswith(('.jpg', '.png', '.jpeg', '.webp', '.gif', '.bmp')):
            if file_path in file_id_cache:
                logger.info(f"Sending cached image file: {file_path}")
                message = bot.send_photo(chat_id, file_id_cache[file_path])
            else:
                with open(file_path, 'rb') as file:
                    message = bot.send_photo(chat_id, file)
                    file_id_cache[file_path] = message.photo[0].file_id
                    logger.info(f"Sending image file: {file_path}, stored in cache: {file_id_cache[file_path]}")
            logger.info(f"Sent image file: {file_path}")
            return True

        else:
            logger.error(f"Unsupported file type: {file_path}")
            return False

    except Exception as e:
        logger.error(f"Failed to send file {file_path}: {e}")
        bot.send_message(chat_id, f"Не удалось отправить файл {file_path}: {e}")
        return False

if __name__ == '__main__':
    logger.info("\n\n\nStarting bot...")
    
    try:
        validate_json_files(QUESTIONS_DIR, "question files")
        validate_json_files(SESSIONS_DIR, "user session files")
    except Exception as e:
        logger.error(f"Failed to validate JSON files: {e}")
        sys.exit(1)
        
    # Set up file watcher
    event_handler = CodeChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path='.', recursive=False)
    observer.schedule(event_handler, path='questions', recursive=False)
    observer.start()
    
    while True:
        try:
            logger.info("Starting bot polling...")
            bot.infinity_polling()
        except (requests.exceptions.ConnectionError, 
                requests.exceptions.ReadTimeout,
                NewConnectionError) as e:
            logger.error(f"Network error occurred: {e}")
            logger.info("Waiting 2 seconds before retry...")
            time.sleep(2)
            continue
        except Exception as e:
            # Log any other unexpected errors
            logger.error(f"Bot crashed with unexpected error: {e}", exc_info=True)
            break
        finally:
            observer.stop()
            observer.join()
            logger.info("Bot stopped") 