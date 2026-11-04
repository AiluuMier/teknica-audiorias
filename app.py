import json
import os
import re
import sqlite3
import time
import uuid
from datetime import timedelta
from functools import wraps
from io import BytesIO

import click
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, session, url_for)
from flask_wtf.csrf import CSRFProtect
from PIL import Image, ImageOps                      # NUEVO
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from xhtml2pdf import pisa

app = Flask(__name__)
from secciones import SECCIONES_INICIALES

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "static", "uploads")
AVATARS = os.path.join(BASE, "static", "avatars")    # NUEVO
DATA = os.path.join(BASE, "data")
CUSTOM_FILE = os.path.join(DATA, "secciones_custom.json")
INIT_FLAG = os.path.join(DATA, "inicializado.flag")
DATABASE = os.path.join(DATA, "usuarios.db")   # usuarios, roles y auditorías
MAX_IMAGENES = 4
EXT_OK = {"png", "jpg", "jpeg", "gif", "webp"}
AVATAR_EXT_OK = {"png", "jpg", "jpeg", "webp"}       # NUEVO
AVATAR_MAX_BYTES = 10 * 1024 * 1024                  # NUEVO (10 MB, luego se achica)
AVATAR_SIZE = 256                                    # NUEVO

# Roles que existen en la base de datos
ROL_ADMIN = "admin"
ROL_SUPERVISOR = "supervisor"
ROLES = (ROL_ADMIN, ROL_SUPERVISOR)

os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(AVATARS, exist_ok=True)                  # NUEVO
os.makedirs(DATA, exist_ok=True)

# En producción definí SECRET_KEY como variable de entorno
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esto-por-algo-secreto")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.permanent_session_lifetime = timedelta(days=14)

CSRFProtect(app)


