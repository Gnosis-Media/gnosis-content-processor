import uuid
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename  
from flask_cors import CORS
import os
import PyPDF2
import docx2txt
from datetime import datetime
import requests
import logging
from threading import Thread
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S', filename='app.log')

# CORS
app = Flask(__name__)
CORS(app)

# Track upload status and results
upload_status = {}
upload_results = {}

C_PORT = 5002

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://admin:VYglUg5GphMwRuOIv6Lz@content-db.c1ytbjumgtbu.us-east-1.rds.amazonaws.com:3306/content_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CONVERSATION_API_URL = 'http://localhost:5000'

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

# Add at the top with other imports
from threading import Thread
from collections import defaultdict

# Add after app initialization
# Track upload status and results
upload_status = {}
upload_results = {}

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        logging.warning("No file part in the request")
        return jsonify({'error': 'No file part in the request'}), 400

    file = request.files['file']
    user_id = request.form.get('user_id')

    if not user_id:
        logging.warning("user_id is required")
        return jsonify({'error': 'user_id is required'}), 400

    try:
        user_id = int(user_id)
    except ValueError:
        logging.warning("user_id must be an integer")
        return jsonify({'error': 'user_id must be an integer'}), 400

    if file.filename == '':
        logging.warning("No selected file")
        return jsonify({'error': 'No selected file'}), 400

    if not allowed_file(file.filename):
        logging.warning("File type not allowed")
        return jsonify({'error': 'File type not allowed'}), 400

    # Save the file content before starting the thread
    file_content = file.read()
    content_type = file.content_type
    original_filename = file.filename

    # Generate upload ID and initialize status
    upload_id = str(uuid.uuid4())
    upload_status[upload_id] = "PROCESSING"
    logging.info(f"Upload started for ID: {upload_id}")

    def process_upload():
        try:
            with app.app_context():
                filename = secure_filename(original_filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                
                # Write the saved content to file
                with open(file_path, 'wb') as f:
                    f.write(file_content)

                # Extract text from file
                extracted_text = extract_text(file_path)
                
                # Save content to database
                new_content = Content(
                    user_id=user_id,
                    file_name=filename,
                    file_type=content_type,
                    file_size=len(file_content),
                    s3_key=file_path
                )
                db.session.add(new_content)
                db.session.flush()  # Get the content ID

                # Process chunks
                chunk_size = 1500
                chunks = [extracted_text[i:i+chunk_size] for i in range(0, len(extracted_text), chunk_size)]
                
                # Save chunks and create conversations
                for i, chunk_text in enumerate(chunks):
                    try:
                        # Save chunk
                        new_chunk = ContentChunk(
                            content_id=new_content.id,
                            chunk_order=i,
                            chunk_text=chunk_text
                        )
                        db.session.add(new_chunk)
                        
                        # Create conversation for chunk
                        conversation_data = {
                            'user_id': user_id,
                            'message': chunk_text
                        }
                        
                        conversation_response = requests.post(
                            f'{CONVERSATION_API_URL}/api/convos',
                            json=conversation_data
                        )

                        if conversation_response.status_code == 201:
                            conversation = conversation_response.json()['conversation']
                            conversation_id = conversation['id']
                            
                            # Update content_chunk_id
                            message_data = {
                                'message': chunk_text,
                                'content_chunk_id': new_content.id
                            }
                            
                            requests.put(
                                f'{CONVERSATION_API_URL}/api/convos/{conversation_id}/reply',
                                json=message_data
                            )
                    except Exception as chunk_error:
                        logging.error(f"Error processing chunk {i}: {str(chunk_error)}")
                        continue

                # Update chunk count and commit
                new_content.chunk_count = len(chunks)
                db.session.commit()

                # Clean up the file after processing
                try:
                    os.remove(file_path)
                except Exception as e:
                    logging.warning(f"Failed to remove temporary file {file_path}: {str(e)}")

                # Set success status and results
                preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text
                upload_status[upload_id] = "COMPLETED"
                upload_results[upload_id] = {
                    'message': 'File successfully uploaded and processed',
                    'filename': filename,
                    'preview': preview,
                    'content_id': new_content.id,
                    'chunk_count': len(chunks)
                }
                logging.info(f"Upload completed for ID: {upload_id}")

        except Exception as e:
            logging.exception("Upload processing failed")
            upload_status[upload_id] = "FAILED"
            upload_results[upload_id] = {
                'error': str(e)
            }
            if 'db' in locals():
                db.session.rollback()

    # Start processing in background
    Thread(target=process_upload).start()

    # Return immediately with upload ID
    return jsonify({
        'message': 'Upload accepted for processing',
        'upload_id': upload_id
    }), 202

@app.route('/api/upload_status/<upload_id>', methods=['GET'])
def get_upload_status(upload_id):
    status = upload_status.get(upload_id)
    if not status:
        logging.warning(f"Upload ID not found: {upload_id}")
        return jsonify({'error': 'Upload ID not found'}), 404

    response = {
        'status': status
    }

    if status in ["COMPLETED", "FAILED"]:
        response['result'] = upload_results.get(upload_id)
        
        # Cleanup completed uploads after sending response
        if status == "COMPLETED":
            upload_status.pop(upload_id, None)
            upload_results.pop(upload_id, None)

    return jsonify(response), 200

@app.route('/api/files', methods=['GET'])
def list_files():
    user_id = request.args.get('user_id')
    
    if not user_id:
        logging.warning("user_id is required")
        return jsonify({'error': 'user_id is required'}), 400
        
    try:
        user_id = int(user_id)
    except ValueError:
        logging.warning("user_id must be an integer")
        return jsonify({'error': 'user_id must be an integer'}), 400
        
    try:
        # Query the content table for files belonging to the user
        user_files = Content.query.filter_by(user_id=user_id).order_by(Content.upload_date.desc()).all()
        
        files_data = [{
            'id': file.id,
            'file_name': file.file_name,
            'file_type': file.file_type,
            'upload_date': file.upload_date,
            'file_size': file.file_size,
            'chunk_count': file.chunk_count,
            'custom_prompt': file.custom_prompt
        } for file in user_files]
        
        return jsonify({
            'files': files_data,
            'count': len(files_data)
        }), 200
        
    except Exception as e:
        logging.error(f"Error fetching files: {e}")
        return jsonify({'error': 'Failed to fetch files'}), 500

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=C_PORT)
