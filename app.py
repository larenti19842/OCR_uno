import os
import base64
import json
import io
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from PIL import Image, ImageEnhance, ImageFilter

app = Flask(__name__, static_folder='.')
CORS(app)

OLLAMA_API_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "ministral-facturador-full"

def optimize_image(image_bytes):
    """
    Optimiza la imagen para OCR preservando tamaño bajo:
    - Ancho máximo 800px (mejor legibilidad)
    - Escala de grises
    - Reducción de ruido (Median Filter)
    - Normalización de brillo
    - Contraste mejorado
    - Unsharp Mask para bordes de texto nítidos
    - Formato JPEG calidad 85% (balance calidad/tamaño)
    """
    img = Image.open(io.BytesIO(image_bytes))
    
    # 1. Convertir a RGB si es necesario (para manejar PNGs con alpha)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    # 2. Redimensionar (Max width 800px para mejor legibilidad)
    max_width = 800
    if img.width > max_width:
        ratio = max_width / float(img.width)
        new_height = int(float(img.height) * float(ratio))
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    # 3. Escala de grises
    img = img.convert('L')
    
    # 4. Reducción de ruido (Median Filter - preserva bordes)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    
    # 5. Normalización de brillo (auto-levels)
    min_val = min(img.getdata())
    max_val = max(img.getdata())
    if max_val > min_val:
        scale = 255.0 / (max_val - min_val)
        offset = -min_val * scale
        img = img.point(lambda x: int(x * scale + offset))
    
    # 6. Contraste mejorado (+20%)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.20)
    
    # 7. Unsharp Mask (mejor que SHARPEN para texto)
    # radius=2, percent=150, threshold=3
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    
    # 8. Guardar como JPEG calidad 85% (buen balance)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
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
        
        # Prompt mejorado para Ollama
        prompt = """
        Eres un experto en extracción de datos de facturas. Analiza la imagen y extrae la información en un JSON estricto.
        
        INSTRUCCIONES CRÍTICAS:
        1. Los números entre paréntesis como '(21.00)' suelen indicar el porcentaje de IVA y NO deben confundirse con la cantidad. 
        2. Si la cantidad no es explícita, asume 1 por defecto.
        3. El 'subtotal' de cada ítem suele ser el valor a la derecha de la línea.
        4. Identifica correctamente los nombres de productos (ej. EMPANADA CAPRESE, EMPANADA ATN, etc).
        5. Extrae los totales finales del pie de la factura.
        
        FORMATO DE SALIDA (JSON):
        {
          "items": [
            {"descripcion": "Nombre del producto", "cantidad": 1, "precio_unitario": 10.0, "subtotal": 10.0}
          ],
          "subtotal_total": 0.0,
          "otros_tributos": 0.0,
          "total_final": 0.0
        }
        """
        
        # Llamar a Ollama
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "images": [base64_image],
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": 1000
            }
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
