"""Reconocimiento de pedidos a partir de una captura de pantalla usando la API de Claude (vision)."""
import base64
import json
import os

MODEL = "claude-sonnet-5"


def _media_type(filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    return {
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'webp': 'image/webp',
        'gif': 'image/gif',
    }.get(ext, 'image/png')


def reconocer_pedido(image_path, catalogo_productos, api_key):
    """
    Envia la captura a Claude y devuelve una lista de items detectados:
    [{"descripcion": str, "cantidad": int, "producto_id": int|None}, ...]
    catalogo_productos: lista de dicts {"id": int, "nombre": str, "modelo": str, "color": str}
    """
    if not api_key:
        raise RuntimeError("No hay ANTHROPIC_API_KEY configurada")

    import anthropic

    with open(image_path, 'rb') as f:
        img_b64 = base64.standard_b64encode(f.read()).decode('utf-8')

    client = anthropic.Anthropic(api_key=api_key)

    catalogo_txt = "\n".join(
        f"- id={p['id']}: {p['nombre']} ({p.get('modelo','')} {p.get('color','')})".strip()
        for p in catalogo_productos
    ) or "(catalogo vacio)"

    prompt = f"""Esta es una captura de pantalla de un chat (Instagram, WhatsApp, etc.) donde un cliente
esta pidiendo fundas de libros. Extrae la lista de fundas que pide, con la cantidad de cada una.

Catalogo de productos disponibles en el stock:
{catalogo_txt}

Para cada funda que el cliente pide, intenta emparejarla con un id del catalogo si es razonable
(por nombre, modelo, color o diseno mencionado). Si no coincide con ningun producto del catalogo,
deja "producto_id" en null y describe la funda en "descripcion".

Responde UNICAMENTE con JSON valido, sin texto adicional, con este formato exacto:
{{"items": [{{"descripcion": "string", "cantidad": 1, "producto_id": null}}], "cliente_detectado": "nombre o null"}}
"""

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": _media_type(image_path),
                    "data": img_b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    text = "".join(block.text for block in message.content if hasattr(block, 'text'))
    text = text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    data = json.loads(text)
    return data
