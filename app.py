from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename  
from flask_cors import CORS
import os
import PyPDF2
import docx2txt
from datetime import datetime
from flask_cors import CORS
import requests
import logging


#CORS
app = Flask(__name__)
CORS(app)
<<<<<<< HEAD
=======
C_PORT = 5001
>>>>>>> 21ab5f483dba4992f14a69e94f0d7259d34f7c9c

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://admin:Wfe._84ivN3UX4j.X2z!dfKnAiRA@content-database-1.c1qcm4w2sbne.us-east-1.rds.amazonaws.com:3306/content_db'
# app.config['SQLALCHEMY_BINDS'] = {
#     'conversation_db': 'mysql+pymysql://admin:Wfe._84ivN3UX4j.X2z!dfKnAiRA@content-database-1.c1qcm4w2sbne.us-east-1.rds.amazonaws.com:3306/conversation_db'
# }
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
    user_id = request.form.get('user_id')  # Obtain user_id from request form data

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    try:
        user_id = int(user_id)
    except ValueError:
        return jsonify({'error': 'user_id must be an integer'}), 400

    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(file_path)

        extracted_text = extract_text(file_path)

        # Save content info to the content DB
        try:
            new_content = Content(
                user_id=user_id,
                file_name=filename,
                file_type=file.content_type,
                file_size=os.path.getsize(file_path),
                s3_key=file_path  # In a real scenario, this would be the S3 key
            )
            db.session.add(new_content)
            db.session.commit()
            logging.info(f"Content saved with ID: {new_content.id}")
        except Exception as e:
            logging.exception("Failed to save content to the database")
            return jsonify({'error': 'Failed to save content'}), 500

        # Save chunks to the content chunk DB
        chunk_size = 1000  # Adjust as needed
        chunks = [extracted_text[i:i+chunk_size] for i in range(0, len(extracted_text), chunk_size)]

        for i, chunk_text in enumerate(chunks):
            try:
                new_chunk = ContentChunk(
                    content_id=new_content.id,
                    chunk_order=i,
                    chunk_text=chunk_text
                )
                db.session.add(new_chunk)
            except Exception as e:
                logging.exception("Failed to save content chunk to the database")
                return jsonify({'error': 'Failed to save content chunk'}), 500

        new_content.chunk_count = len(chunks)
        db.session.commit()
        logging.info(f"Total chunks saved: {new_content.chunk_count}")

        # Send POST requests to the conversation API
        conversation_api_url = 'http://loReplace with your Conversation API URL' 

        for i, chunk_text in enumerate(chunks):
            try:
                # Create a new conversation
                conversation_data = {
                    'user_id': user_id
                }
                conversation_response = requests.post(f'{conversation_api_url}/conversations', json=conversation_data)

                if conversation_response.status_code == 201:
                    conversation = conversation_response.json()
                    conversation_id = conversation['id']
                    logging.info(f"Conversation created with ID: {conversation_id}")

                    # Create a new message
                    message_data = {
                        'conversation_id': conversation_id,
                        'sender': 'user',
                        'content_chunk_id': new_content.id,  # Assuming you want to associate the message with the content ID
                        'message_text': chunk_text[:200]  # First 200 characters
                    }
                    message_response = requests.post(f'{conversation_api_url}/messages', json=message_data)

                    if message_response.status_code == 201:
                        logging.info(f"Message created in conversation ID: {conversation_id}")
                    else:
                        logging.error(f"Failed to create message in conversation ID: {conversation_id}")
                        logging.error(f"Response: {message_response.text}")
                else:
                    logging.error("Failed to create conversation")
                    logging.error(f"Response: {conversation_response.text}")
            except Exception as e:
                logging.exception("An error occurred while creating conversation and message")

        preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text

        return jsonify({
            'message': 'File successfully uploaded and processed',
            'filename': filename,
            'preview': preview
        }), 200
    else:
        return jsonify({'error': 'File type not allowed'}), 400
    
@app.route('/api/process_chunk', methods=['POST'])
def process_chunk():
    data = request.get_json()
    chunk_id = data.get('chunk_id')
    user_id = data.get('user_id')  

    if not chunk_id:
        return jsonify({'error': 'chunk_id is required'}), 400

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    try:
        chunk = ContentChunk.query.get(chunk_id)
        if not chunk:
            return jsonify({'error': 'Content chunk not found'}), 404

        # Generate required fields for conversation creation
        conversation_api_url = 'http://Replace with your Conversation API URL'  

        conversation_data = {
            'user_id': int(user_id)
        }
        conversation_response = requests.post(f'{conversation_api_url}/conversations', json=conversation_data)

        if conversation_response.status_code == 201:
            conversation = conversation_response.json()
            conversation_id = conversation['id']
            logging.info(f"Conversation created with ID: {conversation_id}")

            # Create a new message
            message_data = {
                'conversation_id': conversation_id,
                'sender': 'user',
                'content_chunk_id': chunk.id,
                'message_text': chunk.chunk_text[:200]  # First 200 characters
            }
            message_response = requests.post(f'{conversation_api_url}/messages', json=message_data)

            if message_response.status_code == 201:
                logging.info(f"Message created in conversation ID: {conversation_id}")
                return jsonify({'message': 'Chunk processed successfully'}), 200
            else:
                logging.error(f"Failed to create message in conversation ID: {conversation_id}")
                logging.error(f"Response: {message_response.text}")
                return jsonify({'error': 'Failed to create message'}), 500
        else:
            logging.error("Failed to create conversation")
            logging.error(f"Response: {conversation_response.text}")
            return jsonify({'error': 'Failed to create conversation'}), 500
    except Exception as e:
        logging.exception("An error occurred while processing chunk")
        return jsonify({'error': 'An error occurred while processing chunk'}), 500
    

@app.route('/api/files', methods=['GET'])
def list_files():
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    return jsonify({'files': files})

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=C_PORT)