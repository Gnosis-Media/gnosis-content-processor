import uuid
import random
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
from secrets_manager import get_service_secrets
import json
import boto3
from flask_restx import Api, Resource, fields

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# CORS
app = Flask(__name__)
CORS(app)

# Initialize Flask-RESTX
api = Api(app,
    version='1.0',
    title='Gnosis Content Processor API',
    description='API for processing and managing content files',
    doc='/docs'
)

ns = api.namespace('api', description='Content processing operations')

# Track upload status and results
upload_status = {}
upload_results = {}

secrets = get_service_secrets('gnosis-content-processor')

C_PORT = int(secrets.get('PORT', 5000))

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

SQLALCHEMY_DATABASE_URI = (
    f"mysql+pymysql://{secrets['MYSQL_USER']}:{secrets['MYSQL_PASSWORD_CONTENT']}"
    f"@{secrets['MYSQL_HOST']}:{secrets['MYSQL_PORT']}/{secrets['MYSQL_DATABASE']}"
)
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# CHANGE THIS TO CLOUD URL FOR PRODUCTION
CONVERSATION_API_URL = secrets.get('CONVERSATION_API_URL', 'http://localhost:5000')
EMBEDDING_API_URL = secrets.get('EMBEDDING_API_URL', 'http://localhost:5008')
METADATA_API_URL = secrets.get('METADATA_API_URL', 'http://localhost:5010')
PROFILES_API_URL = secrets.get('PROFILES_API_URL', 'http://localhost:5011')
INFLUENCER_API_URL = secrets.get('INFLUENCER_API_URL', 'http://localhost:5012')
USERS_API_URL = secrets.get('USERS_API_URL', 'http://localhost:5007')
API_KEY = secrets.get('API_KEY')

lambda_client = boto3.client(
    'lambda',
    region_name='us-east-1',  # replace with your AWS region
    aws_access_key_id=secrets.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=secrets.get('AWS_SECRET_ACCESS_KEY')
)

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
    # metadata
    title = db.Column(db.String(255))
    author = db.Column(db.String(255))
    publication_date = db.Column(db.Date)
    publisher = db.Column(db.String(255))
    source_language = db.Column(db.String(255))
    genre = db.Column(db.String(255))
    topic = db.Column(db.Text)

class ContentChunk(db.Model):
    __tablename__ = 'content_chunk'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    content_id = db.Column(db.Integer, db.ForeignKey('content.id'))
    chunk_order = db.Column(db.Integer, nullable=False)
    chunk_text = db.Column(db.Text, nullable=False)
    embedding_id = db.Column(db.Integer)

def allowed_file(filename):
    logging.debug(f"Checking if file '{filename}' is allowed.")
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(file_path):
    logging.info(f"Extracting text from file: {file_path}")
    _, file_extension = os.path.splitext(file_path)
    if file_extension == '.pdf':
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
        logging.info(f"Extracted text from PDF: {file_path}")
        return text
    elif file_extension in ['.doc', '.docx']:
        text = docx2txt.process(file_path)
        logging.info(f"Extracted text from DOCX: {file_path}")
        return text
    elif file_extension == '.txt':
        with open(file_path, 'r') as file:
            text = file.read()
        logging.info(f"Extracted text from TXT: {file_path}")
        return text
    else:
        logging.error(f"Unsupported file type: {file_extension}")
        return "Unsupported file type"
    
def add_links(response_data, endpoint, **params):
    """Add HATEOAS links to response"""
    response_data['_links'] = {
        'self': f"/api/{endpoint}"
    }
    
    # Add contextual links based on endpoint
    if endpoint == 'upload':
        if 'upload_id' in response_data:
            response_data['_links']['status'] = f"/api/upload_status/{response_data['upload_id']}"
            logging.debug(f"Added status link for upload ID: {response_data['upload_id']}")
    
    elif endpoint == 'upload_status':
        if params.get('user_id'):
            response_data['_links']['files'] = f"/api/files?user_id={params['user_id']}"
            logging.debug(f"Added files link for user ID: {params['user_id']}")
    
    elif endpoint == 'files':
        response_data['_links']['upload'] = "/api/upload"
        logging.debug("Added upload link.")

    return response_data

