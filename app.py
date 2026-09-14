from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session as flask_session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, WordCheck, Book, WallWord, VisitorLog, DailyStats, PermanentStats, SentenceBank, UserSentenceRecord
from datetime import datetime, timedelta
from functools import wraps
import json
import os
import requests
import random
import re
import hashlib
import itertools
from dotenv import load_dotenv

# ONLY load .env locally (Vercel uses environment variables directly)
if not os.environ.get('VERCEL'):
    load_dotenv()

# Import AI APIs
from groq import Groq
import cohere
from openai import OpenAI 

# Initialize AI clients
groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))
cohere_client = cohere.Client(os.getenv('COHERE_API_KEY'))
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Track daily usage
daily_usage = {'groq': 0, 'cohere': 0, 'date': datetime.now().date()}

def reset_daily_usage():
    """Reset counters at midnight"""
    today = datetime.now().date()
    if daily_usage.get('date') != today:
        daily_usage['groq'] = 0
        daily_usage['cohere'] = 0
        daily_usage['date'] = today

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError(
        'SECRET_KEY environment variable is required. '
        'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
    )

import re

def sanitize_text_for_ai(text):
    """Remove PII from text before sending to AI providers."""
    if not text:
        return text

    # Email
    text = re.sub(r'\b[\w\.-]+@[\w\.-]+\.\w+\b', '[EMAIL]', text)

    # Phone (US-style and international-ish)
    text = re.sub(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE]', text)

    # SSN
    text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', text)

    # ZIP+4 first, then plain 5-digit ZIP
    text = re.sub(r'\b\d{5}-\d{4}\b', '[ZIP]', text)
    text = re.sub(r'\b\d{5}\b', '[ZIP]', text)

    # Street address (number + word + street-type)
    text = re.sub(
        r'\b\d+\s+[A-Za-z0-9\.\-]+(?:\s+[A-Za-z0-9\.\-]+)*\s+'
        r'(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|Place)\b\.?',
        '[ADDRESS]', text, flags=re.IGNORECASE
    )

    # @handles
    text = re.sub(r'(?<!\w)@[A-Za-z0-9_]{3,}', '[HANDLE]', text)

    # Student IDs like student-12345, ID: 12345
    text = re.sub(r'\b(?:student|pupil|id)[-_\s:]?\d{3,}\b', '[STUDENT_ID]', text, flags=re.IGNORECASE)

    return text


@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://quge5.com https://3nbf4.com https://nap5k.com https://pl29249339.profitablecpmratenetwork.com https://www.highperformanceformat.com https://www.clarity.ms;"
    return response

# 👇 USE NEON POSTGRESQL ON VERCEL, SQLITE LOCALLY 👇
DATABASE_URL = os.environ.get('POSTGRES_URL') or os.environ.get('DATABASE_URL')
if DATABASE_URL:
    # Use PostgreSQL on Vercel (Neon)
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Use SQLite locally
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///vault.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'pool_recycle': 300,
    'pool_pre_ping': True,
}

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ===== SENTENCE BANK FUNCTIONS =====
def get_random_sentence_for_user(user_id, difficulty=None):
    """Get a sentence the user hasn't seen yet"""
    
    # Get IDs of sentences the user has already seen
    seen_ids = db.session.query(UserSentenceRecord.sentence_id).filter_by(user_id=user_id).all()
    seen_ids = [s[0] for s in seen_ids]
    
    # Build query for unseen sentences
    query = SentenceBank.query.filter(~SentenceBank.id.in_(seen_ids)) if seen_ids else SentenceBank.query
    
    if difficulty:
        query = query.filter_by(difficulty=difficulty)
    
    # Get random sentence
    sentence = query.order_by(db.func.random()).first()
    
    if not sentence:
        # User has seen ALL sentences!
        return None
    
    # Record that user saw this sentence
    record = UserSentenceRecord(user_id=user_id, sentence_id=sentence.id)
    db.session.add(record)
    
    # Update usage count
    sentence.times_used += 1
    db.session.commit()
    
    return sentence

# ===== PERMANENT STATS FUNCTIONS =====
def update_permanent_stats(word_check_score, time_spent):
    """Update permanent stats - ONLY INCREASES, never decreases"""
    try:
        stats = PermanentStats.query.first()
        if not stats:
            stats = PermanentStats()
            db.session.add(stats)
            db.session.flush()  # Get the ID
        
        # Make sure values are integers
        if stats.total_word_checks is None:
            stats.total_word_checks = 0
        if stats.total_time_spent is None:
            stats.total_time_spent = 0
        
        stats.total_word_checks += 1
        stats.total_time_spent += time_spent
        
        # Track highest score ever
        if word_check_score > (stats.highest_score_ever or 0):
            stats.highest_score_ever = word_check_score
        
        # Track lowest score ever (above 0)
        if word_check_score < (stats.lowest_score_ever or 100) and word_check_score > 0:
            stats.lowest_score_ever = word_check_score
        
        db.session.commit()
        print(f"✅ Permanent stats updated: {stats.total_word_checks} total checks")
    except Exception as e:
        print(f"⚠️ Stats update error: {e}")
        db.session.rollback()

@app.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    """Permanently delete user account and all data"""
    try:
        user_id = current_user.id
        user_email = current_user.email
        
        # Delete all related data
        WordCheck.query.filter_by(user_id=user_id).delete()
        UserSentenceRecord.query.filter_by(user_id=user_id).delete()
        VisitorLog.query.filter_by(user_id=user_id).delete()
        
        # Delete the user
        User.query.filter_by(id=user_id).delete()
        
        db.session.commit()
        logout_user()
        flash('Your account and all data have been permanently deleted.', 'info')
        return redirect(url_for('index'))
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Delete account error: {e}")
        flash('Error deleting account. Please contact support.', 'error')
        return redirect(url_for('dashboard'))

def update_permanent_user_count():
    """Update permanent user count - ONLY INCREASES"""
    try:
        stats = PermanentStats.query.first()
        if not stats:
            stats = PermanentStats()
            db.session.add(stats)
        
        stats.total_users += 1
        db.session.commit()
        print(f"✅ Permanent user count updated: {stats.total_users} total users")
    except Exception as e:
        print(f"⚠️ User count update error: {e}")
        db.session.rollback()

# ===== AUTO CLEANUP FUNCTION =====
def auto_cleanup_database():
    """Keep database small forever - runs daily, NEVER deletes PermanentStats or SentenceBank"""
    try:
        today = datetime.now().date()
        
        # Keep only 14 days of visitor logs (stats are aggregated in DailyStats anyway)
        cutoff_visitors = today - timedelta(days=14)
        deleted_visitors = VisitorLog.query.filter(
            db.func.date(VisitorLog.created_at) < cutoff_visitors
        ).delete()
        
        # Keep only 90 days of word checks (enough for analytics)
        cutoff_checks = today - timedelta(days=90)
        deleted_checks = WordCheck.query.filter(
            db.func.date(WordCheck.created_at) < cutoff_checks
        ).delete()
        
        # ❌ NEVER DELETE PermanentStats! ❌
        # ❌ NEVER DELETE SentenceBank! ❌
        
        db.session.commit()
        print(f"✅ Auto-cleanup: Deleted {deleted_visitors} visitor logs, {deleted_checks} word checks")
    except Exception as e:
        print(f"⚠️ Auto-cleanup error: {e}")
        db.session.rollback()

