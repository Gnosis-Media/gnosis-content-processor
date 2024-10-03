from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import os
import PyPDF2
import docx2txt
from datetime import datetime
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:dbuserdbuser@localhost:3306/content_db'
# app.config['SQLALCHEMY_BINDS'] = {
#     'conversation_db': 'mysql+pymysql://root:dbuserdbuser@localhost:3306/conversation_db'
# }

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://admin:Wfe._84ivN3UX4j.X2z!dfKnAiRA@content-database-1.c1qcm4w2sbne.us-east-1.rds.amazonaws.com:3306/content_db'
app.config['SQLALCHEMY_BINDS'] = {
    'conversation_db': 'mysql+pymysql://admin:Wfe._84ivN3UX4j.X2z!dfKnAiRA@content-database-1.c1qcm4w2sbne.us-east-1.rds.amazonaws.com:3306/conversation_db'
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Define database models
class Content(db.Model):
    __tablename__ = 'content'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=False)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    file_size = db.Column(db.Integer, nullable=False)
    s3_key = db.Column(db.String(255))
    chunk_count = db.Column(db.Integer, default=0)
    custom_prompt = db.Column(db.Text)

class ContentChunk(db.Model):
    __tablename__ = 'content_chunk'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    content_id = db.Column(db.Integer, db.ForeignKey('content.id'))
    chunk_order = db.Column(db.Integer, nullable=False)
    chunk_text = db.Column(db.Text, nullable=False)
    embedding = db.Column(db.JSON)

class Conversation(db.Model):
    __bind_key__ = 'conversation_db'
    __tablename__ = 'conversation'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Message(db.Model):
    __bind_key__ = 'conversation_db'
    __tablename__ = 'message'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'))
    sender = db.Column(db.Enum('user', 'ai'), nullable=False)
    content_chunk_id = db.Column(db.Integer)
    message_text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(file_path):
    _, file_extension = os.path.splitext(file_path)
    if file_extension == '.pdf':
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
        return text
    elif file_extension in ['.doc', '.docx']:
        return docx2txt.process(file_path)
    elif file_extension == '.txt':
        with open(file_path, 'r') as file:
            return file.read()
    else:
        return "Unsupported file type"

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        extracted_text = extract_text(file_path)
        
        # Save content info to the content db
        new_content = Content(
            user_id=1,  # You should get this from the authenticated user
            file_name=filename,
            file_type=file.content_type,
            file_size=os.path.getsize(file_path),
            s3_key=file_path  # In a real scenario, this would be the S3 key
        )
        db.session.add(new_content)
        db.session.commit()
        
        # Save chunks to the chunk db
        chunk_size = 1000  # Adjust as needed
        chunks = [extracted_text[i:i+chunk_size] for i in range(0, len(extracted_text), chunk_size)]
        
        for i, chunk in enumerate(chunks):
            new_chunk = ContentChunk(
                content_id=new_content.id,
                chunk_order=i,
                chunk_text=chunk
            )
            db.session.add(new_chunk)
        
        new_content.chunk_count = len(chunks)
        db.session.commit()
        
        # Create a new conversation for each chunk
        for chunk in chunks:
            new_conversation = Conversation(user_id=1)  # You should get this from the authenticated user
            db.session.add(new_conversation)
            db.session.commit()
            
            new_message = Message(
                conversation_id=new_conversation.id,
                sender='user',
                content_chunk_id=new_chunk.id,
                message_text=chunk[0:200]
            )
            db.session.add(new_message)
        
        db.session.commit()
        
        preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text
        
        return jsonify({
            'message': 'File successfully uploaded and processed',
            'filename': filename,
            'preview': preview
        }), 200
    else:
        return jsonify({'error': 'File type not allowed'}), 400

@app.route('/api/files', methods=['GET'])
def list_files():
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    return jsonify({'files': files})

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)