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
    content = db.Column(db.Text, nullable=False)  # HTML content (was plain text before)
    version_num = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Attachment(db.Model):
    __tablename__ = 'attachments'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    note_id = db.Column(db.String(64), db.ForeignKey('notes.id'), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    s3_key = db.Column(db.String(512), nullable=False, unique=True)
    content_type = db.Column(db.String(128))
    size = db.Column(db.Integer)
    is_image = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    note = db.relationship('Note', backref=db.backref('attachments', lazy=True))
