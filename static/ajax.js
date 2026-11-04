(function () {
  const ZONAS_POR_DEFECTO = "#flash, #contenido";

  async function pedir(url, opciones, zonas) {
    const resp = await fetch(url, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
      ...opciones,
    });

    // Sesión vencida: Flask redirige al login
    if (new URL(resp.url).pathname === "/login") {
      window.location.href = resp.url;
      return;
    }
    if (!resp.ok) throw new Error("HTTP " + resp.status);

    const html = await resp.text();
    const doc = new DOMParser().parseFromString(html, "text/html");

    zonas.split(",").map(s => s.trim()).forEach(sel => {
      const nuevo = doc.querySelector(sel);
      const viejo = document.querySelector(sel);
      if (nuevo && viejo) viejo.replaceWith(nuevo);
    });

    if (window.autoCerrarAlertas) window.autoCerrarAlertas();
  }

  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-ajax]");
    if (!form || e.defaultPrevented) return; // respeta el confirm() del onsubmit
    e.preventDefault();

    const zonas = form.dataset.target || ZONAS_POR_DEFECTO;
    const datos = new FormData(form, e.submitter);
    const boton = e.submitter || form.querySelector("[type=submit]");
    if (boton) boton.disabled = true;

    try {
      if ((form.method || "get").toLowerCase() === "get") {
        const url = form.action + "?" + new URLSearchParams(datos);
        await pedir(url, {}, zonas);
        history.replaceState(null, "", url);
      } else {
        await pedir(form.action, { method: "POST", body: datos }, zonas);
      }
    } catch (err) {
      // Si algo falla, se envía el formulario de la forma normal
      form.removeAttribute("data-ajax");
      form.requestSubmit(e.submitter);
    } finally {
      if (boton) boton.disabled = false;
    }
  });

  // Los filtros con data-autosubmit se envían al cambiar el select
  document.addEventListener("change", (e) => {
    const campo = e.target.closest("form[data-ajax][data-autosubmit] select");
    if (campo) campo.form.requestSubmit();
  });
})();