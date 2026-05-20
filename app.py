import os
import random
import string
import difflib
import ipaddress
import uuid
from html.parser import HTMLParser
from flask import Flask, request, render_template, redirect, url_for, abort, Response, send_from_directory, jsonify
from dotenv import load_dotenv
from models import db, Note, NoteRevision, Attachment
import boto3
import bleach

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///data.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev')
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('UPLOAD_MAX_SIZE', 10 * 1024 * 1024))

ALLOWED_EXTENSIONS = None  # None = allow all file types
IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff', 'webp'}
ALLOWED_TAGS = ['p', 'h1', 'h2', 'h3', 'strong', 'em', 'u', 's', 'ol', 'ul', 'li',
                'a', 'img', 'pre', 'code', 'blockquote', 'br', 'div', 'span']
ALLOWED_ATTRS = {
    'a': ['href', 'target', 'rel', 'class', 'style', 'contenteditable', 'data-*'],
    'img': ['src', 'alt', 'width', 'height', 'class', 'style'],
    'div': ['class', 'data-*', 'style', 'contenteditable'],
    'span': ['class', 'style'],
}

trusted_networks = []
for ip_str in os.getenv('TRUSTED_IPS', '').split(','):
    ip_str = ip_str.strip()
    if ip_str:
        try:
            trusted_networks.append(ipaddress.ip_network(ip_str, strict=False))
        except ValueError:
            pass
app.config['TRUSTED_NETWORKS'] = trusted_networks

db.init_app(app)

with app.app_context():
    db.create_all()

# --- S3 helpers ---

def get_s3_client():
    return boto3.client(
        's3',
        region_name=os.getenv('S3_REGION', 'auto'),
        endpoint_url=os.getenv('S3_ENDPOINT_URL') or None,
        aws_access_key_id=os.getenv('S3_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),
    )

def get_presigned_url(s3_key, filename=None):
    client = get_s3_client()
    bucket = os.getenv('S3_BUCKET')
    expire = int(os.getenv('S3_PRESIGN_EXPIRE', 3600))
    params = {'Bucket': bucket, 'Key': s3_key}
    if filename:
        from urllib.parse import quote
        encoded_filename = quote(filename.encode('utf-8'))
        params['ResponseContentDisposition'] = f"attachment; filename*=UTF-8''{encoded_filename}"
    return client.generate_presigned_url(
        'get_object',
        Params=params,
        ExpiresIn=expire,
    )

def upload_to_s3(file_data, s3_key, content_type):
    client = get_s3_client()
    bucket = os.getenv('S3_BUCKET')
    client.put_object(Bucket=bucket, Key=s3_key, Body=file_data, ContentType=content_type)

def delete_from_s3(s3_key):
    client = get_s3_client()
    bucket = os.getenv('S3_BUCKET')
    client.delete_object(Bucket=bucket, Key=s3_key)

def allowed_file(filename):
    if ALLOWED_EXTENSIONS is None:
        return True
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in IMAGE_EXTENSIONS

# --- HTML text extraction for diff ---

class HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'br', 'blockquote', 'pre', 'tr'}
    def __init__(self):
        super().__init__()
        self.result = []
    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.result.append('\n')
    def handle_data(self, data):
        self.result.append(data)
    def get_text(self):
        return ''.join(self.result)

def html_to_text(html):
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text().strip()

def sanitize_html(html):
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)

def content_to_html(content):
    """Convert plain text to HTML if needed (backward compatibility)."""
    if not content:
        return ''
    stripped = content.strip()
    if stripped.startswith('<'):
        return stripped
    # Plain text: wrap each line in <p>
    lines = stripped.split('\n')
    return ''.join(f'<p>{line}</p>' for line in lines)

@app.context_processor
def inject_can_edit():
    return dict(can_edit=can_edit())

def can_edit():
    trusted_networks = app.config.get('TRUSTED_NETWORKS', [])
    if not trusted_networks:
        return True # 如果没有配置可信IP，则默认全部允许
    # 获取真实IP，考虑代理
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    client_ip = client_ip.split(',')[0].strip() if client_ip else ''
    try:
        ip_obj = ipaddress.ip_address(client_ip)
        return any(ip_obj in net for net in trusted_networks)
    except ValueError:
        return False

def generate_note_id(length=5):
    chars = '234579abcdefghjkmnpqrstwxyz'
    return ''.join(random.choice(chars) for _ in range(length))

def is_raw_request(req):
    if req.args.get('raw'):
        return True
    ua = req.headers.get('User-Agent', '')
    if ua.startswith('curl') or ua.startswith('Wget'):
        return True
    return False

@app.route('/')
def index():
    note_id = generate_note_id()
    return redirect(url_for('view_note', note_id=note_id))

