import json
import random
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class QuestionManager:
    def __init__(self):
        logger.info("Initializing QuestionManager")
        self.questions = self._load_questions()
        self.stats = self._load_statistics()
        self.all_answers = [q['correct_answer'] for q in self.questions]
        self.current_options = {}
    
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
        stats_file = Path('statistics.json')
        try:
            if stats_file.exists():
                logger.info("Loading existing statistics")
                with open(stats_file, 'r') as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Failed to load statistics.json. Creating new statistics")
        
        logger.info("Creating new statistics file")
        return {}  # Empty dict for storing user-specific statistics
    
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
            with open('statistics.json', 'w', encoding='utf-8') as f:
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
        logger.info(
            f"Updated stats for user {user_id}, question {question_id}: "
            f"from {old_stats} to {stats}"
        )
        self._save_statistics()
    
    def get_answer_message(self, question_id, selected_answer):
        """Generate message about user's answer"""
        question = next(q for q in self.questions if q['id'] == question_id)
        correct_answer = question['correct_answer']
        
        # Если selected_answer is None, значит произошла ошибка или перезапуск бота
        if selected_answer is None:
            return {
                'first_text': "Извините, состояние вопроса было потеряно. Пожалуйста, начните новый вопрос.",
                'second_text': None,
                'audio_paths': []
            }
        
        is_correct = selected_answer == correct_answer
        
        if is_correct:
            return {
                'first_text': (f"Вы выбрали ✅ \"{selected_answer}\" ✅, и это правильно! 😋\n\n"
                              f"{question['explanation']['detailed_text']}"),
                'second_text': None,
                'audio_paths': []
            }
        else:
            # Найдем вопрос, который содержит выбранный ответ как правильный
            wrong_question = next(
                (q for q in self.questions if q['correct_answer'] == selected_answer),
                None
            )
            
            first_text = (f"Вы выбрали ❌ \"{selected_answer}\" ❌, но это неправильный ответ 😕.\n"
                         f"Правильный ответ: \"{correct_answer}\"\n\n"
                         f"{question['explanation']['detailed_text']}\n\n"
                         f"А вот как звучит то, что вы выбрали (\"{selected_answer}\"):")
            
            second_text = None
            wrong_audio_paths = []
            if wrong_question:
                second_text = wrong_question['explanation']['detailed_text']
                wrong_audio_paths = wrong_question.get('audio_paths', [])
            
            return {
                'first_text': first_text,
                'second_text': second_text,
                'audio_paths': wrong_audio_paths
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
                    f"*{question['correct_answer']}*:"
                    f" {q_stats['correct']}/{q_stats['total']} ({percentage:.1f}%)"
                )
        
        # Calculate overall percentage
        overall_percentage = (
            (total_correct / total_questions * 100) if total_questions > 0 else 0
        )
        
        # Construct message
        message = [
            "*Ваша статистика:*\n",
            f"Всего ответов: {total_questions}",
            f"Правильных ответов: {total_correct} ({overall_percentage:.1f}%)\n",
            "*Статистика по вопросам:*"
        ]
        
        message.extend(details)
        
        return "\n".join(message)
    
    def reset_user_statistics(self, user_id):
        """Reset statistics for specific user"""
        user_id = str(user_id)
        logger.info(f"Resetting statistics for user {user_id}")
        
        # Create new empty statistics for all questions
        self.stats[user_id] = {
            str(q['id']): {'correct': 0, 'total': 0} 
            for q in self.questions
        }
        
        # Save updated statistics
        self._save_statistics()
        logger.info(f"Statistics reset completed for user {user_id}")
    