# ===== VISITOR TRACKING MIDDLEWARE =====
def get_client_ip():
    """Get real client IP"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def get_location_from_ip(ip):
    """Get location from IP (free API)"""
    if ip in ['127.0.0.1', 'localhost', '::1']:
        return 'Localhost', 'Localhost'
    try:
        response = requests.get(f'http://ip-api.com/json/{ip}?fields=status,country,city', timeout=3)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                return data.get('country', 'Unknown'), data.get('city', 'Unknown')
    except:
        pass
    return 'Unknown', 'Unknown'

def generate_session_id():
    if current_user.is_authenticated:
        return f"user_{current_user.id}_{datetime.now().date()}"
    else:
        if 'session_id' not in flask_session:
            flask_session['session_id'] = hashlib.md5(
                f"{request.remote_addr}{request.user_agent.string}{datetime.now()}".encode()
            ).hexdigest()
        return flask_session['session_id']

@app.before_request
def track_visitor():
    if request.path.startswith('/static'):
        return
    if request.path.startswith('/api'):
        return
    if 'favicon' in request.path:
        return

    try:
        session_id = generate_session_id()
        today = datetime.now().date()

        existing = VisitorLog.query.filter(
            VisitorLog.session_id == session_id,
            db.func.date(VisitorLog.created_at) == today
        ).first()

        visitor = VisitorLog(
            page_visited=request.path,
            is_unique=(existing is None),
            session_id=session_id,
            user_id=current_user.id if current_user.is_authenticated else None
        )

        db.session.add(visitor)
        db.session.commit()

        update_today_stats()

    except Exception as e:
        print(f"❌ Tracking error: {e}")
        db.session.rollback()

def update_today_stats():
    """Update today's stats in real-time"""
    today = datetime.now().date()
    
    stats = DailyStats.query.filter_by(date=today).first()
    if not stats:
        stats = DailyStats(date=today)
        db.session.add(stats)
    
    unique_visitors = db.session.query(
        db.func.count(db.distinct(VisitorLog.session_id))
    ).filter(
        db.func.date(VisitorLog.created_at) == today
    ).scalar() or 0
    
    active_users = db.session.query(
        db.func.count(db.distinct(WordCheck.user_id))
    ).filter(
        db.func.date(WordCheck.created_at) == today
    ).scalar() or 0
    
    total_visitors = VisitorLog.query.filter(
        db.func.date(VisitorLog.created_at) == today
    ).count()
    
    new_users = User.query.filter(
        db.func.date(User.created_at) == today
    ).count()
    
    total_checks = WordCheck.query.filter(
        db.func.date(WordCheck.created_at) == today
    ).count()
    
    avg_score = db.session.query(db.func.avg(WordCheck.score)).filter(
        db.func.date(WordCheck.created_at) == today
    ).scalar() or 0
    
    stats.unique_visitors = unique_visitors
    stats.active_users = active_users
    stats.total_visitors = total_visitors
    stats.new_users = new_users
    stats.total_checks = total_checks
    stats.avg_score = round(float(avg_score), 1) if avg_score else 0
    
    db.session.commit()

