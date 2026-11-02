#!/bin/sh
# Comprueba si este Odoo admite «e-CF DGII para Odoo» y qué modifica sus plantillas.
# SOLO LEE: no instala, no actualiza módulos y no cambia nada.
# Sirve con Odoo instalado en el servidor y con Odoo en Docker.
#   sh verificar-instalacion.sh [NOMBRE_DE_LA_BASE]
#   sh verificar-instalacion.sh --contenedor NOMBRE [NOMBRE_DE_LA_BASE]
AQUI=$(cd "$(dirname "$0")" && pwd)
CONTENEDOR=""
if [ "$1" = "--contenedor" ]; then
    CONTENEDOR=$2
    shift 2
fi
BASE=$1
SALIDA=/tmp/verificacion-ecf-dgii.txt
XML="$AQUI/l10n_do_ecf_glecf/views/report_invoice.xml"
[ -f "$XML" ] || XML="$AQUI/report_invoice.xml"
[ -f "$XML" ] && [ -f "$AQUI/verificar_instalacion.py" ] || { echo "Ejecuta este archivo desde la carpeta del repositorio."; exit 1; }

# Lo ejecuta dentro del contenedor, con el Odoo de allí.
en_contenedor() {
    docker exec "$1" sh -c 'rm -rf /tmp/verificar-ecf-dgii; mkdir -p /tmp/verificar-ecf-dgii' || exit 1
    for f in "$AQUI/verificar-instalacion.sh" "$AQUI/verificar_instalacion.py" "$XML"; do
        docker cp -q "$f" "$1:/tmp/verificar-ecf-dgii/" || exit 1
    done
    docker exec "$1" sh /tmp/verificar-ecf-dgii/verificar-instalacion.sh $BASE
    R=$?
    docker exec "$1" sh -c 'rm -rf /tmp/verificar-ecf-dgii' 2>/dev/null
    exit $R
}

[ -z "$CONTENEDOR" ] || en_contenedor "$CONTENEDOR"

PID=""
for p in $(pgrep -f 'odoo' 2>/dev/null); do
    [ "$p" = "$$" ] && continue
    # Un Odoo de un contenedor también se ve desde el servidor: no es este.
    grep -qE 'docker|containerd|kubepods|libpod' /proc/$p/cgroup 2>/dev/null && continue
    C=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
    case "$C" in
        *verificar-instalacion*|*pgrep*|*postgres*|*"sh -c"*) continue ;;
        *odoo-bin*|*/odoo\ *|*bin/odoo*|odoo\ *|*python*odoo*)
            # Mejor el que indica su configuración: en Docker no suele hacerlo.
            case "$C" in
                *" -c "*|*--config*) PID=$p; break ;;
                *) [ -n "$PID" ] || PID=$p ;;
            esac ;;
    esac
done
if [ -z "$PID" ]; then
    LISTA=$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -i odoo | grep -viE ' (postgres|redis|nginx)|^[^ ]*-db ' | cut -d' ' -f1)
    if [ -z "$LISTA" ]; then
        command -v docker >/dev/null 2>&1 && ! docker ps >/dev/null 2>&1 && echo "Docker no responde con este usuario: prueba con sudo."
        echo "No encontré un Odoo en marcha."
        exit 1
    fi
    if [ "$(printf '%s\n' "$LISTA" | grep -c .)" = "1" ]; then
        echo "Odoo en Docker: $LISTA"
        en_contenedor "$LISTA"
    fi
    echo "Hay varios Odoo en Docker:"
    printf '%s\n' "$LISTA"
    echo "Indica cuál: sh verificar-instalacion.sh --contenedor NOMBRE"
    exit 1
fi