def validate_metadata(metadata):
    """Validate metadata fields"""
    required_fields = [
        'title', 'author', 'publication_date', 
        'publisher', 'source_language', 'genre', 'topic'
    ]
    
    for field in required_fields:
        if field not in metadata:
            logging.warning(f"Missing field in metadata: {field}")
            metadata[field] = None
    
    # Validate date format if not Unknown
    if metadata['publication_date'] != "Unknown":
        try:
            datetime.strptime(metadata['publication_date'], '%Y-%m-%d')
            logging.debug(f"Validated publication date: {metadata['publication_date']}")
        except ValueError:
            logging.warning("Invalid publication date format")
            metadata['publication_date'] = None
    else:
        metadata['publication_date'] = None
    
    return metadata

def create_conversation(user_id, content_id, content_chunk_id, correlation_id=None):
    """Helper function to create conversation in a separate thread"""
    try:
        conversation_data = {
            'user_id': user_id,
            'content_id': content_id,
            'content_chunk_id': content_chunk_id
        }
        
        headers = {'X-API-KEY': API_KEY}
        if correlation_id:
            headers['X-Correlation-ID'] = correlation_id
            
        logging.info(f"Creating conversation for user_id: {user_id}, content_id: {content_id}, chunk_id: {content_chunk_id}")
        conversation_response = requests.post(
            f'{CONVERSATION_API_URL}/api/convos',
            json=conversation_data,
            headers=headers
        )

        if conversation_response.status_code == 201 or conversation_response.status_code == 200:
            logging.info(f"Conversation created successfully with ID: {conversation_response.json().get('conversation_id')}")
        else:
            logging.error(f"Failed to create conversation: {conversation_response.status_code} {conversation_response.text}")                            
    except Exception as e:
        logging.error(f"Error creating conversation: {str(e)}")

def process_chunks(chunks, new_content, user_id, random_chunks_ids, correlation_id=None):
    """Process chunks in a separate thread"""
    try:
        with app.app_context():
            logging.info(f"Processing {len(chunks)} chunks for content ID: {new_content.id}")
            for i, chunk_text in enumerate(chunks):
                try:
                    # Post embedding get the embedding id
                    headers = {'X-API-KEY': API_KEY}
                    if correlation_id:
                        headers['X-Correlation-ID'] = correlation_id
                        
                    logging.debug(f"Sending chunk {i} for embedding.")
                    embedding_response = requests.post(
                        f'{EMBEDDING_API_URL}/api/embedding',
                        json={'text': chunk_text},
                        headers=headers
                    )

                    if embedding_response.status_code == 202:
                        embedding_id = embedding_response.json().get('id')
                        logging.info(f"Embedding created for chunk {i} with ID: {embedding_id}")
                    else:
                        logging.error(f"Failed to create embedding for chunk {i}: {embedding_response.status_code} {embedding_response.text}")
                        embedding_id = None

                    # Save chunk
                    new_chunk = ContentChunk(
                        content_id=new_content.id,
                        chunk_order=i,
                        chunk_text=chunk_text,
                        embedding_id=embedding_id
                    )
                    db.session.add(new_chunk)
                    db.session.flush()
                    db.session.commit()
                    logging.info(f"Chunk created with ID: {new_chunk.id}")

                    if i in random_chunks_ids:
                        Thread(
                            target=create_conversation,
                            args=(user_id, new_content.id, new_chunk.id, correlation_id)
                        ).start()
                        logging.info(f"Started conversation creation thread for chunk {new_chunk.id}")

                except Exception as chunk_error:
                    logging.error(f"Error processing chunk {i}: {str(chunk_error)}")
                    continue

            # Update chunk count and commit
            new_content.chunk_count = len(chunks)
            db.session.commit()
            logging.info(f"Completed processing all chunks for content ID: {new_content.id}")

    except Exception as e:
        logging.error(f"Error in chunk processing thread: {str(e)}")

