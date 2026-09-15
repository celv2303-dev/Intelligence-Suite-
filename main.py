# -*- coding: utf-8 -*-
import os
import csv
import datetime
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
import requests

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN ESTÁTICA INTEGRADA REAL
# ==========================================
TELEGRAM_TOKEN = "8847229993:AAErL9nrx1Ytw9SLm6qLBN_Z1oTZ22kRqLk"
CHAT_ID_GRATIS = "-1004456471604"
CHAT_ID_VIP = "-1004427304402"

WHATSAPP_PHONE = "56957655242"
WHATSAPP_API_KEY = "pendiente"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO_PATH = os.path.join(BASE_DIR, "registro.csv")

# 🖥️ DISEÑO DE LA PÁGINA DE INICIO (PANEL WEB MANUAL)
PAGINA_INICIO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel - Inteligencia MLB</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f172a; margin: 0; padding: 20px; display: flex; justify-content: center; color: #f8fafc; }
        .container { background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 500px; }
        h2 { text-align: center; color: #38bdf8; margin-bottom: 25px; }
        label { font-weight: bold; color: #cbd5e1; display: block; margin-top: 15px; }
        input, select, textarea { width: 100%; padding: 12px; margin-top: 5px; background: #334155; border: 1px solid #475569; border-radius: 6px; color: white; font-size: 14px; }
        button { width: 100%; background: #0284c7; color: white; padding: 14px; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; margin-top: 25px; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #0369a1; }
        .footer { text-align: center; margin-top: 25px; font-size: 11px; color: #64748b; }
        .alerta-exito { background: #16a34a; color: white; padding: 12px; border-radius: 6px; text-align: center; font-weight: bold; margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>⚾ Panel de Control MLB</h2>
        {% if msg %}
        <div class="alerta-exito">✅ {{ msg }}</div>
        {% endif %}
        <form action="/webhook-pick" method="POST">
            <label>Destino de Publicación:</label>
            <select name="tipo_grupo">
                <option value="ambos">🔄 Ambos Grupos</option>
                <option value="vip">🔒 Grupo VIP Premium</option>
                <option value="gratis">🔓 Grupo Gratis / Público</option>
            </select>
            
            <label>Partido / Evento:</label>
            <input type="text" name="evento" placeholder="Ej: Yankees vs Dodgers" required>

            <label>Pronóstico:</label>
            <input type="text" name="pronostico" placeholder="Ej: Yankees Ganador" required>

            <label>Cuota:</label>
            <input type="text" name="cuota" placeholder="Ej: 1.85" required>

            <label>Stake / Unidades:</label>
            <input type="text" name="unidades" value="1.0" required>

            <label>Análisis Técnico:</label>
            <textarea name="analisis" rows="3" placeholder="Estadísticas de la jugada..." required></textarea>

            <button type="submit">🚀 Registrar y Publicar Pick</button>
        </form>
        <div class="footer">Servidor Activo Sincronizado para Recepción Automática a las 11:00 AM</div>
    </div>
</body>
</html>
"""

def despachar_telegram(mensaje, destino):
    chats = []
    if destino in ["gratis", "ambos"]: chats.append(CHAT_ID_GRATIS)
    if destino in ["vip", "ambos"]: chats.append(CHAT_ID_VIP)
    for chat_id in chats:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except:
            pass

def despachar_whatsapp(mensaje, destino):
    if "pendiente" in WHATSAPP_API_KEY: return
    if destino in ["vip", "ambos"]:
        url = f"https://callmebot.com{WHATSAPP_PHONE}&text={requests.utils.quote(mensaje)}&apikey={WHATSAPP_API_KEY}"
        try: requests.get(url, timeout=10)
        except: pass

def guardar_en_registro(fecha, evento, pronostico, cuota, unidades, destino):
    existe = os.path.exists(REGISTRO_PATH)
    with open(REGISTRO_PATH, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["Fecha", "Evento", "Pronostico", "Cuota", "Unidades", "Grupo Destino"])
        writer.writerow([fecha, evento, pronostico, cuota, unidades, destino])

def construir_plantilla_mensaje(etiqueta, fecha, evento, pronostico, cuota, unidades, analisis):
    return (
        f"{etiqueta}\n\n"
        f"📅 *Fecha:* {fecha}\n"
        f"🔥 *Evento:* {evento}\n"
        f"🎯 *Pronóstico:* {pronostico}\n"
        f"📈 *Cuota:* {cuota}\n"
        f"💰 *Stake Sugerido:* {unidades} U\n\n"
        f"📋 *Análisis Técnico:* {analisis}"
    )

@app.route('/', methods=['GET'])
def inicio():
    msg = request.args.get('msg', '')
    return render_template_string(PAGINA_INICIO, msg=msg)

@app.route('/webhook-pick', methods=['GET', 'POST'])
def recibir_pick_automatico():
    if request.method == 'GET':
        return redirect(url_for('inicio'))

    # Detectar formato de datos entrante de manera robusta
    if request.is_json:
        datos = request.get_json() or {}
    else:
        datos = request.form or {}

    fecha_hoy = datetime.date.today().strftime("%Y-%m-%d")
    
    # Extraer variables principales independientemente del formato de origen
    evento = datos.get('evento')
    pronostico = datos.get('pronostico')
    cuota = datos.get('cuota')
    unidades = str(datos.get('unidades', '1.0'))
    analisis = datos.get('analisis', 'Sin análisis detallado.')
    tipo_grupo = datos.get('tipo_grupo', 'ambos')

    # Guardar en base de datos e iniciar despachos
    guardar_en_registro(fecha_hoy, evento, pronostico, cuota, unidades, tipo_grupo)

    if unidades == '0' or not evento:
        mensaje_no_hay = construir_plantilla_mensaje(
            "⚠️ *AVISO OPERATIVO DE APUESTAS*", fecha_hoy, 
            evento, pronostico, cuota, "0", analisis
        )
        despachar_telegram(mensaje_no_hay, tipo_grupo)
        despachar_whatsapp(mensaje_no_hay, tipo_grupo)
    else:
        etiqueta = "🚀 *PICK OFICIAL GLOBAL*" if tipo_grupo == "ambos" else ("🔒 *EXCLUSIVO VIP PREMIUM*" if tipo_grupo == "vip" else "🔓 *LÍNEA GRATUITA GANCHO*")
        mensaje_pick = construir_plantilla_mensaje(
            etiqueta, fecha_hoy, evento, pronostico, cuota, unidades, analisis
        )
        despachar_telegram(mensaje_pick, tipo_grupo)
        despachar_whatsapp(mensaje_pick, tipo_grupo)

    if request.is_json:
        return jsonify({"status": "success", "message": "Procesado correctamente"}), 200
    return redirect(url_for('inicio', msg="Aviso de 'Sin Picks' enviado correctamente."))

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)