# ===== ENHANCED DICTIONARY API INTEGRATION =====
class DictionaryAPI:
    """Enhanced dictionary service with multiple fallbacks"""
    
    BASE_URL = "https://api.dictionaryapi.dev/api/v2/entries/en"
    
    @classmethod
    def get_word_info(cls, word):
        """Fetch comprehensive word information"""
        word = re.sub(r'[^\w\s-]', '', word.lower().strip())
        
        try:
            response = requests.get(f"{cls.BASE_URL}/{word}", timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                return cls._parse_response(data)
            else:
                return cls._get_fallback_definition(word)
                
        except Exception as e:
            print(f"Dictionary API error: {e}")
            return cls._get_fallback_definition(word)
    
    @classmethod
    def _parse_response(cls, data):
        """Parse the API response into structured data"""
        if not data or len(data) == 0:
            return None
            
        result = {
            'word': data[0].get('word', ''),
            'phonetic': data[0].get('phonetic', ''),
            'meanings': [],
            'synonyms': [],
            'antonyms': [],
            'examples': []
        }
        
        phonetics = data[0].get('phonetics', [])
        for p in phonetics:
            if p.get('audio'):
                result['audio'] = p.get('audio')
                break
        
        for meaning in data[0].get('meanings', []):
            part_of_speech = meaning.get('partOfSpeech', '')
            
            result['synonyms'].extend(meaning.get('synonyms', []))
            result['antonyms'].extend(meaning.get('antonyms', []))
            
            for definition in meaning.get('definitions', []):
                meaning_data = {
                    'partOfSpeech': part_of_speech,
                    'definition': definition.get('definition', ''),
                    'example': definition.get('example', ''),
                }
                result['meanings'].append(meaning_data)
                
                if definition.get('example'):
                    result['examples'].append(definition.get('example'))
        
        result['synonyms'] = list(set(result['synonyms']))[:8]
        result['antonyms'] = list(set(result['antonyms']))[:5]
        result['examples'] = list(set(result['examples']))[:3]
        
        return result
    
    @classmethod
    def _get_fallback_definition(cls, word):
        """Fallback definitions for common words"""
        common_words = {
            'grandiloquent': {
                'word': 'grandiloquent',
                'phonetic': '/ɡranˈdiləkwənt/',
                'meanings': [{
                    'partOfSpeech': 'adjective',
                    'definition': 'Pompous or extravagant in language, style, or manner, especially in a way intended to impress.',
                    'example': 'The politician\'s grandiloquent speech failed to convince the skeptical audience.'
                }],
                'synonyms': ['pompous', 'bombastic', 'pretentious', 'high-flown', 'rhetorical'],
                'antonyms': ['simple', 'modest', 'plain'],
                'examples': ['His grandiloquent mannerisms made him seem insincere.']
            },
            'ephemeral': {
                'word': 'ephemeral',
                'phonetic': '/əˈfem(ə)rəl/',
                'meanings': [{
                    'partOfSpeech': 'adjective',
                    'definition': 'Lasting for a very short time.',
                    'example': 'The beauty of cherry blossoms is ephemeral, lasting only a few weeks each spring.'
                }],
                'synonyms': ['transitory', 'transient', 'fleeting', 'brief', 'short-lived'],
                'antonyms': ['permanent', 'eternal', 'everlasting'],
                'examples': ['Fame is often ephemeral in the digital age.']
            },
            'serendipity': {
                'word': 'serendipity',
                'phonetic': '/ˌserənˈdipədē/',
                'meanings': [{
                    'partOfSpeech': 'noun',
                    'definition': 'The occurrence and development of events by chance in a happy or beneficial way.',
                    'example': 'Finding this tool was pure serendipity.'
                }],
                'synonyms': ['chance', 'fortune', 'luck', 'providence', 'fluke'],
                'antonyms': ['misfortune', 'design', 'plan'],
                'examples': ['Their meeting was a beautiful moment of serendipity.']
            },
            'ubiquitous': {
                'word': 'ubiquitous',
                'phonetic': '/yo͞oˈbikwədəs/',
                'meanings': [{
                    'partOfSpeech': 'adjective',
                    'definition': 'Present, appearing, or found everywhere.',
                    'example': 'Smartphones have become ubiquitous in modern society.'
                }],
                'synonyms': ['omnipresent', 'universal', 'everywhere', 'pervasive', 'prevalent'],
                'antonyms': ['rare', 'scarce', 'uncommon'],
                'examples': ['Coffee shops are ubiquitous in this city.']
            },
            'pulchritudinous': {
                'word': 'pulchritudinous',
                'phonetic': '/ˌpəlkrəˈt(y)o͞od(ə)nəs/',
                'meanings': [{
                    'partOfSpeech': 'adjective',
                    'definition': 'Having great physical beauty and appeal.',
                    'example': 'She was a pulchritudinous woman with striking features.'
                }],
                'synonyms': ['beautiful', 'gorgeous', 'stunning', 'lovely', 'attractive'],
                'antonyms': ['ugly', 'unattractive', 'hideous'],
                'examples': ['The pulchritudinous sunset took everyone\'s breath away.']
            },
            'bank': {
                'word': 'bank',
                'phonetic': '/baNGk/',
                'meanings': [
                    {
                        'partOfSpeech': 'noun',
                        'definition': 'The land alongside or sloping down to a river or lake.',
                        'example': 'She sat on the bank of the river, watching the water flow.'
                    },
                    {
                        'partOfSpeech': 'noun',
                        'definition': 'A financial institution that accepts deposits and makes loans.',
                        'example': 'I need to go to the bank to deposit this check.'
                    }
                ],
                'synonyms': ['shore', 'edge', 'rim', 'institution', 'treasury'],
                'antonyms': ['center', 'middle'],
                'examples': ['Willows lined the river bank.', 'The bank approved my loan.']
            },
            'easy': {
                'word': 'easy',
                'phonetic': '/ˈiːzi/',
                'meanings': [
                    {
                        'partOfSpeech': 'adjective',
                        'definition': 'Achieved without great effort; presenting few difficulties.',
                        'example': 'The test was very easy.'
                    },
                    {
                        'partOfSpeech': 'adjective',
                        'definition': 'Free from worry, anxiety, trouble, or pain.',
                        'example': 'He lived an easy life in the countryside.'
                    }
                ],
                'synonyms': ['simple', 'effortless', 'straightforward', 'unchallenging', 'comfortable'],
                'antonyms': ['difficult', 'hard', 'challenging'],
                'examples': ['The exam was surprisingly easy.', 'She has an easy confidence.']
            },
            'obfuscate': {
                'word': 'obfuscate',
                'phonetic': '/ˈɒbfəskeɪt/',
                'meanings': [{
                    'partOfSpeech': 'verb',
                    'definition': 'To deliberately or unintentionally make something unclear or difficult to understand.',
                    'example': 'The lecturer\'s explanation seemed to obfuscate the topic rather than clarify it.'
                }],
                'synonyms': ['confuse', 'complicate', 'muddle', 'cloud', 'bewilder'],
                'antonyms': ['clarify', 'explain', 'simplify'],
                'examples': ['Politicians often obfuscate the truth.']
            }
        }
        
        word_lower = word.lower()
        if word_lower in common_words:
            return common_words[word_lower]
        
        return {
            'word': word,
            'phonetic': '',
            'meanings': [{
                'partOfSpeech': 'unknown',
                'definition': f"'{word}' - This word may be rare, misspelled, or not in our dictionary.",
                'example': ''
            }],
            'synonyms': [],
            'antonyms': [],
            'examples': []
        }

def analyze_with_openai(sentence, word, user_guess):
    """Use OpenAI GPT-4o-mini (FERPA-compliant, cheap, fast)"""
    reset_daily_usage()
    
    if not os.getenv('OPENAI_API_KEY'):
        print("❌ OPENAI_API_KEY not found!")
        return None
    
    prompt = f"""You are an expert vocabulary tutor and etymologist. Analyze this word comprehensively.

Sentence: "{sentence}"
Target Word: "{word}"
Student's Guess: "{user_guess}"

Return ONLY valid JSON with these fields:
{{
    "detected_pos": "adjective/noun/verb/adverb",
    "contextual_meaning": "definition that fits this context",
    "overallScore": 0-100,
    "wordSimilarityScore": 0-100,
    "contextualScore": 0-100,
    "depthScore": 0-100,
    "whatTheyGotRight": "specific praise",
    "whatTheyMissed": "detailed feedback",
    "betterDefinition": "accurate dictionary definition",
    "contextExplanation": "why this meaning applies",
    "root": "root word and meaning",
    "prefix": "prefix and meaning or None",
    "suffix": "suffix and meaning or None",
    "origin": "language origin",
    "word_family": ["related", "words"],
    "breakdown": "how the word is built",
    "synonyms": ["synonym1", "synonym2", "synonym3"],
    "antonyms": ["antonym1", "antonym2"],
    "example_sentences": ["example1", "example2"],
    "difficulty": "easy/medium/hard",
    "common_usage": "common/uncommon/rare",
    "quick_definition": "one sentence definition",
    "wrong_answers": ["wrong1", "wrong2", "wrong3"]
}}"""

    try:
        print(f"🔄 Calling OpenAI API (gpt-4o-mini)...")
        print(f"📝 Word: {word}")
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500
        )
        
        text = response.choices[0].message.content
        
        print(f"📥 OpenAI response: {text[:200]}...")
        
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group())
            result['api_used'] = 'OpenAI GPT-4o-mini'
            print(f"✅ OpenAI used")
            print(f"📊 Score: {result.get('overallScore', 'N/A')}")
            return result
        else:
            print(f"❌ No JSON in OpenAI response")
            return None
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing error: {e}")
        return None
            
    except Exception as e:
        print(f"❌ OpenAI error: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def analyze_with_groq(sentence, word, user_guess):
    """Use Groq API with enhanced vocabulary analysis"""
    reset_daily_usage()
    
    if not os.getenv('GROQ_API_KEY'):
        print("❌ GROQ_API_KEY not found in environment!")
        return None
    
    if daily_usage['groq'] >= 43200:
        print("⚠️ Groq daily limit reached")
        return None
    
    prompt = f"""You are an expert vocabulary tutor and etymologist. Analyze this word comprehensively.

Sentence: "{sentence}"
Target Word: "{word}"
Student's Guess: "{user_guess}"

IMPORTANT: Return ONLY valid JSON. Do NOT include any other text.

SCORING (use the full 0-100 range):
- overallScore: 0-100 (90+ = perfect, 70-89 = good, 50-69 = partial, 30-49 = mostly wrong, 0-29 = completely wrong)
- wordSimilarityScore: 0-100 (how well they know the definition)
- contextualScore: 0-100 (how well they understand it in THIS context)
- depthScore: 0-100 (how detailed their explanation is)

FEEDBACK:
- whatTheyGotRight: specific praise about what they understood correctly
- whatTheyMissed: detailed explanation of what they missed contextually and why

DEFINITION:
- detected_pos: "adjective/noun/verb/adverb" (as used in THIS sentence)
- contextual_meaning: precise definition that fits THIS specific sentence
- contextExplanation: why this meaning applies here (mention specific context clues)
- betterDefinition: accurate dictionary definition for this context

ETYMOLOGY & WORD STRUCTURE:
- root: the root word and its meaning (e.g., "bio = life")
- prefix: any prefix and its meaning (e.g., "un- = not") - if none, use "None"
- suffix: any suffix and its meaning (e.g., "-able = able to") - if none, use "None"
- origin: language origin (e.g., "Greek", "Latin", "Old English")
- word_family: list of 3-5 related words (same root/prefix/suffix)
- breakdown: simple breakdown showing how the word is built

EXAMPLES:
- synonyms: [3-5 synonyms relevant to this context]
- antonyms: [2-3 antonyms relevant to this context]
- example_sentences: [2-3 example sentences showing different uses]

DIFFICULTY:
- difficulty: "easy/medium/hard" (for vocabulary level)
- common_usage: "common/uncommon/rare" (how often it's used)

QUIZ READY:
- quick_definition: a one-sentence definition for quiz purposes
- wrong_answers: [3 plausible but incorrect meanings] (for quiz generation)

Return ONLY valid JSON with this exact structure. Make sure to CLOSE all brackets properly:
{{
    "detected_pos": "",
    "contextual_meaning": "",
    "overallScore": 0,
    "wordSimilarityScore": 0,
    "contextualScore": 0,
    "depthScore": 0,
    "whatTheyGotRight": "",
    "whatTheyMissed": "",
    "betterDefinition": "",
    "contextExplanation": "",
    "root": "",
    "prefix": "",
    "suffix": "",
    "origin": "",
    "word_family": [],
    "breakdown": "",
    "synonyms": [],
    "antonyms": [],
    "example_sentences": [],
    "difficulty": "",
    "common_usage": "",
    "quick_definition": "",
    "wrong_answers": []
}}"""

    try:
        print(f"🔄 Calling Groq API with model: openai/gpt-oss-120b")
        print(f"📝 Word: {word}")
        print(f"📝 Sentence: {sentence[:50]}...")
        
        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500  # ✅ INCREASED from 1000 to 1500
        )
        
        daily_usage['groq'] += 1
        text = response.choices[0].message.content
        
        print(f"📥 Raw response length: {len(text)} characters")
        print(f"📥 Response preview: {text[:200]}...")
        
        # Extract JSON
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            json_str = json_match.group()
            result = json.loads(json_str)
            result['api_used'] = 'Groq AI (GPT-OSS-120B)'
            print(f"✅ Groq used ({daily_usage['groq']}/43200 today)")
            
            print(f"📊 Score: {result.get('overallScore', 'N/A')}")
            print(f"📚 Root: {result.get('root', 'N/A')}")
            
            return result
        else:
            print(f"❌ No JSON found in response")
            print(f"Full response: {text[:500]}")
            return None
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing error: {e}")
        print(f"Raw text: {text[:500]}")
        return None
            
    except Exception as e:
        print(f"❌ Groq error: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    
def analyze_with_cohere(sentence, word, user_guess):
    """Use Cohere Chat API (backup)"""
    reset_daily_usage()
    
    prompt = f"""You are an expert vocabulary tutor. Analyze this word comprehensively.

Sentence: "{sentence}"
Target Word: "{word}"
Student's Guess: "{user_guess}"

Return ONLY valid JSON with:
- detected_pos
- contextual_meaning
- overallScore
- wordSimilarityScore
- contextualScore
- depthScore
- whatTheyGotRight
- whatTheyMissed
- betterDefinition
- contextExplanation
- root
- prefix
- suffix
- origin
- word_family
- breakdown
- synonyms
- antonyms
- example_sentences
- difficulty
- common_usage
- quick_definition
- wrong_answers"""

    try:
        print(f"🔄 Calling Cohere Chat API...")
        
        # ✅ FIXED: Use the working model
        response = cohere_client.chat(
            model="command-r-08-2024",  # ✅ This works!
            message=prompt,
            temperature=0.3,
            max_tokens=1500
        )
        
        daily_usage['cohere'] += 1
        text = response.text
        
        print(f"📥 Cohere response: {text[:200]}...")
        
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group())
            result['api_used'] = 'Cohere AI'
            print(f"✅ Cohere used")
            return result
        else:
            print(f"❌ No JSON in Cohere response")
            return None
            
    except Exception as e:
        print(f"❌ Cohere error: {str(e)}")
        return None

