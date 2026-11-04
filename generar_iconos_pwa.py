from PIL import Image
import os

# Nombre de la imagen que enviaste
IMAGEN_ORIGINAL = "static/img/teknicalogo.png"

def generar_iconos():
    if not os.path.exists(IMAGEN_ORIGINAL):
        print(f"Error: No se encuentra el archivo '{IMAGEN_ORIGINAL}' en la carpeta.")
        return

    # Abrir la imagen original
    img = Image.open(IMAGEN_ORIGINAL).convert("RGBA")
    ancho, alto = img.size

    # Recortar la parte izquierda (las flechas rojas / isotipo)
    # Calculamos el ancho de la sección de las flechas (aproximadamente el primer 28% de la imagen)
    ancho_isotipo = int(ancho * 0.28)
    isotipo = img.crop((0, 0, ancho_isotipo, alto))

    # Crear una imagen cuadrada con el fondo azul marino característico (#173f72)
    def crear_cuadrado(size):
        # Fondo azul de la marca
        fondo = Image.new("RGBA", (size, size), "#173f72")
        
        # Redimensionar el isotipo manteniendo su proporción para que entre bien
        isotipo_copia = isotipo.copy()
        isotipo_copia.thumbnail((int(size * 0.75), int(size * 0.75)), Image.Resampling.LANCZOS)
        
        iw, ih = isotipo_copia.size
        # Centrar el isotipo en el fondo cuadrado
        pos_x = (size - iw) // 2
        pos_y = (size - ih) // 2
        
        fondo.paste(isotipo_copia, (pos_x, pos_y), isotipo_copia)
        return fondo.convert("RGB")

    os.makedirs("static/img", exist_ok=True)

    # Generar y guardar los tamaños obligatorios para la PWA
    icon_192 = crear_cuadrado(192)
    icon_192.save("static/img/icon-192.png", "PNG")

    icon_512 = crear_cuadrado(512)
    icon_512.save("static/img/icon-512.png", "PNG")

    print("¡Listo! Los íconos 'icon-192.png' y 'icon-512.png' se generaron con éxito en 'static/img/'.")

generar_iconos()