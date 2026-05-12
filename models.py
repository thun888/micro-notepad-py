from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Note(db.Model):
    __tablename__ = 'notes'
    id = db.Column(db.String(64), primary_key=True)
    title = db.Column(db.String(128), default='未命名')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    revisions = db.relationship('NoteRevision', backref='note', lazy=True, order_by='desc(NoteRevision.version_num)')

class NoteRevision(db.Model):
    __tablename__ = 'note_revisions'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    note_id = db.Column(db.String(64), db.ForeignKey('notes.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    version_num = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
