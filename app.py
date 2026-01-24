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
    {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash Exp (Free)"},
    {"id": "mistralai/mistral-3b", "name": "Ministral 3B"},
    {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash"},
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini"},
]

# Configuración de reintentos y fallbacks
MAX_RETRIES = 0 # Sin reintentos en el mismo modelo para ir rápido al siguiente
FALLBACK_DELAY = 1 
PRIMARY_FALLBACKS = [
    "mistralai/mistral-3b", # Muy estable para visión
    "openai/gpt-4o-mini",
    "qwen/qwen-2.5-vl-7b-instruct:free",
    "google/gemini-2.0-flash-exp:free"
]
TIMEOUT_OPENROUTER = 15 # Muy corto para detectar cuellos de botella rápido

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
    Optimiza la imagen para OCR:
    - Redimesiona manteniendo aspecto si es muy grande.
    - Calidad standard para evitar artefactos.
    """
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ('RGBA', 'P'): img = img.convert('RGB')
    
    # Redimensionar a un tamaño razonable para modelos de visión
    max_size = 1536 
    if img.width > max_size or img.height > max_size:
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()

def get_extraction_prompt():
    return """
    Analiza esta factura de Argentina (AFIP) y extrae los datos en JSON. 
    REGLA: Solo responde el JSON, sin texto adicional. Haz los cálculos tú mismo si es necesario.
    
    JSON: {
      "comprobante": {"tipo": "", "letra": "", "punto_venta": "", "numero": "", "fecha_emision": "", "cae": ""},
      "emisor": {"razon_social": "", "cuit": ""},
      "receptor": {"razon_social": "", "cuit": ""},
      "items": [{"cantidad": 1, "descripcion": "", "precio_unitario": 0.0, "subtotal": 0.0}],
      "totales": {"subtotal_neto": 0.0, "total_iva": 0.0, "total": 0.0}
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
            
            # Lógica de reintentos y fallback para OpenRouter
            models_to_try = [model] + [m for m in PRIMARY_FALLBACKS if m != model]
            last_error = ""
            
            for current_model in models_to_try:
                for attempt in range(MAX_RETRIES + 1):
                    try:
                        payload["model"] = current_model
                        print(f"DEBUG: Attempt {attempt} for {current_model} (Timeout: {TIMEOUT_OPENROUTER}s)")
                        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=TIMEOUT_OPENROUTER)
                        
                        if response.status_code == 200:
                            result = response.json()
                            if 'choices' in result and len(result['choices']) > 0:
                                full_response = result['choices'][0].get('message', {}).get('content', '')
                                if full_response:
                                    print(f"DEBUG: Success with {current_model}")
                                    break # Exito!
                        
                        # Si llegamos aquí es un error o respuesta vacía
                        status = response.status_code
                        print(f"DEBUG: Status {status} received")
                        try: err_detail = response.json().get('error', {}).get('message', response.text)
                        except: err_detail = response.text
                        last_error = f"Error {status} con {current_model}: {err_detail}"
                        print(f"Attempt {attempt+1} failed for {current_model}: {last_error}")
                        
                        if status == 402:
                            print(f"FATAL: 402 Insufficient credits for model {current_model}.")
                            return jsonify({"error": "SALDO INSUFICIENTE (402): Tu cuenta de OpenRouter no tiene créditos. Para usar modelos de visión (incluso los gratuitos), OpenRouter suele requerir una recarga mínima de $5 en settings/credits."}), 402
                        
                        if status in [429, 524, 502, 503, 504]:
                            time.sleep(FALLBACK_DELAY * (attempt + 1))
                            continue
                        else:
                            break # No reintentar errores fatales de API
                            
                    except Exception as e:
                        last_error = str(e)
                        print(f"Exception on attempt {attempt+1} for {current_model}: {e}")
                        time.sleep(FALLBACK_DELAY)
                
                if full_response:
                    model = current_model # Actualizar modelo para el log
                    break
            
            if not full_response:
                print(f"FATAL: All attempts failed. Last error: {last_error}")
                return jsonify({"error": f"No se pudo obtener respuesta tras varios intentos: {last_error}"}), 502
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
                    "X-Title": "OCR Invoice Pro"
                }
                # Log de seguridad y depuración
                masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "INVALID_KEY"
                print(f"DEBUG: Starting OpenRouter stream. Key: {masked_key}, Model: {model}, Image Size: {len(processed_b64)} chars")

                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{processed_b64}"}}
                    ]}],
                    "temperature": 0.1, "max_tokens": 2000, "stream": True
                }
                # Lógica de reintentos y fallback para OpenRouter con STREAMING
                models_to_try = [model] + [m for m in PRIMARY_FALLBACKS if m != model]
                success = False
                
                for current_model in models_to_try:
                    payload["model"] = current_model
                    # Intentar NO-STREAM primero para más estabilidad si hay problemas de quota
                    for attempt in range(MAX_RETRIES + 1):
                        use_stream = (attempt > 0) # Segundo intento usa stream para feedback
                        payload["stream"] = use_stream
                        
                        try:
                            print(f"DEBUG: Request to {current_model} (Stream={use_stream}, Attempt={attempt})")
                            response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, stream=use_stream, timeout=TIMEOUT_OPENROUTER)
                            
                            if response.status_code == 200:
                                if not use_stream:
                                    # Caso NO-STREAM
                                    result = response.json()
                                    content = result['choices'][0].get('message', {}).get('content', '') if 'choices' in result else ""
                                    if content and "model output must contain" not in content.lower():
                                        full_response = content
                                        success = True
                                        break
                                    else:
                                        print(f"DEBUG: Empty or invalid content from no-stream {current_model}")
                                else:
                                    # Caso STREAM
                                    line_count = 0
                                    chunk_received = False
                                    for line in response.iter_lines():
                                        if line:
                                            line_str = line.decode('utf-8', errors='ignore')
                                            if "model output must contain" in line_str.lower():
                                                break
                                            if line_str.startswith('data: '):
                                                data_str = line_str[6:]
                                                if data_str.strip() == '[DONE]': break
                                                try:
                                                    chunk = json.loads(data_str)
                                                    if 'error' in chunk: break
                                                    delta = chunk['choices'][0].get('delta', {}) if 'choices' in chunk else {}
                                                    part = delta.get('content', '') or chunk['choices'][0].get('text', '') if 'choices' in chunk else ''
                                                    if part:
                                                        full_response += part
                                                        token_count += 1
                                                        chunk_received = True
                                                        if time.time() - last_update >= 0.5:
                                                            yield f"data: {json.dumps({'phase': 'generating', 'message': f'Extrayendo ({current_model})...', 'tokens': token_count})}\n\n"
                                                            last_update = time.time()
                                                except: continue
                                    
                                    if full_response and chunk_received and "model output must contain" not in full_response.lower():
                                        success = True
                                        break

                            # Gestión de errores
                            status = response.status_code
                            if status == 401:
                                yield f"data: {json.dumps({'phase': 'error', 'message': 'API KEY INVÁLIDA (401)'})}\n\n"
                                return
                            if status == 402:
                                yield f"data: {json.dumps({'phase': 'error', 'message': 'SALDO INSUFICIENTE (402). Recarga en openrouter.ai/settings/credits'})}\n\n"
                                return
                            
                            print(f"DEBUG: Failed {current_model} with status {status}. Falling back...")
                            time.sleep(FALLBACK_DELAY)
                        except Exception as e:
                            print(f"DEBUG: Exception with {current_model}: {e}")
                            time.sleep(FALLBACK_DELAY)
                    
                    if success: break
                
                if not success:
                    print(f"FATAL: All models failed.")
                    yield f"data: {json.dumps({'phase': 'error', 'message': 'OpenRouter no responde (posible falta de saldo o modelo saturado)'})}\n\n"
                    return
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
    config = load_config()
    masked = f"{config['api_key'][:6]}..." if config['api_key'] else "MISSING"
    print(f"--- STARTUP DIAGNOSTIC ---")
    print(f"Provider: {config['provider']}")
    print(f"Key: {masked}")
    print(f"Model: {config['model_openrouter']}")
    print(f"--------------------------")
    app.run(host='0.0.0.0', port=5000, debug=True)
