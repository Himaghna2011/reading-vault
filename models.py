from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import json

db = SQLAlchemy()

class SentenceBank(db.Model):
    """Curated sentences for vocabulary practice - never deleted"""
    __tablename__ = 'sentence_bank'
    
    id = db.Column(db.Integer, primary_key=True)
    sentence = db.Column(db.Text, nullable=False)
    word = db.Column(db.String(100), nullable=False, index=True)
    difficulty = db.Column(db.String(20), default='medium')  # easy, medium, hard
    category = db.Column(db.String(50))  # GRE, SAT, General, Literature
    source = db.Column(db.String(200))  # Where it came from
    times_used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserSentenceRecord(db.Model):
    """Track which sentences each user has seen"""
    __tablename__ = 'user_sentence_record'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sentence_id = db.Column(db.Integer, db.ForeignKey('sentence_bank.id'), nullable=False)
    seen_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'sentence_id', name='unique_user_sentence'),
    )

class PermanentStats(db.Model):
    """NEVER DELETED - Cumulative stats that only increase"""
    __tablename__ = 'permanent_stats'
    
    id = db.Column(db.Integer, primary_key=True)
    total_word_checks = db.Column(db.Integer, default=0)
    total_users = db.Column(db.Integer, default=0)
    highest_score_ever = db.Column(db.Integer, default=0)
    lowest_score_ever = db.Column(db.Integer, default=100)
    total_time_spent = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(UserMixin, db.Model):
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100))
    user_type = db.Column(db.String(50))
    is_admin = db.Column(db.Boolean, default=False)
    
    # Stats
    words_checked = db.Column(db.Integer, default=0)
    total_time_spent = db.Column(db.Integer, default=0)
    current_streak = db.Column(db.Integer, default=0)
    longest_streak = db.Column(db.Integer, default=0)
    last_active = db.Column(db.Date)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    word_checks = db.relationship('WordCheck', back_populates='user', lazy='dynamic')
    visitor_logs = db.relationship('VisitorLog', back_populates='user', lazy='dynamic')
    
    def get_average_score(self):
        """Calculate user's average score"""
        checks = self.word_checks.all()
        if not checks:
            return 0
        total = sum(check.score for check in checks)
        return round(total / len(checks), 1)
    
    def get_total_checks(self):
        """Get total number of word checks"""
        return self.word_checks.count()
    
    def __repr__(self):
        return f'<User {self.email}>'


class WordCheck(db.Model):
    __tablename__ = 'word_check'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    word = db.Column(db.String(100), nullable=False, index=True)
    sentence = db.Column(db.Text, nullable=False)
    book_title = db.Column(db.String(200))
    author = db.Column(db.String(200))
    user_guess = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    ai_feedback = db.Column(db.Text)
    time_spent = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationship
    user = db.relationship('User', back_populates='word_checks')
    
    __table_args__ = (
        db.Index('idx_user_score', 'user_id', 'score'),
        db.Index('idx_word_score', 'word', 'score'),
    )
    
    def get_feedback(self):
        """Parse AI feedback JSON"""
        if self.ai_feedback:
            try:
                return json.loads(self.ai_feedback)
            except:
                return {}
        return {}
    
    def __repr__(self):
        return f'<WordCheck {self.word} - {self.score}%>'


class Book(db.Model):
    __tablename__ = 'book'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(200), nullable=False)
    times_used = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.Index('idx_title_author', 'title', 'author'),
    )
    
    def __repr__(self):
        return f'<Book {self.title} by {self.author}>'


class WallWord(db.Model):
    __tablename__ = 'wall_word'
    
    id = db.Column(db.Integer, primary_key=True)
    word = db.Column(db.String(100), nullable=False, index=True)
    wall_type = db.Column(db.String(20))
    total_checks = db.Column(db.Integer, default=0)
    average_score = db.Column(db.Float, default=0)
    sample_sentence = db.Column(db.Text)
    book_title = db.Column(db.String(200))
    author = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<WallWord {self.word} - {self.wall_type} ({self.average_score}%)>'


class VisitorLog(db.Model):
    __tablename__ = 'visitor_log'
    
    id = db.Column(db.Integer, primary_key=True)
    page_visited = db.Column(db.String(200))
    country = db.Column(db.String(50))
    is_unique = db.Column(db.Boolean, default=True)
    session_id = db.Column(db.String(100), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationship
    user = db.relationship('User', back_populates='visitor_logs')
    
    __table_args__ = (
        db.Index('idx_session_date', 'session_id', 'created_at'),
        db.Index('idx_country_date', 'country', 'created_at'),
        db.Index('idx_page_date', 'page_visited', 'created_at'),
    )
    
    def __repr__(self):
        return f'<VisitorLog {self.ip_address} - {self.page_visited}>'


class DailyStats(db.Model):
    __tablename__ = 'daily_stats'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False, index=True)
    total_visitors = db.Column(db.Integer, default=0)
    unique_visitors = db.Column(db.Integer, default=0)
    new_users = db.Column(db.Integer, default=0)
    active_users = db.Column(db.Integer, default=0)
    total_checks = db.Column(db.Integer, default=0)
    avg_score = db.Column(db.Float, default=0)
    total_time_spent = db.Column(db.Integer, default=0)
    returning_users = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<DailyStats {self.date} - {self.unique_visitors} visitors>'