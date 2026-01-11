import os
import base64
import json
import io
import time
import requests
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
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
    extrema = img.getextrema()
    if extrema:
        min_val, max_val = extrema
        if max_val > min_val:
            scale = 255.0 / (max_val - min_val)
            offset = -min_val * scale
            img = img.point(lambda x: int(x * scale + offset))
    
    # 6. Contraste mejorado (+20%)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.20)
    
    # 7. Unsharp Mask (mejor que SHARPEN para texto)
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

    # Leer el archivo ANTES del generador para evitar error de archivo cerrado
    try:
        image_bytes = file.read()
    except Exception as e:
        return jsonify({"error": f"Error al leer el archivo: {str(e)}"}), 500
        
    def generate():
        start_time = time.time()
        
        try:
            # Fase 1: Optimización de imagen
            yield f"data: {json.dumps({'phase': 'optimizing', 'message': 'Optimizando imagen...', 'elapsed': 0})}\n\n"
            
            optimized_data = optimize_image(image_bytes)
            image_size_kb = len(optimized_data) / 1024
            
            elapsed = round(time.time() - start_time, 1)
            yield f"data: {json.dumps({'phase': 'optimized', 'message': f'Imagen optimizada ({image_size_kb:.1f} KB)', 'elapsed': elapsed})}\n\n"
            
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
            
            # Fase 2: Enviando a Ollama (modo streaming)
            yield f"data: {json.dumps({'phase': 'sending', 'message': 'Enviando a Ollama...', 'elapsed': round(time.time() - start_time, 1)})}\n\n"
            
            payload = {
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": True,  # Habilitamos streaming
                "images": [base64_image],
                "format": "json",
                "options": {
                    "temperature": 0.1,
                    "num_predict": 1000
                }
            }
            
            # Fase 3: Procesando con IA
            response = requests.post(OLLAMA_API_URL, json=payload, stream=True, timeout=600)
            response.raise_for_status()
            
            full_response = ""
            token_count = 0
            last_update = time.time()
            
            for line in response.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        if 'response' in chunk:
                            full_response += chunk['response']
                            token_count += 1
                            
                            # Actualizar progreso cada 0.5 segundos
                            current_time = time.time()
                            if current_time - last_update >= 0.5:
                                elapsed = round(current_time - start_time, 1)
                                tokens_per_sec = round(token_count / elapsed, 1) if elapsed > 0 else 0
                                yield f"data: {json.dumps({'phase': 'generating', 'message': f'Generando respuesta...', 'tokens': token_count, 'tokens_per_sec': tokens_per_sec, 'elapsed': elapsed})}\n\n"
                                last_update = current_time
                        
                        if chunk.get('done', False):
                            break
                    except json.JSONDecodeError:
                        continue
            
            # Fase 4: Completado
            elapsed = round(time.time() - start_time, 1)
            
            try:
                output_data = json.loads(full_response)
            except json.JSONDecodeError:
                output_data = {"error": "No se pudo parsear la respuesta del modelo", "raw": full_response}
            
            yield f"data: {json.dumps({'phase': 'complete', 'message': f'Completado en {elapsed}s', 'tokens': token_count, 'elapsed': elapsed, 'result': output_data})}\n\n"
            
        except requests.exceptions.ConnectionError:
            yield f"data: {json.dumps({'phase': 'error', 'message': 'No se pudo conectar con Ollama. ¿Está el servidor corriendo?'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'phase': 'error', 'message': str(e)})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
