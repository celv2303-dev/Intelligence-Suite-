# -*- coding: utf-8 -*-
import os
import csv
import datetime
from flask import Flask, request, jsonify, render_template_string
import requests

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DE REDES SOCIALES VIP Y GRATIS
# ==========================================
# TELEGRAM
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TU_TOKEN_DE_BOT")
CHAT_ID_VIP = os.environ.get("TELEGRAM_CHAT_ID_VIP", "ID_CANAL_VIP")
CHAT_ID_GRATIS = os.environ.get("TELEGRAM_CHAT_ID_GRATIS", "ID_CANAL_GRATIS")

# WHATSAPP (CallMeBot Gratis)
WHATSAPP_PHONE = "TU_NUMERO_CON_CODIGO_DE_PAIS" 
WHATSAPP_API_KEY = "TU_API_KEY_DE_CALLMEBOT"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO_PATH = os.path.join(BASE_DIR, "registro.csv")

# 🖥️ DISEÑO DE LA PÁGINA DE INICIO (PANEL DE CONTROL VISUAL)
PAGINA_INICIO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel de Control - Picks MLB</title>
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); width: 100%; max-width: 500px; }
        h2 { text-align: center; color: #1a365d; margin-bottom: 20px; }
        label { font-weight: bold; color: #4a5568; display: block; margin-top: 15px; }
        input, select, textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #cbd5e0; border-radius: 6px; box-sizing: border-box; font-size: 14px; }
        button { width: 100%; background: #3182ce; color: white; padding: 12px; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; margin-top: 25px; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #2b6cb0; }
        .footer { text-align: center; margin-top: 20px; font-size: 12px; color: #718096; }
    </style>
</head>
<body>
    <div class="container">
        <h2>⚾ Automatización MLB Panel</h2>
        <form action="/webhook-pick" method="POST">
            <label>Destino de Publicación:</label>
            <select name="tipo_grupo">
                <option value="vip">🔒 Grupo VIP Premiun</option>
                <option value="gratis">🔓 Grupo Gratis / Público</option>
                <option value="ambos">🔄 Ambos Grupos</option>
            </select>

            <label>Partido / Evento:</label>
            <input type="text" name="evento" placeholder="Ej: Yankees vs Dodgers" required>

            <label>Pronóstico:</label>
            <input type="text" name="pronostico" placeholder="Ej: Yankees Ganador (Moneyline)" required>

            <label>Cuota:</label>
            <input type="text" name="cuota" placeholder="Ej: 1.85" required>

            <label>Stake / Unidades:</label>
            <input type="text" name="unidades" value="1.0" required>

            <label>Análisis Técnico:</label>
            <textarea name="analisis" rows="4" placeholder="Escribe aquí las tendencias o estadísticas clave..."></textarea>

            <button type="submit">🚀 Registrar y Publicar Pick</button>
        </form>
        <div class="footer">Intelligence Suite v2.0 - Sincronizado con Render</div>
    </div>
</body>
</html>
"""

def enviar_telegram(mensaje, destino):
    if "TU_TOKEN" in TELEGRAM_TOKEN: return
    
    # Determinar a qué chat enviarlo
    if destino == "vip": chats = [CHAT_ID_VIP]
    elif destino == "gratis": chats = [CHAT_ID_GRATIS]
    else: chats = [CHAT_ID_VIP, CHAT_ID_GRATIS]

    for chat_id in chats:
        url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}
        try: requests.post(url, json=payload)
        except: pass

def enviar_whatsapp_gratis(mensaje):
    if "TU_API_KEY" in WHATSAPP_API_KEY: return
    url = f"https://callmebot.com{WHATSAPP_PHONE}&text={requests.utils.quote(mensaje)}&apikey={WHATSAPP_API_KEY}"
    try: requests.get(url)
    except: pass

@app.route('/', methods=['GET'])
def inicio():
    return render_template_string(PAGINA_INICIO)

@app.route('/webhook-pick', methods=['GET', 'POST'])
def recibir_pick():
    if request.method == 'POST':
        # Soporta tanto JSON de tu otra IA como datos del formulario web
        datos = request.json or request.form
    else:
        datos = request.args

    evento = datos.get('evento')
    pronostico = datos.get('pronostico')
    cuota = datos.get('cuota')
    unidades = datos.get('unidades', '1.0')
    analisis = datos.get('analisis', 'Análisis en desarrollo...')
    tipo_grupo = datos.get('tipo_grupo', 'vip') # Por defecto va al VIP si no se especifica

    if not evento or not pronostico or not cuota:
        return jsonify({"status": "error", "message": "Faltan datos obligatorios"}), 400

    fecha_hoy = datetime.date.today().strftime("%Y-%m-%d")

    # Guardar en base de datos CSV
    existe_archivo = os.path.exists(REGISTRO_PATH)
    with open(REGISTRO_PATH, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe_archivo:
            writer.writerow(["Fecha", "Evento", "Pronostico", "Cuota", "Unidades", "Grupo"])
        writer.writerow([fecha_hoy, evento, pronostico, cuota, unidades, tipo_grupo])

    # Formatear el mensaje de salida
    etiqueta = "🔒 VIP PREMIUN" if tipo_grupo == "vip" else "🔓 LÍNEA GRATUITA"
    if tipo_grupo == "ambos": etiqueta = "⚾ PICK OFICIAL"

    mensaje_vip = (
        f"{etiqueta} *MLB*\n\n"
        f"📅 *Fecha:* {fecha_hoy}\n"
        f"🔥 *Partido:* {evento}\n"
        f"🎯 *Pronóstico:* {pronostico}\n"
        f"📈 *Cuota:* {cuota}\n"
        f"💰 *Stake:* {unidades} U\n\n"
        f"📋 *Análisis:* {analisis}"
    )

    # Envíos automáticos según corresponda
    enviar_telegram(mensaje_vip, tipo_grupo)
    if tipo_grupo in ['vip', 'ambos']:
        enviar_whatsapp_gratis(mensaje_vip)

    return jsonify({"status": "success", "message": f"Pick enviado correctamente a destino: {tipo_grupo}"}), 200

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)