@app.route('/<note_id>', methods=['GET', 'POST'])
def view_note(note_id):
    if not all(c in string.ascii_letters + string.digits + '_-' for c in note_id) or len(note_id) > 64:
        abort(400, "Invalid note ID")
        
    note = Note.query.get(note_id)
    
    if request.method == 'POST':
        if not can_edit():
            abort(403, "Read-only mode: Your IP is not in the trusted list.")

        content = sanitize_html(request.form.get('content', ''))

        if not note:
            note = Note(id=note_id, title='未命名')
            db.session.add(note)

        latest_rev = NoteRevision.query.filter_by(note_id=note_id).order_by(NoteRevision.version_num.desc()).first()
        next_version = (latest_rev.version_num + 1) if latest_rev else 1

        if not latest_rev or latest_rev.content != content:
            new_rev = NoteRevision(note_id=note_id, content=content, version_num=next_version)
            db.session.add(new_rev)
            db.session.commit()

        return redirect(url_for('view_note', note_id=note_id))

    latest_rev = NoteRevision.query.filter_by(note_id=note_id).order_by(NoteRevision.version_num.desc()).first()
    content = content_to_html(latest_rev.content) if latest_rev else ""

    if is_raw_request(request):
        if not latest_rev:
            return Response("Not Found", status=404)
        if request.args.get('format') == 'text':
            return Response(html_to_text(content), mimetype='text/plain')
        return Response(content, mimetype='text/html')
        
    return render_template('edit.html', note=note, note_id=note_id, content=content, latest_rev=latest_rev)

@app.route('/<note_id>/title', methods=['POST'])
def update_title(note_id):
    if not can_edit():
        return {'status': 'error', 'message': 'Forbidden'}, 403
        
    data = request.get_json()
    if data and 'title' in data:
        note = Note.query.get(note_id)
        new_title = data['title'].strip() or '未命名'
        if not note:
            note = Note(id=note_id, title=new_title)
            db.session.add(note)
        else:
            note.title = new_title
        db.session.commit()
        return {'status': 'ok'}
    return {'status': 'error'}, 400

@app.route('/<note_id>/revisions')
def view_revisions(note_id):
    note = Note.query.get_or_404(note_id)
    revisions = NoteRevision.query.filter_by(note_id=note_id).order_by(NoteRevision.version_num.desc()).all()
    return render_template('revisions.html', note=note, revisions=revisions, note_id=note_id)

@app.route('/<note_id>/<int:version_num>')
def view_revision_content(note_id, version_num):
    note = Note.query.get_or_404(note_id)
    rev = NoteRevision.query.filter_by(note_id=note_id, version_num=version_num).first_or_404()
    if is_raw_request(request):
        if request.args.get('format') == 'text':
            return Response(html_to_text(rev.content), mimetype='text/plain')
        return Response(rev.content, mimetype='text/html')
    return render_template('read_only.html', note=note, note_id=note_id, rev=rev, content=content_to_html(rev.content))

@app.route('/<note_id>/revisions/<int:version_num>/diff')
def view_diff(note_id, version_num):
    note = Note.query.get_or_404(note_id)
    curr_rev = NoteRevision.query.filter_by(note_id=note_id, version_num=version_num).first_or_404()
    prev_rev = NoteRevision.query.filter_by(note_id=note_id, version_num=version_num-1).first()

    prev_text = html_to_text(prev_rev.content) if prev_rev else ""
    curr_text = html_to_text(curr_rev.content)

    prev_lines = prev_text.splitlines()
    curr_lines = curr_text.splitlines()

    html_diff = difflib.HtmlDiff().make_table(prev_lines, curr_lines, fromdesc='Previous', todesc=f'Version {version_num}')
    return render_template('diff.html', note=note, note_id=note_id, html_diff=html_diff, version_num=version_num,
                           prev_html=prev_rev.content if prev_rev else "", curr_html=curr_rev.content)

@app.route('/<note_id>/upload', methods=['POST'])
def upload_attachment(note_id):
    if not can_edit():
        return jsonify({'status': 'error', 'message': 'Forbidden'}), 403

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename or not allowed_file(file.filename):
        return jsonify({'status': 'error', 'message': 'File type not allowed'}), 400

    file_data = file.read()
    if len(file_data) > app.config['MAX_CONTENT_LENGTH']:
        return jsonify({'status': 'error', 'message': 'File too large'}), 400

    # Ensure note exists
    note = Note.query.get(note_id)
    if not note:
        note = Note(id=note_id, title='未命名')
        db.session.add(note)

    ext = file.filename.rsplit('.', 1)[1].lower()
    s3_key = f"notes/{note_id}/{uuid.uuid4().hex}.{ext}"
    is_img = is_image_file(file.filename)

    upload_to_s3(file_data, s3_key, file.content_type or 'application/octet-stream')

    attachment = Attachment(
        note_id=note_id,
        filename=file.filename,
        s3_key=s3_key,
        content_type=file.content_type,
        size=len(file_data),
        is_image=is_img,
    )
    db.session.add(attachment)
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'attachment_id': attachment.id,
        'url': url_for('download_attachment_file', attachment_id=attachment.id),
        'filename': file.filename,
        'is_image': is_img,
    })

@app.route('/attachments/<int:attachment_id>/download')
def download_attachment_file(attachment_id):
    attachment = Attachment.query.get_or_404(attachment_id)
    presigned_url = get_presigned_url(attachment.s3_key, filename=attachment.filename)
    return redirect(presigned_url)

@app.route('/attachments/<int:attachment_id>/delete', methods=['POST'])
def delete_attachment(attachment_id):
    if not can_edit():
        return jsonify({'status': 'error', 'message': 'Forbidden'}), 403

    attachment = Attachment.query.get_or_404(attachment_id)
    try:
        delete_from_s3(attachment.s3_key)
    except Exception:
        pass  # Best effort: still delete DB record even if S3 delete fails
    db.session.delete(attachment)
    db.session.commit()

    return jsonify({'status': 'ok'})

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.join(app.root_path, 'static'), filename)

if __name__ == '__main__':
    # app.run(debug=True, port=5000)
    port = int(os.getenv('PORT', 5000))
    app.run(port=port)
