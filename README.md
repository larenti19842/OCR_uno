# Invoice OCR Pro - Argentina 🇦🇷

<div align="center">

![Invoice OCR Pro](https://img.shields.io/badge/Invoice_OCR-Pro%20Edition-667EEA?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0id2hpdGUiIGQ9Ik0xNCAxMkg1djJoOWMtLjI0LjcxLS4yNyAxLjQ4LS4wNiAyLjNMNiAxNnYyaDE0di0yaC01LjI3bC0xLjIzLTN6TTkgMTBIM3YyaDZjMC0uNzEuMjMtMS4zNS42LTJ6IE03IDhoMlY2SDd6IG0xMCA2Yy0xLjEgMC0yLS45LTItMnMuOS0yIDItMiAyIC45IDIgMi0uOSAyLTIgMnoiLz48L3N2Zz4=)
![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-2.0+-000000?style=for-the-badge&logo=flask&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**Sistema de extracción inteligente de datos fiscales para facturas argentinas mediante IA local y en la nube.**

[📖 Documentación](#-arquitectura) • [🚀 Instalación](#-instalación-rápida) • [🔧 API](#-api-rest) • [💡 Uso](#-uso)

</div>

---

## 📋 Características

| Característica | Descripción |
|----------------|-------------|
| 🇦🇷 **Fiscal Universal** | Soporte completo para Facturas A, B, C, M, E y Tickets de Argentina (AFIP) |
| 🤖 **IA Dual** | Procesamiento local (Ollama) o en la nube (OpenRouter: GPT-4o, Claude, Gemini, Qwen) |
| ⚡ **Tiempo Real** | Streaming SSE con progreso de tokens y tiempo de procesamiento |
| 🔍 **Optimización OCR** | Pipeline de imagen con autocontraste, nitidez extrema y normalización |
| 📊 **Desglose Fiscal** | Extracción de IVA (21%, 10.5%, 27%), Percepciones IIBB e Impuestos Internos |
| 🔗 **API REST** | Endpoint `/api/extract` para integración con sistemas externos |
| 🔄 **Config Sincronizada** | Configuración compartida entre la UI web y la API (Postman) |

---

## 🏗️ Arquitectura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              INVOICE OCR PRO                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────────┐        ┌──────────────────┐        ┌──────────────────┐  │
│   │   Frontend   │        │     Backend      │        │   AI Providers   │  │
│   │  (index.html)│        │    (app.py)      │        │                  │  │
│   │              │        │                  │        │  ┌────────────┐  │  │
│   │  ┌────────┐  │  POST  │  ┌────────────┐  │        │  │   Ollama   │  │  │
│   │  │ Drag & │  │ ──────▶│  │  optimize_ │  │        │  │  (Local)   │  │  │
│   │  │  Drop  │  │        │  │   image()  │  │   ───▶ │  └────────────┘  │  │
│   │  └────────┘  │        │  └──────┬─────┘  │        │         │        │  │
│   │              │        │         │        │        │         ▼        │  │
│   │  ┌────────┐  │        │  ┌──────▼─────┐  │        │  ┌────────────┐  │  │
│   │  │ Config │  │ SSE    │  │   /process │  │   ───▶ │  │ OpenRouter │  │  │
│   │  │ Panel  │◀─│────────│  │    (SSE)   │  │        │  │  (Cloud)   │  │  │
│   │  └────────┘  │        │  └────────────┘  │        │  └────────────┘  │  │
│   │              │        │                  │        │                  │  │
│   │  ┌────────┐  │        │  ┌────────────┐  │        │                  │  │
│   │  │Results │  │        │  │ /api/extract│ │        │                  │  │
│   │  │ Table  │  │        │  │   (REST)   │  │        │                  │  │
│   │  └────────┘  │        │  └────────────┘  │        │                  │  │
│   │              │        │                  │        │                  │  │
│   └──────────────┘        │  ┌────────────┐  │        │                  │  │
│                           │  │config.json │  │        │                  │  │
│                           │  │ (Settings) │  │        │                  │  │
│                           │  └────────────┘  │        │                  │  │
│                           └──────────────────┘        └──────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Flujo de Datos

```
┌──────────┐    ┌───────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐
│  Imagen  │───▶│ Optimizar │───▶│   Prompt     │───▶│ LLM Vision  │───▶│   JSON   │
│ Original │    │   (PIL)   │    │ Estructurado │    │  (Ollama/   │    │  Fiscal  │
│          │    │ Grayscale │    │   AFIP       │    │  OpenRouter)│    │ Completo │
│          │    │ Contrast  │    │              │    │             │    │          │
│          │    │ Sharpen   │    │              │    │             │    │          │
└──────────┘    └───────────┘    └──────────────┘    └─────────────┘    └──────────┘
```

---

## 🚀 Instalación Rápida

### Prerrequisitos

- **Python 3.9+**
- **pip** (gestor de paquetes)
- **Ollama** (opcional, para procesamiento local)
- **API Key de OpenRouter** (opcional, para procesamiento en la nube)

### Pasos

```bash
# 1. Clonar el repositorio
git clone https://github.com/larenti19842/OCR_uno.git
cd OCR_uno

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Ejecutar el servidor
python app.py
```

El servidor estará disponible en: **http://localhost:5000**

---

## 💡 Uso

### Interfaz Web

1. Abrí **http://localhost:5000** en tu navegador.
2. Configurá el proveedor de IA en el panel **⚙️ Configuración del Proveedor**:
   - **Ollama**: Para procesamiento local (requiere tener Ollama corriendo).
   - **OpenRouter**: Para procesamiento en la nube (requiere API Key).
3. Arrastrá o subí una imagen de factura.
4. Esperá el procesamiento en tiempo real.
5. Revisá los resultados en la tabla de ítems y el desglose fiscal.

### API REST (Postman / cURL)

#### Endpoint Principal

```
POST /api/extract
Content-Type: multipart/form-data
```

#### Parámetros

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `file` | File | ✅ Sí | Imagen de la factura (JPEG/PNG) |
| `provider` | Text | ❌ No | `ollama` o `openrouter` (default: config) |
| `model` | Text | ❌ No | ID del modelo a usar |
| `api_key` | Text | ⚠️ Condicional | Requerido para OpenRouter si no hay config |

#### Ejemplo con cURL

```bash
curl -X POST http://localhost:5000/api/extract \
  -F "file=@factura.jpg"
```

#### Respuesta Exitosa (200 OK)

```json
{
  "comprobante": {
    "tipo": "Factura",
    "letra": "A",
    "punto_venta": "00001",
    "numero": "00001234",
    "cae": "74123456789012",
    "vto_cae": "2026-01-25"
  },
  "emisor": {
    "razon_social": "EMPRESA S.A.",
    "cuit": "30-12345678-9",
    "condicion_iva": "Responsable Inscripto"
  },
  "receptor": {
    "razon_social": "CLIENTE S.R.L.",
    "cuit": "30-98765432-1"
  },
  "items": [
    {
      "cantidad": 2,
      "descripcion": "Producto X",
      "precio_unitario": 1000.00,
      "alicuota_iva": 21.0,
      "subtotal": 2000.00
    }
  ],
  "impuestos": {
    "neto_gravado_21": 2000.00,
    "iva_21": 420.00
  },
  "totales": {
    "subtotal_neto": 2000.00,
    "total_iva": 420.00,
    "total": 2420.00
  }
}
```

---

## 🔧 API REST

### Endpoints Disponibles

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Interfaz web principal |
| `GET` | `/api/models` | Lista de modelos de OpenRouter disponibles |
| `GET` | `/api/config` | Obtener configuración actual del servidor |
| `POST` | `/api/config` | Guardar configuración (JSON body) |
| `POST` | `/api/extract` | **Extracción sincrónica** (devuelve JSON directo) |
| `POST` | `/process` | **Extracción con streaming** (SSE para la UI web) |

### Códigos de Estado

| Código | Significado |
|--------|-------------|
| `200` | Éxito |
| `400` | Error de validación (falta archivo, falta API key) |
| `401` | Error de autenticación (API key inválida o expirada) |
| `422` | Respuesta del modelo no parseable |
| `502` | Error de conexión con el proveedor (Ollama/OpenRouter) |

---

## ⚙️ Configuración

### Archivo `config.json`

El sistema persiste la configuración en un archivo `config.json` ubicado en la raíz del proyecto:

```json
{
  "provider": "openrouter",
  "api_key": "sk-or-v1-...",
  "model_openrouter": "qwen/qwen-2.5-vl-7b-instruct:free",
  "model_ollama": "ministral-facturador-full"
}
```

Este archivo se crea automáticamente cuando guardás la configuración desde la web y es leído por la API para Postman.

### Variables de Entorno (Alternativa)

También podés configurar usando variables de entorno (overwrites config.json):

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export DEFAULT_PROVIDER="openrouter"
```

---

## 🔍 Pipeline de Optimización OCR

El sistema aplica un pipeline de procesamiento de imagen antes de enviarla a la IA:

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│   Resize    │──▶│  Grayscale  │──▶│ AutoContrast│──▶│  Sharpen    │──▶│ White Border│
│  1024px max │   │    L mode   │   │  cutoff=0.5 │   │UnsharpMask  │   │    15px     │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
```

**Beneficios**:
- Elimina ruido y sombras de cámara.
- Mejora la definición de bordes de texto.
- Reduce el tamaño del payload sin perder calidad OCR.

---

## 🐛 Solución de Problemas

### Error 401: User not found / Invalid API Key

**Causa**: La API Key de OpenRouter es inválida, fue revocada, o no tiene saldo.

**Solución**:
1. Verificá tu clave en [openrouter.ai/keys](https://openrouter.ai/keys).
2. Asegurate de tener al menos $0.01 de crédito.
3. Generá una clave nueva si la anterior está tachada.

### Error 502: Bad Gateway

**Causa**: El servidor no puede conectarse al proveedor de IA.

**Solución**:
- **Ollama**: Verificá que Ollama esté corriendo (`ollama serve`).
- **OpenRouter**: Verificá tu conexión a internet.

### Error: "Respuesta malformada"

**Causa**: El modelo de IA no devolvió un JSON válido (a veces incluye texto extra).

**Solución**:
- El sistema tiene un fallback automático que intenta extraer el JSON.
- Probá con un modelo más potente (GPT-4o, Gemini 2.0 Flash).
- Revisá los logs en la UI para ver la respuesta cruda.

---

## 📁 Estructura del Proyecto

```
OCR_uno/
├── app.py              # Backend Flask (API + SSE)
├── index.html          # Frontend SPA (Vanilla JS)
├── config.json         # Configuración persistente (auto-generado)
├── requirements.txt    # Dependencias Python
├── README.md           # Este archivo
└── .gitignore
```

---

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Por favor:

1. Hacé un fork del repositorio.
2. Creá una rama para tu feature (`git checkout -b feature/nueva-funcion`).
3. Commiteá tus cambios (`git commit -m 'Agrega nueva función'`).
4. Pusheá a la rama (`git push origin feature/nueva-funcion`).
5. Abrí un Pull Request.

---

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Consultá el archivo `LICENSE` para más detalles.

---

<div align="center">

**Desarrollado con ❤️ para la comunidad Argentina**

</div>
