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

# Configuración por defecto y archivos
CONFIG_FILE = "config.json"
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

def load_config():
    # Prioridad: Variables de entorno (Dokploy/Docker) > config.json > defaults
    env_config = {
        "provider": os.environ.get("OCR_PROVIDER"),
        "api_key": os.environ.get("OPENROUTER_API_KEY"),
        "model_openrouter": os.environ.get("OCR_MODEL"),
        "model_ollama": os.environ.get("OLLAMA_MODEL")
    }
    
    file_config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                file_config = json.load(f)
        except: pass
        
    default_config = {
        "provider": "openrouter",
        "api_key": "",
        "model_openrouter": "qwen/qwen-2.5-vl-7b-instruct:free",
        "model_ollama": DEFAULT_OLLAMA_MODEL
    }
    
    # Merge: defaults < file < env (solo valores no-None)
    result = {**default_config, **file_config}
    for k, v in env_config.items():
        if v is not None and v != "":
            result[k] = v
    return result


def save_config(data):
    try:
        current = load_config()
        current.update(data)
        with open(CONFIG_FILE, "w") as f:
            json.dump(current, f, indent=2)
        return True
    except: return False

def optimize_image(image_bytes):
    """
    Optimiza la imagen para OCR con alta fidelidad:
    - Autocontrast con cutoff para eliminar sombras leves
    - Especial énfasis en nitidez de bordes
    """
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ('RGBA', 'P'): img = img.convert('RGB')
    
    max_width = 1024
    if img.width > max_width:
        ratio = max_width / float(img.width)
        new_height = int(float(img.height) * float(ratio))
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    img = img.convert('L')
    img = ImageOps.autocontrast(img, cutoff=0.5)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.40)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=200, threshold=1))
    img = ImageOps.expand(img, border=15, fill='white')
    
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

@app.route('/health')
def health():
    return jsonify({"status": "ok", "service": "OCR Invoice Pro"})

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/models', methods=['GET'])
def get_models():
    return jsonify(OPENROUTER_MODELS)

@app.route('/api/config', methods=['GET', 'POST'])
def manage_config():
    if request.method == 'POST':
        data = request.json
        if save_config(data):
            return jsonify({"status": "success", "message": "Configuración guardada en el servidor"})
        return jsonify({"status": "error", "message": "Error al guardar configuración"}), 500
    else:
        return jsonify(load_config())

@app.route('/api/health/ollama')
def check_ollama():
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=2)
        return jsonify({"connected": response.status_code == 200})
    except Exception as e:
        print(f"Ollama connection error: {e}")
        return jsonify({"connected": False})