# ================= Base de datos: usuarios, roles y auditorías =================
SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT    NOT NULL UNIQUE,
    nombre         TEXT    NOT NULL DEFAULT '',
    password_hash  TEXT    NOT NULL,
    rol            TEXT    NOT NULL CHECK (rol IN ('admin', 'supervisor')),
    activo         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS auditorias (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    seccion_id               TEXT    NOT NULL,
    titulo                   TEXT    NOT NULL,
    fecha                    TEXT    NOT NULL,
    usuario_id               INTEGER NOT NULL REFERENCES usuarios(id),
    usuario_nombre           TEXT    NOT NULL DEFAULT '',
    supervisor_obra          TEXT    NOT NULL DEFAULT '',
    actividad                TEXT    NOT NULL DEFAULT '',
    participantes_auditados  TEXT    NOT NULL DEFAULT '',
    direccion                TEXT    NOT NULL DEFAULT '',
    proyecto                 TEXT    NOT NULL DEFAULT '',
    numero_equipo            TEXT    NOT NULL DEFAULT '',
    auditor_aclaracion       TEXT    NOT NULL DEFAULT '',
    auditor_firma            TEXT    NOT NULL DEFAULT '',
    auditado_nombre          TEXT    NOT NULL DEFAULT '',
    auditado_documento       TEXT    NOT NULL DEFAULT '',
    auditado_puesto          TEXT    NOT NULL DEFAULT '',
    auditado_firma           TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS auditoria_respuestas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    auditoria_id   INTEGER NOT NULL REFERENCES auditorias(id) ON DELETE CASCADE,
    question_id    TEXT    NOT NULL,
    pregunta       TEXT    NOT NULL,
    respuesta      TEXT    NOT NULL CHECK (respuesta IN ('si', 'no', 'na')),
    observacion    TEXT    NOT NULL DEFAULT '',
    imagenes       TEXT    NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_auditorias_fecha   ON auditorias(fecha);
CREATE INDEX IF NOT EXISTS idx_auditorias_usuario ON auditorias(usuario_id);
CREATE INDEX IF NOT EXISTS idx_respuestas_auditoria ON auditoria_respuestas(auditoria_id);
"""

# Hash falso para que el tiempo de respuesta sea parecido si el correo no existe
_DUMMY_HASH = generate_password_hash("contraseña-inexistente")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def sincronizar_sesion():
    if request.endpoint == "static" or "user_id" not in session:
        return
    user = get_db().execute(
        "SELECT rol, activo FROM usuarios WHERE id = ?", (session["user_id"],)
    ).fetchone()
    if user is None or not user["activo"]:
        session.clear()
        return redirect(url_for("login"))
    session["rol"] = user["rol"]


@app.cli.command("init-db")
def init_db():
    get_db().executescript(SCHEMA)
    click.echo("Base de datos lista.")


# ============================ Control de acceso ==============================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def rol_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if session.get("rol") not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("inicio"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        db = get_db()
        db.executescript(SCHEMA)
        user = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()

        hash_a_comprobar = user["password_hash"] if user else _DUMMY_HASH
        password_ok = check_password_hash(hash_a_comprobar, password)

        if user and password_ok and user["activo"] and user["rol"] in ROLES:
            session.clear()
            session["user_id"] = user["id"]
            session["nombre"] = user["nombre"] or user["email"]
            session["rol"] = user["rol"]
            session.permanent = remember
            return redirect(url_for("inicio"))

        flash("Correo o contraseña incorrectos.", "danger")

    return render_template("login.html")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    flash("Has cerrado sesión correctamente.", "info")
    return redirect(url_for("login"))
# ============================ Foto de perfil (NUEVO) ============================
def _avatar_path(user_id):
    return os.path.join(AVATARS, f"{user_id}.jpg")


def _iniciales(email):
    """juan.perez@x.com -> JP | brenkhe@x.com -> BR"""
    local = (email or "").split("@")[0]
    partes = [p for p in re.split(r"[._\-+\d]+", local) if p]
    if len(partes) >= 2:
        return (partes[0][0] + partes[1][0]).upper()
    return (local[:2] or "?").upper()


@app.context_processor
def inyectar_avatar():
    """Deja avatar_url y avatar_iniciales disponibles en todas las plantillas."""
    if "user_id" not in session:
        return {}
    fila = get_db().execute(
        "SELECT email FROM usuarios WHERE id = ?", (session["user_id"],)
    ).fetchone()
    email = fila["email"] if fila else ""

    url = None
    ruta = _avatar_path(session["user_id"])
    if os.path.exists(ruta):
        # ?v=... evita que el navegador muestre la foto vieja en caché
        url = url_for("static", filename=f"avatars/{session['user_id']}.jpg") \
              + f"?v={int(os.path.getmtime(ruta))}"
    return {"avatar_url": url, "avatar_iniciales": _iniciales(email)}


@app.post("/perfil/foto")
@login_required
def subir_foto_perfil():
    destino = request.referrer or url_for("inicio")
    archivo = request.files.get("foto")
    if not archivo or not archivo.filename:
        flash("No seleccionaste ninguna imagen.", "error")
        return redirect(destino)

    ext = archivo.filename.rsplit(".", 1)[-1].lower() if "." in archivo.filename else ""
    if ext not in AVATAR_EXT_OK:
        flash("Formato no válido. Usá PNG, JPG o WEBP.", "error")
        return redirect(destino)

    archivo.seek(0, os.SEEK_END)
    if archivo.tell() > AVATAR_MAX_BYTES:
        flash("La imagen pesa más de 10 MB.", "error")
        return redirect(destino)
    archivo.seek(0)

    try:
        img = Image.open(archivo)
        img = ImageOps.exif_transpose(img)           # respeta la rotación de fotos de celular
        img = img.convert("RGB")
        img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)  # cuadrado centrado
        img.save(_avatar_path(session["user_id"]), "JPEG", quality=85, optimize=True)
    except Exception:
        flash("No se pudo procesar la imagen. Probá con otra.", "error")
        return redirect(destino)

    flash("Foto de perfil actualizada.", "ok")
    return redirect(destino)


@app.post("/perfil/foto/eliminar")
@login_required
def eliminar_foto_perfil():
    ruta = _avatar_path(session["user_id"])
    if os.path.exists(ruta):
        os.remove(ruta)
    flash("Foto de perfil eliminada.", "ok")
    return redirect(request.referrer or url_for("inicio"))


# ======================= Secciones y archivos custom =======================
def leer_json(ruta, defecto):
    if not os.path.exists(ruta):
        return defecto
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def guardar_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def sembrar_secciones():
    if os.path.exists(INIT_FLAG):
        return
    existentes = leer_json(CUSTOM_FILE, [])
    ids = {s["id"] for s in existentes}
    nuevas = [s for s in SECCIONES_INICIALES if s["id"] not in ids]
    guardar_json(CUSTOM_FILE, nuevas + existentes)
    open(INIT_FLAG, "w").close()


sembrar_secciones()

# Crear tablas al arrancar y asegurar que exista un admin
with app.app_context():
    db = get_db()
    db.executescript(SCHEMA)

    admin_count = db.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'admin'").fetchone()[0]
    if admin_count == 0:
        db.execute(
            "INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (?, ?, ?, ?)",
            ("admin@tke.com", "Administrador TKE", generate_password_hash("tkeadmin"), "admin")
        )
        db.commit()


# ============================ Secciones y formularios ============================
def todas_las_secciones():
    return leer_json(CUSTOM_FILE, [])


def subsecciones_de(seccion):
    if seccion.get("subsecciones"):
        return seccion["subsecciones"]
    if seccion.get("questions"):
        return [{"id": f"{seccion['id']}-sub", "titulo": seccion["title"],
                 "questions": seccion["questions"]}]
    return []


def preguntas_de(seccion):
    return [q for sub in seccion["subsecciones"] for q in sub["questions"]]


def buscar_seccion(seccion_id):
    seccion = next((s for s in todas_las_secciones() if s["id"] == seccion_id), None)
    if seccion is not None:
        seccion["subsecciones"] = subsecciones_de(seccion)
    return seccion


@app.route("/")
@rol_required(ROL_ADMIN, ROL_SUPERVISOR)
def inicio():
    return render_template("index.html", secciones=todas_las_secciones())


# ========================== Panel del Supervisor ==========================
@app.route("/mis-auditorias")
@rol_required(ROL_SUPERVISOR)
def mis_auditorias():
    db = get_db()
    usuario_id = session["user_id"]
    
    # Solo los supervisores ven sus propias auditorías
    auditorias = db.execute(
        "SELECT * FROM auditorias WHERE usuario_id = ? ORDER BY id DESC", 
        (usuario_id,)
    ).fetchall()
        
    return render_template("mis_auditorias.html", auditorias=auditorias)
# ========================== Panel de Administración ==========================
@app.route("/admin")
@rol_required(ROL_ADMIN)
def admin_panel():
    db = get_db()
    usuarios = db.execute("SELECT id, email, nombre, rol, activo FROM usuarios").fetchall()
    supervisores = [u for u in usuarios if u["rol"] == ROL_SUPERVISOR]
    ids_supervisores = [u["id"] for u in supervisores]

    total_secciones = len(todas_las_secciones())
    total_auditorias = db.execute("SELECT COUNT(*) FROM auditorias").fetchone()[0]

    mes_actual = time.strftime("%Y-%m")
    mes = request.args.get("mes", mes_actual)
    usuario_sel = request.args.get("usuario", type=int)
    if usuario_sel not in ids_supervisores:
        usuario_sel = None

    meses = sorted(
        {r[0] for r in db.execute("SELECT DISTINCT substr(fecha, 1, 7) FROM auditorias")}
        | {mes_actual},
        reverse=True,
    )

    conteo_mes = {
        r["usuario_id"]: r["n"]
        for r in db.execute(
            "SELECT usuario_id, COUNT(*) AS n FROM auditorias "
            "WHERE substr(fecha, 1, 7) = ? GROUP BY usuario_id", (mes,)
        )
    }

    ids_filtro = [usuario_sel] if usuario_sel else ids_supervisores
    auditorias_mes = []
    if ids_filtro:
        marcas = ",".join("?" * len(ids_filtro))
        auditorias_mes = db.execute(
            f"""
            SELECT a.id, a.fecha, a.titulo, a.usuario_id,
                   COALESCE(NULLIF(u.nombre, ''), u.email, a.usuario_nombre) AS supervisor,
                   SUM(CASE WHEN r.respuesta = 'no' THEN 1 ELSE 0 END) AS cant_no,
                   COUNT(r.id) AS total
            FROM auditorias a
            LEFT JOIN usuarios u ON u.id = a.usuario_id
            LEFT JOIN auditoria_respuestas r ON r.auditoria_id = a.id
            WHERE substr(a.fecha, 1, 7) = ? AND a.usuario_id IN ({marcas})
            GROUP BY a.id
            ORDER BY a.fecha DESC
            """,
            [mes, *ids_filtro],
        ).fetchall()

    return render_template(
        "admin.html",
        usuarios=usuarios,
        supervisores=supervisores,
        total_secciones=total_secciones,
        total_auditorias=total_auditorias,
        meses=meses,
        mes=mes,
        usuario_sel=usuario_sel,
        conteo_mes=conteo_mes,
        auditorias_mes=auditorias_mes,
    )


# ====================== Detalle y PDF de una auditoría ======================
def cargar_auditoria(audit_id):
    db = get_db()
    fila = db.execute("SELECT * FROM auditorias WHERE id = ?", (audit_id,)).fetchone()
    if fila is None:
        abort(404)
    # Un supervisor solo puede ver sus propias auditorías
    if session.get("rol") == ROL_SUPERVISOR and fila["usuario_id"] != session["user_id"]:
        abort(403)

    seccion = buscar_seccion(fila["seccion_id"])
    if seccion is None:
        seccion = {"id": fila["seccion_id"], "title": fila["titulo"], "subsecciones": []}

    a = dict(fila)                      # todas las columnas de la tabla
    a["usuario"] = fila["usuario_nombre"]
    a["respuestas"] = [
        {
            "question_id": r["question_id"],
            "pregunta": r["pregunta"],
            "respuesta": r["respuesta"],
            "observacion": r["observacion"],
            "imagenes": json.loads(r["imagenes"] or "[]"),
        }
        for r in db.execute(
            "SELECT * FROM auditoria_respuestas WHERE auditoria_id = ? ORDER BY id",
            (audit_id,),
        )
    ]
    usuario = db.execute(
        "SELECT email, nombre FROM usuarios WHERE id = ?", (fila["usuario_id"],)
    ).fetchone()
    return a, seccion, usuario


@app.route("/admin/auditoria/<int:audit_id>")
@rol_required(ROL_ADMIN, ROL_SUPERVISOR)
def ver_auditoria(audit_id):
    a, seccion, usuario = cargar_auditoria(audit_id)
    return render_template("auditoria_detalle.html", a=a, seccion=seccion, usuario=usuario)


def link_callback(uri, rel):
    """Le dice a xhtml2pdf dónde están las imágenes de /static en el disco."""
    if uri.startswith("/static/"):
        return os.path.join(BASE, uri.lstrip("/").replace("/", os.sep))
    return uri


@app.route("/auditoria/<int:audit_id>/pdf")
@rol_required(ROL_ADMIN, ROL_SUPERVISOR)
def descargar_pdf(audit_id):
    a, seccion, usuario = cargar_auditoria(audit_id)
    html = render_template("auditoria_pdf.html", a=a, seccion=seccion, usuario=usuario)

    buffer = BytesIO()
    resultado = pisa.CreatePDF(html, dest=buffer, link_callback=link_callback, encoding="utf-8")
    if resultado.err:
        abort(500)

    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"auditoria_{audit_id}.pdf",
    )


# ============================ Gestión de usuarios ============================
@app.post("/admin/usuarios/<int:user_id>/rol")
@rol_required(ROL_ADMIN)
def cambiar_rol_web(user_id):
    nuevo_rol = request.form.get("rol")
    if nuevo_rol not in ROLES:
        abort(400)

    db = get_db()
    usuario = db.execute(
        "SELECT id, email, rol FROM usuarios WHERE id = ?", (user_id,)
    ).fetchone()
    if usuario is None:
        abort(404)

    if usuario["id"] == session["user_id"]:
        flash("No podés cambiar tu propio rol.", "error")
        return redirect(url_for("admin_panel"))

    if usuario["rol"] == ROL_ADMIN and nuevo_rol != ROL_ADMIN:
        admins = db.execute(
            "SELECT COUNT(*) FROM usuarios WHERE rol = ? AND activo = 1", (ROL_ADMIN,)
        ).fetchone()[0]
        if admins <= 1:
            flash("Debe existir al menos un administrador activo.", "error")
            return redirect(url_for("admin_panel"))

    db.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (nuevo_rol, user_id))
    db.commit()
    flash(f"{usuario['email']} ahora es {nuevo_rol}.", "ok")
    return redirect(url_for("admin_panel"))


@app.post("/admin/usuarios/crear")
@rol_required(ROL_ADMIN)
def crear_usuario_web():
    email = request.form.get("email", "").strip().lower()
    nombre = request.form.get("nombre", "").strip()
    password = request.form.get("password", "")
    rol = request.form.get("rol", ROL_SUPERVISOR)

    if not email or not password or rol not in ROLES:
        flash("Completá correo y contraseña, y elegí un rol válido.", "error")
        return redirect(url_for("admin_panel"))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO usuarios (email, nombre, password_hash, rol) VALUES (?, ?, ?, ?)",
            (email, nombre, generate_password_hash(password), rol),
        )
        db.commit()
        flash(f"Usuario {email} creado como {rol}.", "ok")
    except sqlite3.IntegrityError:
        flash("Ya existe un usuario con ese correo.", "error")
    return redirect(url_for("admin_panel"))


@app.post("/admin/usuarios/<int:user_id>/eliminar")
@rol_required(ROL_ADMIN)
def eliminar_usuario(user_id):
    db = get_db()
    usuario = db.execute(
        "SELECT id, email, rol FROM usuarios WHERE id = ?", (user_id,)
    ).fetchone()
    if usuario is None:
        abort(404)

    if usuario["id"] == session["user_id"]:
        flash("No podés eliminarte a vos mismo.", "error")
    elif usuario["rol"] == ROL_ADMIN:
        flash("No se puede eliminar a un administrador. Cambiale el rol primero.", "error")
    else:
        db.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
        db.commit()
        # NUEVO: borrar también su foto de perfil si tenía
        ruta_foto = _avatar_path(user_id)
        if os.path.exists(ruta_foto):
            os.remove(ruta_foto)
        flash(f"Usuario {usuario['email']} eliminado.", "ok")
    return redirect(url_for("admin_panel"))


# ============================ Gestión de secciones ============================
@app.route("/secciones/nueva", methods=["POST"])
@rol_required(ROL_ADMIN)
def nueva_seccion():
    titulo = request.form.get("title", "").strip()
    if not titulo:
        flash("El nombre de la sección es obligatorio.", "error")
        return redirect(url_for("inicio"))
    descripcion = request.form.get("description", "").strip() or "Control personalizado de auditoría."
    codigo = "".join(p[0] for p in titulo.split()[:2]).upper() or "NS"
    sid = f"custom-{int(time.time() * 1000)}"
    nueva = {"id": sid, "title": titulo, "description": descripcion, "code": codigo, "questions": []}
    secciones = leer_json(CUSTOM_FILE, [])
    secciones.append(nueva)
    guardar_json(CUSTOM_FILE, secciones)
    flash("Sección creada. Ahora agregá las preguntas.", "ok")
    return redirect(url_for("editar_seccion", seccion_id=sid))


@app.route("/seccion/<seccion_id>/editar", methods=["GET", "POST"])
@rol_required(ROL_ADMIN)
def editar_seccion(seccion_id):
    secciones = leer_json(CUSTOM_FILE, [])
    seccion = next((s for s in secciones if s["id"] == seccion_id), None)
    if seccion is None:
        abort(404)

    if request.method == "POST":
        titulo = request.form.get("title", "").strip()
        if not titulo:
            flash("El nombre es obligatorio.", "error")
            return redirect(url_for("editar_seccion", seccion_id=seccion_id))

        seccion["title"] = titulo
        seccion["description"] = request.form.get("description", "").strip() or "Control personalizado de auditoría."

        subsecciones_raw = request.form.get("subsecciones_json", "[]")
        try:
            nuevas_subsecciones = json.loads(subsecciones_raw)
        except json.JSONDecodeError:
            nuevas_subsecciones = []

        subsecciones_procesadas = []
        preguntas_planas = []

        for idx, sub in enumerate(nuevas_subsecciones):
            sub_titulo = sub.get("titulo", "").strip()
            if not sub_titulo:
                continue

            sub_id = sub.get("id") or f"{seccion_id}-sub-{idx}"
            qs_procesadas = []

            for q_idx, q in enumerate(sub.get("questions", [])):
                q_text = q.get("text", "").strip()
                if q_text:
                    q_id = q.get("id") or f"{seccion_id}-{idx}-{q_idx}-{uuid.uuid4().hex[:4]}"
                    q_obj = {"id": q_id, "text": q_text}
                    qs_procesadas.append(q_obj)
                    preguntas_planas.append(q_obj)

            if qs_procesadas:
                subsecciones_procesadas.append({
                    "id": sub_id,
                    "titulo": sub_titulo,
                    "questions": qs_procesadas
                })

        seccion["subsecciones"] = subsecciones_procesadas
        seccion["questions"] = preguntas_planas

        guardar_json(CUSTOM_FILE, secciones)
        flash("Cambios guardados.", "ok")
        return redirect(url_for("inicio"))

    return render_template("editar.html", seccion=seccion, subsecciones=subsecciones_de(seccion))


@app.route("/seccion/<seccion_id>/eliminar", methods=["POST"])
@rol_required(ROL_ADMIN)
def eliminar_seccion(seccion_id):
    secciones = leer_json(CUSTOM_FILE, [])
    restantes = [s for s in secciones if s["id"] != seccion_id]
    if len(restantes) == len(secciones):
        flash("No se encontró la sección.", "error")
        return redirect(url_for("inicio"))
    guardar_json(CUSTOM_FILE, restantes)
    flash("Sección eliminada junto con sus preguntas.", "ok")
    return redirect(url_for("inicio"))


# ============================== Formulario ==============================
@app.route("/seccion/<seccion_id>", methods=["GET", "POST"])
@rol_required(ROL_ADMIN, ROL_SUPERVISOR)
def formulario(seccion_id):
    seccion = buscar_seccion(seccion_id)
    if seccion is None:
        abort(404)
    if not preguntas_de(seccion):
        flash("Este formulario todavía no tiene preguntas. Agregalas primero.", "error")
        if session.get("rol") == ROL_ADMIN:
            return redirect(url_for("editar_seccion", seccion_id=seccion_id))
        return redirect(url_for("inicio"))

    if request.method == "POST":
        respuestas = []
        for q in preguntas_de(seccion):
            valor = request.form.get(q["id"])
            if valor not in ("si", "no", "na"):
                flash("Respondé todas las preguntas antes de guardar.", "error")
                return render_template("formulario.html", seccion=seccion, form=request.form, guardado=False)
            imagenes = []
            for archivo in request.files.getlist(f"img-{q['id']}")[:MAX_IMAGENES]:
                nombre = secure_filename(archivo.filename or "")
                if nombre and nombre.rsplit(".", 1)[-1].lower() in EXT_OK:
                    destino = f"{uuid.uuid4().hex}-{nombre}"
                    archivo.save(os.path.join(UPLOADS, destino))
                    imagenes.append(destino)
            respuestas.append({
                "question_id": q["id"], "pregunta": q["text"], "respuesta": valor,
                "observacion": request.form.get(f"note-{q['id']}", "").strip(),
                "imagenes": imagenes,
            })

        fecha = request.form.get("fecha") or time.strftime("%Y-%m-%d %H:%M:%S")
        supervisor_obra = request.form.get("supervisor_obra", "").strip()
        actividad = request.form.get("actividad", "").strip()
        participantes_auditados = request.form.get("participantes_auditados", "").strip()
        direccion = request.form.get("direccion", "").strip()
        proyecto = request.form.get("proyecto", "").strip()
        numero_equipo = request.form.get("numero_equipo", "").strip()

        aclaracion = request.form.get("auditor_aclaracion", "").strip()
        firma = request.form.get("auditor_firma", "")
        auditado_nombre = request.form.get("auditado_nombre", "").strip()
        auditado_documento = request.form.get("auditado_documento", "").strip()
        auditado_puesto = request.form.get("auditado_puesto", "").strip()
        auditado_firma = request.form.get("auditado_firma", "")

        db = get_db()
        cur = db.execute(
            """INSERT INTO auditorias
               (seccion_id, titulo, fecha, usuario_id, usuario_nombre, supervisor_obra,
                actividad, participantes_auditados, direccion, proyecto, numero_equipo,
                auditor_aclaracion, auditor_firma, auditado_nombre, auditado_documento,
                auditado_puesto, auditado_firma)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (seccion["id"], seccion["title"], fecha, session["user_id"],
             session.get("nombre", ""), supervisor_obra, actividad, participantes_auditados,
             direccion, proyecto, numero_equipo, aclaracion, firma, auditado_nombre,
             auditado_documento, auditado_puesto, auditado_firma),
        )
        db.executemany(
            """INSERT INTO auditoria_respuestas
               (auditoria_id, question_id, pregunta, respuesta, observacion, imagenes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(cur.lastrowid, r["question_id"], r["pregunta"], r["respuesta"],
              r["observacion"], json.dumps(r["imagenes"])) for r in respuestas],
        )
        db.commit()
        return render_template("formulario.html", seccion=seccion, form={}, guardado=True)

    return render_template("formulario.html", seccion=seccion, form={}, guardado=False)


if __name__ == "__main__":
    app.run(debug=True)