# Model definitions for request/response
upload_response = api.model('UploadResponse', {
    'message': fields.String,
    'upload_id': fields.String,
    '_links': fields.Raw
})

status_response = api.model('StatusResponse', {
    'status': fields.String,
    'result': fields.Raw,
    '_links': fields.Raw
})

content_ids_response = api.model('ContentIdsResponse', {
    'content_ids': fields.List(fields.Integer)
})

chunks_response = api.model('ChunksResponse', {
    'content_id': fields.Integer,
    'chunks': fields.List(fields.Raw)
})

@ns.route('/upload')
class UploadResource(Resource):
    @api.doc('upload_file')
    @api.response(202, 'Upload accepted')
    @api.response(400, 'Invalid request')
    @api.marshal_with(upload_response)
    def post(self):
        correlation_id = request.headers.get('X-Correlation-ID')
        
        if 'file' not in request.files:
            api.abort(400, 'No file part in the request')

        file = request.files['file']
        user_id = request.form.get('user_id')
        custom_prompt = request.form.get('custom_prompt', '')

        logging.info(f"Uploading file for user_id: {user_id}")

        if not user_id:
            api.abort(400, 'user_id is required')

        try:
            user_id = int(user_id)
            logging.debug(f"Parsed user_id: {user_id}")
        except ValueError:
            api.abort(400, 'user_id must be an integer')

        if file.filename == '':
            api.abort(400, 'No selected file')

        if not allowed_file(file.filename):
            api.abort(400, 'File type not allowed')

        # Save the file content before starting the thread
        file_content = file.read()
        content_type = file.content_type or 'text/plain'
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
                    logging.debug(f"File will be saved to: {file_path}")
                    
                    # Write the saved content to file
                    with open(file_path, 'wb') as f:
                        f.write(file_content)
                        logging.info(f"File written to disk: {file_path}")

                    # Extract text from file
                    extracted_text = extract_text(file_path)

                    # Get metadata
                    headers = {'X-API-KEY': API_KEY}
                    if correlation_id:
                        headers['X-Correlation-ID'] = correlation_id
                        
                    logging.info(f"Requesting metadata extraction for file: {filename}")
                    metadata_response = requests.post(
                        f'{METADATA_API_URL}/api/metadata/extract',
                        json={'text': extracted_text[:3000], 'file_name': filename, 'additional_info': custom_prompt},
                        headers=headers
                    )

                    if metadata_response.status_code == 200 or metadata_response.status_code == 201:
                        metadata = metadata_response.json().get('metadata')
                        logging.info(f"Metadata extracted: {metadata}")
                        # validate metadata
                        metadata = validate_metadata(metadata)
                    else:
                        logging.error(f"Failed to get metadata: {metadata_response.status_code} {metadata_response.text}")
                        metadata = {}
                    
                    # Save content to database
                    new_content = Content(
                        user_id=user_id,
                        file_name=filename,
                        file_type=content_type,
                        file_size=len(file_content),
                        s3_key=file_path,
                        custom_prompt=custom_prompt,
                        title=metadata.get('title', None),
                        author=metadata.get('author', None),
                        publication_date=metadata.get('publication_date', None),
                        publisher=metadata.get('publisher', None),
                        source_language=metadata.get('source_language', None),
                        genre=metadata.get('genre', None),
                        topic=metadata.get('topic', None)
                    )
                    db.session.add(new_content)
                    db.session.flush()  # Get the content ID
                    db.session.commit()
                    logging.info(f"Content saved to database with ID: {new_content.id}")

                    # Call the profiles API to create an AI profile for the content
                    profile_response = requests.post(
                        f'{PROFILES_API_URL}/api/ais',
                        json={'content_id': new_content.id},
                        headers=headers
                    )

                    if profile_response.status_code == 201 or profile_response.status_code == 200:
                        logging.info(f"Profile created successfully for content ID: {new_content.id}")
                    else:
                        logging.error(f"Failed to create profile: {profile_response.status_code} {profile_response.text}")

                    # Process chunks
                    chunk_size = 1500
                    chunks = [extracted_text[i:i+chunk_size] for i in range(0, len(extracted_text), chunk_size)]
                    logging.info(f"Chunked extracted text into {len(chunks)} chunks.")
                                    
                    num_chunks_to_sample = min(9, len(chunks))  # Ensure we don't sample more than available
                    random_chunks_ids = random.sample(range(len(chunks)), num_chunks_to_sample)
                    # Make sure that one of the random chunks is early in the index first 10% of the length
                    random_chunks_ids.append(random.randint(0, int(len(chunks) * 0.1)))
                    # remove duplicates
                    random_chunks_ids = list(set(random_chunks_ids))

                    Thread(
                        target=process_chunks,
                        args=(chunks, new_content, user_id, random_chunks_ids, correlation_id)
                    ).start()
                    logging.info(f"Started chunk processing thread for content ID: {new_content.id}")            
                        
                    # Clean up the file after processing
                    try:
                        os.remove(file_path)
                        logging.info(f"Temporary file removed: {file_path}")
                    except Exception as e:
                        logging.warning(f"Failed to remove temporary file {file_path}: {str(e)}")

                    # Set success status and results
                    # Set the preview to be profile info and first 100 characters of the content
                    preview = f"{metadata.get('title', '')} by {metadata.get('author', '')} - {chunks[0][:100]}..."
                    upload_results[upload_id] = {
                        'message': 'File successfully uploaded and processed',
                        'filename': filename,
                        'preview': preview,
                        'content_id': new_content.id,
                        'chunk_count': len(chunks)
                    }
                    upload_status[upload_id] = "COMPLETED"                    
                    logging.info(f"Upload completed for ID: {upload_id}")

                    # Construct the URL for the GET request
                    url = f'{USERS_API_URL}/api/users/{user_id}/email'

                    # Make the GET request
                    response = requests.get(url, headers=headers)

                    # Initialize Default Email
                    email = "nchimicles@gmail.com"
                    use_user_email = False

                    # Check if the request was successful
                    if use_user_email:
                        if response.status_code == 200:
                            data = response.json()  # Parse the JSON response
                            email = data.get('email', 'No email found')
                            logging.info(f"Email for user ID {user_id}: {email}")
                        else:
                            email = "nchimicles@gmail.com"
                            logging.warning(f"Failed to fetch email for user ID {user_id}. Status code: {response.status_code}")
                            try:
                                error_data = response.json()
                                logging.warning("Error response:", error_data)
                            except json.JSONDecodeError:
                                logging.warning("Response text:", response.text)

                    # Prepare the event payload with the dynamic email address
                    event = {
                        "email": email  # Destination email address
                    }

                    # Convert the event payload to a JSON string
                    json_payload = json.dumps(event)

                    # Invoke the Lambda function
                    logging.info(f"Invoking Lambda function to send email to: {email}")
                    response = lambda_client.invoke(
                        FunctionName='sendEmailtoUser',  # The Lambda function name
                        InvocationType='RequestResponse',  # Synchronous invocation (wait for result)
                        Payload=json_payload  # Pass the event data as Payload
                    )

                    # Parse and print the response from Lambda
                    response_payload = json.loads(response['Payload'].read().decode('utf-8'))
                    logging.info("Response from Lambda:", response_payload)

            except Exception as e:
                logging.exception("Upload processing failed")
                upload_status[upload_id] = "FAILED"
                upload_results[upload_id] = {
                    'error': str(e)
                }
                if 'db' in locals():
                    db.session.rollback()
                    logging.error("Database session rolled back due to error.")

        # Start processing in background
        Thread(target=process_upload).start()

        # Return immediately with upload ID
        response_data = {
            'message': 'Upload accepted for processing',
            'upload_id': upload_id
        }
        
        return add_links(response_data, 'upload'), 202

