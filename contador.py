import os
import json

# Apuntamos al archivo real que se ve en tu carpeta data
RESPUESTAS_PATH = os.path.join("data", "respuestas.json")

# Correos que querés excluir del conteo (como el administrador)
CORREOS_EXCLUIDOS = ["admin@teknica.com", "admin@tke.com"] # Ajustá con el mail real del admin si es necesario

def _leer_respuestas():
    if not os.path.exists(RESPUESTAS_PATH):
        return []
    with open(RESPUESTAS_PATH, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def contar_por_mes(mes=None):
    conteo = {}
    respuestas = _leer_respuestas()
    
    for r in respuestas:
        # Buscamos el email del usuario que realizó la auditoría en el JSON
        email = r.get("email") or r.get("usuario") # Ajustá la clave según cómo guardes el dato en respuestas.json
        
        if email and email not in CORREOS_EXCLUIDOS:
            # Si el JSON ya guarda la fecha, podemos filtrar por mes si se provee
            fecha = r.get("fecha", "")
            if mes and fecha and not fecha.startswith(mes):
                continue
            
            conteo[email] = conteo.get(email, 0) + 1
            
    return conteo