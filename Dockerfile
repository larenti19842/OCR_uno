# Usar una imagen de Python liviana
FROM python:3.10-slim

# Instalar dependencias del sistema necesarias para Pillow
RUN apt-get update && apt-get install -y \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Copiar archivos e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

# Copiar el resto del código
COPY . .

# Exponer el puerto de Flask
EXPOSE 5000

# Comando para iniciar con Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app", "--timeout", "600"]
