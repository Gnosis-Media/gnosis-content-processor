from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
import PyPDF2
import docx2txt

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

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
    # Check if a file is present in the request
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400
    
    file = request.files['file']
    
    # If the user does not select a file, the browser submits an empty file without a filename
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Extract text from the file
        extracted_text = extract_text(file_path)
        
        # Here you would typically process the extracted text further
        # For now, we'll just return the first 500 characters
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
    app.run(debug=True, host='0.0.0.0', port=5000)