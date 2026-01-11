import os
import base64
import json
import io
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from PIL import Image, ImageEnhance

app = Flask(__name__, static_folder='.')
CORS(app)

OLLAMA_API_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "ministral-facturador-full"

def optimize_image(image_bytes):
    """
    Optimiza la imagen según los requerimientos:
    - Ancho máximo 700px (manteniendo proporción)
    - Escala de grises
    - Contraste +15%
    - Formato JPEG calidad 93%
    """
    img = Image.open(io.BytesIO(image_bytes))
    
    # 1. Redimensionar (Max width 700px)
    max_width = 700
    if img.width > max_width:
        ratio = max_width / float(img.width)
        new_height = int(float(img.height) * float(ratio))
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    # 2. Escala de grises
    img = img.convert('L')
    
    # 3. Contraste +15%
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.15)
    
    # 4. Guardar en memoria como JPEG calidad 93%
    buffer = io.BytesIO()
    # JPEG necesita modo RGB o L (escala de grises)
    img.save(buffer, format="JPEG", quality=93)
    return buffer.getvalue()

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/process', methods=['POST'])
def process_invoice():
    if 'file' not in request.files:
        return jsonify({"error": "No hay archivo en la petición"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    try:
        # Leer archivo y optimizar
        image_data = file.read()
        optimized_data = optimize_image(image_data)
        
        # Convertir a Base64 para Ollama
        base64_image = base64.b64encode(optimized_data).decode('utf-8')
        
        # Prompt para Ollama
        prompt = """
        Analiza esta factura y extrae la información en formato JSON estricto.
        Incluye una lista de 'items' con: descripcion, cantidad, precio_unitario, subtotal.
        Incluye totales con: subtotal_total, otros_tributos, total_final.
        Si no encuentras algún campo, déjalo como 0 o string vacío.
        """
        
        # Llamar a Ollama
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "images": [base64_image],
            "format": "json"
        }
        
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=600)
        response.raise_for_status()
        
        result = response.json()
        
        # El modelo devuelve el JSON en el campo 'response'
        output_data = json.loads(result.get("response", "{}"))
        
        return jsonify(output_data)
        
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "No se pudo conectar con Ollama. ¿Está el servidor corriendo?"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