COMANDO=$(tr '\0' ' ' < /proc/$PID/cmdline)
set -- $COMANDO
PY=""; BIN=""; CONF=""; ANT=""; DBHOST=""; DBPORT=""; DBUSER=""; DBPASS=""
for a in "$@"; do
    case "$a" in
        *odoo-bin|*/odoo|odoo|*openerp-server) [ -z "$BIN" ] && BIN=$a ;;
        *python*) [ -z "$PY" ] && [ -z "$BIN" ] && PY=$a ;;
    esac
    case "$ANT" in
        -c|--config) CONF=$a ;;
        --db_host) DBHOST=$a ;;
        --db_port) DBPORT=$a ;;
        --db_user) DBUSER=$a ;;
        --db_password) DBPASS=$a ;;
    esac
    case "$a" in --config=*) CONF=${a#--config=} ;; esac
    ANT=$a
done
RAIZ=$(readlink /proc/$PID/cwd 2>/dev/null)
case "$BIN" in /*) ;; *) [ -f "$RAIZ/$BIN" ] && BIN="$RAIZ/$BIN" ;; esac
[ -n "$CONF" ] || for f in /etc/odoo/odoo.conf /etc/odoo.conf "$HOME/.odoorc" "$HOME/.openerp_serverrc"; do [ -f "$f" ] && CONF=$f && break; done
case "$CONF" in /*|"") ;; *) CONF="$RAIZ/$CONF" ;; esac

if [ -n "$CONF" ] && [ ! -r "$CONF" ]; then
    DUENO=$(ps -o user= -p "$PID" | tr -d ' ')
    TEMPORAL=/tmp/verificar-ecf-dgii
    mkdir -p "$TEMPORAL" && cp "$AQUI/verificar-instalacion.sh" "$AQUI/verificar_instalacion.py" "$XML" "$TEMPORAL/" && chmod -R a+rX "$TEMPORAL"
    echo "Hace falta el usuario de Odoo ($DUENO). Si te pide contraseña, es la de sudo."
    exec sudo -u "$DUENO" sh "$TEMPORAL/verificar-instalacion.sh" $BASE
fi

# Los datos de conexión con los que arrancó Odoo, para Odoo y para psql.
[ -z "$DBHOST" ] || export PGHOST="$DBHOST"
[ -z "$DBPORT" ] || export PGPORT="$DBPORT"
[ -z "$DBUSER" ] || export PGUSER="$DBUSER"
if [ -n "$DBPASS" ]; then PGPASSWORD=$DBPASS; export PGPASSWORD; fi

[ -n "$BASE" ] || BASE=$(printf '%s' "$COMANDO" | sed -n 's/.*\(-d\|--database\)[ =]\([^ ]*\).*/\2/p')
[ -n "$BASE" ] || BASE=$(sed -n 's/^[[:space:]]*db_name[[:space:]]*=[[:space:]]*\([^,[:space:]]*\).*/\1/p' "$CONF" 2>/dev/null | grep -v '^False$' | head -1)
if [ -z "$BASE" ] && command -v psql >/dev/null 2>&1; then
    BASES=$(psql -d postgres -Atc "select datname from pg_database where not datistemplate and datname <> 'postgres'" 2>/dev/null)
    if [ "$(printf '%s\n' "$BASES" | grep -c .)" = "1" ]; then
        BASE=$BASES
    elif [ -n "$BASES" ]; then
        echo "Hay varias bases:"; printf '%s\n' "$BASES"
    fi
fi
if [ -z "$BASE" ]; then
    echo "No pude detectar la base. Vuelve a ejecutarlo así: sh verificar-instalacion.sh NOMBRE_BASE"
    exit 1
fi

echo "Odoo: $BIN | configuración: ${CONF:-sin archivo} | base: $BASE"
if [ -n "$PY" ]; then set -- "$PY" "$BIN"; else set -- "$BIN"; fi
if [ -n "$CONF" ]; then set -- "$@" shell -c "$CONF"; else set -- "$@" shell; fi
GLECF_XML="$XML" "$@" -d "$BASE" --no-http --http-port=8199 < "$AQUI/verificar_instalacion.py" > "$SALIDA" 2>&1
if grep -q '=== FIN ===' "$SALIDA"; then
    echo
    echo "----- COPIA DESDE AQUÍ -----"
    sed -n '/^=== VERIFICACION/,/^=== FIN ===/p' "$SALIDA"
    echo "----- HASTA AQUÍ -----"
    echo
    grep -E '^RESULTADO|^NO ENCONTRADO|^FALTA|^El modulo ya|^La localizacion' "$SALIDA"
    echo "Informe también guardado en: $SALIDA"
else
    echo "No terminó bien. Revisa o envía: $SALIDA"
fi
