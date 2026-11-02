# Verificador de instalación de «e-CF DGII para Odoo». Lo ejecuta verificar-instalacion.sh.
# Comprueba si las plantillas de este Odoo admiten el módulo y qué las modifica.
# SOLO LEE: nada se guarda. No lee contraseñas, claves, facturas ni contactos.
import os

import odoo
from lxml import etree
from odoo.modules.module import get_module_path

V = env["ir.ui.view"].sudo().with_context(active_test=False)
M = env["ir.module.module"].sudo()
D = env["ir.model.data"].sudo()
MODULO = "l10n_do_ecf_glecf"


def origen(vista):
    dato = D.search([("model", "=", "ir.ui.view"), ("res_id", "=", vista.id)], limit=1)
    return "%s.%s noupdate=%s" % (dato.module, dato.name, dato.noupdate) if dato else "SIN XMLID"


def combinada(vista):
    if hasattr(vista, "_get_combined_arch"):
        return vista._get_combined_arch()
    return etree.fromstring(vista.get_combined_arch())


def heredadas(vista, nivel=1):
    for hija in V.search([("inherit_id", "=", vista.id)], order="priority, id"):
        print("%s- id=%s %s activa=%s fecha=%s archivo=%s" % (
            "  " * nivel, hija.id, origen(hija), hija.active, hija.write_date, hija.arch_fs))
        heredadas(hija, nivel + 1)


def compatibilidad():
    arbol = etree.parse(os.environ["GLECF_XML"]).getroot()
    fallos = 0
    for plantilla in arbol.iter("template"):
        padre = env.ref(plantilla.get("inherit_id"), raise_if_not_found=False)
        if not padre:
            print("FALTA la plantilla %s" % plantilla.get("inherit_id"))
            fallos += 1
            continue
        arch = combinada(padre)
        for xpath in plantilla.iter("xpath"):
            expr = xpath.get("expr")
            if not arch.xpath(expr):
                print("NO ENCONTRADO en %s: %s" % (plantilla.get("inherit_id"), expr))
                fallos += 1
    return fallos


print("=== VERIFICACION e-CF DGII ===")
print("Odoo %s" % odoo.release.version)
for nombre in ("l10n_do_accounting", MODULO, "web_studio"):
    m = M.search([("name", "=", nombre)], limit=1)
    print("Modulo %s: estado=%s instalada=%s archivo=%s ruta=%s" % (
        nombre, m.state, m.latest_version, m.installed_version, get_module_path(nombre)))

instalado = M.search([("name", "=", MODULO), ("state", "=", "installed")], limit=1)
print()
print("=== COMPATIBILIDAD DE LAS PLANTILLAS ===")
if env.ref("l10n_do_accounting.informations", raise_if_not_found=False) is None:
    print("La localizacion l10n_do_accounting no esta instalada.")
elif instalado:
    print("El modulo ya esta instalado: no hace falta esta comprobacion.")
else:
    fallos = compatibilidad()
    print("RESULTADO: %s" % ("COMPATIBLE" if not fallos else "%s PUNTO(S) SIN ENCONTRAR" % fallos))

print()
print("=== MODULOS INSTALADOS FUERA DE ODOO ===")
for m in M.search([("state", "=", "installed")], order="name"):
    ruta = get_module_path(m.name) or ""
    if "/odoo/addons/" not in ruta:
        print("%s | %s | %s | %s" % (m.name, m.installed_version, m.author, ruta))

print()
print("=== PLANTILLAS DE LA FACTURA ===")
for nombre in ("informations", "report_invoice_document_inherited", "document_tax_totals", "report_invoice"):
    vista = env.ref("l10n_do_accounting." + nombre, raise_if_not_found=False)
    if not vista:
        print("%s: NO EXISTE" % nombre)
        continue
    print("%s: id=%s %s activa=%s fecha=%s" % (nombre, vista.id, origen(vista), vista.active, vista.write_date))
    heredadas(vista)

vista = env.ref("l10n_do_accounting.informations", raise_if_not_found=False)
if vista:
    print()
    print("=== ARQUITECTURA FINAL DE informations ===")
    print(etree.tostring(combinada(vista), encoding="unicode", pretty_print=True))

print("=== FIN ===")
env.cr.rollback()