@ns.route('/upload_status/<string:upload_id>')
class UploadStatusResource(Resource):
    @api.doc('get_upload_status')
    @api.response(200, 'Success')
    @api.response(404, 'Upload ID not found')
    @api.marshal_with(status_response)
    def get(self, upload_id):
        status = upload_status.get(upload_id)
        if not status:
            logging.warning(f"Upload ID not found: {upload_id}")
            api.abort(404, 'Upload ID not found')

        response_data = {
            'status': status
        }

        if status in ["COMPLETED", "FAILED"]:
            response_data['result'] = upload_results.get(upload_id)
            
            # Cleanup completed uploads after sending response
            if status == "COMPLETED":
                upload_status.pop(upload_id, None)
                upload_results.pop(upload_id, None)
                logging.info(f"Cleaned up upload status and results for ID: {upload_id}")

        return add_links(response_data, 'upload_status', user_id=upload_results.get(upload_id, {}).get('user_id')), 200

@ns.route('/content_ids')
class ContentIdsResource(Resource):
    @api.doc('get_content_ids')
    @api.response(200, 'Success')
    @api.response(400, 'Missing user_id')
    @api.response(500, 'Server error')
    @api.marshal_with(content_ids_response)
    def get(self):
        user_id = request.args.get('user_id')
        if not user_id:
            logging.warning("Missing user_id in request.")
            api.abort(400, 'user_id is required')
            
        try:
            content_ids = [content.id for content in Content.query.filter_by(user_id=user_id).all()]
            logging.info(f"Fetched content IDs for user_id {user_id}: {content_ids}")
            return {'content_ids': content_ids}, 200
        except Exception as e:
            logging.error(f'Failed to get content IDs: {str(e)}')
            api.abort(500, f'Failed to get content IDs: {str(e)}')