# ===== RULE-BASED SCORING (FALLBACK) =====
class ScoringEngine:
    """Advanced scoring that understands context-specific meanings"""
    
    @classmethod
    def analyze(cls, sentence, word, user_guess):
        word_info = DictionaryAPI.get_word_info(word)
        
        detected_pos = cls._detect_pos_from_context(sentence, word)
        contextual_meaning = cls._find_meaning_by_pos(word_info, detected_pos, sentence, word)
        scores = cls._calculate_contextual_scores(sentence, word, user_guess, contextual_meaning, word_info)
        feedback = cls._generate_contextual_feedback(scores, word, sentence, contextual_meaning, word_info, detected_pos)
        formatted_definition = cls._format_contextual_definition(word_info, contextual_meaning, sentence, detected_pos)
        
        return {
            'overallScore': scores['overall'],
            'wordSimilarityScore': scores['word_sim'],
            'contextualScore': scores['context'],
            'depthScore': scores['depth'],
            'whatTheyGotRight': feedback['right'],
            'whatTheyMissed': feedback['missed'],
            'betterDefinition': formatted_definition,
            'detected_pos': detected_pos,
            'api_used': 'Rule-Based (Fallback)',
            'wordInfo': {
                'phonetic': word_info.get('phonetic', ''),
                'synonyms': cls._get_contextual_synonyms(contextual_meaning, word_info),
                'antonyms': word_info.get('antonyms', [])[:3],
                'examples': word_info.get('examples', [])[:2],
                'otherMeanings': cls._get_other_meanings(word_info, contextual_meaning)
            }
        }
    
    @classmethod
    def _detect_pos_from_context(cls, sentence, word):
        sentence_lower = sentence.lower()
        word_lower = word.lower()
        
        words = sentence_lower.split()
        word_index = -1
        for i, w in enumerate(words):
            if word_lower in w:
                word_index = i
                break
        
        scores = {'adjective': 0, 'noun': 0, 'verb': 0, 'adverb': 0}
        
        if ' very ' in sentence_lower or ' too ' in sentence_lower or ' so ' in sentence_lower:
            scores['adjective'] += 25
        if word_index > 0 and words[word_index - 1] in ['is', 'was', 'are', 'were', 'seems', 'feels', 'looks', 'becomes']:
            scores['adjective'] += 25
        
        if word_lower == 'easy':
            if 'very' in sentence_lower or 'too' in sentence_lower:
                scores['adjective'] += 40
            if ' was ' in sentence_lower or ' is ' in sentence_lower:
                scores['adjective'] += 30
        
        if word_index > 0 and words[word_index - 1] in ['the', 'a', 'an', 'my', 'your', 'his', 'her', 'this', 'that']:
            scores['noun'] += 30
        
        if word_index > 0 and words[word_index - 1] in ['to', 'will', 'can', 'could', 'should', 'must']:
            scores['verb'] += 25
        if word.endswith('ed') or word.endswith('ing'):
            scores['verb'] += 20
        
        if word.endswith('ly'):
            scores['adverb'] += 30
        
        best_pos = max(scores, key=scores.get)
        return best_pos if scores[best_pos] > 0 else 'adjective'
    
    @classmethod
    def _find_meaning_by_pos(cls, word_info, target_pos, sentence, word):
        meanings = word_info.get('meanings', [])
        if not meanings:
            return {'partOfSpeech': target_pos, 'definition': f'Definition for {word}'}
        
        exact_matches = [m for m in meanings if m.get('partOfSpeech') == target_pos]
        if exact_matches:
            meaning = exact_matches[0].copy()
        else:
            meaning = meanings[0].copy()
        
        meaning['context_explanation'] = cls._generate_context_explanation(sentence, word, meaning, target_pos)
        return meaning
    
    @classmethod
    def _extract_context_keywords(cls, sentence):
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
                      'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were'}
        words = sentence.lower().split()
        keywords = []
        for word in words:
            clean_word = word.strip('.,!?;:"\'()[]{}')
            if clean_word not in stop_words and len(clean_word) > 2:
                keywords.append(clean_word)
        return keywords[:10]
    
    @classmethod
    def _generate_context_explanation(cls, sentence, word, meaning, detected_pos):
        if not meaning:
            return ""
        pos_names = {'adjective': 'an adjective (describes a quality)', 'noun': 'a noun', 
                     'verb': 'a verb', 'adverb': 'an adverb'}
        pos_text = pos_names.get(detected_pos, f'a {detected_pos}')
        definition = meaning.get('definition', '')
        return f"In this sentence, '{word}' functions as {pos_text} meaning: {definition}"
    
    @classmethod
    def _calculate_contextual_scores(cls, sentence, word, user_guess, contextual_meaning, word_info):
        guess_lower = user_guess.lower()
        
        word_sim = 50
        if contextual_meaning and contextual_meaning.get('definition'):
            def_text = contextual_meaning['definition'].lower()
            def_words = set(def_text.split())
            guess_words = set(guess_lower.split())
            overlap = len(def_words & guess_words)
            word_sim += min(overlap * 6, 30)
        word_sim = min(word_sim, 100)
        
        context = 50
        context_keywords = cls._extract_context_keywords(sentence)
        context_match = sum(1 for kw in context_keywords if kw in guess_lower)
        context += min(context_match * 8, 30)
        
        pos = contextual_meaning.get('partOfSpeech', '')
        if cls._detect_pos_understanding(guess_lower, pos):
            context += 15
        context = min(context, 100)
        
        depth = 50
        guess_length = len(guess_lower.split())
        if guess_length > 15: depth += 35
        elif guess_length > 10: depth += 25
        elif guess_length > 6: depth += 15
        elif guess_length > 3: depth += 8
        depth = min(depth, 100)
        
        overall = int(word_sim * 0.30 + context * 0.45 + depth * 0.25)
        return {'overall': overall, 'word_sim': word_sim, 'context': context, 'depth': depth}
    
    @classmethod
    def _detect_pos_understanding(cls, guess, part_of_speech):
        pos_indicators = {
            'noun': ['person', 'place', 'thing', 'idea', 'concept'],
            'verb': ['action', 'doing', 'perform', 'act'],
            'adjective': ['describes', 'quality', 'characteristic'],
            'adverb': ['how', 'manner', 'degree']
        }
        indicators = pos_indicators.get(part_of_speech, [])
        return any(ind in guess.lower() for ind in indicators)
    
    @classmethod
    def _generate_contextual_feedback(cls, scores, word, sentence, contextual_meaning, word_info, detected_pos):
        overall = scores['overall']
        context_score = scores['context']
        
        if context_score >= 80:
            right = f"🌟 Excellent! You understood how '{word}' functions as an {detected_pos} in this context. "
        elif context_score >= 65:
            right = f"👍 Good job! You've grasped the contextual meaning of '{word}' as an {detected_pos}. "
        elif context_score >= 50:
            right = f"📚 You're on the right track. '{word}' is used as an {detected_pos} here. "
        else:
            right = f"💪 Good attempt! Note that '{word}' functions as an {detected_pos} in this context. "
        
        if overall >= 85:
            missed = "You're very close to mastery!"
        elif overall >= 70:
            missed = f"Pay attention to how '{word}' is modified by surrounding words."
        elif overall >= 55:
            missed = f"Focus on the word's function as an {detected_pos}. The context clues point to this usage."
        else:
            missed = f"This word functions as an {detected_pos} here. Look at the words around '{word}'."
        
        other_meanings = cls._get_other_meanings(word_info, contextual_meaning)
        if other_meanings:
            missed += f"\n\n💡 Note: '{word}' has {len(other_meanings)} other meaning(s) as different parts of speech!"
        
        return {'right': right, 'missed': missed}
    
    @classmethod
    def _format_contextual_definition(cls, word_info, contextual_meaning, sentence, detected_pos):
        parts = []
        word_display = word_info['word'].capitalize()
        if word_info.get('phonetic'):
            parts.append(f"**{word_display}** {word_info['phonetic']}")
        else:
            parts.append(f"**{word_display}**")
        parts.append(f"\n🎯 **IN THIS CONTEXT (as an {detected_pos.upper()}):**")
        definition = contextual_meaning.get('definition', 'Definition not available')
        parts.append(f"\n{definition}")
        return '\n'.join(parts)
    
    @classmethod
    def _get_contextual_synonyms(cls, contextual_meaning, word_info):
        return word_info.get('synonyms', [])[:5]
    
    @classmethod
    def _get_other_meanings(cls, word_info, current_meaning):
        if not word_info.get('meanings'):
            return []
        other = []
        current_pos = current_meaning.get('partOfSpeech', '')
        for meaning in word_info['meanings']:
            if meaning.get('partOfSpeech') != current_pos:
                other.append({
                    'partOfSpeech': meaning.get('partOfSpeech', ''),
                    'definition': meaning.get('definition', '')[:100] + '...'
                })
        return other[:3]

