import sqlite3
import os
from werkzeug.security import generate_password_hash

def inicializar_bd():
    # Nos aseguramos de que exista la carpeta data
    os.makedirs('data', exist_ok=True)
    
    ruta_db = os.path.join('data', 'usuarios.db')
    conn = sqlite3.connect(ruta_db)
    cursor = conn.cursor()
    
    # Creamos la tabla de usuarios
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            rol TEXT NOT NULL
        )
    ''')
    
    # Creamos un administrador por defecto
    email_admin = "admin@teknica.com"
    password_admin = generate_password_hash("admin123")
    
    try:
        cursor.execute("INSERT INTO usuarios (email, password, rol) VALUES (?, ?, ?)", 
                       (email_admin, password_admin, 'admin'))
        conn.commit()
        print("¡Base de datos creada con éxito!")
        print(f"Admin creado -> Email: {email_admin} | Contraseña: admin123")
    except sqlite3.IntegrityError:
        print("La base de datos ya estaba creada y el admin ya existe.")
        
    conn.close()

if __name__ == '__main__':
    inicializar_bd()