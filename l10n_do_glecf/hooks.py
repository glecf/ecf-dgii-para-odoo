import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

OLD = "l10n_do_accounting"
NEW = "l10n_do_glecf"


def pre_init_hook(env):
    """Take over a database that has l10n_do_accounting installed.

    Up to 17.0.1.1.5, «e-CF DGII para Odoo» worked on l10n_do_accounting,
    installed apart. This module is that same code under another name. Without
    this step, installing it next to the old one would create every view,
    group and document type a second time.

    Nothing is copied or deleted: what the old module created (its records, its
    views, its constraints) is handed to this one by renaming its owner. The
    tables and the columns are the same, so the invoices are not touched.
    """
    cr = env.cr
    cr.execute("SELECT id, state FROM ir_module_module WHERE name = %s", (OLD,))
    old = cr.fetchone()
    if not old or old[1] not in ("installed", "to upgrade"):
        return
    old_id = old[0]

    # Another module that needs l10n_do_accounting would be left without it.
    cr.execute(
        """SELECT m.name FROM ir_module_module_dependency d
             JOIN ir_module_module m ON m.id = d.module_id
            WHERE d.name = %s AND m.name NOT IN ('l10n_do_ecf_glecf', %s)
              AND m.state IN ('installed', 'to upgrade', 'to install')""",
        (OLD, NEW),
    )
    others = [row[0] for row in cr.fetchall()]
    if others:
        raise UserError(
            "No se puede actualizar todavía: estos módulos necesitan l10n_do_accounting "
            "y dejarían de funcionar: %s. Escribe a soporte@glecf.com." % ", ".join(others)
        )

    cr.execute("SELECT id FROM ir_module_module WHERE name = %s", (NEW,))
    new_id = cr.fetchone()[0]

    # A leftover of a failed try would clash with the names taken over below.
    cr.execute("DELETE FROM ir_model_data WHERE module = %s", (NEW,))
    cr.execute("UPDATE ir_model_data SET module = %s WHERE module = %s", (NEW, OLD))
    moved = cr.rowcount
    cr.execute("UPDATE ir_model_constraint SET module = %s WHERE module = %s", (new_id, old_id))
    cr.execute("UPDATE ir_model_relation SET module = %s WHERE module = %s", (new_id, old_id))

    # Report templates are found by their key, and other views call them by name.
    old_dot, new_dot = OLD + ".", NEW + "."
    cr.execute(
        "UPDATE ir_ui_view SET key = replace(key, %s, %s) WHERE key LIKE %s",
        (old_dot, new_dot, old_dot + "%"),
    )
    cr.execute(
        "UPDATE ir_ui_view SET arch_db = replace(arch_db::text, %s, %s)::jsonb "
        "WHERE arch_db::text LIKE %s",
        (old_dot, new_dot, "%" + old_dot + "%"),
    )

    # The old module stays on disk but no longer owns anything.
    cr.execute(
        "UPDATE ir_module_module SET state = 'uninstalled', latest_version = NULL WHERE id = %s",
        (old_id,),
    )
    _logger.info("%s took over %s records of %s", NEW, moved, OLD)


def post_init_hook(env):
    """While the old module is still loaded in this same run, Odoo writes a few
    names again under it. It owns nothing any more: remove them."""
    cr = env.cr
    cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (OLD,))
    old = cr.fetchone()
    if old and old[0] == "uninstalled":
        cr.execute("DELETE FROM ir_model_data WHERE module = %s", (OLD,))
