import os
import random
import string
import difflib
import ipaddress
from flask import Flask, request, render_template, redirect, url_for, abort, Response, send_from_directory
from dotenv import load_dotenv
from models import db, Note, NoteRevision

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///data.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev')

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
            
        content = request.form.get('content', '')
        
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
    content = latest_rev.content if latest_rev else ""

    if is_raw_request(request):
        if not latest_rev:
            return Response("Not Found", status=404)
        return Response(content, mimetype='text/plain')
        
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
        return Response(rev.content, mimetype='text/plain')
    return render_template('read_only.html', note=note, note_id=note_id, rev=rev)

@app.route('/<note_id>/revisions/<int:version_num>/diff')
def view_diff(note_id, version_num):
    note = Note.query.get_or_404(note_id)
    curr_rev = NoteRevision.query.filter_by(note_id=note_id, version_num=version_num).first_or_404()
    prev_rev = NoteRevision.query.filter_by(note_id=note_id, version_num=version_num-1).first()
    
    prev_content = prev_rev.content.splitlines() if prev_rev else []
    curr_content = curr_rev.content.splitlines()
    
    html_diff = difflib.HtmlDiff().make_table(prev_content, curr_content, fromdesc='Previous', todesc=f'Version {version_num}')
    return render_template('diff.html', note=note, note_id=note_id, html_diff=html_diff, version_num=version_num)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

if __name__ == '__main__':
    # app.run(debug=True, port=5000)
    port = int(os.getenv('PORT', 5000))
    app.run(port=port)
