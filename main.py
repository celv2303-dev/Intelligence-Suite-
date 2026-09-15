# -*- coding: utf-8 -*-
import os
import csv
import datetime
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
import requests

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DIRECTA Y ESTÁTICA FORZADA
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
        url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Error forzado enviando a Telegram: {e}")

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

    es_json = request.is_json
    if es_json:
        datos_recibidos = request.get_json()
    else:
        datos_recibidos = request.form

    fecha_hoy = datetime.date.today().strftime("%Y-%m-%d")
    
    picks_lista = datos_recibidos.get('picks', []) if es_json else []
    if not picks_lista and datos_recibidos.get('evento'):
        picks_lista = [datos_recibidos]

    cantidad_picks = len(picks_lista)

    if cantidad_picks == 0 or (cantidad_picks == 1 and str(picks_lista[0].get('unidades') if es_json else picks_lista[0].get('unidades')) == '0'):
        p_vacio = picks_lista[0] if cantidad_picks == 1 else datos_recibidos
        evento_texto = p_vacio.get('evento', f"Sin Pick Disponible — {fecha_hoy}")
        pronostico_texto = p_vacio.get('pronostico', "Sin pick hoy")
        cuota_texto = p_vacio.get('cuota', "—")
        analisis_texto = p_vacio.get('analisis', "No hay pick disponible para el día de hoy.")
        tipo_grupo = p_vacio.get('tipo_grupo', 'ambos')

        mensaje_no_hay = construir_plantilla_mensaje(
            "⚠️ *AVISO OPERATIVO DE APUESTAS*", fecha_hoy, 
            evento_texto, pronostico_texto, cuota_texto, "0", analisis_texto
        )
        
        guardar_en_registro(fecha_hoy, evento_texto, pronostico_texto, cuota_texto, "0", tipo_grupo)
        despachar_telegram(mensaje_no_hay, tipo_grupo)
        despachar_whatsapp(mensaje_no_hay, tipo_grupo)
        
        if es_json:
            return jsonify({"status": "success", "message": "Mensaje enviado"}), 200
        return redirect(url_for('inicio', msg="Aviso de 'Sin Picks' enviado correctamente."))

    elif cantidad_picks == 1:
        p = picks_lista[0]
        tipo_grupo = p.get('tipo_grupo', 'ambos')
        mensaje = construir_plantilla_mensaje(
            "🚀 *PICK OFICIAL GLOBAL*", fecha_hoy, p.get('evento'), 
            p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), p.get('analisis', 'Análisis en desarrollo...')
        )
        guardar_en_registro(fecha_hoy, p.get('evento'), p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), tipo_grupo)
        despachar_telegram(mensaje, tipo_grupo)
        despachar_whatsapp(mensaje, tipo_grupo)
        
        if es_json:
            return jsonify({"status": "success", "message": "Pick enviado"}), 200
        return redirect(url_for('inicio', msg="Pick publicado con éxito."))

    else:
        for indice, p in enumerate(picks_lista):
            if indice == 0:
                destino = "gratis"
                etiqueta = "🔓 *LÍNEA GRATUITA GANCHO*"
            else:
                destino = "vip"
                etiqueta = "🔒 *EXCLUSIVO VIP PREMIUM*"

            mensaje = construir_plantilla_mensaje(
                etiqueta, fecha_hoy, p.get('evento'), 
                p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), p.get('analisis', 'Análisis premium.')
            )
            guardar_en_registro(fecha_hoy, p.get('evento'), p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), destino)
            despachar_telegram(mensaje, destino)
            despachar_whatsapp(mensaje, destino)

        return jsonify({"status": "success", "message": "Múltiples picks divididos correctamente."}), 200

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)
