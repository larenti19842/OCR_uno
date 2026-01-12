import os
import base64
import json
import io
import time
import requests
import re
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

app = Flask(__name__, static_folder='.')
CORS(app)

# Configuración por defecto
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "ministral-facturador-full"

# Modelos populares de OpenRouter para visión
OPENROUTER_MODELS = [
    {"id": "qwen/qwen-2.5-vl-7b-instruct:free", "name": "Qwen 2.5 VL 7B (Free)"},
    {"id": "mistralai/ministral-3b", "name": "Ministral 3B"},
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
    Optimiza la imagen para OCR con alta fidelidad:
    - Autocontrast con cutoff para eliminar sombras leves
    - Especial énfasis en nitidez de bordes
    """
    img = Image.open(io.BytesIO(image_bytes))
    
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    
    # 1. Redimensionar
    max_width = 1024
    if img.width > max_width:
        ratio = max_width / float(img.width)
        new_height = int(float(img.height) * float(ratio))
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    # 2. Convertir a escala de grises
    img = img.convert('L')
    
    # 3. Autocontrast avanzado (elimina ruidos en blancos/negros extremos)
    img = ImageOps.autocontrast(img, cutoff=0.5)
    
    # 4. Aumento de contraste (+40%)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.40)
    
    # 5. Nitidez extrema para OCR
    img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=200, threshold=1))
    
    # 6. Borde protector
    img = ImageOps.expand(img, border=15, fill='white')
    
    # 7. Guardar
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95, optimize=True)
    return buffer.getvalue()

def get_extraction_prompt():
    return """
    Eres un experto contable y de OCR especializado en facturación de ARGENTINA (AFIP). 
    Analiza la imagen y extrae la información en un JSON estricto siguiendo las leyes fiscales locales.

    REGLAS DE ORO (CRÍTICAS):
    1. VALORES NUMÉRICOS PUROS: Bajo ninguna circunstancia incluyas cálculos, sumas o fórmulas matemáticas dentro del JSON. 
       - INCORRECTO: "neto": 100 + 200
       - CORRECTO: "neto": 300.00
    2. JSON ESTRICTO: Solo devuelve el objeto JSON. Sin texto antes ni después.
    3. NO NOMBRES DE VARIABLES: Si el valor es una suma de varios ítems, haz la cuenta tú mismo y entrega solo el número final.

    INSTRUCCIONES FISCALES (ARGENTINA):
    1. COMPROBANTE: Identifica la letra (A, B, C, M, E) y el tipo. Punto de Venta (5 dígitos) y Número (8 dígitos). Extrae el CAE y su vencimiento.
    2. EMISOR/RECEPTOR: Extrae Razon Social, CUIT (con guiones), Condición de IVA y Domicilio.
    3. ÍTEMS: Extrae Cantidad, Descripción, Precio Unitario, Alícuota de IVA (21, 10.5, 27, 0) y Subtotal.
    4. IMPUESTOS (DESGLOSE): 
       - Subtotal Neto Gravado (separado por alícuota 21%, 10.5%, etc).
       - IVA Liquidado (separado por alícuota).
       - Percepciones de IVA, IIBB, Impuestos Internos y Otros Tributos.
    5. TOTALES: Subtotal neto total, Total IVA total, Total Tributos y Importe Total Final.

    FORMATO DE SALIDA (JSON ESTRICTO):
    {
      "comprobante": {
        "tipo": "Factura", "letra": "A", "punto_venta": "", "numero": "",
        "fecha_emision": "", "fecha_vencimiento": "", "cae": "", "vto_cae": "", "condicion_venta": ""
      },
      "emisor": {
        "razon_social": "", "cuit": "", "condicion_iva": "", "domicilio": "", "iibb": ""
      },
      "receptor": {
        "razon_social": "", "cuit": "", "condicion_iva": "", "domicilio": ""
      },
      "items": [
        {"cantidad": 1, "descripcion": "", "precio_unitario": 0.0, "alicuota_iva": 21.0, "subtotal": 0.0}
      ],
      "impuestos": {
        "neto_gravado_21": 0.0, "neto_gravado_10_5": 0.0, "neto_gravado_27": 0.0,
        "iva_21": 0.0, "iva_10_5": 0.0, "iva_27": 0.0,
        "percepcion_iva": 0.0, "percepcion_iibb": 0.0, "otros_tributos": 0.0
      },
      "totales": {
        "subtotal_neto": 0.0, "total_iva": 0.0, "total_tributos": 0.0, "total": 0.0
      }
    }
    """

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/models', methods=['GET'])
def get_models():
    return jsonify(OPENROUTER_MODELS)

@app.route('/api/extract', methods=['POST'])
def api_extract():
    """
    API REST para extracción de datos de facturas.
    
    Params (multipart/form-data):
    - file: Imagen de la factura (required)
    - provider: 'ollama' o 'openrouter' (default: 'ollama')
    - model: Nombre del modelo (default: ministral-facturador-full)
    - api_key: API key de OpenRouter (required si provider=openrouter)
    
    Returns: JSON con los datos extraídos
    """
    if 'file' not in request.files:
        return jsonify({"error": "No hay archivo en la petición"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    provider = request.form.get('provider', 'openrouter')
    api_key = request.form.get('api_key', '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')
    model = request.form.get('model', 'qwen/qwen-2.5-vl-7b-instruct:free')

    try:
        image_bytes = file.read()
        optimized_data = optimize_image(image_bytes)
        processed_b64 = base64.b64encode(optimized_data).decode('utf-8')
        prompt = get_extraction_prompt()
        
        full_response = ""
        
        if provider == "openrouter":
            if not api_key:
                return jsonify({"error": "Se requiere api_key para OpenRouter"}), 400
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Invoice OCR Pro API"
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{processed_b64}"}}
                ]}],
                "temperature": 0.1,
                "max_tokens": 2000
            }
            response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                full_response = result['choices'][0].get('message', {}).get('content', '')
        else:
            payload = {
                "model": model, "prompt": prompt, "stream": False, "images": [processed_b64], "format": "json",
                "options": {"temperature": 0.1, "num_predict": 2000}
            }
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=600)
            response.raise_for_status()
            result = response.json()
            full_response = result.get('response', '')
        
        # Limpiar y parsear JSON
        json_match = re.search(r'```json\s*({.*})\s*```', full_response, re.DOTALL)
        if not json_match:
            json_match = re.search(r'```\s*({.*})?\s*```', full_response, re.DOTALL)
        
        clean_json = full_response
        if json_match and json_match.group(1):
            clean_json = json_match.group(1)
        else:
            start = full_response.find('{')
            end = full_response.rfind('}')
            if start != -1 and end != -1:
                clean_json = full_response[start:end+1]
        
        # Evaluar expresiones matemáticas
        def eval_math_expr(match):
            expr = match.group(1)
            try:
                if re.match(r'^[\d\s\+\-\*\/\.\(\)]+$', expr):
                    result = eval(expr, {"__builtins__": {}}, {})
                    return str(round(result, 2))
            except:
                pass
            return expr
        
        clean_json = re.sub(r':\s*([\d\.\s\+\-\*\/\(\)]+(?:\s*[\+\-\*\/]\s*[\d\.\s\+\-\*\/\(\)]+)+)', 
                           lambda m: ': ' + eval_math_expr(m), clean_json)
        
        output_data = json.loads(clean_json.strip())
        return jsonify(output_data)
        
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Respuesta malformada: {str(e)}", "raw": full_response}), 422
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error de conexión: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/process', methods=['POST'])
def process_invoice():
    if 'file' not in request.files:
        return jsonify({"error": "No hay archivo en la petición"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

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
            processed_b64 = base64.b64encode(optimized_data).decode('utf-8')
            
            elapsed = round(time.time() - start_time, 1)
            yield f"data: {json.dumps({'phase': 'optimized', 'message': f'Imagen optimizada ({len(optimized_data)/1024:.1f} KB)', 'elapsed': elapsed})}\n\n"
            
            prompt = get_extraction_prompt()
            provider_name = "OpenRouter" if provider == "openrouter" else "Ollama"
            yield f"data: {json.dumps({'phase': 'sending', 'message': f'Enviando a {provider_name}...', 'elapsed': round(time.time() - start_time, 1)})}\n\n"
            
            full_response = ""
            token_count = 0
            
            if provider == "openrouter":
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
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{processed_b64}"}}
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
                            if data_str.strip() == '[DONE]': break
                            try:
                                chunk = json.loads(data_str)
                                if 'choices' in chunk and len(chunk['choices']) > 0:
                                    content = chunk['choices'][0].get('delta', {}).get('content', '')
                                    if content:
                                        full_response += content
                                        token_count += 1
                                        current_time = time.time()
                                        if current_time - last_update >= 0.5:
                                            elapsed = round(current_time - start_time, 1)
                                            tokens_per_sec = round(token_count / elapsed, 1) if elapsed > 0 else 0
                                            yield f"data: {json.dumps({'phase': 'generating', 'message': 'Generando respuesta...', 'tokens': token_count, 'tokens_per_sec': tokens_per_sec, 'elapsed': elapsed})}\n\n"
                                            last_update = current_time
                            except json.JSONDecodeError: continue
            else:
                payload = {
                    "model": model, "prompt": prompt, "stream": True, "images": [processed_b64], "format": "json",
                    "options": {"temperature": 0.1, "num_predict": 1000}
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
                            if chunk.get('done', False): break
                        except json.JSONDecodeError: continue
            
            elapsed = round(time.time() - start_time, 1)
            # Intentar extraer JSON de la respuesta de forma robusta
            try:
                # 1. Buscar bloques de código markdown ```json ... ```
                json_match = re.search(r'```json\s*({.*})\s*```', full_response, re.DOTALL)
                if not json_match:
                    # 2. Buscar cualquier bloque de código ``` ... ```
                    json_match = re.search(r'```\s*({.*})?\s*```', full_response, re.DOTALL)
                
                clean_json = full_response
                if json_match and json_match.group(1):
                    clean_json = json_match.group(1)
                else:
                    # 3. Buscar el primer '{' y el último '}'
                    start = full_response.find('{')
                    end = full_response.rfind('}')
                    if start != -1 and end != -1:
                        clean_json = full_response[start:end+1]
                
                # 4. FIX: Evaluar expresiones matemáticas en valores numéricos
                # Algunos modelos escriben "neto": 100 + 200 en lugar de "neto": 300
                def eval_math_expr(match):
                    expr = match.group(1)
                    try:
                        # Solo permitimos: números, +, -, *, /, (, ), espacios y .
                        if re.match(r'^[\d\s\+\-\*\/\.\(\)]+$', expr):
                            result = eval(expr, {"__builtins__": {}}, {})
                            return str(round(result, 2))
                    except:
                        pass
                    return expr
                
                # Buscar patrones como: 1234.00 + 5678.00 * 0.21
                clean_json = re.sub(r':\s*([\d\.\s\+\-\*\/\(\)]+(?:\s*[\+\-\*\/]\s*[\d\.\s\+\-\*\/\(\)]+)+)', 
                                   lambda m: ': ' + eval_math_expr(m), clean_json)
                
                clean_json = clean_json.strip()
                output_data = json.loads(clean_json)
            except json.JSONDecodeError as e:
                output_data = {"error": f"Respuesta malformada: {str(e)}", "raw": full_response}
            
            yield f"data: {json.dumps({'phase': 'complete', 'message': f'Completado en {elapsed}s', 'tokens': token_count, 'elapsed': elapsed, 'result': output_data, 'processed_image': processed_b64})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'phase': 'error', 'message': str(e)})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