def get_ai_analysis(sentence, word, user_guess):
    """Try AI APIs in order of preference"""
    
    # 1️⃣ PRIMARY: Groq (FREE, FAST, UNLIMITED)
    result = analyze_with_groq(sentence, word, user_guess)
    if result:
        print("✅ Got response from Groq!")
        return result
    
    # 2️⃣ SECOND: OpenAI (FERPA-compliant, cheap, fast)
    result = analyze_with_openai(sentence, word, user_guess)
    if result:
        print("✅ Got response from OpenAI!")
        return result
    
    # 3️⃣ THIRD: Cohere (slow but works)
    result = analyze_with_cohere(sentence, word, user_guess)
    if result:
        print("✅ Got response from Cohere!")
        return result
    
    # 4️⃣ LAST: Rule-based fallback
    print("📚 Using rule-based fallback")
    return ScoringEngine.analyze(sentence, word, user_guess)

# ===== ROUTES =====

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    total_words = WallWord.query.count()
    total_checks = WordCheck.query.count()
    top_words = WallWord.query.filter_by(wall_type='fame')\
        .order_by(WallWord.average_score.desc()).limit(3).all()
    
    return render_template('index.html', 
                         total_words=total_words, 
                         total_checks=total_checks,
                         top_words=top_words)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        name = request.form.get('name')
        user_type = request.form.get('user_type')
        
        if not email or not password:
            flash('Email and password are required', 'error')
            return redirect(url_for('signup'))
            
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return redirect(url_for('signup'))
        
        if len(password) < 8:
            flash('Password must be at least 8 characters', 'error')
            return redirect(url_for('signup'))
        
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered', 'error')
            return redirect(url_for('signup'))
        
        ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
        is_admin = (email == ADMIN_EMAIL)
        
        user = User(
            email=email,
            name=name,
            user_type=user_type,
            is_admin=is_admin
        )
        user.password = generate_password_hash(password)
        
        db.session.add(user)
        db.session.commit()
        
        # 👇 UPDATE PERMANENT USER COUNT 👇
        update_permanent_user_count()
        
        login_user(user)
        flash('Welcome to Reading Vault! 🎉', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user, remember=remember)
            flash(f'Welcome back, {user.name or user.email.split("@")[0]}!', 'success')
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        
        flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    today = datetime.now().date()
    if current_user.last_active:
        if current_user.last_active == today - timedelta(days=1):
            current_user.current_streak += 1
        elif current_user.last_active != today:
            current_user.current_streak = 1
    else:
        current_user.current_streak = 1
    
    current_user.last_active = today
    current_user.longest_streak = max(current_user.longest_streak, current_user.current_streak)
    db.session.commit()
    
    total_checks = current_user.words_checked
    avg_score = current_user.get_average_score()
    total_time_hours = round(current_user.total_time_spent / 3600, 1)
    
    recent_checks = WordCheck.query.filter_by(user_id=current_user.id)\
        .order_by(WordCheck.created_at.desc()).limit(10).all()
    
    top_words = WordCheck.query.filter_by(user_id=current_user.id)\
        .filter(WordCheck.score >= 80)\
        .order_by(WordCheck.score.desc()).limit(5).all()
    
    needs_work = WordCheck.query.filter_by(user_id=current_user.id)\
        .filter(WordCheck.score < 60)\
        .order_by(WordCheck.score).limit(5).all()
    
    progress_data = []
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        checks = WordCheck.query.filter_by(user_id=current_user.id)\
            .filter(db.func.date(WordCheck.created_at) == date).all()
        if checks:
            avg = sum(c.score for c in checks) / len(checks)
            progress_data.append({'date': date.strftime('%a'), 'avg_score': round(avg, 1), 'count': len(checks)})
        else:
            progress_data.append({'date': date.strftime('%a'), 'avg_score': 0, 'count': 0})
    
    return render_template('dashboard.html',
        user=current_user, total_checks=total_checks, avg_score=avg_score,
        total_time_hours=total_time_hours, recent_checks=recent_checks,
        top_words=top_words, needs_work=needs_work, progress_data=progress_data)

@app.route('/check')
@login_required
def check():
    return render_template('check.html', user=current_user)