@ns.route('/content/<int:content_id>/chunks')
class ContentChunksResource(Resource):
    @api.doc('get_content_chunks')
    @api.response(200, 'Success')
    @api.response(404, 'No chunks found')
    @api.response(500, 'Server error')
    @api.marshal_with(chunks_response)
    def get(self, content_id):
        try:
            chunks = ContentChunk.query.filter_by(content_id=content_id).all()
            
            if not chunks:
                logging.warning(f"No chunks found for content ID: {content_id}")
                api.abort(404, 'No chunks found')

            chunks_data = [{
                'id': chunk.id,
                'chunk_order': chunk.chunk_order,
                'embedding_id': chunk.embedding_id
            } for chunk in chunks]

            logging.info(f"Fetched {len(chunks)} chunks for content ID: {content_id}")
            return {
                "content_id": content_id,
                "chunks": chunks_data
            }, 200

        except Exception as e:
            logging.error(f"Failed to fetch chunks for content ID {content_id}: {str(e)}")
            api.abort(500, f'Failed to fetch chunks: {str(e)}')

@app.before_request
def log_request_info():
    # Exempt the /docs endpoint from logging and API key checks
    if request.path.startswith('/docs') or request.path.startswith('/swagger'):
        return
        
    logging.info(f"Headers: {request.headers}")
    logging.info(f"Body: {request.get_data()}")

    if 'X-API-KEY' not in request.headers:
        logging.warning("No X-API-KEY in request headers.")
        return jsonify({'error': 'No X-API-KEY'}), 401
    
    x_api_key = request.headers.get('X-API-KEY')
    if x_api_key != API_KEY:
        logging.warning("Invalid X-API-KEY provided.")
        return jsonify({'error': 'Invalid X-API-KEY'}), 401
    else:
        logging.info("Valid X-API-KEY provided.")
        return

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=C_PORT)