@app.route('/api/extract', methods=['POST'])
def api_extract():
    config = load_config()
    
    if 'file' not in request.files:
        return jsonify({"error": "No hay archivo en la petición"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    provider = (request.form.get('provider') or config.get('provider', 'openrouter')).strip().lower()
    api_key = (request.form.get('api_key') or config.get('api_key', '')).strip()
    
    default_model = config.get('model_openrouter' if provider == 'openrouter' else 'model_ollama')
    model = (request.form.get('model') or default_model or DEFAULT_OLLAMA_MODEL).strip()
    
    print(f"API Request: Provider={provider}, Model={model}, Key={api_key[:10]}...")

    try:
        image_bytes = file.read()
        optimized_data = optimize_image(image_bytes)
        processed_b64 = base64.b64encode(optimized_data).decode('utf-8')
        prompt = get_extraction_prompt()
        
        full_response = ""
        
        if provider == "openrouter":
            if not api_key:
                return jsonify({"error": "Se requiere api_key para OpenRouter (configúrala en la web o envíala en el form)"}), 400
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
            print(f"Sending to OpenRouter: {model}")
            response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=300)
            print(f"OpenRouter Response Status: {response.status_code}")
            if response.status_code != 200:
                try: err_msg = response.json().get('error', {}).get('message', response.text)
                except: err_msg = response.text
                return jsonify({"error": f"OpenRouter Error ({response.status_code}): {err_msg}"}), response.status_code
            
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
        if not json_match: json_match = re.search(r'```\s*({.*})?\s*```', full_response, re.DOTALL)
        
        clean_json = full_response
        if json_match and json_match.group(1): clean_json = json_match.group(1)
        else:
            start = full_response.find('{')
            end = full_response.rfind('}')
            if start != -1 and end != -1: clean_json = full_response[start:end+1]
        
        def eval_math_expr(match):
            expr = match.group(1)
            try:
                if re.match(r'^[\d\s\+\-\*\/\.\(\)]+$', expr):
                    result = eval(expr, {"__builtins__": {}}, {})
                    return str(round(result, 2))
            except: pass
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
    config = load_config()
    
    if 'file' not in request.files:
        return jsonify({"error": "No hay archivo en la petición"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    provider = (request.form.get('provider') or config.get('provider', 'openrouter')).strip().lower()
    api_key = (request.form.get('api_key') or config.get('api_key', '')).strip()
    
    default_model = config.get('model_openrouter' if provider == 'openrouter' else 'model_ollama')
    model = (request.form.get('model') or default_model or DEFAULT_OLLAMA_MODEL).strip()

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
                if not api_key:
                    yield f"data: {json.dumps({'phase': 'error', 'message': 'API Key de OpenRouter no configurada'})}\n\n"
                    return
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "Invoice OCR Pro"
                }
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{processed_b64}"}}
                    ]}],
                    "temperature": 0.1, "max_tokens": 2000, "stream": True
                }
                print(f"Streaming from OpenRouter: {model}")
                response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, stream=True, timeout=300)
                print(f"OpenRouter Stream Status: {response.status_code}")
                if response.status_code != 200:
                    try: err_msg = response.json().get('error', {}).get('message', response.text)
                    except: err_msg = response.text
                    yield f"data: {json.dumps({'phase': 'error', 'message': f'OpenRouter Error ({response.status_code}): {err_msg}'})}\n\n"
                    return

                line_count = 0
                last_update = time.time()
                
                # Log de control
                print(f"DEBUG - Connection established. Status: {response.status_code}")
                print(f"DEBUG - Headers: {dict(response.headers)}")

                for line in response.iter_lines():
                    if line:
                        line_count += 1
                        line_str = line.decode('utf-8')
                        
                        if line_count <= 3:
                            print(f"DEBUG - Raw line {line_count}: {line_str[:100]}")
                            
                        if line_str.startswith('data: '):
                            data_str = line_str[6:]
                            if data_str.strip() == '[DONE]': break
                            try:
                                chunk = json.loads(data_str)
                                content = ""
                                if 'choices' in chunk and len(chunk['choices']) > 0:
                                    choice = chunk['choices'][0]
                                    if 'delta' in choice:
                                        content = choice['delta'].get('content', '')
                                    elif 'text' in choice:
                                        content = choice.get('text', '')
                                
                                if content:
                                    full_response += content
                                    token_count += 1
                                    if time.time() - last_update >= 0.5:
                                        elapsed = round(time.time() - start_time, 1)
                                        yield f"data: {json.dumps({'phase': 'generating', 'message': 'Generando...', 'tokens': token_count, 'tokens_per_sec': round(token_count/elapsed, 1), 'elapsed': elapsed})}\n\n"
                                        last_update = time.time()
                            except Exception as e:
                                continue
                
                # FALLBACK: Si el stream terminó pero no capturamos nada, intentar leer como texto plano
                if not full_response:
                    print(f"DEBUG - Stream empty. Attempting fallback read...")
                    try:
                        # Si es un error de OpenRouter que no vino por SSE
                        fallback_data = response.text
                        print(f"DEBUG - Fallback data length: {len(fallback_data)}")
                        if fallback_data and not fallback_data.startswith('data: '):
                            full_response = fallback_data
                    except:
                        pass
                
                print(f"DEBUG - Stream finished. Total lines: {line_count} | Total content length: {len(full_response)}")
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
                                if time.time() - last_update >= 0.5:
                                    elapsed = round(time.time() - start_time, 1)
                                    yield f"data: {json.dumps({'phase': 'generating', 'message': 'Generando...', 'tokens': token_count, 'tokens_per_sec': round(token_count/elapsed, 1), 'elapsed': elapsed})}\n\n"
                                    last_update = time.time()
                            if chunk.get('done', False): break
                        except: continue
            
            # --- LIMPIEZA AGRESIVA DE JSON ---
            # 1. Quitar caracteres de control invisibles que rompen json.loads
            full_clean = "".join(char for char in full_response if char.isprintable() or char in "\n\r\t")
            
            # 2. Intentar extraer bloque JSON si existe (varias formas de markdown)
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', full_clean, re.DOTALL)
            if json_match:
                clean_json = json_match.group(1)
            else:
                # Buscar el primer { y el último }
                start = full_clean.find('{')
                end = full_clean.rfind('}')
                if start != -1 and end != -1:
                    clean_json = full_clean[start:end+1]
                else:
                    clean_json = full_clean
            
            # 3. Limpiar posibles comas finales antes de cerrar llaves o corchetes
            clean_json = re.sub(r',\s*([\}\]])', r'\1', clean_json)
            
            # 4. Math Eval Protection (CUITs and dates)
            def eval_math_expr(match):
                expr = match.group(1).strip()
                # NO considerar CUIT o Fechas como operaciones de resta
                if re.match(r'^\d+-\d+(-\d+)*$', expr):
                    return f'"{expr}"'
                try:
                    if any(op in expr for op in '+*/') or ('-' in expr and not re.match(r'^\d+-\d+', expr)):
                        if re.match(r'^[\d\s\+\-\*\/\.\(\)]+$', expr):
                            result = eval(expr, {"__builtins__": {}}, {})
                            return str(round(result, 2))
                except: pass
                return '"' + expr + '"' if not expr.startswith('"') else expr
                
            clean_json = re.sub(r':\s*([\d\.\s\+\-\*\/\(\)]+(?:\s*[\+\-\*\/]\s*[\d\.\s\+\-\*\/\(\)]+)+)', 
                               lambda m: ': ' + eval_math_expr(m), clean_json)
            
            try: 
                output_data = json.loads(clean_json.strip())
            except Exception as e:
                # Loggear error específico para diagnóstico en consola del servidor
                print(f"!!! FATAL PARSE ERROR: {e}")
                print(f"!!! CLEAN ATTEMPT: {clean_json}")
                output_data = {"error": "Respuesta malformada", "raw": full_response, "clean_attempt": clean_json, "parse_error": str(e)}
            
            yield f"data: {json.dumps({'phase': 'complete', 'message': f'Completado en {round(time.time()-start_time,1)}s', 'tokens': token_count, 'elapsed': round(time.time()-start_time,1), 'result': output_data, 'processed_image': processed_b64})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'phase': 'error', 'message': str(e)})}\n\n"
    
    response = Response(stream_with_context(generate()), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
