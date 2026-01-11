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

# Configuración por defecto
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "ministral-facturador-full"

# Modelos populares de OpenRouter para visión
OPENROUTER_MODELS = [
    {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash"},
    {"id": "google/gemini-pro-vision", "name": "Gemini Pro Vision"},
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet"},
    {"id": "anthropic/claude-3-haiku", "name": "Claude 3 Haiku"},
    {"id": "openai/gpt-4o", "name": "GPT-4o"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini"},
    {"id": "meta-llama/llama-3.2-90b-vision-instruct", "name": "Llama 3.2 90B Vision"},
    {"id": "qwen/qwen2.5-vl-72b-instruct", "name": "Qwen 2.5 VL 72B"},
]

def optimize_image(image_bytes):
    """
    Optimiza la imagen para OCR preservando tamaño bajo.
    """
    img = Image.open(io.BytesIO(image_bytes))
    
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    max_width = 800
    if img.width > max_width:
        ratio = max_width / float(img.width)
        new_height = int(float(img.height) * float(ratio))
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    img = img.convert('L')
    img = img.filter(ImageFilter.MedianFilter(size=3))
    
    extrema = img.getextrema()
    if extrema:
        min_val, max_val = extrema
        if max_val > min_val:
            scale = 255.0 / (max_val - min_val)
            offset = -min_val * scale
            img = img.point(lambda x: int(x * scale + offset))
    
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.20)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()

def get_extraction_prompt():
    return """
    Eres un experto en extracción de datos de facturas y OCR. Analiza la imagen y extrae la información en un JSON estricto.
    
    PROHIBICIONES CRÍTICAS:
    - NO incluyas NINGUNA nota, explicación, razonamiento o texto fuera del JSON.
    - NO incluyas metadatos o comentarios dentro del JSON.
    - El resultado debe ser EXCLUSIVAMENTE el objeto JSON.
    
    INSTRUCCIONES DE EXTRACCIÓN:
    1. CABECERA: Extrae el CUIT del emisor, punto de venta, número de factura y fecha.
    2. ÍTEMS: 
       - Los números en paréntesis como '(21.00)' son porcentajes de IVA (21%), NO los uses como cantidad. 
       - Si la cantidad no es clara, asume 1.
       - El precio unitario es el valor individual.
       - El subtotal es el producto Cantidad x Precio o el valor a la derecha.
    3. TOTALES: Extrae el Subtotal neto (antes de impuestos), otros tributos/tasas y el Total Final.
    
    FORMATO DE SALIDA (JSON ESTRICTO):
    {
      "cabecera": {
        "cuit_emisor": "",
        "punto_venta": "",
        "numero_factura": "",
        "fecha": ""
      },
      "items": [
        {"descripcion": "", "cantidad": 1, "precio_unitario": 0.0, "subtotal": 0.0}
      ],
      "totales": {
        "subtotal": 0.0,
        "otros_tributos": 0.0,
        "total_final": 0.0
      }
    }
    """

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/models', methods=['GET'])
def get_models():
    """Retorna la lista de modelos disponibles para OpenRouter"""
    return jsonify(OPENROUTER_MODELS)

@app.route('/process', methods=['POST'])
def process_invoice():
    if 'file' not in request.files:
        return jsonify({"error": "No hay archivo en la petición"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    # Obtener configuración del proveedor
    provider = request.form.get('provider', 'ollama')
    api_key = request.form.get('api_key', '')
    model = request.form.get('model', DEFAULT_OLLAMA_MODEL)

    try:
        image_bytes = file.read()
    except Exception as e:
        return jsonify({"error": f"Error al leer el archivo: {str(e)}"}), 500
        
    def generate():
        start_time = time.time()
        
        try:
            yield f"data: {json.dumps({'phase': 'optimizing', 'message': 'Optimizando imagen...', 'elapsed': 0})}\n\n"
            
            optimized_data = optimize_image(image_bytes)
            image_size_kb = len(optimized_data) / 1024
            
            elapsed = round(time.time() - start_time, 1)
            yield f"data: {json.dumps({'phase': 'optimized', 'message': f'Imagen optimizada ({image_size_kb:.1f} KB)', 'elapsed': elapsed})}\n\n"
            
            base64_image = base64.b64encode(optimized_data).decode('utf-8')
            prompt = get_extraction_prompt()
            
            provider_name = "OpenRouter" if provider == "openrouter" else "Ollama"
            yield f"data: {json.dumps({'phase': 'sending', 'message': f'Enviando a {provider_name}...', 'elapsed': round(time.time() - start_time, 1)})}\n\n"
            
            full_response = ""
            token_count = 0
            
            if provider == "openrouter":
                # Llamar a OpenRouter
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "Invoice OCR Pro"
                }
                
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                            ]
                        }
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "stream": True
                }
                
                response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, stream=True, timeout=120)
                response.raise_for_status()
                
                last_update = time.time()
                
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode('utf-8')
                        if line_str.startswith('data: '):
                            data_str = line_str[6:]
                            if data_str.strip() == '[DONE]':
                                break
                            try:
                                chunk = json.loads(data_str)
                                if 'choices' in chunk and len(chunk['choices']) > 0:
                                    delta = chunk['choices'][0].get('delta', {})
                                    content = delta.get('content', '')
                                    if content:
                                        full_response += content
                                        token_count += 1
                                        
                                        current_time = time.time()
                                        if current_time - last_update >= 0.5:
                                            elapsed = round(current_time - start_time, 1)
                                            tokens_per_sec = round(token_count / elapsed, 1) if elapsed > 0 else 0
                                            yield f"data: {json.dumps({'phase': 'generating', 'message': 'Generando respuesta...', 'tokens': token_count, 'tokens_per_sec': tokens_per_sec, 'elapsed': elapsed})}\n\n"
                                            last_update = current_time
                            except json.JSONDecodeError:
                                continue
            else:
                # Llamar a Ollama (local)
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": True,
                    "images": [base64_image],
                    "format": "json",
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 1000
                    }
                }
                
                response = requests.post(OLLAMA_API_URL, json=payload, stream=True, timeout=600)
                response.raise_for_status()
                
                last_update = time.time()
                
                for line in response.iter_lines():
                    if line:
                        try:
                            chunk = json.loads(line.decode('utf-8'))
                            if 'response' in chunk:
                                full_response += chunk['response']
                                token_count += 1
                                
                                current_time = time.time()
                                if current_time - last_update >= 0.5:
                                    elapsed = round(current_time - start_time, 1)
                                    tokens_per_sec = round(token_count / elapsed, 1) if elapsed > 0 else 0
                                    yield f"data: {json.dumps({'phase': 'generating', 'message': 'Generando respuesta...', 'tokens': token_count, 'tokens_per_sec': tokens_per_sec, 'elapsed': elapsed})}\n\n"
                                    last_update = current_time
                            
                            if chunk.get('done', False):
                                break
                        except json.JSONDecodeError:
                            continue
            
            # Intentar extraer JSON de la respuesta de forma robusta
            try:
                # 1. Buscar el primer '{' y el último '}'
                import re
                json_match = re.search(r'({.*})', full_response, re.DOTALL)
                if json_match:
                    full_response = json_match.group(1)
                
                # 2. Limpieza básica de comentarios o texto extra si el regex fue muy amplio
                # Intentamos parsear. Si falla, es que el modelo metió texto dentro.
                output_data = json.loads(full_response)
            except json.JSONDecodeError:
                # Si falla, último intento: limpiar líneas que no parezcan JSON
                lines = full_response.split('\n')
                cleaned_lines = [l for l in lines if any(c in l for c in '{}:",[]0123456789')]
                try:
                    output_data = json.loads("".join(cleaned_lines))
                except:
                    output_data = {"error": "Respuesta malformada", "raw": full_response}
            
            yield f"data: {json.dumps({'phase': 'complete', 'message': f'Completado en {elapsed}s', 'tokens': token_count, 'elapsed': elapsed, 'result': output_data})}\n\n"
            
        except requests.exceptions.ConnectionError:
            error_msg = "No se pudo conectar con OpenRouter" if provider == "openrouter" else "No se pudo conectar con Ollama. ¿Está el servidor corriendo?"
            yield f"data: {json.dumps({'phase': 'error', 'message': error_msg})}\n\n"
        except requests.exceptions.HTTPError as e:
            yield f"data: {json.dumps({'phase': 'error', 'message': f'Error HTTP: {str(e)}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'phase': 'error', 'message': str(e)})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