@app.route('/api/get-sentence')
@login_required
def get_random_sentence():
    """Get a random sentence the user hasn't seen"""
    difficulty = request.args.get('difficulty')
    
    try:
        sentence = get_random_sentence_for_user(current_user.id, difficulty)
        
        if sentence:
            return jsonify({
                'success': True,
                'sentence': sentence.sentence,
                'word': sentence.word,
                'difficulty': sentence.difficulty,
                'id': sentence.id
            })
        else:
            return jsonify({
                'success': False,
                'message': 'You\'ve seen all our sentences! Check back soon for more.'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze():
    try:
        data = request.get_json()
        
        sentence = data.get('sentence', '')
        word = data.get('word', '')
        user_guess = data.get('userGuess', '')
        book_title = data.get('bookTitle', '')
        author = data.get('author', '')
        time_spent = data.get('timeSpent', 0)
        
        safe_sentence = sanitize_text_for_ai(sentence)
        safe_guess = sanitize_text_for_ai(user_guess)
        result = get_ai_analysis(safe_sentence, word, safe_guess)
        
        word_info = DictionaryAPI.get_word_info(word)
        if 'wordInfo' not in result:
            result['wordInfo'] = {
                'phonetic': word_info.get('phonetic', ''),
                'synonyms': result.get('synonyms', word_info.get('synonyms', []))[:5],
                'examples': word_info.get('examples', [])[:2],
                'otherMeanings': []
            }
        
        word_check = WordCheck(
            user_id=current_user.id,
            word=word,
            sentence=sentence,
            book_title=book_title,
            author=author,
            user_guess=user_guess,
            score=result['overallScore'],
            ai_feedback=json.dumps(result),
            time_spent=time_spent
        )
        
        db.session.add(word_check)
        current_user.words_checked += 1
        current_user.total_time_spent += time_spent
        
        # Track book (only if book info was provided)
        if book_title and author:
            book = Book.query.filter_by(title=book_title, author=author).first()
            if book:
                book.times_used += 1
            else:
                book = Book(title=book_title, author=author)
                db.session.add(book)
        
        # Add to wall (always — no book required, no content filter)
        wall_word = WallWord.query.filter_by(word=word).first()
        if wall_word:
            wall_word.total_checks += 1
            wall_word.average_score = (
                wall_word.average_score * (wall_word.total_checks - 1) + result['overallScore']
            ) / wall_word.total_checks
            wall_word.wall_type = 'fame' if wall_word.average_score >= 70 else 'shame'
        else:
            wall_word = WallWord(
                word=word,
                wall_type='fame' if result['overallScore'] >= 70 else 'shame',
                total_checks=1,
                average_score=result['overallScore'],
                sample_sentence=sentence,
                book_title=book_title,
                author=author
            )
            db.session.add(wall_word)
        
        db.session.commit()
        
        # 👇 UPDATE PERMANENT STATS 👇
        update_permanent_stats(result['overallScore'], time_spent)
        
        update_today_stats()

        return jsonify(result)
    
    except Exception as e:
        db.session.rollback()
        print(f"Error in analyze: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Analysis failed. Please try again.'}), 500

@app.route('/api/autocomplete/<field>')
def autocomplete(field):
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify([])
    
    if field == 'book':
        books = Book.query.filter(Book.title.ilike(f'%{query}%'))\
            .order_by(Book.times_used.desc()).limit(8).all()
        results = [{'title': b.title, 'author': b.author, 'count': b.times_used} for b in books]
    else:
        authors = db.session.query(Book.author, db.func.count(Book.id).label('count'))\
            .filter(Book.author.ilike(f'%{query}%'))\
            .group_by(Book.author)\
            .order_by(db.text('count DESC')).limit(8).all()
        results = [{'author': a[0], 'count': a[1]} for a in authors]
    
    return jsonify(results)


# ===== DAILY STATS AGGREGATION =====
def aggregate_daily_stats(date=None):
    """Calculate and save stats for a specific date"""
    if date is None:
        date = datetime.now().date()
    
    existing = DailyStats.query.filter_by(date=date).first()
    if existing:
        return existing
    
    day_start = datetime.combine(date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    
    total_visitors = VisitorLog.query.filter(
        VisitorLog.created_at >= day_start,
        VisitorLog.created_at < day_end
    ).count()
    
    unique_visitors = db.session.query(
        db.func.count(db.distinct(VisitorLog.session_id))
    ).filter(
        VisitorLog.created_at >= day_start,
        VisitorLog.created_at < day_end
    ).scalar() or 0
    
    new_users = User.query.filter(
        User.created_at >= day_start,
        User.created_at < day_end
    ).count()
    
    checks = WordCheck.query.filter(
        WordCheck.created_at >= day_start,
        WordCheck.created_at < day_end
    )
    total_checks = checks.count()
    
    avg_score = db.session.query(db.func.avg(WordCheck.score)).filter(
        WordCheck.created_at >= day_start,
        WordCheck.created_at < day_end
    ).scalar() or 0
    
    active_users = db.session.query(
        db.func.count(db.distinct(WordCheck.user_id))
    ).filter(
        WordCheck.created_at >= day_start,
        WordCheck.created_at < day_end
    ).scalar() or 0
    
    stats = DailyStats(
        date=date,
        total_visitors=total_visitors,
        unique_visitors=unique_visitors,
        new_users=new_users,
        total_checks=total_checks,
        avg_score=round(float(avg_score), 1),
        active_users=active_users
    )
    
    db.session.add(stats)
    db.session.commit()
    
    return stats

@app.route('/admin/aggregate-stats')
@login_required
def admin_aggregate_stats():
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    if current_user.email != ADMIN_EMAIL:
        return jsonify({'error': 'Unauthorized'}), 403
    
    today = datetime.now().date()
    start_date = today - timedelta(days=365)
    
    first_visitor = VisitorLog.query.order_by(VisitorLog.created_at).first()
    if first_visitor:
        start_date = max(start_date, first_visitor.created_at.date())
    
    current_date = start_date
    stats_created = 0
    
    while current_date <= today:
        existing = DailyStats.query.filter_by(date=current_date).first()
        if not existing:
            aggregate_daily_stats(current_date)
            stats_created += 1
        current_date += timedelta(days=1)
    
    flash(f'✅ Aggregated {stats_created} days of statistics!', 'success')
    return redirect(url_for('admin'))

def calculate_peak_visitors():
    """Calculate the maximum concurrent visitors (peak)"""
    today = datetime.now().date()
    
    unique_today = db.session.query(
        db.func.count(db.distinct(VisitorLog.session_id))
    ).filter(
        db.func.date(VisitorLog.created_at) == today
    ).scalar() or 0
    
    all_time_peak = db.session.query(
        db.func.max(DailyStats.unique_visitors)
    ).scalar() or 0
    
    peak = max(unique_today, all_time_peak)
    
    update_today_stats()
    
    today_stats = DailyStats.query.filter_by(date=today).first()
    if today_stats and today_stats.unique_visitors > peak:
        peak = today_stats.unique_visitors
    
    return peak if peak > 0 else 1

@app.route('/admin')
@login_required
def admin():
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    if current_user.email != ADMIN_EMAIL:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    # 👇 AUTO CLEANUP - Runs once per day when admin visits 👇
    last_cleanup = flask_session.get('last_cleanup')
    today_str = datetime.now().date().isoformat()
    
    if last_cleanup != today_str:
        auto_cleanup_database()
        flask_session['last_cleanup'] = today_str
    
    now = datetime.now()
    
    # 🔥 ULTRA SAFE DEFAULTS - Everything starts at 0/empty 🔥
    total_users = 0
    total_checks = 0
    total_visitors = 0
    today_visitors = 0
    today_active = 0
    peak_value = 0
    peak_day = None
    trend_percent = 0
    total_active_days = 0
    dates = []
    visitors = []
    active_users_list = []
    geo_stats = []
    source_stats = []
    word_stats = []
    top_users = []
    popular_books = []
    device_stats = {'Mobile': 0, 'Desktop': 0, 'Tablet': 0}
    
    # Permanent stats defaults
    total_words_ever = 0
    total_users_ever = 0
    highest_score_ever = 0
    lowest_score_ever = 0
    total_hours_ever = 0
    
    # 👇 Safe queries 👇
    try:
        total_users = User.query.count() or 0
    except Exception as e:
        print(f"User count error: {e}")
    
    try:
        total_checks = WordCheck.query.count() or 0
    except Exception as e:
        print(f"Check count error: {e}")
    
    try:
        perm_stats = PermanentStats.query.first()
        if perm_stats:
            total_words_ever = perm_stats.total_word_checks or 0
            total_users_ever = perm_stats.total_users or 0
            highest_score_ever = perm_stats.highest_score_ever or 0
            lowest_score_ever = perm_stats.lowest_score_ever or 0
            total_hours_ever = round((perm_stats.total_time_spent or 0) / 3600, 1)
    except Exception as e:
        print(f"Perm stats error: {e}")
    
    try:
        today = datetime.now().date()
        today_stats = DailyStats.query.filter_by(date=today).first()
        if today_stats:
            today_visitors = today_stats.unique_visitors or 0
            today_active = today_stats.active_users or 0
    except Exception as e:
        print(f"Today stats error: {e}")
    
    # 👇 WORD STATS 👇
    try:
        word_stats = db.session.query(
            WordCheck.word,
            db.func.count(WordCheck.id).label('count'),
            db.func.avg(WordCheck.score).label('avg_score')
        ).group_by(WordCheck.word).order_by(db.text('count DESC')).limit(8).all()
    except Exception as e:
        print(f"⚠️ Word stats failed: {e}")
        word_stats = []
    
    # 👇 TOP USERS & POPULAR BOOKS 👇
    try:
        top_users = User.query.order_by(User.words_checked.desc()).limit(8).all()
    except Exception as e:
        print(f"⚠️ Top users failed: {e}")
        top_users = []
    
    try:
        popular_books = Book.query.order_by(Book.times_used.desc()).limit(8).all()
    except Exception as e:
        print(f"⚠️ Popular books failed: {e}")
        popular_books = []
    
    return render_template('admin.html',
        now=now,
        total_users=total_users,
        total_checks=total_checks,
        total_visitors=total_visitors,
        today_visitors=today_visitors,
        today_active=today_active,
        peak_day=peak_day,
        peak_value=peak_value,
        trend_percent=trend_percent,
        total_active_days=total_active_days,
        dates=json.dumps(dates),
        visitors=json.dumps(visitors),
        active_users=json.dumps(active_users_list),
        checks=json.dumps([]),
        geo_stats=geo_stats,
        source_stats=source_stats,
        device_stats=device_stats,
        word_stats=word_stats,
        top_users=top_users,
        popular_books=popular_books,
        total_words_ever=total_words_ever,
        total_users_ever=total_users_ever,
        highest_score_ever=highest_score_ever,
        lowest_score_ever=lowest_score_ever,
        total_hours_ever=total_hours_ever
    )

@app.route('/admin/backfill-stats')
@login_required
def backfill_stats():
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    if current_user.email != ADMIN_EMAIL:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        stats = PermanentStats.query.first()
        if not stats:
            stats = PermanentStats()
            db.session.add(stats)
        
        total_checks = WordCheck.query.count()
        total_users = User.query.count()
        highest = db.session.query(db.func.max(WordCheck.score)).scalar() or 0
        lowest = db.session.query(db.func.min(WordCheck.score)).scalar() or 0
        total_time = db.session.query(db.func.sum(WordCheck.time_spent)).scalar() or 0
        
        stats.total_word_checks = total_checks
        stats.total_users = total_users
        stats.highest_score_ever = highest
        stats.lowest_score_ever = lowest if lowest > 0 else 0
        stats.total_time_spent = total_time
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'total_checks': total_checks,
            'total_users': total_users,
            'highest': highest,
            'lowest': lowest,
            'total_time': total_time
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/seed-sentences')
@login_required
def admin_seed_sentences():
    """Seed the production database with curated sentences"""
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    if current_user.email != ADMIN_EMAIL:
        return jsonify({'error': 'Unauthorized'}), 403
    
    sentences = [
        # ===== EASY =====
        ("The test was very easy and everyone passed.", "easy", "easy"),
        ("She has a beautiful voice that captivates everyone.", "beautiful", "easy"),
        ("The weather today is absolutely perfect for a picnic.", "perfect", "easy"),
        ("He felt happy when he received the good news.", "happy", "easy"),
        ("The food at the restaurant was delicious and affordable.", "delicious", "easy"),
        ("She wore a magnificent dress to the party.", "magnificent", "easy"),
        ("The movie was interesting from beginning to end.", "interesting", "easy"),
        ("His room was always clean and organized.", "organized", "easy"),
        ("The garden looked vibrant after the rain.", "vibrant", "easy"),
        ("She felt comfortable in her new home.", "comfortable", "easy"),
        ("The puzzle was simple enough for a child to solve.", "simple", "easy"),
        ("His explanation was clear and everyone understood.", "clear", "easy"),
        ("The sunset was gorgeous over the ocean.", "gorgeous", "easy"),
        ("She gave a brilliant presentation at work.", "brilliant", "easy"),
        ("The water was refreshing on the hot summer day.", "refreshing", "easy"),
        ("He is a generous person who always helps others.", "generous", "easy"),
        ("The music was relaxing after a long day.", "relaxing", "easy"),
        ("Her smile was radiant and lit up the room.", "radiant", "easy"),
        ("The book was fascinating from the first page.", "fascinating", "easy"),
        ("The mountain view was spectacular at sunrise.", "spectacular", "easy"),
        ("She felt grateful for all the support.", "grateful", "easy"),
        ("The puppy was adorable and playful.", "adorable", "easy"),
        ("His answer was accurate and well-researched.", "accurate", "easy"),
        ("The party was enjoyable for all the guests.", "enjoyable", "easy"),
        ("The forest was peaceful in the early morning.", "peaceful", "easy"),
        ("Her performance was impressive to the judges.", "impressive", "easy"),
        ("The cake was moist and flavorful.", "flavorful", "easy"),
        ("He remained calm during the emergency.", "calm", "easy"),
        ("The hotel room was spacious and luxurious.", "spacious", "easy"),
        ("Her kindness made everyone feel welcome.", "kindness", "easy"),
        ("The athlete was powerful and fast.", "powerful", "easy"),
        ("The solution was obvious once explained.", "obvious", "easy"),
        ("She gave an honest opinion about the matter.", "honest", "easy"),
        ("The bridge was sturdy and well-built.", "sturdy", "easy"),
        ("His courage inspired the entire team.", "courage", "easy"),
        ("The flower arrangement was elegant and simple.", "elegant", "easy"),
        ("She felt nervous before the big performance.", "nervous", "easy"),
        ("The coffee was bitter but energizing.", "bitter", "easy"),
        ("His dedication to the project was remarkable.", "dedication", "easy"),
        ("The beach was crowded during the holiday weekend.", "crowded", "easy"),
        ("She was curious about the mysterious package.", "curious", "easy"),
        ("The instructions were confusing at first.", "confusing", "easy"),
        ("His loyalty to his friends was unwavering.", "loyalty", "easy"),
        ("The dessert was sweet and satisfying.", "sweet", "easy"),
        ("She felt proud of her accomplishments.", "proud", "easy"),
        ("The journey was long but worthwhile.", "worthwhile", "easy"),
        ("His patience with children was admirable.", "patience", "easy"),
        ("The apartment was cozy and warm in winter.", "cozy", "easy"),
        ("She made a wise decision about her career.", "wise", "easy"),
        ("The athlete was exhausted after the marathon.", "exhausted", "easy"),
        ("His humor lightened the tense situation.", "humor", "easy"),
        ("The evidence was convincing to the jury.", "convincing", "easy"),
        ("She felt lonely in the big city.", "lonely", "easy"),
        ("The project was successful beyond expectations.", "successful", "easy"),
        ("His anger was justified given the circumstances.", "justified", "easy"),
        ("The silence was awkward during the dinner.", "awkward", "easy"),
        ("She remained optimistic despite the challenges.", "optimistic", "easy"),
        ("The sculpture was creative and unique.", "creative", "easy"),
        ("His apology seemed sincere and heartfelt.", "sincere", "easy"),
        ("The storm was fierce but passed quickly.", "fierce", "easy"),
        ("She felt relieved after finishing the exam.", "relieved", "easy"),
        ("The tradition was ancient and respected.", "ancient", "easy"),
        ("His knowledge of history was extensive.", "extensive", "easy"),
        ("The baby was gentle and rarely cried.", "gentle", "easy"),
        ("She was determined to achieve her goals.", "determined", "easy"),
        ("The joke was funny and made everyone laugh.", "funny", "easy"),
        ("His leadership transformed the company.", "leadership", "easy"),
        ("The painting was colorful and abstract.", "colorful", "easy"),
        ("She felt anxious before the interview.", "anxious", "easy"),
        ("The diamond was precious and rare.", "precious", "easy"),
        ("His recovery was rapid after the surgery.", "rapid", "easy"),
        ("The mystery was intriguing to the detective.", "intriguing", "easy"),
        ("She was modest about her achievements.", "modest", "easy"),
        ("The river was shallow near the shore.", "shallow", "easy"),
        ("His victory was decisive and complete.", "decisive", "easy"),
        ("The fabric was soft and comfortable.", "soft", "easy"),
        ("She felt jealous of her sister's success.", "jealous", "easy"),
        ("The argument was reasonable and logical.", "reasonable", "easy"),
        ("His talent for music was natural.", "natural", "easy"),
        ("The city was noisy at night.", "noisy", "easy"),
        ("She was faithful to her principles.", "faithful", "easy"),
        ("The soil was fertile and productive.", "fertile", "easy"),
        ("His excuse was lame and unconvincing.", "lame", "easy"),
        ("The forest was dense and dark.", "dense", "easy"),
        ("She felt dizzy after spinning around.", "dizzy", "easy"),
        ("The soup was hot and spicy.", "spicy", "easy"),
        ("His grip was firm and confident.", "firm", "easy"),
        ("The blanket was warm and fuzzy.", "fuzzy", "easy"),
        ("She was eager to start the new project.", "eager", "easy"),
        ("The road was steep and winding.", "steep", "easy"),
        ("His voice was hoarse from shouting.", "hoarse", "easy"),
        ("The kitten was tiny and fragile.", "fragile", "easy"),
        ("She felt gloomy on the rainy day.", "gloomy", "easy"),
        ("The award was prestigious and coveted.", "prestigious", "easy"),
        ("His manners were polite and refined.", "polite", "easy"),
        ("The task was tedious and boring.", "tedious", "easy"),
        ("She was thrilled about the surprise party.", "thrilled", "easy"),
        ("The ocean was calm and serene.", "serene", "easy"),
        ("His memory was sharp and reliable.", "sharp", "easy"),
        ("The pillow was fluffy and soft.", "fluffy", "easy"),
        ("She felt awkward at the formal dinner.", "awkward", "easy"),
        
        # ===== MEDIUM =====
        ("The politician's rhetoric was inflammatory and divisive.", "rhetoric", "medium"),
        ("She showed remarkable resilience after the devastating loss.", "resilience", "medium"),
        ("His ambivalence about the decision frustrated his advisors.", "ambivalence", "medium"),
        ("The professor's erudite lecture captivated the graduate students.", "erudite", "medium"),
        ("Her penchant for perfection made her an excellent editor.", "penchant", "medium"),
        ("The company's avarice ultimately led to its bankruptcy.", "avarice", "medium"),
        ("The ubiquitous nature of social media has transformed communication.", "ubiquitous", "medium"),
        ("His laconic response hinted at deeper frustration.", "laconic", "medium"),
        ("The ephemeral beauty of the sunset reminded us to cherish moments.", "ephemeral", "medium"),
        ("She tried to elucidate the complex theory with simple analogies.", "elucidate", "medium"),
        ("The witness gave an ambiguous account of the incident.", "ambiguous", "medium"),
        ("His benevolent donation transformed the small community.", "benevolent", "medium"),
        ("The scientist conducted meticulous research over two decades.", "meticulous", "medium"),
        ("She attempted to placate the angry customer with a refund.", "placate", "medium"),
        ("The pragmatic solution saved the company millions.", "pragmatic", "medium"),
        ("His intrepid exploration of the uncharted cave made headlines.", "intrepid", "medium"),
        ("The two theories were diametrically opposed to each other.", "diametrically", "medium"),
        ("Her ostentatious display of wealth made others uncomfortable.", "ostentatious", "medium"),
        ("The storm wrought catastrophic damage on the coastal town.", "catastrophic", "medium"),
        ("She showed incredible tenacity in pursuing her dreams.", "tenacity", "medium"),
        ("His verbose explanation confused rather than clarified.", "verbose", "medium"),
        ("The novel's protagonist was a neurotic and anxious character.", "neurotic", "medium"),
        ("Her clandestine meetings aroused suspicion among colleagues.", "clandestine", "medium"),
        ("The professor's pedantic corrections annoyed his students.", "pedantic", "medium"),
        ("His stoic demeanor hid a deeply emotional nature.", "stoic", "medium"),
        ("The company's nefarious practices were exposed by journalists.", "nefarious", "medium"),
        ("She gave a succinct summary of the complex report.", "succinct", "medium"),
        ("His gregarious personality made him popular at parties.", "gregarious", "medium"),
        ("The artist's eclectic style drew from many traditions.", "eclectic", "medium"),
        ("Her fastidious attention to detail impressed her superiors.", "fastidious", "medium"),
        ("The arduous journey took them through treacherous terrain.", "arduous", "medium"),
        ("His capricious decisions frustrated his employees.", "capricious", "medium"),
        ("The serendipitous discovery changed the course of science.", "serendipitous", "medium"),
        ("She was loquacious when discussing her favorite topics.", "loquacious", "medium"),
        ("The esoteric subject matter confused most readers.", "esoteric", "medium"),
        ("His magnanimous gesture surprised everyone who knew him.", "magnanimous", "medium"),
        ("The deleterious effects of the drug were well-documented.", "deleterious", "medium"),
        ("Her mellifluous voice made her a successful audiobook narrator.", "mellifluous", "medium"),
        ("The recalcitrant student refused to follow classroom rules.", "recalcitrant", "medium"),
        ("His perspicacious analysis impressed the board of directors.", "perspicacious", "medium"),
        ("The lugubrious music set a somber mood for the film.", "lugubrious", "medium"),
        ("Her obsequious behavior toward the boss annoyed coworkers.", "obsequious", "medium"),
        ("The insidious disease progressed without noticeable symptoms.", "insidious", "medium"),
        ("His sardonic wit often offended those who didn't know him well.", "sardonic", "medium"),
        ("The mercurial weather made planning outdoor events difficult.", "mercurial", "medium"),
        ("She gave an impassioned speech that moved the audience to tears.", "impassioned", "medium"),
        ("The parsimonious manager refused to approve necessary expenses.", "parsimonious", "medium"),
        ("His sanguine outlook helped the team stay positive during crisis.", "sanguine", "medium"),
        ("The vociferous protesters could be heard from blocks away.", "vociferous", "medium"),
        ("Her diffident manner made public speaking a challenge.", "diffident", "medium"),
        
        # ===== HARD =====
        ("The lecture seemed to obfuscate rather than clarify the complex topic.", "obfuscate", "hard"),
        ("Her peroration brought the audience to their feet with thunderous applause.", "peroration", "hard"),
        ("The sycophantic courtiers flattered the emperor constantly.", "sycophantic", "hard"),
        ("His solipsistic worldview prevented him from empathizing with others.", "solipsistic", "hard"),
        ("The esoteric philosophical text required years of study to comprehend.", "esoteric", "hard"),
        ("Her pulchritudinous appearance captivated everyone at the gala.", "pulchritudinous", "hard"),
        ("The pusillanimous leader abandoned his people during the crisis.", "pusillanimous", "hard"),
        ("His grandiloquent promises proved impossible to fulfill.", "grandiloquent", "hard"),
        ("The obsequious assistant agreed with everything the boss said.", "obsequious", "hard"),
        ("Her perspicacious analysis revealed flaws others had missed.", "perspicacious", "hard"),
        ("The magnanimous victor forgave his defeated enemies.", "magnanimous", "hard"),
        ("His loquacious nature made him the life of every party.", "loquacious", "hard"),
        ("The mercurial artist changed styles with every new project.", "mercurial", "hard"),
        ("Her sangfroid during the emergency saved many lives.", "sangfroid", "hard"),
        ("The lugubrious atmosphere of the funeral weighed on everyone.", "lugubrious", "hard"),
        ("His supercilious smirk revealed his contempt for others.", "supercilious", "hard"),
        ("The deleterious consequences of the policy became apparent.", "deleterious", "hard"),
        ("Her mellifluous singing voice brought tears to the audience.", "mellifluous", "hard"),
        ("The recalcitrant child refused every form of discipline.", "recalcitrant", "hard"),
        ("His perspicacious investment strategy yielded enormous returns.", "perspicacious", "hard"),
        ("The bromidic speech put half the audience to sleep.", "bromidic", "hard"),
        ("The contumacious student was eventually expelled from school.", "contumacious", "hard"),
        ("His encomium for the retiring professor was deeply moving.", "encomium", "hard"),
        ("The crepuscular light created an eerie atmosphere in the forest.", "crepuscular", "hard"),
        ("Her ataraxia in the face of chaos amazed her colleagues.", "ataraxia", "hard"),
        ("The cachinnation from the audience showed the comedy's success.", "cachinnation", "hard"),
        ("His peregrinations across Asia provided material for three books.", "peregrinations", "hard"),
        ("The jeremiad about society's decline resonated with many readers.", "jeremiad", "hard"),
        ("Her apotheosis from actress to cultural icon took a decade.", "apotheosis", "hard"),
        ("The eleemosynary organization provided aid to thousands.", "eleemosynary", "hard"),
        ("His tergiversation on the issue frustrated his supporters.", "tergiversation", "hard"),
        ("The fuliginous smoke from the factory darkened the sky.", "fuliginous", "hard"),
        ("Her vituperative attack on her opponent backfired politically.", "vituperative", "hard"),
        ("The quidnunc neighbor knew everyone's business before they did.", "quidnunc", "hard"),
        ("His concupiscent desires led to his eventual downfall.", "concupiscent", "hard"),
        ("The cynosure of the entire ceremony was the bride herself.", "cynosure", "hard"),
        ("Her psittacism in repeating others' ideas showed no original thought.", "psittacism", "hard"),
        ("The thaumaturgical powers attributed to the relic were legendary.", "thaumaturgical", "hard"),
        ("His mordacious wit made enemies as quickly as admirers.", "mordacious", "hard"),
        ("The inspissated liquid refused to flow through the pipe.", "inspissated", "hard"),
        ("The susurrant whispers in the library created a calming atmosphere.", "susurrant", "hard"),
        ("His ultracrepidarian opinions on medicine frustrated his doctor.", "ultracrepidarian", "hard"),
        ("The plenipotentiary ambassador negotiated the historic treaty.", "plenipotentiary", "hard"),
        ("Her tergiversant behavior made it impossible to trust her.", "tergiversant", "hard"),
        ("The fulgurous lightning illuminated the entire valley.", "fulgurous", "hard"),
        ("His integument was severely damaged by the chemical exposure.", "integument", "hard"),
        ("The maieutic teaching method helped students discover answers themselves.", "maieutic", "hard"),
        ("Her latitudinarian views on religion were ahead of her time.", "latitudinarian", "hard"),
        ("The contumelious remarks about his opponent cost him the election.", "contumelious", "hard"),
        ("His feracious mind produced one brilliant idea after another.", "feracious", "hard"),
    ]
    
    try:
        added = 0
        skipped = 0
        
        for sentence, word, difficulty in sentences:
            existing = SentenceBank.query.filter_by(sentence=sentence).first()
            if not existing:
                s = SentenceBank(
                    sentence=sentence,
                    word=word,
                    difficulty=difficulty,
                    source="Reading Vault Curated"
                )
                db.session.add(s)
                added += 1
            else:
                skipped += 1
            
            if added % 50 == 0:
                db.session.commit()
                print(f"✅ Added {added} sentences...")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Added {added} sentences, skipped {skipped} duplicates',
            'added': added,
            'skipped': skipped,
            'total': SentenceBank.query.count()
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/sw.js')
def serve_sw():
    """Serve the Monetag service worker file"""
    sw_content = '''self.options = {
    "domain": "3nbf4.com",
    "zoneId": 10922184
}
self.lary = ""
importScripts('https://3nbf4.com/act/files/service-worker.min.js?r=sw')'''
    
    from flask import Response
    return Response(sw_content, mimetype='application/javascript')

@app.route('/test-ads')
def test_ads():
    return render_template('test-ads.html')

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

@app.context_processor
def utility_processor():
    def get_score_class(score):
        if score >= 85: return 'excellent'
        elif score >= 70: return 'good'
        elif score >= 55: return 'fair'
        return 'poor'
    return dict(get_score_class=get_score_class)

# ===== CREATE TABLES ON STARTUP =====
with app.app_context():
    db.create_all()
    
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
    ADMIN_NAME = os.getenv('ADMIN_NAME', 'Admin')
    
    if ADMIN_EMAIL and ADMIN_PASSWORD:
        admin = User.query.filter_by(email=ADMIN_EMAIL).first()
        if not admin:
            admin = User(
                email=ADMIN_EMAIL, 
                name=ADMIN_NAME, 
                is_admin=True
            )
            admin.password = generate_password_hash(ADMIN_PASSWORD)
            db.session.add(admin)
            db.session.commit()
            print(f"✅ Admin user created: {ADMIN_EMAIL}")

@app.route('/admin/users')
@login_required
def admin_users():
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
    if current_user.email != ADMIN_EMAIL:
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    users = User.query.order_by(User.words_checked.desc()).all()
    
    return render_template('admin_users.html', users=users)

# Export for Vercel
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
else:
    application = app